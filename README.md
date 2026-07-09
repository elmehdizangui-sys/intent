# intent

A Claude Code plugin that links the *why* (your intent) to the *what* (the
diff git already tracks). One receipt JSON per task, readable at review time
alongside `git diff` — plus an optional, per-project approval gate that blocks
edits until a plan is confirmed.

**Status:** internal preview. Being tested on a small number of projects
before a public release.

## What it does

- **`/intent <request>`** — the agent states a plan, waits for you to say
  "go", makes the edits, then writes a receipt (`.intent/receipts/<id>.json`)
  recording the plan, the files touched, and (optionally) marked regions
  linking specific lines back to the receipt.
- **`/intent <query>`** — `verify`, `list`, `find`, `explain`, `ack` against
  existing receipts and markers.
- **Auto-intent mode** — a project can opt into running this workflow on
  every task without typing `/intent`, with a configurable gate:
  `none` (no approval needed), `lightweight` (one-line plan, no blocking), or
  `full` (plan + explicit "go", enforced at the tool layer — edits are denied
  until approved).

See [`skills/intent/SKILL.md`](skills/intent/SKILL.md) for the full agent-facing
spec and command reference.

## Zero impact by default

Everything here is **off unless a project opts in**. Installing or enabling
this plugin has no effect until that project commits its own
`.intent/config.json`:

```json
{ "auto_intent": true, "gate": "none", "auto_finalize": true, "model": "claude-opus-4-8" }
```

With no config file, `gate` defaults to `"none"` and `auto_intent` defaults to
`false` — no blocking, no forced receipts, nothing written. This is enforced
in code (`scripts/intent_config.py`), not just by convention, so enabling the
plugin in your global Claude Code settings is safe: only projects that add
their own `.intent/config.json` are affected.

## Installing (per project)

This repo is a self-contained Claude Code plugin **and** a single-plugin
marketplace (`.claude-plugin/marketplace.json`), so it can be installed
straight from a local clone or a private git remote — no access to any other
repo required.

```
/plugin marketplace add <path-or-git-url-to-this-repo>
/plugin install intent@ezangui-intent
```

To scope it to one project only, enable it in that project's own
`.claude/settings.json` (not your global `~/.claude/settings.json`) — e.g.:

```json
{
  "enabledPlugins": {
    "intent@ezangui-intent": true
  }
}
```

Then, still inside that project, opt into whichever gate mode you want to
test by adding `.intent/config.json` as shown above (or leave it out entirely
to just use the manual `/intent <request>` workflow with no auto-mode).

## Layout

```
intent-skill/
├── .claude-plugin/
│   ├── plugin.json        # plugin manifest
│   └── marketplace.json   # single-plugin marketplace manifest
├── .mcp.json               # registers the MCP server below
├── skills/intent/SKILL.md  # agent-facing instructions (the skill itself)
├── hooks/
│   ├── hooks.json           # SessionStart / UserPromptSubmit / PreToolUse / Stop
│   └── scripts/              # hook implementations (Python)
├── scripts/                 # CLI: intent_blocks.py, intent_config.py, intent_gate.py
├── mcp/server.py            # MCP stdio server wrapping the CLI as tools
└── tests/                   # unit + integration (pytest)
```

## Running the tests

```bash
python3 -m pytest tests/ -q
```

## Feedback

This is a pre-open-source internal test. Report friction, surprising
defaults, or missing docs directly — the goal is to find rough edges before
this goes public.
