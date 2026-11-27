# Macro Goblin staging

This is a synthetic test of one OAuth client and two fresh accounts. The account
service can issue a product invite, confirm a time zone and verify a narrowly scoped
Clerk access token. Macro owns its data. This path keeps Sheets out of the process.

## Services

| Service | Source | Storage and public route |
| --- | --- | --- |
| `creekmasons-auth` | Creekmasons Auth pilot branch | Persistent `/data` for accounts, grants and connections. Own HTTPS domain for browser sign-in and `/account/pilot`. |
| `macro` | This repo root on the pilot branch | Separate persistent `/data`. Private Railway network only. |
| `ingress` | This repo's `deploy/ingress` root on the pilot branch | Public HTTPS, with product paths closed until enabled. |

Use one replica per volume-backed service. In the Macro service set
`MACRO_DATABASE_PATH=/data/macro.sqlite3`, `MACRO_RESOURCE_URL` to the exact
HTTPS ingress URL ending in `/macro-goblin`, and `CLERK_ISSUER` to the exact
Development issuer. Set `CREEKMASONS_PRIVATE_ORIGIN` to
`http://creekmasons-auth.railway.internal:8080` if the auth service listens on
8080. Set `CREEKMASONS_MACRO_SERVICE_KEY` to the same randomly generated value as
the staging auth service. The auth service's separate recipient hashing key
stays there. Use approved Development keys and an explicitly allowlisted
operator and OAuth client on the auth service.

Set `PORT=8080` on both private listeners when using the upstream examples.
Macro listens on `::` by default. Use an IPv6-capable private listener on the auth
service. Set ingress `MCP_HOST` to its exact public hostname and leave
`MACRO_ENABLED=0` while loading settings and checking readiness. Only enable
`MACRO_ENABLED=1` for an approved reachable synthetic trial. Keep the original
auth deployment and any other product routes untouched.

## OAuth and acceptance

Register the exact Macro resource and assign `nutrition:read` and
`nutrition:write` to the approved client in Clerk Development. Use opaque access
tokens for this staging slice. Verify that the chosen client and Clerk actually
produce an opaque token with the exact audience, client and scopes. A missing
audience or a refresh-style token is denied. Use no offline scope. The invite
must be redeemed before connecting. Finish revocation before issuing a fresh
invite for a new connection.

1. With synthetic users A and B, issue separate Macro invites for their verified
   primary addresses. Sign in, confirm each time zone, and redeem each invite.
2. Through the selected assistant, connect A, authorize Macro, log one synthetic
   label and quantity, and read it from history. Repeat the same write key and
   confirm there is one resulting entry and one receipt.
3. Connect B and verify A's entry is absent and any attempt to edit it returns
   the same public error as a missing entry. Check wrong audience and scopes.
4. Revoke A's invite as the operator. Verify subsequent reads, writes and retries
   fail while B remains authorized. Record only safe outcomes and release SHAs.

Run `uv run pytest -q --no-cov tests/test_hosted.py` for the local MCP test
with a simulated authentication service. A local pass is transport and record
isolation evidence. Real OAuth, restore and four-client acceptance remain staging
gates.

[Clerk OAuth verification](https://clerk.com/docs/guides/configure/auth-strategies/oauth/verify-oauth-tokens) ·
[Railway private networking](https://docs.railway.com/networking/private-networking) ·
[Macro pilot acceptance](https://github.com/rubotpaul-assistant/macro-goblin/issues/10)
