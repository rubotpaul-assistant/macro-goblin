---
name: security
description: "Review defensive changes to Goblin authentication, tenant isolation, credentials, logs, and third-party data projections."
---

# Security and privacy review

- Derive the owner from verified authorization and enforce scope and ownership at every relevant read, write, replay, and export boundary. Check denied and revoked access as well as valid grants.
- Validate types and boundaries early, use parameterized SQL, and keep secrets in runtime credential storage. Use fixture credentials for tests.
- Inspect data flow to BI or Sheets projections, routine logs, audit records, exports, and deletion. Document which system holds each copy and how authorization and retention apply.
- Add focused regression tests using two fixture accounts, separate destinations where applicable, and a fake external provider. Run the existing lint, Bandit, and test gates.
