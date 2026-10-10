"""
SQLite hash-cache: avoid re-running Docling+Groq on a file we've
already processed. Deliberately simple — one table, no ORM. Upgrade to
Postgres later only if/when concurrent-write load actually demands it.
"""

import sqlite3
import hashlib
import json
import os
from contextlib import contextmanager

# Anchored to this file's own directory rather than left as a bare
# relative path — a bare "documents.db" resolves against the current
# working directory, which depends on where the process was launched
# from (e.g. `uvicorn api:app` run from a different folder, or a cron
# job with its own CWD). That silently created/read a *different*
# documents.db than you expected. Anchoring to __file__ makes the
# location independent of launch directory.
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "documents.db")


def init_db():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_hash TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL,
                extracted_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def hash_file_bytes(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def get_cached_result(file_hash: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT status, extracted_json FROM documents WHERE file_hash = ?",
            (file_hash,),
        ).fetchone()
    if row is None:
        return None
    status, extracted_json = row
    return {
        "status": status,
        "result": json.loads(extracted_json) if extracted_json else None,
    }


def save_result(file_hash: str, status: str, result: dict | None):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO documents (file_hash, status, extracted_json)
            VALUES (?, ?, ?)
            ON CONFLICT(file_hash) DO UPDATE SET
                status = excluded.status,
                extracted_json = excluded.extracted_json
            """,
            (file_hash, status, json.dumps(result) if result is not None else None),
        )