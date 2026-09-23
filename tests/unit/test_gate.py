"""Unit tests for the enforcing plan gate (intent_gate)."""
from __future__ import annotations

import json
import subprocess
import sys
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


# --- approval with plan-name override ---------------------------------------

@pytest.mark.parametrize("prompt", ["go", "go ahead", "lgtm", "  go  "])
def test_parse_approval_bare_has_no_override(prompt: str):
    assert gate.parse_approval(prompt) == (True, "")


@pytest.mark.parametrize(
    ("prompt", "override"),
    [
        ("go as fetch-quest-by-id", "fetch-quest-by-id"),
        ("go named fetch-quest-by-id", "fetch-quest-by-id"),
        ("go name: fetch-quest-by-id", "fetch-quest-by-id"),
        ("go ahead as add-quest-finder", "add-quest-finder"),
    ],
)
def test_parse_approval_with_override(prompt: str, override: str):
    assert gate.parse_approval(prompt) == (True, override)


@pytest.mark.parametrize(
    "prompt",
    ["go to the store", "go implement the parser", "should I go ahead?", ""],
)
def test_parse_approval_non_approval_has_no_override(prompt: str):
    assert gate.parse_approval(prompt) == (False, "")


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
    path = gate.plan_path(tmp_path, "s1")
    assert path.name == "001-do-the-thing.md"
    text = path.read_text(encoding="utf-8")
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


def test_plan_path_is_unaffected_by_unsafe_session_id(tmp_path: Path):
    """Session id no longer appears in the filename, so it can't inject path
    segments there — it only ever lands inside the file as a comment."""
    path = gate.plan_path(tmp_path, "../../etc/passwd")
    assert path.parent == tmp_path / ".intent" / "plans"
    assert ".." not in path.name and "/" not in path.name


def test_plan_path_falls_back_to_untitled_for_empty_plan_text(tmp_path: Path):
    path = gate.plan_path(tmp_path, "s1")
    assert path.name == "001-untitled.md"


def test_plan_path_slug_reflects_plan_text_first_line(tmp_path: Path):
    path = gate.plan_path(tmp_path, "s1", plan_text="## Add subtraction support\n\nmore detail")
    assert path.name == "001-add-subtraction-support.md"


def test_plan_path_slug_prefers_plan_name_line_over_preamble(tmp_path: Path):
    plan_text = (
        "I need to get plan confirmation first per this repo's intent-gate "
        "hook. Here's my plan:\n\n"
        "**Plan name:** add-find-quest-method\n\n"
        "**Reformulation:** Add a findQuest method.\n\n"
        "1. Do the thing\n2. Do another thing"
    )
    path = gate.plan_path(tmp_path, "s1", plan_text=plan_text)
    assert path.name == "001-add-find-quest-method.md"


def test_plan_path_slug_falls_back_to_reformulation_line(tmp_path: Path):
    plan_text = (
        "Sure, here is what I'll do:\n\n"
        "**Reformulation:** Add subtraction support to the calculator.\n\n"
        "1. Edit Calculator.kt"
    )
    path = gate.plan_path(tmp_path, "s1", plan_text=plan_text)
    assert path.name == "001-add-subtraction-support-to-the-calculator.md"


def test_plan_path_slug_override_wins_over_mined_plan_text(tmp_path: Path):
    plan_text = "**Plan name:** whatever-the-ai-picked\n\n1. Step one"
    path = gate.plan_path(tmp_path, "s1", plan_text=plan_text, slug_override="fetch-quest-by-id")
    assert path.name == "001-fetch-quest-by-id.md"


def test_plan_path_sequence_increments_across_sessions(tmp_path: Path):
    gate.open_gate(tmp_path, "s1", plan_text="1. First plan")
    gate.open_gate(tmp_path, "s2", plan_text="1. Second plan")
    assert gate.plan_path(tmp_path, "s1").name == "001-first-plan.md"
    assert gate.plan_path(tmp_path, "s2").name == "002-second-plan.md"


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


