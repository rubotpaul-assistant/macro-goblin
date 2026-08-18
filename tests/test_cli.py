import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import macro_tracker.cli
from macro_tracker.cli import app

runner = CliRunner()


def test_cli_add_log_summary_and_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "macro.sqlite3"
    monkeypatch.setenv("MACRO_DATABASE_PATH", str(database))
    added = runner.invoke(
        app,
        [
            "item",
            "add",
            "Shake",
            "--serving",
            "1 bottle",
            "--calories",
            "150",
            "--protein-g",
            "25",
            "--carbs-g",
            "8",
            "--fat-g",
            "3",
            "--source",
            "label",
        ],
    )
    logged = runner.invoke(app, ["log", "item", "--item", "Shake", "--servings", "1.5"])
    searched = runner.invoke(app, ["item", "search", "Sha"])
    summary = runner.invoke(app, ["summary", "day", datetime_now_pacific_date()])
    history = runner.invoke(app, ["history", "recent"])
    sync_status = runner.invoke(app, ["sheets", "status"])

    assert all(
        result.exit_code == 0 for result in (added, logged, searched, summary, history, sync_status)
    )
    assert json.loads(logged.stdout)["totals"]["protein_g"] == "37.500"
    assert json.loads(summary.stdout)["item_count"] == 1
    assert json.loads(history.stdout)["entries"][0]["item_name"] == "Shake"
    assert json.loads(searched.stdout)["items"][0]["name"] == "Shake"
    assert json.loads(sync_status.stdout)["configured"] is False


def datetime_now_pacific_date() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(tz=ZoneInfo("America/Los_Angeles")).date().isoformat()


def test_cli_reports_invalid_numeric_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MACRO_DATABASE_PATH", str(tmp_path / "macro.sqlite3"))
    result = runner.invoke(
        app,
        [
            "item",
            "add",
            "Bad",
            "--serving",
            "1",
            "--calories",
            "wat",
            "--protein-g",
            "1",
            "--carbs-g",
            "1",
            "--fat-g",
            "1",
        ],
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "macro values must be numeric"


def test_cli_sync_without_sheet_reports_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MACRO_DATABASE_PATH", str(tmp_path / "macro.sqlite3"))
    monkeypatch.delenv("MACRO_SPREADSHEET_ID", raising=False)
    monkeypatch.delenv("GYM_SPREADSHEET_ID", raising=False)

    result = runner.invoke(app, ["sheets", "sync"])

    assert result.exit_code == 1
    assert "SPREADSHEET_ID" in json.loads(result.stdout)["error"]


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["--servings", "many"], "servings must be numeric"),
        (["--consumed-at", "last night"], "consumed-at must be ISO 8601"),
    ],
)
def test_cli_log_rejects_malformed_values_as_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    expected: str,
) -> None:
    monkeypatch.setenv("MACRO_DATABASE_PATH", str(tmp_path / "macro.sqlite3"))

    result = runner.invoke(app, ["log", "item", "--item", "Shake", *arguments])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == expected


def test_cli_sync_external_failure_is_stable_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingSheets:
        def sync(self) -> dict[str, object]:
            raise RuntimeError("socket details")

    monkeypatch.setattr(macro_tracker.cli, "_sheets_service", FailingSheets)

    result = runner.invoke(app, ["sheets", "sync"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"] == "Google Sheets sync failed"
    assert "socket details" not in result.stdout
