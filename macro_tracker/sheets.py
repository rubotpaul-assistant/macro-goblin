"""Optional Google Sheets projection of canonical SQLite data."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from importlib import import_module
from json import JSONDecodeError, loads
from os import getenv
from pathlib import Path
from typing import Any, Protocol, cast

from macro_tracker.db import Database


class SheetsWriter(Protocol):
    """Small Google client boundary for deterministic tests."""

    def replace_rows(self, spreadsheet_id: str, tab: str, rows: list[list[Any]]) -> None:
        """Replace a tab with normalized rows."""


class SheetsService:
    """Project snapshots to Sheets without transferring ownership."""

    def __init__(
        self, database: Database, spreadsheet_id: str | None, writer: SheetsWriter
    ) -> None:
        self.database = database
        self.database.initialize()
        self.spreadsheet_id = spreadsheet_id
        self.writer = writer

    def status(self) -> dict[str, Any]:
        """Return sync metadata without secrets."""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT pending,last_success_at,last_error FROM sync_state WHERE singleton=1"
            ).fetchone()
        return {"ok": True, "configured": bool(self.spreadsheet_id), **dict(row)}

    def sync(self) -> dict[str, Any]:
        """Replace the projection and preserve pending state on failure."""
        if not self.spreadsheet_id:
            raise ValueError("MACRO_SPREADSHEET_ID or GYM_SPREADSHEET_ID is not configured")
        try:
            with self.database.connect() as connection:
                state = connection.execute(
                    "SELECT revision FROM sync_state WHERE singleton=1"
                ).fetchone()
                revision = int(state["revision"])
                rows = self._history_rows(connection)
            self.writer.replace_rows(self.spreadsheet_id, "Macro Log", rows)
        except Exception as error:
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE sync_state SET pending=1,last_error=? WHERE singleton=1",
                    (type(error).__name__,),
                )
            raise
        completed_at = datetime.now(tz=UTC).isoformat()
        with self.database.connect() as connection:
            result = connection.execute(
                """UPDATE sync_state SET pending=0,last_success_at=?,last_error=NULL
                WHERE singleton=1 AND revision=?""",
                (completed_at, revision),
            )
        return {"ok": True, "rows": len(rows) - 1, "pending": result.rowcount == 0}

    @staticmethod
    def _history_rows(connection: sqlite3.Connection) -> list[list[Any]]:
        header = [
            "Consumed At",
            "Local Date",
            "Item",
            "Serving",
            "Servings",
            "Calories",
            "Protein (g)",
            "Carbs (g)",
            "Fat (g)",
            "Fiber (g)",
            "Source",
            "Source Notes",
            "Notes",
        ]
        records = connection.execute(
            """SELECT consumed_at,local_date,item_name,serving_description,servings,calories,
            protein_g,carbs_g,fat_g,fiber_g,source_type,source_notes,notes
            FROM consumption_entries ORDER BY consumed_at,id"""
        ).fetchall()
        return [header, *[list(record) for record in records]]


class GoogleSheetsWriter:
    """Google API adapter using the same runtime authentication as Iron Goblin."""

    @staticmethod
    def _credentials() -> Any:
        service_account_json = getenv("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON")
        if not service_account_json:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON is not configured")
        service_account = import_module("google.oauth2.service_account")
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        credential_path = Path(service_account_json)
        if credential_path.is_file():
            factory = cast("Any", service_account.Credentials.from_service_account_file)
            return factory(credential_path, scopes=scopes)
        try:
            service_account_info = loads(service_account_json)
        except JSONDecodeError as error:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON is not valid JSON") from error
        factory = cast("Any", service_account.Credentials.from_service_account_info)
        return factory(service_account_info, scopes=scopes)

    @staticmethod
    def _ensure_tab(service: Any, spreadsheet_id: str, tab: str) -> None:
        metadata = (
            service.spreadsheets()
            .get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title")
            .execute()
        )
        titles = {sheet["properties"]["title"] for sheet in metadata.get("sheets", [])}
        if tab not in titles:
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
            ).execute()

    def replace_rows(self, spreadsheet_id: str, tab: str, rows: list[list[Any]]) -> None:
        build = cast("Any", import_module("googleapiclient.discovery").build)
        service = build("sheets", "v4", credentials=self._credentials(), cache_discovery=False)
        self._ensure_tab(service, spreadsheet_id, tab)
        values = service.spreadsheets().values()
        values.clear(spreadsheetId=spreadsheet_id, range=f"'{tab}'").execute()
        values.update(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab}'!A1",
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()
