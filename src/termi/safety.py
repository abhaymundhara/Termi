"""Command safety analysis for Termi.

Detects destructive/dangerous commands and warns the user before execution.
"""
import re
import shlex
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple


class RiskLevel(Enum):
    SAFE = "safe"
    CAUTION = "caution"
    DANGEROUS = "dangerous"
    CRITICAL = "critical"


@dataclass
class SafetyResult:
    level: RiskLevel
    reasons: List[str]
    suggestion: Optional[str] = None


# Patterns that indicate dangerous commands
_CRITICAL_PATTERNS = [
    (r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?(/|~|\$HOME)\b", "Deleting root or home directory"),
    (r"\bmkfs\b", "Formatting filesystem"),
    (r"\bdd\s+.*of=/dev/", "Writing directly to block device"),
    (r":(){ :|:& };:", "Fork bomb detected"),
    (r"\b>\s*/dev/sd[a-z]", "Overwriting block device"),
    (r"\bchmod\s+(-R\s+)?777\s+/", "Removing all permissions on root"),
    (r"\bchown\s+(-R\s+)?.*\s+/\s*$", "Changing ownership of root"),
]

_DANGEROUS_PATTERNS = [
    (r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*)", "Recursive or forced file deletion"),
    (r"\bsudo\s+rm\b", "Privileged file deletion"),
    (r"\bsudo\s+dd\b", "Privileged disk operation"),
    (r"\bkill\s+-9\b", "Force-killing process"),
    (r"\bkillall\b", "Killing multiple processes"),
    (r"\bsystemctl\s+(stop|disable|mask)\b", "Stopping/disabling system service"),
    (r"\biptables\s+-F\b", "Flushing firewall rules"),
    (r"\b>\s*/etc/", "Overwriting system config"),
    (r"\bcurl\s+.*\|\s*(sudo\s+)?\b(bash|sh|zsh)\b", "Piping URL to shell"),
    (r"\bwget\s+.*\|\s*(sudo\s+)?\b(bash|sh|zsh)\b", "Piping URL to shell"),
]

_CAUTION_PATTERNS = [
    (r"\brm\b", "File deletion"),
    (r"\bsudo\b", "Elevated privileges"),
    (r"\bmv\s+", "Moving files"),
    (r"\bchmod\b", "Changing permissions"),
    (r"\bchown\b", "Changing ownership"),
    (r"\bgit\s+push\s+(-f|--force)", "Force pushing to git"),
    (r"\bgit\s+reset\s+--hard", "Hard git reset"),
    (r"\bdrop\s+database\b", "Dropping database", re.IGNORECASE),
    (r"\btruncate\b", "Truncating table/file"),
    (r"\bshutdown\b", "System shutdown"),
    (r"\breboot\b", "System reboot"),
]


def analyze_command(cmd: str) -> SafetyResult:
    """Analyze a command for safety risks."""
    if not cmd or not cmd.strip():
        return SafetyResult(level=RiskLevel.SAFE, reasons=[])

    reasons: List[str] = []
    max_level = RiskLevel.SAFE

    for pattern, reason, *flags in _CRITICAL_PATTERNS:
        flag = flags[0] if flags else 0
        if re.search(pattern, cmd, flag):
            reasons.append(f"CRITICAL: {reason}")
            max_level = RiskLevel.CRITICAL

    if max_level != RiskLevel.CRITICAL:
        for pattern, reason, *flags in _DANGEROUS_PATTERNS:
            flag = flags[0] if flags else 0
            if re.search(pattern, cmd, flag):
                reasons.append(f"DANGER: {reason}")
                if max_level.value < RiskLevel.DANGEROUS.value or max_level == RiskLevel.SAFE:
                    max_level = RiskLevel.DANGEROUS

    if max_level == RiskLevel.SAFE:
        for pattern, reason, *flags in _CAUTION_PATTERNS:
            flag = flags[0] if flags else 0
            if re.search(pattern, cmd, flag):
                reasons.append(f"Caution: {reason}")
                max_level = RiskLevel.CAUTION

    suggestion = None
    if max_level == RiskLevel.CRITICAL:
        suggestion = "This command is extremely dangerous. Please reconsider."
    elif max_level == RiskLevel.DANGEROUS:
        suggestion = "Consider adding --dry-run or --interactive flag first."

    return SafetyResult(level=max_level, reasons=reasons, suggestion=suggestion)


def risk_color(level: RiskLevel) -> str:
    return {
        RiskLevel.SAFE: "termi.success",
        RiskLevel.CAUTION: "termi.warning",
        RiskLevel.DANGEROUS: "termi.error",
        RiskLevel.CRITICAL: "bold red on white",
    }.get(level, "white")
