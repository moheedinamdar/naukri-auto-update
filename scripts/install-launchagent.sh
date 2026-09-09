#!/usr/bin/env bash
# Optional: install a macOS LaunchAgent that runs ./run.sh daily at 10:00.
# It renders naukri-update.plist.template with THIS checkout's absolute paths,
# so no personal paths are ever committed. A visible browser window is expected
# (headless is blocked by Naukri/Akamai).
set -euo pipefail
cd "$(dirname "$0")/.."

DIR="$(pwd)"
TEMPLATE="naukri-update.plist.template"
LABEL="local.naukri-update"
OUT="$HOME/Library/LaunchAgents/${LABEL}.plist"

[ -f "$TEMPLATE" ] || { echo "missing $TEMPLATE" >&2; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents"

sed -e "s#__LABEL__#${LABEL}#g" \
    -e "s#__WORKDIR__#${DIR}#g" \
    -e "s#__RUN_SH__#${DIR}/run.sh#g" \
    "$TEMPLATE" > "$OUT"

launchctl unload "$OUT" 2>/dev/null || true
launchctl load "$OUT"

echo "Installed LaunchAgent: $OUT"
echo "Runs ./run.sh every day at 10:00 (a browser window appears briefly, that's expected)."
echo "Remove with:  launchctl unload \"$OUT\" && rm \"$OUT\""
