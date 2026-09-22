"""Architectural masking of critical secrets: card numbers, CVV, bank
account numbers, passport/ID numbers, passwords, tokens and API keys.

Applied at every boundary where data leaves its origin: on ingestion
(before anything is stored, embedded or sent to the LLM), on every tool
result entering the LLM prompt, on the user's chat message, on the saved
reply, on API responses and in logs (MaskingLogFilter). Ordinary personal
data (name, phone, address) is intentionally left alone.
"""
from __future__ import annotations

import logging
import re
from typing import Any

MASK = "•"

_DIGIT_SEP = r"[ \- ]?"


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _keep_last4(digits: str) -> str:
    return f"{MASK * 4} {digits[-4:]}"


_CARD_RE = re.compile(rf"(?<!\d)(?<!\d[.,])(?:\d{_DIGIT_SEP}){{12,18}}\d(?!\d|[.,]\d)")


def _mask_card(match: re.Match[str]) -> str:
    raw = match.group(0)
    digits = re.sub(r"\D", "", raw)
    grouped_like_card = bool(re.fullmatch(r"\d{4}([ \- ])\d{4}\1\d{4}\1\d{4}(\1\d{1,3})?", raw))
    if len(digits) == 20:
        return f"[счёт {_keep_last4(digits)}]"
    if len(digits) == 16 or (13 <= len(digits) <= 19 and (grouped_like_card or _luhn_ok(digits))):
        return f"[карта {_keep_last4(digits)}]"
    return raw


_CVV_RE = re.compile(r"(?i)\b(cvv2?|cvc2?|cid|код безопасности|код с обратной стороны)(\s*[:=\-]?\s*)(\d{3,4})\b")
_ACCOUNT_RE = re.compile(
    r"(?i)\b(р/с|р\\с|к/с|расч[её]тн\w* сч[её]т\w*|корр?\w* сч[её]т\w*|сч[её]т\w*|account(?: number)?|iban)"
    r"(\s*(?:№|no\.?|#)?\s*[:=\-]?\s*)([A-Z]{2}\d{2}[A-Z0-9 ]{10,30}|\d[\d ]{9,30}\d)"
)
_PASSPORT_RE = re.compile(
    r"(?i)\b(паспорт\w*|passport|серия(?: и номер)?|снилс|инн|вод\w* удостоверени\w*|загранпаспорт\w*)"
    r"([^\d\n]{0,25})(\d[\d \-]{5,16}\d)"
)
_SECRET_KV_RE = re.compile(
    r"(?i)\b(парол\w*|password|passwd|pwd|pin(?:-?код)?|пин(?:-?код)?|код из смс|sms[- ]?код|одноразов\w* код|"
    r"token|токен\w*|api[_ \-]?key|апи[- ]?ключ\w*|secret|секрет\w*|client[_ ]secret|access[_ ]key|ключ доступа)"
    r"(\s*[:=]\s*|\s+)([^\s,;\[]{4,})"
)
_KNOWN_TOKEN_RES = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9\-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),
    re.compile(r"\bya29\.[0-9A-Za-z_\-]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
]

_SECRET_WORDS_WITHOUT_SPACE_FORM = {"pin", "пин"}
_URL_SECRET_RE = re.compile(
    r"(?i)([?&](?:code|token|access_token|refresh_token|id_token|key|api_key|apikey|secret|client_secret|password|state)=)[^&\s\"']+"
)


def _mask_secret_kv(match: re.Match[str]) -> str:
    key, sep, value = match.group(1), match.group(2), match.group(3)
    if not sep.strip():
        # "пароль от почты" / "token bucket" — a bare following word is not
        # a value unless it actually looks like a secret (has digits).
        if key.lower() in _SECRET_WORDS_WITHOUT_SPACE_FORM or not re.search(r"\d", value):
            return match.group(0)
    return f"{key}{sep}[скрыто]"


def _mask_document(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(3))
    return f"{match.group(1)}{match.group(2)}[документ {MASK * 4}{digits[-2:]}]"


def _mask_account(match: re.Match[str]) -> str:
    value = re.sub(r"[^0-9A-Z]", "", match.group(3))
    return f"{match.group(1)}{match.group(2)}[счёт {_keep_last4(value)}]"


def mask_sensitive(text: str) -> str:
    if not text or not isinstance(text, str):
        return text
    text = _CVV_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[скрыто]", text)
    text = _URL_SECRET_RE.sub(lambda m: f"{m.group(1)}[скрыто]", text)
    text = _SECRET_KV_RE.sub(_mask_secret_kv, text)
    for pattern in _KNOWN_TOKEN_RES:
        text = pattern.sub("[секрет скрыт]", text)
    text = _PASSPORT_RE.sub(_mask_document, text)
    text = _ACCOUNT_RE.sub(_mask_account, text)
    text = _CARD_RE.sub(_mask_card, text)
    return text


def mask_obj(value: Any) -> Any:
    if isinstance(value, str):
        return mask_sensitive(value)
    if isinstance(value, dict):
        return {k: mask_obj(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_obj(v) for v in value]
    if isinstance(value, tuple):
        return tuple(mask_obj(v) for v in value)
    return value


class MaskingLogFilter(logging.Filter):
    """Masks secrets in every log record before any handler formats it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — a malformed record must still be logged
            return True
        masked = mask_sensitive(message)
        if masked != message:
            record.msg = masked
            record.args = None
        return True


_LOG_FILTER = MaskingLogFilter()


def install_log_masking() -> None:
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            if _LOG_FILTER not in handler.filters:
                handler.addFilter(_LOG_FILTER)
