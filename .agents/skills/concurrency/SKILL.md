---
name: concurrency
description: "Design or review SQLite transactions, concurrent Goblin writes, idempotency, and asynchronous projection work."
---

# Concurrent operations

1. State the invariant and identify which reads and writes must be atomic. Check SQLite isolation, the connection lifecycle, and uniqueness constraints before adding process-level synchronization.
2. Keep canonical mutations and their audit, replay, and projection-state changes in the owning transaction. Keep network requests outside the transaction.
3. Define how competing commands are serialized, retried, or rejected. A projection should acknowledge the revision it uploaded. Separately handle overlapping uploads so an older snapshot cannot overwrite a newer acknowledged one.
4. Prove the ordering with deterministic fixture tests for competing calls, failure before commit, and failure after commit.
