"""Dev tool: show what's actually in the FAISS vector index and how it
lines up with memory.db (the source of truth — see FaissMemoryIndex's own
docstring: "SQLite is the source of truth. The vector index is only a
search index and can be rebuilt from this repository at any time.").

The index itself only stores raw float vectors + memory IDs, nothing
readable — this joins those IDs back to memory.db's content so you can see
what each vector actually represents, and flags any IDs that are out of
sync between the two (which would mean the FAISS index needs rebuilding).

Run from anywhere: `python backend/inspect_memory_index.py [user_id] [--vectors]`
  user_id    optional — filter to one user's memories only
  --vectors  also print each vector's first 6 dimensions + L2 norm
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

# Windows consoles default to a legacy codepage (cp1251 etc.) that can't
# encode Cyrillic content/₽ signs — same fix competition_task/test_memory_emails.py
# uses for the same reason.
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
MEMORY_DIR = ROOT.parent / "memory"
sys.path.insert(0, str(MEMORY_DIR))

import faiss  # noqa: E402
import numpy as np  # noqa: E402

INDEX_PATH = DATA_DIR / "memory_index" / "memories.faiss"
MAPPING_PATH = DATA_DIR / "memory_index" / "memory_ids.json"
DB_PATH = DATA_DIR / "memory.db"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    show_vectors = "--vectors" in sys.argv
    filter_user = args[0] if args else None

    if not INDEX_PATH.exists() or not MAPPING_PATH.exists():
        print(f"FAISS-индекса ещё нет по пути {INDEX_PATH} — ничего не было заэмбежено.")
        return

    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    memory_ids: list[str] = mapping["memory_ids"]
    dimension = mapping["dimension"]
    index = faiss.read_index(str(INDEX_PATH))

    print(f"FAISS-индекс: {index.ntotal} вектор(ов), размерность={dimension}")
    print(f"  файл индекса: {INDEX_PATH}")
    print(f"  файл маппинга id: {MAPPING_PATH}\n")

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    db_rows = {
        row["id"]: row
        for row in conn.execute("SELECT id, user_id, type, content, status, created_at FROM memories")
    }

    vectors = index.reconstruct_n(0, index.ntotal) if show_vectors and index.ntotal else None

    shown = 0
    for position, memory_id in enumerate(memory_ids):
        row = db_rows.get(memory_id)
        if row is not None and filter_user and row["user_id"] != filter_user:
            continue
        shown += 1

        if row is None:
            print(f"[{position}] {memory_id} — ORPHAN VECTOR (no matching row in memory.db, index is stale)")
            continue

        preview = row["content"][:100] + ("…" if len(row["content"]) > 100 else "")
        print(f"[{position}] {memory_id}  user={row['user_id']}  type={row['type']}  status={row['status']}")
        print(f"      {preview}")
        if vectors is not None:
            v = vectors[position]
            print(f"      vector[:6]={np.round(v[:6], 4).tolist()}  |v|={float(np.linalg.norm(v)):.4f}")

    print(f"\n{shown} vector(s) shown.")

    # The reverse check: active memories in the DB with no vector at all
    # (e.g. added while the index was mid-rebuild) — also worth knowing.
    indexed_ids = set(memory_ids)
    missing = [
        row for row in db_rows.values()
        if row["id"] not in indexed_ids and row["status"] == "active" and (not filter_user or row["user_id"] == filter_user)
    ]
    if missing:
        print(f"\n{len(missing)} ACTIVE memory row(s) in memory.db have no vector in the index (needs a rebuild):")
        for row in missing:
            print(f"  {row['id']}  user={row['user_id']}  {row['content'][:80]}")


if __name__ == "__main__":
    main()
