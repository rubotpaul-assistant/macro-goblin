---
name: ci-debugging
description: "Diagnose a failed Goblin GitHub Actions run or a discrepancy between CI and local development checks."
---

# Debug a failed CI run

1. Identify the failing job, command, commit, and first actionable error. Compare the relevant workflow, lockfile, configuration, and diff against the last passing run.
2. Reproduce the CI command on the same revision and Python version. Use the repository's locked dependencies and inspect environment differences if reproduction diverges.
3. For a flaky test, control time, order, or external I/O and verify the specific failure mode. For a coverage failure, inspect the changed paths and add relevant coverage where appropriate.
4. Rerun the failing command and repository preflight. Report the cause and the verification result.
