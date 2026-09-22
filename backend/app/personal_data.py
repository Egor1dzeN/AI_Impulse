"""Raw store for imported user data (email / calendar / messenger).

Every imported record is normalized into one canonical shape, masked
(masking.py), scanned for injections/phishing (injection_guard.py) and
kept verbatim in app.db. Deterministic tools (personal_analytics.py) answer
exact questions from this store; the fuzzy fact memory only receives the
records that carry profile knowledge and passed the security scan.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import db
from .bank_parser import parse_bank_notification
from .injection_guard import SenderTrust, normalize, scan_text
from .masking import mask_sensitive

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = os.environ.get("DEFAULT_TIMEZONE", "Europe/Moscow")
SENT_FOLDERS = {"SENT", "SENT ITEMS", "SENT MAIL", "ОТПРАВЛЕННЫЕ", "OUTBOX", "[GMAIL]/SENT MAIL"}
BANK_SOURCE = "bank_email"


# ============================================================
# Time
# ============================================================


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Moscow")


def user_timezone(user_id: str) -> ZoneInfo:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT timezone, COUNT(*) AS c FROM raw_calendar_events "
            "WHERE user_id = ? AND timezone IS NOT NULL GROUP BY timezone ORDER BY c DESC LIMIT 1",
            (user_id,),
        ).fetchall()
    return _zone(rows[0]["timezone"] if rows else None)


def now_local(user_id: str) -> datetime:
    """Current moment in the user's timezone. ASSISTANT_NOW (ISO datetime)
    pins it for reproducible evaluation against a dataset's reference point."""
    tz = user_timezone(user_id)
    override = os.environ.get("ASSISTANT_NOW", "").strip()
    if override:
        try:
            pinned = datetime.fromisoformat(override)
            return pinned.replace(tzinfo=tz) if pinned.tzinfo is None else pinned.astimezone(tz)
        except ValueError:
            logger.warning("ASSISTANT_NOW=%r is not an ISO datetime, ignoring", override)
    return datetime.now(tz)


