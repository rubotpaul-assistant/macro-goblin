"""Offline export of an explicitly selected Macro account."""

from __future__ import annotations

import json
import os
from pathlib import Path
from sqlite3 import Error as SQLiteError
from tempfile import NamedTemporaryFile
from typing import Any

from macro_tracker.db import Database
from macro_tracker.service import MacroError

EXPORT_QUERIES = {
    "food_profiles": """SELECT id,name,serving_description,calories,protein_g,carbs_g,fat_g,fiber_g,
        source_type,source_notes,active,created_at,updated_at,profile_version
        FROM food_items WHERE user_id=? ORDER BY id""",
    "consumption_snapshots": """SELECT id,food_item_id,consumed_at,local_date,time_zone,
        historical_date_explicit,occurred_at_utc,offset_minutes,item_name,serving_description,
        servings,calories,protein_g,carbs_g,fat_g,fiber_g,source_type,source_notes,notes,profile_basis
        FROM consumption_entries WHERE user_id=? ORDER BY id""",
    "consumption_changes": """SELECT entry_id,replacement_id,changed_at
        FROM consumption_changes WHERE user_id=? ORDER BY entry_id""",
    "logging_receipts": """SELECT operation,client_key,entry_id,response_json
        FROM logging_receipts WHERE user_id=? ORDER BY operation,client_key""",
}


def _read_account_export(database: Database, owner_id: int) -> dict[str, Any]:
    try:
        with database.connect(read_only=True) as connection:
            connection.execute("BEGIN")
            user = connection.execute(
                "SELECT id,slug,display_name,active,created_at FROM users WHERE id=?",
                (owner_id,),
            ).fetchone()
            if user is None:
                raise MacroError("unknown Macro account")
            sections = {
                name: [dict(row) for row in connection.execute(query, (owner_id,))]
                for name, query in EXPORT_QUERIES.items()
            }
    except SQLiteError as error:
        raise MacroError("could not read Macro account data") from error
    for snapshot in sections["consumption_snapshots"]:
        if snapshot["profile_basis"] is not None:
            snapshot["profile_basis"] = json.loads(snapshot["profile_basis"])
    for receipt in sections["logging_receipts"]:
        receipt["response"] = json.loads(receipt.pop("response_json"))
    return {"schema_version": 1, "account": dict(user), **sections}


def write_account_export(database: Database, owner_id: int, output: Path) -> dict[str, Any]:
    """Publish a complete, private file without replacing an existing destination."""
    archive = _read_account_export(database, owner_id)
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".macro-export-",
            dir=output.parent,
            delete_on_close=False,
        ) as temporary:
            json.dump(archive, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            os.link(temporary.name, output)
    except FileExistsError as error:
        raise MacroError("export destination already exists") from error
    except OSError as error:
        raise MacroError("could not write Macro account export") from error
    return {"ok": True, "schema_version": archive["schema_version"]}
