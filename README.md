# Termi

**Termi** is a local‑LLM powered terminal copilot that turns natural language into safe shell commands.

By default, Termi uses your local Ollama models for reasoning. If Ollama is not running, it gracefully falls back to the OpenAI API (if configured).

## Install
```bash
# From PyPI (stable)
pip install termi-copilot

```

## Usage
```bash
termi       #interactive
# or
termi --explain "find . -type f -size +100M -print0 | xargs -0 ls -lh"
termi --dry-run "rm -rf /tmp/test"
termi --model llama3:latest "list all active network connections"
termi --version
termi --help
```

## Notes

Termi always attempts to use a local LLM first, preferring Ollama. It supports multi-step reasoning when the chosen model allows. 

## Development

The project is still in early stages. I'm not that familiar with github contributions and stuff so please if you're interested to connect and discuss further about the project please do! 
My discord is - abhay066841