"""Deterministic item catalog, consumption log, and summaries."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from sqlite3 import Connection, IntegrityError, Row
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from macro_tracker.db import Database
from macro_tracker.domain import MacroProfile, SourceType

PACIFIC = ZoneInfo("America/Los_Angeles")
NUTRIENT_FIELDS = ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g")


class MacroError(Exception):
    """Expected domain or user-input error."""


class LoggingReplayConflict(MacroError):
    """A client key already identifies different logging input."""


class StaleFoodProfile(MacroError):
    """The requested food profile version has been replaced."""


class FoodNotFound(MacroError):
    """The active food is absent from the owner's catalog."""


class FoodAlreadyExists(MacroError):
    """A food with the same name already exists in the owner's catalog."""


class ConsumptionNotFound(MacroError):
    """An entry ID is absent from this owner's history."""


class StaleConsumption(MacroError):
    """The selected snapshot was already corrected."""


class DeletedConsumption(MacroError):
    """The selected consumption was deleted."""


class DeletionConfirmationRequired(MacroError):
    """The owner must explicitly confirm deletion of this entry."""


class MissingServingProfile(MacroError):
    """An older snapshot needs its original serving profile for a quantity correction."""


@dataclass(frozen=True)
class ConsumptionTime:
    consumed_at: str
    local_date: str
    time_zone: str
    historical_date_explicit: bool
    occurred_at_utc: str | None
    offset_minutes: int | None


@dataclass(frozen=True)
class ConsumptionCorrection:
    servings: Decimal | None = None
    historical_date: date | None = None
    notes: str | None = None
    original_profile: MacroProfile | None = None


