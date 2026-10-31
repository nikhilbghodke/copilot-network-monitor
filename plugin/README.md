# Copilot network monitor

Shows a friendly, colored connectivity indicator in Copilot's native status bar.
This is an Agent Plugins 1.0 package. macOS, Python 3.10+, curl, and Copilot CLI are
required; no runtime Python dependencies or AI requests are needed.

## Quick start

```bash
copilot plugin marketplace add nikhilbghodke/copilot-network-monitor
copilot plugin install copilot-network-monitor@copilot-network-tools
```

Start a new Copilot session, invoke
`/copilot-network-monitor:network-monitor`, and request setup.
If the command is unknown in an existing session, run `/restart` first.
Installation alone does not start the service. Or, from this package directory,
run:

```bash
python3 manage.py setup
python3 manage.py status
```

Setup preserves the existing status indicator and starts a per-user LaunchAgent
immediately and at subsequent logins. Monitoring continues when Copilot is closed.
By default, it sends a small, unauthenticated HTTPS request 25 seconds after the
previous check completes; timeouts keep that cadence, while HTTP 429/5xx back off
up to five minutes.

The default probe is `https://api.githubcopilot.com/_ping`. Enterprise routing:

```bash
python3 manage.py setup --endpoint https://api.enterprise.githubcopilot.com/_ping
```

To change the interval, invoke `/copilot-network-monitor:network-monitor` and
ask "Set my interval to 35 seconds," or run `python3 manage.py setup --interval 35`.
Allowed values are whole seconds from 5 to 300; the default is 25. Setup retains
options unless overridden. This changes connectivity checks, not the one-second
status-bar refresh; HTTP 429/5xx can still cause longer backoff.
Only connection status, check duration, and sample age are displayed. No traffic
counters, conversation history, session logs, or model calls are used.

## Updating and removing

After `copilot plugin update copilot-network-monitor@copilot-network-tools`,
rerun setup using `/copilot-network-monitor:network-monitor` to refresh the
service's runtime. Before uninstalling:

```bash
python3 manage.py remove
copilot plugin uninstall copilot-network-monitor@copilot-network-tools
```

If installed from another marketplace, use the marketplace shown by
`copilot plugin list` instead of `copilot-network-tools` in these commands.

Plugin disable/uninstall alone does not stop the independent login service.
Removal preserves newer status-line edits and unrelated settings.

The runtime and reversible setup record are under
`~/Library/Application Support/copilot-network-monitor/`; the service plist is
`~/Library/LaunchAgents/local.copilot-network-monitor.plist`. Private caches and
rotating diagnostics are under `~/Library/Caches/copilot-network-monitor/` and
`~/Library/Logs/copilot-network-monitor/`.

The service does not automatically inherit shell proxy variables, Copilot's proxy
configuration, or OS PAC settings. "Reachable" means the HTTPS check succeeded,
not that authentication, model availability, or streaming is guaranteed.

## License

[MIT](LICENSE).
