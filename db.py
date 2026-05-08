import sqlite3
from datetime import datetime
from contextlib import contextmanager

DB_FILE = "snapshots.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS universities (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    url           TEXT    NOT NULL,
    region        TEXT,
    is_competitor INTEGER NOT NULL DEFAULT 0,
    notes         TEXT,
    added_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS snapshots (
    uni_id          INTEGER NOT NULL,
    page_url        TEXT    NOT NULL,
    doc_type        TEXT,
    content_clean   TEXT,
    content_hash    TEXT    NOT NULL,
    last_checked    TEXT    NOT NULL,
    last_changed_at TEXT,
    PRIMARY KEY (uni_id, page_url),
    FOREIGN KEY (uni_id) REFERENCES universities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS change_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    uni_id         INTEGER NOT NULL,
    page_url       TEXT    NOT NULL,
    doc_type       TEXT,
    detected_at    TEXT    NOT NULL,
    llm_meaningful INTEGER,
    llm_reason     TEXT,
    diff_excerpt   TEXT,
    FOREIGN KEY (uni_id) REFERENCES universities(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_change_events_uni     ON change_events(uni_id, detected_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_doc_type    ON snapshots(doc_type);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema():
    with connect() as conn:
        conn.executescript(SCHEMA)


def list_universities():
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, name, url, region, is_competitor, notes "
            "FROM universities ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_university(name, url, region=None, is_competitor=False, notes=None):
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO universities (name, url, region, is_competitor, notes)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                url           = excluded.url,
                region        = excluded.region,
                is_competitor = excluded.is_competitor,
                notes         = excluded.notes
            """,
            (name, url, region, 1 if is_competitor else 0, notes),
        )


def get_snapshot(uni_id, page_url):
    with connect() as conn:
        row = conn.execute(
            "SELECT content_clean, content_hash, last_checked, last_changed_at "
            "FROM snapshots WHERE uni_id = ? AND page_url = ?",
            (uni_id, page_url),
        ).fetchone()
        return dict(row) if row else None


def upsert_snapshot(uni_id, page_url, doc_type, content_clean, content_hash, changed):
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        existing = conn.execute(
            "SELECT last_changed_at FROM snapshots WHERE uni_id = ? AND page_url = ?",
            (uni_id, page_url),
        ).fetchone()
        last_changed_at = now if changed or existing is None else existing["last_changed_at"]
        conn.execute(
            """
            INSERT INTO snapshots (uni_id, page_url, doc_type, content_clean,
                                   content_hash, last_checked, last_changed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uni_id, page_url) DO UPDATE SET
                doc_type        = excluded.doc_type,
                content_clean   = excluded.content_clean,
                content_hash    = excluded.content_hash,
                last_checked    = excluded.last_checked,
                last_changed_at = excluded.last_changed_at
            """,
            (uni_id, page_url, doc_type, content_clean, content_hash, now, last_changed_at),
        )


def log_change_event(uni_id, page_url, doc_type, llm_meaningful, llm_reason, diff_excerpt):
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO change_events (uni_id, page_url, doc_type, detected_at,
                                       llm_meaningful, llm_reason, diff_excerpt)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uni_id,
                page_url,
                doc_type,
                now,
                None if llm_meaningful is None else (1 if llm_meaningful else 0),
                llm_reason,
                diff_excerpt,
            ),
        )
