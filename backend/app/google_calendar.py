from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import jwt
import requests
from fastapi import HTTPException

from .auth import ALGORITHM, GOOGLE_CLIENT_ID, SECRET_KEY

logger = logging.getLogger(__name__)

# Short-lived, separate from the login access token: only proves "this
# browser started the connect flow for this user_id" across the redirect to
# Google and back — the callback request itself carries no Authorization
# header (Google redirects the bare browser there, not our frontend's
# fetch), so the user_id has to travel inside `state` instead.
STATE_TTL = timedelta(minutes=10)
STATE_PURPOSE = "google_calendar_connect"


def create_state(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "purpose": STATE_PURPOSE,
        "exp": datetime.now(timezone.utc) + STATE_TTL,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_state(state: str) -> str:
    try:
        payload = jwt.decode(state, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=400, detail="invalid or expired state") from None
    if payload.get("purpose") != STATE_PURPOSE:
        raise HTTPException(status_code=400, detail="invalid state")
    return payload["sub"]

# Same OAuth client as Google Sign-In (auth.py's GOOGLE_CLIENT_ID) — it's
# already a "Web application" client, which is what the authorization-code
# flow below needs too. Sign-In only ever used its id_token verification
# path (no client_secret needed there), so this is a NEW secret to add in
# Cloud Console for that same client, not a new client.
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_CALENDAR_REDIRECT_URI = os.environ.get(
    "GOOGLE_CALENDAR_REDIRECT_URI", "http://127.0.0.1:8001/api/google-calendar/callback"
)

# calendar.events (not the broader "calendar" scope) is enough to create
# events and is easier to justify on the OAuth consent screen — this app
# never needs to read/delete the user's other calendar entries.
SCOPE = "https://www.googleapis.com/auth/calendar.events"

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
EVENTS_ENDPOINT = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

# Reminders always mean this timezone in this deployment (single-user-ish
# personal project, no per-user timezone setting yet) — see the ai_impulse
# session decision to hardcode Europe/Moscow rather than read it from the
# Google account, which would need an extra Calendar API settings call.
TIMEZONE = "Europe/Moscow"


def google_calendar_is_configured() -> bool:
    return bool(GOOGLE_CLIENT_SECRET)


def build_authorize_url(state: str) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_CALENDAR_REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        # Forces Google to re-issue a refresh_token even if this user
        # already granted this scope before — without it, a reconnect after
        # disconnecting would silently come back with no refresh_token at
        # all (Google only issues one on a user's very first consent).
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def exchange_code_for_refresh_token(code: str) -> str:
    resp = requests.post(
        TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_CALENDAR_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        # Most common cause: the user had already granted this scope before
        # and Google didn't re-issue one this time despite prompt=consent
        # (e.g. it was revoked oddly, or this is a test/publishing quirk).
        raise RuntimeError("Google did not return a refresh_token — try disconnecting and reconnecting")
    return refresh_token


def _get_access_token(refresh_token: str) -> str:
    resp = requests.post(
        TOKEN_ENDPOINT,
        data={
            "refresh_token": refresh_token,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
        },
        timeout=10,
    )
    if resp.status_code == 400:
        # invalid_grant: refresh_token revoked (user removed access in their
        # Google account, or it expired from prolonged inactivity).
        raise RuntimeError("Google Calendar access was revoked — reconnect it in Settings")
    resp.raise_for_status()
    return resp.json()["access_token"]


def revoke(refresh_token: str) -> None:
    try:
        requests.post(REVOKE_ENDPOINT, params={"token": refresh_token}, timeout=10)
    except requests.RequestException:
        logger.warning("Google token revoke request failed, clearing locally anyway", exc_info=True)


def list_events(refresh_token: str, date: str, days: int = 1) -> list[dict[str, Any]]:
    """List events on the user's primary calendar starting `date`
    (YYYY-MM-DD, Europe/Moscow) across `days` day(s).

    Russia doesn't observe DST (fixed UTC+3 since 2014), so "+03:00" is a
    safe hardcoded offset for the timeMin/timeMax bounds Calendar's list
    endpoint requires as self-contained RFC3339 timestamps — unlike
    create_event's `timeZone` field, there's no separate timezone param
    here that would resolve a naive time for us.
    """
    access_token = _get_access_token(refresh_token)

    start_dt = datetime.fromisoformat(date)
    end_dt = start_dt + timedelta(days=days)

    resp = requests.get(
        EVENTS_ENDPOINT,
        headers={"Authorization": f"Bearer {access_token}"},
        params={
            "timeMin": f"{start_dt.isoformat()}+03:00",
            "timeMax": f"{end_dt.isoformat()}+03:00",
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 50,
        },
        timeout=10,
    )
    resp.raise_for_status()

    events = []
    for item in resp.json().get("items", []):
        start = item.get("start", {})
        events.append(
            {
                "id": item.get("id"),
                "summary": item.get("summary", "(без названия)"),
                "start": start.get("dateTime", start.get("date")),
                "description": item.get("description"),
            }
        )
    return events


def create_event(
    refresh_token: str,
    summary: str,
    start_iso: str,
    duration_minutes: int = 30,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a Google Calendar event acting as a reminder: a popup
    notification fires exactly at `start_iso` (Europe/Moscow local time, no
    UTC offset — Google resolves it using `timeZone` below), regardless of
    the user's own default-reminder settings.

    start_iso: naive local datetime, e.g. "2026-09-22T09:00:00".
    """
    access_token = _get_access_token(refresh_token)

    start_dt = datetime.fromisoformat(start_iso)
    end_dt = start_dt + timedelta(minutes=duration_minutes)

    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": TIMEZONE},
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": 0}],
        },
    }

    resp = requests.post(
        EVENTS_ENDPOINT,
        headers={"Authorization": f"Bearer {access_token}"},
        json=body,
        timeout=10,
    )
    resp.raise_for_status()
    event = resp.json()
    return {
        "id": event.get("id"),
        "summary": event.get("summary"),
        "start": start_iso,
        "html_link": event.get("htmlLink"),
    }
