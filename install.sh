#!/usr/bin/env bash
# One-time setup: add cc_tool commands to your PATH.
# Run once from the cc_tool directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$SCRIPT_DIR/bin"

chmod +x "$BIN_DIR/cc-setup" "$BIN_DIR/cc-update" "$BIN_DIR/cc-install-superpowers"

# Detect shell profile
if [[ -f "$HOME/.zshrc" ]]; then
    PROFILE="$HOME/.zshrc"
elif [[ -f "$HOME/.bashrc" ]]; then
    PROFILE="$HOME/.bashrc"
else
    PROFILE="$HOME/.profile"
fi

PATH_LINE="export PATH=\"$BIN_DIR:\$PATH\""

if grep -qF "$BIN_DIR" "$PROFILE" 2>/dev/null; then
    echo "✓ $BIN_DIR already in PATH ($PROFILE)"
else
    echo "" >> "$PROFILE"
    echo "# cc_tool: Claude Code project setup" >> "$PROFILE"
    echo "$PATH_LINE" >> "$PROFILE"
    echo "✓ Added to PATH in $PROFILE"
fi

echo ""
echo "Reload your shell:"
echo "  source $PROFILE"
echo ""
echo "Then:"
echo "  cc-install-superpowers       # one-time: install Superpowers globally"
echo "  cc-setup [project_path]      # per-project: configure Ruflo + hooks"
echo "  cc-update                    # update both tools anytime"
