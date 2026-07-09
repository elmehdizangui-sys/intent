# intent-skill

## Version bump on every change (required)

This repo is distributed as a Claude Code plugin via a git-based marketplace
(`.claude-plugin/marketplace.json`). Installed copies are cached by teammates
keyed on the `version` field in `.claude-plugin/plugin.json` — if that field
doesn't change, `claude plugin update` has nothing new to detect, even after
the marketplace repo itself has newer commits. Teammates will silently stay
on the old version.

**Whenever you commit a change to anything under `skills/`, `hooks/`,
`scripts/`, or `mcp/` (i.e. anything that affects plugin behavior), bump the
`version` field in `.claude-plugin/plugin.json` in the same commit.**

- Docs-only changes (README, comments) do not require a bump.
- Use semver: patch (`0.9.0` -> `0.9.1`) for fixes/tweaks, minor (`0.9.0` ->
  `0.10.0`) for new features/behavior changes, major for breaking config or
  gate-behavior changes.
- Do not bump the version without also making a corresponding change — an
  empty version bump forces unnecessary re-installs across the team.

Teammates pick up a new version by running, then restarting Claude Code:

```bash
claude plugin marketplace update ezangui-intent
claude plugin update intent@ezangui-intent
```
