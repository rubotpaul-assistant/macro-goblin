"""Deterministic item catalog, consumption log, and summaries."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from sqlite3 import IntegrityError
from typing import Any
from zoneinfo import ZoneInfo

from macro_tracker.db import Database
from macro_tracker.domain import MacroProfile, SourceType

PACIFIC = ZoneInfo("America/Los_Angeles")


class MacroError(Exception):
    """Expected domain or user-input error."""


class MacroService:
    """Own all canonical macro-tracking mutations and queries."""

    def __init__(self, database: Database, user_slug: str = "geoff") -> None:
        self.database = database
        self.database.initialize()
        with self.database.connect() as connection:
            user = connection.execute(
                "SELECT id,slug FROM users WHERE active=1 AND slug=? COLLATE NOCASE",
                (user_slug.strip(),),
            ).fetchone()
        if user is None:
            raise MacroError(f"unknown user: {user_slug}")
        self.user_id = int(user["id"])
        self.user_slug = str(user["slug"])

    def add_user(self, slug: str, display_name: str) -> dict[str, Any]:
        """Create a user who owns an isolated catalog and consumption history."""
        clean_slug = slug.strip().lower()
        clean_name = display_name.strip()
        if not clean_slug or not clean_name:
            raise MacroError("user slug and display name are required")
        if not clean_slug.replace("-", "").isalnum():
            raise MacroError("user slug may contain only letters, numbers, and hyphens")
        with self.database.connect() as connection:
            try:
                cursor = connection.execute(
                    "INSERT INTO users(slug,display_name,created_at) VALUES(?,?,?)",
                    (clean_slug, clean_name, datetime.now(tz=PACIFIC).isoformat()),
                )
            except IntegrityError as error:
                raise MacroError(f"user already exists: {clean_slug}") from error
        return {"ok": True, "user_id": int(cursor.lastrowid or 0), "slug": clean_slug}

    def list_users(self) -> dict[str, Any]:
        """List active users without exposing unrelated state."""
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id,slug,display_name FROM users WHERE active=1 ORDER BY slug"
            ).fetchall()
        return {"ok": True, "users": [dict(row) for row in rows]}

    def add_item(
        self,
        name: str,
        serving_description: str,
        profile: MacroProfile,
        source_type: SourceType,
        source_notes: str = "",
        *,
        replace: bool = False,
    ) -> dict[str, Any]:
        """Add a food definition or explicitly replace its future-use profile."""
        clean_name = name.strip()
        clean_serving = serving_description.strip()
        if not clean_name or not clean_serving:
            raise MacroError("name and serving description are required")
        now = datetime.now(tz=PACIFIC).isoformat()
        values = profile.as_strings()
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM food_items WHERE user_id=? AND name=? COLLATE NOCASE",
                (self.user_id, clean_name),
            ).fetchone()
            if existing is not None and not replace:
                raise MacroError(f"item already exists: {clean_name}")
            if existing is None:
                cursor = connection.execute(
                    """INSERT INTO food_items(
                    user_id,name,serving_description,calories,protein_g,carbs_g,fat_g,fiber_g,
                    source_type,source_notes,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        self.user_id,
                        clean_name,
                        clean_serving,
                        values["calories"],
                        values["protein_g"],
                        values["carbs_g"],
                        values["fat_g"],
                        values["fiber_g"],
                        source_type.value,
                        source_notes,
                        now,
                        now,
                    ),
                )
                item_id = int(cursor.lastrowid or 0)
            else:
                item_id = int(existing["id"])
                connection.execute(
                    """UPDATE food_items SET serving_description=?,calories=?,protein_g=?,
                    carbs_g=?,fat_g=?,fiber_g=?,source_type=?,source_notes=?,active=1,updated_at=?
                    WHERE id=?""",
                    (
                        clean_serving,
                        values["calories"],
                        values["protein_g"],
                        values["carbs_g"],
                        values["fat_g"],
                        values["fiber_g"],
                        source_type.value,
                        source_notes,
                        now,
                        item_id,
                    ),
                )
        return {
            "ok": True,
            "item_id": item_id,
            "name": clean_name,
            "replaced": existing is not None,
            "user_slug": self.user_slug,
        }

    def search_items(self, query: str) -> dict[str, Any]:
        """Search active item definitions by name."""
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT id,name,serving_description,calories,protein_g,carbs_g,fat_g,
                fiber_g,source_type,source_notes FROM food_items
                WHERE user_id=? AND active=1 AND name LIKE ? ORDER BY name""",
                (self.user_id, f"%{query.strip()}%"),
            ).fetchall()
        return {"ok": True, "user_slug": self.user_slug, "items": [dict(row) for row in rows]}

    def log_item(
        self,
        name: str,
        servings: Decimal,
        consumed_at: datetime | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """Log one item with an immutable scaled snapshot."""
        if not servings.is_finite():
            raise MacroError("servings must be finite")
        if servings <= 0:
            raise MacroError("servings must be positive")
        if servings > Decimal("10000"):
            raise MacroError("servings exceed reasonable maximum")
        exponent = servings.as_tuple().exponent
        if isinstance(exponent, int) and exponent < -3:
            raise MacroError("servings support at most three decimal places")
        timestamp = consumed_at or datetime.now(tz=PACIFIC)
        if timestamp.tzinfo is None:
            raise MacroError("consumed-at timestamp must include a timezone")
        timestamp = timestamp.astimezone(PACIFIC)
        with self.database.connect() as connection:
            item = connection.execute(
                """SELECT * FROM food_items
                WHERE user_id=? AND active=1 AND name=? COLLATE NOCASE""",
                (self.user_id, name),
            ).fetchone()
            if item is None:
                raise MacroError(f"unknown item: {name}")
            profile = MacroProfile(
                Decimal(item["calories"]),
                Decimal(item["protein_g"]),
                Decimal(item["carbs_g"]),
                Decimal(item["fat_g"]),
                Decimal(item["fiber_g"]),
            ).scale(servings)
            values = profile.as_strings()
            cursor = connection.execute(
                """INSERT INTO consumption_entries(
                user_id,food_item_id,consumed_at,local_date,item_name,serving_description,servings,
                calories,protein_g,carbs_g,fat_g,fiber_g,source_type,source_notes,notes
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    self.user_id,
                    item["id"],
                    timestamp.isoformat(),
                    timestamp.date().isoformat(),
                    item["name"],
                    item["serving_description"],
                    format(servings, "f"),
                    values["calories"],
                    values["protein_g"],
                    values["carbs_g"],
                    values["fat_g"],
                    values["fiber_g"],
                    item["source_type"],
                    item["source_notes"],
                    notes,
                ),
            )
            connection.execute(
                "UPDATE sync_state SET pending=1,revision=revision+1 WHERE singleton=1"
            )
            entry_id = int(cursor.lastrowid or 0)
        return {
            "ok": True,
            "entry_id": entry_id,
            "item": item["name"],
            "servings": format(servings, "f"),
            "totals": values,
            "sync_pending": True,
            "user_slug": self.user_slug,
        }

    def recent_history(self, limit: int = 20) -> dict[str, Any]:
        """Return recent item-level consumption snapshots."""
        if limit < 1 or limit > 100:
            raise MacroError("limit must be between 1 and 100")
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT id,consumed_at,local_date,item_name,serving_description,servings,
                calories,protein_g,carbs_g,fat_g,fiber_g,source_type,source_notes,notes
                FROM consumption_entries WHERE user_id=?
                ORDER BY consumed_at DESC,id DESC LIMIT ?""",
                (self.user_id, limit),
            ).fetchall()
        entries = [{"user_slug": self.user_slug, **dict(row)} for row in rows]
        return {"ok": True, "user_slug": self.user_slug, "entries": entries}

    def daily_summary(self, local_date: str) -> dict[str, Any]:
        """Aggregate stored snapshots for one Pacific local date."""
        try:
            parsed_date = date.fromisoformat(local_date)
        except ValueError as error:
            raise MacroError("date must use YYYY-MM-DD") from error
        if parsed_date.isoformat() != local_date:
            raise MacroError("date must use YYYY-MM-DD")
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT calories,protein_g,carbs_g,fat_g,fiber_g
                FROM consumption_entries WHERE user_id=? AND local_date=?""",
                (self.user_id, local_date),
            ).fetchall()
        totals = MacroProfile(
            *(
                sum((Decimal(row[key]) for row in rows), Decimal("0"))
                for key in ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g")
            )
        )
        return {
            "ok": True,
            "date": local_date,
            "item_count": len(rows),
            "totals": totals.as_strings(),
            "user_slug": self.user_slug,
        }
