#!/usr/bin/env python3
"""Termi CLI - Rich TUI terminal copilot.

Complete rewrite with proper TUI, streaming, safety analysis,
multi-backend LLM support, and persistent history.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

import click
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from . import __version__
from .config import load_config, write_default_config, get_system_info
from .context import build_context
from .fallback import fallback_command
from .history import Bookmarks, History
from .llm import (
    call_llm,
    ensure_model_available,
    ensure_ollama_installed,
    ensure_ollama_running,
    generate_chat,
    generate_command,
    generate_explanation,
    generate_plan,
    list_ollama_models,
    stream_chat,
    stream_llm,
    build_system_prompt,
)
from .safety import RiskLevel, analyze_command, risk_color
from .themes import get_theme

# ---------------------------------------------------------------------------
# Console setup
# ---------------------------------------------------------------------------

console: Optional[Console] = None


def _get_console(cfg: Dict[str, Any]) -> Console:
    global console
    if console is None:
        theme = get_theme(cfg.get("theme", "monokai"))
        console = Console(theme=theme)
    return console


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

def _parse_json_field(text: str, field: str) -> str:
    """Extract a field from JSON response, with fallback to raw text."""
    t = text.strip()
    if t.startswith("`"):
        t = t.strip("`")
    if t.startswith("```"):
        lines = t.splitlines()
        t = "\n".join(lines[1:-1] if len(lines) > 2 else lines)
    try:
        obj = json.loads(t)
        if isinstance(obj, dict) and field in obj:
            return str(obj[field]).strip()
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: return first non-empty line
    for line in t.splitlines():
        line = line.strip()
        if line and not line.startswith("{"):
            return line
    return t


def _parse_cmd(text: str) -> str:
    return _parse_json_field(text, "cmd")


def _parse_explanation(text: str) -> str:
    return _parse_json_field(text, "explanation")


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------

def _run_command(cmd: str, shell: str) -> int:
    if not cmd or not cmd.strip():
        return 1
    try:
        p = subprocess.run(cmd, shell=True, executable=shell)
        return p.returncode
    except KeyboardInterrupt:
        return 130
    except Exception:
        return 1


def _looks_like_command(s: str) -> bool:
    if not s or not s.strip():
        return False
    try:
        parts = shlex.split(s)
    except ValueError:
        return False
    return len(parts) > 0 and shutil.which(parts[0]) is not None


def _copy_to_clipboard(text: str, con: Console) -> None:
    try:
        import pyperclip
        pyperclip.copy(text)
        con.print("[termi.success]Copied to clipboard[/]")
    except Exception:
        con.print("[termi.muted]Could not copy to clipboard[/]")


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _show_command(cmd: str, con: Console) -> None:
    """Display a proposed command with syntax highlighting."""
    syntax = Syntax(cmd, "bash", theme="monokai", word_wrap=True)
    con.print(Panel(syntax, title="Proposed Command", border_style="cyan", expand=False))


def _show_safety(cmd: str, con: Console, cfg: Dict[str, Any]) -> bool:
    """Analyze and show safety warnings. Returns True if safe to proceed."""
    if not cfg.get("safety_confirm", True):
        return True
    result = analyze_command(cmd)
    if result.level == RiskLevel.SAFE:
        return True
    color = risk_color(result.level)
    con.print(f"\n[{color}]{result.level.value.upper()} RISK[/]")
    for reason in result.reasons:
        con.print(f"  {reason}")
    if result.suggestion:
        con.print(f"[termi.info]{result.suggestion}[/]")
    if result.level == RiskLevel.CRITICAL:
        con.print("[bold red]This command is blocked. Override with --no-safety.[/]")
        return False
    return True


def _confirm(prompt: str, con: Console, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        ans = con.input(f"[termi.prompt]{prompt} {suffix}: [/]").strip().lower()
    except (EOFError, KeyboardInterrupt):
        con.print()
        return False
    if not ans:
        return default
    return ans in ("y", "yes")


def _stream_response(messages: List[Dict], model: str, cfg: Dict[str, Any], con: Console) -> str:
    """Stream LLM response with live display."""
    full_text = ""
    try:
        with Live("", console=con, refresh_per_second=15) as live:
            for token in stream_llm(messages, model, cfg):
                full_text += token
                live.update(Text(full_text))
    except Exception:
        # Fallback to non-streaming
        full_text = call_llm(messages, model, cfg)
        con.print(full_text)
    return full_text


# ---------------------------------------------------------------------------
# Core flows
# ---------------------------------------------------------------------------

def _do_explain(text: str, model: str, cfg: Dict[str, Any], con: Console) -> None:
    with con.status("[termi.muted]Thinking...[/]"):
        explanation = generate_explanation(text, model, cfg)
    parsed = _parse_explanation(explanation)
    con.print(Panel(parsed, title="Explanation", border_style="cyan", expand=False))


def _do_oneshot(text: str, model: str, cfg: Dict[str, Any], con: Console,
                history: History, dry: bool = False) -> int:
    """NL -> command -> confirm -> run."""
    context = build_context(cfg.get("context_lines", 50))

    with con.status("[termi.muted]Generating command...[/]"):
        try:
            raw = generate_command(text, model, cfg, context=context)
        except Exception:
            raw = ""

    cmd = _parse_cmd(raw) if raw else ""

    if not cmd:
        # Fallback to heuristic
        cmd = fallback_command(text)
        con.print("[termi.warning]LLM unavailable, using heuristic fallback[/]")

    _show_command(cmd, con)

    if not _show_safety(cmd, con, cfg):
        return 1

    if cfg.get("clipboard_auto"):
        _copy_to_clipboard(cmd, con)

    if dry:
        con.print("[termi.muted](dry-run, not executing)[/]")
        history.add(text, cmd, mode="dry-run", model=model, cwd=os.getcwd())
        return 0

    if not _confirm("Run this?", con):
        con.print("[termi.muted]Skipped[/]")
        return 0

    rc = _run_command(cmd, cfg.get("shell", "/bin/bash"))
    history.add(text, cmd, mode="oneshot", model=model, exit_code=rc, cwd=os.getcwd())

    if rc == 0:
        con.print(f"[termi.success]Done (exit 0)[/]")
    else:
        con.print(f"[termi.error]Exit code: {rc}[/]")
    return rc


def _do_plan(task: str, model: str, cfg: Dict[str, Any], con: Console,
             history: History, auto: bool = False, dry: bool = False) -> int:
    with con.status("[termi.muted]Planning...[/]"):
        steps, notes = generate_plan(task, model, cfg)

    if not steps:
        con.print("[termi.error]Planner returned no steps. Try rephrasing or a bigger model.[/]")
        return 1

    table = Table(title="Execution Plan", border_style="cyan")
    table.add_column("#", style="termi.step", width=3)
    table.add_column("Thought", style="termi.thought")
    table.add_column("Command", style="termi.command")
    for i, st in enumerate(steps, 1):
        table.add_row(str(i), st["thought"] or "-", st["cmd"])
    con.print(table)
    if notes:
        con.print(f"[termi.info]Notes: {notes}[/]")

    rc_final = 0
    for i, st in enumerate(steps, 1):
        cmd = st["cmd"]
        if not cmd:
            continue
        if dry:
            con.print(f"[termi.muted][dry-run] step {i}: {cmd}[/]")
            continue
        if not _show_safety(cmd, con, cfg):
            continue
        if not auto:
            if not _confirm(f"Run step {i}? {cmd}", con):
                con.print("[termi.muted]Skipped[/]")
                continue
        con.print(f"\n[termi.step]Step {i}:[/] {cmd}")
        rc = _run_command(cmd, cfg.get("shell", "/bin/bash"))
        history.add(task, cmd, mode="plan", model=model, exit_code=rc, cwd=os.getcwd())
        rc_final = rc if rc != 0 else rc_final
        if rc != 0:
            con.print(f"[termi.error]Step {i} failed (exit {rc})[/]")
            if not auto and not _confirm("Continue?", con, default=False):
                break
    return rc_final


def _do_chat(message: str, model: str, cfg: Dict[str, Any], con: Console,
             chat_history: List[Dict]) -> None:
    if cfg.get("stream", True):
        full = ""
        try:
            with Live("", console=con, refresh_per_second=15) as live:
                for token in stream_chat(message, model, cfg, history=chat_history):
                    full += token
                    live.update(Markdown(full))
        except Exception:
            full = generate_chat(message, model, cfg, history=chat_history)
            con.print(Markdown(full))
    else:
        with con.status("[termi.muted]Thinking...[/]"):
            full = generate_chat(message, model, cfg, history=chat_history)
        con.print(Markdown(full))

    chat_history.append({"role": "user", "content": message})
    chat_history.append({"role": "assistant", "content": full})
    # Keep history bounded
    if len(chat_history) > 20:
        chat_history[:] = chat_history[-20:]


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

def _interactive_completer():
    """Build prompt_toolkit completer for interactive mode."""
    try:
        from prompt_toolkit.completion import WordCompleter
        words = [
            ":help", ":quit", ":exit", ":model", ":explain",
            ":chat", ":plan", ":plan-auto", ":history", ":clear",
            ":bookmark", ":bookmarks", ":unbookmark", ":copy",
            ":context", ":config", ":models", ":theme", ":version",
            ":safety",
        ]
        return WordCompleter(words, sentence=True)
    except ImportError:
        return None


def _interactive(cfg: Dict[str, Any]) -> None:
    model = cfg.get("model", "gemma2:2b")
    con = _get_console(cfg)
    history = History(limit=cfg.get("history_limit", 500))
    bookmarks = Bookmarks()
    chat_history: List[Dict] = []

    # Welcome banner
    banner = Text()
    banner.append("Termi", style="bold cyan")
    banner.append(f" v{__version__}", style="dim")
    banner.append(" | local LLM copilot\n", style="dim")
    banner.append(f"Model: {model}", style="termi.info")
    banner.append(" | Type ", style="dim")
    banner.append(":help", style="termi.command")
    banner.append(" for commands", style="dim")
    con.print(Panel(banner, border_style="cyan", expand=False))

    # Try prompt_toolkit for better UX
    use_pt = False
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from .config import CONFIG_DIR
        pt_history = FileHistory(str(CONFIG_DIR / "prompt_history"))
        session = PromptSession(
            history=pt_history,
            completer=_interactive_completer(),
            enable_history_search=True,
        )
        use_pt = True
    except ImportError:
        session = None

    while True:
        try:
            if use_pt and session:
                s = session.prompt("termi> ").strip()
            else:
                s = input("termi> ").strip()
        except (EOFError, KeyboardInterrupt):
            con.print()
            break

        if not s:
            continue

        # Meta commands
        if s in (":q", ":quit", ":exit"):
            break

        if s in (":h", ":help"):
            _show_help(con)
            continue

        if s in (":v", ":version"):
            con.print(f"termi {__version__}")
            continue

        if s.startswith(":model"):
            parts = s.split(maxsplit=1)
            if len(parts) == 2:
                model = parts[1].strip()
                con.print(f"[termi.success]Model set to {model}[/]")
            else:
                con.print(f"Current model: [termi.command]{model}[/]")
            continue

        if s == ":models":
            models = list_ollama_models()
            if models:
                for m in models:
                    marker = " *" if m == model else ""
                    con.print(f"  [termi.command]{m}[/]{marker}")
            else:
                con.print("[termi.muted]No models found (is Ollama running?)[/]")
            continue

        if s.startswith(":theme"):
            parts = s.split(maxsplit=1)
            if len(parts) == 2:
                cfg["theme"] = parts[1].strip()
                global console
                console = None
                con = _get_console(cfg)
                con.print(f"[termi.success]Theme set to {cfg['theme']}[/]")
            else:
                con.print(f"Current theme: {cfg.get('theme', 'monokai')}")
                con.print("Available: monokai, dracula, minimal")
            continue

        if s == ":safety":
            current = cfg.get("safety_confirm", True)
            cfg["safety_confirm"] = not current
            state = "ON" if cfg["safety_confirm"] else "OFF"
            con.print(f"[termi.info]Safety checks: {state}[/]")
            continue

        if s.startswith(":explain "):
            _do_explain(s[len(":explain "):].strip(), model, cfg, con)
            continue

        if s.startswith(":chat "):
            _do_chat(s[len(":chat "):].strip(), model, cfg, con, chat_history)
            continue

        if s.startswith(":plan-auto "):
            task = s[len(":plan-auto "):].strip()
            _do_plan(task, model, cfg, con, history, auto=True)
            continue

        if s.startswith(":plan "):
            task = s[len(":plan "):].strip()
            _do_plan(task, model, cfg, con, history)
            continue

        if s == ":history":
            entries = history.recent(20)
            if not entries:
                con.print("[termi.muted]No history yet[/]")
            else:
                table = Table(title="Recent Commands", border_style="dim")
                table.add_column("Query", style="dim", max_width=40)
                table.add_column("Command", style="termi.command")
                table.add_column("Exit", width=4)
                for e in entries:
                    exit_str = str(e.exit_code) if e.exit_code is not None else "-"
                    table.add_row(e.query[:40], e.command, exit_str)
                con.print(table)
            continue

        if s.startswith(":history "):
            query = s[len(":history "):].strip()
            entries = history.search(query)
            if not entries:
                con.print(f"[termi.muted]No matches for '{query}'[/]")
            else:
                for e in entries[:10]:
                    con.print(f"  [termi.command]{e.command}[/] [dim]({e.query})[/]")
            continue

        if s == ":clear":
            history.clear()
            con.print("[termi.success]History cleared[/]")
            continue

        if s.startswith(":bookmark "):
            parts = s[len(":bookmark "):].strip().split(maxsplit=1)
            if len(parts) >= 1:
                name = parts[0]
                cmd = parts[1] if len(parts) > 1 else ""
                if not cmd and history.entries:
                    cmd = history.entries[-1].command
                bookmarks.add(name, cmd)
                con.print(f"[termi.success]Bookmarked '{name}': {cmd}[/]")
            continue

        if s == ":bookmarks":
            bms = bookmarks.list_all()
            if not bms:
                con.print("[termi.muted]No bookmarks[/]")
            else:
                for name, info in bms.items():
                    con.print(f"  [termi.highlight]{name}[/]: [termi.command]{info['command']}[/]")
            continue

        if s.startswith(":unbookmark "):
            name = s[len(":unbookmark "):].strip()
            if bookmarks.remove(name):
                con.print(f"[termi.success]Removed bookmark '{name}'[/]")
            else:
                con.print(f"[termi.muted]Bookmark '{name}' not found[/]")
            continue

        if s.startswith(":copy"):
            if history.entries:
                _copy_to_clipboard(history.entries[-1].command, con)
            else:
                con.print("[termi.muted]No command to copy[/]")
            continue

        if s == ":context":
            ctx = build_context(cfg.get("context_lines", 50))
            con.print(Panel(ctx, title="Current Context", border_style="dim"))
            continue

        if s == ":config":
            info = get_system_info()
            table = Table(title="Configuration", border_style="dim")
            table.add_column("Key", style="termi.highlight")
            table.add_column("Value")
            for k, v in cfg.items():
                table.add_row(k, str(v))
            table.add_row("---", "---")
            for k, v in info.items():
                table.add_row(f"sys.{k}", v)
            con.print(table)
            continue

        # Check if it's a bookmarked command
        bm = bookmarks.get(s.lstrip(":"))
        if bm:
            cmd = bm["command"]
            _show_command(cmd, con)
            if _show_safety(cmd, con, cfg) and _confirm("Run this?", con):
                rc = _run_command(cmd, cfg.get("shell", "/bin/bash"))
                history.add(s, cmd, mode="bookmark", model=model, exit_code=rc, cwd=os.getcwd())
            continue

        # Direct command
        if _looks_like_command(s):
            if _show_safety(s, con, cfg):
                rc = _run_command(s, cfg.get("shell", "/bin/bash"))
                history.add(s, s, mode="direct", exit_code=rc, cwd=os.getcwd())
            continue

        # NL -> command
        _do_oneshot(s, model, cfg, con, history)


def _show_help(con: Console) -> None:
    help_text = """
