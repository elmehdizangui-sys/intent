---
name: intent
description: |
  Intent annotation tool — two modes:
  1. AUTHORING (`/intent <request>`): plan → wait for go → edit → mark → finalize.
     Only hard gate: `finalize` exits 1 if --plan or --regions is empty.
  2. QUERY (`/intent <cmd>`): verify, list, find, explain, ack.
  Trigger on: `/intent`, "explain line", "list markers",
  "verify receipt", "find intents", "ack drift".
---

# Intent — v1.0

**Purpose:** link the *why* (your intent) to the *what* (the diff git already tracks).
One receipt JSON per task — readable at review time alongside `git diff`.

---

## Mode A — `/intent <cmd>` (query)

Resolve `SKILL_SCRIPTS_DIR` (see bottom), then run:

```bash
python3 "$SKILL_SCRIPTS_DIR/intent_blocks.py" <cmd>
```

| Command | What it does |
|---|---|
| `find [path]` | List all @intent:/@for: markers in the tree |
| `verify [path] [--strict]` | Re-hash regions, report drift; `--strict` exits non-zero (CI) |
| `list <file>` | Show markers in a file with linked receipt ids |
| `explain <file>:<line>` | Show the full receipt for the region at that line |
| `ack <id> --region <N> --reason "<why>"` | Acknowledge drift and update hash |

---

## Mode B — `/intent <request>` (authoring)

Three steps in order. Do not skip or reorder.

### Step 1 — Plan

Read files needed to understand the request (max 20). Then write in chat:

- **Plan name**: a short kebab-case slug (3-6 words) that summarizes the change, e.g. `add-find-quest-method`. This becomes the filename under `.intent/plans/` — pick something a developer could recognize months later in a directory listing, not a paraphrase of the request sentence.
- **Reformulation**: one sentence — the dev's intent in clear English, same scope, never expanded.
- **Plan**: numbered list of every file and change needed.

Say:

> "Reply **go** to proceed with this plan name, **go as \<name>** to use a different one, or tell me what to change."

**Do not edit any file until the dev replies "go".**

### Step 2 — Execute & Mark

Make all code edits.

**Choose a marking mode:**

**Option A: With Markers** (for multi-region or important changes)
- Insert one marker on the line **immediately above** the first changed line in each region
- No blank line between marker and code
- **Never mark import lines** (script will reject these)
- Never mark trivial edits (≤ 3 lines, pure rename or whitespace-only)

**Primary** (`role = "primary"`) — the file where the principal edit happened. Exactly one per receipt.
**Secondary** (`role = "for"`) — every other file meaningfully edited.

| Language | Prefix |
|---|---|
| Kotlin / Java / Swift / TS / JS / Go / Rust / C / C++ | `//` |
| Python / Ruby / Shell / YAML | `#` |
| SQL / Lua / Haskell | `--` |

```kotlin
// @intent: <why this region exists>
<the edited code>
```

```kotlin
// @for: <why this collateral region was also touched>
<the collateral code>
```

**Option B: Marker-less** (for simple single-file changes)
- Skip markers entirely
- Use `--git-diff` mode in Step 3

### Step 3 — Finalize

**Option A: With Markers (auto-hash mode)**

The script validates markers, computes hashes automatically:

```bash
python3 "$SKILL_SCRIPTS_DIR/intent_blocks.py" finalize \
  --plan "<your numbered plan>" \
  --regions '[
    {"role": "primary", "file": "src/Foo.kt", "marker_line_hint": 42, "local_intent": "add logging"},
    {"role": "for", "file": "src/Bar.kt", "marker_line_hint": 10, "local_intent": "update caller"}
  ]' \
  --auto-hash \
  --model "claude-sonnet-4-6"
```

**What the script does:**
- Reads each file
- Finds marker matching `local_intent` near `marker_line_hint`
- Extracts 1-3 lines after marker
- Computes hash automatically
- **Rejects** if:
  - Marker not found
  - Marker placed on import line
  - Hash mismatch (if you provided one)

**Option B: Marker-less (git-diff mode)**

The script detects changes automatically:

```bash
python3 "$SKILL_SCRIPTS_DIR/intent_blocks.py" finalize \
  --plan "<your numbered plan>" \
  --git-diff \
  --model "claude-sonnet-4-6"
```

**What the script does:**
- Runs `git diff HEAD` to detect changes
- Parses changed regions and line numbers
- Computes hashes for each region
- No markers required

**When to use each mode:**

| Use Markers (Option A) | Use Git-Diff (Option B) |
|---|---|
| Multi-region task with several logical edits | Single-file or small change |
| Need to record *why* each region exists | Intent already clear from plan |
| Changes span multiple files | Want zero source pollution |
| Important changes requiring explicit intent text | Quick refactors or simple fixes |

**Validation output:**

If successful, the script prints:

```
Receipt a3f2c1d9 written → .intent/receipts/a3f2c1d9.json

VALIDATION COMPLETED:
  ✓ 2 region(s) validated
  ✓ 2 hash(es) computed and verified
  ✓ 2 import-line check(s) passed

  @intent: src/Foo.kt:43
  @for:    src/Bar.kt:11

Next steps:
  1. Run: python3 scripts/intent_blocks.py verify
  2. Commit receipt alongside code changes
  3. Include receipt in PR for reviewer context
```

