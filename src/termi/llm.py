"""Multi-backend LLM client for Termi.

Supports: Ollama, LM Studio, llama.cpp server.
All local, zero cloud dependencies.
"""
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import platform
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from rich.console import Console

from .config import get_system_info

console = Console()

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are Termi, a terminal copilot. Convert the user's natural-language request into ONE safe shell command.

System: {os} {arch} | Shell: {shell} | CWD: {cwd}

Output FORMAT (STRICT):
- Return a single-line JSON object, nothing else.
- For commands: {{"cmd": "<command>"}}
- For explanations: {{"explanation": "<2-3 sentences>"}}
- No code fences, no backticks, no extra keys.

Safety rules:
- Never output commands that delete the home directory or root filesystem.
- For destructive operations (rm -rf, mkfs, dd), add a confirmation flag or --dry-run when possible.
- Prefer non-destructive alternatives when the intent is ambiguous.

Examples:
User: list files
Assistant: {{"cmd": "ls -la"}}

User: show disk usage by folder
Assistant: {{"cmd": "du -sh * | sort -rh | head -20"}}

User: explain `find . -type f -size +100M`
Assistant: {{"explanation": "Searches the current directory recursively for regular files larger than 100 MB."}}
"""

CHAT_PROMPT = (
    "You are Termi, a concise terminal copilot. Answer the user's question plainly. "
    "If they ask about commands, include a short example."
)

PLAN_PROMPT = (
    "You are Termi, a terminal copilot that plans multi-step tasks using shell commands. "
    "Given a high-level task, produce a short JSON plan. Each step has 'thought' and 'cmd'. "
    'Output STRICT JSON: {{"plan": [{{"thought": str, "cmd": str}}, ...], "notes": str}}. '
    "No code fences."
)


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def is_port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((host, port))
            return True
    except (OSError, socket.error):
        return False


def _parse_url(url: str) -> Tuple[str, int]:
    parsed = urllib.parse.urlparse(url)
    return (parsed.hostname or "localhost", parsed.port or 11434)


# ---------------------------------------------------------------------------
# Ollama management
# ---------------------------------------------------------------------------

def ensure_ollama_installed() -> None:
    if shutil.which("ollama"):
        return
    console.print("[termi.warning]Ollama not found.[/]")
    ans = console.input("[termi.prompt]Install Ollama now? [Y/n]: [/]").strip().lower()
    if ans in ("", "y", "yes"):
        system = platform.system()
        if system == "Darwin" and shutil.which("brew"):
            cmd = "brew install ollama"
        elif system == "Linux":
            cmd = "curl -fsSL https://ollama.com/install.sh | sh"
        else:
            console.print("[termi.info]Download Ollama from https://ollama.com/download[/]")
            sys.exit(1)
        console.print(f"[termi.info]Running: {cmd}[/]")
        rc = subprocess.run(cmd, shell=True).returncode
        if rc != 0:
            console.print(f"[termi.error]Installation failed (exit {rc})[/]")
            sys.exit(rc)
    else:
        console.print("[termi.error]Ollama required. Exiting.[/]")
        sys.exit(1)


def ensure_ollama_running(url: str) -> None:
    host, port = _parse_url(url)
    if is_port_open(host, port):
        return

    console.print(f"[termi.warning]Ollama not running on {host}:{port}[/]")
    ans = console.input("[termi.prompt]Start Ollama server? [Y/n]: [/]").strip().lower()
    if ans not in ("", "y", "yes"):
        console.print("[termi.error]Ollama server required. Exiting.[/]")
        sys.exit(1)

    if platform.system() == "Darwin":
        subprocess.Popen(
            ["osascript", "-e",
             'tell application "Terminal" to do script "/bin/zsh -lc \'ollama serve\'"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            "nohup ollama serve >/dev/null 2>&1 &",
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    for i in range(20):
        if is_port_open(host, port):
            return
        time.sleep(min(0.2 * (1.5 ** (i // 3)), 2.0))

    console.print(f"[termi.error]Ollama did not start on {host}:{port}[/]")
    sys.exit(1)


def ensure_model_available(model: str) -> None:
    if not shutil.which("ollama"):
        return
    try:
        out = subprocess.check_output(["ollama", "list"], text=True, stderr=subprocess.PIPE)
        if model in out:
            return
    except (subprocess.CalledProcessError, OSError):
        pass

    console.print(f"[termi.warning]Model '{model}' not found locally.[/]")
    ans = console.input(f"[termi.prompt]Pull '{model}' now? [Y/n]: [/]").strip().lower()
    if ans in ("", "y", "yes"):
        rc = subprocess.run(f"ollama pull {shlex.quote(model)}", shell=True).returncode
        if rc != 0:
            console.print(f"[termi.error]Failed to pull {model}[/]")
    else:
        console.print("[termi.warning]Model may be missing; generation could fail.[/]")


def list_ollama_models() -> List[str]:
    if not shutil.which("ollama"):
        return []
    try:
        out = subprocess.check_output(["ollama", "list"], text=True, stderr=subprocess.PIPE)
        models = []
        for line in out.strip().splitlines()[1:]:
            parts = line.split()
            if parts:
                models.append(parts[0])
        return models
    except (subprocess.CalledProcessError, OSError):
        return []


# ---------------------------------------------------------------------------
# Generic LLM call (Ollama / LM Studio / llama.cpp)
# ---------------------------------------------------------------------------

def _build_ollama_payload(
    messages: List[Dict[str, str]], model: str, cfg: Dict[str, Any], stream: bool = False,
) -> Dict[str, Any]:
    return {
        "model": model,
        "messages": messages,
        "stream": stream,
        "options": {
            "temperature": cfg.get("temperature", 0.1),
            "top_p": 0.9,
            "repeat_penalty": 1.05,
            "num_ctx": cfg.get("num_ctx", 4096),
            "num_predict": cfg.get("num_predict", 512),
        },
    }


def _build_openai_payload(
    messages: List[Dict[str, str]], model: str, cfg: Dict[str, Any], stream: bool = False,
) -> Dict[str, Any]:
    return {
        "model": model,
        "messages": messages,
        "stream": stream,
        "temperature": cfg.get("temperature", 0.1),
        "max_tokens": cfg.get("num_predict", 512),
    }


def call_llm(
    messages: List[Dict[str, str]],
    model: str,
    cfg: Dict[str, Any],
) -> str:
    """Call LLM with automatic backend fallback. Returns full response text."""
    backends = cfg.get("backends", ["ollama"])
    last_err = None

    for backend in backends:
        try:
            if backend == "ollama":
                url = cfg.get("ollama_url", "http://localhost:11434")
                endpoint = f"{url.rstrip('/')}/api/chat"
                payload = _build_ollama_payload(messages, model, cfg, stream=False)
            elif backend == "lmstudio":
                url = cfg.get("lmstudio_url", "http://localhost:1234")
                endpoint = f"{url.rstrip('/')}/v1/chat/completions"
                payload = _build_openai_payload(messages, model, cfg, stream=False)
            elif backend == "llamacpp":
                url = cfg.get("llamacpp_url", "http://localhost:8080")
                endpoint = f"{url.rstrip('/')}/v1/chat/completions"
                payload = _build_openai_payload(messages, model, cfg, stream=False)
            else:
                continue

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                endpoint, data=data, headers={"Content-Type": "application/json"}
            )

            with urllib.request.urlopen(req, timeout=180) as r:
                obj = json.loads(r.read().decode("utf-8"))

            if backend == "ollama":
                return obj.get("message", {}).get("content", "").strip()
            else:
                choices = obj.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
                return ""

        except Exception as e:
            last_err = e
            continue

    if last_err:
        raise last_err
    raise RuntimeError("No LLM backend available")


def stream_llm(
    messages: List[Dict[str, str]],
    model: str,
    cfg: Dict[str, Any],
) -> Generator[str, None, None]:
    """Stream LLM response token by token."""
    backends = cfg.get("backends", ["ollama"])

    for backend in backends:
        try:
            if backend == "ollama":
                url = cfg.get("ollama_url", "http://localhost:11434")
                endpoint = f"{url.rstrip('/')}/api/chat"
                payload = _build_ollama_payload(messages, model, cfg, stream=True)
            elif backend in ("lmstudio", "llamacpp"):
                base = cfg.get(f"{backend}_url", "http://localhost:1234")
                endpoint = f"{base.rstrip('/')}/v1/chat/completions"
                payload = _build_openai_payload(messages, model, cfg, stream=True)
            else:
                continue

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                endpoint, data=data, headers={"Content-Type": "application/json"}
            )
            resp = urllib.request.urlopen(req, timeout=180)

            buffer = b""
            while True:
                chunk = resp.read(1)
                if not chunk:
                    break
                buffer += chunk
                if chunk == b"\n":
                    line = buffer.decode("utf-8", errors="replace").strip()
                    buffer = b""
                    if not line:
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    if line == "[DONE]":
                        break

                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if backend == "ollama":
                        token = obj.get("message", {}).get("content", "")
                        if obj.get("done", False):
                            if token:
                                yield token
                            break
                    else:
                        choices = obj.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            token = delta.get("content", "")
                        else:
                            token = ""

                    if token:
                        yield token

            resp.close()
            return

        except Exception:
            continue


# ---------------------------------------------------------------------------
# High-level helpers
# ---------------------------------------------------------------------------

def build_system_prompt(cfg: Dict[str, Any]) -> str:
    info = get_system_info()
    return SYSTEM_PROMPT.format(**info)


def generate_command(query: str, model: str, cfg: Dict[str, Any], context: str = "") -> str:
    system = build_system_prompt(cfg)
    if context:
        system += f"\n\nAdditional context:\n{context}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": query.strip()},
    ]
    return call_llm(messages, model, cfg)


def generate_explanation(query: str, model: str, cfg: Dict[str, Any]) -> str:
    system = build_system_prompt(cfg)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Explain this command: {query.strip()}\n\n(Return explanation only, no command.)"},
    ]
    return call_llm(messages, model, cfg)


def generate_chat(
    message: str, model: str, cfg: Dict[str, Any],
    history: Optional[List[Dict]] = None,
) -> str:
    msgs: List[Dict[str, str]] = [{"role": "system", "content": CHAT_PROMPT}]
    if history:
        msgs.extend(history[-10:])
    msgs.append({"role": "user", "content": message.strip()})
    return call_llm(msgs, model, cfg)


def stream_chat(
    message: str, model: str, cfg: Dict[str, Any],
    history: Optional[List[Dict]] = None,
) -> Generator[str, None, None]:
    msgs: List[Dict[str, str]] = [{"role": "system", "content": CHAT_PROMPT}]
    if history:
        msgs.extend(history[-10:])
    msgs.append({"role": "user", "content": message.strip()})
    yield from stream_llm(msgs, model, cfg)


def generate_plan(task: str, model: str, cfg: Dict[str, Any]) -> Tuple[List[Dict[str, str]], str]:
    messages = [
        {"role": "system", "content": PLAN_PROMPT},
        {"role": "user", "content": task.strip()},
    ]
    raw = call_llm(messages, model, cfg)
    return _parse_plan(raw)


def _parse_plan(text: str) -> Tuple[List[Dict[str, str]], str]:
    t = text.strip().strip("`")
    try:
        obj = json.loads(t)
        if isinstance(obj, dict) and "plan" in obj:
            steps = []
            for it in obj["plan"]:
                if isinstance(it, dict) and "cmd" in it:
                    steps.append({
                        "thought": str(it.get("thought", "")).strip(),
                        "cmd": str(it.get("cmd", "")).strip(),
                    })
            return steps, str(obj.get("notes", "")).strip()
    except (json.JSONDecodeError, ValueError):
        pass
    return [], ""
