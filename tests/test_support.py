import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from macro_tracker.cli import app
from macro_tracker.db import Database
from macro_tracker.domain import MacroProfile, SourceType
from macro_tracker.service import ConsumptionCorrection, MacroService


def test_support_export_keeps_immutable_history_and_receipts_owner_scoped(tmp_path: Path) -> None:
    database = Database(tmp_path / "canonical.sqlite3")
    admin = MacroService(database)
    admin.add_user("fixture-a", "Fixture A")
    admin.add_user("fixture-b", "Fixture B")
    first = MacroService(database, "fixture-a")
    second = MacroService(database, "fixture-b")
    portion = Decimal("1.25")
    timestamp = datetime(2026, 8, 17, 1, tzinfo=UTC)
    profile = MacroProfile(Decimal("130"), Decimal("4"), Decimal("12"), Decimal("2"))
    original = first.log_new_food(
        "Fixture crisp",
        "1 piece",
        profile,
        SourceType.LABEL,
        portion,
        client_key="fixture-log",
        consumed_at=timestamp,
        notes="owner-a-fixture-note",
    )
    second.log_new_food(
        "Fixture crisp",
        "1 bowl",
        profile,
        SourceType.ESTIMATE,
        portion,
        client_key="fixture-log",
        consumed_at=timestamp,
        notes="owner-b-fixture-note",
    )
    corrected = first.correct_consumption(
        original["entry_id"], ConsumptionCorrection(servings=Decimal("1")), client_key="edit"
    )
    first.delete_consumption(corrected["entry_id"], client_key="delete", confirmed=True)
    output = tmp_path / "archive.json"
    env = {"MACRO_DATABASE_PATH": str(database.path), "MACRO_USER_SLUG": "fixture-b"}
    args = ["support", "export", "--owner-id", str(first.user_id), "--output", str(output)]

    result = CliRunner().invoke(app, args, env=env)
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"ok": True, "schema_version": 1}
    original_file = output.read_text()
    archive = json.loads(original_file)
    assert archive["account"]["id"] == first.user_id
    assert archive["account"]["slug"] == "fixture-a"
    assert [food["id"] for food in archive["food_profiles"]] == [original["food_id"]]
    assert [food["serving_description"] for food in archive["food_profiles"]] == ["1 piece"]
    assert [snapshot["id"] for snapshot in archive["consumption_snapshots"]] == [
        original["entry_id"],
        corrected["entry_id"],
    ]
    first_snapshot = archive["consumption_snapshots"][0]
    assert first_snapshot["calories"] == "162.500"
    assert first_snapshot["profile_basis"]["calories"] == "130.000"
    assert first_snapshot["notes"] == "owner-a-fixture-note"
    assert [change["replacement_id"] for change in archive["consumption_changes"]] == [
        corrected["entry_id"],
        None,
    ]
    receipts = {receipt["operation"]: receipt for receipt in archive["logging_receipts"]}
    assert receipts["log_new_food"]["response"] == original
    assert receipts["delete_consumption"]["response"]["deleted"] is True
    assert "request_hash" not in receipts["log_new_food"]
    assert "owner-b-fixture-note" not in original_file
    assert "fixture-b" not in original_file
    assert output.stat().st_mode & 0o077 == 0

    repeated = CliRunner().invoke(app, args, env=env)
    assert repeated.exit_code == 1
    assert json.loads(repeated.stdout)["error"] == "export destination already exists"
    assert output.read_text() == original_file
    assert list(tmp_path.glob(".macro-export-*")) == []
    assert first.recent_history()["entries"] == []
    assert second.recent_history()["entries"][0]["notes"] == "owner-b-fixture-note"


def test_support_export_requires_an_existing_database_and_account(tmp_path: Path) -> None:
    database = Database(tmp_path / "canonical.sqlite3")
    output = tmp_path / "archive.json"
    runner = CliRunner()
    args = ["support", "export", "--owner-id", "5000", "--output", str(output)]
    env = {"MACRO_DATABASE_PATH": str(database.path)}
    missing_database = runner.invoke(app, args, env=env)
    assert missing_database.exit_code == 1
    assert json.loads(missing_database.stdout)["error"] == "could not read Macro account data"
    assert not database.path.exists()
    assert not output.exists()

    MacroService(database)
    missing_account = runner.invoke(app, args, env=env)
    assert missing_account.exit_code == 1
    assert json.loads(missing_account.stdout)["error"] == "unknown Macro account"
    assert not output.exists()
