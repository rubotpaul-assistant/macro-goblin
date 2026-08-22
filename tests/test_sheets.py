from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from macro_tracker.db import Database
from macro_tracker.domain import MacroProfile, SourceType
from macro_tracker.service import MacroService
from macro_tracker.sheets import GoogleSheetsWriter, SheetsService


class FakeWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list[list[Any]]]] = []

    def replace_rows(self, spreadsheet_id: str, tab: str, rows: list[list[Any]]) -> None:
        self.calls.append((spreadsheet_id, tab, rows))


def test_sync_projects_item_level_snapshots(tmp_path: Path) -> None:
    database = Database(tmp_path / "macro.sqlite3")
    service = MacroService(database)
    service.add_item(
        "Shake",
        "1 bottle",
        MacroProfile(Decimal("150"), Decimal("25"), Decimal("8"), Decimal("3")),
        SourceType.LABEL,
    )
    service.log_item("Shake", Decimal("1"), datetime(2026, 8, 17, 20, tzinfo=UTC))
    writer = FakeWriter()

    result = SheetsService(database, "sheet-id", writer).sync()

    assert result == {"ok": True, "rows": 1, "pending": False}
    assert writer.calls[0][1] == "Macro Log"
    assert writer.calls[0][2][0][0] == "User"
    assert writer.calls[0][2][1][0] == "geoff"
    assert writer.calls[0][2][1][3] == "Shake"


def test_sync_requires_spreadsheet_id(tmp_path: Path) -> None:
    database = Database(tmp_path / "macro.sqlite3")
    MacroService(database)
    with pytest.raises(ValueError, match="SPREADSHEET_ID"):
        SheetsService(database, None, FakeWriter()).sync()


def test_status_and_failed_sync_preserve_pending_state(tmp_path: Path) -> None:
    class FailingWriter:
        def replace_rows(self, spreadsheet_id: str, tab: str, rows: list[list[Any]]) -> None:
            raise RuntimeError("network unavailable")

    database = Database(tmp_path / "macro.sqlite3")
    MacroService(database)
    sheets = SheetsService(database, "sheet-id", FailingWriter())

    assert sheets.status()["pending"] == 0
    with pytest.raises(RuntimeError, match="network unavailable"):
        sheets.sync()
    status = sheets.status()
    assert status["pending"] == 1
    assert status["last_error"] == "RuntimeError"


def test_google_credentials_require_valid_runtime_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON", raising=False)
    with pytest.raises(ValueError, match="not configured"):
        GoogleSheetsWriter._credentials()
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON", "not-json")
    with pytest.raises(ValueError, match="not valid JSON"):
        GoogleSheetsWriter._credentials()


def test_sync_does_not_clear_change_logged_during_upload(tmp_path: Path) -> None:
    database = Database(tmp_path / "macro.sqlite3")
    service = MacroService(database)
    service.add_item(
        "Shake",
        "1 bottle",
        MacroProfile(Decimal("150"), Decimal("25"), Decimal("8"), Decimal("3")),
        SourceType.LABEL,
    )
    service.log_item("Shake", Decimal("1"))

    class ConcurrentWriter:
        def replace_rows(self, spreadsheet_id: str, tab: str, rows: list[list[Any]]) -> None:
            service.log_item("Shake", Decimal("2"))

    result = SheetsService(database, "sheet-id", ConcurrentWriter()).sync()

    assert result["pending"] is True
    assert SheetsService(database, "sheet-id", FakeWriter()).status()["pending"] == 1
