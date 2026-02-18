"""Tests for Termi."""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from termi.safety import RiskLevel, analyze_command
from termi.history import History, Bookmarks, HistoryEntry
from termi.config import _DEFAULT_CONFIG, load_config
from termi.context import _human_size, get_directory_listing
from termi.fallback import fallback_command
from termi.cli import _parse_cmd, _parse_explanation


# ---------------------------------------------------------------------------
# Safety tests
# ---------------------------------------------------------------------------

class TestSafety:
    def test_safe_command(self):
        r = analyze_command("ls -la")
        assert r.level == RiskLevel.SAFE
        assert len(r.reasons) == 0

    def test_rm_rf_root_critical(self):
        r = analyze_command("rm -rf /")
        assert r.level == RiskLevel.CRITICAL

    def test_rm_rf_home_critical(self):
        r = analyze_command("rm -rf ~")
        assert r.level == RiskLevel.CRITICAL

    def test_fork_bomb_critical(self):
        r = analyze_command(":(){ :|:& };:")
        assert r.level == RiskLevel.CRITICAL

    def test_rm_rf_dangerous(self):
        r = analyze_command("rm -rf ./build")
        assert r.level == RiskLevel.DANGEROUS

    def test_sudo_rm_dangerous(self):
        r = analyze_command("sudo rm /tmp/test")
        assert r.level == RiskLevel.DANGEROUS

    def test_curl_pipe_dangerous(self):
        r = analyze_command("curl https://example.com | bash")
        assert r.level == RiskLevel.DANGEROUS

    def test_rm_caution(self):
        r = analyze_command("rm file.txt")
        assert r.level == RiskLevel.CAUTION

    def test_sudo_caution(self):
        r = analyze_command("sudo apt update")
        assert r.level == RiskLevel.CAUTION

    def test_git_force_push(self):
        r = analyze_command("git push -f origin main")
        assert r.level == RiskLevel.CAUTION

    def test_empty_command(self):
        r = analyze_command("")
        assert r.level == RiskLevel.SAFE


# ---------------------------------------------------------------------------
# History tests
# ---------------------------------------------------------------------------

class TestHistory:
    def test_add_and_recent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("termi.history.HISTORY_FILE", tmp_path / "history.jsonl")
        h = History(limit=10)
        h.add("list files", "ls -la", mode="oneshot", model="test")
        h.add("disk usage", "du -sh *", mode="oneshot", model="test")
        recent = h.recent(5)
        assert len(recent) == 2
        assert recent[0].command == "du -sh *"
        assert recent[1].command == "ls -la"

    def test_search(self, tmp_path, monkeypatch):
        monkeypatch.setattr("termi.history.HISTORY_FILE", tmp_path / "history.jsonl")
        h = History(limit=10)
        h.add("list files", "ls -la")
        h.add("find python files", "find . -name '*.py'")
        results = h.search("python")
        assert len(results) == 1
        assert "python" in results[0].query.lower() or "py" in results[0].command

    def test_clear(self, tmp_path, monkeypatch):
        monkeypatch.setattr("termi.history.HISTORY_FILE", tmp_path / "history.jsonl")
        h = History(limit=10)
        h.add("test", "echo test")
        h.clear()
        assert len(h.entries) == 0


# ---------------------------------------------------------------------------
# Bookmarks tests
# ---------------------------------------------------------------------------

class TestBookmarks:
    def test_add_and_get(self, tmp_path, monkeypatch):
        monkeypatch.setattr("termi.history.BOOKMARKS_FILE", tmp_path / "bookmarks.json")
        b = Bookmarks()
        b.add("deploy", "git push origin main", "Deploy to production")
        bm = b.get("deploy")
        assert bm is not None
        assert bm["command"] == "git push origin main"

    def test_remove(self, tmp_path, monkeypatch):
        monkeypatch.setattr("termi.history.BOOKMARKS_FILE", tmp_path / "bookmarks.json")
        b = Bookmarks()
        b.add("test", "echo test")
        assert b.remove("test") is True
        assert b.get("test") is None
        assert b.remove("nonexistent") is False


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestConfig:
    def test_defaults(self):
        assert "model" in _DEFAULT_CONFIG
        assert "ollama_url" in _DEFAULT_CONFIG
        assert _DEFAULT_CONFIG["stream"] is True

    def test_load_with_overrides(self):
        cfg = load_config(model="llama3", stream=False)
        assert cfg["model"] == "llama3"
        assert cfg["stream"] is False


# ---------------------------------------------------------------------------
# Context tests
# ---------------------------------------------------------------------------

class TestContext:
    def test_human_size(self):
        assert _human_size(0) == "0B"
        assert _human_size(1024) == "1.0KB"
        assert _human_size(1048576) == "1.0MB"

    def test_directory_listing(self):
        listing = get_directory_listing(5)
        assert isinstance(listing, str)


# ---------------------------------------------------------------------------
# Fallback tests
# ---------------------------------------------------------------------------

class TestFallback:
    def test_list_files(self):
        assert fallback_command("list files") == "ls -la"

    def test_large_files(self):
        cmd = fallback_command("find largest files")
        assert "find" in cmd and "size" in cmd

    def test_git_status(self):
        assert fallback_command("git status") == "git status"

    def test_disk_usage(self):
        cmd = fallback_command("show disk usage")
        assert "du" in cmd

    def test_free_space(self):
        assert fallback_command("show free space") == "df -h"


# ---------------------------------------------------------------------------
# Parse tests
# ---------------------------------------------------------------------------

class TestParse:
    def test_parse_cmd_json(self):
        assert _parse_cmd('{"cmd": "ls -la"}') == "ls -la"

    def test_parse_cmd_plain(self):
        result = _parse_cmd("ls -la")
        assert "ls" in result

    def test_parse_explanation_json(self):
        assert _parse_explanation('{"explanation": "Lists files"}') == "Lists files"

    def test_parse_empty(self):
        result = _parse_cmd("")
        assert result == ""
