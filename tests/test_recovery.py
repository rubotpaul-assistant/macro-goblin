import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from macro_tracker.db import Database
from macro_tracker.domain import MacroProfile, SourceType
from macro_tracker.service import MacroError, MacroService


def test_restore_recovers_isolated_snapshots_and_replay_receipts(tmp_path: Path) -> None:
    canonical = Database(tmp_path / "canonical.sqlite3")
    admin = MacroService(canonical)
    admin.add_user("tester-a", "Fixture A")
    admin.add_user("tester-b", "Fixture B")
    profile = MacroProfile(Decimal("130"), Decimal("4"), Decimal("12"), Decimal("2"))
    timestamp = datetime(2026, 8, 17, 2, tzinfo=UTC)
    portions = {"tester-a": Decimal("1.25"), "tester-b": Decimal("2")}

    def log_fixture_food(service: MacroService, servings: Decimal) -> dict:
        return service.log_new_food(
            "Fixture crisp",
            "1 piece",
            profile,
            SourceType.LABEL,
            servings,
            client_key="first-log",
            consumed_at=timestamp,
        )

    original = {
        slug: log_fixture_food(MacroService(canonical, slug), servings)
        for slug, servings in portions.items()
    }
    backup_path = tmp_path / "backup.sqlite3"
    with canonical.connect() as source, closing(sqlite3.connect(backup_path)) as backup:
        source.backup(backup)

    live_owner = MacroService(canonical, "tester-a")
    live_owner.log_known_food(
        original["tester-a"]["food_id"], Decimal("1"), client_key="after-backup"
    )

    restored = Database(tmp_path / "restored.sqlite3")
    with (
        closing(sqlite3.connect(f"{backup_path.as_uri()}?mode=ro", uri=True)) as backup,
        restored.connect() as destination,
    ):
        backup.backup(destination)
        assert destination.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert destination.execute("PRAGMA foreign_key_check").fetchall() == []

    for slug, servings in portions.items():
        owner = MacroService(restored, slug)
        receipt = original[slug]
        assert log_fixture_food(owner, servings) == receipt
        history = owner.recent_history()["entries"]
        assert [entry["id"] for entry in history] == [receipt["entry_id"]]
        assert history[0]["source_type"] == "label"
        assert owner.daily_summary("2026-08-16")["totals"] == receipt["totals"]

    restored_owner = MacroService(restored, "tester-a")
    with pytest.raises(MacroError, match=r"^unknown food$"):
        restored_owner.log_known_food(
            original["tester-b"]["food_id"], Decimal("1"), client_key="foreign"
        )
    with restored.connect() as connection:
        assert connection.execute("SELECT revision FROM sync_state").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM logging_receipts").fetchone()[0] == 2
    assert len(live_owner.recent_history()["entries"]) == 2
