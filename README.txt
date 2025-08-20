# Termi Copilot

Local-LLM terminal copilot that turns natural language into shell commands.

## Install
```bash
pipx install termi-copilot
# or
pip install termi-copilot

##Usage
```bash
termi
termi "find large files in ~/Downloads"
termi --explain "find . -type f -size +100M -print0 | xargs -0 ls -lh"