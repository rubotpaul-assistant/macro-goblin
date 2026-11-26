# Adepthood workflow import

These repository-local Codex assets adapt the development practices at the pinned [Adepthood source](https://github.com/Geoffe-Ga/adepthood/tree/050657483ed161ae493caa65507d59c317537e20). The two Goblin repositories use the same skill names and the same root-level Python commands. Keep any project-specific behavior in the repository's `AGENTS.md`.

| Adepthood source | Goblin disposition |
| --- | --- |
| `AGENTS.md`, `CLAUDE.md`, `git-workflow`, `stay-green`, `max-quality-no-shortcuts` | Adapted into `AGENTS.md`, the PR template, and the native preflight workflow. |
| `tracer-code`, `testing`, `quick-test`, `preflight`, `ci-debugging`, `review-diff`, `error-handling`, `concurrency`, `architectural-decisions`, `backlog-grooming`, `security` | Ported as Codex skills with project-specific commands, examples, and guardrails. |
| `address-feedback`, `await-claude-review`, `comprehensive-pr-review`, `continue-epic`, `flare`, `scan-issue-writer`, `status`, `triage-and-plan` | Portable review and backlog practices are covered by `review-diff` and `backlog-grooming`. Claude review polling and repo-specific automation are deferred. |
| `bug-squashing-methodology`, `documentation`, `file-naming-conventions`, `user-facing-error-messages` | Relevant regression, documentation, naming, and message practices are covered by `testing`, `error-handling`, and `AGENTS.md`. |
| `cve-remediation`, `de-slopify`, `decide`, `frontend-aesthetics`, `mutation-testing`, `prompt-engineering`, `skill-craft`, `vibe`, `walkthrough` | Deferred until a Goblin task benefits from the separate workflow, asset, or added dependency. |
| `graph`, `.claude/agents/`, `.claude/commands/`, `.claude/settings.json`, `.claude/hooks/` | Adepthood-specific runtime setup, Graphify, and automatic worktree cleanup are deferred. |
| `scripts/dev-setup.sh`, `scripts/backend/`, `scripts/ci/`, CI and dependency configuration | Reuse Goblin's checked-in uv lockfile, pre-commit, and GitHub Actions. Adepthood's scripts depend on its app layout, additional runtimes, services, or automation. |

Codex discovers these skills from `.agents/skills/` when it opens a checkout containing the import. The repository instructions live in the root `AGENTS.md`. This keeps the workflows versioned with the code and gives each project its own copy.
