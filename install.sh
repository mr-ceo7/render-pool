#!/usr/bin/env bash
# ==============================================================================
# render-pool installer
# Installs render-pool into ~/.local/bin/render-pool
# ==============================================================================

set -e

REPO_RAW_URL="https://raw.githubusercontent.com/mr-ceo7/render-pool/main"
TARGET_DIR="${HOME}/.local/bin"
TARGET_BIN="${TARGET_DIR}/render-pool"

echo "=== Installing render-pool ==="

# Check Python 3
if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: Python 3 is required but not installed." >&2
    exit 1
fi

# Ensure ~/.local/bin exists
mkdir -p "${TARGET_DIR}"

# If running from local repo clone, copy package and wrapper; otherwise download
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
if [ -f "${SCRIPT_DIR}/render-pool" ]; then
    echo "Installing from local repository (${SCRIPT_DIR})..."
    # Install package via pip or copy executable wrapper
    python3 -m pip install -e "${SCRIPT_DIR}" --quiet 2>/dev/null || {
        cp -f "${SCRIPT_DIR}/render-pool" "${TARGET_BIN}"
    }
else
    echo "Downloading render-pool from GitHub..."
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "${REPO_RAW_URL}/render-pool" -o "${TARGET_BIN}"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "${TARGET_BIN}" "${REPO_RAW_URL}/render-pool"
    else
        echo "Error: Neither curl nor wget was found." >&2
        exit 1
    fi
    chmod +x "${TARGET_BIN}"
fi

echo "✓ render-pool successfully installed to ${TARGET_BIN}"

# Check PATH
case ":$PATH:" in
    *":${TARGET_DIR}:"*) ;;
    *)
        echo ""
        echo "Note: ${TARGET_DIR} is not in your \$PATH."
        echo "Add it to your shell configuration (~/.bashrc or ~/.zshrc):"
        echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
        ;;
esac
