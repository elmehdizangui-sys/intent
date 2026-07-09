#!/usr/bin/env python3
"""
Enforcing plan gate for auto-intent `gate="full"` repos.

SessionStart can only *advise* the agent to present a plan and wait for "go";
it cannot stop an edit. This module backs a real gate that lives at PreToolUse:

  - UserPromptSubmit  -> open the gate on a bare approval ("go") only if the
                         assistant's last turn actually looks like a plan;
                         otherwise clear it (a new substantive request
                         re-arms the gate).
  - PreToolUse(Edit)  -> allow the edit only while the gate is open; otherwise
                         deny it (exit 2) and tell the agent to present a plan.

State is a single token file `.intent/.gate` holding the session id that opened
it. A new session's id will not match a stale token, so the gate is closed by
default across sessions. All functions are pure/deterministic and never raise on
malformed state — a broken gate must never wedge a session.

`.intent/plans/<seq>-<slug>.md` is written when the gate opens, holding the
plan text the agent posted in chat right before the approval — as plain text,
not escaped JSON, so it reads naturally in a diff or file view. `<seq>` is a
zero-padded counter over the plan directory (so listing the directory shows
creation order) and `<slug>` comes from the plan's `Plan name:` line (the
agent proposes one), falling back to `Reformulation:` and then the first
non-empty line, so filenames are readable at a glance instead of an opaque
session id. The dev can override the proposed name at approval time with
"go as <name>". The
session id itself is recorded inside the file (`<!-- session_id: ... -->`),
not in the name. Every session gets its own file, so old sessions' plans
stay in the repo permanently. Multiple approvals within the SAME session are
found by that marker and append to the same file (separated by `---`). The
gate token (`.intent/.gate`) re-arming for a new task never touches these
files.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

GATE_REL = ".intent/.gate"
PLAN_DIR = ".intent/plans"
_SEQ_PREFIX = re.compile(r"^(\d+)-")
_WORD = re.compile(r"[A-Za-z0-9]+")
_LEADING_MARKUP = re.compile(r"^(#+|\d+[.)]|[-*])\s*")
_MAX_SLUG_WORDS = 8
_PLAN_NAME_LINE = re.compile(r"(?im)^\**plan\s*name\**\s*:?\s*(.+?)\s*$")
_REFORMULATION_LINE = re.compile(r"(?im)^\**reformulation\**\s*:?\s*(.+?)\s*$")

# A prompt is treated as an approval only when it consists ENTIRELY of these
# tokens (1-3 of them). This keeps "should I go ahead?" or "add go-routine"
# from being misread as approval, while accepting "go", "go ahead", "yes go",
# "proceed", "lgtm", "ship it", "do it", etc.
APPROVAL_TOKENS = frozenset({
    "go", "ahead", "proceed", "yes", "yep", "yeah", "ok", "okay", "sure",
    "approved", "approve", "lgtm", "confirmed", "confirm", "do", "it",
    "please", "ship",
})
MAX_APPROVAL_TOKENS = 3

_PUNCT = str.maketrans("", "", "!.,;:?\"'`-")

# After a bare approval prefix, an optional "as <name>" / "named <name>" /
# "name: <name>" suffix lets the dev override the AI-proposed plan name, e.g.
# "go as fetch-quest-by-id". The prefix must itself be a bare approval, so
# unrelated sentences that happen to start with "go" are never misread.
_SLUG_OVERRIDE_RE = re.compile(r"(?is)^(.*?)\s+(?:as|named?|name)\s*:?\s+(.+)$")


def find_repo_root(start: Path) -> Path:
    p = start.resolve()
    while p != p.parent:
        if (p / ".git").exists() or (p / ".intent").exists():
            return p
        p = p.parent
    return start.resolve()


def gate_path(root: Path) -> Path:
    return root / GATE_REL


def _slug_from_text(text: str) -> str:
    """Free text -> filename slug, e.g. "Add request logging" ->
    "add-request-logging"."""
    text = _LEADING_MARKUP.sub("", (text or "").strip())
    words = _WORD.findall(text.lower())[:_MAX_SLUG_WORDS]
    return "-".join(words) or "untitled"


def _slugify(plan_text: str) -> str:
    """Plan text -> filename slug. Prefers an explicit `Plan name:` line (the
    agent is asked to propose one per skills/intent/SKILL.md), then falls back
    to the `Reformulation:` line, then to the first non-empty line — so a
    chatty preamble before the actual plan ("I need to get plan confirmation
    first...") never becomes the slug."""
    text = plan_text or ""
    m = _PLAN_NAME_LINE.search(text)
    if not m:
        m = _REFORMULATION_LINE.search(text)
    if m:
        return _slug_from_text(m.group(1))
    first_line = ""
    for line in text.splitlines():
        line = line.strip()
        if line:
            first_line = line
            break
    return _slug_from_text(first_line)


def _next_seq(plan_dir: Path) -> int:
    max_seq = 0
    if plan_dir.exists():
        for f in plan_dir.glob("*.md"):
            m = _SEQ_PREFIX.match(f.name)
            if m:
                max_seq = max(max_seq, int(m.group(1)))
    return max_seq + 1


def _find_session_plan(plan_dir: Path, session_id: str) -> Path | None:
    """Locate this session's existing plan file by its embedded marker, since
    the filename no longer encodes the session id."""
    if not session_id or not plan_dir.exists():
        return None
    marker = f"<!-- session_id: {session_id} -->"
    for f in sorted(plan_dir.glob("*.md")):
        try:
            if marker in f.read_text(encoding="utf-8")[:2000]:
                return f
        except OSError:
            continue
    return None


def plan_path(root: Path, session_id: str, plan_text: str = "", slug_override: str = "") -> Path:
    """Resolve this session's plan file: reuse it if one already exists
    (found via its `session_id` marker), otherwise mint a new
    `<seq>-<slug>.md` name. `slug_override` (a dev-supplied name from the
    approval message, e.g. "go as fetch-quest-by-id") wins over anything
    mined from the plan text."""
    plan_dir = root / PLAN_DIR
    existing = _find_session_plan(plan_dir, session_id)
    if existing is not None:
        return existing
    seq = _next_seq(plan_dir)
    slug = _slug_from_text(slug_override) if slug_override else _slugify(plan_text)
    return plan_dir / f"{seq:03d}-{slug}.md"


def is_approval(prompt: str) -> bool:
    """True when the whole message reads as a bare go-ahead, not a new task."""
    if not prompt:
        return False
    tokens = prompt.strip().lower().translate(_PUNCT).split()
    if not tokens or len(tokens) > MAX_APPROVAL_TOKENS:
        return False
    return all(t in APPROVAL_TOKENS for t in tokens)


def parse_approval(prompt: str) -> tuple[bool, str]:
    """Like `is_approval`, but also recognizes a trailing plan-name override:
    "go as <name>" / "go named <name>" / "go name: <name>". Returns
    `(approved, slug_override)` — `slug_override` is "" when the dev didn't
    supply one (the AI-proposed name from the plan text is used instead).

    The prefix before "as"/"named"/"name" must itself pass `is_approval`, so
    an unrelated sentence that happens to start with "go" (e.g. "go to the
    store") is never misread as approval."""
    if not prompt or not prompt.strip():
        return False, ""
    text = prompt.strip()
    m = _SLUG_OVERRIDE_RE.match(text)
    if m:
        head, override = m.group(1), m.group(2).strip()
        if is_approval(head) and override:
            return True, override
    return is_approval(text), ""


_PLAN_MIN_CHARS = 40
_PLAN_MIN_LINES = 2
_PLAN_LIST_ITEM = re.compile(r"(?m)^\s*(\d+[.)]|[-*])\s+\S")


def looks_like_plan(text: str) -> bool:
    """Heuristic: does this assistant turn actually look like a Reformulation +
    numbered Plan, or is it just chat (e.g. a post-hoc summary, a question, an
    empty transcript)? A bare "go" must not open the gate on the strength of
    whatever the assistant happened to say last — it must have proposed a plan
    with concrete steps first."""
    stripped = (text or "").strip()
    if len(stripped) < _PLAN_MIN_CHARS:
        return False
    lines = [line for line in stripped.splitlines() if line.strip()]
    if len(lines) < _PLAN_MIN_LINES:
        return False
    return bool(_PLAN_LIST_ITEM.search(stripped))


def open_gate(root: Path, session_id: str, plan_text: str = "", slug_override: str = "") -> Path:
    path = gate_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"session_id": session_id or ""}) + "\n",
        encoding="utf-8",
    )
    if plan_text:
        write_plan(root, session_id, plan_text, slug_override=slug_override)
    return path


