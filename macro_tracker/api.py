"""Authorized HTTP interface to the canonical Macro service."""

from datetime import date, datetime
from decimal import Decimal
from sqlite3 import Error as SQLiteError
from typing import Annotated, Any, Self

from fastapi import Depends, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from macro_tracker.access import AccessDenied, AccessUnavailable, Account, Authorizer, Scope
from macro_tracker.db import Database
from macro_tracker.domain import MacroProfile, SourceType
from macro_tracker.service import (
    ConsumptionCorrection,
    ConsumptionNotFound,
    DeletedConsumption,
    DeletionConfirmationRequired,
    FoodAlreadyExists,
    FoodNotFound,
    LoggingReplayConflict,
    MacroError,
    MacroService,
    MissingServingProfile,
    StaleConsumption,
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
ClientKey = Annotated[str, StringConstraints(min_length=1, max_length=128)]
EntryPath = Annotated[int, Path(gt=0, le=2**63 - 1)]


def _parse_date(value: object) -> date:
    if isinstance(value, str):
        parsed = date.fromisoformat(value)
        if parsed.isoformat() == value:
            return parsed
    raise ValueError("date must use YYYY-MM-DD")


LocalDate = Annotated[date, BeforeValidator(_parse_date)]


class LogInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_key: ClientKey
    servings: DecimalInput = Field(description="Amount consumed in units of the declared serving.")
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


class ServingProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calories: DecimalInput
    protein_g: DecimalInput
    carbs_g: DecimalInput
    fat_g: DecimalInput
    fiber_g: DecimalInput = Field(
        default="0", description="Dietary fiber per serving. Required for label provenance."
    )

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


class NewFoodInput(LogInput, ServingProfileInput):
    model_config = ConfigDict(
        json_schema_extra={
            "if": {"properties": {"source_type": {"const": "label"}}},
            "then": {"required": ["fiber_g"]},
        }
    )

    name: str = Field(min_length=1, max_length=200)
    serving_description: str = Field(
        min_length=1, max_length=200, description="Serving size for the submitted nutrient values."
    )
    source_type: SourceType = Field(
        description="Use label for clear nutrition facts, estimate for an assistant estimate, "
        "or manual for values explicitly supplied as manual."
    )
    source_notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def require_label_fiber(self) -> Self:
        if self.source_type is SourceType.LABEL and "fiber_g" not in self.model_fields_set:
            raise ValueError("label provenance requires a fiber value")
        return self


class CorrectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_key: ClientKey
    servings: DecimalInput | None = Field(default=None, description="Corrected amount consumed.")
    historical_date: LocalDate | None = None
    notes: str | None = Field(default=None, max_length=2000)
    original_profile: ServingProfileInput | None = Field(
        default=None, description="Original per-serving values for an older entry when requested."
    )


class DeletionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_key: ClientKey
    confirmed: bool = Field(
        default=False,
        strict=True,
        description="Set true only after the user explicitly confirms the selected deletion.",
    )


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


class ConsumptionResult(EventContext):
    entry_id: int
    food_id: int
    item: str
    servings: str
    totals: NutrientValues
    source_type: SourceType


class LoggedFood(ConsumptionResult):
    profile_version: int


class CorrectedConsumption(ConsumptionResult):
    previous_entry_id: int


class ConsumptionDeletion(BaseModel):
    entry_id: int
    deleted: bool


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


def domain_error_status(error: MacroError) -> tuple[int, str]:
    return {
        FoodNotFound: (404, "NOT_FOUND"),
        ConsumptionNotFound: (404, "NOT_FOUND"),
        FoodAlreadyExists: (409, "FOOD_ALREADY_EXISTS"),
        LoggingReplayConflict: (409, "REPLAY_CONFLICT"),
        StaleFoodProfile: (409, "STALE_FOOD_PROFILE"),
        StaleConsumption: (409, "STALE_CONSUMPTION"),
        DeletedConsumption: (410, "CONSUMPTION_DELETED"),
        DeletionConfirmationRequired: (422, "CONFIRMATION_REQUIRED"),
        MissingServingProfile: (422, "MISSING_SERVING_PROFILE"),
    }.get(type(error), (422, "INVALID_INPUT"))


def account_service(database: Database, account: Account) -> MacroService:
    try:
        return MacroService(database, account.macro_user_slug, time_zone=account.time_zone)
    except MacroError as error:
        raise AccessUnavailable() from error


def record_known_food(service: MacroService, food_id: int, data: KnownFoodInput) -> dict[str, Any]:
    return service.log_known_food(
        food_id,
        Decimal(data.servings),
        client_key=data.client_key,
        expected_profile_version=data.expected_profile_version,
        consumed_at=data.timestamp(),
        historical_date=data.historical_date,
        notes=data.notes,
    )


def record_new_food(service: MacroService, data: NewFoodInput) -> dict[str, Any]:
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


def apply_correction(service: MacroService, entry_id: int, data: CorrectionInput) -> dict[str, Any]:
    correction = ConsumptionCorrection(
        servings=Decimal(data.servings) if data.servings is not None else None,
        historical_date=data.historical_date,
        notes=data.notes,
        original_profile=data.original_profile.profile() if data.original_profile else None,
    )
    return service.correct_consumption(entry_id, correction, client_key=data.client_key)


def apply_deletion(service: MacroService, entry_id: int, data: DeletionInput) -> dict[str, Any]:
    return service.delete_consumption(
        entry_id, client_key=data.client_key, confirmed=data.confirmed
    )


def create_app(database: Database, authorizer: Authorizer) -> FastAPI:
    """Build an app with an injected trusted account integration and no projection routes."""
    app = FastAPI(
        title="Macro Goblin",
        version="1",
        responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 410, 422, 503)},
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
        return _error(*domain_error_status(error))

    def service_for(scope: Scope) -> Any:
        def resolve(
            credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER)],
        ) -> MacroService:
            account = authorizer.require(credentials.credentials if credentials else None, scope)
            return account_service(database, account)

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
        return record_known_food(service, food_id, data)

    @app.post(
        "/v1/foods/logs",
        response_model=LoggedFood,
        operation_id="macro_log_new_food",
        description=(
            "Log a clear label and known amount directly when asked. Submit structured nutrient "
            "values per serving and the amount consumed in servings. Ask for the missing detail "
            "when the food identity, serving size, nutrient values, or consumed amount is unclear. "
            "Use estimate provenance for an assistant estimate that the user asks to log."
        ),
    )
    def log_new_food(data: NewFoodInput, service: MacroService = write) -> dict[str, Any]:
        return record_new_food(service, data)

    @app.patch(
        "/v1/consumptions/{entry_id}",
        response_model=CorrectedConsumption,
        operation_id="macro_correct_consumption",
    )
    def correct(
        entry_id: EntryPath, data: CorrectionInput, service: MacroService = write
    ) -> dict[str, Any]:
        return apply_correction(service, entry_id, data)

    @app.post(
        "/v1/consumptions/{entry_id}/delete",
        response_model=ConsumptionDeletion,
        operation_id="macro_delete_consumption",
    )
    def delete(
        entry_id: EntryPath, data: DeletionInput, service: MacroService = write
    ) -> dict[str, Any]:
        return apply_deletion(service, entry_id, data)

    return app
