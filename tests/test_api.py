from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from typer.testing import CliRunner

from macro_tracker.access import AccessUnavailable, Account, Authorizer, Consent, Scope
from macro_tracker.access import VerifiedAccess as Access
from macro_tracker.api import create_app
from macro_tracker.cli import app as cli
from macro_tracker.db import Database
from macro_tracker.domain import MacroProfile, SourceType
from macro_tracker.mcp_server import create_mcp_app
from macro_tracker.service import MacroService

pytestmark = pytest.mark.anyio

ISSUER = "https://accounts.fixture.invalid"
AUDIENCE = "https://macro.fixture.invalid"
SCOPES = frozenset({Scope.READ, Scope.WRITE})
LOG_DAY = "2026-08-16"
FOOD = {
    "name": "Fixture crisp",
    "serving_description": "1 piece",
    "calories": "130",
    "protein_g": "4",
    "carbs_g": "12",
    "fat_g": "2",
    "fiber_g": "1",
    "source_type": "label",
    "servings": "1.25",
    "client_key": "fixture-log",
    "consumed_at": "2026-08-17T01:00:00Z",
}


class FixtureAccess:
    def __init__(self) -> None:
        self.tokens: dict[str, Access] = {}
        self.accounts: dict[tuple[str, str], Account] = {}
        self.grants: set[tuple[str, str]] = set()
        self.consents: dict[str, Consent] = {}
        self.online = True

    def verify(self, bearer: str) -> Access | None:
        if not self.online:
            raise AccessUnavailable("fixture-internal-provider-details")
        return self.tokens.get(bearer)

    def active_account(self, issuer: str, subject: str) -> Account | None:
        return self.accounts.get((issuer, subject))

    def has_grant(self, account_id: str, product: str) -> bool:
        return (account_id, product) in self.grants

    def active_consent(self, connection_id: str) -> Consent | None:
        return self.consents.get(connection_id)

    def connect(self, owner: str, client: str) -> str:
        key = f"{owner}-{client}"
        self.tokens[key] = Access(
            ISSUER,
            f"subject-{owner}",
            AUDIENCE,
            client,
            key,
            datetime.now(tz=UTC) + timedelta(minutes=5),
            SCOPES,
        )
        self.consents[key] = Consent(f"account-{owner}", "macro", client, SCOPES)
        return key


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def context(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, FixtureAccess, Database]]:
    database = Database(tmp_path / "fixture.sqlite3")
    service = MacroService(database)
    provider = FixtureAccess()
    for owner, zone in [("west", "America/Los_Angeles"), ("east", "Europe/London")]:
        service.add_user(owner, f"Fixture {owner}")
        account_id = f"account-{owner}"
        provider.accounts[ISSUER, f"subject-{owner}"] = Account(account_id, owner, zone)
        provider.grants.add((account_id, "macro"))
        provider.connect(owner, "first")
    app = create_app(database, Authorizer(provider, ISSUER, AUDIENCE))
    async with AsyncClient(transport=ASGITransport(app=app), base_url=AUDIENCE) as client:
        yield client, provider, database


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_http_round_trip_replays_across_clients_and_isolates_owners(
    context: tuple[AsyncClient, FixtureAccess, Database],
) -> None:
    client, provider, database = context
    west = bearer("west-first")
    west_other = bearer(provider.connect("west", "other"))
    east = bearer("east-first")
    logged = await client.post("/v1/foods/logs", headers=west, json=FOOD)
    assert logged.status_code == 200
    receipt = logged.json()
    assert receipt["totals"]["calories"] == "162.500"
    assert receipt["source_type"] == "label"
    assert receipt["local_date"] == LOG_DAY
    assert "user_slug" not in receipt
    assert "sync_pending" not in receipt
    assert (await client.post("/v1/foods/logs", headers=west_other, json=FOOD)).json() == receipt

    second = (
        await client.post(
            "/v1/foods/logs",
            headers=east,
            json={**FOOD, "calories": "60", "source_type": "estimate"},
        )
    ).json()
    assert second["entry_id"] != receipt["entry_id"]
    assert second["source_type"] == "estimate"
    assert second["local_date"] == "2026-08-17"
    assert (await client.get("/v1/foods?query=crisp", headers=west)).json()["items"][0][
        "id"
    ] == receipt["food_id"]
    assert (await client.get("/v1/foods?query=crisp", headers=east)).json()["items"][0][
        "id"
    ] == second["food_id"]
    request = {"client_key": "known", "servings": "1", "historical_date": "2026-08-12"}
    foreign = await client.post(f"/v1/foods/{receipt['food_id']}/logs", headers=east, json=request)
    missing = await client.post("/v1/foods/9999/logs", headers=east, json=request)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"error": {"code": "NOT_FOUND"}}
    known_path = f"/v1/foods/{receipt['food_id']}/logs"
    known = await client.post(known_path, headers=west, json=request)
    assert known.status_code == 200
    assert known.json()["occurred_at_utc"] is None
    assert (await client.post(known_path, headers=west_other, json=request)).json() == known.json()
    collision = await client.post(known_path, headers=west, json={**request, "servings": "2"})
    assert collision.status_code == 409
    assert collision.json() == {"error": {"code": "REPLAY_CONFLICT"}}
    duplicate = await client.post(
        "/v1/foods/logs", headers=west, json={**FOOD, "client_key": "other"}
    )
    assert duplicate.json() == {"error": {"code": "FOOD_ALREADY_EXISTS"}}

    history = await client.get(
        f"/v1/history?start_date={LOG_DAY}&end_date={LOG_DAY}", headers=west_other
    )
    assert history.status_code == 200
    assert [row["id"] for row in history.json()["entries"]] == [receipt["entry_id"]]
    assert (await client.get(f"/v1/totals?day={LOG_DAY}", headers=east)).json()["item_count"] == 0
    totals = (await client.get(f"/v1/totals?day={LOG_DAY}", headers=west)).json()["totals"]
    legacy = CliRunner().invoke(
        cli,
        ["summary", "day", LOG_DAY],
        env={"MACRO_DATABASE_PATH": str(database.path), "MACRO_USER_SLUG": "west"},
    )
    assert legacy.exit_code == 0
    assert f'"calories": "{totals["calories"]}"' in legacy.output
    assert len((await client.get("/v1/history", headers=west)).json()["entries"]) == 2

    provider.consents.pop("west-first")
    blocked = await client.post("/v1/foods/logs", headers=west, json=FOOD)
    assert blocked.status_code == 403
    assert blocked.json() == {"error": {"code": "ACCESS_DENIED"}}
    assert (await client.post("/v1/foods/logs", headers=west_other, json=FOOD)).json() == receipt


