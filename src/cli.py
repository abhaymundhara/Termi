#!/usr/bin/env python3
import os, sys, json, shlex, shutil, subprocess, textwrap, urllib.request, urllib.error, time, platform, socket

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
DEFAULT_MODEL = os.environ.get("TERMI_MODEL", "gemma2:2b")  # change if you prefer
SHELL = os.environ.get("SHELL", "/bin/zsh")

SYSTEM_PROMPT = """You are a helpful terminal copilot.
- Convert the user's natural-language request into exactly ONE safe, POSIX-compatible shell command for macOS (zsh).
- Prefer read-only or non-destructive commands unless the user clearly asks otherwise.
- Use simple, commonly installed tools (ls, grep, sed, awk, find, curl, git, python, node etc).
- If the user asks to explain a command, return a 2-3 sentence explanation (no command).
STRICT OUTPUT RULES:
- If the user wants a command: output ONLY the command on a single line. No backticks, no code fences, no prose.
- If the user asked for an explanation: output ONLY the explanation prose (2-3 sentences), no command.
"""

HELP = f"""\
Termi — terminal copilot (Ollama-backed)

Usage:
  termi                 # interactive mode
  termi "text here"     # one-shot: NL → command → confirm → run
  termi --explain "cmd" # explain a command without running it
  termi --dry-run "..." # NL → command (show only, don't run)
  termi --model <name>  # override model (default: {DEFAULT_MODEL})

Notes:
  • On first run, Termi checks for Ollama and offers to install/start it for you.
  • If the default model is missing, Termi offers to pull it.

Env:
  OLLAMA_URL=http://localhost:11434/api/generate
  TERMI_MODEL={DEFAULT_MODEL}
"""

def print_err(*a): print(*a, file=sys.stderr)

# --- Ollama presence & server checks -------------------------------------------------

def is_port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            return True
        except OSError:
            return False


def prompt_install_ollama() -> bool:
    print("Ollama is not installed.")
    return ask_yes_no("Install Ollama now? This may require sudo.", default="y")


def install_ollama() -> int:
    system = platform.system()
    # Prefer Homebrew on macOS if present, otherwise use official install script
    if system == "Darwin" and shutil.which("brew"):
        cmd = "brew install ollama"
    else:
        # Fallback cross-platform installer (official script)
        cmd = "curl -fsSL https://ollama.com/install.sh | sh"
    print(f"Installing via: {cmd}")
    return run_command(cmd)


