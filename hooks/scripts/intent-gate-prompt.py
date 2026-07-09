#!/usr/bin/env python3
"""
UserPromptSubmit hook: drive the plan gate from the user's message.

  - Bare approval ("go", "go ahead", "lgtm", ...) -> open the gate for this
    session so the agent's edits are allowed to proceed, and mine the
    transcript for the plan the agent posted just before this reply so it
    lands in `.intent/plans/<session_id>.md` for review.
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
        if gate.is_approval(prompt):
            plan_text = gate.mine_last_assistant_text(payload.get("transcript_path", ""))
            gate.open_gate(root, session_id, plan_text=plan_text)
        else:
            gate.clear_gate(root)
    except Exception:
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
