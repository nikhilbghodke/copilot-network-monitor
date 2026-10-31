#!/usr/bin/env python3
"""Set up, inspect, or remove the per-user macOS Copilot network monitor."""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from network_monitor import validate_config


NAME = "copilot-network-monitor"
LABEL = "local.copilot-network-monitor"
SOURCE = Path(__file__).resolve().parent
RUNTIME_FILES = ("network_monitor.py", "statusline.py")
DEFAULT_ENDPOINT = "https://api.githubcopilot.com/_ping"


def paths():
    home = Path.home()
    runtime = home / "Library/Application Support" / NAME
    return {
        "runtime": runtime,
        "state": runtime / "installation.json",
        "config": runtime / "config.json",
        "settings": Path(os.environ.get("COPILOT_HOME", home / ".copilot")).expanduser().resolve() / "settings.json",
        "agent": home / "Library/LaunchAgents" / f"{LABEL}.plist",
        "cache": home / "Library/Caches" / NAME,
        "log": home / "Library/Logs" / NAME / "monitor.log",
    }


def read_json(path):
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def atomic_write(path, data):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(data)
            handle.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, indent=2) + "\n").encode())


@contextmanager
def install_lock(location):
    location["runtime"].mkdir(parents=True, mode=0o700, exist_ok=True)
    with (location["runtime"] / "setup.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another setup/removal is running") from None
        yield


def launchctl(*args, check=True):
    return subprocess.run(
        ["/bin/launchctl", *args], capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=15, check=check,
    )


def service_target():
    return f"gui/{os.getuid()}/{LABEL}"