def clear_gate(root: Path) -> None:
    """Close the gate. Per-session plan files under `.intent/plans/` are left
    alone — re-arming for a new task must not erase what was already approved."""
    try:
        gate_path(root).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def write_plan(root: Path, session_id: str, plan_text: str, slug_override: str = "") -> Path:
    """Append the approved plan to this session's own file. Each session has a
    distinct file (`.intent/plans/<seq>-<slug>.md`), so nothing from an older
    session is ever overwritten."""
    path = plan_path(root, session_id, plan_text, slug_override=slug_override)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = f"<!-- session_id: {session_id or ''} -->\n\n{plan_text or ''}\n"

    if path.exists():
        existing = path.read_text(encoding="utf-8")
        path.write_text(existing.rstrip("\n") + "\n\n---\n\n" + entry, encoding="utf-8")
    else:
        path.write_text(entry, encoding="utf-8")
    return path


def _blocks_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def mine_last_assistant_text(transcript_path: str, limit: int = 4000) -> str:
    """Best-effort: return the most recent assistant text turn in the transcript.

    Used to capture the plan the agent presented in chat right before the
    user's approval, without requiring the agent to re-type it into a CLI call.
    """
    if not transcript_path:
        return ""
    p = Path(transcript_path)
    if not p.exists():
        return ""
    text = ""
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                evt = json.loads(line)
            except Exception:
                continue
            if evt.get("type") != "assistant":
                continue
            candidate = _blocks_to_text(evt.get("message", {}).get("content", "")).strip()
            if candidate:
                text = candidate
    except Exception:
        return ""
    return text[:limit]


def gate_is_open(root: Path, session_id: str) -> bool:
    """Open only when a token exists and was written by THIS session."""
    path = gate_path(root)
    if not path.exists():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return str(raw.get("session_id", "")) == (session_id or "")
    except Exception:
        return False
