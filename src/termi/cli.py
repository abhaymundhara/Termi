#!/usr/bin/env python3
import os
import sys
import json
import shlex
import shutil
import subprocess
import urllib.request
import urllib.error
import urllib.parse
import time
import platform
import socket
import re

try:
    import importlib.metadata as _im
    __version__ = _im.version("termi-copilot")
except (ImportError, Exception):
    __version__ = "dev"

# Default Ollama REST endpoint (we'll swap to /api/chat in requests)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
# Fast default model; override with --model or TERMI_MODEL
DEFAULT_MODEL = os.environ.get("TERMI_MODEL", "gemma2:2b")
SHELL = os.environ.get("SHELL", "/bin/zsh")

# Cache parsed URL to avoid repeated parsing
_PARSED_OLLAMA_URL = urllib.parse.urlparse(OLLAMA_URL)
OLLAMA_HOST = _PARSED_OLLAMA_URL.hostname or "localhost"
OLLAMA_PORT = _PARSED_OLLAMA_URL.port or 11434

# --- Core prompts -------------------------------------------------------------------

SYSTEM_PROMPT = """You are a terminal copilot. Convert the user's natural-language request into ONE safe, POSIX-compatible macOS (zsh) shell command.

Output FORMAT (STRICT):
- ALWAYS return a single-line JSON object.
- For commands: {"cmd": "<the command>"}
- For explanations: {"explanation": "<2-3 sentences>"}
- No code fences, no backticks, no extra keys, no prose.

Guidelines:
- Prefer non-destructive commands unless the user clearly asks otherwise.
- Use common tools (ls, grep, sed, awk, find, du, df, curl, git, python, node, etc.).
- Assume current working directory is the user's project folder.
- If ambiguous, choose a reasonable default and still return a command.

Examples:
User: list files
Assistant: {"cmd": "ls -la"}

User: show largest files
Assistant: {"cmd": "find . -type f -size +100M -print0 | xargs -0 ls -lh | sort -k5 -h | tail -n 20"}

User: explain `find . -type f -size +100M`
Assistant: {"explanation": "Searches the current directory for regular files larger than 100 MB and lists them."}
"""

# For general chat answers
CHAT_PROMPT = (
    "You are a concise, helpful terminal copilot. Answer the user's question plainly. "
    "If they ask about commands, include a short example, otherwise just answer."
)

# For planner (multi-step)
PLAN_PROMPT = (
    "You are a terminal copilot that plans and executes tasks using shell commands. "
    "Given a high-level task, produce a short JSON plan with steps. Each step must have a 'thought' and a 'cmd'. "
    "Output STRICT JSON on one line: {\"plan\": [{\"thought\": str, \"cmd\": str}, ...], \"notes\": str}. No code fences."
)

# --- Help banner --------------------------------------------------------------------

HELP = f"""\
Termi — Your local terminal copilot

Usage:
  termi                      # interactive mode
  termi "text here"          # one-shot: NL → command → confirm → run
  termi --chat "message"     # general chat/answer (no command)
  termi --plan "task"        # multi-step plan → confirm each → run
  termi --auto --plan "task" # auto-run a planned sequence (no prompts)
  termi --explain "cmd"      # explain a command without running it
  termi --dry-run "..."      # NL → command (show only, don't run)
  termi --model <name>       # override model (default: {DEFAULT_MODEL})

Notes:
  • On first run, Termi checks for Ollama and offers to install/start it for you.
  • If the default model is missing, Termi offers to pull it.
  • Use --chat for general questions; use --plan for multi-step tasks with reasoning.

Env:
  OLLAMA_URL=http://localhost:11434/api/generate
  TERMI_MODEL={DEFAULT_MODEL}
"""

def print_err(*a): print(*a, file=sys.stderr)

# --- Network / environment helpers --------------------------------------------------

