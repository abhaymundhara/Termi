"""Configuration management for Termi.

Reads from ~/.config/termi/config.toml, merges with env vars and CLI flags.
"""
import os
import sys
import json
import platform
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]


_DEFAULT_CONFIG: Dict[str, Any] = {
    "model": "gemma2:2b",
    "ollama_url": "http://localhost:11434",
    "temperature": 0.1,
    "num_ctx": 4096,
    "num_predict": 512,
    "stream": True,
    "theme": "monokai",
    "safety_confirm": True,
    "history_limit": 500,
    "context_lines": 50,
    "clipboard_auto": False,
    "shell": os.environ.get("SHELL", "/bin/bash"),
    "backends": ["ollama"],
    "lmstudio_url": "http://localhost:1234",
    "llamacpp_url": "http://localhost:8080",
}

CONFIG_DIR = Path.home() / ".config" / "termi"
CONFIG_FILE = CONFIG_DIR / "config.toml"
HISTORY_FILE = CONFIG_DIR / "history.jsonl"
BOOKMARKS_FILE = CONFIG_DIR / "bookmarks.json"


def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _load_file_config() -> Dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        text = CONFIG_FILE.read_text(encoding="utf-8")
        if tomllib is not None:
            return tomllib.loads(text)
    except Exception:
        pass
    return {}


def _apply_env(cfg: Dict[str, Any]) -> None:
    env_map = {
        "TERMI_MODEL": "model",
        "OLLAMA_URL": "ollama_url",
        "TERMI_THEME": "theme",
        "TERMI_SHELL": "shell",
        "TERMI_STREAM": "stream",
        "TERMI_SAFETY": "safety_confirm",
        "LMSTUDIO_URL": "lmstudio_url",
        "LLAMACPP_URL": "llamacpp_url",
    }
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            if cfg_key in ("stream", "safety_confirm", "clipboard_auto"):
                cfg[cfg_key] = val.lower() in ("1", "true", "yes")
            elif cfg_key in ("num_ctx", "num_predict", "history_limit", "context_lines"):
                try:
                    cfg[cfg_key] = int(val)
                except ValueError:
                    pass
            elif cfg_key == "temperature":
                try:
                    cfg[cfg_key] = float(val)
                except ValueError:
                    pass
            else:
                cfg[cfg_key] = val


def load_config(**overrides: Any) -> Dict[str, Any]:
    _ensure_config_dir()
    cfg = dict(_DEFAULT_CONFIG)
    cfg.update(_load_file_config())
    _apply_env(cfg)
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    return cfg


def write_default_config() -> Path:
    _ensure_config_dir()
    if CONFIG_FILE.exists():
        return CONFIG_FILE
    lines = [
        "# Termi Configuration",
        "# https://github.com/abhaymundhara/Termi",
        "",
        'model = "gemma2:2b"',
        'ollama_url = "http://localhost:11434"',
        "temperature = 0.1",
        "num_ctx = 4096",
        "num_predict = 512",
        "stream = true",
        'theme = "monokai"',
        "safety_confirm = true",
        "history_limit = 500",
        "",
        "# LLM backends: ollama, lmstudio, llamacpp",
        'backends = ["ollama"]',
        'lmstudio_url = "http://localhost:1234"',
        'llamacpp_url = "http://localhost:8080"',
    ]
    CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return CONFIG_FILE


def get_system_info() -> Dict[str, str]:
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "shell": os.environ.get("SHELL", "unknown"),
        "user": os.environ.get("USER", os.environ.get("USERNAME", "unknown")),
        "home": str(Path.home()),
        "cwd": os.getcwd(),
    }
