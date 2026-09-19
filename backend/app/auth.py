from __future__ import annotations

import logging
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Header, HTTPException
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from passlib.context import CryptContext

from . import db

logger = logging.getLogger(__name__)

_DEV_SECRET_KEY = "dev-insecure-secret-key-change-me"
SECRET_KEY = os.environ.get("AUTH_SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = _DEV_SECRET_KEY
    logger.warning(
        "AUTH_SECRET_KEY is not set; using an insecure development default. "
        "Set the AUTH_SECRET_KEY environment variable before deploying."
    )

# Public identifier, not a secret — safe to default in source. Override via
# env if the app is ever registered under a different Google Cloud project.
GOOGLE_CLIENT_ID = os.environ.get(
    "GOOGLE_CLIENT_ID",
    "75107685144-51b4nd1b4stkl6p6en293sndm6d5jk5q.apps.googleusercontent.com",
)

ALGORITHM = "HS256"
TOKEN_TTL = timedelta(days=30)

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_google_auth_request = google_requests.Request()


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _pwd_context.verify(password, password_hash)


def create_access_token(user_id: str, username: str) -> str:
    payload = {
        "sub": user_id,
        "username": username,
        "exp": datetime.now(timezone.utc) + TOKEN_TTL,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return payload["sub"]


def register_user(username: str, password: str) -> tuple[str, str]:
    username = username.strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password are required")

    user_id = db.new_id()
    password_hash = hash_password(password)

    try:
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (user_id, username, password_hash, db.now_iso()),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="username is already taken")

    return user_id, create_access_token(user_id, username)


def login_user(username: str, password: str) -> tuple[str, str]:
    username = username.strip()
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()

    if row is None or not verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid username or password")

    return row["id"], create_access_token(row["id"], username)


def verify_google_id_token(token: str) -> dict:
    try:
        return google_id_token.verify_oauth2_token(token, _google_auth_request, GOOGLE_CLIENT_ID)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid Google ID token")


def _unique_username(base: str, conn: sqlite3.Connection) -> str:
    base = base or "user"
    candidate = base
    suffix = 1
    while conn.execute("SELECT 1 FROM users WHERE username = ?", (candidate,)).fetchone():
        suffix += 1
        candidate = f"{base}{suffix}"
    return candidate


def login_or_register_google_user(google_id: str, email: str, name: str | None) -> tuple[str, str]:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, username FROM users WHERE google_id = ?",
            (google_id,),
        ).fetchone()
        if row is not None:
            return row["id"], create_access_token(row["id"], row["username"])

        user_id = db.new_id()
        username = _unique_username((name or email.split("@")[0]).strip(), conn)
        # No password login for Google-only accounts — store an unusable
        # random hash so the NOT NULL constraint holds without a real one.
        password_hash = hash_password(secrets.token_hex(32))

        conn.execute(
            "INSERT INTO users (id, username, password_hash, google_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, username, password_hash, google_id, db.now_iso()),
        )

    return user_id, create_access_token(user_id, username)


def get_current_user_id(authorization: str | None = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    return decode_access_token(token)
