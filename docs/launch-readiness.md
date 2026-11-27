# Launch readiness

**Audited:** 2026-11-27 at `361f835`

## Recommendation

Macro Goblin is ready for local development and controlled, operator-assisted API/MCP rehearsal.
It is **not ready for an external or paid pilot** and is not ready for a public launch.

The deterministic nutrition core and authorized transport adapters are well tested. The
repository does not yet provide the hosted identity, deployment, recovery, privacy, and operator
evidence needed to entrust it with external users' food records.

## Evidence on current main

- The repository gate passes: Ruff check and format, strict mypy, Bandit, and 84 tests.
- Test coverage is 95.48%, above the configured 90% floor.
- Catalog, history, summary and support access are owner-scoped.
- Authorized HTTP account bindings supply the IANA time zone used for consumption dates; local
  CLI operations default to `America/Los_Angeles`.
- Complete nutrition labels can be logged directly while partial labels are rejected.
- Meal writes are atomic and replay-safe; authorized corrections and confirmed deletions are
  exposed through the HTTP surface.
- The MCP adapter delegates to the authorized API/service path instead of duplicating domain
  behavior.
- Package/SQLite recovery and read-only support export have disposable-fixture rehearsals.

## Launch gates still open

1. **Hosted runtime:** package and deploy the approved private HTTP/MCP entry point with health
   checks, TLS, secret management, bounded logs, and no Sheets credentials in the pilot runtime.
2. **Production identity:** connect the adapters to the selected identity provider and verify
   issuer, audience, account ownership, product grants, revocation, and cross-account denial.
3. **Hosted persistence and recovery:** choose the pilot database, rehearse migration and restore
   in approved staging, and verify backup retention, expiry, and cutover behavior.
4. **Operator and privacy controls:** deploy rate limits, invitation/kill controls, authorized
   support roles, retention/deletion policy, incident rollback, and auditable access without meal
   contents in routine logs.
5. **Real connector rehearsal:** prove tool discovery, replay after response loss, correction,
   confirmed deletion, and revocation through each supported client from fresh accounts.
6. **Pilot evidence:** run a small consented cohort through onboarding, daily logging, support,
   export/deletion and recovery before accepting payment or widening access.

Recovery detail lives in [Macro pilot recovery rehearsal](pilot-recovery.md); operator export and
data-handling boundaries live in [Support account data](support-account-data.md).

## Release decision

Do not describe the current repository as production-ready or generally available. A private
external pilot becomes supportable only after gates 1 through 5 have evidence from the actual
hosted environment. Public launch requires successful pilot evidence and an explicit security,
privacy, reliability, and support review.
