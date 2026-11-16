#!/usr/bin/env bash
# Opens Dungeon Delve in its own terminal window.
GAME_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
RUN="cd \"$GAME_DIR\" && python3 dungeon_delve.py; status=\$?; \
if [ \$status -ne 0 ]; then echo; echo \"Game exited with an error (code \$status).\"; read -rp 'Press Enter to close...'; fi"

if command -v konsole >/dev/null; then
    exec konsole --hide-menubar --hide-tabbar -p TerminalColumns=100 -p TerminalRows=40 \
        -p tabtitle="Dungeon Delve" -e bash -c "$RUN"
elif command -v alacritty >/dev/null; then
    exec alacritty --title "Dungeon Delve" -o window.dimensions.columns=100 -o window.dimensions.lines=40 \
        -e bash -c "$RUN"
else
    exec xterm -T "Dungeon Delve" -geometry 100x40 -e bash -c "$RUN"
fi