@pytest.mark.parametrize(
    ("change", "code", "status"),
    [
        ("missing-bearer", "UNAUTHENTICATED", 401),
        ("issuer", "UNAUTHENTICATED", 401),
        ("audience", "UNAUTHENTICATED", 401),
        ("expired", "UNAUTHENTICATED", 401),
        ("naive-expiry", "UNAUTHENTICATED", 401),
        ("unmapped", "ACCESS_DENIED", 403),
        ("iron-grant-only", "ACCESS_DENIED", 403),
        ("other-client", "ACCESS_DENIED", 403),
        ("iron-consent", "ACCESS_DENIED", 403),
        ("foreign-consent", "ACCESS_DENIED", 403),
        ("token-scope", "INSUFFICIENT_SCOPE", 403),
        ("consent-scope", "INSUFFICIENT_SCOPE", 403),
        ("unavailable", "UNAVAILABLE", 503),
        ("missing-local-owner", "UNAVAILABLE", 503),
    ],
)
async def test_http_denies_invalid_access(
    context: tuple[AsyncClient, FixtureAccess, Database], change: str, code: str, status: int
) -> None:
    client, provider, _ = context
    connection = "west-first"
    access = provider.tokens[connection]
    consent = provider.consents[connection]
    if change == "issuer":
        provider.tokens[connection] = replace(access, issuer="https://other.fixture.invalid")
    elif change == "audience":
        provider.tokens[connection] = replace(access, audience="https://iron.fixture.invalid")
    elif change == "expired":
        provider.tokens[connection] = replace(access, expires_at=datetime(2020, 1, 1, tzinfo=UTC))
    elif change == "naive-expiry":
        provider.tokens[connection] = replace(
            access, expires_at=access.expires_at.replace(tzinfo=None)
        )
    elif change == "unmapped":
        provider.tokens[connection] = replace(access, subject="unmapped-subject")
    elif change == "iron-grant-only":
        provider.grants.remove(("account-west", "macro"))
        provider.grants.add(("account-west", "iron"))
    elif change == "other-client":
        provider.tokens[connection] = replace(access, client_id="other")
    elif change == "iron-consent":
        provider.consents[connection] = replace(consent, product="iron")
    elif change == "foreign-consent":
        provider.consents[connection] = replace(consent, account_id="account-east")
    elif change == "token-scope":
        provider.tokens[connection] = replace(access, scopes=frozenset({Scope.WRITE}))
    elif change == "consent-scope":
        provider.consents[connection] = replace(consent, scopes=frozenset({Scope.WRITE}))
    elif change == "unavailable":
        provider.online = False
    elif change == "missing-local-owner":
        account = provider.accounts[ISSUER, access.subject]
        provider.accounts[ISSUER, access.subject] = replace(account, macro_user_slug="absent")
    response = await client.get(
        "/v1/history", headers={} if change == "missing-bearer" else bearer(connection)
    )
    assert response.status_code == status
    assert response.json() == {"error": {"code": code}}