def is_port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    """Check if a port is open with proper error handling."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((host, port))
            return True
    except (OSError, socket.error):
        return False

def prompt_install_ollama() -> bool:
    print("Ollama is not installed.")
    return ask_yes_no("Install Ollama now? This may require sudo.", default="y")

def install_ollama() -> int:
    system = platform.system()
    if system == "Darwin" and shutil.which("brew"):
        cmd = "brew install ollama"
    else:
        cmd = "curl -fsSL https://ollama.com/install.sh | sh"
    print(f"Installing via: {cmd}")
    return run_command(cmd)

def run_in_new_terminal_mac(command: str) -> int:
    """Open a new macOS Terminal window running the given command."""
    # Use proper escaping for AppleScript
    escaped_cmd = command.replace('\\', '\\\\').replace('"', '\\"')
    osa = (
        'osascript -e '
        '"tell application \\"Terminal\\" to do script \\"' + escaped_cmd + '\\""'
    )
    return run_command(osa)

def ensure_ollama_installed() -> None:
    if shutil.which("ollama"):
        return
    if not prompt_install_ollama():
        print_err("✗ Ollama not installed. Termi needs Ollama to work. Exiting.")
        sys.exit(1)
    rc = install_ollama()
    if rc != 0:
        print_err("✗ Ollama installation failed (exit code", rc, ")")
        sys.exit(rc)

def ensure_ollama_running() -> None:
    if is_port_open(OLLAMA_HOST, OLLAMA_PORT):
        return

    print("Ollama server not detected on", f"{OLLAMA_HOST}:{OLLAMA_PORT}")
    if not ask_yes_no("Start Ollama server in a new Terminal window?", default="y"):
        print_err("✗ Ollama server not running. Exiting.")
        sys.exit(1)

    if platform.system() == "Darwin":
        rc = run_in_new_terminal_mac(f"/bin/zsh -lc 'ollama serve'")
        if rc != 0:
            print_err("✗ Failed to spawn ollama serve (osascript exit", rc, ")")
            sys.exit(rc)
    else:
        rc = run_command("nohup ollama serve >/dev/null 2>&1 &")
        if rc != 0:
            print_err("✗ Failed to start ollama serve (exit", rc, ")")
            sys.exit(rc)

    # Wait for server with exponential backoff
    # Using stepped exponential: increases every 3 iterations to balance responsiveness vs waiting
    for i in range(15):
        if is_port_open(OLLAMA_HOST, OLLAMA_PORT):
            return
        time.sleep(min(0.2 * (2 ** (i // 3)), 2.0))  # Capped at 2s to avoid excessive delays
    print_err("✗ Ollama server did not become ready on", f"{OLLAMA_HOST}:{OLLAMA_PORT}")
    sys.exit(1)

def ensure_model_available(model: str) -> None:
    """Check if model is available, with improved error handling."""
    if not shutil.which("ollama"):
        return
    try:
        out = subprocess.check_output(["ollama", "list"], text=True, stderr=subprocess.DEVNULL)
        if model in out:
            return
    except (subprocess.CalledProcessError, OSError):
        pass
    
    print(f"Model '{model}' not found locally.")
    if ask_yes_no(f"Pull '{model}' now?", default="y"):
        rc = run_command(f"ollama pull {shlex.quote(model)}")
        if rc != 0:
            print_err("✗ Failed to pull model", model, "(exit", rc, ")")
    else:
        print_err("Skipping model pull; generation may fail if the model is missing.")

# --- Parsing helpers ----------------------------------------------------------------

def _parse_cmd_from_response(text: str) -> str:
    """Parse command from LLM response, handling JSON and fallback to plain text."""
    t = text.strip()
    if not t:
        return ""
    
    # Remove backticks if present
    if t.startswith('`'):
        t = t.strip('`')
    
    try:
        obj = json.loads(t)
        if isinstance(obj, dict) and "cmd" in obj and isinstance(obj["cmd"], str):
            return obj["cmd"].strip()
    except (json.JSONDecodeError, ValueError):
        pass
    
    # Fallback: return first non-empty line
    for line in t.splitlines():
        line = line.strip()
        if line:
            return line
    return ""

def _parse_explanation_from_response(text: str) -> str:
    """Parse explanation from LLM response, handling JSON and fallback to plain text."""
    t = text.strip()
    if not t:
        return ""
    
    # Remove backticks if present
    if t.startswith('`'):
        t = t.strip('`')
    
    try:
        obj = json.loads(t)
        if isinstance(obj, dict) and "explanation" in obj and isinstance(obj["explanation"], str):
            return obj["explanation"].strip()
    except (json.JSONDecodeError, ValueError):
        pass
    
    return t

def _parse_plan_from_response(text: str):
    """Parse plan from LLM response with improved error handling."""
    t = text.strip()
    if not t:
        return [], ""
    
    # Remove backticks if present
    if t.startswith('`'):
        t = t.strip('`')
    
    try:
        obj = json.loads(t)
        if isinstance(obj, dict) and "plan" in obj and isinstance(obj["plan"], list):
            steps = []
            for it in obj["plan"]:
                if isinstance(it, dict) and "cmd" in it:
                    steps.append({
                        "thought": str(it.get("thought", "")).strip(),
                        "cmd": str(it.get("cmd", "")).strip(),
                    })
            notes = str(obj.get("notes", "")).strip()
            return steps, notes
    except (json.JSONDecodeError, ValueError):
        pass
    
    return [], ""

# --- Simple shell helpers ------------------------------------------------------------

def which_exists(token: str) -> bool:
    return shutil.which(token) is not None

def looks_like_command(s: str) -> bool:
    """Check if string looks like a valid command with improved validation."""
    if not s or not s.strip():
        return False
    
    try:
        parts = shlex.split(s)
    except ValueError:
        return False
    
    return len(parts) > 0 and which_exists(parts[0])

def run_command(cmd: str) -> int:
    """Execute a shell command with proper error handling."""
    if not cmd or not cmd.strip():
        print_err("✗ Empty command, nothing to run.")
        return 1
    
    try:
        p = subprocess.run(cmd, shell=True, executable=SHELL)
        return p.returncode
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print_err(f"✗ Error executing command: {e}")
        return 1

def ask_yes_no(prompt: str, default="n") -> bool:
    """Ask user for yes/no confirmation with proper error handling."""
    prompt_full = f"{prompt} [{'Y/n' if default=='y' else 'y/N'}]: "
    try:
        ans = input(prompt_full).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    
    if not ans:
        ans = default
    return ans in ("y", "yes")

# --- Heuristic fallback (only when Ollama is unavailable) ----------------------------

# Pre-compiled regex patterns for efficiency
_RE_TOP_N = re.compile(r"top\s+(\d+)")
_RE_SIZE_PATTERN = re.compile(r"(\d+)\s*(m|mb|g|gb)\b")
_RE_QUOTED_TEXT = re.compile(r'"([^"]+)"|\'([^\']+)\'')
_RE_FILE_EXT = re.compile(r'\.(\w+)$')
_RE_NAME_PATTERN = re.compile(r'name\s+([\w\-\.]+)')

def _fallback_command(nl: str) -> str:
    """Generate fallback command based on heuristics when LLM is unavailable."""
    s = nl.strip().lower()
    
    # Large files first
    if ("large" in s or "largest" in s or "big" in s or "biggest" in s or "huge" in s) and ("file" in s or "files" in s):
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
    
    # Disk usage
    if ("disk" in s and "usage" in s) or ("space" in s and ("used" in s or "free" in s)):
        return "du -sh * | sort -h"
    
    # Free space
    if "free space" in s or ("how much space" in s):
        return "df -h"
    
    # Search text
    if ("search" in s or "find" in s) and ("text" in s or "string" in s or " for " in s):
        m = _RE_QUOTED_TEXT.search(nl)
        term = m.group(1) if m and m.group(1) is not None else (m.group(2) if m else None)
        if term:
            return f"grep -RIn {shlex.quote(term)} ."
        return "grep -RIn ."
    
    # Find by extension/name
    if "find" in s and ("file" in s or "files" in s or "name" in s or s.strip().endswith(('.py', '.js', '.txt'))):
        m = _RE_FILE_EXT.search(s)
        if m:
            ext = m.group(1)
            return f"find . -type f -iname '*.{ext}'"
        
        m = _RE_NAME_PATTERN.search(s)
        if m:
            pattern = m.group(1)
            return f"find . -type f -iname {shlex.quote(pattern)}"
        return "find . -type f -maxdepth 3 -print"
    
    # Processes
    if "process" in s or "processes" in s or "running apps" in s:
        return "ps aux | less"
    
    # Ports
    if "open ports" in s or ("ports" in s and "listen" in s):
        return "lsof -i -P | grep LISTEN"
    
    # IP address
    if "ip address" in s or "my ip" in s:
        return "ipconfig getifaddr en0 || ipconfig getifaddr en1 || hostname -I"
    
    # System info
    if "system info" in s or "os version" in s:
        return "sw_vers && uname -a"
    
    # Git basics
    if s.startswith("git status") or "git status" in s:
        return "git status"
    if "pull" in s and "git" in s:
        return "git pull --ff-only"
    if "show branches" in s or ("git" in s and "branch" in s):
        return "git branch -vv"
    
    # Generic list (default)
    if ("list" in s or "show" in s) and ("files" in s or "dir" in s or "directory" in s or "here" in s or "current" in s):
        return "ls -la"
    
    return "ls -la"

# --- LLM calls ----------------------------------------------------------------------

def call_ollama(prompt: str, model: str, explain: bool=False) -> str:
    """Call Ollama API with improved error handling and caching."""
    # Ensure server reachable; start if needed
    if not is_port_open(OLLAMA_HOST, OLLAMA_PORT):
        print_err("Ollama not reachable; attempting to start server...")
        ensure_ollama_running()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt.strip()},
    ]
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "top_p": 0.9,
            "repeat_penalty": 1.05,
            "num_ctx": 2048,
            "num_predict": 64,
            "seed": 7,
        },
    }

    def _do_request(payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            OLLAMA_URL.replace('/api/generate','/api/chat'),
            data=data,
            headers={"Content-Type":"application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            obj = json.loads(r.read().decode("utf-8"))
            return obj.get("message", {}).get("content", "").strip()

    try:
        resp = _do_request(payload)
        text = _parse_explanation_from_response(resp) if explain else _parse_cmd_from_response(resp)
        if text:
            return text
        # Retry with a stronger instruction if empty
        payload_retry = dict(payload)
        payload_retry["messages"] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (prompt.strip() + "\n\nReturn JSON only.")},
        ]
        resp2 = _do_request(payload_retry)
        return _parse_explanation_from_response(resp2) if explain else _parse_cmd_from_response(resp2)
    except urllib.error.URLError as e:
        print_err("✗ Could not reach Ollama at", OLLAMA_URL)
        print_err("  Make sure Ollama is running: `ollama serve` and you pulled the model:", model)
        raise e

def call_ollama_chat(message: str, model: str) -> str:
    """Call Ollama for chat with optimized connection checking."""
    if not is_port_open(OLLAMA_HOST, OLLAMA_PORT):
        print_err("Ollama not reachable; attempting to start server...")
        ensure_ollama_running()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": CHAT_PROMPT},
            {"role": "user", "content": message.strip()},
        ],
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 256},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL.replace('/api/generate','/api/chat'), data=data, headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        obj = json.loads(r.read().decode("utf-8"))
        return obj.get("message", {}).get("content", "").strip()

def run_plan(task: str, model: str, auto: bool=False, dry_run: bool=False) -> int:
    """Execute a multi-step plan with improved error handling."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": PLAN_PROMPT},
            {"role": "user", "content": task.strip()},
        ],
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 256},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL.replace('/api/generate','/api/chat'), data=data, headers={"Content-Type":"application/json"})
    
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            obj = json.loads(r.read().decode("utf-8"))
            content = obj.get("message", {}).get("content", "").strip()
    except urllib.error.URLError as e:
        print_err("✗ Failed to reach Ollama for planning:", str(e))
        return 1

    steps, notes = _parse_plan_from_response(content)
    if not steps:
        print_err("✗ Planner returned no steps. Try rephrasing or a bigger model.")
        return 1

    print("Plan:")
    for i, st in enumerate(steps, 1):
        print(f"  {i}. {st['thought'] or '(step)'}\n     → {st['cmd']}")
    if notes:
        print(f"Notes: {notes}")

    rc_final = 0
    for i, st in enumerate(steps, 1):
        cmd = st["cmd"]
        if not cmd:
            continue
        if dry_run:
            print(f"[dry-run] step {i}: {cmd}")
            continue
        if not auto:
            if not ask_yes_no(f"Run step {i}? {cmd}", default="y"):
                print("Skipped.")
                continue
        print(f"\n▶ step {i}: {cmd}")
        rc = run_command(cmd)
        rc_final = rc if rc != 0 else rc_final
        if rc != 0 and not ask_yes_no("A step failed. Continue?", default="n"):
            break
    return rc_final

