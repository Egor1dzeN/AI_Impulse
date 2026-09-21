from __future__ import annotations

import os
from threading import Lock

from gigachat import GigaChat

# memory/ is a standalone package (flat imports, no dependency on
# backend.app — see memory_bridge.py's sys.path note), so it keeps its own
# small GigaChat client instead of importing backend.app.chat_gigachat.
# Same env var names as that module, so one .env toggles both.
GIGACHAT_CREDENTIALS = os.environ.get("GIGA_CHAT_IDE")
GIGACHAT_SCOPE = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_BASE_URL = os.environ.get("GIGACHAT_BASE_URL", "https://api.giga.chat/v1")
GIGACHAT_VERIFY_SSL = os.environ.get("GIGACHAT_VERIFY_SSL", "0") == "1"

_lock = Lock()
_client: GigaChat | None = None


def gigachat_is_configured() -> bool:
    return bool(GIGACHAT_CREDENTIALS)


def get_client() -> GigaChat:
    """Lazily build the single process-wide GigaChat client used by the
    memory/ package's API-backed embedder and grammar normalizer."""
    global _client
    if _client is not None:
        return _client

    with _lock:
        if _client is not None:
            return _client

        if not GIGACHAT_CREDENTIALS:
            raise RuntimeError(
                "GIGA_CHAT_IDE is not set — a GigaChat-backed memory component was selected but has no credentials."
            )

        _client = GigaChat(
            base_url=GIGACHAT_BASE_URL,
            credentials=GIGACHAT_CREDENTIALS,
            scope=GIGACHAT_SCOPE,
            verify_ssl_certs=GIGACHAT_VERIFY_SSL,
        )

    return _client
