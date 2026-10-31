#!/usr/bin/env bash
# One-time machine setup: add cc_tool commands to PATH and install Superpowers.
# Run once from the cc_tool directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$SCRIPT_DIR/bin"

# ── 0. Prerequisites ──────────────────────────────────────────────────────────
# Checked before anything is written, so a failed run leaves the machine as it was.
case "$(uname -s)" in
    Darwin) OS=macos ;;
    MINGW*|MSYS*|CYGWIN*)
        echo "Error: cc_tool does not run in Git Bash or native Windows: it needs a Unix terminal and /proc." >&2
        echo "  Install WSL 2 (in PowerShell: wsl --install), then clone and run install.sh inside it." >&2
        echo "  See \"Windows\" in README.md." >&2
        exit 1
        ;;
    *) if grep -qi microsoft /proc/version 2>/dev/null; then OS=wsl; else OS=linux; fi ;;
esac

case "$OS" in
    macos)
        HINT_PYTHON="brew install python"
        HINT_GIT="xcode-select --install"
        HINT_NODE="brew install node"
        ;;
    wsl)
        HINT_PYTHON="sudo apt install python3 (Ubuntu 24.04 or newer ships 3.12+)"
        HINT_GIT="sudo apt install git"
        HINT_NODE="sudo apt install nodejs npm"
        ;;
    *)
        HINT_PYTHON="install python3 with your package manager"
        HINT_GIT="install git with your package manager"
        HINT_NODE="install nodejs and npm with your package manager"
        ;;
esac

missing=0
need() {  # need <what> <how to get it>
    echo "✗ $1 — $2" >&2
    missing=1
}
# WSL sees the Windows PATH, and a Windows claude or npx found there fails on Linux paths.
windows_copy() {
    [[ "$OS" == wsl && "$(command -v "$1")" == /mnt/* ]]
}

# Running them, not `command -v`: macOS ships python3 and git as stubs until the
# Command Line Tools are installed.
python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null \
    || need "Python 3.12+ as python3" "$HINT_PYTHON"
git --version >/dev/null 2>&1 || need "git" "$HINT_GIT"
if ! command -v npx >/dev/null 2>&1; then
    need "node/npx" "$HINT_NODE"
elif windows_copy npx; then
    need "node/npx inside WSL (found the Windows one: $(command -v npx))" "$HINT_NODE"
fi
if ! command -v claude >/dev/null 2>&1; then
    need "Claude Code" "curl -fsSL https://claude.ai/install.sh | bash"
elif windows_copy claude; then
    need "Claude Code inside WSL (found the Windows one: $(command -v claude))" \
        "curl -fsSL https://claude.ai/install.sh | bash"
fi
if (( missing )); then
    echo "Install the above, open a new terminal, and run ./install.sh again." >&2
    exit 1
fi
if [[ "$OS" == wsl && "$SCRIPT_DIR" == /mnt/* ]]; then
    echo "⚠ cc_tool is on the Windows disk ($SCRIPT_DIR). Clone it into your WSL home (~) instead: /mnt/ is slow and loses file modes."
fi

chmod +x "$BIN_DIR/cc-setup" "$BIN_DIR/cc-update" "$BIN_DIR/cc-install-superpowers" "$BIN_DIR/cc-install-security" "$BIN_DIR/cc-install-tasteskill" "$BIN_DIR/cc-devcontainer" "$BIN_DIR/cc-token" "$BIN_DIR/cc-update-project" "$BIN_DIR/cc-update-permissions" "$BIN_DIR/cc-vault" "$BIN_DIR/cc-dash" "$BIN_DIR/cc-factory" "$BIN_DIR/cc-docs-migrate" "$BIN_DIR/cc-selfheal" "$BIN_DIR/cc-statusline"

# ── 1. PATH ───────────────────────────────────────────────────────────────────
# A zsh user gets ~/.zshrc even before the file exists: a new Mac has none, and
# zsh never reads ~/.profile. Terminal on macOS starts bash as a login shell,
# which reads ~/.bash_profile and not ~/.bashrc.
if [[ "${SHELL:-}" == */zsh || -f "$HOME/.zshrc" ]]; then
    PROFILE="$HOME/.zshrc"
elif [[ "$OS" == macos ]]; then
    PROFILE="$HOME/.bash_profile"
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
# A dashboard already running is stopped first (waiting up to 10 s, then
# killed), so it always comes up on its usual port in the mode asked for.
# Arguments pass through, e.g. `ccdash --control`. The log holds the one-time
# pairing link (read-only), so it is written owner-only.
# The control code (--control) never touches the log or any file: cc-dash writes
# it straight to this terminal (/dev/tty). fd 3 is a pipe that carries nothing:
# cc-dash closes it once the code is shown, so `| cat` holds the prompt until
# then and the code lands above it, not in the middle of your next command.
CCDASH_LINE='ccdash() { local b r d="${XDG_STATE_HOME:-$HOME/.local/state}/cc_tool"; b="$(command -v cc-dash)" || { echo "cc-dash not on PATH" >&2; return 1; }; if pkill -u "$USER" -f "$b" 2>/dev/null; then for i in {1..50}; do pgrep -u "$USER" -f "$b" >/dev/null || break; sleep 0.2; done; pkill -KILL -u "$USER" -f "$b" 2>/dev/null && sleep 0.5; fi; mkdir -p "$d" || return 1; r="$( (umask 077; nohup "$b" --ready-fd 3 "$@" 3>&1 >>"$d/cc-dash.log" 2>&1 </dev/null &) )"; [ "$r" = refused ] && return 1; echo "cc-dash is opening in your browser (log: $d/cc-dash.log)"; }'

if grep -qxF -- "$CCDASH_LINE" "$PROFILE" 2>/dev/null; then
    echo "✓ ccdash already defined ($PROFILE)"
elif grep -qF 'ccdash() {' "$PROFILE" 2>/dev/null; then
    # An older ccdash: replace its one line in place (v0.2.1 waits for the control code).
    tmp="$(mktemp)"
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" == "ccdash() {"* ]]; then printf '%s\n' "$CCDASH_LINE"; else printf '%s\n' "$line"; fi
    done < "$PROFILE" > "$tmp"
    cat "$tmp" > "$PROFILE" && rm -f "$tmp"
    echo "✓ Updated ccdash in $PROFILE (it now waits for the previous dashboard to stop before starting)"
else
    echo "# cc_tool: ccdash opens the dashboard (cc-dash) in the background" >> "$PROFILE"
    echo "$CCDASH_LINE" >> "$PROFILE"
    echo "✓ Added ccdash to $PROFILE (run: ccdash, or ccdash --control)"
fi

# Claude Code hands a subscription's 5-hour and weekly usage limits only to the
# status line command, so cc-dash shows them only when cc-statusline is the
# user-level status line (every session in every project then records them).
# An existing status line is left alone.
"$BIN_DIR/cc-statusline" --install || echo "  (cc-dash will show no usage limits until cc-statusline is your status line)"

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
