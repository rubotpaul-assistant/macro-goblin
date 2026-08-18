"""SQLite persistence and idempotent schema initialization."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS food_items (
 id INTEGER PRIMARY KEY,
 name TEXT NOT NULL UNIQUE COLLATE NOCASE,
 serving_description TEXT NOT NULL,
 calories TEXT NOT NULL,
 protein_g TEXT NOT NULL,
 carbs_g TEXT NOT NULL,
 fat_g TEXT NOT NULL,
 fiber_g TEXT NOT NULL,
 source_type TEXT NOT NULL CHECK(source_type IN ('label','estimate','manual')),
 source_notes TEXT NOT NULL DEFAULT '',
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consumption_entries (
 id INTEGER PRIMARY KEY,
 food_item_id INTEGER NOT NULL REFERENCES food_items(id),
 consumed_at TEXT NOT NULL,
 local_date TEXT NOT NULL,
 item_name TEXT NOT NULL,
 serving_description TEXT NOT NULL,
 servings TEXT NOT NULL,
 calories TEXT NOT NULL,
 protein_g TEXT NOT NULL,
 carbs_g TEXT NOT NULL,
 fat_g TEXT NOT NULL,
 fiber_g TEXT NOT NULL,
 source_type TEXT NOT NULL CHECK(source_type IN ('label','estimate','manual')),
 source_notes TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_consumption_local_date
 ON consumption_entries(local_date,consumed_at);
CREATE TABLE IF NOT EXISTS sync_state (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 pending INTEGER NOT NULL DEFAULT 0 CHECK(pending IN (0,1)),
 revision INTEGER NOT NULL DEFAULT 0,
 last_success_at TEXT,
 last_error TEXT
);
INSERT OR IGNORE INTO sync_state(singleton,pending) VALUES(1,0);
"""


class Database:
    """Connection factory for the canonical local database."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        """Create the parent directory and schema."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(sync_state)")}
            if "revision" not in columns:
                connection.execute(
                    "ALTER TABLE sync_state ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a transactional connection with foreign keys enabled."""
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()
