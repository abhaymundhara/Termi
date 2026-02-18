"""Heuristic fallback commands when no LLM is available."""
import re
import shlex

_RE_TOP_N = re.compile(r"top\s+(\d+)")
_RE_SIZE_PATTERN = re.compile(r"(\d+)\s*(m|mb|g|gb)\b")
_RE_QUOTED_TEXT = re.compile(r'"([^"]+)"|\'([^\']+)\'')
_RE_FILE_EXT = re.compile(r'\.(\w+)$')
_RE_NAME_PATTERN = re.compile(r'name\s+([\w\-\.]+)')


def fallback_command(nl: str) -> str:
    """Generate a best-guess command from natural language without an LLM."""
    s = nl.strip().lower()

    # Large files
    if ("large" in s or "largest" in s or "big" in s or "biggest" in s or "huge" in s) and (
        "file" in s or "files" in s
    ):
        m = _RE_TOP_N.search(s)
        top = int(m.group(1)) if m else 20
        thresh = "+100M"
        m2 = _RE_SIZE_PATTERN.search(s)
        if m2:
            qty, unit = m2.groups()
            unit = unit.lower()
            if unit in ("m", "mb"):
                thresh = f"+{qty}M"
            elif unit in ("g", "gb"):
                thresh = f"+{qty}G"
        return f"find . -type f -size {thresh} -print0 | xargs -0 ls -lh | sort -k5 -h | tail -n {top}"

    if "free space" in s or "how much space" in s:
        return "df -h"

    if ("disk" in s and "usage" in s) or ("space" in s and "used" in s):
        return "du -sh * | sort -rh"

    if ("search" in s or "find" in s) and ("text" in s or "string" in s or " for " in s):
        m = _RE_QUOTED_TEXT.search(nl)
        term = m.group(1) if m and m.group(1) is not None else (m.group(2) if m else None)
        if term:
            return f"grep -RIn {shlex.quote(term)} ."
        return "grep -RIn ."

    if "find" in s and ("file" in s or "files" in s or "name" in s):
        m = _RE_FILE_EXT.search(s)
        if m:
            ext = m.group(1)
            return f"find . -type f -iname '*.{ext}'"
        m = _RE_NAME_PATTERN.search(s)
        if m:
            pattern = m.group(1)
            return f"find . -type f -iname {shlex.quote(pattern)}"
        return "find . -type f -maxdepth 3 -print"

    if "process" in s or "processes" in s or "running apps" in s:
        return "ps aux | head -30"

    if "open ports" in s or ("ports" in s and "listen" in s):
        return "lsof -i -P | grep LISTEN"

    if "ip address" in s or "my ip" in s:
        return "ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null || curl -s ifconfig.me"

    if "system info" in s or "os version" in s:
        return "uname -a && (sw_vers 2>/dev/null || cat /etc/os-release 2>/dev/null || echo 'Unknown OS')"

    if s.startswith("git status") or "git status" in s:
        return "git status"
    if "pull" in s and "git" in s:
        return "git pull --ff-only"
    if "show branches" in s or ("git" in s and "branch" in s):
        return "git branch -vv"
    if "git log" in s or "commit history" in s:
        return "git log --oneline -20"

    if ("list" in s or "show" in s) and ("files" in s or "dir" in s or "directory" in s):
        return "ls -la"

    if "cpu" in s and ("usage" in s or "load" in s):
        return "top -l 1 -n 0 2>/dev/null || uptime"

    if "memory" in s or "ram" in s:
        return "free -h 2>/dev/null || vm_stat 2>/dev/null || echo 'Use Activity Monitor'"

    if "network" in s and ("connection" in s or "interface" in s):
        return "ifconfig 2>/dev/null || ip addr show"

    if "docker" in s and ("container" in s or "running" in s):
        return "docker ps"

    if "env" in s and ("variable" in s or "var" in s):
        return "env | sort | head -50"

    if "count" in s and ("file" in s or "lines" in s):
        return "find . -type f | wc -l"

    if "compress" in s or "zip" in s or "tar" in s:
        return "tar -czvf archive.tar.gz ."

    if "extract" in s or "unzip" in s or "untar" in s:
        return "tar -xzvf archive.tar.gz"

    return "ls -la"
