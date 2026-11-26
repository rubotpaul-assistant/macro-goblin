from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from sqlite3 import IntegrityError
from threading import Barrier
from typing import Any

import pytest

from macro_tracker.db import Database
from macro_tracker.domain import MacroProfile, SourceType
from macro_tracker.service import (
    ConsumptionCorrection,
    ConsumptionNotFound,
    DeletedConsumption,
    DeletionConfirmationRequired,
    LoggingReplayConflict,
    MacroError,
    MacroService,
    MissingServingProfile,
    StaleConsumption,
)
from macro_tracker.sheets import SheetsService

PROFILE = MacroProfile(Decimal("150"), Decimal("12"), Decimal("8"), Decimal("0.001"))
TIME = datetime(2026, 8, 17, 1, tzinfo=UTC)


@pytest.fixture
def owner(tmp_path: Path) -> MacroService:
    database = Database(tmp_path / "macro.sqlite3")
    MacroService(database).add_user("tester", "Fixture tester")
    return MacroService(database, "tester")


def log_fixture_food(owner: MacroService) -> dict[str, Any]:
    return owner.log_new_food(
        "Crisp",
        "1 piece",
        PROFILE,
        SourceType.LABEL,
        Decimal("0.1"),
        client_key="initial",
        consumed_at=TIME,
    )


def test_correct_one_snapshot_and_delete_its_latest_revision(owner: MacroService) -> None:
    original = log_fixture_food(owner)
    similar = owner.log_known_food(
        original["food_id"], Decimal("0.1"), client_key="similar", consumed_at=TIME
    )
    owner.add_item(
        "Crisp",
        "1 bowl",
        MacroProfile(Decimal("300"), Decimal("20"), Decimal("0"), Decimal("1")),
        SourceType.ESTIMATE,
        replace=True,
    )

    entry_id = original["entry_id"]
    change = ConsumptionCorrection(Decimal("1"), date(2026, 8, 10), "adjusted quantity")
    corrected = owner.correct_consumption(entry_id, change, client_key="correct")
    assert corrected["entry_id"] != entry_id
    assert corrected["previous_entry_id"] == entry_id
    assert corrected["local_date"] == "2026-08-10"
    assert corrected["occurred_at_utc"] == original["occurred_at_utc"]
    assert corrected["source_type"] == "label"
    assert corrected["totals"]["calories"] == "150.000"
    assert corrected["totals"]["fat_g"] == "0.001"
    assert owner.correct_consumption(entry_id, change, client_key="correct") == corrected
    assert log_fixture_food(owner) == original
    with pytest.raises(StaleConsumption):
        owner.correct_consumption(entry_id, change, client_key="stale")
    with pytest.raises(LoggingReplayConflict):
        owner.correct_consumption(
            entry_id, ConsumptionCorrection(notes="different"), client_key="correct"
        )
    assert owner.daily_summary("2026-08-16")["totals"]["calories"] == "15.000"
    assert owner.daily_summary("2026-08-10")["totals"]["calories"] == "150.000"
    assert owner.search_items("Crisp")["items"][0]["calories"] == "300.000"

    corrected_id = corrected["entry_id"]
    with pytest.raises(DeletionConfirmationRequired):
        owner.delete_consumption(corrected_id, client_key="delete", confirmed=False)
    assert owner.daily_summary("2026-08-10")["item_count"] == 1
    deletion = owner.delete_consumption(corrected_id, client_key="delete", confirmed=True)
    assert owner.delete_consumption(corrected_id, client_key="delete", confirmed=True) == deletion
    with pytest.raises(DeletedConsumption):
        log_fixture_food(owner)
    with pytest.raises(DeletedConsumption):
        owner.correct_consumption(entry_id, change, client_key="correct")
    with pytest.raises(DeletedConsumption):
        owner.delete_consumption(corrected_id, client_key="after-delete", confirmed=True)
    assert [entry["id"] for entry in owner.recent_history()["entries"]] == [similar["entry_id"]]
    assert owner.daily_summary("2026-08-10")["item_count"] == 0
    with owner.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM consumption_entries").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM consumption_changes").fetchone()[0] == 2
        assert connection.execute("SELECT revision FROM sync_state").fetchone()[0] == 4
        ids = [row["id"] for row in connection.execute("SELECT id FROM active_consumption_entries")]
        assert ids == [similar["entry_id"]]

    class FixtureWriter:
        rows: list[list[Any]]

        def replace_rows(self, spreadsheet_id: str, tab: str, rows: list[list[Any]]) -> None:
            self.rows = rows

    writer = FixtureWriter()
    assert SheetsService(owner.database, "fixture-sheet", writer).sync()["rows"] == 1
    assert writer.rows[1][4] == "1 piece"


