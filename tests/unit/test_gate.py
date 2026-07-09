"""Unit tests for the enforcing plan gate (intent_gate)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import intent_gate as gate


# --- approval detection ----------------------------------------------------

@pytest.mark.parametrize(
    "prompt",
    ["go", "Go", "go!", "go ahead", "yes", "yes go", "proceed.", "lgtm",
     "ship it", "do it", "ok", "approved", "  go  "],
)
def test_approval_phrases(prompt: str):
    assert gate.is_approval(prompt) is True


@pytest.mark.parametrize(
    "prompt",
    ["add subtraction", "should I go ahead?", "go implement the parser",
     "yes but first refactor the module", "", "   ",
     "add a go routine to the worker"],
)
def test_non_approval_phrases(prompt: str):
    assert gate.is_approval(prompt) is False


# --- gate state lifecycle --------------------------------------------------

def test_gate_closed_by_default(tmp_path: Path):
    assert gate.gate_is_open(tmp_path, "s1") is False


def test_open_then_open_for_same_session(tmp_path: Path):
    gate.open_gate(tmp_path, "s1")
    assert gate.gate_is_open(tmp_path, "s1") is True


def test_open_does_not_leak_across_sessions(tmp_path: Path):
    gate.open_gate(tmp_path, "s1")
    assert gate.gate_is_open(tmp_path, "s2") is False


def test_clear_closes_the_gate(tmp_path: Path):
    gate.open_gate(tmp_path, "s1")
    gate.clear_gate(tmp_path)
    assert gate.gate_is_open(tmp_path, "s1") is False


def test_clear_when_absent_is_safe(tmp_path: Path):
    gate.clear_gate(tmp_path)  # must not raise
    assert gate.gate_is_open(tmp_path, "s1") is False


def test_malformed_token_reads_as_closed(tmp_path: Path):
    p = gate.gate_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert gate.gate_is_open(tmp_path, "s1") is False


def test_empty_session_id_matches_empty(tmp_path: Path):
    gate.open_gate(tmp_path, "")
    assert gate.gate_is_open(tmp_path, "") is True
    assert gate.gate_is_open(tmp_path, "s1") is False


# --- plan capture ------------------------------------------------------------

def test_open_gate_with_plan_writes_session_plan_file(tmp_path: Path):
    gate.open_gate(tmp_path, "s1", plan_text="1. Do the thing")
    text = gate.plan_path(tmp_path, "s1").read_text(encoding="utf-8")
    assert text == "<!-- session_id: s1 -->\n\n1. Do the thing\n"


def test_open_gate_without_plan_does_not_write_plan_file(tmp_path: Path):
    gate.open_gate(tmp_path, "s1")
    assert not gate.plan_path(tmp_path, "s1").exists()


def test_clear_gate_leaves_plan_file_alone(tmp_path: Path):
    gate.open_gate(tmp_path, "s1", plan_text="1. Do the thing")
    gate.clear_gate(tmp_path)
    assert gate.plan_path(tmp_path, "s1").exists()
    assert not gate.gate_path(tmp_path).exists()


def test_clear_gate_without_plan_file_is_safe(tmp_path: Path):
    gate.open_gate(tmp_path, "s1")
    gate.clear_gate(tmp_path)  # must not raise even though no plan file exists


def test_second_approval_same_session_appends(tmp_path: Path):
    gate.open_gate(tmp_path, "s1", plan_text="1. First plan")
    gate.clear_gate(tmp_path)  # re-arm for a new task, same session
    gate.open_gate(tmp_path, "s1", plan_text="1. Second plan")
    text = gate.plan_path(tmp_path, "s1").read_text(encoding="utf-8")
    assert "1. First plan" in text
    assert "1. Second plan" in text
    assert text.index("1. First plan") < text.index("1. Second plan")
    assert text.count("---") == 1


def test_third_approval_same_session_keeps_appending(tmp_path: Path):
    gate.open_gate(tmp_path, "s1", plan_text="1. First plan")
    gate.open_gate(tmp_path, "s1", plan_text="1. Second plan")
    gate.open_gate(tmp_path, "s1", plan_text="1. Third plan")
    text = gate.plan_path(tmp_path, "s1").read_text(encoding="utf-8")
    for plan in ("1. First plan", "1. Second plan", "1. Third plan"):
        assert plan in text
    assert text.count("---") == 2


def test_new_session_gets_its_own_file_old_one_survives(tmp_path: Path):
    gate.open_gate(tmp_path, "s1", plan_text="1. Old session plan")
    gate.open_gate(tmp_path, "s2", plan_text="1. New session plan")
    old_text = gate.plan_path(tmp_path, "s1").read_text(encoding="utf-8")
    new_text = gate.plan_path(tmp_path, "s2").read_text(encoding="utf-8")
    assert "1. Old session plan" in old_text
    assert "1. New session plan" in new_text
    assert "1. New session plan" not in old_text


def test_plan_path_sanitizes_unsafe_session_id(tmp_path: Path):
    path = gate.plan_path(tmp_path, "../../etc/passwd")
    assert path.parent == tmp_path / ".intent" / "plans"
    assert ".." not in path.name and "/" not in path.name


def test_plan_path_falls_back_for_empty_session_id(tmp_path: Path):
    path = gate.plan_path(tmp_path, "")
    assert path.name == "unknown.md"


def test_mine_last_assistant_text_empty_path():
    assert gate.mine_last_assistant_text("") == ""


def test_mine_last_assistant_text_missing_file(tmp_path: Path):
    assert gate.mine_last_assistant_text(str(tmp_path / "missing.jsonl")) == ""


def test_mine_last_assistant_text_picks_last_assistant_turn(tmp_path: Path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join([
            json.dumps({"type": "user", "message": {"content": "do the task"}}),
            json.dumps({"type": "assistant", "message": {"content": "1. Plan A"}}),
            json.dumps({"type": "user", "message": {"content": "actually, tweak it"}}),
            json.dumps({"type": "assistant", "message": {
                "content": [{"type": "text", "text": "1. Plan B"}]
            }}),
        ]),
        encoding="utf-8",
    )
    assert gate.mine_last_assistant_text(str(transcript)) == "1. Plan B"


def test_mine_last_assistant_text_truncates_to_limit(tmp_path: Path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"type": "assistant", "message": {"content": "x" * 100}}),
        encoding="utf-8",
    )
    assert gate.mine_last_assistant_text(str(transcript), limit=10) == "x" * 10


def test_mine_last_assistant_text_skips_malformed_lines(tmp_path: Path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join([
            "{not json",
            json.dumps({"type": "assistant", "message": {"content": "1. Plan A"}}),
        ]),
        encoding="utf-8",
    )
    assert gate.mine_last_assistant_text(str(transcript)) == "1. Plan A"
