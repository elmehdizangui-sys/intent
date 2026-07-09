"""Unit tests for configurable auto-intent: config + directive + Stop backstop."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import intent_config as cfgmod


def test_missing_config_is_off_and_none_gate(tmp_path: Path):
    cfg = cfgmod.load_config(tmp_path)
    assert cfg.auto_intent is False
    assert cfg.gate == "none"
    assert cfg.auto_finalize is False


def test_save_then_load_roundtrip(tmp_path: Path):
    saved = cfgmod.IntentConfig(auto_intent=True, gate="none", auto_finalize=True, model="m")
    cfgmod.save_config(tmp_path, saved)
    loaded = cfgmod.load_config(tmp_path)
    assert loaded == saved


def test_invalid_gate_falls_back_to_none(tmp_path: Path):
    (tmp_path / ".intent").mkdir()
    cfgmod.config_path(tmp_path).write_text(json.dumps({"gate": "bogus", "auto_intent": True}))
    assert cfgmod.load_config(tmp_path).gate == "none"


def test_malformed_config_never_raises(tmp_path: Path):
    (tmp_path / ".intent").mkdir()
    cfgmod.config_path(tmp_path).write_text("{not json")
    cfg = cfgmod.load_config(tmp_path)
    assert cfg.auto_intent is False  # safe default


def test_directive_empty_when_off(tmp_path: Path):
    cfg = cfgmod.IntentConfig(auto_intent=False)
    assert cfgmod.auto_directive(cfg, "/x/intent_blocks.py") == ""


@pytest.mark.parametrize(
    "gate,needle",
    [
        ("none", "Do NOT wait for approval"),
        ("lightweight", "one-line plan"),
        ("full", "reply 'go'"),
    ],
)
def test_directive_per_gate(gate: str, needle: str):
    cfg = cfgmod.IntentConfig(auto_intent=True, gate=gate)
    text = cfgmod.auto_directive(cfg, "/x/intent_blocks.py")
    assert f"gate={gate}" in text
    assert needle in text


def test_merge_is_immutable(tmp_path: Path):
    base = cfgmod.IntentConfig()
    new = cfgmod.merge(base, auto_intent=True, gate="none")
    assert base.auto_intent is False  # unchanged
    assert new.auto_intent is True and new.gate == "none"
