from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from macro_tracker.db import Database
from macro_tracker.hosted import HostedAuthorizer, create_hosted_app

MCP_HOST = "https://mcp.example.test"
MCP_ENDPOINT = f"{MCP_HOST}/macro-goblin"
AUTH_ENDPOINT = "http://accounts.example.test/api/internal/pilot/macro/authorize"
ISSUER = "https://issuer.example.test"
OWNER_IDS = {
    "tester-a": "b2818af4-9802-4fee-950b-050ba61b9a1a",
    "tester-b": "46429e84-01d1-45d1-bedf-07121d3d4c42",
}
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


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_hosted_mcp_writes_once_and_rechecks_current_access(tmp_path: Path) -> None:
    active = dict(OWNER_IDS)

    def authorize(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == AUTH_ENDPOINT
        assert request.headers["X-Creekmasons-Service-Key"] == "fixture-service-key"
        bearer = request.headers["Authorization"].removeprefix("Bearer ")
        if bearer == "wrong-audience":
            return httpx.Response(401)
        if bearer == "authority-offline":
            return httpx.Response(503)
        if bearer not in active:
            return httpx.Response(403)
        return httpx.Response(
            200,
            json={
                "account_id": active[bearer],
                "time_zone": "America/Los_Angeles",
                "scopes": ["nutrition:read", "nutrition:write"],
            },
        )

    database = Database(tmp_path / "macro.sqlite3")
    with httpx.Client(transport=httpx.MockTransport(authorize)) as upstream:
        authorizer = HostedAuthorizer(
            database, ISSUER, MCP_ENDPOINT, AUTH_ENDPOINT, "fixture-service-key", upstream
        )
        app = create_hosted_app(database, authorizer)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=MCP_HOST) as client,
        ):
            assert (await client.get("/healthz")).json() == {"status": "ready"}
            assert (await client.post("/macro-goblin")).status_code == 401
            metadata = await client.get("/.well-known/oauth-protected-resource/macro-goblin")
            assert metadata.json()["resource"] == MCP_ENDPOINT
            for token, status in (("wrong-audience", 401), ("authority-offline", 503)):
                denied = await client.post(
                    "/macro-goblin", headers={"Authorization": f"Bearer {token}"}
                )
                assert denied.status_code == status
            client.headers["Authorization"] = "Bearer tester-a"
            async with (
                streamable_http_client(MCP_ENDPOINT, http_client=client) as (incoming, outgoing, _),
                ClientSession(incoming, outgoing) as session,
            ):
                await session.initialize()
                logged = await session.call_tool("macro_log_new_food", FOOD)
                assert not logged.isError
                replay = await session.call_tool("macro_log_new_food", FOOD)
                assert replay.structuredContent == logged.structuredContent
                history = await session.call_tool("macro_read_history", {})
                assert len(history.structuredContent["entries"]) == 1

            client.headers["Authorization"] = "Bearer tester-b"
            async with (
                streamable_http_client(MCP_ENDPOINT, http_client=client) as (incoming, outgoing, _),
                ClientSession(incoming, outgoing) as session,
            ):
                await session.initialize()
                history = await session.call_tool("macro_read_history", {})
                assert history.structuredContent == {"entries": []}

            del active["tester-a"]
            denied = await client.post(
                "/macro-goblin", headers={"Authorization": "Bearer tester-a"}
            )
            assert denied.status_code == 403
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM consumption_entries").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM logging_receipts").fetchone()[0] == 1
