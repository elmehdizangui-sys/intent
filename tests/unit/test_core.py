"""Unit tests for intent_blocks v1.0 core functions."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
from intent_blocks import comment_prefix, compute_hash, find_markers, code_lines_after_marker


class TestCommentPrefix:
    def test_kotlin(self):
        assert comment_prefix("Foo.kt") == "//"

    def test_python(self):
        assert comment_prefix("foo.py") == "#"

    def test_yaml(self):
        assert comment_prefix("config.yaml") == "#"

    def test_sql(self):
        assert comment_prefix("migration.sql") == "--"

    def test_typescript(self):
        assert comment_prefix("app.ts") == "//"

    def test_dockerfile(self):
        assert comment_prefix("Dockerfile") == "#"

    def test_unknown_extension_defaults_to_slash(self):
        assert comment_prefix("file.xyz") == "//"


class TestComputeHash:
    def test_produces_sha256_prefix(self):
        h = compute_hash("do something", ["val x = 1"])
        assert h.startswith("sha256:")
        assert len(h) > 15

    def test_same_inputs_same_hash(self):
        h1 = compute_hash("intent", ["line one", "line two"])
        h2 = compute_hash("intent", ["line one", "line two"])
        assert h1 == h2

    def test_different_intent_different_hash(self):
        h1 = compute_hash("intent A", ["code"])
        h2 = compute_hash("intent B", ["code"])
        assert h1 != h2

    def test_trailing_whitespace_ignored(self):
        h1 = compute_hash("intent", ["val x = 1"])
        h2 = compute_hash("intent", ["val x = 1   "])
        assert h1 == h2

    def test_non_trailing_change_changes_hash(self):
        h1 = compute_hash("intent", ["val x = 1"])
        h2 = compute_hash("intent", ["val x = 2"])
        assert h1 != h2


class TestFindMarkers:
    def test_finds_intent_marker(self, tmp_path):
        f = tmp_path / "Foo.kt"
        f.write_text("package foo\n// @intent: do the thing\nval x = 1\n")
        markers = find_markers(f, "//")
        assert len(markers) == 1
        lineno, mtype, text = markers[0]
        assert lineno == 1
        assert mtype == "@intent"
        assert text == "do the thing"

    def test_finds_for_marker(self, tmp_path):
        f = tmp_path / "Bar.kt"
        f.write_text("// @for: collateral change\nval y = 2\n")
        markers = find_markers(f, "//")
        assert markers[0][1] == "@for"
        assert markers[0][2] == "collateral change"

    def test_ignores_import_lines(self, tmp_path):
        f = tmp_path / "Baz.kt"
        f.write_text("import foo.Bar\n// @intent: real intent\nval z = 3\n")
        markers = find_markers(f, "//")
        assert len(markers) == 1

    def test_python_hash_prefix(self, tmp_path):
        f = tmp_path / "foo.py"
        f.write_text("# @intent: python intent\nx = 1\n")
        markers = find_markers(f, "#")
        assert len(markers) == 1
        assert markers[0][2] == "python intent"

    def test_no_markers_returns_empty(self, tmp_path):
        f = tmp_path / "empty.kt"
        f.write_text("val x = 1\n")
        assert find_markers(f, "//") == []


class TestCodeLinesAfterMarker:
    def test_returns_up_to_3_non_blank_lines(self):
        lines = ["// @intent: x", "val a = 1", "val b = 2", "val c = 3", "val d = 4"]
        result = code_lines_after_marker(lines, 0, count=3)
        assert result == ["val a = 1", "val b = 2", "val c = 3"]

    def test_skips_blank_lines(self):
        lines = ["// @intent: x", "", "val a = 1"]
        result = code_lines_after_marker(lines, 0)
        assert result == ["val a = 1"]

    def test_strips_trailing_whitespace(self):
        lines = ["// @intent: x", "val a = 1   "]
        result = code_lines_after_marker(lines, 0)
        assert result == ["val a = 1"]
