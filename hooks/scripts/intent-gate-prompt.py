#!/usr/bin/env python3
"""
UserPromptSubmit hook: drive the plan gate from the user's message.

  - Bare approval ("go", "go ahead", "lgtm", ...) -> mine the transcript for
    the assistant's last turn, and only if it actually looks like a plan
    (Reformulation + numbered steps, not just chat or a summary) open the
    gate for this session and land it in `.intent/plans/<seq>-<slug>.md` for
    review. A "go" with no real plan behind it leaves the gate untouched.
    The slug is the AI-proposed `Plan name:` from the plan text, unless the
    dev overrides it in the approval itself ("go as fetch-quest-by-id").
  - Anything else (a new substantive request) -> clear the gate, re-arming it so
    the next edit attempt is blocked until a fresh plan is approved.

Only acts when `.intent/config.json` has gate="full"; otherwise it is a no-op.
Always exits 0 — never block prompt submission.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import intent_gate as gate  # noqa: E402
from intent_config import load_config  # noqa: E402


def main() -> None:
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    payload = {}
    if raw.strip():
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}

    try:
        root = gate.find_repo_root(Path("."))
        if load_config(root).gate != "full":
            return
        prompt = payload.get("prompt", "") or ""
        session_id = payload.get("session_id", "") or ""
        approved, slug_override = gate.parse_approval(prompt)
        if approved:
            plan_text = gate.mine_last_assistant_text(payload.get("transcript_path", ""))
            if gate.looks_like_plan(plan_text):
                gate.open_gate(root, session_id, plan_text=plan_text, slug_override=slug_override)
            # else: a bare "go" with no real plan behind it must not open the
            # gate — leave it as-is so PreToolUse keeps blocking edits.
        else:
            gate.clear_gate(root)
    except Exception:
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
