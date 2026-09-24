"""Audit trail: who sent what, and how the system handled it.

Append-only JSON Lines, one file per day, in backend/audit/ (outside
backend/data/, so neither "delete all my data" nor wiping the databases
touches it). Every record passes through mask_obj — secrets never land
here, same as in the regular logs.

Sources of records:
- AuditMiddleware: every HTTP request — user (from the JWT), IP, method,
  path, masked request body (passwords dropped), status, duration and the
  JSON response (the chat SSE stream is not buffered here);
- agent.py: a full trace of each chat turn — system prompt (stored once per
  hash in prompts/), history size, every tool call with arguments and
  result, guard refusals, retries, errors, the final reply;
- memory_bridge.py: what the memory pipeline decided (created / updated /
  ignored facts).

Read it back with backend/scripts/audit_view.py.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .masking import mask_obj

logger = logging.getLogger(__name__)

AUDIT_DIR = Path(os.environ.get("AUDIT_DIR", "") or Path(__file__).resolve().parent.parent / "audit")
MAX_FIELD_CHARS = 60_000
MAX_RESPONSE_BYTES = 256_000
_SKIP_PATHS = {"/api/health", "/api/import/progress"}
_SECRET_FIELDS = {"password", "invite_code", "id_token", "token", "intervals_icu_api_key", "access_token"}

_lock = threading.Lock()
_usernames: dict[str, str] = {}

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("audit_request_id", default=None)
client_ip_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("audit_client_ip", default=None)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + f"…<+{len(value) - MAX_FIELD_CHARS} chars>"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value]
    return value


def _strip_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[скрыто]" if k in _SECRET_FIELDS and v else _strip_secrets(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_secrets(v) for v in value]
    return value


def username_for(user_id: str | None) -> str | None:
    if not user_id:
        return None
    if user_id not in _usernames:
        try:
            from . import db

            with db.get_conn() as conn:
                row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
            if row:
                _usernames[user_id] = row["username"]
        except Exception:  # noqa: BLE001
            return None
    return _usernames.get(user_id)


def record(event: str, user_id: str | None = None, **fields: Any) -> None:
    """Append one audit record. Never raises: auditing must not break a request."""
    try:
        entry = {
            "ts": _now(),
            "event": event,
            "request_id": request_id_var.get(),
            "user_id": user_id,
            "username": username_for(user_id),
            "ip": client_ip_var.get(),
            **fields,
        }
        entry = mask_obj(_clip(_strip_secrets(entry)))
        line = json.dumps(entry, ensure_ascii=False, default=str)
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        path = AUDIT_DIR / f"audit-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        with _lock, open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        logger.exception("audit record failed: event=%s", event)


def store_prompt(text: str) -> str:
    """Keep each distinct system prompt once (prompts/<sha>.txt); records reference the hash."""
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    try:
        folder = AUDIT_DIR / "prompts"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{sha}.txt"
        if not path.exists():
            from .masking import mask_sensitive

            path.write_text(mask_sensitive(text), encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.exception("audit prompt store failed")
    return sha


# --- HTTP middleware ---------------------------------------------------------

def _user_from_headers(headers: dict[str, str]) -> str | None:
    auth = headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        from .auth import decode_access_token

        return decode_access_token(auth[7:])
    except Exception:  # noqa: BLE001
        return None


def _user_from_token_response(body: Any) -> str | None:
    if isinstance(body, dict) and body.get("access_token"):
        try:
            from .auth import decode_access_token

            return decode_access_token(body["access_token"])
        except Exception:  # noqa: BLE001
            return None
    return None


def _parse_body(content_type: str, raw: bytes) -> Any:
    if not raw:
        return None
    if "application/json" in content_type:
        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            return raw[:2000].decode("utf-8", errors="replace")
    if "multipart/form-data" in content_type:
        # Files are not copied into the audit — only what was uploaded.
        head = raw[:200_000].decode("utf-8", errors="replace")
        files = re.findall(r'name="([^"]+)"; filename="([^"]*)"', head)
        fields = {
            m.group(1): m.group(2).strip()[:500]
            for m in re.finditer(r'name="([^"]+)"\r\n\r\n([^\r]*)\r\n', head)
        }
        return {"files": [{"field": f, "filename": n} for f, n in files], "fields": fields, "bytes": len(raw)}
    if "x-www-form-urlencoded" in content_type:
        return raw[:2000].decode("utf-8", errors="replace")
    return {"bytes": len(raw), "content_type": content_type}


class AuditMiddleware:
    """Pure ASGI: tees the request body and the response without buffering
    streams, so the chat SSE keeps streaming as before."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api") or scope["path"] in _SKIP_PATHS:
            return await self.app(scope, receive, send)

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        forwarded = headers.get("x-forwarded-for", "").split(",")[0].strip()
        ip = forwarded or (scope.get("client") or [None])[0]
        rid = uuid.uuid4().hex[:12]
        rid_token = request_id_var.set(rid)
        ip_token = client_ip_var.set(ip)
        started = time.monotonic()
        request_chunks: list[bytes] = []
        response: dict[str, Any] = {"status": None, "content_type": "", "chunks": [], "size": 0}

        async def tee_receive():
            message = await receive()
            if message["type"] == "http.request" and message.get("body"):
                if sum(len(c) for c in request_chunks) < 2_000_000:
                    request_chunks.append(message["body"])
            return message

        async def tee_send(message):
            if message["type"] == "http.response.start":
                response["status"] = message["status"]
                for k, v in message.get("headers", []):
                    if k.lower() == b"content-type":
                        response["content_type"] = v.decode("latin-1")
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                response["size"] += len(body)
                if "text/event-stream" not in response["content_type"] and response["size"] <= MAX_RESPONSE_BYTES:
                    response["chunks"].append(body)
            await send(message)

        error = None
        try:
            await self.app(scope, tee_receive, tee_send)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            try:
                req_body = _parse_body(headers.get("content-type", ""), b"".join(request_chunks))
                resp_body = None
                if response["chunks"] and "application/json" in response["content_type"]:
                    try:
                        resp_body = json.loads(b"".join(response["chunks"]).decode("utf-8", errors="replace"))
                    except ValueError:
                        resp_body = None
                user_id = _user_from_headers(headers) or _user_from_token_response(resp_body)
                if isinstance(resp_body, dict) and "access_token" in resp_body:
                    resp_body = {**resp_body, "access_token": "[скрыто]"}
                # Endpoints whose responses are just lists of what's already
                # stored (history, memories, transactions) are not copied.
                heavy_get = scope["method"] == "GET" and scope["path"] in (
                    "/api/history", "/api/memories", "/api/finance/transactions", "/api/memories/export",
                    "/api/conversations", "/api/sandbox",
                )
                record(
                    "http",
                    user_id,
                    method=scope["method"],
                    path=scope["path"],
                    query=scope.get("query_string", b"").decode("latin-1") or None,
                    user_agent=headers.get("user-agent"),
                    request=req_body,
                    status=response["status"] or (500 if error else None),
                    duration_ms=round((time.monotonic() - started) * 1000),
                    response_bytes=response["size"],
                    response=None if heavy_get else resp_body,
                    error=error,
                )
            finally:
                request_id_var.reset(rid_token)
                client_ip_var.reset(ip_token)
