#!/bin/sh
# Drives the emulated radio (tools/emuport.py) with tdh3's step commands, as an AI agent would.
#   (cd tools/tdh3 && go build) && tools/emucheck.sh        # ~2 minutes, no radio needed
set -e
cd "$(dirname "$0")/.."
PY=$(uv python find pypy3.11 2>/dev/null || echo python3)
port=$(mktemp)
$PY tools/emuport.py >"$port" &
emu=$!
trap 'kill $emu; rm -f "$port"' EXIT
while [ ! -s "$port" ]; do sleep 1; done
t() { tools/tdh3/tdh3 -p "$(cat "$port")" remote --wait 3s "$@" 2>&1; }
check() { echo "$2" | grep -q "$3" && echo "PASS $1" || { echo "FAIL $1"; echo "$2"; exit 1; }; }

check 'tune 147.525' "$(t tune 147.525)" '^ 23  32  147.525'
check 'channel 2' "$(t channel 2)" '^ 27   0  \*M2'
check 'slot B' "$(t slot B)" '^ 73   1  B  (lit)'
check 'empty channel refused' "$(t channel 50 || true)" 'empty channel'
check 'settings, back home' "$(t slot A && t settings && t screen)" '^ 14   1  A  (lit)'
check 'PTT never sent' "$(t keys PTT || true)" 'PTT is never sent'
