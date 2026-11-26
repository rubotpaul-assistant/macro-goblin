from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from sqlite3 import IntegrityError
from threading import Barrier

import pytest

from macro_tracker.db import Database
from macro_tracker.domain import MacroProfile, SourceType
from macro_tracker.service import LoggingReplayConflict, MacroError, MacroService, StaleFoodProfile


@pytest.fixture
def service(tmp_path: Path) -> MacroService:
    return MacroService(Database(tmp_path / "macro.sqlite3"))


def profile() -> MacroProfile:
    return MacroProfile(Decimal("150"), Decimal("25"), Decimal("8"), Decimal("3"), Decimal("2"))


def test_add_search_and_log_item_with_snapshot(service: MacroService) -> None:
    added = service.add_item("Protein shake", "1 bottle", profile(), SourceType.LABEL)
    matches = service.search_items("shake")
    logged = service.log_item(
        "protein shake", Decimal("1.5"), datetime(2026, 8, 17, 20, tzinfo=UTC), "post gym"
    )

    assert added["item_id"] == 1
    assert matches["items"][0]["name"] == "Protein shake"
    assert logged["totals"] == {
        "calories": "225.000",
        "protein_g": "37.500",
        "carbs_g": "12.000",
        "fat_g": "4.500",
        "fiber_g": "3.000",
    }
    assert logged["sync_pending"] is True


def test_item_update_does_not_rewrite_logged_snapshot(service: MacroService) -> None:
    service.add_item("Apple", "1 apple", profile(), SourceType.ESTIMATE, "medium Honeycrisp")
    service.log_item("Apple", Decimal("1"), datetime(2026, 8, 17, 12, tzinfo=UTC))
    service.add_item(
        "Apple",
        "1 apple",
        MacroProfile(Decimal("200"), Decimal("1"), Decimal("40"), Decimal("0")),
        SourceType.ESTIMATE,
        "large Honeycrisp",
        replace=True,
    )

    history = service.recent_history()
    assert history["entries"][0]["calories"] == "150.000"


def test_summary_aggregates_item_snapshots_for_local_date(service: MacroService) -> None:
    service.add_item("Shake", "1 bottle", profile(), SourceType.LABEL)
    service.log_item("Shake", Decimal("2"), datetime(2026, 8, 17, 19, tzinfo=UTC))

    summary = service.daily_summary("2026-08-17")
    assert summary["totals"]["protein_g"] == "50.000"
    assert summary["item_count"] == 1


def test_users_have_isolated_catalogs_and_history(tmp_path: Path) -> None:
    database = Database(tmp_path / "macro.sqlite3")
    geoff = MacroService(database)
    geoff.add_user("freedom", "Freedom")
    freedom = MacroService(database, "freedom")
    geoff.add_item("Shake", "Geoff serving", profile(), SourceType.LABEL)
    freedom.add_item("Shake", "Freedom serving", profile(), SourceType.MANUAL)
    geoff.log_item("Shake", Decimal("1"), datetime(2026, 8, 17, 19, tzinfo=UTC))
    freedom.log_item("Shake", Decimal("2"), datetime(2026, 8, 17, 20, tzinfo=UTC))

    assert geoff.search_items("Shake")["items"][0]["serving_description"] == "Geoff serving"
    assert freedom.search_items("Shake")["items"][0]["serving_description"] == "Freedom serving"
    assert geoff.daily_summary("2026-08-17")["item_count"] == 1
    assert freedom.daily_summary("2026-08-17")["totals"]["calories"] == "300.000"
    assert geoff.recent_history()["entries"][0]["user_slug"] == "geoff"


def test_unknown_user_is_clear_error(tmp_path: Path) -> None:
    database = Database(tmp_path / "macro.sqlite3")
    MacroService(database)
    with pytest.raises(MacroError, match="unknown user"):
        MacroService(database, "nobody")


