---
name: error-handling
description: "Design or review Goblin CLI, HTTP, or MCP error handling, especially validation, denials, stale state, and retry conflicts."
---

# Error handling

- Keep domain failures explicit. Map them to stable machine-readable codes at new adapter boundaries and preserve existing CLI response compatibility.
- Give the caller a safe next action when possible. Distinguish invalid input, stale state, replay conflicts, and transient failures so callers can choose whether to fix input or retry the same command.
- Return the same public error for missing and inaccessible records where disclosure would cross an account boundary.
- Keep underlying exceptions available to controlled diagnostics. Record safe context such as operation, error class, and correlation ID. Exclude sensitive request bodies, credentials, and health or food notes.
- Test both the code and what the caller sees. Include invalid input, foreign IDs, and any rollback or replay behavior affected by the change.
