# Copilot network monitor

An Agent Plugins 1.0 package for a clean, colored Copilot connection indicator:

```text
Copilot connection: Reachable · 941 ms · checked 8s ago
```

Requires macOS, Python 3.10+, curl, and Copilot CLI with custom status-line support.
No runtime Python packages, AI calls, MCP server, telemetry, or administrator access.

## Install

Register the marketplace and install the plugin:

```bash
copilot plugin marketplace add nikhilbghodke/copilot-network-monitor
copilot plugin install copilot-network-monitor@copilot-network-tools
```

Start a new Copilot session, invoke
`/copilot-network-monitor:network-monitor`, and request setup.
Installing the plugin alone does not change your status bar or start monitoring.
If the command is unknown in an existing session, run `/restart` first.

Setup starts a per-user macOS LaunchAgent immediately and at future logins. It
preserves your previous status indicator. No startup command is needed after
installation, and monitoring continues even when Copilot is closed.

The default endpoint is `https://api.githubcopilot.com/_ping`. For Enterprise
routing, request that endpoint during setup. From a source checkout, the
equivalent command is:

```bash
python3 plugin/manage.py setup --endpoint https://api.enterprise.githubcopilot.com/_ping
```

In Copilot, the installed `/copilot-network-monitor:network-monitor` skill can
perform setup, status, interval changes, updates, and removal. If needed, enable
the custom item using `/statusline`, or restart Copilot to reload its settings.

`plugin/` is the distributable package; `.github/plugin/marketplace.json` is its
marketplace catalog. For local development, register the checkout with
`copilot plugin marketplace add .` and run `python3 plugin/manage.py setup`.
Do not distribute `.venv`, local caches, or `previous-statusline.json`.

## Inspect, update, and remove

Use `/copilot-network-monitor:network-monitor` for status, setup after an update,
or removal of the background service. The Python commands below assume a source
checkout:

```bash
python3 plugin/manage.py status
copilot plugin list

# After changing this local package, refresh the installed runtime:
python3 plugin/manage.py setup

# For a remotely installed package:
copilot plugin update copilot-network-monitor@copilot-network-tools
# Then use /copilot-network-monitor:network-monitor to rerun setup.

# Remove in this order:
python3 plugin/manage.py remove
copilot plugin uninstall copilot-network-monitor@copilot-network-tools
```

These examples use this repository's `copilot-network-tools` marketplace. If you
installed from another marketplace, use the marketplace shown by
`copilot plugin list` in the update and uninstall commands.

Setup is safe to repeat and retains the original status-line backup. Removal
restores that backup only if the installed status-line configuration has not
since changed; newer user settings are left untouched. Unrelated settings and
files are preserved. Diagnostic logs and measurement caches are retained.

**Plugin update refreshes the package, not the running service: rerun setup.**
Likewise, disabling or uninstalling the plugin alone does not stop or remove the
login service. Run `remove` first. The utility reports partial failures and can
be rerun to repair setup or undo it.

## What it measures

Only an unauthenticated HTTPS GET to the configured `/_ping` endpoint. The small
response body is discarded (4 KiB limit), redirects are not followed, and no
GitHub API requests, traffic counters, conversations, or session logs are read.

Only the status is colored: green **Reachable** for HTTP 200, red **Check failed**
for transport errors or other HTTP statuses, and yellow **Outdated** or
**Checking…** for stale or pending observations. `NO_COLOR=1` suppresses colors.
The duration includes DNS, connection setup, TLS, and the response. It is not
maximum bandwidth, pure network latency, or a guarantee of model availability.

By default, the sampler checks 25 seconds after each previous check completes,
including after timeouts. Only HTTP 429 and 5xx use exponential backoff from the
configured interval, capped at 300 seconds (50, 100, 200, then 300 by default).
The UI reads a cache every second without making network calls. Samples older
than twice the configured interval, or a heartbeat over ten seconds old, are
shown as outdated. Requests have a three-second curl deadline and a four-second
process deadline.

The existing status command receives Copilot's status JSON as before. The network
monitor does not store or transmit that input.

## Configuration and files

Invoke `/copilot-network-monitor:network-monitor` and ask to set the interval,
for example, "Set my interval to 35 seconds." From a source checkout:

```bash
python3 plugin/manage.py setup --interval 35
```

The interval must be a whole number from 5 to 300 seconds; the default is 25.
It controls the delay after each check completes, not the one-second status-bar
refresh. Server errors and rate limits can still cause longer backoff.
Options are retained on subsequent setup calls unless explicitly overridden.
Use `COPILOT_HOME` if your CLI settings live outside `~/.copilot`.

| Purpose | Location |
|---|---|
| Stable runtime, configuration, original status-line backup | `~/Library/Application Support/copilot-network-monitor/` |
| Login service | `~/Library/LaunchAgents/local.copilot-network-monitor.plist` |
| Cached observations | `~/Library/Caches/copilot-network-monitor/` |
| Diagnostics, rotated at 256 KiB with two backups | `~/Library/Logs/copilot-network-monitor/monitor.log` |

The LaunchAgent records the Python executable used for setup. If you remove or
replace that Python installation, rerun setup with the new interpreter.

Curl has its own TLS and proxy behavior. The login service does **not** inherit
your shell's proxy variables, Copilot's `proxyUrl`, or OS PAC settings. No proxy
credentials are copied automatically. A successful check does not prove that the
CLI uses the same route or that an authenticated model stream will succeed.

## Development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install 'pytest>=8,<10'

PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider

python3 plugin/network_monitor.py --cached --json
```

The old root `statusline.sh` is a compatibility wrapper for the original local
installation. New installations do not use it or depend on the style-levers plugin.

## License

[MIT](LICENSE). The distributable plugin includes its own copy of the license.