def run_in_new_terminal_mac(command: str) -> int:
    """Open a new macOS Terminal window running the given command."""
    osa = (
        'osascript -e ' 
        '"tell application \"Terminal\" to do script \"' + command.replace('"', '\\"') + '\""'
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
    # Quick port probe first
    parsed = urllib.parse.urlparse(OLLAMA_URL)
    host = parsed.hostname or "localhost"
    port = parsed.port or 11434
    if is_port_open(host, port):
        return

    print("Ollama server not detected on", f"{host}:{port}")
    if not ask_yes_no("Start Ollama server in a new Terminal window?", default="y"):
        print_err("✗ Ollama server not running. Exiting.")
        sys.exit(1)

    if platform.system() == "Darwin":
        # Use login shell so PATH/brew are available, then keep that window running
        rc = run_in_new_terminal_mac(f"/bin/zsh -lc 'ollama serve'")
        if rc != 0:
            print_err("✗ Failed to spawn ollama serve (osascript exit", rc, ")")
            sys.exit(rc)
    else:
        # Best-effort background start for Linux
        rc = run_command("nohup ollama serve >/dev/null 2>&1 &")
        if rc != 0:
            print_err("✗ Failed to start ollama serve (exit", rc, ")")
            sys.exit(rc)

    # Wait a few seconds for server to come up
    for _ in range(30):
        if is_port_open(host, port):
            return
        time.sleep(0.2)
    print_err("✗ Ollama server did not become ready on", f"{host}:{port}")
    sys.exit(1)


def ensure_model_available(model: str) -> None:
    # Check if model is present in `ollama list`
    if not shutil.which("ollama"):
        return
    try:
        out = subprocess.check_output(["ollama", "list"], text=True)
        if model in out:
            return
    except Exception:
        # If list fails, try to pull anyway
        pass
    print(f"Model '{model}' not found locally.")
    if ask_yes_no(f"Pull '{model}' now?", default="y"):
        rc = run_command(f"ollama pull {shlex.quote(model)}")
        if rc != 0:
            print_err("✗ Failed to pull model", model, "(exit", rc, ")")
    else:
        print_err("Skipping model pull; generation may fail if the model is missing.")

def which_exists(token: str) -> bool:
    return shutil.which(token) is not None

def looks_like_command(s: str) -> bool:
    try:
        parts = shlex.split(s)
    except ValueError:
        return False
    return len(parts) > 0 and which_exists(parts[0])

def run_command(cmd: str) -> int:
    try:
        p = subprocess.run(cmd, shell=True, executable=SHELL)
        return p.returncode
    except KeyboardInterrupt:
        return 130

def ask_yes_no(prompt: str, default="n") -> bool:
    prompt_full = f"{prompt} [{'Y/n' if default=='y' else 'y/N'}]: "
    ans = input(prompt_full).strip().lower()
    if not ans:
        ans = default
    return ans in ("y","yes")

def call_ollama(prompt: str, model: str, explain: bool=False) -> str:
    # Ensure server is reachable (in case user launched Termi directly)
    try:
        parsed = urllib.parse.urlparse(OLLAMA_URL)
        host = parsed.hostname or "localhost"
        port = parsed.port or 11434
        if not is_port_open(host, port):
            raise urllib.error.URLError("ollama not reachable")
    except Exception:
        print_err("Ollama not reachable; attempting to start server...")
        ensure_ollama_running()

    full_prompt = f"System:\n{SYSTEM_PROMPT}\n\nUser:\n{prompt.strip()}\n"
    payload = {
        "model": model,
        "prompt": full_prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=data, headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            obj = json.loads(r.read().decode("utf-8"))
            resp = obj.get("response","").strip()
            # Enforce “single line command” if not explaining
            if not explain:
                # strip code fences/backticks if any slipped through
                resp = resp.strip().strip("`")
                # take first line
                resp = resp.splitlines()[0].strip()
            return resp
    except urllib.error.URLError as e:
        print_err("✗ Could not reach Ollama at", OLLAMA_URL)
        print_err("  Make sure Ollama is running: `ollama serve` and you pulled the model:", model)
        raise e

def explain(cmd: str, model: str):
    prompt = f"Explain this command clearly and concisely:\n\n{cmd}\n\n(Per rules: return only a short explanation, no command.)"
    explanation = call_ollama(prompt, model, explain=True)
    print(explanation)

def one_shot(args):
    model = DEFAULT_MODEL
    dry = False
    if "--model" in args:
        i = args.index("--model")
        try: model = args[i+1]
        except IndexError:
            print_err("Missing model name after --model"); sys.exit(2)
        args = args[:i] + args[i+2:]
    if "--dry-run" in args:
        dry = True
        args.remove("--dry-run")
    if "--explain" in args:
        args.remove("--explain")
        text = " ".join(args).strip()
        if not text:
            print(HELP); sys.exit(0)
        explain(text, model)
        return

    text = " ".join(args).strip()
    if not text:
        print(HELP); sys.exit(0)

    if looks_like_command(text):
        # Run as-is
        returncode = run_command(text)
        sys.exit(returncode)

    # NL → command
    cmd = call_ollama(text, model)
    print(f"Proposed: {cmd}")
    if dry or not ask_yes_no("Run this?", default="y"):
        print("Skipped.")
        return
    sys.exit(run_command(cmd))

def interactive():
    print("Termi (local LLM copilot). Type natural language or a command. Type :help, :model, :quit.")
    model = DEFAULT_MODEL
    while True:
        try:
            s = input("termi> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not s: 
            continue
        if s in (":q", ":quit", ":exit"):
            break
        if s in (":h", ":help"):
            print(HELP)
            continue
        if s.startswith(":model"):
            # :model llama3.1:8b
            parts = s.split(maxsplit=1)
            if len(parts)==2:
                model = parts[1].strip()
                print(f"✓ model set to {model}")
            else:
                print(f"current model: {model}")
            continue
        if s.startswith(":explain "):
            explain(s[len(":explain "):].strip(), model)
            continue

        if looks_like_command(s):
            run_command(s)
            continue

        # NL → command
        try:
            cmd = call_ollama(s, model)
        except Exception:
            continue
        print(f"Proposed: {cmd}")
        if ask_yes_no("Run this?", default="y"):
            run_command(cmd)

def main():
    # Ensure prerequisites before doing anything
    ensure_ollama_installed()
    ensure_ollama_running()
    # Determine model from env/defaults that will be used initially (one_shot may override)
    ensure_model_available(DEFAULT_MODEL)

    if len(sys.argv) == 1:
        interactive()
    else:
        one_shot(sys.argv[1:])

if __name__ == "__main__":
    main()