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
  recording the original prompt, the plan, the files touched, and
  (optionally) marked regions linking specific lines back to that receipt.
- **`/intent <query>`** — `verify`, `list`, `find`, `explain`, `ack` against
  existing receipts and markers (see "Tracing a prompt to code" below).
- **Auto-intent mode** — a project can opt into running this workflow on
  every task without typing `/intent`, with a configurable gate:
  `none` (no approval needed), `lightweight` (one-line plan, no blocking), or
  `full` (plan + explicit "go", enforced at the tool layer — edits are denied
  until approved).

See [`skills/intent/SKILL.md`](skills/intent/SKILL.md) for the full agent-facing
spec and command reference (that file is what the agent itself reads —
this README is the human-facing install/ops guide).

## Zero impact by default

Everything here is **off unless a project opts in**. Installing or enabling
this plugin has no effect until that project commits its own
`.intent/config.json`. With no config file, `gate` defaults to `"none"` and
`auto_intent` defaults to `false` — no blocking, no forced receipts, nothing
written. This is enforced in code (`scripts/intent_config.py`), not just by
convention, so enabling the plugin in your global Claude Code settings is
safe: only projects that add their own `.intent/config.json` are affected.

---

## Installing per project

Prerequisites: Claude Code CLI with the `claude plugin` subcommand (recent
versions), Python 3 on `PATH`, and a local clone (or reachable git remote) of
this repo. This repo is itself a self-contained Claude Code plugin **and** a
single-plugin marketplace (`.claude-plugin/marketplace.json`) — no access to
any other repo is required.

### 1. Register this repo as a plugin source (one-time, per machine)

```bash
claude plugin marketplace add /path/to/intent-skill      # local clone
# or
claude plugin marketplace add https://github.com/your-org/intent-skill.git
```

This writes to *your own* `~/.claude/settings.json` (`extraKnownMarketplaces`).
It only registers where the plugin can be found — it does not enable it
anywhere yet, so this step alone has zero effect on any project.

### 2. Install it, scoped to one project

Run this **from inside the target project's directory** (e.g.
`service-promotion`):

```bash
cd /path/to/your-project
claude plugin install intent@ezangui-intent --scope project
```

`--scope project` is what matters here: it writes `enabledPlugins` into
`your-project/.claude/settings.json` — a file that belongs to that project,
not your global config. Other repos you work on are completely unaffected.
(`--scope local` writes to `.claude/settings.local.json` instead, if you'd
rather not commit the enablement to the repo yet.)

### 3. Verify it loaded

```bash
claude plugin details intent@ezangui-intent
```

Expected output: 1 skill (`intent`), 4 hooks (`UserPromptSubmit`,
`PreToolUse`, `SessionStart`, `Stop`), 1 MCP server. At this point the plugin
is loaded but still inert — no `.intent/config.json` exists yet, so
`gate=none` and `auto_intent=false`.

### 4. Opt in to a gate mode (optional — this is what actually turns it on)

Manual usage (`/intent <request>` typed explicitly) works with **no config
file at all**. To also test **auto-intent mode** — where the workflow runs on
every task without typing `/intent` — commit `.intent/config.json` at the
project root:

```json
{ "auto_intent": true, "gate": "lightweight", "auto_finalize": true, "model": "claude-opus-4-8" }
```

See "Configuration reference" below for what each field does and which
combination to pick for your test.

### Uninstalling / rolling back

```bash
claude plugin disable intent@ezangui-intent          # keep installed, turn off
claude plugin uninstall intent@ezangui-intent         # remove entirely
```

Either way, `.intent/config.json` (if any) and `.intent/receipts/` are left
untouched on disk — they're just data, not tied to the plugin being enabled.

---

## Configuration reference

Config lives at `.intent/config.json`, committed with the project it applies
to. Loading is defensive: a missing or malformed file is treated as "all
off", never raises, and never blocks a session.

```json
{
  "auto_intent":   true,
  "gate":          "lightweight",
  "auto_finalize": true,
  "model":         "claude-opus-4-8"
}
```

