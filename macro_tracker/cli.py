"""Structured CLI for deterministic macro tracking."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any

import typer

from macro_tracker.config import Settings
from macro_tracker.db import Database
from macro_tracker.domain import MacroProfile, SourceType
from macro_tracker.service import MacroError, MacroService
from macro_tracker.sheets import GoogleSheetsWriter, SheetsService
from macro_tracker.support import write_account_export

app = typer.Typer(no_args_is_help=True)
item_app = typer.Typer(no_args_is_help=True)
log_app = typer.Typer(no_args_is_help=True)
history_app = typer.Typer(no_args_is_help=True)
summary_app = typer.Typer(no_args_is_help=True)
sheets_app = typer.Typer(no_args_is_help=True)
user_app = typer.Typer(no_args_is_help=True)
support_app = typer.Typer(no_args_is_help=True)
app.add_typer(item_app, name="item")
app.add_typer(log_app, name="log")
app.add_typer(history_app, name="history")
app.add_typer(summary_app, name="summary")
app.add_typer(sheets_app, name="sheets")
app.add_typer(user_app, name="user")
app.add_typer(support_app, name="support")


def _service() -> MacroService:
    settings = Settings.from_environment()
    return MacroService(Database(settings.database_path), settings.user_slug)


def _admin_service() -> MacroService:
    settings = Settings.from_environment()
    return MacroService(Database(settings.database_path))


def _emit(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, sort_keys=True))


def _run(operation: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> None:
    try:
        _emit(operation(*args, **kwargs))
    except (MacroError, ValueError, InvalidOperation) as error:
        _emit({"ok": False, "error": str(error), "error_type": type(error).__name__})
        raise typer.Exit(code=1) from error


def _profile(calories: str, protein_g: str, carbs_g: str, fat_g: str, fiber_g: str) -> MacroProfile:
    try:
        return MacroProfile(
            Decimal(calories),
            Decimal(protein_g),
            Decimal(carbs_g),
            Decimal(fat_g),
            Decimal(fiber_g),
        )
    except InvalidOperation as error:
        raise MacroError("macro values must be numeric") from error


@user_app.command("add")
def user_add(
    slug: str,
    display_name: Annotated[str, typer.Option("--display-name")],
) -> None:
    """Add a user with an isolated catalog and history."""
    _run(_admin_service().add_user, slug, display_name)


@user_app.command("list")
def user_list() -> None:
    """List active users."""
    _run(_admin_service().list_users)


@support_app.command("export")
def support_export(
    owner_id: Annotated[int, typer.Option("--owner-id", min=1, max=2**63 - 1)],
    output: Annotated[Path, typer.Option("--output")],
) -> None:
    """Write a private Macro archive for one verified owner."""
    settings = Settings.from_environment()
    _run(write_account_export, Database(settings.database_path), owner_id, output)


@item_app.command("add")
def item_add(
    name: str,
    serving: Annotated[str, typer.Option("--serving")],
    calories: Annotated[str, typer.Option("--calories")],
    protein_g: Annotated[str, typer.Option("--protein-g")],
    carbs_g: Annotated[str, typer.Option("--carbs-g")],
    fat_g: Annotated[str, typer.Option("--fat-g")],
    fiber_g: Annotated[str, typer.Option("--fiber-g")] = "0",
    source: Annotated[SourceType, typer.Option("--source")] = SourceType.MANUAL,
    source_notes: Annotated[str, typer.Option("--source-notes")] = "",
    replace: Annotated[bool, typer.Option("--replace")] = False,
) -> None:
    """Add or explicitly replace a per-serving food item."""

    def operation() -> dict[str, Any]:
        return _service().add_item(
            name,
            serving,
            _profile(calories, protein_g, carbs_g, fat_g, fiber_g),
            source,
            source_notes,
            replace=replace,
        )

    _run(operation)


@item_app.command("search")
def item_search(query: str) -> None:
    """Search stored food definitions."""
    _run(_service().search_items, query)


@log_app.command("item")
def log_item(
    item: Annotated[str, typer.Option("--item")],
    servings: Annotated[str, typer.Option("--servings")] = "1",
    consumed_at: Annotated[str | None, typer.Option("--consumed-at")] = None,
    notes: Annotated[str, typer.Option("--notes")] = "",
) -> None:
    """Log one item and snapshot its scaled macros."""

    def operation() -> dict[str, Any]:
        try:
            parsed_servings = Decimal(servings)
        except InvalidOperation as error:
            raise MacroError("servings must be numeric") from error
        try:
            timestamp = datetime.fromisoformat(consumed_at) if consumed_at else None
        except ValueError as error:
            raise MacroError("consumed-at must be ISO 8601") from error
        return _service().log_item(item, parsed_servings, timestamp, notes)

    _run(operation)


@history_app.command("recent")
def history_recent(limit: Annotated[int, typer.Option("--limit")] = 20) -> None:
    """Show recent item-level history."""
    _run(_service().recent_history, limit)


@summary_app.command("day")
def summary_day(date: str) -> None:
    """Summarize one Pacific local date."""
    _run(_service().daily_summary, date)


def _sheets_service() -> SheetsService:
    settings = Settings.from_environment()
    return SheetsService(
        Database(settings.database_path), settings.spreadsheet_id, GoogleSheetsWriter()
    )


@sheets_app.command("status")
def sheets_status() -> None:
    """Show safe synchronization metadata."""
    _run(_sheets_service().status)


@sheets_app.command("sync")
def sheets_sync() -> None:
    """Project item-level history to the Macro Log tab."""

    def operation() -> dict[str, Any]:
        try:
            return _sheets_service().sync()
        except (MacroError, ValueError):
            raise
        except Exception as error:
            raise MacroError("Google Sheets sync failed") from error

    _run(operation)


if __name__ == "__main__":
    app()
