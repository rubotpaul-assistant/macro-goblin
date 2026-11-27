# Macro Goblin

Deterministic, item-level macronutrient tracking for OpenClaw:

`PHOTO OR DESCRIPTION → AGENT INTERPRETATION → CLI → SQLITE → GOOGLE SHEETS`

SQLite is canonical. Google Sheets is a derived visualization layer. The CLI performs all
validation, scaling, persistence, aggregation, and sync-state management. An agent may transcribe
a nutrition label or estimate a complex meal, but it must declare the provenance as `label`,
`estimate`, or `manual`; the application never silently invents nutrition data.

Macro Goblin also contains authorized HTTP and MCP adapters, replay-safe meal writes, label-first
logging, governed correction/deletion workflows, support export, and a recovery rehearsal. These
are private-pilot building blocks, not a hosted production service. See
[Launch readiness](docs/launch-readiness.md) for the current evidence, remaining gates, and launch
recommendation.

## Data model

- **Food item:** a reusable per-serving nutrient profile, such as one protein shake, hamburger, or
  Honeycrisp apple. Names are unique within a user's catalog.
- **User:** an owner of an isolated food catalog and consumption history. `geoff` is the default.
- **Consumption entry:** an immutable, user-owned item-level snapshot scaled by servings. Updating
  a reusable item never rewrites history.
- **Sync state:** records whether canonical SQLite changes still need projection.

All nutrient arithmetic uses `Decimal`; JSON and SQLite store normalized fixed-point strings.
HTTP consumption dates use the authorized account binding's IANA time zone. Local CLI operations
currently default to `America/Los_Angeles`.

## Development

```bash
uv sync --all-extras --dev
uv run pytest
uv run pre-commit run --all-files
```

## CLI

```bash
# Transcribed from a photographed label
macro item add "Core Power Elite Vanilla" \
  --serving "1 bottle" --calories 230 --protein-g 42 --carbs-g 9 --fat-g 3.5 \
  --source label --source-notes "Photographed label"

macro log item --item "Core Power Elite Vanilla" --servings 1

# An auditable LLM estimate for a complex meal
macro item add "Restaurant hamburger" \
  --serving "1 hamburger" --calories 700 --protein-g 40 --carbs-g 50 --fat-g 38 \
  --fiber-g 3 --source estimate \
  --source-notes "Estimated from described patty, bun, cheese, and condiments"

macro history recent --limit 20
macro summary day 2026-08-17
macro sheets status
macro sheets sync

# Add another user, then select that user for normal commands
macro user add freedom --display-name Freedom
MACRO_USER_SLUG=freedom macro item search "shake"
```

Commands emit stable JSON for agent use. Existing item definitions require explicit `--replace`
before future logs use revised values.

## Runtime configuration

- `MACRO_DATABASE_PATH` — defaults to `data/macro.sqlite3`
- `MACRO_USER_SLUG` — active user for catalog/history commands; defaults to `geoff`
- `MACRO_SPREADSHEET_ID` — destination spreadsheet
- `GYM_SPREADSHEET_ID` — fallback, allowing the linked Iron Goblin spreadsheet to be reused
- `GOOGLE_SERVICE_ACCOUNT_CREDENTIALS_JSON` — the same runtime-managed inline JSON or credential
  file reference used by Iron Goblin

Sync creates or replaces a dedicated **Macro Log** tab. It never reads or modifies Iron Goblin's
workout tab.