def test_log_rejects_non_positive_servings(service: MacroService) -> None:
    service.add_item("Shake", "1 bottle", profile(), SourceType.LABEL)
    with pytest.raises(MacroError, match="servings must be positive"):
        service.log_item("Shake", Decimal("0"))


def test_unknown_item_is_clear_error(service: MacroService) -> None:
    with pytest.raises(MacroError, match="unknown item"):
        service.log_item("Mystery", Decimal("1"))


def test_validation_errors_are_explicit(service: MacroService) -> None:
    with pytest.raises(MacroError, match="required"):
        service.add_item("", "1 serving", profile(), SourceType.MANUAL)
    service.add_item("Shake", "1 bottle", profile(), SourceType.LABEL)
    with pytest.raises(MacroError, match="already exists"):
        service.add_item("shake", "1 bottle", profile(), SourceType.LABEL)
    with pytest.raises(MacroError, match="timezone"):
        service.log_item(
            "Shake", Decimal("1"), datetime(2026, 8, 17, tzinfo=UTC).replace(tzinfo=None)
        )
    with pytest.raises(MacroError, match="between 1 and 100"):
        service.recent_history(0)
    with pytest.raises(MacroError, match="YYYY-MM-DD"):
        service.daily_summary("yesterday")
    with pytest.raises(MacroError, match="YYYY-MM-DD"):
        service.daily_summary("20260817")
    with pytest.raises(MacroError, match="servings"):
        service.log_item("Shake", Decimal("Infinity"))


def test_known_food_replays_original_result_after_profile_replacement(
    service: MacroService,
) -> None:
    food_id = service.add_item("Shake", "1 bottle", profile(), SourceType.LABEL)["item_id"]
    version = service.search_items("Shake", include_version=True)["items"][0]["profile_version"]
    timestamp = datetime(2026, 8, 17, 1, tzinfo=UTC)
    original = service.log_known_food(
        food_id,
        Decimal("1.5"),
        client_key="first",
        expected_profile_version=version,
        consumed_at=timestamp,
        notes="fixture note",
    )
    service.add_item("Shake", "1 cup", profile(), SourceType.ESTIMATE, replace=True)
    reopened = MacroService(service.database)
    assert (
        reopened.log_known_food(
            food_id,
            Decimal("1.500"),
            client_key="first",
            expected_profile_version=version,
            consumed_at=timestamp.astimezone(timezone(timedelta(hours=-7))),
            notes="fixture note",
        )
        == original
    )
    assert original["food_id"] == food_id
    assert original["source_type"] == "label"
    assert original["local_date"] == "2026-08-16"
    assert original["totals"]["calories"] == "225.000"
    with pytest.raises(LoggingReplayConflict):
        reopened.log_known_food(food_id, Decimal("2"), client_key="first")
    with pytest.raises(StaleFoodProfile):
        reopened.log_known_food(
            food_id, Decimal("1.5"), client_key="second", expected_profile_version=version
        )
    assert len(reopened.recent_history()["entries"]) == 1
    updated = reopened.log_known_food(food_id, Decimal("1.5"), client_key="second")
    repeat = reopened.log_known_food(food_id, Decimal("1.5"), client_key="third")
    assert updated["entry_id"] != repeat["entry_id"]
    assert updated["source_type"] == "estimate"
    assert updated["profile_version"] == version + 1
    assert reopened.log_known_food(food_id, Decimal("1.5"), client_key="second") == updated
    saved = next(
        entry
        for entry in reopened.recent_history()["entries"]
        if entry["id"] == original["entry_id"]
    )
    assert saved["serving_description"] == "1 bottle"
    assert saved["source_type"] == "label"
    assert saved["calories"] == "225.000"
    assert saved["notes"] == "fixture note"
    with reopened.database.connect() as connection:
        assert connection.execute("SELECT revision FROM sync_state").fetchone()[0] == 3
        receipts = connection.execute("SELECT entry_id FROM logging_receipts").fetchall()
        assert {row["entry_id"] for row in receipts} == {
            original["entry_id"],
            updated["entry_id"],
            repeat["entry_id"],
        }