[bold cyan]Termi Commands[/]

[termi.command]:help[/]              Show this help
[termi.command]:quit[/]              Exit interactive mode
[termi.command]:model <name>[/]      Switch LLM model
[termi.command]:models[/]            List available models
[termi.command]:theme <name>[/]      Switch theme (monokai, dracula, minimal)
[termi.command]:explain <cmd>[/]     Explain a command
[termi.command]:chat <msg>[/]        General chat (multi-turn)
[termi.command]:plan <task>[/]       Multi-step plan with confirmation
[termi.command]:plan-auto <task>[/]  Auto-run a planned sequence
[termi.command]:history[/]           Show recent commands
[termi.command]:history <query>[/]   Search history
[termi.command]:clear[/]             Clear history
[termi.command]:bookmark <n> <cmd>[/] Bookmark a command
[termi.command]:bookmarks[/]         List bookmarks
[termi.command]:unbookmark <n>[/]    Remove bookmark
[termi.command]:copy[/]              Copy last command to clipboard
[termi.command]:context[/]           Show current context (cwd, git, etc.)
[termi.command]:config[/]            Show configuration
[termi.command]:safety[/]            Toggle safety checks
[termi.command]:version[/]           Show version

[dim]Or just type natural language to get a command, or type a command to run it directly.[/]
"""
    con.print(Panel(help_text.strip(), title="Help", border_style="cyan", expand=False))


# ---------------------------------------------------------------------------
# Shell completion generator
# ---------------------------------------------------------------------------

def _generate_completions(shell: str) -> str:
    if shell == "bash":
        return """# Termi bash completion
