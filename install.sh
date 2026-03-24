#!/usr/bin/env bash
set -e

INSTALL_DIR="$HOME/.local/bin"
TMP="$(mktemp -d)"
RTLS_URL="https://raw.githubusercontent.com/AleXDE54/realtools/main/rtls.py"

mkdir -p "$INSTALL_DIR"

echo "Downloading rtls..."
curl -sSL "$RTLS_URL" -o "$TMP/rtls.py"
chmod +x "$TMP/rtls.py"
mv "$TMP/rtls.py" "$INSTALL_DIR/rtls"

# Ensure INSTALL_DIR is in ~/.profile
PROFILE="$HOME/.profile"
if ! grep -q "$INSTALL_DIR" "$PROFILE" 2>/dev/null; then
    echo "" >> "$PROFILE"
    echo "# added by rtls installer" >> "$PROFILE"
    echo "export PATH=\"\$PATH:$INSTALL_DIR\"" >> "$PROFILE"
    echo "[info] Added $INSTALL_DIR to $PROFILE. Run 'source $PROFILE' or restart your shell."
fi

echo "rtls installed to $INSTALL_DIR/rtls"
echo "Run: rtls help"
