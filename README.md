# Termi

**Termi** is a local-LLM powered terminal copilot that turns natural language into safe shell commands. 100% local, zero cloud APIs.

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## Features

- **Natural Language → Commands**: Describe what you want, get a shell command
- **Multi-Backend LLM**: Ollama, LM Studio, llama.cpp — all local, no cloud
- **Token-by-Token Streaming**: See responses as they're generated
- **Safety Analysis**: Color-coded risk warnings before running dangerous commands
- **Rich TUI**: Syntax highlighting, panels, tables, spinners via Rich
- **Interactive Mode**: prompt_toolkit with history search, autocompletion, vi/emacs keys
- **Multi-Turn Chat**: Conversational context preserved across messages
- **Multi-Step Plans**: Break complex tasks into confirmed step sequences
- **Context-Aware**: Reads your CWD, git status, directory listing for better suggestions
- **Command History**: Persistent JSONL history with search
- **Bookmarks**: Save and recall favorite commands
- **Clipboard Integration**: Auto-copy commands with pyperclip
- **Heuristic Fallback**: 30+ pattern-matched commands when LLM is unavailable
- **Shell Completions**: bash, zsh, fish completion generators
- **Pipe Support**: `echo "query" | termi`
- **Configurable**: TOML config file + env vars + CLI flags
- **Cross-Platform**: macOS, Linux (Windows partial)
- **3 Themes**: monokai, dracula, minimal

## Architecture

```
src/termi/
├── __init__.py     # Package metadata
├── cli.py          # Main CLI + Rich TUI + interactive mode
├── config.py       # Config loading (TOML + env + CLI)
├── llm.py          # Multi-backend LLM client with streaming
├── safety.py       # Command risk analysis
├── history.py      # Persistent history + bookmarks
├── context.py      # Environment context gathering
├── fallback.py     # Heuristic command generation
└── themes.py       # Rich color themes
```

## Install

### Quick Start
```bash
git clone https://github.com/abhaymundhara/Termi.git
cd Termi
chmod +x setup.sh
./setup.sh
```

### Manual
```bash
pip install -e '.[dev]'
```

### From PyPI
```bash
pip install termi-copilot
```

## Usage

### One-Shot Mode
```bash
termi "list all Python files larger than 1MB"
termi "show git branches sorted by last commit"
termi --dry-run "delete all .pyc files"
termi --explain "find . -type f -size +100M"
```

### Chat Mode
```bash
termi --chat "what does the -exec flag do in find?"
```

### Multi-Step Plans
```bash
termi --plan "set up a new Python project with git, venv, and pytest"
termi --auto --plan "clean up Docker images and containers"
```

### Interactive Mode
```bash
termi  # just run it
```

**Interactive commands:**
| Command | Description |
|---------|-------------|
| `:help` | Show all commands |
| `:model <name>` | Switch LLM model |
| `:models` | List available models |
| `:chat <msg>` | Multi-turn chat |
| `:plan <task>` | Multi-step plan |
| `:explain <cmd>` | Explain a command |
| `:history` | Show recent commands |
| `:history <q>` | Search history |
| `:bookmark <n> <cmd>` | Save a command |
| `:bookmarks` | List bookmarks |
| `:context` | Show current context |
| `:config` | Show configuration |
| `:theme <name>` | Switch theme |
| `:safety` | Toggle safety checks |
| `:copy` | Copy last command |

### Pipe Mode
```bash
echo "show disk usage" | termi
cat prompt.txt | termi --dry-run
```

### Shell Completions
```bash
# Bash
termi --completions bash >> ~/.bashrc

# Zsh
termi --completions zsh >> ~/.zshrc

# Fish
termi --completions fish > ~/.config/fish/completions/termi.fish
```

## Configuration

```bash
termi --init-config  # creates ~/.config/termi/config.toml
```

```toml
# ~/.config/termi/config.toml
model = "gemma2:2b"
ollama_url = "http://localhost:11434"
temperature = 0.1
num_ctx = 4096
num_predict = 512
stream = true
theme = "monokai"       # monokai, dracula, minimal
safety_confirm = true
history_limit = 500

# Multiple LLM backends (tried in order)
backends = ["ollama"]
lmstudio_url = "http://localhost:1234"
llamacpp_url = "http://localhost:8080"
```

### Environment Variables
| Variable | Description |
|----------|-------------|
| `TERMI_MODEL` | Override default model |
| `OLLAMA_URL` | Ollama server URL |
| `TERMI_THEME` | Color theme |
| `TERMI_STREAM` | Enable/disable streaming |
| `TERMI_SAFETY` | Enable/disable safety |
| `LMSTUDIO_URL` | LM Studio server URL |
| `LLAMACPP_URL` | llama.cpp server URL |

## Safety Analysis

Termi analyzes every command before execution:

| Risk Level | Color | Example | Action |
|-----------|-------|---------|--------|
| **Safe** | Green | `ls -la` | Run immediately |
| **Caution** | Yellow | `rm file.txt` | Confirm first |
| **Dangerous** | Red | `rm -rf ./build` | Warning + confirm |
| **Critical** | Red/White | `rm -rf /` | Blocked by default |

Disable with `--no-safety` or `:safety` toggle in interactive mode.

## Multiple LLM Backends

Termi supports multiple local LLM backends, tried in order:

1. **Ollama** (default) — `ollama serve`
2. **LM Studio** — OpenAI-compatible API at localhost:1234
3. **llama.cpp** — server mode at localhost:8080

```toml
# In config.toml
backends = ["ollama", "lmstudio", "llamacpp"]
```

If Ollama is down, Termi automatically falls back to LM Studio, then llama.cpp.

## Development

```bash
git clone https://github.com/abhaymundhara/Termi.git
cd Termi
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest tests/ -v
```

## License

MIT