@pytest.mark.parametrize(
    "invalid",
    [
        {"account_id": "account-east"},
        {"servings": 1.25},
        {"servings": "0"},
        {"calories": "700000"},
        {"calories": "100000", "servings": "2"},
        {"consumed_at": "2026-08-17T01:00:00"},
        {"consumed_at": "2026-08-17T01:00:00+99:00"},
        {"historical_date": "20260817"},
        {"source_type": "unknown"},
    ],
)
async def test_invalid_log_input_has_no_effect(
    context: tuple[AsyncClient, FixtureAccess, Database], invalid: dict[str, Any]
) -> None:
    client, _, _ = context
    headers = bearer("west-first")
    response = await client.post("/v1/foods/logs", headers=headers, json={**FOOD, **invalid})
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "INVALID_INPUT"}}
    assert (await client.get("/v1/foods", headers=headers)).json() == {"items": []}
    assert (await client.get("/v1/history", headers=headers)).json() == {"entries": []}


async def test_openapi_and_independent_scopes(
    context: tuple[AsyncClient, FixtureAccess, Database],
) -> None:
    client, provider, _ = context
    schema = (await client.get("/openapi.json")).json()
    assert set(schema["paths"]) == {
        "/v1/foods",
        "/v1/history",
        "/v1/totals",
        "/v1/foods/{food_id}/logs",
        "/v1/foods/logs",
        "/v1/consumptions/{entry_id}",
        "/v1/consumptions/{entry_id}/delete",
    }
    assert schema["paths"]["/v1/foods/logs"]["post"]["security"] == [{"CreekMasonsAccess": []}]
    assert schema["components"]["schemas"]["NewFoodInput"]["properties"]["servings"]["type"] == (
        "string"
    )
    assert schema["components"]["schemas"]["Food"]["properties"]["calories"]["type"] == "string"
    access = provider.tokens["west-first"]
    provider.tokens["west-first"] = replace(access, scopes=frozenset({Scope.READ}))
    assert (
        await client.post("/v1/foods/logs", headers=bearer("west-first"), json=FOOD)
    ).status_code == 403
    provider.tokens["west-first"] = replace(access, scopes=frozenset({Scope.WRITE}))
    assert (
        await client.post("/v1/foods/logs", headers=bearer("west-first"), json=FOOD)
    ).status_code == 200
    assert (await client.get("/v1/history", headers=bearer("west-first"))).status_code == 403


