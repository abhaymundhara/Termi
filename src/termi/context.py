"""Context gathering for Termi.

Builds context from the current environment: directory listing, git status,
recent commands, etc. This helps the LLM generate more relevant commands.
"""
import os
import subprocess
from pathlib import Path
from typing import Optional


def get_directory_listing(max_entries: int = 50) -> str:
    """Get current directory listing."""
    try:
        entries = sorted(Path.cwd().iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = []
        for entry in entries[:max_entries]:
            prefix = "d" if entry.is_dir() else "f"
            try:
                size = entry.stat().st_size if entry.is_file() else 0
                size_str = _human_size(size) if size else "-"
            except OSError:
                size_str = "?"
            lines.append(f"  [{prefix}] {entry.name:<40} {size_str}")
        if len(entries) > max_entries:
            lines.append(f"  ... and {len(entries) - max_entries} more")
        return "\n".join(lines) if lines else "  (empty directory)"
    except OSError:
        return "  (cannot read directory)"


def get_git_status() -> Optional[str]:
    """Get git status if in a git repo."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--branch"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip() or "(clean)"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def get_git_branch() -> Optional[str]:
    """Get current git branch name."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def get_recent_shell_history(n: int = 10) -> str:
    """Get recent shell history entries."""
    histfile = os.environ.get("HISTFILE")
    if not histfile:
        shell = os.environ.get("SHELL", "")
        if "zsh" in shell:
            histfile = os.path.expanduser("~/.zsh_history")
        elif "bash" in shell:
            histfile = os.path.expanduser("~/.bash_history")
    if not histfile or not os.path.exists(histfile):
        return "(no shell history available)"
    try:
        with open(histfile, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        recent = [l.strip().split(";", 1)[-1] if ";" in l else l.strip()
                  for l in lines[-n:] if l.strip()]
        return "\n".join(f"  {l}" for l in recent) if recent else "(empty)"
    except OSError:
        return "(cannot read history)"


def build_context(max_entries: int = 50) -> str:
    """Build full context string for LLM prompt augmentation."""
    parts = [f"CWD: {os.getcwd()}"]

    git_branch = get_git_branch()
    if git_branch:
        parts.append(f"Git branch: {git_branch}")

    git_status = get_git_status()
    if git_status:
        parts.append(f"Git status:\n{git_status}")

    listing = get_directory_listing(max_entries)
    parts.append(f"Directory listing:\n{listing}")

    return "\n\n".join(parts)


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PB"
