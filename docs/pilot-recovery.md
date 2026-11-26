# Macro pilot recovery rehearsal

Macro's current canonical store is SQLite. This rehearsal uses an installed package
and disposable databases. It supports the [Macro pilot gate][pilot] and the
[shared recovery work][recovery].

## Rehearse the base package

Run from the repository root with Python 3.12 and uv. The temporary environment
installs the base wheel and its runtime dependencies using the lockfile.

```sh
rehearsal_dir=$(mktemp -d)
UV_PROJECT_ENVIRONMENT="$rehearsal_dir/venv" uv sync --locked --no-dev --no-editable
"$rehearsal_dir/venv/bin/python" -c 'from importlib.util import find_spec; assert find_spec("googleapiclient") is None'
env -i PATH="$rehearsal_dir/venv/bin:/usr/bin:/bin" \
    MACRO_DATABASE_PATH="$rehearsal_dir/fixture.sqlite3" macro user list
```

Expect one seeded operator in the disposable database. The hosted adapter must
use the canonical service and omit Sheets tools and credentials. The operator CLI
still includes the optional Sheets commands.

## Rehearse backup and restore

Run `uv run pytest --no-cov tests/test_recovery.py`. The fixture logs synthetic
food for two owners, takes a consistent SQLite backup using the online backup
API, makes a later write, and restores to a separate database. It checks SQLite
integrity and foreign keys, account isolation, original replay responses, daily
totals, the snapshot provenance, and the projection revision. The restored file
reflects the backup point and the original continues to contain the later write.

For a hosted restore, rehearse on approved staging using the selected storage
backend. Confirm the backup age and restore time, and apply the account-deletion
reconciliation from [Macro #2][account-data] before allowing traffic. The
fixture exercise uses fresh accounts and leaves hosted storage, backup retention,
and cutover verification to the pilot's release checks.

[pilot]: https://github.com/rubotpaul-assistant/macro-goblin/issues/5
[recovery]: https://github.com/rubotpaul-assistant/iron-goblin/issues/17
[account-data]: https://github.com/rubotpaul-assistant/macro-goblin/issues/2