# --- Core flows ---------------------------------------------------------------------

def explain(cmd: str, model: str):
    prompt = f"Explain this command clearly and concisely:\n\n{cmd}\n\n(Per rules: return only a short explanation, no command.)"
    explanation = call_ollama(prompt, model, explain=True)
    print(explanation)

def one_shot(args):
    """Handle one-shot command execution with improved error handling."""
    model = DEFAULT_MODEL
    # Early help/version
    if any(a in ("-h", "--help") for a in args):
        print(HELP); sys.exit(0)
    if any(a in ("-v", "-V", "--version") for a in args):
        print(f"termi {__version__}"); sys.exit(0)

    dry = False
    if "--model" in args:
        i = args.index("--model")
        try:
            model = args[i+1]
        except IndexError:
            print_err("Missing model name after --model"); sys.exit(2)
        args = args[:i] + args[i+2:]
    if "--dry-run" in args:
        dry = True
        args.remove("--dry-run")

    auto = False
    if "--auto" in args:
        auto = True
        args.remove("--auto")

    if "--explain" in args:
        args.remove("--explain")
        text = " ".join(args).strip()
        if not text:
            print(HELP); sys.exit(0)
        try:
            explain(text, model)
        except Exception as e:
            print_err(f"✗ Error during explanation: {e}")
            sys.exit(1)
        return

    if "--chat" in args:
        args.remove("--chat")
        text = " ".join(args).strip()
        if not text:
            print(HELP); sys.exit(0)
        try:
            reply = call_ollama_chat(text, model)
        except Exception as e:
            print_err(f"✗ Ollama unavailable; cannot chat without LLM. Error: {e}")
            sys.exit(1)
        print(reply)
        return

    if "--plan" in args:
        args.remove("--plan")
        text = " ".join(args).strip()
        if not text:
            print(HELP); sys.exit(0)
        try:
            rc = run_plan(text, model, auto=auto, dry_run=dry)
        except Exception as e:
            print_err(f"✗ Ollama unavailable; planner requires the LLM. Error: {e}")
            sys.exit(1)
        sys.exit(rc)

    text = " ".join(args).strip()
    if not text:
        print(HELP); sys.exit(0)

    if looks_like_command(text):
        returncode = run_command(text)
        sys.exit(returncode)

    # NL → command (fallback only if Ollama unavailable)
    try:
        cmd = call_ollama(text, model)
    except Exception:
        cmd = _fallback_command(text)
        print_err("ℹ︎ Ollama unavailable; using fallback command.")
    
    if not cmd:
        print_err("✗ The model returned no command. Try rephrasing or switch models with --model.")
        sys.exit(1)
    
    print(f"Proposed: {cmd}")
    if dry or not ask_yes_no("Run this?", default="y"):
        print("Skipped."); return
    sys.exit(run_command(cmd))

