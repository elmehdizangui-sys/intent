#!/usr/bin/env python3
"""
Enforcing plan gate for auto-intent `gate="full"` repos.

SessionStart can only *advise* the agent to present a plan and wait for "go";
it cannot stop an edit. This module backs a real gate that lives at PreToolUse:

  - UserPromptSubmit  -> open the gate on a bare approval ("go"), otherwise
                         clear it (a new substantive request re-arms the gate).
  - PreToolUse(Edit)  -> allow the edit only while the gate is open; otherwise
                         deny it (exit 2) and tell the agent to present a plan.

State is a single token file `.intent/.gate` holding the session id that opened
it. A new session's id will not match a stale token, so the gate is closed by
default across sessions. All functions are pure/deterministic and never raise on
malformed state — a broken gate must never wedge a session.

`.intent/plans/<session_id>.md` is written when the gate opens, holding the
plan text the agent posted in chat right before the approval — as plain text,
not escaped JSON, so it reads naturally in a diff or file view. Every session
gets its own file, so old sessions' plans stay in the repo permanently,
clearly linked to the session that produced them, instead of being
overwritten by the next session. Multiple approvals within the SAME session
append to that session's file (separated by `---`). The gate token
(`.intent/.gate`) re-arming for a new task never touches these files.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

GATE_REL = ".intent/.gate"
PLAN_DIR = ".intent/plans"
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]+")

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


def find_repo_root(start: Path) -> Path:
    p = start.resolve()
    while p != p.parent:
        if (p / ".git").exists() or (p / ".intent").exists():
            return p
        p = p.parent
    return start.resolve()


def gate_path(root: Path) -> Path:
    return root / GATE_REL


def plan_path(root: Path, session_id: str) -> Path:
    safe_id = _SAFE_ID.sub("_", (session_id or "").strip()) or "unknown"
    return root / PLAN_DIR / f"{safe_id}.md"


def is_approval(prompt: str) -> bool:
    """True when the whole message reads as a bare go-ahead, not a new task."""
    if not prompt:
        return False
    tokens = prompt.strip().lower().translate(_PUNCT).split()
    if not tokens or len(tokens) > MAX_APPROVAL_TOKENS:
        return False
    return all(t in APPROVAL_TOKENS for t in tokens)


def open_gate(root: Path, session_id: str, plan_text: str = "") -> Path:
    path = gate_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"session_id": session_id or ""}) + "\n",
        encoding="utf-8",
    )
    if plan_text:
        write_plan(root, session_id, plan_text)
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


def write_plan(root: Path, session_id: str, plan_text: str) -> Path:
    """Append the approved plan to this session's own file. Each session has a
    distinct file (`.intent/plans/<session_id>.md`), so nothing from an older
    session is ever overwritten."""
    path = plan_path(root, session_id)
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