async def test_stale_profile_and_invalid_range_are_safe(
    context: tuple[AsyncClient, FixtureAccess, Database],
) -> None:
    client, _, database = context
    headers = bearer("west-first")
    original = (await client.post("/v1/foods/logs", headers=headers, json=FOOD)).json()
    owner = MacroService(database, "west")
    owner.add_item(
        FOOD["name"],
        "1 bowl",
        MacroProfile(Decimal("20"), Decimal("4"), Decimal("0"), Decimal("0")),
        SourceType.MANUAL,
        replace=True,
    )
    response = await client.post(
        f"/v1/foods/{original['food_id']}/logs",
        headers=headers,
        json={"client_key": "stale", "servings": "1", "expected_profile_version": 1},
    )
    assert response.status_code == 409
    assert response.json() == {"error": {"code": "STALE_FOOD_PROFILE"}}
    assert len((await client.get("/v1/history", headers=headers)).json()["entries"]) == 1
    invalid = await client.get(
        "/v1/history?start_date=2026-08-17&end_date=2026-08-16", headers=headers
    )
    assert invalid.status_code == 422
    assert invalid.json() == {"error": {"code": "INVALID_INPUT"}}


async def test_mcp_tools_replay_the_same_http_receipt(
    context: tuple[AsyncClient, FixtureAccess, Database],
) -> None:
    http, provider, database = context
    endpoint = f"{AUDIENCE}/mcp"
    mcp_provider = Authorizer(provider, ISSUER, endpoint)
    alternate_client = provider.connect("west", "mcp")
    provider.tokens[alternate_client] = replace(
        provider.tokens[alternate_client], audience=endpoint
    )
    app = create_mcp_app(database, mcp_provider)
    original = (await http.post("/v1/foods/logs", headers=bearer("west-first"), json=FOOD)).json()

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url=AUDIENCE) as client,
    ):
        unauthorized = await client.post("/mcp")
        assert unauthorized.status_code == 401
        metadata_path = "/.well-known/oauth-protected-resource/mcp"
        assert unauthorized.headers["WWW-Authenticate"] == (
            f'Bearer resource_metadata="{AUDIENCE}{metadata_path}"'
        )
        metadata = (await client.get(metadata_path)).json()
        assert metadata["resource"] == endpoint
        assert metadata["authorization_servers"] == [ISSUER]
        assert set(metadata["scopes_supported"]) == SCOPES
        wrong_audience = await client.post("/mcp", headers=bearer("west-first"))
        assert wrong_audience.status_code == 401
        client.headers.update(bearer(alternate_client))

        async with (
            streamable_http_client(endpoint, http_client=client) as (incoming, outgoing, _),
            ClientSession(incoming, outgoing) as session,
        ):
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "Macro Goblin"
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert set(tools) == {
                "macro_search_foods",
                "macro_read_history",
                "macro_daily_totals",
                "macro_log_known_food",
                "macro_log_new_food",
                "macro_correct_consumption",
                "macro_delete_consumption",
            }
            assert tools["macro_log_new_food"].inputSchema["properties"]["servings"]["type"] == (
                "string"
            )
            assert tools["macro_log_known_food"].annotations.idempotentHint is True
            assert tools["macro_read_history"].annotations.readOnlyHint is True
            assert tools["macro_delete_consumption"].annotations.destructiveHint is True
            replay = await session.call_tool("macro_log_new_food", FOOD)
            assert replay.isError is False
            assert replay.structuredContent == original
            search = await session.call_tool("macro_search_foods", {"query": "crisp"})
            assert search.structuredContent["items"][0]["id"] == original["food_id"]
            known = await session.call_tool(
                "macro_log_known_food",
                {
                    "food_id": original["food_id"],
                    "client_key": "known",
                    "servings": "0.5",
                    "historical_date": LOG_DAY,
                },
            )
            assert known.structuredContent["totals"]["calories"] == "65.000"
            history = await session.call_tool(
                "macro_read_history", {"start_date": LOG_DAY, "end_date": LOG_DAY}
            )
            assert len(history.structuredContent["entries"]) == 2
            summary = await session.call_tool("macro_daily_totals", {"day": LOG_DAY})
            assert summary.structuredContent["totals"]["calories"] == "227.500"
            invalid = await session.call_tool(
                "macro_log_new_food", {**FOOD, "name": "Fixture missing amount", "servings": None}
            )
            assert invalid.isError is True
            assert invalid.structuredContent == {"error": {"code": "INVALID_INPUT"}}
            provider.consents[alternate_client] = replace(
                provider.consents[alternate_client], scopes=frozenset({Scope.READ})
            )
            denied = await session.call_tool("macro_log_new_food", FOOD)
            assert denied.structuredContent == {"error": {"code": "INSUFFICIENT_SCOPE"}}

        provider.consents.pop(alternate_client)
        revoked = await client.post("/mcp")
        assert revoked.status_code == 403
        assert revoked.json() == {"error": {"code": "ACCESS_DENIED"}}


