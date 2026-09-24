"""Read the audit trail (backend/audit/audit-YYYY-MM-DD.jsonl) back as a timeline.

    python backend/scripts/audit_view.py                          # сводка: кто, сколько запросов и ходов чата
    python backend/scripts/audit_view.py --user Test1             # хронология одного аккаунта
    python backend/scripts/audit_view.py --user Test1 --full      # с аргументами/результатами инструментов целиком
    python backend/scripts/audit_view.py --conversation <id>      # один чат
    python backend/scripts/audit_view.py --since "2026-09-25 10:00" --until "2026-09-25 12:00"
    python backend/scripts/audit_view.py --event chat_turn_start --user Test2   # только вопросы
    python backend/scripts/audit_view.py --user Test1 --json > test1.jsonl     # выгрузка как есть
    python backend/scripts/audit_view.py --prompt <sha>           # системный промпт хода
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

AUDIT_DIR = Path(__file__).resolve().parent.parent / "audit"


def load(args) -> list[dict]:
    rows = []
    for path in sorted(AUDIT_DIR.glob("audit-*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                ts = r.get("ts", "")
                if args.since and ts[:19].replace("T", " ") < args.since:
                    continue
                if args.until and ts[:19].replace("T", " ") > args.until:
                    continue
                if args.user and args.user not in (r.get("username"), r.get("user_id")):
                    continue
                if args.conversation and r.get("conversation_id") != args.conversation:
                    continue
                if args.event and r.get("event") != args.event:
                    continue
                if not args.http and r.get("event") == "http" and not args.event:
                    # the chat turn records already cover /api/chat; keep other requests
                    if r.get("path") == "/api/chat" or (r.get("method") == "GET" and r.get("status", 0) < 400):
                        continue
                rows.append(r)
    return rows


def short(value, n) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def show(rows: list[dict], full: bool) -> None:
    n = 10**9 if full else 220
    for r in rows:
        ts = r.get("ts", "")[:23].replace("T", " ")
        who = r.get("username") or (r.get("user_id") or "-")[:8]
        ev = r.get("event")
        head = f"{ts}  {who:<12} "
        if ev == "chat_turn_start":
            print(f"\n{head}▶ ВОПРОС  [чат {str(r.get('conversation_id'))[:8]}] {short(r.get('message'), n)}")
            print(f"{'':37}промпт {r.get('system_prompt_sha')}, история {r.get('history_messages')} сообщ., "
                  f"Gmail {'да' if r.get('gmail_connected') else 'нет'}")
        elif ev == "tool_call":
            mark = "✖ ОТКЛОНЁН" if r.get("refused") else "⚙"
            print(f"{head}  {mark} {r.get('tool')}({short(r.get('arguments'), n)})")
            print(f"{'':39}→ {short(r.get('result'), n)}")
        elif ev == "model_note":
            print(f"{head}  💭 {short(r.get('text'), n)}")
        elif ev == "chat_retry":
            print(f"{head}  ↻ повтор: {r.get('reason')} {short(r.get('reply') or '', n)}")
        elif ev == "chat_error":
            print(f"{head}  ⚠ ошибка: {r.get('kind')}")
        elif ev == "chat_turn_done":
            print(f"{head}◀ ОТВЕТ  ({r.get('duration_ms')} мс, инструменты: {', '.join(r.get('used_tools') or []) or '—'})")
            print(f"{'':37}{short(r.get('reply'), n if full else 600)}")
        elif ev == "memory_update":
            decisions = r.get("decisions") or []
            summary = "; ".join(f"{d['action']}: {short(d['content'], 90)}" for d in decisions) or "0 фактов"
            print(f"{head}  🧠 память ({(r.get('source') or {}).get('type')}): {short(summary, n)}")
        elif ev == "http":
            status = r.get("status")
            flag = " ⚠" if (status or 0) >= 400 else ""
            print(f"{head}  {r.get('method')} {r.get('path')} → {status}{flag} ({r.get('duration_ms')} мс, IP {r.get('ip')})"
                  + (f"\n{'':39}запрос: {short(r.get('request'), n)}" if r.get("request") else "")
                  + (f"\n{'':39}ответ: {short(r.get('response'), n)}" if r.get("response") and (full or flag) else ""))
        else:
            print(f"{head}  {ev}: {short({k: v for k, v in r.items() if k not in ('ts', 'event')}, n)}")


def summary(rows: list[dict]) -> None:
    per_user = defaultdict(Counter)
    ips = defaultdict(set)
    first, last = {}, {}
    for r in rows:
        who = r.get("username") or r.get("user_id") or "(без входа)"
        per_user[who][r.get("event")] += 1
        if r.get("event") == "http" and (r.get("status") or 0) >= 400:
            per_user[who]["http_errors"] += 1
        if r.get("ip"):
            ips[who].add(r["ip"])
        first.setdefault(who, r.get("ts", ""))
        last[who] = r.get("ts", "")
    print(f"{'аккаунт':<16}{'вопросов':>9}{'инструм.':>9}{'откл.':>7}{'ошибок':>8}{'память':>8}  период / IP")
    for who, c in sorted(per_user.items(), key=lambda kv: -kv[1]["chat_turn_start"]):
        print(f"{who:<16}{c['chat_turn_start']:>9}{c['tool_call']:>9}{c.get('refused', 0):>7}"
              f"{c['http_errors'] + c['chat_error']:>8}{c['memory_update']:>8}  "
              f"{first[who][:16]} … {last[who][:16]}  {', '.join(sorted(ips[who]))}")


def main() -> None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", help="логин или user_id")
    ap.add_argument("--conversation")
    ap.add_argument("--event")
    ap.add_argument("--since", help='"YYYY-MM-DD HH:MM"')
    ap.add_argument("--until")
    ap.add_argument("--full", action="store_true", help="без сокращений")
    ap.add_argument("--http", action="store_true", help="показать и все успешные GET")
    ap.add_argument("--json", action="store_true", help="выгрузить записи как JSONL")
    ap.add_argument("--prompt", help="показать системный промпт по sha")
    args = ap.parse_args()

    if args.prompt:
        print((AUDIT_DIR / "prompts" / f"{args.prompt}.txt").read_text(encoding="utf-8"))
        return
    rows = load(args)
    if args.json:
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
    elif args.user or args.conversation or args.event or args.since:
        show(rows, args.full)
    else:
        for r in rows:
            if r.get("event") == "tool_call" and r.get("refused"):
                r["_refused"] = True
        summary(rows)


if __name__ == "__main__":
    main()
