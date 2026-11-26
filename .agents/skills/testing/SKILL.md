---
name: testing
description: "Design or improve pytest coverage for Goblin domain behavior, CLI compatibility, SQLite migrations, adapters, or projections."
---

# Testing Goblin changes

- Use a minimal fixture with real temporary SQLite for persistence and state transitions. Use test doubles at external boundaries such as Sheets, authorization providers, clocks, and transport.
- Test the user-visible contract and canonical rows together. Include representative legacy data when a migration or schema change is involved.
- Reproduce a reported regression before changing code. For retries and competing writes, control ordering with synchronization primitives and assert durable outcomes. Avoid timing-based sleeps.
- Keep command and response compatibility checks at the normal CLI entry point. Check the same domain outcome through any new adapter.
- Run the focused test during development and the configured full suite for coverage before handing off the change.

See [fixture patterns](references/fixture-patterns.md) for examples.