def test_logging_key_is_scoped_to_owner_and_operation(service: MacroService) -> None:
    service.add_user("tester", "Fixture tester")
    tester = MacroService(service.database, "tester")
    first = service.log_new_food(
        "Shake", "1 bottle", profile(), SourceType.LABEL, Decimal("1"), client_key="same"
    )
    second = tester.log_new_food(
        "Shake", "1 cup", profile(), SourceType.MANUAL, Decimal("2"), client_key="same"
    )
    assert first["food_id"] != second["food_id"]
    assert second["source_type"] == "manual"
    with pytest.raises(MacroError, match=r"^unknown food$"):
        tester.log_known_food(first["food_id"], Decimal("1"), client_key="same")
    with pytest.raises(MacroError, match=r"^unknown food$"):
        tester.log_known_food(999, Decimal("1"), client_key="same")
    third = tester.log_known_food(second["food_id"], Decimal("2"), client_key="same")
    assert third["entry_id"] != second["entry_id"]
    assert len(service.recent_history()["entries"]) == 1
    assert len(tester.recent_history()["entries"]) == 2


@pytest.mark.parametrize("new_food", [False, True])
def test_concurrent_logging_replays_one_commit(service: MacroService, new_food: bool) -> None:
    food_id = None
    if not new_food:
        food_id = service.add_item("Shake", "1 bottle", profile(), SourceType.LABEL)["item_id"]
    start = Barrier(2, timeout=5)

    def submit() -> dict:
        worker = MacroService(service.database)
        start.wait()
        if food_id is None:
            return worker.log_new_food(
                "Shake", "1 bottle", profile(), SourceType.LABEL, Decimal("2"), client_key="retry"
            )
        return worker.log_known_food(food_id, Decimal("2"), client_key="retry")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(submit), pool.submit(submit)
        assert first.result(timeout=10) == second.result(timeout=10)

    assert len(service.recent_history()["entries"]) == 1
    assert len(service.search_items("Shake")["items"]) == 1
    with service.database.connect() as connection:
        assert connection.execute("SELECT revision FROM sync_state").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM logging_receipts").fetchone()[0] == 1


def test_atomic_new_food_rolls_back_if_receipt_write_fails(service: MacroService) -> None:
    with service.database.connect() as connection:
        connection.execute(
            """CREATE TRIGGER reject_receipt BEFORE INSERT ON logging_receipts
            BEGIN SELECT RAISE(ABORT, 'fixture receipt failure'); END"""
        )

    def log() -> dict:
        return service.log_new_food(
            "Shake",
            "1 bottle",
            profile(),
            SourceType.ESTIMATE,
            Decimal("1.25"),
            client_key="new-food",
            source_notes="fixture estimate",
        )

    with pytest.raises(IntegrityError, match="fixture receipt failure"):
        log()
    assert service.search_items("Shake")["items"] == []
    assert service.recent_history()["entries"] == []
    with service.database.connect() as connection:
        assert connection.execute("SELECT revision FROM sync_state").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM logging_receipts").fetchone()[0] == 0
        connection.execute("DROP TRIGGER reject_receipt")

    original = log()
    assert log() == original
    assert original["source_type"] == "estimate"
    assert original["totals"]["calories"] == "187.500"
    assert service.recent_history()["entries"][0]["source_notes"] == "fixture estimate"
    with pytest.raises(MacroError, match="item already exists"):
        service.log_new_food(
            "shake", "1 cup", profile(), SourceType.MANUAL, Decimal("1"), client_key="duplicate"
        )
    with pytest.raises(MacroError, match="client key"):
        service.log_known_food(original["food_id"], Decimal("1"), client_key=" ")
    assert len(service.recent_history()["entries"]) == 1
