"""Authorization from the shared Creek Masons pilot account contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol


class Scope(StrEnum):
    READ = "nutrition:read"
    WRITE = "nutrition:write"


class AccessDenied(Exception):
    """A safe denial code suitable for clients."""

    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class AccessUnavailable(Exception):
    """The verifier or current access store cannot supply an authoritative answer."""


@dataclass(frozen=True)
class VerifiedAccess:
    issuer: str
    subject: str
    audience: str
    client_id: str
    connection_id: str
    expires_at: datetime
    scopes: frozenset[str]


@dataclass(frozen=True)
class Account:
    account_id: str
    macro_user_slug: str
    time_zone: str


@dataclass(frozen=True)
class Consent:
    account_id: str
    product: str
    client_id: str
    scopes: frozenset[str]


class AccessProvider(Protocol):
    """Trusted verifier and current access state supplied by the account integration."""

    def verify(self, bearer: str) -> VerifiedAccess | None:
        """Verify a resource access token and normalize its claims."""

    def active_account(self, issuer: str, subject: str) -> Account | None:
        """Resolve a verified issuer and subject to their active Macro owner binding."""

    def has_grant(self, account_id: str, product: str) -> bool:
        """Check the current product grant."""

    def active_consent(self, connection_id: str) -> Consent | None:
        """Look up the current connection and consent."""


@dataclass(frozen=True)
class AuthorizedConnection:
    account: Account
    scopes: frozenset[str]

    def require(self, scope: Scope) -> Account:
        if scope not in self.scopes:
            raise AccessDenied("INSUFFICIENT_SCOPE", 403)
        return self.account


class ConnectionAuthorizer(Protocol):
    @property
    def issuer(self) -> str: ...
    @property
    def audience(self) -> str: ...
    def active_connection(self, bearer: str | None) -> AuthorizedConnection: ...


@dataclass(frozen=True)
class Authorizer:
    provider: AccessProvider
    issuer: str
    audience: str

    def require(self, bearer: str | None, scope: Scope) -> Account:
        """Check current access before exposing any catalog data or replay receipt."""
        return self.active_connection(bearer).require(scope)

    def active_connection(self, bearer: str | None) -> AuthorizedConnection:
        """Resolve the current account and the intersection of token and consent scopes."""
        access = self.provider.verify(bearer) if bearer else None
        if (
            access is None
            or access.issuer != self.issuer
            or access.audience != self.audience
            or access.expires_at.utcoffset() is None
            or access.expires_at <= datetime.now(tz=UTC)
        ):
            raise AccessDenied("UNAUTHENTICATED", 401)
        account = self.provider.active_account(access.issuer, access.subject)
        consent = self.provider.active_consent(access.connection_id)
        if (
            account is None
            or not self.provider.has_grant(account.account_id, "macro")
            or consent is None
            or (consent.account_id, consent.product, consent.client_id)
            != (account.account_id, "macro", access.client_id)
        ):
            raise AccessDenied("ACCESS_DENIED", 403)
        return AuthorizedConnection(account, access.scopes & consent.scopes)
