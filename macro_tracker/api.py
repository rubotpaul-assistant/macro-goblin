"""Authorized HTTP interface to the canonical Macro service."""

from datetime import date, datetime
from decimal import Decimal
from sqlite3 import Error as SQLiteError
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from macro_tracker.access import AccessDenied, AccessUnavailable, Authorizer, Scope
from macro_tracker.db import Database
from macro_tracker.domain import MacroProfile, SourceType
from macro_tracker.service import (
    FoodAlreadyExists,
    FoodNotFound,
    LoggingReplayConflict,
    MacroError,
    MacroService,
    StaleFoodProfile,
)

BEARER = HTTPBearer(auto_error=False, scheme_name="CreekMasonsAccess")
DecimalInput = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,6}(\.[0-9]{1,3})?$")]
OffsetTimestamp = Annotated[
    str,
    StringConstraints(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$"
    ),
]


def _parse_date(value: object) -> date:
    if isinstance(value, str):
        parsed = date.fromisoformat(value)
        if parsed.isoformat() == value:
            return parsed
    raise ValueError("date must use YYYY-MM-DD")


LocalDate = Annotated[date, BeforeValidator(_parse_date)]


class LogInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_key: str = Field(min_length=1, max_length=128)
    servings: DecimalInput
    consumed_at: OffsetTimestamp | None = None
    historical_date: LocalDate | None = None
    notes: str = Field(default="", max_length=2000)

    def timestamp(self) -> datetime | None:
        try:
            return datetime.fromisoformat(self.consumed_at) if self.consumed_at else None
        except ValueError as error:
            raise MacroError("invalid event timestamp") from error


class KnownFoodInput(LogInput):
    expected_profile_version: int | None = Field(default=None, gt=0, strict=True)


class NewFoodInput(LogInput):
    name: str = Field(min_length=1, max_length=200)
    serving_description: str = Field(min_length=1, max_length=200)
    calories: DecimalInput
    protein_g: DecimalInput
    carbs_g: DecimalInput
    fat_g: DecimalInput
    fiber_g: DecimalInput = "0"
    source_type: SourceType
    source_notes: str = Field(default="", max_length=2000)

    def profile(self) -> MacroProfile:
        try:
            return MacroProfile(
                Decimal(self.calories),
                Decimal(self.protein_g),
                Decimal(self.carbs_g),
                Decimal(self.fat_g),
                Decimal(self.fiber_g),
            )
        except ValueError as error:
            raise MacroError("invalid nutrient profile") from error


class NutrientValues(BaseModel):
    calories: str
    protein_g: str
    carbs_g: str
    fat_g: str
    fiber_g: str


class Food(NutrientValues):
    id: int
    name: str
    serving_description: str
    profile_version: int
    source_type: SourceType
    source_notes: str


class FoodSearch(BaseModel):
    items: list[Food]


class EventContext(BaseModel):
    consumed_at: str
    local_date: str
    time_zone: str | None = None
    historical_date_explicit: bool | None = None
    occurred_at_utc: str | None = None
    offset_minutes: int | None = None


class LoggedFood(EventContext):
    entry_id: int
    food_id: int
    profile_version: int
    item: str
    servings: str
    totals: NutrientValues
    source_type: SourceType


class HistoryEntry(EventContext, NutrientValues):
    id: int
    item_name: str
    serving_description: str
    servings: str
    source_type: SourceType
    source_notes: str
    notes: str


class History(BaseModel):
    entries: list[HistoryEntry]


class DailyTotals(BaseModel):
    date: str
    item_count: int
    totals: NutrientValues


class ErrorCode(BaseModel):
    code: str


class ErrorResponse(BaseModel):
    error: ErrorCode


def _error(status: int, code: str) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse({"error": {"code": code}}, status_code=status, headers=headers)


def create_app(database: Database, authorizer: Authorizer) -> FastAPI:
    """Build an app with an injected trusted account integration and no projection routes."""
    app = FastAPI(
        title="Macro Goblin",
        version="1",
        responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 503)},
    )

    @app.exception_handler(AccessDenied)
    async def access_error(request: Request, error: AccessDenied) -> JSONResponse:
        return _error(error.status, error.code)

    @app.exception_handler(AccessUnavailable)
    @app.exception_handler(SQLiteError)
    async def unavailable(request: Request, error: Exception) -> JSONResponse:
        return _error(503, "UNAVAILABLE")

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValueError)
    async def validation_error(request: Request, error: Exception) -> JSONResponse:
        return _error(422, "INVALID_INPUT")

    @app.exception_handler(MacroError)
    async def domain_error(request: Request, error: MacroError) -> JSONResponse:
        status, code = {
            FoodNotFound: (404, "NOT_FOUND"),
            FoodAlreadyExists: (409, "FOOD_ALREADY_EXISTS"),
            LoggingReplayConflict: (409, "REPLAY_CONFLICT"),
            StaleFoodProfile: (409, "STALE_FOOD_PROFILE"),
        }.get(type(error), (422, "INVALID_INPUT"))
        return _error(status, code)

    def service_for(scope: Scope) -> Any:
        def resolve(
            credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER)],
        ) -> MacroService:
            account = authorizer.require(credentials.credentials if credentials else None, scope)
            try:
                return MacroService(database, account.macro_user_slug, time_zone=account.time_zone)
            except MacroError as error:
                raise AccessUnavailable() from error

        return Depends(resolve)

    read = service_for(Scope.READ)
    write = service_for(Scope.WRITE)

    @app.get("/v1/foods", response_model=FoodSearch, operation_id="macro_search_foods")
    def search_foods(
        query: Annotated[str, Query(max_length=200)] = "", service: MacroService = read
    ) -> dict[str, Any]:
        return service.search_items(query, include_version=True)

    @app.get("/v1/history", response_model=History, operation_id="macro_read_history")
    def history(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        start_date: LocalDate | None = None,
        end_date: LocalDate | None = None,
        service: MacroService = read,
    ) -> dict[str, Any]:
        return service.recent_history(
            limit, include_event_context=True, start_date=start_date, end_date=end_date
        )

    @app.get("/v1/totals", response_model=DailyTotals, operation_id="macro_daily_totals")
    def daily_totals(day: LocalDate | None = None, service: MacroService = read) -> dict[str, Any]:
        return service.daily_summary(day.isoformat() if day is not None else None)

    @app.post(
        "/v1/foods/{food_id}/logs", response_model=LoggedFood, operation_id="macro_log_known_food"
    )
    def log_known_food(
        food_id: Annotated[int, Path(gt=0, le=2**63 - 1)],
        data: KnownFoodInput,
        service: MacroService = write,
    ) -> dict[str, Any]:
        return service.log_known_food(
            food_id,
            Decimal(data.servings),
            client_key=data.client_key,
            expected_profile_version=data.expected_profile_version,
            consumed_at=data.timestamp(),
            historical_date=data.historical_date,
            notes=data.notes,
        )

    @app.post("/v1/foods/logs", response_model=LoggedFood, operation_id="macro_log_new_food")
    def log_new_food(data: NewFoodInput, service: MacroService = write) -> dict[str, Any]:
        return service.log_new_food(
            data.name,
            data.serving_description,
            data.profile(),
            data.source_type,
            Decimal(data.servings),
            client_key=data.client_key,
            source_notes=data.source_notes,
            consumed_at=data.timestamp(),
            historical_date=data.historical_date,
            notes=data.notes,
        )

    return app
