"""Streamable HTTP MCP transport for authorized Macro operations."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from json import dumps
from sqlite3 import Error as SQLiteError
from typing import Any, cast
from urllib.parse import urlsplit

from fastapi.security.utils import get_authorization_scheme_param
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Receive, Send
from starlette.types import Scope as ASGIScope

from macro_tracker.access import AccessDenied, AccessUnavailable, AuthorizedConnection, Authorizer
from macro_tracker.access import Scope as NutritionScope
from macro_tracker.api import (
    DailyTotals,
    FoodSearch,
    History,
    KnownFoodInput,
    LocalDate,
    LoggedFood,
    NewFoodInput,
    account_service,
    domain_error_status,
    record_known_food,
    record_new_food,
)
from macro_tracker.db import Database
from macro_tracker.service import MacroError, MacroService


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(default="", max_length=200)


class HistoryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=100, strict=True)
    start_date: LocalDate | None = None
    end_date: LocalDate | None = None


class TotalsArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: LocalDate | None = None


class KnownFoodArguments(KnownFoodInput):
    food_id: int = Field(gt=0, le=2**63 - 1, strict=True)


@dataclass(frozen=True)
class Operation[InputT: BaseModel]:
    description: str
    scope: NutritionScope
    input_model: type[InputT]
    output_model: type[BaseModel]
    execute: Callable[[MacroService, InputT], dict[str, Any]]

    def describe(self, name: str) -> types.Tool:
        read_only = self.scope is NutritionScope.READ
        return types.Tool(
            name=name,
            description=self.description,
            inputSchema=self.input_model.model_json_schema(),
            outputSchema=self.output_model.model_json_schema(mode="serialization"),
            annotations=types.ToolAnnotations(
                readOnlyHint=read_only,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )

    def call(
        self,
        database: Database,
        connection: AuthorizedConnection,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        account = connection.require(self.scope)
        values = self.input_model.model_validate(arguments)
        service = account_service(database, account)
        output = self.execute(service, values)
        return self.output_model.model_validate(output).model_dump(mode="json")


OPERATIONS: dict[str, Operation[Any]] = {
    "macro_search_foods": Operation(
        "Find reusable foods and their current profile versions in the signed-in account.",
        NutritionScope.READ,
        SearchArguments,
        FoodSearch,
        lambda service, values: service.search_items(values.query, include_version=True),
    ),
    "macro_read_history": Operation(
        "Read recent food logs or filter by an inclusive range of effective local dates.",
        NutritionScope.READ,
        HistoryArguments,
        History,
        lambda service, values: service.recent_history(
            values.limit,
            include_event_context=True,
            start_date=values.start_date,
            end_date=values.end_date,
        ),
    ),
    "macro_daily_totals": Operation(
        "Read nutrient totals for a local date. Omit the date for the account's today.",
        NutritionScope.READ,
        TotalsArguments,
        DailyTotals,
        lambda service, values: service.daily_summary(
            values.day.isoformat() if values.day is not None else None
        ),
    ),
    "macro_log_known_food": Operation(
        "Log servings of a known food by ID. Reuse the client key only when retrying this log.",
        NutritionScope.WRITE,
        KnownFoodArguments,
        LoggedFood,
        lambda service, values: record_known_food(service, values.food_id, values),
    ),
    "macro_log_new_food": Operation(
        "Create a food from structured per-serving values and log the portion in one action. "
        "Send the same client key and values to retry.",
        NutritionScope.WRITE,
        NewFoodInput,
        LoggedFood,
        record_new_food,
    ),
}


def _tool_error(code: str) -> types.CallToolResult:
    body = {"error": {"code": code}}
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=dumps(body))],
        structuredContent=body,
        isError=True,
    )


@dataclass(frozen=True)
class _AuthorizedTransport:
    manager: StreamableHTTPSessionManager
    authorizer: Authorizer
    metadata_url: str

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive=receive)
        scheme, bearer = get_authorization_scheme_param(request.headers.get("Authorization"))
        try:
            connection = await run_in_threadpool(
                self.authorizer.active_connection, bearer if scheme.lower() == "bearer" else None
            )
        except AccessDenied as error:
            headers = (
                {"WWW-Authenticate": f'Bearer resource_metadata="{self.metadata_url}"'}
                if error.status == 401
                else None
            )
            response = JSONResponse(
                {"error": {"code": error.code}}, status_code=error.status, headers=headers
            )
            await response(scope, receive, send)
            return
        except AccessUnavailable:
            response = JSONResponse({"error": {"code": "UNAVAILABLE"}}, status_code=503)
            await response(scope, receive, send)
            return
        request.state.macro_connection = connection
        await self.manager.handle_request(scope, receive, send)


def create_mcp_app(
    database: Database, authorizer: Authorizer, *, allowed_origins: tuple[str, ...] = ()
) -> Starlette:
    """Build the stateless transport at the configured Macro resource URL."""
    resource = urlsplit(authorizer.audience)
    if resource.scheme not in ("http", "https") or not resource.netloc:
        raise ValueError("Macro MCP audience must be an HTTP resource URL")
    endpoint = resource.path or "/"
    origin = f"{resource.scheme}://{resource.netloc}"
    metadata_path = f"/.well-known/oauth-protected-resource{endpoint.rstrip('/')}"
    server: Server[None, Request] = Server("Macro Goblin")

    async def list_tools() -> list[types.Tool]:
        return [operation.describe(name) for name, operation in OPERATIONS.items()]

    async def call_tool(
        name: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | types.CallToolResult:
        operation = OPERATIONS.get(name)
        if operation is None:
            return _tool_error("UNKNOWN_TOOL")
        request = server.request_context.request
        if request is None:
            return _tool_error("UNAUTHENTICATED")
        connection = cast(AuthorizedConnection, request.state.macro_connection)
        try:
            return await run_in_threadpool(operation.call, database, connection, arguments)
        except AccessDenied as error:
            return _tool_error(error.code)
        except (AccessUnavailable, SQLiteError):
            return _tool_error("UNAVAILABLE")
        except MacroError as error:
            return _tool_error(domain_error_status(error)[1])
        except ValueError:
            return _tool_error("INVALID_INPUT")

    server.list_tools()(list_tools)  # type: ignore[no-untyped-call]
    server.call_tool(validate_input=False)(call_tool)

    manager = StreamableHTTPSessionManager(
        app=server,
        stateless=True,
        json_response=True,
        security_settings=TransportSecuritySettings(
            allowed_hosts=[resource.netloc], allowed_origins=[origin, *allowed_origins]
        ),
        max_request_body_size=64 * 1024,
    )
    transport = _AuthorizedTransport(manager, authorizer, f"{origin}{metadata_path}")

    async def metadata(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "resource": authorizer.audience,
                "authorization_servers": [authorizer.issuer],
                "scopes_supported": list(NutritionScope),
                "bearer_methods_supported": ["header"],
            }
        )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            yield

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route(endpoint, transport, methods=["GET", "POST", "DELETE"]),
            Route(metadata_path, metadata),
        ],
    )
