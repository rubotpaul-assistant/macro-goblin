---
name: preflight
description: "Run Goblin repository quality checks before a handoff, commit, or pull request, and diagnose failures in the native checks."
---

# Preflight

From the repository root run:

```sh
uv sync --locked --all-extras --dev
uv run pre-commit run --all-files
```

Review `.github/workflows/` for additional checks or different tool versions. Examine the first failing hook and resolve the underlying problem. If a formatter changes files, inspect its diff and rerun the gate. Confirm the final repository status and report the commands and any limitations.
