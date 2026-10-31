#!/usr/bin/env python3
"""Local, bounded network observations for Copilot's native status line."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit


PROBE_INTERVAL = 25
MAX_BACKOFF = 300
PING_URL = "https://api.enterprise.githubcopilot.com/_ping"
CURL_ERRORS = {
    5: "proxy-DNS",
    6: "DNS",
    7: "connect",
    16: "HTTP2",
    28: "timeout",
    35: "TLS",
    52: "empty-response",
    55: "send",
    56: "receive",
    60: "TLS-certificate",
    92: "HTTP2-stream",
}


def validate_config(endpoint, interval):
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path != "/_ping" or parsed.query or parsed.fragment):
        raise ValueError("Endpoint must be an HTTPS /_ping URL without credentials, query, or fragment")
    if type(interval) is not int or not 5 <= interval <= 300:
        raise ValueError("Probe interval must be an integer between 5 and 300 seconds")


def load_config(path):
    global PING_URL, PROBE_INTERVAL
    settings = json.loads(path.read_text())
    endpoint, interval = settings["endpoint"], settings["interval"]
    validate_config(endpoint, interval)
    PING_URL, PROBE_INTERVAL = endpoint, interval
    return settings


def read_cache(path):
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError(f"Unrecognized cache format: {path.name}")
    return value


def write_cache(path, value):
    value["version"] = 1
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(value, handle, allow_nan=False)
            handle.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def locked_update(path, update):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return read_cache(path)
        state = update(read_cache(path))
        write_cache(path, state)
        return state


def probe():
    args = [
        "/usr/bin/curl", "--disable", "--silent", "--show-error",
        "--output", "/dev/null", "--proto", "=https", "--max-filesize", "4096",
        "--connect-timeout", "2", "--max-time", "3",
        "--write-out", "%{http_code} %{time_namelookup} %{time_connect} "
        "%{time_appconnect} %{time_starttransfer} %{time_total}",
        PING_URL,
    ]
    try:
        result = subprocess.run(
            args, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=4, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "time": time.time()}
    if result.returncode:
        return {
            "error": CURL_ERRORS.get(result.returncode, f"curl-{result.returncode}"),
            "time": time.time(),
        }
    fields = result.stdout.split()
    if len(fields) != 6:
        raise ValueError("Unrecognized curl metrics")
    status = int(fields[0])
    timings = [float(field) for field in fields[1:]]
    if not 100 <= status <= 599 or not all(math.isfinite(value) and value >= 0 for value in timings):
        raise ValueError("Invalid curl metrics")
    return {
        "http": status, "dns_ms": timings[0] * 1000,
        "connect_ms": timings[1] * 1000, "tls_ms": timings[2] * 1000,
        "ttfb_ms": timings[3] * 1000, "total_ms": timings[4] * 1000,
        "time": time.time(),
    }


def requires_backoff(result):
    status = result.get("http", 0)
    return status == 429 or 500 <= status <= 599


def update_probe(previous, result):
    failed = bool(result.get("error")) or result.get("http") != 200
    failures = min(previous.get("failures", 0) + 1, 5) if failed else 0
    backoff_failures = min(previous.get("backoff_failures", 0) + 1, 5) if requires_backoff(result) else 0
    interval = min(PROBE_INTERVAL * 2 ** backoff_failures, MAX_BACKOFF)
    return {
        **result, "failures": failures, "backoff_failures": backoff_failures,
        "next": result["time"] + interval,
    }


def ping_state(state, passive):
    result = state.get("probe", {}) if state.get("endpoint") == PING_URL else {}
    if result and not requires_backoff(result):
        result = {**result, "backoff_failures": 0, "next": result["time"] + PROBE_INTERVAL}
    return {
        "endpoint": PING_URL,
        "probe": result,
        "passive": passive,
        "updated": state.get("updated", 0),
    }


def probe_due(result, now):
    return now >= result.get("next", 0) or now < result.get("time", 0)


def update_network(state, passive=False):
    state = ping_state(state, passive)
    if not passive and probe_due(state["probe"], time.time()):
        state["probe"] = update_probe(state["probe"], probe())
    state["updated"] = time.time()
    return state


def watch(cache_dir, passive=False):
    cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (cache_dir / "sampler.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("A sampler is already running for this cache") from None
        stop = threading.Event()
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: stop.set())
        pending = None
        with ThreadPoolExecutor(max_workers=1) as pool:
            logging.info("Network sampler running (no Copilot session data is read).")
            while not stop.is_set():
                started = time.monotonic()

                def tick(state):
                    nonlocal pending
                    state = ping_state(state, passive)
                    if pending is not None and pending.done():
                        state["probe"] = update_probe(state["probe"], pending.result())
                        pending = None
                    now = time.time()
                    if not passive and pending is None and probe_due(state["probe"], now):
                        pending = pool.submit(probe)
                    state["updated"] = now
                    return state

                locked_update(cache_dir / "network.json", tick)
                stop.wait(max(0, 1 - (time.monotonic() - started)))
    return 0


def age_text(timestamp, now):
    age = now - timestamp
    if age < -1:
        return "clock changed"
    if age < 60:
        return f"{max(0, int(age))}s ago"
    if age < 3600:
        return f"{int(age / 60)}m ago"
    return f"{int(age / 3600)}h ago"


def render(network, now, color=False):
    def label(text, code):
        status = f"\033[{code}m{text}\033[0m" if color else text
        return f"Copilot connection: {status}"

    if not network or not 0 <= now - network.get("updated", 0) <= 10:
        age = f" \u00b7 updated {age_text(network['updated'], now)}" if network.get("updated") else ""
        return label("Outdated", 33) + age
    if network.get("passive"):
        return label("Checks off", 33)
    result = network.get("probe", {}) if network.get("endpoint") == PING_URL else {}
    if not result:
        return label("Checking\u2026", 33)
    age = age_text(result["time"], now)
    if not -1 <= now - result["time"] <= PROBE_INTERVAL * 2:
        return label("Outdated", 33) + f" \u00b7 checked {age}"
    if result.get("error") or result["http"] != 200:
        return label("Check failed", 31) + f" \u00b7 checked {age}"
    return label("Reachable", 32) + f" \u00b7 {result['total_ms']:.0f} ms \u00b7 checked {age}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print measurement details as JSON")
    parser.add_argument("--passive", action="store_true", help="Do not make HTTPS requests")
    parser.add_argument("--color", action="store_true", help="Color status labels (respects NO_COLOR)")
    parser.add_argument("--config", type=Path, help="JSON file containing endpoint and interval")
    parser.add_argument("--log-file", type=Path, help="Rotating diagnostic log (3 files of at most 256 KiB)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--watch", action="store_true", help="Sample continuously until interrupted")
    mode.add_argument("--cached", action="store_true", help="Only render cached data; no commands or network requests")
    parser.add_argument("--cache-dir", type=Path, default=Path.home() / "Library/Caches/copilot-network-monitor")
    args = parser.parse_args()
    try:
        if args.log_file:
            args.log_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            handler = RotatingFileHandler(args.log_file, maxBytes=256 * 1024, backupCount=2)
            logging.basicConfig(handlers=[handler], level=logging.INFO, force=True)
        if args.config:
            load_config(args.config)
        if args.watch:
            return watch(args.cache_dir, args.passive)
        if args.cached:
            network = read_cache(args.cache_dir / "network.json")
        else:
            network = locked_update(
                args.cache_dir / "network.json",
                lambda state: update_network(state, args.passive),
            )
        if args.json:
            print(json.dumps(network, indent=2))
        else:
            print(render(network, time.time(), color=args.color and not os.environ.get("NO_COLOR")))
        return 0
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        if args.log_file:
            logging.error("Network monitor error: %s", error)
        else:
            print(f"Network monitor error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
