from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

import requests

from . import google_oauth

GOOGLE_CALENDAR_REDIRECT_URI = os.environ.get(
    "GOOGLE_CALENDAR_REDIRECT_URI", "http://127.0.0.1:8001/api/google-calendar/callback"
)
STATE_PURPOSE = "google_calendar_connect"

# calendar.events (not the broader "calendar" scope) is enough to create
# events and is easier to justify on the OAuth consent screen — this app
# never needs to read/delete the user's other calendar entries.
SCOPE = "https://www.googleapis.com/auth/calendar.events"

EVENTS_ENDPOINT = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

# Reminders always mean this timezone in this deployment (single-user-ish
# personal project, no per-user timezone setting yet) — see the ai_impulse
# session decision to hardcode Europe/Moscow rather than read it from the
# Google account, which would need an extra Calendar API settings call.
TIMEZONE = "Europe/Moscow"


def google_calendar_is_configured() -> bool:
    return google_oauth.google_oauth_is_configured()


def create_state(user_id: str) -> str:
    return google_oauth.create_state(user_id, STATE_PURPOSE)


def decode_state(state: str) -> str:
    return google_oauth.decode_state(state, STATE_PURPOSE)


def build_authorize_url(state: str) -> str:
    return google_oauth.build_authorize_url(SCOPE, GOOGLE_CALENDAR_REDIRECT_URI, state)


def exchange_code_for_refresh_token(code: str) -> str:
    return google_oauth.exchange_code_for_refresh_token(code, GOOGLE_CALENDAR_REDIRECT_URI)


def _get_access_token(refresh_token: str) -> str:
    return google_oauth.get_access_token(refresh_token, "Google Calendar access was revoked — reconnect it in Settings")


def revoke(refresh_token: str) -> None:
    google_oauth.revoke(refresh_token)


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
