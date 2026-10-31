import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

import pytest

import manage
import network_monitor


@pytest.fixture
def install(tmp_path, monkeypatch):
    home = tmp_path / "home with spaces"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("COPILOT_HOME", str(home / ".copilot"))
    monkeypatch.setattr(manage.shutil, "which", lambda command: "/bin/copilot")
    loaded = {"value": False}
    monkeypatch.setattr(manage, "service_loaded", lambda: loaded["value"])

    def launchctl(*args, **kwargs):
        if args[0] == "bootstrap":
            loaded["value"] = True
        elif args[0] == "bootout":
            loaded["value"] = False
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(manage, "launchctl", launchctl)
    monkeypatch.setattr(manage, "wait_ready", lambda *args: None)
    return manage.paths(), loaded


def test_setup_preserves_settings_and_repeat_setup_does_not_wrap_itself(install):
    location, loaded = install
    previous = {"command": "printf old-style", "padding": 2, "refreshInterval": 5}
    manage.write_json(location["settings"], {"theme": "dim", "statusLine": previous})

    manage.setup(location, "https://api.enterprise.githubcopilot.com/_ping", 25)
    manage.setup(location)

    state = manage.read_json(location["state"])
    settings = manage.read_json(location["settings"])
    assert state["previous_status_line"] == previous
    assert settings["theme"] == "dim"
    assert settings["statusLine"]["padding"] == 2
    assert settings["statusLine"]["refreshInterval"] == 1
    assert loaded["value"]
    assert manage.read_json(location["config"])["endpoint"] == "https://api.enterprise.githubcopilot.com/_ping"
    agent = plistlib.loads(location["agent"].read_bytes())
    assert agent["RunAtLoad"] and agent["KeepAlive"]
    assert agent["ProgramArguments"][1] == str(location["runtime"] / "network_monitor.py")
    assert location["state"].stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("original", [
    {"theme": "dim"}, {"theme": "dim", "statusLine": None},
    {"theme": "dim", "statusLine": {"command": "printf original", "padding": 1}},
])
def test_remove_restores_exact_original_status_and_preserves_other_settings(install, original):
    location, loaded = install
    manage.write_json(location["settings"], original)
    manage.setup(location)
    changed = manage.read_json(location["settings"])
    changed["theme"] = "github"
    manage.write_json(location["settings"], changed)

    manage.remove(location)
    manage.remove(location)

    assert manage.read_json(location["settings"]) == {**original, "theme": "github"}
    assert not loaded["value"]
    assert not location["agent"].exists()
    assert not location["state"].exists()
    assert not (location["runtime"] / "network_monitor.py").exists()


def test_newer_status_configuration_is_not_overwritten_on_update_or_remove(install, capsys):
    location, _ = install
    manage.setup(location)
    newer = {"command": "printf my-new-status"}
    manage.write_json(location["settings"], {"statusLine": newer})

    with pytest.raises(RuntimeError, match="Status line has changed"):
        manage.setup(location)
    manage.remove(location)

    assert manage.read_json(location["settings"]) == {"statusLine": newer}
    assert "Preserving" in capsys.readouterr().out


def test_failure_after_writing_runtime_can_be_cleaned_up(install, monkeypatch):
    location, _ = install
    original = {"theme": "dim"}
    manage.write_json(location["settings"], original)

    def fail_bootstrap(*args, **kwargs):
        raise subprocess.CalledProcessError(5, args, stderr="bootstrap failed")

    monkeypatch.setattr(manage, "launchctl", fail_bootstrap)
    with pytest.raises(subprocess.CalledProcessError):
        manage.setup(location)
    assert manage.read_json(location["settings"]) == original
    assert location["state"].exists()

    manage.remove(location)
    assert not location["state"].exists()
    assert manage.read_json(location["settings"]) == original