# --- plan-shape heuristic ---------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "1. Reformulate the request\n2. Edit Foo.kt\n3. Edit Bar.kt",
        "Reformulation: add logging\n\nPlan:\n1. Add logger to Service\n2. Wire it in main",
        "- Update the schema\n- Add a migration\n- Write a test",
    ],
)
def test_looks_like_plan_true_for_plan_shaped_text(text: str):
    assert gate.looks_like_plan(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "sounds good",
        "Everything is in order, all done!",
        "1. Just one line, no second line",
        "This is a single long line with no list markers at all, just prose describing what happened after the fact",
    ],
)
def test_looks_like_plan_false_for_non_plan_text(text: str):
    assert gate.looks_like_plan(text) is False


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


# --- Bash file-write detection ----------------------------------------------

@pytest.mark.parametrize(
    "command",
    [
        "sed -i '' 's/a/b/' src/Foo.kt",
        "sed -i.bak -e 's/a/b/' src/Foo.kt",
        "perl -pi -e 's/a/b/' src/Foo.kt",
        "echo hi > src/Foo.kt",
        "echo hi >> README.md",
        "cat > src/Foo.kt <<'EOF'\nclass Foo\nEOF",
        "printf x | tee src/Foo.kt",
        "cp a.kt src/b.kt",
        "mv a.kt b.kt",
        "git apply change.patch",
        # the heredoc shape that walked past the Edit-only gate in practice
        "python3 - <<'EOF'\nimport pathlib\nf=pathlib.Path('Foo.kt'); f.write_text(f.read_text().replace('a','b'))\nEOF",
        "python3 -c \"open('Foo.kt', 'w').write('x')\"",
        "node -e \"require('fs').writeFileSync('a.js', 'x')\"",
    ],
)
def test_bash_writes_files_true_for_write_idioms(command: str):
    assert gate.bash_writes_files(command) is True


@pytest.mark.parametrize(
    "command",
    [
        "",
        "grep -rn 'foo' src",
        "cat src/Foo.kt | head -20",
        "git status && git diff",
        "sed -n 1,40p src/Foo.kt",
        "./gradlew build 2>&1 | tail -30",
        "ls missing 2>/dev/null",
        "echo err >&2",
        "./gradlew build > /tmp/build.log 2>&1",
        "./gradlew build > /private/tmp/claude/scratch/build.log",
        "grep -n 'a -> b' src/Foo.kt",
        "python3 -c \"print(1 >= 0)\"",
        "python3 -c \"open('Foo.kt').read()\"",
    ],
)
def test_bash_writes_files_false_for_read_only_commands(command: str):
    assert gate.bash_writes_files(command) is False


# --- PreToolUse hook end to end ---------------------------------------------

_HOOK = Path(__file__).resolve().parents[2] / "hooks" / "scripts" / "intent-gate-check.py"


def _run_hook(repo: Path, payload: dict) -> int:
    return subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload), text=True, cwd=repo, capture_output=True,
    ).returncode


@pytest.fixture
def full_gate_repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".intent").mkdir()
    (tmp_path / ".intent" / "config.json").write_text(json.dumps({"gate": "full"}), encoding="utf-8")
    return tmp_path


def test_hook_denies_bash_write_while_gate_closed(full_gate_repo: Path):
    payload = {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "sed -i '' 's/a/b/' Foo.kt"}}
    assert _run_hook(full_gate_repo, payload) == 2


def test_hook_allows_read_only_bash_while_gate_closed(full_gate_repo: Path):
    payload = {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "grep -rn foo ."}}
    assert _run_hook(full_gate_repo, payload) == 0


def test_hook_allows_bash_write_once_gate_open(full_gate_repo: Path):
    gate.open_gate(full_gate_repo, "s1", plan_text="1. Step one\n2. Step two")
    payload = {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "sed -i '' 's/a/b/' Foo.kt"}}
    assert _run_hook(full_gate_repo, payload) == 0
