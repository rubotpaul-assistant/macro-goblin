# Macro account data support

Account-wide requests go to support. Verify the requester through their Creek
Masons sign-in and identify the matching Macro owner ID from the trusted
account binding. Use an explicit owner ID even if the local operator has a
default CLI user.

Run this rehearsal with an initialized disposable SQLite database and a
private directory. The destination file must be new.

```sh
MACRO_DATABASE_PATH=/path/to/fixture.sqlite3 uv run macro support export \
  --owner-id 2 --output /path/to/private/fixture-export.json
```

The command uses a read-only connection and one consistent database view.
It writes a private JSON file and prints only a success response. The file
contains Macro account metadata, food profiles, immutable consumption
snapshots, correction and deletion links, and replay metadata and responses.
Inspect the account ID and the expected data before delivery. Treat the
archive as private account data and release it only through the approved
support handoff to the verified requester.

The operator CLI owns this workflow. The pilot HTTP and MCP surfaces expose
individual meal correction and confirmed deletion. They have no export or
Sheets tools. Install the pilot transport extras without the Sheets extra
and keep projection credentials outside the pilot runtime.

Account-wide deletion requires the shared retention and recovery policy.
Reconcile prior deletions before enabling access after an approved hosted
restore. Track the remaining work in [Macro #2][support] and the
[shared recovery plan][recovery].

[support]: https://github.com/rubotpaul-assistant/macro-goblin/issues/2
[recovery]: https://github.com/rubotpaul-assistant/iron-goblin/issues/17