def test_settings_changed_during_setup_are_preserved(install, monkeypatch):
    location, _ = install
    new_settings = {"theme": "github", "statusLine": {"command": "printf newer"}}
    monkeypatch.setattr(manage, "wait_ready", lambda *args: manage.write_json(location["settings"], new_settings))

    with pytest.raises(RuntimeError, match="Status line changed during"):
        manage.setup(location)

    assert manage.read_json(location["settings"]) == new_settings
    manage.remove(location)
    assert manage.read_json(location["settings"]) == new_settings


def test_existing_sampler_blocks_setup_without_changing_user_settings(install):
    location, _ = install
    location["cache"].mkdir(parents=True)
    with (location["cache"] / "sampler.lock").open("a") as handle:
        manage.fcntl.flock(handle, manage.fcntl.LOCK_EX | manage.fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="standalone sampler"):
            manage.setup(location)

    assert not location["state"].exists()
    assert not location["settings"].exists()


def test_unmanaged_runtime_is_not_overwritten(install):
    location, _ = install
    location["runtime"].mkdir(parents=True)
    script = location["runtime"] / "network_monitor.py"
    script.write_text("unrelated file")

    with pytest.raises(RuntimeError, match="Unmanaged files"):
        manage.setup(location)

    assert script.read_text() == "unrelated file"


def test_conflicting_copilot_home_is_rejected(install):
    location, _ = install
    manage.setup(location)
    other = {**location, "settings": location["settings"].parent / "another.json"}

    with pytest.raises(RuntimeError, match="another configuration"):
        manage.remove(other)

    assert location["agent"].exists()


def test_installed_renderer_preserves_arbitrary_status_command_and_is_cache_only(install):
    location, _ = install
    previous = {"command": "printf 'custom-status'"}
    manage.write_json(location["settings"], {"statusLine": previous})
    manage.setup(location)
    env = {**os.environ, "NO_COLOR": "1", "PYTHONDONTWRITEBYTECODE": "1"}

    result = subprocess.run(
        [sys.executable, str(location["runtime"] / "statusline.py")],
        input="{}", text=True, capture_output=True, timeout=5, check=True, env=env,
    )

    assert result.stdout == "custom-status\nCopilot connection: Outdated\n"
    assert not (location["cache"] / "network.json").exists()


def test_previous_status_failure_does_not_hide_network_indicator(install):
    location, _ = install
    manage.write_json(location["settings"], {"statusLine": {"command": "exit 7"}})
    manage.setup(location)

    result = subprocess.run(
        [sys.executable, str(location["runtime"] / "statusline.py")],
        input="{}", text=True, capture_output=True, timeout=5, check=True,
        env={**os.environ, "NO_COLOR": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    )

    assert result.stdout == "Existing status indicator unavailable\nCopilot connection: Outdated\n"
    assert "Previous status command failed" in result.stderr


@pytest.mark.parametrize("endpoint, interval", [
    ("http://example.test/_ping", 25),
    ("https://user:pass@example.test/_ping", 25),
    ("https://example.test/responses", 25),
    ("https://example.test/_ping?token=test", 25),
    ("https://example.test/_ping", 0),
    ("https://example.test/_ping", True),
])
def test_bad_configuration_does_not_modify_the_machine(install, endpoint, interval):
    location, _ = install
    with pytest.raises(ValueError):
        manage.setup(location, endpoint, interval)
    assert not location["state"].exists()
    assert not location["settings"].exists()


def test_load_config_changes_endpoint_and_interval(monkeypatch, tmp_path):
    monkeypatch.setattr(network_monitor, "PING_URL", network_monitor.PING_URL)
    monkeypatch.setattr(network_monitor, "PROBE_INTERVAL", network_monitor.PROBE_INTERVAL)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"endpoint": "https://api.githubcopilot.com/_ping", "interval": 30}))

    network_monitor.load_config(path)

    assert network_monitor.PING_URL == "https://api.githubcopilot.com/_ping"
    assert network_monitor.PROBE_INTERVAL == 30