class MacroService:
    """Own all canonical macro-tracking mutations and queries."""

    def __init__(
        self, database: Database, user_slug: str = "geoff", *, time_zone: str = PACIFIC.key
    ) -> None:
        try:
            self.time_zone = ZoneInfo(time_zone)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise MacroError("invalid account time zone") from error
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
        clean_name, clean_serving = self._item_text(name, serving_description)
        now = datetime.now(tz=PACIFIC).isoformat()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._find_item_by_name(connection, clean_name)
            if existing is None:
                item = self._insert_item(
                    connection, clean_name, clean_serving, profile, source_type, source_notes, now
                )
                item_id = int(item["id"])
            else:
                if not replace:
                    raise MacroError(f"item already exists: {clean_name}")
                item_id = int(existing["id"])
                values = profile.as_strings()
                connection.execute(
                    """UPDATE food_items SET serving_description=?,calories=?,protein_g=?,
                    carbs_g=?,fat_g=?,fiber_g=?,source_type=?,source_notes=?,active=1,
                    updated_at=?,profile_version=profile_version+1
                    WHERE id=? AND user_id=?""",
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
                        self.user_id,
                    ),
                )
        return {
            "ok": True,
            "item_id": item_id,
            "name": clean_name,
            "replaced": existing is not None,
            "user_slug": self.user_slug,
        }

    @staticmethod
    def _item_text(name: str, serving_description: str) -> tuple[str, str]:
        name, serving_description = name.strip(), serving_description.strip()
        if not name or not serving_description:
            raise MacroError("name and serving description are required")
        return name, serving_description

    def _find_item_by_name(
        self, connection: Connection, name: str, *, active_only: bool = False
    ) -> Row | None:
        return cast(
            Row | None,
            connection.execute(
                """SELECT * FROM food_items WHERE user_id=? AND name=? COLLATE NOCASE
                AND (?=0 OR active=1)""",
                (self.user_id, name, active_only),
            ).fetchone(),
        )

    def _insert_item(
        self,
        connection: Connection,
        name: str,
        serving_description: str,
        profile: MacroProfile,
        source_type: SourceType,
        source_notes: str,
        now: str,
    ) -> Row:
        values = profile.as_strings()
        return cast(
            Row,
            connection.execute(
                """INSERT INTO food_items(
                user_id,name,serving_description,calories,protein_g,carbs_g,fat_g,fiber_g,
                source_type,source_notes,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) RETURNING *""",
                (
                    self.user_id,
                    name,
                    serving_description,
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
            ).fetchone(),
        )

    def search_items(self, query: str, *, include_version: bool = False) -> dict[str, Any]:
        """Search active definitions, optionally including their version for ID-based logging."""
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT id,name,serving_description,calories,protein_g,carbs_g,fat_g,
                fiber_g,source_type,source_notes,profile_version FROM food_items
                WHERE user_id=? AND active=1 AND name LIKE ? ORDER BY name""",
                (self.user_id, f"%{query.strip()}%"),
            ).fetchall()
        items = [dict(row) for row in rows]
        if not include_version:
            for item in items:
                del item["profile_version"]
        return {"ok": True, "user_slug": self.user_slug, "items": items}

    def log_item(
        self,
        name: str,
        servings: Decimal,
        consumed_at: datetime | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """Log one item with an immutable scaled snapshot."""
        self._validate_servings(servings)
        event_time = self._event_time(consumed_at)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            item = self._find_item_by_name(connection, name, active_only=True)
            if item is None:
                raise MacroError(f"unknown item: {name}")
            return self._insert_snapshot(connection, item, servings, event_time, notes)

    def log_known_food(
        self,
        food_id: int,
        servings: Decimal,
        *,
        client_key: str,
        expected_profile_version: int | None = None,
        consumed_at: datetime | None = None,
        historical_date: date | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """Log an active food by owner-scoped ID, with an optional version precondition."""

        def resolve_item(connection: Connection) -> Row:
            item = connection.execute(
                "SELECT * FROM food_items WHERE user_id=? AND id=? AND active=1",
                (self.user_id, food_id),
            ).fetchone()
            if item is None:
                raise FoodNotFound("unknown food")
            if (
                expected_profile_version is not None
                and item["profile_version"] != expected_profile_version
            ):
                raise StaleFoodProfile("food profile changed; read it again before logging")
            return cast(Row, item)

        return self._log_with_replay(
            "log_known_food",
            client_key,
            {"food_id": food_id, "expected_profile_version": expected_profile_version},
            servings,
            consumed_at,
            historical_date,
            notes,
            resolve_item,
        )

    def log_new_food(
        self,
        name: str,
        serving_description: str,
        profile: MacroProfile,
        source_type: SourceType,
        servings: Decimal,
        *,
        client_key: str,
        source_notes: str = "",
        consumed_at: datetime | None = None,
        historical_date: date | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """Create a food definition and log its first consumption atomically."""
        name, serving_description = self._item_text(name, serving_description)

        def resolve_item(connection: Connection) -> Row:
            if self._find_item_by_name(connection, name) is not None:
                raise FoodAlreadyExists(f"item already exists: {name}")
            return self._insert_item(
                connection,
                name,
                serving_description,
                profile,
                source_type,
                source_notes,
                datetime.now(tz=PACIFIC).isoformat(),
            )

        return self._log_with_replay(
            "log_new_food",
            client_key,
            {
                "name": name,
                "serving_description": serving_description,
                "profile": profile.as_strings(),
                "source_type": source_type.value,
                "source_notes": source_notes,
            },
            servings,
            consumed_at,
            historical_date,
            notes,
            resolve_item,
        )

    def _log_with_replay(
        self,
        operation: str,
        client_key: str,
        food_input: dict[str, Any],
        servings: Decimal,
        consumed_at: datetime | None,
        historical_date: date | None,
        notes: str,
        resolve_item: Callable[[Connection], Row],
    ) -> dict[str, Any]:
        self._validate_servings(servings)
        event_time = self._event_time(consumed_at, historical_date)
        payload = {
            **food_input,
            "servings": format(servings, ".3f"),
            "consumed_at": None if consumed_at is None else consumed_at.astimezone(UTC).isoformat(),
            "notes": notes,
        }
        # Preserve the fingerprint for receipts created before explicit dates were supported.
        if historical_date is not None:
            payload["historical_date"] = historical_date.isoformat()

        def record(connection: Connection) -> dict[str, Any]:
            item = resolve_item(connection)
            return {
                **self._insert_snapshot(connection, item, servings, event_time, notes),
                "food_id": item["id"],
                "profile_version": item["profile_version"],
                **asdict(event_time),
                "source_type": item["source_type"],
            }

        return self._write_with_receipt(operation, client_key, payload, record, check_deleted=True)

    def _write_with_receipt(
        self,
        operation: str,
        client_key: str,
        payload: dict[str, Any],
        record: Callable[[Connection], dict[str, Any]],
        *,
        check_deleted: bool,
    ) -> dict[str, Any]:
        if not client_key.strip() or len(client_key) > 128:
            raise MacroError("client key must contain 1 to 128 characters")
        request_hash = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        receipt_key = (self.user_id, operation, client_key)
        with self.database.connect() as connection:
            # Reserve the write before deciding whether to replay or create a snapshot.
            connection.execute("BEGIN IMMEDIATE")
            receipt = connection.execute(
                """SELECT request_hash,response_json,entry_id FROM logging_receipts
                WHERE user_id=? AND operation=? AND client_key=?""",
                receipt_key,
            ).fetchone()
            if receipt is not None:
                if receipt["request_hash"] != request_hash:
                    raise LoggingReplayConflict("client key already used for different input")
                if check_deleted and self._is_deleted(connection, receipt["entry_id"]):
                    raise DeletedConsumption("consumption was deleted")
                return cast(dict[str, Any], json.loads(receipt["response_json"]))

            result = record(connection)
            connection.execute(
                """INSERT INTO logging_receipts(
                user_id,operation,client_key,request_hash,entry_id,response_json
                ) VALUES(?,?,?,?,?,?)""",
                (*receipt_key, request_hash, result["entry_id"], json.dumps(result)),
            )
        return result

    def _is_deleted(self, connection: Connection, entry_id: int) -> bool:
        """Follow correction links to see whether an entry was later deleted."""
        return (
            connection.execute(
                """WITH RECURSIVE versions(id) AS (
                    SELECT ?
                    UNION ALL
                    SELECT change.replacement_id FROM consumption_changes AS change
                    JOIN versions ON versions.id=change.entry_id
                    WHERE change.user_id=? AND change.replacement_id IS NOT NULL
                )
                SELECT 1 FROM consumption_changes AS change
                JOIN versions ON versions.id=change.entry_id
                WHERE change.user_id=? AND change.replacement_id IS NULL""",
                (entry_id, self.user_id, self.user_id),
            ).fetchone()
            is not None
        )

    def _current_entry(self, connection: Connection, entry_id: int) -> Row:
        entry = connection.execute(
            """SELECT entry.*,change.entry_id AS changed,change.replacement_id
            FROM consumption_entries AS entry
            LEFT JOIN consumption_changes AS change
                ON change.entry_id=entry.id AND change.user_id=entry.user_id
            WHERE entry.id=? AND entry.user_id=?""",
            (entry_id, self.user_id),
        ).fetchone()
        if entry is None:
            raise ConsumptionNotFound("unknown consumption")
        if entry["changed"] is not None:
            if entry["replacement_id"] is None:
                raise DeletedConsumption("consumption was deleted")
            raise StaleConsumption("consumption was corrected; read history again")
        return cast(Row, entry)

    def correct_consumption(
        self, entry_id: int, correction: ConsumptionCorrection, *, client_key: str
    ) -> dict[str, Any]:
        """Append a corrected snapshot and record which entry it replaces."""
        if correction.servings is not None:
            self._validate_servings(correction.servings)
        elif correction.original_profile is not None:
            raise MacroError("an original profile is only needed for a quantity correction")
        if (
            correction.servings is None
            and correction.historical_date is None
            and correction.notes is None
        ):
            raise MacroError("at least one correction is required")
        day = correction.historical_date.isoformat() if correction.historical_date else None
        supplied_basis = correction.original_profile
        payload = {
            "entry_id": entry_id,
            "servings": (
                format(correction.servings, ".3f") if correction.servings is not None else None
            ),
            "historical_date": day,
            "notes": correction.notes,
            "original_profile": supplied_basis.as_strings() if supplied_basis is not None else None,
        }

        def record(connection: Connection) -> dict[str, Any]:
            entry = self._current_entry(connection, entry_id)
            event_time = ConsumptionTime(
                consumed_at=day
                if day and entry["occurred_at_utc"] is None
                else entry["consumed_at"],
                local_date=day or entry["local_date"],
                time_zone=entry["time_zone"],
                historical_date_explicit=bool(day or entry["historical_date_explicit"]),
                occurred_at_utc=entry["occurred_at_utc"],
                offset_minutes=entry["offset_minutes"],
            )
            basis = entry["profile_basis"]
            servings = entry["servings"]
            values = {field: entry[field] for field in NUTRIENT_FIELDS}
            if correction.servings is not None:
                if basis is not None:
                    if supplied_basis is not None:
                        raise MacroError("the snapshot already has its original serving profile")
                    profile = self._profile_from_values(json.loads(basis))
                elif supplied_basis is not None:
                    profile = supplied_basis
                    original = profile.scale(Decimal(entry["servings"])).as_strings()
                    if any(
                        Decimal(original[field]) != Decimal(values[field])
                        for field in NUTRIENT_FIELDS
                    ):
                        raise MacroError("original per-serving values do not match the snapshot")
                    basis = json.dumps(profile.as_strings())
                else:
                    raise MissingServingProfile("original per-serving values are required")
                servings = format(correction.servings, "f")
                values = profile.scale(correction.servings).as_strings()
            notes = entry["notes"] if correction.notes is None else correction.notes
            saved = self._save_snapshot(
                connection, entry, servings, event_time, notes, values, basis
            )
            self._mark_entry_changed(connection, entry_id, saved["entry_id"])
            return {
                **saved,
                "previous_entry_id": entry_id,
                "food_id": entry["food_item_id"],
                **asdict(event_time),
                "source_type": entry["source_type"],
            }

        return self._write_with_receipt(
            "correct_consumption", client_key, payload, record, check_deleted=True
        )

    def delete_consumption(
        self, entry_id: int, *, client_key: str, confirmed: bool
    ) -> dict[str, Any]:
        """Void one selected snapshot after explicit confirmation."""
        if confirmed is not True:
            raise DeletionConfirmationRequired("confirm deletion of the selected entry")

        def record(connection: Connection) -> dict[str, Any]:
            self._current_entry(connection, entry_id)
            self._mark_entry_changed(connection, entry_id, None)
            self._advance_revision(connection)
            return {"entry_id": entry_id, "deleted": True}

        return self._write_with_receipt(
            "delete_consumption", client_key, {"entry_id": entry_id}, record, check_deleted=False
        )

    def _mark_entry_changed(
        self, connection: Connection, entry_id: int, replacement_id: int | None
    ) -> None:
        connection.execute(
            """INSERT INTO consumption_changes(user_id,entry_id,replacement_id,changed_at)
            VALUES(?,?,?,?)""",
            (self.user_id, entry_id, replacement_id, datetime.now(tz=UTC).isoformat()),
        )

    @staticmethod
    def _validate_servings(servings: Decimal) -> None:
        if not servings.is_finite():
            raise MacroError("servings must be finite")
        if servings <= 0:
            raise MacroError("servings must be positive")
        if servings > Decimal("10000"):
            raise MacroError("servings exceed reasonable maximum")
        exponent = servings.as_tuple().exponent
        if isinstance(exponent, int) and exponent < -3:
            raise MacroError("servings support at most three decimal places")

    def _event_time(
        self, consumed_at: datetime | None, historical_date: date | None = None
    ) -> ConsumptionTime:
        if consumed_at is None and historical_date is not None:
            day = historical_date.isoformat()
            return ConsumptionTime(day, day, self.time_zone.key, True, None, None)

        timestamp = consumed_at or datetime.now(tz=self.time_zone)
        offset = timestamp.utcoffset()
        if offset is None:
            raise MacroError("consumed-at timestamp must include a timezone")
        localized = timestamp.astimezone(self.time_zone)
        offset_minutes, remainder = divmod(offset.total_seconds(), 60)
        return ConsumptionTime(
            localized.isoformat(),
            (historical_date or localized.date()).isoformat(),
            self.time_zone.key,
            historical_date is not None,
            timestamp.astimezone(UTC).isoformat(timespec="microseconds"),
            None if remainder else int(offset_minutes),
        )

    def _insert_snapshot(
        self,
        connection: Connection,
        item: Row,
        servings: Decimal,
        event_time: ConsumptionTime,
        notes: str,
    ) -> dict[str, Any]:
        profile = self._profile_from_values(item)
        source = {**dict(item), "food_item_id": item["id"], "item_name": item["name"]}
        return self._save_snapshot(
            connection,
            source,
            format(servings, "f"),
            event_time,
            notes,
            profile.scale(servings).as_strings(),
            json.dumps(profile.as_strings()),
        )

    @staticmethod
    def _profile_from_values(values: Row | Mapping[str, str]) -> MacroProfile:
        return MacroProfile(*(Decimal(values[field]) for field in NUTRIENT_FIELDS))

    def _save_snapshot(
        self,
        connection: Connection,
        source: Row | Mapping[str, Any],
        servings: str,
        event_time: ConsumptionTime,
        notes: str,
        values: dict[str, str],
        basis: str | None,
    ) -> dict[str, Any]:
        cursor = connection.execute(
            """INSERT INTO consumption_entries(
            user_id,food_item_id,consumed_at,local_date,time_zone,historical_date_explicit,
            occurred_at_utc,offset_minutes,item_name,serving_description,servings,
            calories,protein_g,carbs_g,fat_g,fiber_g,source_type,source_notes,notes,profile_basis
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                self.user_id,
                source["food_item_id"],
                event_time.consumed_at,
                event_time.local_date,
                event_time.time_zone,
                event_time.historical_date_explicit,
                event_time.occurred_at_utc,
                event_time.offset_minutes,
                source["item_name"],
                source["serving_description"],
                servings,
                values["calories"],
                values["protein_g"],
                values["carbs_g"],
                values["fat_g"],
                values["fiber_g"],
                source["source_type"],
                source["source_notes"],
                notes,
                basis,
            ),
        )
        self._advance_revision(connection)
        return {
            "ok": True,
            "entry_id": int(cursor.lastrowid or 0),
            "item": source["item_name"],
            "servings": servings,
            "totals": values,
            "sync_pending": True,
            "user_slug": self.user_slug,
        }

    @staticmethod
    def _advance_revision(connection: Connection) -> None:
        connection.execute("UPDATE sync_state SET pending=1,revision=revision+1 WHERE singleton=1")

    def recent_history(
        self,
        limit: int = 20,
        *,
        include_event_context: bool = False,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        """Return recent consumption snapshots within the optional inclusive local-date range."""
        if limit < 1 or limit > 100:
            raise MacroError("limit must be between 1 and 100")
        if start_date is not None and end_date is not None and start_date > end_date:
            raise MacroError("start date must precede or equal end date")
        lower = start_date.isoformat() if start_date is not None else None
        upper = end_date.isoformat() if end_date is not None else None
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT id,consumed_at,local_date,item_name,serving_description,servings,
                calories,protein_g,carbs_g,fat_g,fiber_g,source_type,source_notes,notes,
                time_zone,historical_date_explicit,occurred_at_utc,offset_minutes
                FROM active_consumption_entries WHERE user_id=?
                AND (? IS NULL OR local_date>=?) AND (? IS NULL OR local_date<=?)
                ORDER BY local_date DESC,occurred_at_utc DESC,id DESC LIMIT ?""",
                (self.user_id, lower, lower, upper, upper, limit),
            ).fetchall()
        entries = [{"user_slug": self.user_slug, **dict(row)} for row in rows]
        for entry in entries:
            if include_event_context:
                entry["historical_date_explicit"] = bool(entry["historical_date_explicit"])
            else:
                for field in (
                    "time_zone",
                    "historical_date_explicit",
                    "occurred_at_utc",
                    "offset_minutes",
                ):
                    del entry[field]
        return {"ok": True, "user_slug": self.user_slug, "entries": entries}

    def daily_summary(self, local_date: str | None = None) -> dict[str, Any]:
        """Aggregate snapshots for a stored local day, defaulting to the account's today."""
        if local_date is None:
            local_date = datetime.now(tz=self.time_zone).date().isoformat()
        try:
            parsed_date = date.fromisoformat(local_date)
        except ValueError as error:
            raise MacroError("date must use YYYY-MM-DD") from error
        if parsed_date.isoformat() != local_date:
            raise MacroError("date must use YYYY-MM-DD")
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT calories,protein_g,carbs_g,fat_g,fiber_g
                FROM active_consumption_entries WHERE user_id=? AND local_date=?""",
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
