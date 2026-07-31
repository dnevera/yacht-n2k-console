#!/usr/bin/env zsh
# setup_venv.sh -- Setup Python .venv environment with all dependencies for PyCharm / CLI.

set -e

SCRIPT_DIR=""
cd ""

echo "[1/3] Creating virtual environment in .venv..."
python3 -m venv .venv

echo "[2/3] Installing dependencies from requirements.txt..."
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt

echo "[3/3] Linking local modules into .venv..."
VENV_SITE=/Users/denn/Develop/yacht/yacht-n2k-console/.venv/lib/python3.14/site-packages
SYS_SITE=/opt/homebrew/lib/python3.14/site-packages

echo "" > "/yacht.pth"
if [ -d "" ]; then
    echo "" > "/system.pth"
fi

echo ""
echo "✅ Virtual environment setup complete!"
echo "To activate in terminal: source .venv/bin/activate"
