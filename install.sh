#!/usr/bin/env bash
# One-time machine setup: add cc_tool commands to PATH and install Superpowers.
# Run once from the cc_tool directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$SCRIPT_DIR/bin"

chmod +x "$BIN_DIR/cc-setup" "$BIN_DIR/cc-update" "$BIN_DIR/cc-install-superpowers" "$BIN_DIR/cc-install-security" "$BIN_DIR/cc-install-tasteskill" "$BIN_DIR/cc-devcontainer" "$BIN_DIR/cc-token" "$BIN_DIR/cc-update-project" "$BIN_DIR/cc-update-permissions" "$BIN_DIR/cc-vault" "$BIN_DIR/cc-dash"

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

# One command to open the dashboard from any terminal: starts cc-dash in the
# background (the browser opens and pairs itself) and returns the prompt.
# A dashboard already running is stopped first, so it always comes up on its
# usual port in the mode asked for.
# Arguments pass through, e.g. `ccdash --control`. The log holds the one-time
# pairing link, so it is written owner-only.
CCDASH_LINE='ccdash() { local b d="${XDG_STATE_HOME:-$HOME/.local/state}/cc_tool"; b="$(command -v cc-dash)" || { echo "cc-dash not on PATH" >&2; return 1; }; pkill -u "$USER" -f "$b" 2>/dev/null && sleep 1; mkdir -p "$d" && (umask 077; nohup "$b" "$@" >>"$d/cc-dash.log" 2>&1 </dev/null &) && echo "cc-dash is opening in your browser (log: $d/cc-dash.log)"; }'

if grep -qF 'ccdash() {' "$PROFILE" 2>/dev/null; then
    echo "✓ ccdash already defined ($PROFILE)"
else
    echo "# cc_tool: ccdash opens the dashboard (cc-dash) in the background" >> "$PROFILE"
    echo "$CCDASH_LINE" >> "$PROFILE"
    echo "✓ Added ccdash to $PROFILE (run: ccdash, or ccdash --control)"
fi

# ── 2. Global plugins (bin/ not in PATH yet, call via full path) ──────────────
echo ""
"$BIN_DIR/cc-install-superpowers"
echo ""
"$BIN_DIR/cc-install-security"
echo ""
"$BIN_DIR/cc-install-tasteskill"

echo ""
echo "Reload your shell, then set up your project:"
echo "  source $PROFILE"
echo "  cc-setup [project_path]      # per-project: configure hooks + skills + CLAUDE.md"
echo "  cc-update                    # update tools anytime"
