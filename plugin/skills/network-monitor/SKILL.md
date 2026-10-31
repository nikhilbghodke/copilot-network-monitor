---
name: network-monitor
description: Set up, update, inspect, or remove the local Copilot connection status bar and macOS login service.
---

# Network monitor

Use the deterministic Python utility bundled with this skill; no model requests
are needed for monitoring. Do not inspect conversations, credentials, or session
logs.

Resolve the plugin root as `../..` relative to the directory containing this
SKILL.md. Use that absolute path, not the current workspace, when running
`python3 <plugin-root>/manage.py`.
Python 3.10+, macOS, curl, and Copilot CLI are required.

- Set up or update: `python3 <plugin-root>/manage.py setup`
- Check installation: `python3 <plugin-root>/manage.py status`
- Remove service and restore the prior status bar:
  `python3 <plugin-root>/manage.py remove`

Default endpoint: `https://api.githubcopilot.com/_ping`. For Enterprise routing,
pass `--endpoint https://api.enterprise.githubcopilot.com/_ping` to setup.
Existing installations retain their endpoint and interval on subsequent setup.

Setup writes a per-user LaunchAgent, starts it at login, and preserves the user's
previous status indicator. It makes small unauthenticated HTTPS checks every 25
seconds, even when Copilot is closed. Explain these effects and obtain approval
before setup unless the user already explicitly requested them. Do not install
system dependencies or change proxy configuration automatically.

After setup, run `status`. A failed connectivity check is not a failed installation.
If setup reports a standalone sampler, report it and obtain approval before
stopping that exact process; never kill processes by name.

After `copilot plugin update copilot-network-monitor@copilot-network-tools`, rerun
setup to refresh the installed runtime. Before
`copilot plugin uninstall copilot-network-monitor@copilot-network-tools`, run
remove first. Disabling or uninstalling the plugin alone does not remove its
independently running login service. Do not claim otherwise.
