#!/bin/bash
set -e

echo "==> Installing cloudsync..."

# Define repo (Agent/User note: update OWNER/REPO before finalizing)
REPO="OWNER/REPO"

# 1. Context aware paths
IS_ROOT=false
if [ "$(id -u)" -eq 0 ]; then
    IS_ROOT=true
    BIN_DIR="/usr/local/bin"
else
    BIN_DIR="$HOME/.local/bin"
    mkdir -p "$BIN_DIR"
fi

# 2. Ensure dependencies
if ! command -v python3 >/dev/null 2>&1 || ! command -v rclone >/dev/null 2>&1; then
    echo "Error: Python3 and rclone must be installed."
    exit 1
fi

# 3. Fetch latest release URL
echo "==> Finding latest release..."
LATEST_URL=$(curl -sSL "https://api.github.com/repos/$REPO/releases/latest" | grep '"browser_download_url":' | grep 'cloudsync.pyz' | cut -d '"' -f 4)

if [ -z "$LATEST_URL" ]; then
    echo "Error: Could not find cloudsync.pyz in the latest GitHub release."
    exit 1
fi

# 4. Download and setup executable
echo "==> Downloading to $BIN_DIR/cloudsync..."
curl -sSL "$LATEST_URL" -o "$BIN_DIR/cloudsync"
chmod +x "$BIN_DIR/cloudsync"

# Ensure BIN_DIR is in PATH for this session
export PATH="$BIN_DIR:$PATH"

# 5. Initialize config and units
echo "==> Wiring system..."
cloudsync setup
cloudsync units install

if [ "$IS_ROOT" = false ]; then
    echo "==> Enabling systemd user linger for $(whoami)..."
    loginctl enable-linger "$(whoami)" || echo "Note: Run 'loginctl enable-linger \$(whoami)' as root to allow background sync without logging in."
fi

cat <<EOF

✅ Installation Complete!

Next steps:
  1. Add a remote:  cloudsync add personal myremote:
  2. Start sync:    cloudsync resync personal
  3. Verify health: cloudsync doctor

Note: You installed as $(whoami). Run 'cloudsync' commands as this user.
EOF