def test_foreign_ids_are_indistinguishable_from_missing(owner: MacroService) -> None:
    foreign = log_fixture_food(owner)["entry_id"]
    owner.add_user("other", "Other fixture")
    other = MacroService(owner.database, "other")
    for entry_id in (foreign, 999):
        with pytest.raises(ConsumptionNotFound, match=r"^unknown consumption$"):
            other.delete_consumption(entry_id, client_key="delete", confirmed=True)
        with pytest.raises(ConsumptionNotFound, match=r"^unknown consumption$"):
            other.correct_consumption(
                entry_id, ConsumptionCorrection(notes="other"), client_key="edit"
            )
    assert len(owner.recent_history()["entries"]) == 1
    assert other.recent_history()["entries"] == []


def test_only_one_concurrent_correction_can_replace_an_entry(owner: MacroService) -> None:
    original = log_fixture_food(owner)["entry_id"]
    start = Barrier(2, timeout=5)

    def correct(key: str) -> dict[str, Any] | None:
        worker = MacroService(owner.database, "tester")
        start.wait()
        try:
            return worker.correct_consumption(
                original, ConsumptionCorrection(notes=key), client_key=key
            )
        except StaleConsumption:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(correct, ("first", "second")))
    assert sum(result is not None for result in results) == 1
    assert len(owner.recent_history()["entries"]) == 1
    with owner.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM consumption_changes").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM logging_receipts").fetchone()[0] == 2


def test_audit_failure_rolls_back_correction_and_receipt(owner: MacroService) -> None:
    original = log_fixture_food(owner)["entry_id"]
    with owner.database.connect() as connection:
        connection.execute(
            """CREATE TRIGGER reject_edit BEFORE INSERT ON consumption_changes
            BEGIN SELECT RAISE(ABORT, 'fixture edit failure'); END"""
        )
    change = ConsumptionCorrection(notes="fixture")
    with pytest.raises(IntegrityError, match="fixture edit failure"):
        owner.correct_consumption(original, change, client_key="edit")
    assert [entry["id"] for entry in owner.recent_history()["entries"]] == [original]
    with owner.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM consumption_entries").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM logging_receipts").fetchone()[0] == 1
        assert connection.execute("SELECT revision FROM sync_state").fetchone()[0] == 1
        connection.execute("DROP TRIGGER reject_edit")
    saved = owner.correct_consumption(original, change, client_key="edit")
    assert owner.correct_consumption(original, change, client_key="edit") == saved


def test_older_snapshot_accepts_verified_basis_for_quantity_correction(owner: MacroService) -> None:
    original = log_fixture_food(owner)["entry_id"]
    with owner.database.connect() as connection:
        connection.execute("ALTER TABLE consumption_entries DROP COLUMN profile_basis")
    reopened = MacroService(owner.database, "tester")
    with pytest.raises(MacroError, match="at least one correction"):
        reopened.correct_consumption(original, ConsumptionCorrection(), client_key="edit")
    with pytest.raises(MissingServingProfile):
        reopened.correct_consumption(
            original, ConsumptionCorrection(servings=Decimal("1")), client_key="edit"
        )
    with pytest.raises(MacroError, match="do not match"):
        reopened.correct_consumption(
            original,
            ConsumptionCorrection(
                servings=Decimal("1"),
                original_profile=MacroProfile(Decimal("180"), Decimal(0), Decimal(0), Decimal(0)),
            ),
            client_key="edit",
        )
    saved = reopened.correct_consumption(
        original,
        ConsumptionCorrection(servings=Decimal("1"), original_profile=PROFILE),
        client_key="edit",
    )
    assert saved["totals"]["calories"] == "150.000"
