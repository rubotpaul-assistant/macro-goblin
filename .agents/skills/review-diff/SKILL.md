---
name: review-diff
description: "Review a Goblin branch, patch, or pull request for regressions, compatibility, and missing acceptance tests."
---

# Review a Goblin diff

1. Identify the comparison base, inspect the whole diff, and read the issue's acceptance criteria.
2. Follow changed behavior through the normal entry points and SQLite state. Check validation, authorization, retries, migrations, projection ordering, escaping, and error handling when the diff touches those boundaries.
3. Confirm tests pin the intended outcomes and include important failure paths. Run the checks that can materially affect the review.
4. Report actionable findings in severity order with file and line references, the affected behavior, and a minimal fix direction. Call out residual verification gaps.