def parse_dt(value: Any, tz: ZoneInfo) -> datetime | None:
    """Parse ISO / epoch values; naive values are taken as the user's local time."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).astimezone(tz)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%a, %d %b %Y %H:%M:%S %z"):
            try:
                parsed = datetime.strptime(str(value).strip(), fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# ============================================================
# Normalization — tolerant to schema variations between datasets
# ============================================================


def _person(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {"name": str(value.get("name") or value.get("displayName") or "").strip(),
                "email": str(value.get("email") or value.get("address") or "").strip().lower()}
    if isinstance(value, str):
        match = re.match(r"\s*\"?([^\"<]*)\"?\s*<([^>]+)>", value)
        if match:
            return {"name": match.group(1).strip(), "email": match.group(2).strip().lower()}
        if "@" in value:
            return {"name": "", "email": value.strip().lower()}
        return {"name": value.strip(), "email": ""}
    return {"name": "", "email": ""}


def _people(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [p for p in (_person(v) for v in value) if p["name"] or p["email"]]
    if isinstance(value, str) and "," in value and "@" in value:
        return [_person(v) for v in value.split(",") if v.strip()]
    person = _person(value)
    return [person] if person["name"] or person["email"] else []


def _telegram_text(value: Any) -> str:
    if isinstance(value, list):
        return "".join(part if isinstance(part, str) else str(part.get("text", "")) for part in value)
    return str(value or "")


def _stable_id(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:20]


def normalize_email(record: dict[str, Any]) -> dict[str, Any]:
    sender = (_people(record.get("from")) or [{"name": "", "email": ""}])[0]
    labels = [str(l).upper() for l in record.get("labelIds") or record.get("labels") or []]
    folder = str(record.get("folder") or ("SENT" if "SENT" in labels else "INBOX")).upper()
    body = record.get("body") or record.get("text") or record.get("snippet") or ""
    if isinstance(body, dict):
        body = body.get("text") or body.get("plain") or body.get("html") or ""
    subject = str(record.get("subject") or "")
    date_value = record.get("date") or record.get("internalDate") or record.get("sent_at")
    return {
        "id": str(record.get("id") or _stable_id(sender["email"], date_value, subject)),
        "thread_id": str(record.get("thread_id") or record.get("threadId") or record.get("id") or ""),
        "folder": folder,
        "date": date_value,
        "from": sender,
        "to": _people(record.get("to")),
        "cc": _people(record.get("cc")),
        "subject": subject,
        "body": str(body),
        "attachments": record.get("attachments") or [],
    }


def normalize_calendar_event(record: dict[str, Any]) -> dict[str, Any]:
    def when(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return {"date": value} if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else {"dateTime": value}
        return {}

    event = dict(record)
    event["start"] = when(record.get("start") or record.get("dtstart"))
    event["end"] = when(record.get("end") or record.get("dtend"))
    event["summary"] = record.get("summary") or record.get("title") or "(без названия)"
    event["id"] = str(record.get("id") or _stable_id(event["summary"], event["start"]))
    return event


def normalize_messages(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten the mock JSONL shape as well as Telegram Desktop exports
    ({"chats": {"list": [...]}} or one chat {"name", "messages": [...]})."""
    flat: list[dict[str, Any]] = []
    for record in records:
        if isinstance(record.get("chats"), dict):
            for chat in record["chats"].get("list") or []:
                flat.extend(_chat_messages(chat))
        elif isinstance(record.get("messages"), list):
            flat.extend(_chat_messages(record))
        else:
            flat.append(record)

    owner = _guess_message_owner(flat)
    normalized = []
    for m in flat:
        sender = str(m.get("sender") or m.get("from") or m.get("author") or "").strip()
        out = m.get("out")
        if out is None:
            out = bool(owner) and sender == owner
        normalized.append({
            "chat": str(m.get("chat") or m.get("chat_name") or "chat"),
            "msg_id": str(m.get("msg_id") or m.get("id") or _stable_id(m.get("date"), sender, m.get("text"))),
            "date": m.get("date") or m.get("timestamp"),
            "sender": sender,
            "out": bool(out),
            "text": _telegram_text(m.get("text")).strip(),
            "reply_to": m.get("reply_to") or m.get("reply_to_message_id"),
            "service": m.get("service") or (m.get("action") if m.get("type") == "service" else None),
            "media": m.get("media") or m.get("media_type"),
        })
    return normalized


def _chat_messages(chat: dict[str, Any]) -> list[dict[str, Any]]:
    name = chat.get("name") or str(chat.get("id") or "chat")
    return [{**m, "chat": name} for m in chat.get("messages") or [] if m.get("type", "message") == "message"]


def _guess_message_owner(messages: list[dict[str, Any]]) -> str | None:
    """The account owner is the one sender present in (almost) every chat."""
    chats_by_sender: dict[str, set[str]] = {}
    for m in messages:
        sender = str(m.get("sender") or m.get("from") or "").strip()
        if sender:
            chats_by_sender.setdefault(sender, set()).add(str(m.get("chat") or m.get("chat_name")))
    if not chats_by_sender:
        return None
    return max(chats_by_sender, key=lambda s: len(chats_by_sender[s]))


# ============================================================
# Identity of the data owner
# ============================================================


