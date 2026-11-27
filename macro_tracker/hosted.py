"""Private Macro MCP process for the Creekmasons staging pilot."""

import os
import socket
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Error as SQLiteError
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import uvicorn
from pydantic import BaseModel, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from macro_tracker.access import (
    AccessDenied,
    AccessUnavailable,
    Account,
    AuthorizedConnection,
    Scope,
)
from macro_tracker.db import Database
from macro_tracker.mcp_server import create_mcp_app


class AccountDecision(BaseModel):
    account_id: UUID
    time_zone: str
    scopes: list[Scope]


@dataclass(frozen=True)
class HostedAuthorizer:
    database: Database
    issuer: str
    audience: str
    decision_url: str
    service_key: str
    client: httpx.Client

    def active_connection(self, bearer: str | None) -> AuthorizedConnection:
        if not bearer:
            raise AccessDenied("UNAUTHENTICATED", 401)
        try:
            response = self.client.post(
                self.decision_url,
                headers={
                    "Authorization": f"Bearer {bearer}",
                    "X-Creekmasons-Service-Key": self.service_key,
                },
            )
            if response.status_code in (401, 403):
                status = response.status_code
                code = "UNAUTHENTICATED" if status == 401 else "ACCESS_DENIED"
                raise AccessDenied(code, status)
            response.raise_for_status()
            decision = AccountDecision.model_validate_json(response.content)
            ZoneInfo(decision.time_zone)
            slug = f"pilot-{decision.account_id}"
            with self.database.connect() as connection:
                connection.execute(
                    """INSERT INTO users(slug,display_name,created_at)
                       VALUES(?, 'Pilot account', CURRENT_TIMESTAMP)
                       ON CONFLICT(slug) DO NOTHING""",
                    (slug,),
                )
                row = connection.execute(
                    "SELECT active FROM users WHERE slug=?", (slug,)
                ).fetchone()
        except (httpx.HTTPError, SQLiteError, ValidationError, ZoneInfoNotFoundError) as exc:
            raise AccessUnavailable() from exc
        if row is None or not row["active"]:
            raise AccessDenied("ACCESS_DENIED", 403)
        account = Account(str(decision.account_id), slug, decision.time_zone)
        return AuthorizedConnection(account, frozenset(decision.scopes))


def create_hosted_app(database: Database, authorizer: HostedAuthorizer) -> Starlette:
    database.initialize()
    app = create_mcp_app(database, authorizer)

    async def health(_request: Request) -> JSONResponse:
        try:
            with database.connect() as connection:
                connection.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        except SQLiteError:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    app.routes.append(Route("/healthz", health))
    return app


def main() -> None:
    os.umask(0o077)
    path = Path(os.environ["MACRO_DATABASE_PATH"])
    issuer = os.environ["CLERK_ISSUER"]
    audience = os.environ["MACRO_RESOURCE_URL"]
    origin = os.environ["CREEKMASONS_PRIVATE_ORIGIN"].rstrip("/")
    service_key = os.environ["CREEKMASONS_MACRO_SERVICE_KEY"]
    auth_host = urlsplit(issuer)
    resource = urlsplit(audience)
    upstream = urlsplit(origin)
    if (
        not path.is_absolute()
        or auth_host.scheme != "https"
        or not auth_host.hostname
        or auth_host.path
        or resource.scheme != "https"
        or not resource.hostname
        or resource.path != "/macro-goblin"
        or resource.query
        or resource.fragment
        or upstream.scheme not in ("https", "http")
        or not upstream.hostname
        or upstream.username is not None
        or upstream.path
        or upstream.query
        or upstream.fragment
        or len(service_key) < 32
    ):
        raise ValueError("Invalid private Macro staging configuration")

    database = Database(path)
    with httpx.Client(trust_env=False, timeout=5, follow_redirects=False) as client:
        authorizer = HostedAuthorizer(
            database,
            issuer,
            audience,
            origin + "/api/internal/pilot/macro/authorize",
            service_key,
            client,
        )
        app = create_hosted_app(database, authorizer)
        host = os.environ.get("MACRO_BIND_HOST", "::")
        port = int(os.environ.get("PORT", "8080"))
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            access_log=False,
            proxy_headers=False,
        )
        server = uvicorn.Server(config)
        if host != "::":
            server.run()
            return
        # asyncio's default IPv6 socket is IPv6-only. Railway health checks can
        # arrive over IPv4 while private services reach the listener over IPv6.
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            listener.bind((host, port))
            server.run(sockets=[listener])


if __name__ == "__main__":
    main()
