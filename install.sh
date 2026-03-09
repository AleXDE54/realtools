#!/usr/bin/env bash
set -e

REPO_RAW_BASE="https://raw.githubusercontent.com/AleXDE54/realtools/main"
INSTALL_DIR="$HOME/.local/bin"
TMP="$(mktemp -d)"
RTLS_URL="$REPO_RAW_BASE/rtls.py"

echo "Installing rtls to $INSTALL_DIR (user mode)..."
mkdir -p "$INSTALL_DIR"

curl -sSL "$RTLS_URL" -o "$TMP/rtls.py"
chmod +x "$TMP/rtls.py"
mv "$TMP/rtls.py" "$INSTALL_DIR/rtls"
echo "rtls installed to $INSTALL_DIR/rtls"
export PATH="$PATH:$HOME/.local/bin"
echo "rtls added to the PATH"

echo "If not working add the following to ~/.profile or ~/.bashrc (and restart shell):"
echo "export PATH=\"\$PATH:$INSTALL_DIR\""

echo "Done. Run: rtls help"
