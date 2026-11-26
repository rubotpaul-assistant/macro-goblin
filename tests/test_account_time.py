import json
from datetime import UTC, date, datetime, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import Self

import pytest

from macro_tracker import service as service_module
from macro_tracker.db import Database
from macro_tracker.domain import MacroProfile, SourceType
from macro_tracker.service import LoggingReplayConflict, MacroError, MacroService

# Golden vectors from Iron #18 at dfd01256d2a51556b35e768570a6770f96c974d5.
# The service accepts a missing event date as an instruction to log now.
VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "creek-masons-pilot-v1-time.json").read_text()
)
PROFILE = MacroProfile(Decimal("150"), Decimal("25"), Decimal("8"), Decimal("3"))


@pytest.mark.parametrize("vector", VECTORS, ids=[vector["name"] for vector in VECTORS])
def test_account_event_vectors(tmp_path: Path, vector: dict) -> None:
    database = Database(tmp_path / "macro.sqlite3")
    MacroService(database).add_user("tester", "Fixture tester")
    timestamp = vector.get("occurred_at")
    historical_date = vector.get("historical_date")

    def log() -> tuple[MacroService, dict]:
        owner = MacroService(database, "tester", time_zone=vector["time_zone"])
        return owner, owner.log_new_food(
            "Fixture food",
            "1 piece",
            PROFILE,
            SourceType.LABEL,
            Decimal("1.25"),
            client_key="vector",
            consumed_at=datetime.fromisoformat(timestamp) if timestamp else None,
            historical_date=date.fromisoformat(historical_date) if historical_date else None,
        )

    if vector.get("error"):
        message = "timezone" if vector["name"] == "missing offset" else vector["error"]
        with pytest.raises(MacroError, match=message):
            log()
        owner = MacroService(database, "tester")
        assert owner.recent_history()["entries"] == []
        assert owner.search_items("")["items"] == []
        return

    owner, response = log()
    stored = owner.recent_history(include_event_context=True)["entries"][0]
    for result in (response, stored):
        assert result["local_date"] == vector["local_date"]
        assert result["time_zone"] == vector["time_zone"]
        assert result["historical_date_explicit"] is (historical_date is not None)
        assert result["offset_minutes"] == vector["offset_minutes"]
        expected_utc = vector["occurred_at_utc"]
        if expected_utc:
            assert datetime.fromisoformat(result["occurred_at_utc"]) == datetime.fromisoformat(
                expected_utc
            )
        else:
            assert result["occurred_at_utc"] is None
            assert result["consumed_at"] == historical_date
    assert owner.daily_summary(vector["local_date"])["totals"]["calories"] == "187.500"


def test_account_today_and_replay_use_different_zone_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instant = datetime(2026, 8, 17, 1, tzinfo=UTC)

    class FixtureClock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp(instant.timestamp(), tz)

    monkeypatch.setattr(service_module, "datetime", FixtureClock)
    database = Database(tmp_path / "macro.sqlite3")
    admin = MacroService(database)
    admin.add_user("west", "Fixture West")
    admin.add_user("east", "Fixture East")
    west = MacroService(database, "west", time_zone="America/Los_Angeles")
    east = MacroService(database, "east", time_zone="Europe/London")
    for owner, amount in ((west, "1"), (east, "2")):
        owner.log_new_food(
            "Fixture food", "1 piece", PROFILE, SourceType.LABEL, Decimal(amount), client_key="now"
        )
    assert west.daily_summary()["date"] == "2026-08-16"
    assert west.daily_summary()["totals"]["calories"] == "150.000"
    assert west.daily_summary("2026-08-17")["item_count"] == 0
    assert east.daily_summary()["date"] == "2026-08-17"
    assert east.daily_summary()["totals"]["calories"] == "300.000"
    assert east.daily_summary("2026-08-16")["item_count"] == 0

    food = west.search_items("Fixture")["items"][0]
    original = west.log_known_food(food["id"], Decimal("1"), client_key="same")
    moved = MacroService(database, "west", time_zone="Europe/London")
    assert moved.log_known_food(food["id"], Decimal("1"), client_key="same") == original
    assert moved.daily_summary()["item_count"] == 0
    assert moved.daily_summary("2026-08-16")["item_count"] == 2
    assert moved.recent_history(include_event_context=True)["entries"][0]["time_zone"] == (
        "America/Los_Angeles"
    )
    with pytest.raises(LoggingReplayConflict):
        moved.log_known_food(
            food["id"], Decimal("1"), client_key="same", historical_date=date(2026, 8, 16)
        )
    moved.log_known_food(food["id"], Decimal("1"), client_key="intentional-repeat")
    assert moved.daily_summary()["item_count"] == 1


def test_history_orders_fall_back_hour_by_instant(tmp_path: Path) -> None:
    database = Database(tmp_path / "macro.sqlite3")
    MacroService(database).add_user("tester", "Fixture tester")
    owner = MacroService(database, "tester")
    food_id = owner.add_item("Fixture food", "1 piece", PROFILE, SourceType.LABEL)["item_id"]
    later = owner.log_known_food(
        food_id,
        Decimal("1"),
        client_key="later",
        consumed_at=datetime.fromisoformat("2026-11-01T01:15:00-08:00"),
    )
    earlier = owner.log_known_food(
        food_id,
        Decimal("1"),
        client_key="earlier",
        consumed_at=datetime.fromisoformat("2026-11-01T01:45:00-07:00"),
    )
    history = owner.recent_history()["entries"]
    assert [entry["id"] for entry in history] == [later["entry_id"], earlier["entry_id"]]
    assert owner.daily_summary("2026-11-01")["item_count"] == 2
