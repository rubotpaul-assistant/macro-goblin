# Label logging fixture

Use the [fictitious Fixture Crisp label](../tests/fixtures/fixture-crisp-label.jpg)
with a disposable account. The label lists one piece per serving, 130 calories,
4 g protein, 12 g carbohydrate, 2 g fat, and 1 g fiber.

For the [real client acceptance run][clients], ask the assistant to log
1.25 pieces and attach the label. Expect it to log directly with `label`
provenance. Read history and verify one entry with 162.500 calories,
5.000 g protein, 15.000 g carbohydrate, 2.500 g fat, and 1.250 g fiber.
Retry the same structured call with its original client key and verify the
same entry ID and totals.

Send the label without an amount, then cover a nutrient value in another
attempt. In each case, the assistant should ask only for the missing detail
and history should contain no new entry. For a user-requested unlabeled
estimate, send the structured values with `estimate` provenance and verify
that history identifies it as an estimate.

The image goes to the assistant. Macro receives the name, serving description,
nutrient values, servings, and provenance as structured tool arguments.
Local contract tests simulate that transcription and use disposable SQLite.

[clients]: https://github.com/rubotpaul-assistant/macro-goblin/issues/10