async def test_mcp_owner_scope_and_origin_check(
    context: tuple[AsyncClient, FixtureAccess, Database],
) -> None:
    http, provider, database = context
    endpoint = f"{AUDIENCE}/mcp"
    west = (await http.post("/v1/foods/logs", headers=bearer("west-first"), json=FOOD)).json()
    east = (
        await http.post(
            "/v1/foods/logs", headers=bearer("east-first"), json={**FOOD, "calories": "80"}
        )
    ).json()
    connection = provider.connect("east", "mcp")
    provider.tokens[connection] = replace(provider.tokens[connection], audience=endpoint)
    app = create_mcp_app(database, Authorizer(provider, ISSUER, endpoint))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app), base_url=AUDIENCE, headers=bearer(connection)
        ) as client,
    ):
        origin_denied = await client.post(
            "/mcp", headers={"Origin": "https://other.fixture.invalid"}, json={}
        )
        assert origin_denied.status_code == 403
        async with (
            streamable_http_client(endpoint, http_client=client) as (incoming, outgoing, _),
            ClientSession(incoming, outgoing) as session,
        ):
            await session.initialize()
            search = await session.call_tool("macro_search_foods", {"query": "crisp"})
            assert [item["id"] for item in search.structuredContent["items"]] == [east["food_id"]]
            foreign = await session.call_tool(
                "macro_log_known_food",
                {"food_id": west["food_id"], "client_key": "foreign", "servings": "1"},
            )
            assert foreign.structuredContent == {"error": {"code": "NOT_FOUND"}}
            assert foreign.isError is True


async def test_label_transcription_requires_complete_values_and_an_amount(
    context: tuple[AsyncClient, FixtureAccess, Database],
) -> None:
    client, _, database = context
    headers = bearer("west-first")
    # These values match tests/fixtures/fixture-crisp-label.jpg.
    for omitted in ("name", "serving_description", "fiber_g", "servings"):
        incomplete = {key: value for key, value in FOOD.items() if key != omitted}
        rejected = await client.post("/v1/foods/logs", headers=headers, json=incomplete)
        assert rejected.status_code == 422
        assert rejected.json() == {"error": {"code": "INVALID_INPUT"}}
    image_request = {**FOOD, "image": "fixture-crisp-label.jpg"}
    assert (
        await client.post("/v1/foods/logs", headers=headers, json=image_request)
    ).status_code == 422
    assert (await client.get("/v1/foods", headers=headers)).json() == {"items": []}
    assert (await client.get("/v1/history", headers=headers)).json() == {"entries": []}

    verified = await client.post("/v1/foods/logs", headers=headers, json=FOOD)
    assert verified.status_code == 200
    receipt = verified.json()
    assert receipt["source_type"] == "label"
    assert receipt["totals"] == {
        "calories": "162.500",
        "protein_g": "5.000",
        "carbs_g": "15.000",
        "fat_g": "2.500",
        "fiber_g": "1.250",
    }
    owner = MacroService(database, "west")
    owner.add_item(
        FOOD["name"],
        "1 bowl",
        MacroProfile(Decimal("20"), Decimal("4"), Decimal("0"), Decimal("0")),
        SourceType.ESTIMATE,
        replace=True,
    )
    assert (await client.post("/v1/foods/logs", headers=headers, json=FOOD)).json() == receipt
    entries = (await client.get("/v1/history", headers=headers)).json()["entries"]
    assert len(entries) == 1
    assert entries[0]["source_type"] == "label"
    assert entries[0]["calories"] == "162.500"

    estimate = {key: value for key, value in FOOD.items() if key != "fiber_g"}
    estimate.update(name="Estimate crisp", source_type="estimate", client_key="estimate")
    result = await client.post("/v1/foods/logs", headers=headers, json=estimate)
    assert result.status_code == 200
    assert result.json()["source_type"] == "estimate"


