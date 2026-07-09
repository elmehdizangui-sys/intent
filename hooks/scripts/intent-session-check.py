#!/usr/bin/env python3
"""
SessionStart hook: delegate to `intent_blocks.py session-check`, which injects
recent receipt history AND — when `.intent/config.json` enables it — the
auto-intent directive for the configured gate.

Single source of truth lives in intent_blocks.py; this wrapper only forwards
its stdout (the {"systemMessage": ...} contract) and never fails the session.
"""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "intent_blocks.py"


def main() -> None:
    try:
        result = subprocess.run(
            ["python3", str(SCRIPT), "session-check"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.stdout.strip():
            sys.stdout.write(result.stdout)
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
