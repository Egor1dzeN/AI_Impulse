from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid

import imageio_ffmpeg
import requests
from fastapi import HTTPException

from . import db

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()

logger = logging.getLogger(__name__)

# TEMP debug aid while chasing the "Format not recognised" 400 from
# cloud.ru's Whisper endpoint (it couldn't open the browser-recorded audio
# from a BytesIO — likely doesn't accept webm/opus) — every upload gets
# dumped here so we can play it back and see what MediaRecorder actually
# produced, before deciding whether to fix the mimeType or transcode
# server-side. Remove this once the real format issue is nailed down.
VOICE_DEBUG_DIR = db.DATA_DIR / "voice_debug"


def save_debug_copy(file_bytes: bytes) -> str:
    VOICE_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = VOICE_DEBUG_DIR / f"{uuid.uuid4().hex}.mp3"
    path.write_bytes(file_bytes)
    logger.info("voice debug: saved upload to %s (%d bytes)", path, len(file_bytes))
    return str(path)


# Same cloud.ru key/base as memory/cloud_ru_embedder.py and
# cloud_ru_reranker.py (naming kept: GIGA_CHAT_EMBEDDING is actually a
# cloud.ru key, not GigaChat) — this is a backend/app-level feature (used
# directly by the chat UI), so it keeps its own tiny config instead of
# importing from the memory/ package, matching how chat_gigachat.py already
# duplicates GigaChat's own client config rather than sharing memory/'s.
CLOUD_RU_API_KEY = os.environ.get("GIGA_CHAT_EMBEDDING")
CLOUD_RU_BASE_URL = os.environ.get("CLOUD_RU_BASE_URL", "https://foundation-models.api.cloud.ru/v1")
WHISPER_MODEL = os.environ.get("CLOUD_RU_WHISPER_MODEL", "openai/whisper-large-v3")


def voice_input_is_configured() -> bool:
    return bool(CLOUD_RU_API_KEY)


def _transcode_to_mp3(file_bytes: bytes) -> bytes:
    """The browser's MediaRecorder only ever produces a webm/opus (or
    ogg/opus in Firefox) container — cloud.ru's Whisper endpoint fails to
    open that (its "Format not recognised" 400, confirmed via a saved
    sample that VLC played fine by sniffing content, ignoring the .mp3
    extension). No browser can encode straight to mp3/wav itself, so this
    transcodes server-side via a bundled static ffmpeg binary
    (imageio-ffmpeg) instead of requiring a system-wide ffmpeg install."""
    proc = subprocess.run(
        [FFMPEG_EXE, "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-f", "mp3", "pipe:1"],
        input=file_bytes,
        capture_output=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise HTTPException(
            status_code=422, detail=f"could not process audio: {proc.stderr.decode(errors='replace').strip()}"
        )
    return proc.stdout


def transcribe_audio(file_bytes: bytes, language: str = "ru") -> str:
    if not CLOUD_RU_API_KEY:
        raise HTTPException(status_code=503, detail="voice input is not configured on this server")

    mp3_bytes = _transcode_to_mp3(file_bytes)

    resp = requests.post(
        f"{CLOUD_RU_BASE_URL}/audio/transcriptions",
        headers={"Authorization": f"Bearer {CLOUD_RU_API_KEY}"},
        data={
            "model": WHISPER_MODEL,
            "response_format": "text",
            "temperature": "0.0",
            "language": language,
        },
        files={"file": ("audio.mp3", mp3_bytes, "audio/mpeg")},
        timeout=60,
    )
    if resp.status_code >= 400:
        # Surface cloud.ru's own message (e.g. "Format not recognised")
        # instead of letting requests.raise_for_status() turn this into an
        # unhandled HTTPError that FastAPI flattens to a generic 500.
        raise HTTPException(status_code=422, detail=f"transcription failed: {resp.text.strip()}")
    text = resp.text.strip()
    # Despite response_format="text", this cloud.ru endpoint was observed
    # to still return a JSON body ({"text": "...", "usage": {...}}) rather
    # than the bare transcript OpenAI's Whisper API docs describe for that
    # format — unwrap it when present instead of returning raw JSON to callers.
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text
    return str(data.get("text", text)).strip()