def interactive():
    """Run interactive mode with improved error handling."""
    print("Termi (local LLM copilot). Type natural language or a command. Type :help, :model, :quit.")
    model = DEFAULT_MODEL
    while True:
        try:
            s = input("termi> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        
        if not s:
            continue
        
        if s in (":q", ":quit", ":exit"):
            break
        if s in (":h", ":help"):
            print(HELP); continue
        if s in (":v", ":version"):
            print(f"termi {__version__}"); continue
        
        if s.startswith(":model"):
            parts = s.split(maxsplit=1)
            if len(parts)==2:
                model = parts[1].strip()
                print(f"✓ model set to {model}")
            else:
                print(f"current model: {model}")
            continue
        
        if s.startswith(":explain "):
            try:
                explain(s[len(":explain "):].strip(), model)
            except Exception as e:
                print_err(f"✗ Error during explanation: {e}")
            continue

        # Interactive chat & plan
        if s.startswith(":chat "):
            msg = s[len(":chat "):].strip()
            try:
                ans = call_ollama_chat(msg, model)
                print(ans)
            except Exception as e:
                print_err(f"✗ Ollama unavailable; cannot chat. Error: {e}")
            continue

        if s.startswith(":plan "):
            task = s[len(":plan "):].strip()
            try:
                run_plan(task, model, auto=False, dry_run=False)
            except Exception as e:
                print_err(f"✗ Ollama unavailable; planner requires the LLM. Error: {e}")
            continue

        if s.startswith(":plan-auto "):
            task = s[len(":plan-auto "):].strip()
            try:
                run_plan(task, model, auto=True, dry_run=False)
            except Exception as e:
                print_err(f"✗ Ollama unavailable; planner requires the LLM. Error: {e}")
            continue

        if looks_like_command(s):
            run_command(s)
            continue

        # NL → command (fallback only on Ollama unavailability)
        try:
            cmd = call_ollama(s, model)
            if not cmd:
                print_err("✗ The model returned no command. Try rephrasing or use :model to switch.")
                continue
        except Exception:
            cmd = _fallback_command(s)
            print_err("ℹ︎ Ollama unavailable; using fallback command.")
        
        print(f"Proposed: {cmd}")
        if ask_yes_no("Run this?", default="y"):
            run_command(cmd)

def main():
    # Handle help/version before Ollama checks
    if len(sys.argv) > 1:
        if any(a in ("-h", "--help") for a in sys.argv[1:]):
            print(HELP)
            sys.exit(0)
        if any(a in ("-v", "-V", "--version") for a in sys.argv[1:]):
            print(f"termi {__version__}")
            sys.exit(0)
    
    ensure_ollama_installed()
    ensure_ollama_running()
    ensure_model_available(DEFAULT_MODEL)

    if len(sys.argv) == 1:
        interactive()
    else:
        one_shot(sys.argv[1:])

if __name__ == "__main__":
    main()