If validation fails, the script exits 1 with clear error messages.

---

## What NOT to do

- Never edit files before the dev says "go"
- Never call `finalize` with an empty `--plan`
- Never mark import lines (script will reject)
- Never insert a blank line between a marker and its code
- Never auto-trigger this skill **unless auto-intent is enabled** (see below); otherwise wait for an explicit `/intent` call

---

## Choosing a mode: decision tree

```
Start: /intent <request>
  ↓
1. Plan + wait for "go"
  ↓
2. Make all edits
  ↓
3. How many regions? How important?
  ↓
  ├─ Single file, simple change
  │  → finalize --git-diff
  │
  ├─ Multiple regions, need intent text
  │  → Insert markers
  │  → finalize --regions '...' --auto-hash
  │
  └─ Hybrid: some marked, some not
     → Insert markers for key regions
     → finalize --regions '...' --auto-hash
     (Script will still compute hashes, you don't)
```

---

## Auto-intent mode (seamless, configurable)

A repo can opt into capturing intent **without** typing `/intent` every time.
Behavior is driven by `.intent/config.json` (committed with the repo):

```json
{ "auto_intent": true, "gate": "none", "auto_finalize": true, "model": "claude-opus-4-8" }
```

| Field | Meaning |
|---|---|
| `auto_intent` | Inject an auto-mode directive at SessionStart so the agent runs the intent workflow on its own |
| `gate` | `none` (proceed, no approval) · `lightweight` (one-line plan, proceed unless objected) · `full` (Reformulation + Plan, wait for "go" — **enforced**, see below) |
| `auto_finalize` | A `Stop` hook writes a `--git-diff` receipt as a deterministic backstop if the agent forgets |
| `model` | Model id recorded on auto-written receipts |

**No config file → auto mode is OFF and `gate=none`** — installing or enabling this plugin has no effect on a project until that project opts in with its own `.intent/config.json`. Enforcement is strictly per-project.

How it works (hybrid):
1. **SessionStart hook** reads the config and injects the directive for the chosen gate. The agent then plans + marks + finalizes naturally — richest receipts (real plan text, marked regions).
2. **Stop hook** is the guarantee: on session end, if the working tree changed since the last receipt and `auto_finalize` is on, it auto-writes a git-diff receipt — even if the agent never called `finalize`.

The `full` gate is **enforced**, not advisory. SessionStart can only inject text, which the agent can ignore for short requests. So the gate also runs at the tool layer:
- **`UserPromptSubmit` hook** (`intent-gate-prompt.py`) opens the gate when the user sends a bare approval (`go`, `go ahead`, `lgtm`, `proceed`, …) **and** the assistant's last turn actually looks like a plan (Reformulation + numbered steps, not just chat or a post-hoc summary) — a "go" with nothing plan-shaped behind it leaves the gate untouched, so `PreToolUse` keeps blocking. Any other prompt clears the gate (a new task re-arms it). The gate is a per-session token at `.intent/.gate`. On a real approval it also mines the transcript for the plan text the agent posted just before the "go" and appends it as plain text to `.intent/plans/<seq>-<slug>.md` — a hidden, committable, per-session log readable at review time even before `finalize` runs. `<seq>` is a zero-padded counter so files list in creation order; `<slug>` comes from the agent's proposed **`Plan name:`** line (falling back to `Reformulation:`, then the first non-empty line, if the agent didn't propose one), so the filename says what the plan is about at a glance instead of paraphrasing whatever chatty preamble preceded the plan. The dev can override the proposed name at approval time — `go as fetch-quest-by-id` — and that name is used verbatim instead. The session id itself is recorded inside the file, not the name. Every session gets its own file (found again via that embedded session id), so old sessions' plans stay in the repo permanently instead of being overwritten by the next session; multiple approvals within the same session append to that session's file. Re-arming the gate for a new task only clears `.intent/.gate` — plan files are never touched by that.
- **`PreToolUse` hook** on `Edit|Write|MultiEdit|Bash` (`intent-gate-check.py`) **denies the edit (exit 2)** while the gate is closed — including a Bash command that looks like a file write (`sed -i`, `>` redirection, `tee`, `cp`/`mv`, a heredoc calling `write_text`), so the shell is not a way around the gate; read-only Bash is always allowed, instructing the agent to present a plan and wait for "go". It fails open on `gate != "full"` or any internal error, so other modes and broken state never wedge a session. Receipts are written via the CLI (not `Edit`/`Write`), so `finalize` is never blocked.

Configure it:

```bash
python3 "$SKILL_SCRIPTS_DIR/intent_blocks.py" config set \
  --auto-intent true --gate none --auto-finalize true --model claude-opus-4-8
python3 "$SKILL_SCRIPTS_DIR/intent_blocks.py" config show
```

## SKILL_SCRIPTS_DIR

Resolve relative to the plugin install, falling back to a legacy global copy:

```bash
SKILL_SCRIPTS_DIR="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/skills/intent}/scripts"
```
