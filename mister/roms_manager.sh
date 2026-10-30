#!/bin/bash
# MiSTer Scripts-menu launcher. No updater/core/save files are changed.
CLIENT_DIR=/media/fat/Scripts/.config/roms_manager
if command -v python3 >/dev/null 2>&1; then
    if [ -t 0 ] && [ -t 1 ]; then
        python3 "$CLIENT_DIR/roms_manager_client.py" --config "$CLIENT_DIR/config.json" --ui
        exit $?
    else
        python3 "$CLIENT_DIR/roms_manager_client.py" --config "$CLIENT_DIR/config.json" --sync
    fi
    result=$?
else
    echo "ROMs Manager requires Python 3.8 or newer. See the installation documentation."
    result=1
fi
if [ -t 0 ]; then
    read -r -p "Press Enter to return to MiSTer..." _answer
fi
exit "$result"
