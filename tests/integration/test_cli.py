"""Integration tests for intent_blocks v1.0 CLI commands."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = str(Path(__file__).parent.parent.parent / "scripts" / "intent_blocks.py")
PYTHON = sys.executable


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, SCRIPT, *args],
        capture_output=True, text=True, cwd=str(cwd)
    )


def make_receipt_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".intent" / "receipts"
    d.mkdir(parents=True)
    return d


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------

class TestFinalize:
    def test_happy_path_writes_receipt(self, tmp_path):
        f = tmp_path / "Foo.kt"
        f.write_text("// @intent: do the thing\nval x = 1\n")
        make_receipt_dir(tmp_path)

        regions = json.dumps([{
            "role": "primary",
            "file": "Foo.kt",
            "marker_line_hint": 0,
            "local_intent": "do the thing",
        }])
        result = run("finalize", "--plan", "1. Do the thing", "--regions", regions,
                     "--auto-hash",  # Let script compute hash
                     "--model", "claude-test", "--prompt", "do the thing",
                     cwd=tmp_path)
        assert result.returncode == 0
        assert "Receipt" in result.stdout

        receipts = list((tmp_path / ".intent" / "receipts").glob("*.json"))
        assert len(receipts) == 1
        data = json.loads(receipts[0].read_text())
        assert data["intent"]["plan"] == "1. Do the thing"
        assert data["spec_version"] == "1.0"
        assert data["validation"]["auto_hash"] is True

    def test_empty_plan_exits_1(self, tmp_path):
        make_receipt_dir(tmp_path)
        regions = json.dumps([{"role": "primary", "file": "x.kt",
                                "local_intent": "x", "anchor_content_hash": "sha256:a"}])
        result = run("finalize", "--plan", "", "--regions", regions, cwd=tmp_path)
        assert result.returncode == 1
        assert "plan" in result.stderr.lower()

    def test_empty_regions_exits_1(self, tmp_path):
        make_receipt_dir(tmp_path)
        result = run("finalize", "--plan", "1. Do thing", "--regions", "[]", cwd=tmp_path)
        assert result.returncode == 1
        assert "regions" in result.stderr.lower()

    def test_invalid_regions_json_exits_3(self, tmp_path):
        make_receipt_dir(tmp_path)
        result = run("finalize", "--plan", "1. Do thing", "--regions", "not-json", cwd=tmp_path)
        assert result.returncode == 3


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

class TestVerify:
    def _write_receipt(self, tmp_path: Path, local_intent: str, code_line: str) -> dict:
        import hashlib
        code = code_line.rstrip()
        h = "sha256:" + hashlib.sha256(f"{local_intent}\n{code}".encode()).hexdigest()
        receipt = {
            "id": "test1234",
            "spec_version": "1.0",
            "created_at": "2026-01-01T00:00:00+00:00",
            "model": "test",
            "intent": {"original_prompt": "test", "plan": "1. test"},
            "regions": [{
                "role": "primary",
                "file": "Foo.kt",
                "marker_line_hint": 0,
                "anchor_content_hash": h,
                "local_intent": local_intent,
            }],
        }
        rd = make_receipt_dir(tmp_path)
        (rd / "test1234.json").write_text(json.dumps(receipt))
        return receipt

    def test_ok_when_hash_matches(self, tmp_path):
        intent = "do the thing"
        code = "val x = 1"
        (tmp_path / "Foo.kt").write_text(f"// @intent: {intent}\n{code}\n")
        self._write_receipt(tmp_path, intent, code)
        result = run("verify", cwd=tmp_path)
        assert result.returncode == 0
        assert "OK" in result.stdout

    def test_drift_when_code_changed(self, tmp_path):
        intent = "do the thing"
        (tmp_path / "Foo.kt").write_text(f"// @intent: {intent}\nval x = 999\n")
        self._write_receipt(tmp_path, intent, "val x = 1")
        result = run("verify", cwd=tmp_path)
        assert result.returncode == 0
        assert "DRIFT" in result.stdout

    def test_strict_exits_nonzero_on_drift(self, tmp_path):
        intent = "do the thing"
        (tmp_path / "Foo.kt").write_text(f"// @intent: {intent}\nval x = 999\n")
        self._write_receipt(tmp_path, intent, "val x = 1")
        result = run("verify", "--strict", cwd=tmp_path)
        assert result.returncode == 1

    def test_missing_when_file_absent(self, tmp_path):
        intent = "do the thing"
        self._write_receipt(tmp_path, intent, "val x = 1")
        result = run("verify", cwd=tmp_path)
        assert "MISSING" in result.stdout

    def test_no_receipts_exits_0(self, tmp_path):
        make_receipt_dir(tmp_path)
        result = run("verify", cwd=tmp_path)
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

class TestList:
    def test_shows_markers_with_receipt_id(self, tmp_path):
        f = tmp_path / "Foo.kt"
        f.write_text("// @intent: do the thing\nval x = 1\n")
        rd = make_receipt_dir(tmp_path)
        receipt = {
            "id": "abcd1234",
            "spec_version": "1.0",
            "created_at": "2026-01-01T00:00:00+00:00",
            "model": "test",
            "intent": {"original_prompt": "t", "plan": "1. t"},
            "regions": [{"role": "primary", "file": "Foo.kt",
                         "local_intent": "do the thing", "anchor_content_hash": "sha256:x"}],
        }
        (rd / "abcd1234.json").write_text(json.dumps(receipt))
        result = run("list", "Foo.kt", cwd=tmp_path)
        assert result.returncode == 0
        assert "do the thing" in result.stdout
        assert "abcd1234" in result.stdout

    def test_no_markers_message(self, tmp_path):
        f = tmp_path / "Empty.kt"
        f.write_text("val x = 1\n")
        make_receipt_dir(tmp_path)
        result = run("list", "Empty.kt", cwd=tmp_path)
        assert result.returncode == 0
        assert "No" in result.stdout


# ---------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------

class TestFind:
    def test_finds_marker_in_tree(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "Foo.kt").write_text("// @intent: something\nval x = 1\n")
        make_receipt_dir(tmp_path)
        result = run("find", cwd=tmp_path)
        assert result.returncode == 0
        assert "@intent" in result.stdout
        assert "something" in result.stdout

    def test_no_markers_message(self, tmp_path):
        make_receipt_dir(tmp_path)
        result = run("find", cwd=tmp_path)
        assert "No" in result.stdout


# ---------------------------------------------------------------------------
# ack
# ---------------------------------------------------------------------------

class TestAck:
    def test_ack_updates_hash(self, tmp_path):
        import hashlib
        intent = "do the thing"
        new_code = "val x = 999"
        (tmp_path / "Foo.kt").write_text(f"// @intent: {intent}\n{new_code}\n")

        old_hash = "sha256:" + hashlib.sha256(f"{intent}\nval x = 1".encode()).hexdigest()
        receipt = {
            "id": "aaaa1234",
            "spec_version": "1.0",
            "created_at": "2026-01-01T00:00:00+00:00",
            "model": "test",
            "intent": {"original_prompt": "t", "plan": "1. t"},
            "regions": [{
                "role": "primary",
                "file": "Foo.kt",
                "local_intent": intent,
                "anchor_content_hash": old_hash,
            }],
        }
        rd = make_receipt_dir(tmp_path)
        receipt_path = rd / "aaaa1234.json"
        receipt_path.write_text(json.dumps(receipt))

        result = run("ack", "aaaa1234", "--region", "0", "--reason", "refactored", cwd=tmp_path)
        assert result.returncode == 0

        updated = json.loads(receipt_path.read_text())
        acks = updated["regions"][0].get("acks", [])
        assert len(acks) == 1
        assert acks[0]["reason"] == "refactored"

        new_expected = "sha256:" + hashlib.sha256(f"{intent}\n{new_code}".encode()).hexdigest()
        assert updated["regions"][0]["anchor_content_hash"] == new_expected
