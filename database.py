"""
SQLite storage. Deliberately simple — plain SQL, no ORM. Upgrade to
Postgres later only if/when concurrent-write load actually demands it.

Three tables:
  documents           extraction cache: file hash(+mode) -> extracted JSON,
                      so an identical file never costs another Docling+LLM run
  shipments           one row per shipment the user creates
  shipment_documents  the files attached to a shipment, each with its own copy
                      of the extraction. A shipment is an accumulating set:
                      documents can arrive in separate emails over time, and
                      the merge is simply recomputed from whatever is attached.
"""

import sqlite3
import hashlib
import json
import os
from contextlib import contextmanager

# Anchored to this file's own directory rather than left as a bare
# relative path — a bare "documents.db" resolves against the current
# working directory, which depends on where the process was launched
# from. Anchoring to __file__ makes the location independent of that.
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shipments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Each attached document stores its OWN copy of the extraction, so a
        # later EXTRACTION_VERSION bump (which invalidates the cache) doesn't
        # silently change or lose what's already attached, and a future human
        # review step can correct a stored extraction.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shipment_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shipment_id INTEGER NOT NULL,
                file_hash TEXT NOT NULL,
                filename TEXT,
                mode TEXT,
                extraction_version INTEGER,
                extracted_json TEXT NOT NULL,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (shipment_id, file_hash)
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


# ---------------------------------------------------------------------------
# Extraction cache
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Shipments
# ---------------------------------------------------------------------------

def create_shipment(label: str) -> int:
    with _connect() as conn:
        cur = conn.execute("INSERT INTO shipments (label) VALUES (?)", (label,))
        return cur.lastrowid


def get_shipment(shipment_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, label, created_at FROM shipments WHERE id = ?",
            (shipment_id,),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "label": row[1], "created_at": row[2]}


def list_shipments() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("""
            SELECT s.id, s.label, s.created_at, COUNT(d.id)
            FROM shipments s
            LEFT JOIN shipment_documents d ON d.shipment_id = s.id
            GROUP BY s.id
            ORDER BY s.id DESC
        """).fetchall()
    return [
        {"id": r[0], "label": r[1], "created_at": r[2], "document_count": r[3]}
        for r in rows
    ]


def delete_shipment(shipment_id: int) -> bool:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM shipment_documents WHERE shipment_id = ?", (shipment_id,)
        )
        cur = conn.execute("DELETE FROM shipments WHERE id = ?", (shipment_id,))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Documents attached to a shipment
# ---------------------------------------------------------------------------

def add_shipment_document(
    shipment_id: int,
    file_hash: str,
    filename: str | None,
    mode: str | None,
    extraction_version: int | None,
    fields: dict,
) -> tuple[int, bool]:
    """
    Attach an extraction to a shipment. Returns (document_id, created).
    The same file attached twice to one shipment is ignored (created=False);
    the same file in two different shipments is fine.
    """
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO shipment_documents
                (shipment_id, file_hash, filename, mode, extraction_version, extracted_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (shipment_id, file_hash, filename, mode, extraction_version, json.dumps(fields)),
        )
        if cur.rowcount > 0:
            return cur.lastrowid, True
        row = conn.execute(
            "SELECT id FROM shipment_documents WHERE shipment_id = ? AND file_hash = ?",
            (shipment_id, file_hash),
        ).fetchone()
        return row[0], False


def list_shipment_documents(shipment_id: int) -> list[dict]:
    """Oldest first: the order documents were added is the 'received' order the
    merge uses as a tie-break."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, file_hash, filename, mode, extraction_version,
                   extracted_json, added_at
            FROM shipment_documents
            WHERE shipment_id = ?
            ORDER BY id ASC
            """,
            (shipment_id,),
        ).fetchall()
    return [
        {
            "id": r[0], "file_hash": r[1], "filename": r[2], "mode": r[3],
            "extraction_version": r[4], "fields": json.loads(r[5]), "added_at": r[6],
        }
        for r in rows
    ]


def delete_shipment_document(shipment_id: int, document_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM shipment_documents WHERE id = ? AND shipment_id = ?",
            (document_id, shipment_id),
        )
        return cur.rowcount > 0