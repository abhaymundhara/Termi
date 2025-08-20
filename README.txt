# Termi

**Termi** is a local‑LLM powered terminal copilot that turns natural language into safe shell commands.

## Install
```bash
pipx install termi
# or
pip install termi
```

## Usage
```bash
termi
termi "find large files in ~/Downloads"
termi --explain "find . -type f -size +100M -print0 | xargs -0 ls -lh"
```

## Notes

Termi requires Ollama to be installed and running. If Ollama is not found, Termi will attempt to install it automatically.