def user_identity(user_id: str) -> dict[str, Any]:
    with db.get_conn() as conn:
        inbound_to = conn.execute(
            "SELECT to_json FROM raw_emails WHERE user_id = ? AND direction = 'in'", (user_id,)
        ).fetchall()
        sent_from = conn.execute(
            "SELECT from_name, from_email FROM raw_emails WHERE user_id = ? AND direction = 'out'", (user_id,)
        ).fetchall()
        calendar_self = conn.execute(
            "SELECT organizer_email, organizer_name, attendees_json FROM raw_calendar_events WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        out_senders = conn.execute(
            "SELECT sender, COUNT(*) AS c FROM raw_messages WHERE user_id = ? AND is_out = 1 "
            "GROUP BY sender ORDER BY c DESC LIMIT 1",
            (user_id,),
        ).fetchall()

    emails: Counter[str] = Counter()
    names: Counter[str] = Counter()
    for row in inbound_to:
        for person in json.loads(row["to_json"] or "[]"):
            if person.get("email"):
                emails[person["email"]] += 1
                if person.get("name"):
                    names[person["name"]] += 1
    for row in sent_from:
        if row["from_email"]:
            emails[row["from_email"]] += 3
        if row["from_name"]:
            names[row["from_name"]] += 3
    for row in calendar_self:
        for person in json.loads(row["attendees_json"] or "[]"):
            if person.get("self"):
                emails[person.get("email", "")] += 1
                if person.get("name"):
                    names[person["name"]] += 1

    own_email = emails.most_common(1)[0][0] if emails else None
    own_emails = {e for e, c in emails.items() if e and (e == own_email or c >= 3)}
    return {
        "name": names.most_common(1)[0][0] if names else None,
        "emails": sorted(own_emails),
        "primary_email": own_email,
        "messenger_name": out_senders[0]["sender"] if out_senders else None,
        "timezone": str(user_timezone(user_id)),
    }


# ============================================================
# Storage
# ============================================================


def _flags(reasons: list[str], **extra: Any) -> str | None:
    payload = {k: v for k, v in extra.items() if v}
    if reasons:
        payload["security"] = sorted(set(reasons))
    return json.dumps(payload, ensure_ascii=False) if payload else None


