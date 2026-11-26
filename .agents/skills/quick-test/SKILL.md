---
name: quick-test
description: "Select and run focused tests for a Goblin diff or reproduce a single failure before running the full quality gate."
---

# Quick test

1. Inspect staged, unstaged, and relevant untracked paths. Map a changed CLI, domain, persistence, or projection module to the applicable files in `tests/`.
2. Run `uv run pytest -q -x --no-cov` with explicit test files or node IDs. Include affected callers when changing a shared service. Use the full `uv run pytest` command when scope is unclear.
3. Report the selection and outcome. The full suite enforces the repository's coverage threshold. Run it for final verification.
