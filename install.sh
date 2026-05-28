#!/usr/bin/env bash
# One-time machine setup: add cc_tool commands to PATH and install Superpowers.
# Run once from the cc_tool directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$SCRIPT_DIR/bin"

chmod +x "$BIN_DIR/cc-setup" "$BIN_DIR/cc-update" "$BIN_DIR/cc-install-superpowers" "$BIN_DIR/cc-install-security" "$BIN_DIR/cc-devcontainer" "$BIN_DIR/cc-token" "$BIN_DIR/cc-update-project" "$BIN_DIR/cc-update-permissions"

# ── 1. PATH ───────────────────────────────────────────────────────────────────
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

# ── 2. Global plugins (bin/ not in PATH yet, call via full path) ──────────────
echo ""
"$BIN_DIR/cc-install-superpowers"
echo ""
"$BIN_DIR/cc-install-security"

echo ""
echo "Reload your shell, then set up your project:"
echo "  source $PROFILE"
echo "  cc-setup [project_path]      # per-project: configure Ruflo + hooks + CLAUDE.md"
echo "  cc-update                    # update tools anytime"
