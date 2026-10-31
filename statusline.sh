#!/usr/bin/env bash

cat >/dev/null
if style="$(bash "$HOME/.copilot/style-levers-statusline.sh")"; then
    printf '%s\n' "$style"
else
    printf '%s\n' 'style indicator: unavailable'
fi
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! python3 "$script_dir/plugin/network_monitor.py" --cached --color; then
    printf '%s\n' 'Copilot connection: Check unavailable'
fi
