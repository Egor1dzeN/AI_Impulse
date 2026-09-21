from __future__ import annotations

import base64
import os
from email.mime.text import MIMEText
from typing import Any

import requests

from . import db, google_oauth

GMAIL_REDIRECT_URI = os.environ.get("GMAIL_REDIRECT_URI", "http://127.0.0.1:8001/api/gmail/callback")
STATE_PURPOSE = "gmail_connect"

# Both requested up front on the same connection: readonly lets the agent
# list/read mail, send lets the confirmed-draft flow actually deliver one
# (see tools.py::draft_email — the agent itself is never given a tool that
# calls send_message below; only POST /api/gmail/send, triggered by the
# user's own "Send" click in the UI, does).
SCOPE = "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send"

API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def gmail_is_configured() -> bool:
    return google_oauth.google_oauth_is_configured()


def create_state(user_id: str) -> str:
    return google_oauth.create_state(user_id, STATE_PURPOSE)


def decode_state(state: str) -> str:
    return google_oauth.decode_state(state, STATE_PURPOSE)


def build_authorize_url(state: str) -> str:
    return google_oauth.build_authorize_url(SCOPE, GMAIL_REDIRECT_URI, state)


def exchange_code_for_refresh_token(code: str) -> str:
    return google_oauth.exchange_code_for_refresh_token(code, GMAIL_REDIRECT_URI)


def _get_access_token(refresh_token: str) -> str:
    return google_oauth.get_access_token(refresh_token, "Gmail access was revoked — reconnect it in Settings")


def revoke(refresh_token: str) -> None:
    google_oauth.revoke(refresh_token)


def _header(headers: list[dict[str, str]], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def list_messages(refresh_token: str, query: str = "", max_results: int = 10) -> list[dict[str, Any]]:
    """List messages matching `query` (Gmail search syntax, e.g. "from:boss
    is:unread", empty = inbox default). One metadata-only GET per message
    (headers + snippet, no body) — cheap and enough for a list view; use
    get_message() for the full body of one specific message."""
    access_token = _get_access_token(refresh_token)
    headers = {"Authorization": f"Bearer {access_token}"}

    resp = requests.get(
        f"{API_BASE}/messages",
        headers=headers,
        params={"q": query, "maxResults": max_results},
        timeout=10,
    )
    resp.raise_for_status()
    ids = [item["id"] for item in resp.json().get("messages", [])]

    messages = []
    for message_id in ids:
        meta_resp = requests.get(
            f"{API_BASE}/messages/{message_id}",
            headers=headers,
            params={
                "format": "metadata",
                "metadataHeaders": ["From", "Subject", "Date"],
            },
            timeout=10,
        )
        meta_resp.raise_for_status()
        data = meta_resp.json()
        msg_headers = data.get("payload", {}).get("headers", [])
        messages.append(
            {
                "id": message_id,
                "from": _header(msg_headers, "From"),
                "subject": _header(msg_headers, "Subject"),
                "date": _header(msg_headers, "Date"),
                "snippet": data.get("snippet", ""),
            }
        )
    return messages


def _extract_plain_text(payload: dict) -> str:
    """Walk the (possibly nested multipart/*) MIME tree for the first
    text/plain part and base64url-decode its body. Falls back to "" rather
    than raising — an email with only an HTML part (no text/plain
    alternative) is unusual but not an error condition here."""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")

    if mime_type == "text/plain" and body_data:
        return base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")

    for part in payload.get("parts", []) or []:
        text = _extract_plain_text(part)
        if text:
            return text
    return ""


def get_message(refresh_token: str, message_id: str) -> dict[str, Any]:
    access_token = _get_access_token(refresh_token)
    resp = requests.get(
        f"{API_BASE}/messages/{message_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"format": "full"},
        timeout=10,
    )
    if resp.status_code == 404:
        return {"found": False}
    resp.raise_for_status()
    data = resp.json()
    payload = data.get("payload", {})
    msg_headers = payload.get("headers", [])
    return {
        "found": True,
        "id": message_id,
        "from": _header(msg_headers, "From"),
        "subject": _header(msg_headers, "Subject"),
        "date": _header(msg_headers, "Date"),
        "body": _extract_plain_text(payload),
    }


def draft_already_sent(draft_id: str) -> bool:
    with db.get_conn() as conn:
        row = conn.execute("SELECT 1 FROM sent_email_drafts WHERE draft_id = ?", (draft_id,)).fetchone()
    return row is not None


def mark_draft_sent(draft_id: str, user_id: str) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sent_email_drafts (draft_id, user_id, sent_at) VALUES (?, ?, ?)",
            (draft_id, user_id, db.now_iso()),
        )


def send_message(refresh_token: str, to: str, subject: str, body: str) -> dict[str, Any]:
    access_token = _get_access_token(refresh_token)

    mime = MIMEText(body)
    mime["To"] = to
    mime["Subject"] = subject
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")

    resp = requests.post(
        f"{API_BASE}/messages/send",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"raw": raw},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"id": data.get("id"), "to": to, "subject": subject}
