import sqlite3
from pathlib import Path

from macro_tracker.db import Database


def test_initialize_migrates_pre_revision_sync_state(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE sync_state (
             singleton INTEGER PRIMARY KEY,
             pending INTEGER NOT NULL DEFAULT 0,
             last_success_at TEXT,
             last_error TEXT
            );
            INSERT INTO sync_state(singleton,pending) VALUES(1,1);
            """
        )

    database = Database(path)
    database.initialize()

    with database.connect() as connection:
        row = connection.execute(
            "SELECT pending,revision FROM sync_state WHERE singleton=1"
        ).fetchone()
    assert dict(row) == {"pending": 1, "revision": 0}


def test_initialize_backfills_legacy_rows_to_geoff(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE food_items (
             id INTEGER PRIMARY KEY,
             name TEXT NOT NULL UNIQUE COLLATE NOCASE,
             serving_description TEXT NOT NULL,
             calories TEXT NOT NULL,
             protein_g TEXT NOT NULL,
             carbs_g TEXT NOT NULL,
             fat_g TEXT NOT NULL,
             fiber_g TEXT NOT NULL,
             source_type TEXT NOT NULL,
             source_notes TEXT NOT NULL DEFAULT '',
             active INTEGER NOT NULL DEFAULT 1,
             created_at TEXT NOT NULL,
             updated_at TEXT NOT NULL
            );
            CREATE TABLE consumption_entries (
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
             source_type TEXT NOT NULL,
             source_notes TEXT NOT NULL DEFAULT '',
             notes TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO food_items VALUES(
             7,'Shake','1 bottle','150','25','8','3','0','label','',1,'created','updated'
            );
            INSERT INTO consumption_entries VALUES(
             9,7,'2026-08-19T12:00:00-07:00','2026-08-19','Shake','1 bottle','1',
             '150','25','8','3','0','label','',''
            );
            """
        )

    Database(path).initialize()

    with Database(path).connect() as connection:
        user = connection.execute("SELECT id,slug,display_name FROM users").fetchone()
        food = connection.execute("SELECT id,user_id FROM food_items").fetchone()
        entry = connection.execute(
            "SELECT id,user_id,food_item_id FROM consumption_entries"
        ).fetchone()
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    assert dict(user) == {"id": 1, "slug": "geoff", "display_name": "Geoff"}
    assert dict(food) == {"id": 7, "user_id": 1}
    assert dict(entry) == {"id": 9, "user_id": 1, "food_item_id": 7}
    assert foreign_key_errors == []
