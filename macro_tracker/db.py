"""SQLite persistence and idempotent schema migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY,
 slug TEXT NOT NULL UNIQUE COLLATE NOCASE,
 display_name TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 created_at TEXT NOT NULL
);
INSERT OR IGNORE INTO users(id,slug,display_name,created_at)
 VALUES(1,'geoff','Geoff',CURRENT_TIMESTAMP);
"""

TRACKING_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS food_items (
 id INTEGER PRIMARY KEY,
 user_id INTEGER NOT NULL REFERENCES users(id),
 name TEXT NOT NULL COLLATE NOCASE,
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
 updated_at TEXT NOT NULL,
 profile_version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(user_id,name),
 UNIQUE(id,user_id)
)""",
    """CREATE TABLE IF NOT EXISTS consumption_entries (
 id INTEGER PRIMARY KEY,
 user_id INTEGER NOT NULL REFERENCES users(id),
 food_item_id INTEGER NOT NULL,
 consumed_at TEXT NOT NULL,
 local_date TEXT NOT NULL,
 time_zone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
 historical_date_explicit INTEGER NOT NULL DEFAULT 0 CHECK(historical_date_explicit IN (0,1)),
 occurred_at_utc TEXT,
 offset_minutes INTEGER,
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
 notes TEXT NOT NULL DEFAULT '',
 profile_basis TEXT,
 FOREIGN KEY(food_item_id,user_id) REFERENCES food_items(id,user_id)
)""",
    """CREATE INDEX IF NOT EXISTS ix_consumption_user_local_date
    ON consumption_entries(user_id,local_date,consumed_at)""",
    """CREATE TABLE IF NOT EXISTS logging_receipts (
 user_id INTEGER NOT NULL REFERENCES users(id),
 operation TEXT NOT NULL,
 client_key TEXT NOT NULL,
 request_hash TEXT NOT NULL,
 entry_id INTEGER NOT NULL,
 response_json TEXT NOT NULL,
 PRIMARY KEY(user_id,operation,client_key)
)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS ix_consumption_id_owner
    ON consumption_entries(id,user_id)""",
    """CREATE TABLE IF NOT EXISTS consumption_changes (
 user_id INTEGER NOT NULL,
 entry_id INTEGER NOT NULL,
 replacement_id INTEGER,
 changed_at TEXT NOT NULL,
 PRIMARY KEY(user_id,entry_id),
 FOREIGN KEY(entry_id,user_id) REFERENCES consumption_entries(id,user_id),
 FOREIGN KEY(replacement_id,user_id) REFERENCES consumption_entries(id,user_id)
)""",
    """CREATE VIEW IF NOT EXISTS active_consumption_entries AS
 SELECT entries.* FROM consumption_entries AS entries
 WHERE NOT EXISTS (
    SELECT 1 FROM consumption_changes AS changes
    WHERE changes.user_id=entries.user_id AND changes.entry_id=entries.id
 )""",
)

SYNC_SCHEMA = """
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
        """Create the schema and migrate legacy single-user rows to Geoff."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.executescript(USERS_SCHEMA)
            connection.executescript(SYNC_SCHEMA)
            connection.execute("BEGIN IMMEDIATE")
            if self._needs_user_migration(connection):
                self._migrate_legacy_tracking_tables(connection)
            self._create_tracking_tables(connection)
            food_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(food_items)")
            }
            if "profile_version" not in food_columns:
                connection.execute(
                    "ALTER TABLE food_items ADD COLUMN profile_version INTEGER NOT NULL DEFAULT 1"
                )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(sync_state)")}
            if "revision" not in columns:
                connection.execute(
                    "ALTER TABLE sync_state ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
                )
            self._migrate_event_context(connection)
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(consumption_entries)")
            }
            if "profile_basis" not in columns:
                connection.execute("ALTER TABLE consumption_entries ADD COLUMN profile_basis TEXT")
            connection.commit()
            connection.execute("PRAGMA foreign_keys = ON")
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError("foreign key violation after database migration")
        finally:
            connection.close()

    @staticmethod
    def _migrate_event_context(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(consumption_entries)")
        }
        if "time_zone" in columns:
            return
        connection.execute(
            "ALTER TABLE consumption_entries ADD COLUMN time_zone "
            "TEXT NOT NULL DEFAULT 'America/Los_Angeles'"
        )
        connection.execute(
            "ALTER TABLE consumption_entries ADD COLUMN historical_date_explicit INTEGER "
            "NOT NULL DEFAULT 0 CHECK(historical_date_explicit IN (0,1))"
        )
        connection.execute("ALTER TABLE consumption_entries ADD COLUMN occurred_at_utc TEXT")
        connection.execute("ALTER TABLE consumption_entries ADD COLUMN offset_minutes INTEGER")
        Database._backfill_event_instants(connection)

    @staticmethod
    def _backfill_event_instants(connection: sqlite3.Connection) -> None:
        # The older schema retained the Pacific timestamp, but discarded the input offset.
        rows = connection.execute("SELECT id,consumed_at FROM consumption_entries")
        connection.executemany(
            "UPDATE consumption_entries SET occurred_at_utc=? WHERE id=?",
            (
                (
                    datetime.fromisoformat(row["consumed_at"])
                    .astimezone(UTC)
                    .isoformat(timespec="microseconds"),
                    row["id"],
                )
                for row in rows
            ),
        )

    @staticmethod
    def _create_tracking_tables(connection: sqlite3.Connection) -> None:
        for statement in TRACKING_SCHEMA:
            connection.execute(statement)

    @staticmethod
    def _needs_user_migration(connection: sqlite3.Connection) -> bool:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='food_items'"
        ).fetchone()
        if table is None:
            return False
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(food_items)")}
        return "user_id" not in columns

    @staticmethod
    def _migrate_legacy_tracking_tables(connection: sqlite3.Connection) -> None:
        connection.execute("ALTER TABLE food_items RENAME TO legacy_food_items")
        connection.execute("ALTER TABLE consumption_entries RENAME TO legacy_consumption_entries")
        Database._create_tracking_tables(connection)
        connection.execute(
            """INSERT INTO food_items(
                id,user_id,name,serving_description,calories,protein_g,carbs_g,fat_g,fiber_g,
                source_type,source_notes,active,created_at,updated_at
                ) SELECT id,1,name,serving_description,calories,protein_g,carbs_g,fat_g,fiber_g,
                source_type,source_notes,active,created_at,updated_at FROM legacy_food_items"""
        )
        connection.execute(
            """INSERT INTO consumption_entries(
                id,user_id,food_item_id,consumed_at,local_date,item_name,serving_description,
                servings,calories,protein_g,carbs_g,fat_g,fiber_g,source_type,source_notes,notes
                ) SELECT id,1,food_item_id,consumed_at,local_date,item_name,serving_description,
                servings,calories,protein_g,carbs_g,fat_g,fiber_g,source_type,source_notes,notes
                FROM legacy_consumption_entries"""
        )
        connection.execute("DROP TABLE legacy_consumption_entries")
        connection.execute("DROP TABLE legacy_food_items")
        Database._backfill_event_instants(connection)

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
