"""Consistent snapshot of the stand's data: app.db, memory.db (SQLite online
backup API — safe while the backend is running), the FAISS index and
today's audit files. Keeps the newest KEEP snapshots.

    python backend/scripts/backup_db.py                 # -> ~/backups/db/<YYYYmmdd-HHMMSS>/
    BACKUP_DIR=/path KEEP=48 python backend/scripts/backup_db.py

Hourly on the server (crontab -e):
    0 * * * * cd ~/ai_impulse && backend/.venv/bin/python backend/scripts/backup_db.py >> ~/backups/backup.log 2>&1
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
DATA = BACKEND / "data"
AUDIT = BACKEND / "audit"
TARGET = Path(os.environ.get("BACKUP_DIR", "") or Path.home() / "backups" / "db")
KEEP = int(os.environ.get("KEEP", "72"))


def main() -> None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = TARGET / stamp
    out.mkdir(parents=True, exist_ok=True)
    for name in ("app.db", "memory.db"):
        src = DATA / name
        if src.exists():
            with sqlite3.connect(src) as source, sqlite3.connect(out / name) as dest:
                source.backup(dest)
    if (DATA / "memory_index").exists():
        shutil.copytree(DATA / "memory_index", out / "memory_index")
    if AUDIT.exists():
        shutil.copytree(AUDIT, out / "audit")
    snapshots = sorted(p for p in TARGET.iterdir() if p.is_dir())
    for old in snapshots[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"{stamp} backup -> {out} ({size / 1e6:.1f} MB), kept {min(len(snapshots), KEEP)}")


if __name__ == "__main__":
    main()
