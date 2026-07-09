#!/usr/bin/env python3
"""
Per-repo auto-intent configuration.

Config lives at `.intent/config.json` (committed with the repo). When the file
is absent (or unreadable), auto-mode is OFF and gate defaults to "none" —
installing or enabling the hooks/skill has NO effect on a project until that
project opts in with its own config.json. This keeps gate and receipt
enforcement strictly per-project.

Schema:
  {
    "auto_intent":   bool,   # inject an auto-mode directive at SessionStart
    "gate":          str,    # "none" | "lightweight" | "full"
    "auto_finalize": bool,   # Stop hook writes a git-diff receipt as a backstop
    "model":         str     # model id recorded on auto-written receipts
  }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

CONFIG_REL = ".intent/config.json"

VALID_GATES = ("none", "lightweight", "full")


@dataclass(frozen=True)
class IntentConfig:
    auto_intent: bool = False
    gate: str = "none"
    auto_finalize: bool = False
    model: str = ""

    def to_dict(self) -> dict:
        return {
            "auto_intent": self.auto_intent,
            "gate": self.gate,
            "auto_finalize": self.auto_finalize,
            "model": self.model,
        }


def _coerce(raw: dict) -> IntentConfig:
    gate = str(raw.get("gate", "none")).strip().lower()
    if gate not in VALID_GATES:
        gate = "none"
    return IntentConfig(
        auto_intent=bool(raw.get("auto_intent", False)),
        gate=gate,
        auto_finalize=bool(raw.get("auto_finalize", False)),
        model=str(raw.get("model", "") or ""),
    )


def config_path(root: Path) -> Path:
    return root / CONFIG_REL


def load_config(root: Path) -> IntentConfig:
    """Load config from `.intent/config.json`; return safe defaults if missing
    or malformed (never raises — a broken config must not break a session)."""
    path = config_path(root)
    if not path.exists():
        return IntentConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return IntentConfig()
        return _coerce(raw)
    except Exception:
        return IntentConfig()


def save_config(root: Path, cfg: IntentConfig) -> Path:
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def merge(cfg: IntentConfig, **changes) -> IntentConfig:
    """Return a new config with the given fields overridden (immutable)."""
    clean = {k: v for k, v in changes.items() if v is not None}
    if "gate" in clean:
        g = str(clean["gate"]).strip().lower()
        clean["gate"] = g if g in VALID_GATES else cfg.gate
    return replace(cfg, **clean)


# ---------------------------------------------------------------------------
# Directive text injected at SessionStart, one per gate mode.
# ---------------------------------------------------------------------------

def auto_directive(cfg: IntentConfig, script_path: str) -> str:
    """Build the SessionStart directive that tells the agent how to operate in
    auto-intent mode for the configured gate. Returns "" when auto mode is off."""
    if not cfg.auto_intent:
        return ""

    model = cfg.model or "the active model"
    finalize_cmd = (
        f'python3 "{script_path}" finalize '
        f'--plan "<your numbered plan>" --git-diff '
        f'--prompt "<the original request>" --model "{cfg.model or ""}"'
    )

    if cfg.gate == "none":
        gate_rule = (
            "Do NOT wait for approval. Proceed directly with the change."
        )
    elif cfg.gate == "lightweight":
        gate_rule = (
            "State a one-line plan, then proceed in the same turn unless the "
            "user objects. Do not block waiting for an explicit 'go'."
        )
    else:  # full
        gate_rule = (
            "Present a Reformulation + numbered Plan and WAIT for the user to "
            "reply 'go' before editing any file."
        )

    backstop = (
        " A Stop hook will auto-write a git-diff receipt as a backstop if you "
        "forget, but prefer to finalize explicitly so the plan text is captured."
        if cfg.auto_finalize else ""
    )

    return (
        f"=== AUTO-INTENT MODE (gate={cfg.gate}) ===\n"
        f"This repo has auto-intent enabled. For ANY task that edits code:\n"
        f"  1. {gate_rule}\n"
        f"  2. Make the edits (mark regions with @intent:/@for: when it adds signal).\n"
        f"  3. Before finishing, persist a receipt:\n"
        f"     {finalize_cmd}\n"
        f"Model: {model}.{backstop}"
    )