| Field | Type | Default | Meaning |
|---|---|---|---|
| `auto_intent` | bool | `false` | If `true`, a `SessionStart` hook injects a directive telling the agent to run the intent workflow on every task automatically, without the dev typing `/intent`. |
| `gate` | `"none"` \| `"lightweight"` \| `"full"` | `"none"` | How much approval friction auto-intent mode adds (see table below). Only matters when `auto_intent` is `true` — but see "Manual mode always available" below. |
| `auto_finalize` | bool | `false` | Backstop: a `Stop` hook writes a `--git-diff` receipt automatically at session end if the working tree changed but the agent never called `finalize` itself. |
| `model` | string | `""` | Model id recorded on auto-written receipts (informational, doesn't change behavior). |

### Gate modes, in practice

| Gate | What the agent does | What's enforced |
|---|---|---|
| `none` | Proceeds directly, no approval step. | Nothing — advisory only. |
| `lightweight` | States a one-line plan, then proceeds in the same turn unless you object. | Nothing — advisory only, never blocks. |
| `full` | Presents a Reformulation + numbered Plan and must wait for you to reply "go" before touching any file. | **Enforced at the tool layer.** A `PreToolUse` hook denies (`exit 2`) any `Edit`/`Write`/`MultiEdit` call while the gate is closed for that session. This is the only mode that can actually stop an edit — the others are text-only guidance the agent could in theory ignore. |

`full` gate mechanics, if you're testing it: a `UserPromptSubmit` hook opens
the gate for the session the moment you send a bare approval (`go`,
`go ahead`, `lgtm`, `proceed`, …), and closes it again on your next
substantive prompt (re-arming for the next task). On approval, it also mines
the transcript for the plan text the agent posted right before your "go" and
appends it to `.intent/plans/<session_id>.md` — a plain-text, per-session,
committable log of every approved plan, readable even before `finalize` runs.

### Recommended starting configs

```json
// Safest way to try auto-mode: never blocks, just gets receipts written.
{ "auto_intent": true, "gate": "none", "auto_finalize": true }
```

```json
// Middle ground: visible plan, no blocking, still a backstop receipt.
{ "auto_intent": true, "gate": "lightweight", "auto_finalize": true }
```

```json
// Full enforcement: nothing gets edited without an explicit "go".
{ "auto_intent": true, "gate": "full", "auto_finalize": true, "model": "claude-opus-4-8" }
```

### Manual mode always available

Regardless of `.intent/config.json`, `/intent <request>` and `/intent <query>`
(`verify`/`list`/`find`/`explain`/`ack`) always work when typed explicitly —
auto-intent config only controls whether the workflow runs *without* being
asked.

---

## How it works

Four hooks, one CLI, one JSON file format:

1. **`SessionStart`** (`intent-session-check.py`) — if `.intent/` exists in
   the project, injects recent receipt history plus the auto-intent
   directive (if `auto_intent` is on) into the agent's context. No-op, zero
   cost, on any project without `.intent/`.
2. **`UserPromptSubmit`** (`intent-gate-prompt.py`) — only relevant to
   `gate=full`: detects bare approvals and opens/closes the per-session gate
   token, and captures the approved plan text (see above).
3. **`PreToolUse`** on `Edit|Write|MultiEdit` (`intent-gate-check.py`) — the
   actual enforcement point for `gate=full`. Fails **open** (allows the edit)
   on any internal error or when gate isn't `full`, so a broken config or an
   unrelated project can never get wedged.
4. **`Stop`** (`intent-auto-finalize.py`) — if `auto_finalize` is on and the
   working tree changed since the last receipt, writes a `--git-diff` receipt
   automatically as a deterministic backstop.

The CLI (`scripts/intent_blocks.py`) is the single source of truth all four
hooks and the `/intent` skill delegate to — it's also exposed as MCP tools
(`mcp/server.py`) so IDEs/other agents can call `intent_find`, `intent_verify`,
`intent_explain`, etc. directly instead of shelling out.

---

## Tracing a prompt to code

This is the core traceability feature: every receipt records the **original
prompt and plan** alongside the exact code regions it produced, so a
reviewer (or you, six months later) can go from a line of code straight back
to the request that justified it — without digging through chat history.

### The receipt

`finalize` writes one JSON file per task to `.intent/receipts/<id>.json`:

```json
{
  "id": "a3f2c1d9",
  "spec_version": "1.0",
  "created_at": "2026-07-09T14:02:11Z",
  "model": "claude-opus-4-8",
  "intent": {
    "original_prompt": "add request logging to the promotion endpoint",
    "plan": "1. Add a logging interceptor in ServiceConfig.kt\n2. Wire it into the promotion route in PromotionController.kt"
  },
  "regions": [
    { "role": "primary", "file": "src/ServiceConfig.kt", "local_intent": "add logging interceptor", "anchor_content_hash": "sha256:..." },
    { "role": "for",     "file": "src/PromotionController.kt", "local_intent": "wire logging into route", "anchor_content_hash": "sha256:..." }
  ],
  "validation": { "hash_validated": true, "auto_hash": true, "validation_mode": "marker" }
}
```

`intent.original_prompt` and `intent.plan` are the trace back to *why*;
`regions` is the trace forward to *where*. Each region's hash is checked
against the live file, so drift (someone edited the code after the fact
without updating the receipt) is detectable, not silent.

### Walking the trace, from either direction

**From a request → to the code**, just read the receipt (`explain`, below,
or open the JSON directly).

**From a line of code → back to the request that produced it:**

```bash
python3 scripts/intent_blocks.py explain src/ServiceConfig.kt:43
```

Finds the nearest `@intent:`/`@for:` marker at or above line 43, looks up the
receipt that marker belongs to, and prints the full receipt — prompt, plan,
model, every other region it touched.

**Other traceability commands:**

| Command | Use it to... |
|---|---|
| `find [path]` | List every `@intent:`/`@for:` marker in the tree — a map of every AI-touched region. |
| `list <file>` | Show markers in one file with the receipt id each one links to. |
| `verify [path] [--strict]` | Re-hash every marked region and report drift (code changed since the receipt was written). `--strict` exits non-zero — wire it into CI to catch silently-drifted receipts. |
| `ack <id> --region <N> --reason "<why>"` | When drift is *expected* (a legit follow-up edit), record why and update the stored hash instead of leaving it flagged forever. |

For single-file or simple changes, receipts can also be written in
**git-diff mode** (`finalize --git-diff`, no markers needed) — the trace
still works (`original_prompt`/`plan` are still recorded and diff regions are
still hashed), it's just slightly coarser than marker-anchored regions since
line ranges come from `git diff` rather than an explicit marker.

**In review:** commit the receipt alongside the code change and reference it
in the PR description, or just point reviewers at
`.intent/receipts/<id>.json` — it reads like a structured commit message that
can't drift silently from the diff it describes.

---

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