async def test_correct_and_delete_the_same_entry_across_clients(
    context: tuple[AsyncClient, FixtureAccess, Database],
) -> None:
    http, provider, database = context
    west = bearer("west-first")
    east = bearer("east-first")
    original = (await http.post("/v1/foods/logs", headers=west, json=FOOD)).json()
    edit_path = f"/v1/consumptions/{original['entry_id']}"
    edit = {"client_key": "edit", "historical_date": "2026-08-12", "servings": "2"}
    denied = await http.patch(edit_path, headers=east, json=edit)
    assert denied.status_code == 404
    assert denied.json() == {"error": {"code": "NOT_FOUND"}}
    corrected = await http.patch(edit_path, headers=west, json=edit)
    assert corrected.status_code == 200
    receipt = corrected.json()
    assert receipt["previous_entry_id"] == original["entry_id"]
    assert receipt["local_date"] == "2026-08-12"
    assert receipt["totals"]["calories"] == "260.000"
    stale = await http.patch(edit_path, headers=west, json={**edit, "client_key": "different"})
    assert stale.status_code == 409
    assert stale.json() == {"error": {"code": "STALE_CONSUMPTION"}}
    assert (await http.get(f"/v1/totals?day={LOG_DAY}", headers=west)).json()["item_count"] == 0
    assert (await http.get("/v1/totals?day=2026-08-12", headers=west)).json()["item_count"] == 1

    active_entry = receipt["entry_id"]
    delete_path = f"/v1/consumptions/{active_entry}/delete"
    foreign_delete = await http.post(
        delete_path, headers=east, json={"client_key": "delete", "confirmed": True}
    )
    assert foreign_delete.status_code == 404
    assert foreign_delete.json() == denied.json()
    unconfirmed_http = await http.post(delete_path, headers=west, json={"client_key": "delete"})
    assert unconfirmed_http.json() == {"error": {"code": "CONFIRMATION_REQUIRED"}}

    endpoint = f"{AUDIENCE}/mcp"
    connection = provider.connect("west", "mcp")
    provider.tokens[connection] = replace(provider.tokens[connection], audience=endpoint)
    app = create_mcp_app(database, Authorizer(provider, ISSUER, endpoint))
    delete = {"entry_id": active_entry, "client_key": "delete"}
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app), base_url=AUDIENCE, headers=bearer(connection)
        ) as client,
        streamable_http_client(endpoint, http_client=client) as (incoming, outgoing, _),
        ClientSession(incoming, outgoing) as session,
    ):
        await session.initialize()
        correction_replay = await session.call_tool(
            "macro_correct_consumption", {**edit, "entry_id": original["entry_id"]}
        )
        assert correction_replay.structuredContent == receipt
        unconfirmed = await session.call_tool("macro_delete_consumption", delete)
        assert unconfirmed.structuredContent == {"error": {"code": "CONFIRMATION_REQUIRED"}}
        deleted = await session.call_tool("macro_delete_consumption", {**delete, "confirmed": True})
        assert deleted.structuredContent == {"entry_id": active_entry, "deleted": True}
        history = await session.call_tool("macro_read_history")
        assert history.structuredContent == {"entries": []}

    retry = await http.post(
        delete_path, headers=west, json={"client_key": "delete", "confirmed": True}
    )
    assert retry.json() == {"entry_id": active_entry, "deleted": True}
    log_replay = await http.post("/v1/foods/logs", headers=west, json=FOOD)
    assert log_replay.status_code == 410
    assert log_replay.json() == {"error": {"code": "CONSUMPTION_DELETED"}}
    assert (await http.get("/v1/totals?day=2026-08-12", headers=west)).json()["item_count"] == 0
