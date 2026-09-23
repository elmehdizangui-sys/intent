#!/usr/bin/env python3
"""
PreToolUse hook (Edit|Write|MultiEdit|Bash): the enforcing half of the plan gate.

Denies a file edit (exit 2) unless the gate is open for this session. Bash is
gated too, but only when the command looks like a file write (sed -i, `>`
redirection, a heredoc calling write_text, ...) - otherwise editing through
the shell walks straight past the gate. Read-only Bash is always allowed. This is
the mechanism SessionStart could never provide — a refused tool call, not an
advisory note. Active only when `.intent/config.json` has gate="full".

Exit codes:
  0  allow (gate not "full", or gate open, or any internal error -> fail open)
  2  deny  (gate "full" and not yet opened for this task)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import intent_gate as gate  # noqa: E402
from intent_config import load_config  # noqa: E402

BLOCK_MSG = (
    "[intent] BLOCKED: no plan confirmed for this task. "
    "Present a Reformulation + numbered Plan and wait for the user to reply "
    "'go' before editing any file. (gate=full)"
)


def main() -> int:
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
            return 0
        if payload.get("tool_name") == "Bash":
            command = (payload.get("tool_input") or {}).get("command", "") or ""
            if not gate.bash_writes_files(command):
                return 0
        session_id = payload.get("session_id", "") or ""
        if gate.gate_is_open(root, session_id):
            return 0
    except Exception:
        # Fail open: a broken gate must not wedge the session.
        return 0

    sys.stderr.write(BLOCK_MSG + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