_termi_complete() {
    local cur=${COMP_WORDS[COMP_CWORD]}
    COMPREPLY=($(compgen -W "--help --version --explain --chat --plan --auto --dry-run --model --no-safety --stream --no-stream --list-models --init-config --completions" -- "$cur"))
}
complete -F _termi_complete termi"""
    elif shell == "zsh":
        return """# Termi zsh completion
_termi() {
    _arguments \\
        '--help[Show help]' \\
        '--version[Show version]' \\
        '--explain[Explain a command]' \\
        '--chat[General chat]' \\
        '--plan[Multi-step plan]' \\
        '--auto[Auto-run plan]' \\
        '--dry-run[Show command only]' \\
        '--model[Override model]:model:' \\
        '--no-safety[Disable safety]' \\
        '--stream[Enable streaming]' \\
        '--no-stream[Disable streaming]' \\
        '--list-models[List models]' \\
        '--init-config[Create config]' \\
        '--completions[Generate completions]:shell:(bash zsh fish)' \\
        '*:query:'
}
compdef _termi termi"""
    elif shell == "fish":
        return """# Termi fish completion
complete -c termi -l help -d 'Show help'
complete -c termi -l version -d 'Show version'
complete -c termi -l explain -d 'Explain a command'
complete -c termi -l chat -d 'General chat'
complete -c termi -l plan -d 'Multi-step plan'
complete -c termi -l auto -d 'Auto-run plan'
complete -c termi -l dry-run -d 'Show command only'
complete -c termi -l model -d 'Override model' -r
complete -c termi -l no-safety -d 'Disable safety'
complete -c termi -l list-models -d 'List models'
complete -c termi -l init-config -d 'Create config'
complete -c termi -l completions -d 'Generate completions' -r -a 'bash zsh fish'"""
    return ""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]

    # Quick exits before loading config
    if "-h" in args or "--help" in args:
        _print_usage()
        sys.exit(0)
    if "-v" in args or "-V" in args or "--version" in args:
        print(f"termi {__version__}")
        sys.exit(0)

    # Parse flags
    model_override = None
    if "--model" in args:
        i = args.index("--model")
        if i + 1 < len(args):
            model_override = args[i + 1]
            args = args[:i] + args[i + 2:]
        else:
            print("Missing model name after --model", file=sys.stderr)
            sys.exit(2)

    dry = "--dry-run" in args
    if dry:
        args.remove("--dry-run")

    auto = "--auto" in args
    if auto:
        args.remove("--auto")

    no_safety = "--no-safety" in args
    if no_safety:
        args.remove("--no-safety")

    use_stream = "--stream" in args
    if use_stream:
        args.remove("--stream")

    no_stream = "--no-stream" in args
    if no_stream:
        args.remove("--no-stream")

    # Load config
    overrides = {}
    if model_override:
        overrides["model"] = model_override
    if no_safety:
        overrides["safety_confirm"] = False
    if use_stream:
        overrides["stream"] = True
    if no_stream:
        overrides["stream"] = False

    cfg = load_config(**overrides)
    con = _get_console(cfg)
    model = cfg.get("model", "gemma2:2b")

    # Special commands
    if "--init-config" in args:
        path = write_default_config()
        con.print(f"[termi.success]Config written to {path}[/]")
        sys.exit(0)

    if "--list-models" in args:
        models = list_ollama_models()
        if models:
            for m in models:
                con.print(f"  {m}")
        else:
            con.print("[termi.muted]No models found (is Ollama running?)[/]")
        sys.exit(0)

    if "--completions" in args:
        i = args.index("--completions")
        shell = args[i + 1] if i + 1 < len(args) else "bash"
        print(_generate_completions(shell))
        sys.exit(0)

    # Pipe support
    if not sys.stdin.isatty():
        text = sys.stdin.read().strip()
        if text:
            ensure_ollama_installed()
            ensure_ollama_running(cfg.get("ollama_url", "http://localhost:11434"))
            ensure_model_available(model)
            history = History(limit=cfg.get("history_limit", 500))
            rc = _do_oneshot(text, model, cfg, con, history, dry=dry)
            sys.exit(rc)
        sys.exit(0)

    # Bootstrap Ollama
    ensure_ollama_installed()
    ensure_ollama_running(cfg.get("ollama_url", "http://localhost:11434"))
    ensure_model_available(model)

    history = History(limit=cfg.get("history_limit", 500))

    # Handle flags
    if "--explain" in args:
        args.remove("--explain")
        text = " ".join(args).strip()
        if not text:
            _print_usage()
            sys.exit(0)
        _do_explain(text, model, cfg, con)
        return

    if "--chat" in args:
        args.remove("--chat")
        text = " ".join(args).strip()
        if not text:
            _print_usage()
            sys.exit(0)
        chat_history: List[Dict] = []
        _do_chat(text, model, cfg, con, chat_history)
        return

    if "--plan" in args:
        args.remove("--plan")
        text = " ".join(args).strip()
        if not text:
            _print_usage()
            sys.exit(0)
        rc = _do_plan(text, model, cfg, con, history, auto=auto, dry=dry)
        sys.exit(rc)

    # One-shot or interactive
    text = " ".join(args).strip()
    if text:
        if _looks_like_command(text):
            sys.exit(_run_command(text, cfg.get("shell", "/bin/bash")))
        rc = _do_oneshot(text, model, cfg, con, history, dry=dry)
        sys.exit(rc)

    # Interactive mode
    _interactive(cfg)


def _print_usage():
    print(f"""Termi v{__version__} - Your local terminal copilot

Usage:
  termi                        Interactive mode
  termi "text"                 NL -> command -> confirm -> run
  termi --chat "message"       General chat (multi-turn)
  termi --plan "task"          Multi-step plan with confirmations
  termi --auto --plan "task"   Auto-run planned sequence
  termi --explain "cmd"        Explain a command
  termi --dry-run "text"       Show command only, don't execute
  termi --model <name> "text"  Override model
  termi --no-safety "text"     Skip safety checks
  termi --stream               Force streaming output
  termi --no-stream            Force non-streaming output
  termi --list-models          List available Ollama models
  termi --init-config          Create default config file
  termi --completions <shell>  Generate shell completions (bash/zsh/fish)
  echo "text" | termi          Pipe mode

Config: ~/.config/termi/config.toml
Env:    TERMI_MODEL, OLLAMA_URL, TERMI_THEME, TERMI_STREAM""")


if __name__ == "__main__":
    main()
