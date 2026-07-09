#!/usr/bin/env python3
"""
Stop hook: deterministic backstop for auto-intent mode.

Reads the hook payload on stdin (Claude Code Stop event) and mines the
transcript for (a) the last user request and (b) the agent's closing summary —
the natural-language "what I did and why" that stands in for an explicit plan.
Then asks intent_blocks.py to write a git-diff receipt, but only when
`.intent/config.json` has auto_finalize=true and the tree changed since the
last receipt.

Always exits 0: a backstop must never block the session from ending.
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "intent_blocks.py"


def _blocks_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def mine_transcript(transcript_path: str) -> tuple[str, str, str]:
    """Return (last_user_prompt, last_assistant_summary, model), best-effort.

    The assistant summary is the final assistant text turn — typically a recap
    of the change, the most plan-like signal available without the agent
    calling finalize itself. The model is read from the assistant turns so the
    receipt reflects what actually ran, not a static config label.
    """
    if not transcript_path:
        return "", "", ""
    p = Path(transcript_path)
    if not p.exists():
        return "", "", ""

    prompt = ""
    summary = ""
    model = ""
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                evt = json.loads(line)
            except Exception:
                continue
            etype = evt.get("type")
            msg = evt.get("message", {})
            text = _blocks_to_text(msg.get("content", "")).strip()
            if etype == "user":
                # Skip tool-result / command-stdout noise; keep real user text.
                if text and not text.startswith("<"):
                    prompt = text
            elif etype == "assistant":
                if text:
                    summary = text
                if msg.get("model"):
                    model = msg["model"]
    except Exception:
        return "", "", ""

    return prompt[:500], summary[:1000], model


def main() -> None:
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    payload = {}
    if raw.strip():
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}

    prompt, summary, model = mine_transcript(payload.get("transcript_path", ""))

    cmd = ["python3", str(SCRIPT), "auto-finalize", "--prompt", prompt]
    if summary:
        cmd += ["--plan", summary]
    if model:
        cmd += ["--model", model]

    try:
        subprocess.run(cmd, timeout=15, check=False)
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
