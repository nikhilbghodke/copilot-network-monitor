---
name: network-monitor
description: Set up, update, inspect, change the check interval, or remove the local Copilot connection status bar and macOS login service.
---

# Network monitor

Use the deterministic [Python utility](../../manage.py) bundled with this skill; no model requests
are needed for monitoring. Do not inspect conversations, credentials, or session
logs.

Resolve the plugin root as `../..` relative to the directory containing this
SKILL.md. Use that absolute path, not the current workspace, when running
`python3 <plugin-root>/manage.py`.
Python 3.10+, macOS, curl, and Copilot CLI are required.

- Set up or update: `python3 <plugin-root>/manage.py setup`
- Change interval, for example to 35 seconds:
  `python3 <plugin-root>/manage.py setup --interval 35`
- Check installation: `python3 <plugin-root>/manage.py status`
- Remove service and restore the prior status bar:
  `python3 <plugin-root>/manage.py remove`

Default endpoint: `https://api.githubcopilot.com/_ping`. For Enterprise routing,
pass `--endpoint https://api.enterprise.githubcopilot.com/_ping` to setup.
Existing installations retain their endpoint and interval on subsequent setup.
Intervals must be whole seconds from 5 to 300, with a default of 25. Use the
user's requested value; do not change the endpoint when only changing interval.
The interval is the delay after a check completes; the UI still refreshes every
second. HTTP 429/5xx use exponential backoff capped at 300 seconds.
If invoked without an action, check status without changing settings.

Setup writes a per-user LaunchAgent, starts it at login, and preserves the user's
previous status indicator. It makes small unauthenticated HTTPS checks at the
configured interval, even when Copilot is closed. Explain these effects and obtain approval
before setup unless the user already explicitly requested them. Do not install
system dependencies or change proxy configuration automatically.

After setup, run `status`. A failed connectivity check is not a failed installation.
If setup reports a standalone sampler, report it and obtain approval before
stopping that exact process; never kill processes by name.

For package updates or uninstall, first use `copilot plugin list` to identify
the installed marketplace; do not assume `copilot-network-tools`.
After `copilot plugin update copilot-network-monitor@<marketplace>`, rerun setup
to refresh the installed runtime. Before
`copilot plugin uninstall copilot-network-monitor@<marketplace>`, run remove
first. Disabling or uninstalling the plugin alone does not remove its
independently running login service. Do not claim otherwise.
