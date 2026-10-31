#!/usr/bin/env python3
"""Render the existing status command followed by cached connection status."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import network_monitor


def main():
    root = Path(__file__).resolve().parent
    try:
        state = json.loads((root / "installation.json").read_text())
        network_monitor.load_config(root / "config.json")
        previous = state["previous_status_line"] or {}
        command = previous.get("command")
        if command:
            payload = "" if sys.stdin.isatty() else sys.stdin.read()
            try:
                # This is the user's pre-existing, explicitly configured shell command.
                result = subprocess.run(
                    command, shell=True, input=payload, text=True,
                    capture_output=True, timeout=2, check=True,
                )
                if result.stdout.strip():
                    print(result.stdout.rstrip("\n"))
                if result.stderr:
                    print(result.stderr, file=sys.stderr, end="")
            except (OSError, subprocess.SubprocessError) as error:
                print("Existing status indicator unavailable")
                print(f"Previous status command failed: {error}", file=sys.stderr)
        network = network_monitor.read_cache(Path(state["cache_dir"]) / "network.json")
        print(network_monitor.render(network, time.time(), color=not os.environ.get("NO_COLOR")))
    except (OSError, ValueError, TypeError, KeyError) as error:
        print("Copilot connection: Check unavailable")
        print(f"Status line error: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
