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
