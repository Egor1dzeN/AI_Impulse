from __future__ import annotations

import os
from threading import BoundedSemaphore, Lock

from gigachat import GigaChat

# memory/ is a standalone package (flat imports, no dependency on
# backend.app — see memory_bridge.py's sys.path note), so it keeps its own
# small GigaChat client instead of importing backend.app.chat_gigachat.
# Same env var names as that module, so one .env toggles both.
GIGACHAT_CREDENTIALS = os.environ.get("GIGA_CHAT_IDE")
GIGACHAT_SCOPE = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_BASE_URL = os.environ.get("GIGACHAT_BASE_URL", "https://api.giga.chat/v1")
GIGACHAT_VERIFY_SSL = os.environ.get("GIGACHAT_VERIFY_SSL", "0") == "1"
# Bulk memory work (import extraction, profile build, grammar fixes) fires
# many requests back to back from several background tasks at once, which
# the API answers with 429 Too Many Requests — and a failed call used to
# silently drop that record's facts. Cap how many run at the same time and
# let the library retry 429/5xx with exponential backoff (1, 2, 4, … s,
# honouring Retry-After). Plain chat() calls only, so a retry can't
# duplicate streamed text.
GIGACHAT_MEMORY_CONCURRENCY = int(os.environ.get("GIGACHAT_MEMORY_CONCURRENCY", "2"))
GIGACHAT_MAX_RETRIES = int(os.environ.get("GIGACHAT_MAX_RETRIES", "6"))

_lock = Lock()
_client: "_ThrottledClient | None" = None
_slots = BoundedSemaphore(max(1, GIGACHAT_MEMORY_CONCURRENCY))


class _ThrottledClient:
    """The GigaChat client with at most GIGACHAT_MEMORY_CONCURRENCY calls in
    flight; everything else is passed straight through."""

    def __init__(self, client: GigaChat) -> None:
        self._client = client

    def chat(self, *args, **kwargs):
        with _slots:
            return self._client.chat(*args, **kwargs)

    def embeddings(self, *args, **kwargs):
        with _slots:
            return self._client.embeddings(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._client, name)


def gigachat_is_configured() -> bool:
    return bool(GIGACHAT_CREDENTIALS)


def get_client() -> "_ThrottledClient":
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

        _client = _ThrottledClient(GigaChat(
            base_url=GIGACHAT_BASE_URL,
            credentials=GIGACHAT_CREDENTIALS,
            scope=GIGACHAT_SCOPE,
            verify_ssl_certs=GIGACHAT_VERIFY_SSL,
            max_retries=GIGACHAT_MAX_RETRIES,
            retry_backoff_factor=1.0,
        ))

    return _client
