#!/usr/bin/env bash
# ==============================================================================
# render-pool uninstaller
# Removes render-pool from ~/.local/bin and optionally cleans ~/.render_accounts
# ==============================================================================

set -e

TARGET_BIN="${HOME}/.local/bin/render-pool"
DATA_DIR="${HOME}/.render_accounts"

echo "=== Uninstalling render-pool ==="

if [ -f "${TARGET_BIN}" ]; then
    rm -f "${TARGET_BIN}"
    echo "✓ Removed ${TARGET_BIN}"
else
    echo "render-pool binary not found in ~/.local/bin"
fi

if [ -d "${DATA_DIR}" ]; then
    read -p "Do you want to delete account metadata in ${DATA_DIR}? (y/N): " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -rf "${DATA_DIR}"
        echo "✓ Removed ${DATA_DIR}"
    else
        echo "Kept ${DATA_DIR}"
    fi
fi

echo "✓ render-pool uninstallation complete."
