#!/usr/bin/env bash
# Termi Setup Script
# One-command bootstrap for development or production use.
set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

info "Setting up Termi..."

# 1. Check Python
if ! command -v python3 &>/dev/null; then
    error "Python 3 not found. Install it first."
    exit 1
fi

PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
info "Python version: $PYVER"

# 2. Create venv if needed
if [ ! -d ".venv" ]; then
    info "Creating virtual environment..."
    python3 -m venv .venv
fi
source .venv/bin/activate
info "Using venv: $(which python3)"

# 3. Install in editable mode
info "Installing Termi (editable)..."
pip install -e '.[dev]' --quiet

# 4. Check Ollama
if command -v ollama &>/dev/null; then
    info "Ollama found: $(which ollama)"
    if ollama list 2>/dev/null | grep -q 'gemma2:2b'; then
        info "Default model gemma2:2b is available"
    else
        warn "Default model gemma2:2b not found"
        read -p "Pull gemma2:2b now? [Y/n] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
            ollama pull gemma2:2b
        fi
    fi
else
    warn "Ollama not found. Termi will prompt to install on first run."
    echo "  Or install manually: https://ollama.com/download"
fi

# 5. Create default config
if [ ! -f "$HOME/.config/termi/config.toml" ]; then
    info "Creating default config..."
    termi --init-config 2>/dev/null || true
fi

# 6. Run tests
info "Running tests..."
pytest tests/ -v --tb=short 2>/dev/null || warn "Some tests failed (may need Ollama running)"

echo
info "${BOLD}Setup complete!${NC}"
echo
echo -e "  ${GREEN}termi${NC}                    # interactive mode"
echo -e "  ${GREEN}termi \"list files\"${NC}       # one-shot"
echo -e "  ${GREEN}termi --chat \"hello\"${NC}    # chat mode"
echo -e "  ${GREEN}termi --plan \"deploy\"${NC}   # multi-step plan"
echo