def store_records(user_id: str, source: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist one import batch. Returns counts plus `memory_records`: the
    normalized records that should also go through fact extraction."""
    if source == "email":
        return _store_emails(user_id, records)
    if source == "calendar":
        return _store_calendar(user_id, records)
    if source == "telegram":
        return _store_messages(user_id, records)
    raise ValueError(f"Unknown import source: {source!r}")


def _store_emails(user_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    tz = _zone(DEFAULT_TIMEZONE) if not _has_calendar(user_id) else user_timezone(user_id)
    normalized = [normalize_email(r) for r in records]
    with db.get_conn() as conn:
        for e in normalized:
            dt = parse_dt(e["date"], tz)
            subject = mask_sensitive(normalize(e["subject"]))
            body = mask_sensitive(normalize(e["body"]))
            reasons = scan_text(f"{e['subject']}\n{e['body']}")
            direction = "out" if e["folder"] in SENT_FOLDERS else "in"
            conn.execute(
                "INSERT OR REPLACE INTO raw_emails (user_id, id, thread_id, folder, direction, date, from_name, "
                "from_email, to_json, cc_json, subject, body, attachments_json, flags_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id, e["id"], e["thread_id"], e["folder"], direction, _iso(dt), e["from"]["name"],
                    e["from"]["email"], json.dumps(e["to"], ensure_ascii=False), json.dumps(e["cc"], ensure_ascii=False),
                    subject, body, json.dumps(e["attachments"], ensure_ascii=False, default=str), _flags(reasons),
                ),
            )

    identity = user_identity(user_id)
    own = set(identity["emails"])
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, from_email, direction, subject, body, date, to_json, flags_json FROM raw_emails WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        trust = SenderTrust([r["from_email"] for r in rows if r["direction"] == "in"], own)
        written_to = {
            p.get("email") for r in rows if r["direction"] == "out" or r["from_email"] in own
            for p in json.loads(r["to_json"] or "[]")
        }
        for cal in conn.execute("SELECT attendees_json, organizer_email FROM raw_calendar_events WHERE user_id = ?", (user_id,)):
            written_to.add(cal["organizer_email"])
            written_to.update(a.get("email") for a in json.loads(cal["attendees_json"] or "[]"))
        for r in rows:
            if r["direction"] == "in" and r["from_email"] in own:
                conn.execute("UPDATE raw_emails SET direction = 'out' WHERE user_id = ? AND id = ?", (user_id, r["id"]))
            reasons = set((json.loads(r["flags_json"]) if r["flags_json"] else {}).get("security", []))
            is_inbound = r["direction"] == "in" and r["from_email"] not in own
            reasons.update(trust.check(r["from_email"]) if is_inbound else [])
            # A one-off sender the user never wrote to may be legit (a service
            # sign-up) but is not a trusted source of facts about the user.
            unverified = is_inbound and not trust.is_established(r["from_email"]) and r["from_email"] not in written_to
            conn.execute(
                "UPDATE raw_emails SET flags_json = ? WHERE user_id = ? AND id = ?",
                (_flags(sorted(reasons), unverified_sender=unverified), user_id, r["id"]),
            )

    transactions, flagged, memory_records = _extract_transactions_and_select(user_id, normalized)
    return {
        "stored": len(normalized),
        "transactions": transactions,
        "flagged": flagged,
        "memory_records": memory_records,
    }


def _extract_transactions_and_select(user_id: str, normalized: list[dict[str, Any]]) -> tuple[int, list[dict], list[dict]]:
    ids = [e["id"] for e in normalized]
    with db.get_conn() as conn:
        rows = {
            r["id"]: r
            for r in conn.execute(
                f"SELECT id, direction, date, subject, body, from_name, from_email, flags_json FROM raw_emails "
                f"WHERE user_id = ? AND id IN ({','.join('?' * len(ids))})",
                (user_id, *ids),
            ).fetchall()
        } if ids else {}

    transactions = 0
    flagged: list[dict[str, Any]] = []
    memory_records: list[dict[str, Any]] = []
    for e in normalized:
        row = rows.get(e["id"])
        if row is None:
            continue
        flags = json.loads(row["flags_json"]) if row["flags_json"] else {}
        security = flags.get("security", [])
        if security:
            flagged.append({"id": e["id"], "subject": row["subject"], "from": row["from_email"], "reasons": security})
            continue
        if flags.get("unverified_sender"):
            continue
        if row["direction"] == "in":
            tx = parse_bank_notification(row["subject"], row["body"], row["date"])
            if tx:
                _store_transaction(user_id, e["id"], tx)
                transactions += 1
                continue
        memory_records.append({
            **e,
            "from": [e["from"]],
            "folder": "SENT" if row["direction"] == "out" else e["folder"],
            "subject": row["subject"],
            "body": row["body"],
            "date": row["date"],
        })
    return transactions, flagged, memory_records


def _store_transaction(user_id: str, email_id: str, tx: dict[str, Any]) -> None:
    tx_date = (tx["datetime"] or "")[:10] or date.today().isoformat()
    note = tx["merchant"] or None
    with db.get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO expenses (id, user_id, amount, type, category, note, date, created_at, "
            "source, external_id, merchant, balance) VALUES ("
            "COALESCE((SELECT id FROM expenses WHERE user_id = ? AND source = ? AND external_id = ?), ?), "
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id, BANK_SOURCE, email_id, db.new_id(),
                user_id, tx["amount"], tx["kind"], tx["category"], note, tx_date, db.now_iso(),
                BANK_SOURCE, email_id, tx["merchant"], tx["balance"],
            ),
        )


def _has_calendar(user_id: str) -> bool:
    with db.get_conn() as conn:
        return conn.execute("SELECT 1 FROM raw_calendar_events WHERE user_id = ? LIMIT 1", (user_id,)).fetchone() is not None


def _store_calendar(user_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    events = [normalize_calendar_event(r) for r in records]
    tz_counts = Counter(
        (e["start"].get("timeZone") or e.get("timeZone")) for e in events if (e["start"].get("timeZone") or e.get("timeZone"))
    )
    default_tz = _zone(tz_counts.most_common(1)[0][0] if tz_counts else DEFAULT_TIMEZONE)

    flagged: list[dict[str, Any]] = []
    memory_records: list[dict[str, Any]] = []
    with db.get_conn() as conn:
        for e in events:
            tz = _zone(e["start"].get("timeZone") or e.get("timeZone") or str(default_tz))
            all_day = "date" in e["start"] and "dateTime" not in e["start"]
            if all_day:
                start, end = e["start"].get("date"), e["end"].get("date") or e["start"].get("date")
            else:
                start = _iso(parse_dt(e["start"].get("dateTime"), tz))
                end = _iso(parse_dt(e["end"].get("dateTime"), tz)) or start
            description = mask_sensitive(normalize(str(e.get("description") or "")))
            reasons = scan_text(f"{e['summary']}\n{e.get('description') or ''}\n{e.get('location') or ''}")
            attendees = [
                {
                    "name": a.get("displayName") or "",
                    "email": (a.get("email") or "").lower(),
                    "self": bool(a.get("self")),
                    "organizer": bool(a.get("organizer")),
                    "optional": bool(a.get("optional")),
                    "response": a.get("responseStatus"),
                }
                for a in e.get("attendees") or []
            ]
            organizer = e.get("organizer") or {}
            original = e.get("originalStartTime") or {}
            importance = (((e.get("extendedProperties") or {}).get("private") or {}).get("importance")
                          or (((e.get("extendedProperties") or {}).get("shared") or {}).get("importance")))
            conn.execute(
                "INSERT OR REPLACE INTO raw_calendar_events (user_id, id, series_id, summary, description, location, "
                "start, end, all_day, timezone, status, transparency, event_type, importance, organizer_email, "
                "organizer_name, attendees_json, recurrence_json, original_start, flags_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id, e["id"], e.get("recurringEventId"), mask_sensitive(e["summary"]), description,
                    mask_sensitive(str(e.get("location") or "")) or None, start, end, int(all_day),
                    e["start"].get("timeZone") or str(tz), e.get("status") or "confirmed", e.get("transparency"),
                    e.get("eventType") or "default", importance, (organizer.get("email") or "").lower() or None,
                    organizer.get("displayName"), json.dumps(attendees, ensure_ascii=False),
                    json.dumps(e.get("recurrence")) if e.get("recurrence") else None,
                    original.get("dateTime") or original.get("date"), _flags(reasons),
                ),
            )
            if reasons:
                flagged.append({"id": e["id"], "summary": e["summary"], "reasons": reasons})
                # The meeting itself is real — only its description is untrusted.
                memory_records.append({**e, "description": ""})
            elif e.get("status") != "cancelled":
                memory_records.append({**e, "description": description})

    # Recurring instances repeat the same fact; one per series is enough for memory.
    seen_series: set[str] = set()
    deduped = []
    for e in memory_records:
        series = e.get("recurringEventId")
        if series:
            if series in seen_series:
                continue
            seen_series.add(series)
        deduped.append(e)
    return {"stored": len(events), "flagged": flagged, "memory_records": deduped}


def _store_messages(user_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    tz = user_timezone(user_id)
    messages = normalize_messages(records)
    flagged: list[dict[str, Any]] = []
    memory_records: list[dict[str, Any]] = []
    with db.get_conn() as conn:
        for m in messages:
            text = mask_sensitive(normalize(m["text"]))
            reasons = scan_text(m["text"])
            conn.execute(
                "INSERT OR REPLACE INTO raw_messages (user_id, chat, msg_id, date, sender, is_out, text, reply_to, flags_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id, m["chat"], m["msg_id"], _iso(parse_dt(m["date"], tz)), m["sender"], int(m["out"]),
                    text, str(m["reply_to"]) if m["reply_to"] else None, _flags(reasons, media=m.get("media")),
                ),
            )
            if reasons:
                flagged.append({"chat": m["chat"], "msg_id": m["msg_id"], "text": text[:120], "reasons": reasons})
            elif text:
                memory_records.append({**m, "text": text, "_dt": parse_dt(m["date"], tz)})
    return {"stored": len(messages), "flagged": flagged, "memory_records": _conversation_chunks(memory_records)}


CHUNK_GAP = timedelta(hours=3)
CHUNK_MAX_MESSAGES = 20


def _conversation_chunks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group messages into conversation windows (same chat, pauses under 3h,
    at most 20 messages) so fact extraction sees each reply in context
    ("Бегаю, куда я денусь" means nothing alone) and runs ~10x fewer times."""
    by_chat: dict[str, list[dict[str, Any]]] = {}
    for m in messages:
        by_chat.setdefault(m["chat"], []).append(m)
    chunks: list[dict[str, Any]] = []
    for chat, items in by_chat.items():
        items.sort(key=lambda m: (m["_dt"] or datetime.min.replace(tzinfo=timezone.utc)))
        current: list[dict[str, Any]] = []
        for m in items:
            if current and (
                len(current) >= CHUNK_MAX_MESSAGES
                or (m["_dt"] and current[-1]["_dt"] and m["_dt"] - current[-1]["_dt"] > CHUNK_GAP)
            ):
                chunks.append(_chunk(chat, current))
                current = []
            current.append(m)
        if current:
            chunks.append(_chunk(chat, current))
    return chunks


def _chunk(chat: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "chat": chat,
        "msg_id": f"{items[0]['msg_id']}-{items[-1]['msg_id']}",
        "date": _iso(items[0]["_dt"]),
        "messages": [{"sender": m["sender"], "out": m["out"], "text": m["text"]} for m in items],
    }


def delete_all(user_id: str) -> None:
    with db.get_conn() as conn:
        for table in ("raw_emails", "raw_calendar_events", "raw_messages"):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM expenses WHERE user_id = ? AND source = ?", (user_id, BANK_SOURCE))


def data_overview(user_id: str) -> dict[str, Any]:
    with db.get_conn() as conn:
        email = conn.execute(
            "SELECT COUNT(*) AS n, MIN(date) AS first, MAX(date) AS last FROM raw_emails WHERE user_id = ?", (user_id,)
        ).fetchone()
        cal = conn.execute(
            "SELECT COUNT(*) AS n, MIN(start) AS first, MAX(start) AS last FROM raw_calendar_events WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        msg = conn.execute(
            "SELECT COUNT(*) AS n, MIN(date) AS first, MAX(date) AS last FROM raw_messages WHERE user_id = ?", (user_id,)
        ).fetchone()
        tx = conn.execute(
            "SELECT COUNT(*) AS n, MIN(date) AS first, MAX(date) AS last FROM expenses WHERE user_id = ? AND source = ?",
            (user_id, BANK_SOURCE),
        ).fetchone()
        chats = conn.execute(
            "SELECT chat, COUNT(*) AS n, MIN(date) AS first, MAX(date) AS last FROM raw_messages WHERE user_id = ? "
            "GROUP BY chat ORDER BY n DESC",
            (user_id,),
        ).fetchall()
        chat_senders = conn.execute(
            "SELECT chat, sender, COUNT(*) AS n FROM raw_messages WHERE user_id = ? AND is_out = 0 GROUP BY chat, sender",
            (user_id,),
        ).fetchall()

    participants: dict[str, list[str]] = {}
    for row in chat_senders:
        participants.setdefault(row["chat"], []).append(row["sender"])
    return {
        "identity": user_identity(user_id),
        "emails": dict(email),
        "calendar_events": dict(cal),
        "messages": dict(msg),
        "bank_transactions": dict(tx),
        "chats": [
            {**dict(c), "participants": sorted(participants.get(c["chat"], []))} for c in chats
        ],
    }


def iter_days(start: date, end: date) -> Iterable[date]:
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)
