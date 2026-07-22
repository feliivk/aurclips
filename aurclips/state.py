"""Estado persistente en SQLite: videos procesados, clips y cola de publicación."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,             -- 'youtube' | 'local'
    source_id   TEXT NOT NULL UNIQUE,      -- id de YouTube o ruta del archivo
    title       TEXT,
    path        TEXT,
    duration    REAL,
    status      TEXT NOT NULL DEFAULT 'new',  -- new|transcribed|selected|rendered|done|skipped|failed
    error       TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
    id          INTEGER PRIMARY KEY,
    video_id    INTEGER NOT NULL REFERENCES videos(id),
    idx         INTEGER NOT NULL,
    start       REAL NOT NULL,
    end         REAL NOT NULL,
    title       TEXT,
    description TEXT,
    tags        TEXT,                      -- JSON
    text        TEXT,                      -- transcripción del clip (dedup/filtro)
    score       REAL,                      -- puntuación de la heurística
    marked      INTEGER,                   -- 1 = lo marcaste tú al grabar
    approved    INTEGER,                   -- NULL sin revisar | 1 aprobado | 0 descartado
    path        TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending|rendered|uploaded|flagged|failed
    publish_at  TEXT,
    youtube_id  TEXT,
    views       INTEGER,
    likes       INTEGER,
    stats_at    TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class State:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """Agrega columnas nuevas a bases creadas con versiones anteriores."""
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(clips)")}
        for col, ddl in [("text", "TEXT"), ("score", "REAL"),
                         ("views", "INTEGER"), ("likes", "INTEGER"),
                         ("stats_at", "TEXT"), ("marked", "INTEGER"),
                         ("approved", "INTEGER")]:
            if col not in existing:
                self.conn.execute(f"ALTER TABLE clips ADD COLUMN {col} {ddl}")

    # --- videos ----------------------------------------------------------
    def video_known(self, source_id: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM videos WHERE source_id = ?", (source_id,))
        return cur.fetchone() is not None

    def add_video(self, source: str, source_id: str, title: str | None = None,
                  path: str | None = None, duration: float | None = None,
                  status: str = "new") -> int:
        cur = self.conn.execute(
            "INSERT INTO videos (source, source_id, title, path, duration, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (source, source_id, title, path, duration, status, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_video(self, video_id: int, **fields):
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE videos SET {cols} WHERE id = ?", (*fields.values(), video_id)
        )
        self.conn.commit()

    def videos_with_status(self, *statuses: str) -> list[sqlite3.Row]:
        marks = ", ".join("?" for _ in statuses)
        cur = self.conn.execute(
            f"SELECT * FROM videos WHERE status IN ({marks}) ORDER BY id", statuses
        )
        return cur.fetchall()

    # --- clips -----------------------------------------------------------
    def add_clip(self, video_id: int, idx: int, start: float, end: float,
                 title: str, description: str, tags: list[str],
                 text: str | None = None, score: float | None = None,
                 status: str = "pending", marked: bool = False) -> int:
        cur = self.conn.execute(
            "INSERT INTO clips (video_id, idx, start, end, title, description, tags,"
            " text, score, marked, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (video_id, idx, start, end, title, description, json.dumps(tags),
             text, score, int(marked), status, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def clips_to_review(self) -> list[sqlite3.Row]:
        """Clips renderizados que aún no has aprobado ni descartado."""
        cur = self.conn.execute(
            "SELECT * FROM clips WHERE status = 'rendered' AND approved IS NULL"
            " ORDER BY video_id, idx"
        )
        return cur.fetchall()

    def update_clip(self, clip_id: int, **fields):
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE clips SET {cols} WHERE id = ?", (*fields.values(), clip_id)
        )
        self.conn.commit()

    def clips_with_status(self, *statuses: str) -> list[sqlite3.Row]:
        marks = ", ".join("?" for _ in statuses)
        cur = self.conn.execute(
            f"SELECT * FROM clips WHERE status IN ({marks}) ORDER BY id", statuses
        )
        return cur.fetchall()

    def clips_for_video(self, video_id: int) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM clips WHERE video_id = ? ORDER BY idx", (video_id,)
        )
        return cur.fetchall()

    # --- meta ------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        cur = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str):
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()
