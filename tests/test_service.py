from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from macro_tracker.db import Database
from macro_tracker.domain import MacroProfile, SourceType
from macro_tracker.service import MacroError, MacroService


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
