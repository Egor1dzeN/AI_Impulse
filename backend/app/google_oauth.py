from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import jwt
import requests
from fastapi import HTTPException

from .auth import ALGORITHM, GOOGLE_CLIENT_ID, SECRET_KEY

logger = logging.getLogger(__name__)

# Same OAuth client as Google Sign-In (auth.py's GOOGLE_CLIENT_ID) — it's
# already a "Web application" client, which is what the authorization-code
# flow below needs too. Sign-In only ever used its id_token verification
# path (no client_secret needed there), so this is a NEW secret to add in
# Cloud Console for that same client, not a new client. Shared by every
# Google integration (Calendar, Gmail, ...) — one client, one secret.
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

STATE_TTL = timedelta(minutes=10)


def google_oauth_is_configured() -> bool:
    return bool(GOOGLE_CLIENT_SECRET)


def create_state(user_id: str, purpose: str) -> str:
    """Short-lived, separate from the login access token: only proves "this
    browser started the connect flow for this user_id (for this purpose)"
    across the redirect to Google and back — the callback request itself
    carries no Authorization header (Google redirects the bare browser
    there, not our frontend's fetch), so the user_id has to travel inside
    `state` instead. `purpose` stops a Calendar-flow state token (say) from
    being replayed against the Gmail callback or vice versa."""
    payload = {
        "sub": user_id,
        "purpose": purpose,
        "exp": datetime.now(timezone.utc) + STATE_TTL,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_state(state: str, expected_purpose: str) -> str:
    try:
        payload = jwt.decode(state, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=400, detail="invalid or expired state") from None
    if payload.get("purpose") != expected_purpose:
        raise HTTPException(status_code=400, detail="invalid state")
    return payload["sub"]


def build_authorize_url(scope: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "access_type": "offline",
        # Forces Google to re-issue a refresh_token even if this user
        # already granted this scope before — without it, a reconnect after
        # disconnecting would silently come back with no refresh_token at
        # all (Google only issues one on a user's very first consent).
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def exchange_code_for_refresh_token(code: str, redirect_uri: str) -> str:
    resp = requests.post(
        TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
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


def get_access_token(refresh_token: str, revoked_message: str) -> str:
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
        raise RuntimeError(revoked_message)
    resp.raise_for_status()
    return resp.json()["access_token"]


def revoke(refresh_token: str) -> None:
    try:
        requests.post(REVOKE_ENDPOINT, params={"token": refresh_token}, timeout=10)
    except requests.RequestException:
        logger.warning("Google token revoke request failed, clearing locally anyway", exc_info=True)