def service_loaded():
    result = launchctl("print", service_target(), check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 113 and "Could not find service" in result.stderr:
        return False
    raise RuntimeError(f"Unable to inspect login service: {result.stderr.strip()}")


def stop_service():
    if service_loaded():
        launchctl("bootout", "--wait", service_target())
        deadline = time.monotonic() + 10
        while service_loaded():
            if time.monotonic() >= deadline:
                raise RuntimeError("Login service did not stop in time")
            time.sleep(0.1)


def require_free_sampler(location):
    location["cache"].mkdir(parents=True, mode=0o700, exist_ok=True)
    with (location["cache"] / "sampler.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("A standalone sampler is running. Stop it before setup, then retry.") from None


def launch_agent(location, python):
    return {
        "Label": LABEL,
        "ProgramArguments": [
            python, str(location["runtime"] / "network_monitor.py"),
            "--watch", "--config", str(location["config"]),
            "--cache-dir", str(location["cache"]), "--log-file", str(location["log"]),
        ],
        "WorkingDirectory": str(location["runtime"]),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "ProcessType": "Background",
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    }


def wait_ready(location, started):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = read_json(location["cache"] / "network.json")
        if state and state.get("updated", 0) >= started:
            return
        time.sleep(0.2)
    raise RuntimeError(f"Sampler did not update its cache. See {location['log']}; rerun setup or remove.")


def read_installation(location):
    state = read_json(location["state"])
    if state is not None:
        if state.get("name") != NAME or state.get("settings_path") != str(location["settings"]):
            raise RuntimeError("Installation belongs to another configuration. Use the original COPILOT_HOME.")
    return state


def configure_status_line(location, expected, replacement, expected_present, replacement_present):
    # Re-read before writing so unrelated settings changed during setup are preserved.
    settings = read_json(location["settings"]) or {}
    if ("statusLine" in settings) != expected_present or settings.get("statusLine") != expected:
        raise RuntimeError("Status line changed during setup/removal; it was not overwritten. Rerun after reviewing.")
    if replacement_present:
        settings["statusLine"] = replacement
    else:
        settings.pop("statusLine", None)
    write_json(location["settings"], settings)


def setup(location, endpoint=None, interval=None):
    settings = read_json(location["settings"]) or {}
    old_state = read_installation(location)
    if old_state and settings.get("statusLine") not in (
        old_state["installed_status_line"], old_state["previous_status_line"],
    ):
        raise RuntimeError("Status line has changed since setup. Run remove first; your new settings will be preserved.")
    if not old_state:
        if location["agent"].exists() or any((location["runtime"] / name).exists() for name in RUNTIME_FILES):
            raise RuntimeError("Unmanaged files already exist at the installation path; refusing to overwrite them.")
    config = read_json(location["config"]) or {}
    config = {
        "endpoint": endpoint if endpoint is not None else config.get("endpoint", DEFAULT_ENDPOINT),
        "interval": interval if interval is not None else config.get("interval", 25),
    }
    validate_config(config["endpoint"], config["interval"])
    python = str(Path(sys.executable).absolute())
    if not os.access("/usr/bin/curl", os.X_OK):
        raise RuntimeError("/usr/bin/curl is required")
    if not shutil.which("copilot"):
        raise RuntimeError("Copilot CLI must be installed before setup")
    previous = old_state["previous_status_line"] if old_state else settings.get("statusLine")
    if previous is not None and not isinstance(previous, dict):
        raise ValueError("Existing statusLine must be an object")
    installed = {
        **(previous or {}),
        "command": shlex.join([python, str(location["runtime"] / "statusline.py")]),
        "refreshInterval": 1,
    }
    state = {
        "name": NAME,
        "settings_path": str(location["settings"]),
        "cache_dir": str(location["cache"]),
        "previous_status_line": previous,
        "previous_status_line_present": (
            old_state["previous_status_line_present"] if old_state else "statusLine" in settings
        ),
        "installed_status_line": installed,
    }
    if old_state:
        stop_service()
    elif service_loaded():
        raise RuntimeError("An unmanaged login service already uses this label")
    require_free_sampler(location)
    write_json(location["state"], state)
    for name in RUNTIME_FILES:
        atomic_write(location["runtime"] / name, (SOURCE / name).read_bytes())
    write_json(location["config"], config)
    location["log"].parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    atomic_write(location["agent"], plistlib.dumps(launch_agent(location, python)))
    started = time.time()
    launchctl("bootstrap", f"gui/{os.getuid()}", str(location["agent"]))
    wait_ready(location, started)
    configure_status_line(
        location, settings.get("statusLine"), installed,
        "statusLine" in settings, True,
    )
    print("Network monitor installed: automatic login startup enabled.")
    print(f"Configuration: {location['config']}")


def remove(location):
    state = read_installation(location)
    if state is None:
        print("Network monitor is not installed; nothing changed.")
        return
    settings = read_json(location["settings"]) or {}
    stop_service()
    require_free_sampler(location)
    if settings.get("statusLine") == state["installed_status_line"]:
        configure_status_line(
            location, state["installed_status_line"], state["previous_status_line"],
            True, state["previous_status_line_present"],
        )
    elif settings.get("statusLine") != state["previous_status_line"]:
        print("Preserving the status line changed after installation.")
    location["agent"].unlink(missing_ok=True)
    for name in RUNTIME_FILES:
        (location["runtime"] / name).unlink(missing_ok=True)
    location["config"].unlink(missing_ok=True)
    location["state"].unlink(missing_ok=True)
    print("Network monitor removed. Previous status line restored where unchanged.")
    print("Diagnostic logs and measurement cache were retained.")


def status(location):
    state = read_installation(location)
    if not state:
        print("Not installed.")
        return 1
    loaded = service_loaded()
    cache = read_json(location["cache"] / "network.json")
    fresh = bool(cache and 0 <= time.time() - cache.get("updated", 0) < 10)
    settings = read_json(location["settings"]) or {}
    configured = settings.get("statusLine") == state["installed_status_line"]
    print(f"Login service: {'loaded' if loaded else 'not loaded'}")
    print(f"Sampler: {'live' if fresh else 'stale'}")
    print(f"Status line: {'configured' if configured else 'changed or removed'}")
    print(f"Diagnostics: {location['log']}")
    return 0 if loaded and fresh and configured else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "remove", "status"))
    parser.add_argument("--endpoint", help="HTTPS /_ping endpoint (setup only)")
    parser.add_argument("--interval", type=int, help="Seconds between checks, 5-300 (setup only)")
    args = parser.parse_args()
    try:
        if sys.platform != "darwin" or sys.version_info < (3, 10):
            raise RuntimeError("Setup currently requires macOS and Python 3.10+")
        if args.action != "setup" and (args.endpoint is not None or args.interval is not None):
            raise ValueError("--endpoint and --interval are only valid with setup")
        location = paths()
        if args.action == "status":
            return status(location)
        with install_lock(location):
            if args.action == "setup":
                setup(location, args.endpoint, args.interval)
            else:
                remove(location)
        return 0
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Network monitor {args.action} failed: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr.strip(), file=sys.stderr)
        if args.action == "setup":
            print("Fix the error and rerun setup, or run remove to undo a partially completed setup.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
