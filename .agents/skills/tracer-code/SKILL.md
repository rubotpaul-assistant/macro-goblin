---
name: tracer-code
description: "Plan or build a narrow end-to-end Goblin workflow across assistant tools, HTTP, the domain service, and SQLite. Use for new integration paths."
---

# Tracer development

1. Pick one complete user action and define its observable record, failure cases, and success condition.
2. Write the contract at each boundary and identify the existing service entry point. Use a disposable database and fake external integrations.
3. Wire a protocol test from the caller through the adapters to the service. Keep any test doubles inside the test harness and require a real canonical write before calling the tracer complete.
4. Add authorization, a lost-response retry, and a read that proves the intended record exists exactly once. Assert canonical IDs, state, and response equality where replay is supported.
5. Keep each change reviewable and run the repository's quality checks. Extend features after the original tracer still passes.

See [tracer examples](references/tracer-examples.md) for the current workflows.
