# Macro Goblin contributor instructions

## Development

Run commands from the repository root with Python 3.12 and uv.

```sh
uv sync --locked --all-extras --dev
uv run pytest
uv run pre-commit run --all-files
```

Use a focused pytest run to iterate and the full configured gate to verify a change. Keep fixes covered by a reproduction and test public behavior through its normal entry point. For migration work, include representative legacy fixtures and verify canonical records after reopen.

## Domain and data

- Keep the deterministic domain behavior in `macro_tracker/`. The `macro` CLI and any future adapters should share service validation, arithmetic, and persistence.
- Preserve user-scoped catalog and history access, fixed-point Decimal output, and the `label`, `estimate`, and `manual` provenance values. A profile replacement affects future logs and leaves historical snapshots intact.
- Use `MACRO_DATABASE_PATH` and an explicit fixture user for integration tests. Sheets is a derived projection. Use a fake writer in tests.
- Preserve documented OpenClaw-facing CLI inputs and JSON. Verify any separately managed live skill changes when they are in scope.

Keep credentials and user data out of fixtures, diffs, and routine logs. Run local work against fixture databases and fake external integrations. Production data changes, external deployment, and live integration changes require explicit authorization.

## GitHub workflow

Read the associated issue and check the current Git status before editing. Preserve unrelated work. Use a scoped diff, follow existing project names and style, and link the issue and verification results in the PR. Reference related work across the two Goblin repos where appropriate.

Repository-local Codex workflows live in `.agents/skills/`. See the [Adepthood import inventory](docs/adepthood-import.md) for provenance.
