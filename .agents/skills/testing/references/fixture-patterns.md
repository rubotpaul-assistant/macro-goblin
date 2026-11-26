# Fixture patterns

- Create a fresh database under pytest's `tmp_path`. Supply the database path explicitly or use pytest's `monkeypatch` to set the documented CLI override for the test.
- For ownership, create two users with matching record names. Assert each account's full result set and cover a request using the other's canonical ID when the interface accepts IDs.
- For replay, assert response equality, exactly one durable effect, and no partial writes after an injected failure. Include a retry after the original object's state changes.
- For projections, capture rows using a fake Sheets writer. Inject failures or a canonical write during upload and inspect pending state against the exported revision.
- Use explicit offset-aware timestamps when dates or time zones affect assertions.
