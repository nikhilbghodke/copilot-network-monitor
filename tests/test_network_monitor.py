import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

import network_monitor as monitor


def state_for(result, updated=110):
    return {"endpoint": monitor.PING_URL, "probe": result, "updated": updated}


@pytest.mark.parametrize("code", [200, 401, 403, 404, 429, 503])
def test_only_http_200_is_a_successful_ping(code):
    completed = subprocess.CompletedProcess([], 0, f"{code} .01 .02 .05 .1 .12", "")
    with patch.object(monitor.subprocess, "run", return_value=completed):
        result = monitor.probe()

    text = monitor.render(state_for(result, result["time"]), result["time"], color=True)
    if code == 200:
        assert text.startswith("Copilot connection: \033[32mReachable\033[0m")
        assert "120 ms" in text
    else:
        assert text == "Copilot connection: \033[31mCheck failed\033[0m \u00b7 checked 0s ago"
    assert "HTTP" not in text
    assert "/_ping" not in text
    assert "CLI" not in text
    assert "healthy" not in text


def test_timeout_and_dns_failures_are_distinct():
    completed = subprocess.CompletedProcess([], 6, "000 0 0 0 0 0", "DNS failed")
    with patch.object(monitor.subprocess, "run", return_value=completed):
        assert monitor.probe()["error"] == "DNS"
    with patch.object(monitor.subprocess, "run", side_effect=subprocess.TimeoutExpired("curl", 4)):
        assert monitor.probe()["error"] == "timeout"


def test_server_failures_back_off_and_success_resets_schedule():
    previous = {}
    for _ in range(10):
        previous = monitor.update_probe(previous, {"http": 503, "time": 100})
    assert previous["next"] == 400

    recovered = monitor.update_probe(previous, {"http": 200, "time": 110})
    assert recovered["failures"] == 0
    assert recovered["next"] == 135


def test_cached_probe_does_not_run_on_each_render():
    state = state_for({"time": 100, "next": 125})
    with patch.object(monitor.time, "time", return_value=110), \
            patch.object(monitor, "probe", side_effect=AssertionError("Unexpected network request")):
        result = monitor.update_network(state)

    assert result["probe"]["next"] == 125


def test_passive_mode_never_probes():
    with patch.object(monitor, "probe", side_effect=AssertionError("Unexpected network request")):
        result = monitor.update_network({}, passive=True)

    assert "Checks off" in monitor.render(result, result["updated"])


def test_stale_probe_result_keeps_its_age():
    network = state_for({"http": 200, "total_ms": 100, "time": 10}, updated=140)

    text = monitor.render(network, 140, color=True)
    assert "Copilot connection: \033[33mOutdated\033[0m \u00b7 checked 2m ago" == text
    assert "\033[32m" not in text


def test_stopped_sampler_cannot_look_live():
    network = {
        "updated": 100,
        "sample": {"interface": "en0", "time": 100},
        "rates": {"rx": 1000, "tx": 500},
    }

    text = monitor.render(network, 120)
    assert text == "Copilot connection: Outdated \u00b7 updated 20s ago"
    assert "RX" not in text


def test_cached_mode_reads_cache_without_network_or_sampling(tmp_path, monkeypatch, capsys):
    monitor.write_cache(tmp_path / "network.json", {"updated": 100})
    monkeypatch.setattr("sys.argv", ["monitor", "--cached", "--cache-dir", str(tmp_path)])
    monkeypatch.setattr(monitor, "update_network", lambda *args: pytest.fail("Sampling in renderer"))

    assert monitor.main() == 0
    assert "Outdated" in capsys.readouterr().out


def test_cache_is_atomically_replaced_and_corruption_is_an_error(tmp_path):
    path = tmp_path / "network.json"
    monitor.locked_update(path, lambda state: {**state, "updated": 10})
    monitor.locked_update(path, lambda state: {**state, "updated": 20})
    assert monitor.read_cache(path)["updated"] == 20
    assert path.stat().st_mode & 0o077 == 0
    path.write_text("invalid json")

    with pytest.raises(json.JSONDecodeError):
        monitor.read_cache(path)


def test_concurrent_reader_does_not_wait_for_busy_sampler(tmp_path):
    path = tmp_path / "network.json"
    monitor.write_cache(path, {"updated": 10})
    with path.with_suffix(".lock").open("a") as lock:
        monitor.fcntl.flock(lock, monitor.fcntl.LOCK_EX | monitor.fcntl.LOCK_NB)
        result = monitor.locked_update(path, lambda state: pytest.fail("Acquired a busy lock"))

    assert result["updated"] == 10


def test_wrapper_preserves_style_without_a_trailing_newline(tmp_path):
    style_dir = tmp_path / ".copilot"
    style_dir.mkdir()
    (style_dir / "style-levers-statusline.sh").write_text("printf 'original style'")
    wrapper = Path(__file__).resolve().parents[1] / "statusline.sh"

    result = subprocess.run(
        ["bash", str(wrapper)], input='{"session_id":"do-not-display"}',
        capture_output=True, text=True, timeout=5,
        env={**os.environ, "HOME": str(tmp_path), "NO_COLOR": "1"}, check=True,
    )

    assert result.stdout.startswith("original style\nCopilot connection: Outdated")
    assert "do-not-display" not in result.stdout


def test_upgrade_discards_root_probe_backoff_and_stops_other_network_checks():
    previous = {
        "probes": {"CAPI": {"http": 404, "next": 9999999999}, "GitHub": {"http": 200}},
        "rates": {"rx": 100}, "sample": {"interface": "en0"},
    }
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, "200 .01 .02 .05 .1 .12", "")

    with patch.object(monitor.subprocess, "run", side_effect=run):
        result = monitor.update_network(previous)

    assert len(commands) == 1
    assert commands[0][-1] == "https://api.enterprise.githubcopilot.com/_ping"
    assert "--head" not in commands[0]
    assert "--location" not in commands[0]
    assert result["probe"]["http"] == 200
    assert not {"probes", "rates", "sample"} & result.keys()


def test_cached_old_root_response_is_not_displayed_as_a_ping_result():
    text = monitor.render({"updated": 100, "probes": {"CAPI": {"http": 404}}}, 100)
    assert text == "Copilot connection: Checking\u2026"


def test_color_can_be_disabled_and_json_has_no_escape_codes(tmp_path, monkeypatch, capsys):
    monitor.write_cache(
        tmp_path / "network.json",
        state_for({"http": 200, "total_ms": 941, "time": 100}),
    )
    monkeypatch.setattr(monitor.time, "time", lambda: 110)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("sys.argv", ["monitor", "--cached", "--color", "--cache-dir", str(tmp_path)])

    assert monitor.main() == 0
    assert capsys.readouterr().out == "Copilot connection: Reachable \u00b7 941 ms \u00b7 checked 10s ago\n"

    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setattr("sys.argv", ["monitor", "--cached", "--color", "--json", "--cache-dir", str(tmp_path)])
    assert monitor.main() == 0
    assert json.loads(capsys.readouterr().out)["probe"]["http"] == 200


def test_timeout_is_red_but_clock_change_is_stale_not_successful():
    state = state_for({"error": "timeout", "time": 100})
    assert "Copilot connection: \033[31mCheck failed\033[0m \u00b7 checked 10s ago" == monitor.render(
        state, 110, color=True,
    )

    state = state_for({"http": 200, "total_ms": 100, "time": 120})
    assert "Outdated" in monitor.render(state, 110, color=True)


@pytest.mark.parametrize("http, expected_delay", [(301, 25), (401, 25), (404, 25), (429, 50), (503, 50)])
def test_only_server_errors_and_rate_limits_back_off(http, expected_delay):
    result = monitor.update_probe({}, {"http": http, "time": 100})

    assert result["next"] == 100 + expected_delay
    assert result["failures"] == 1


@pytest.mark.parametrize("error", ["timeout", "DNS", "TLS", "connect"])
def test_repeated_network_failures_still_retry_every_25_seconds(error):
    previous = {}
    for _ in range(10):
        previous = monitor.update_probe(previous, {"error": error, "time": 100})

    assert previous["next"] == 125
    assert previous["failures"] == 5
    assert previous["backoff_failures"] == 0


def test_network_failure_resets_server_backoff_streak():
    previous = {}
    for _ in range(5):
        previous = monitor.update_probe(previous, {"http": 503, "time": 100})
    timed_out = monitor.update_probe(previous, {"error": "timeout", "time": 110})
    rate_limited = monitor.update_probe(timed_out, {"http": 429, "time": 140})

    assert timed_out["next"] == 135
    assert rate_limited["next"] == 190


@pytest.mark.parametrize("result, expected_calls", [
    ({"error": "timeout"}, 1),
    ({"http": 429}, 0),
    ({"http": 503}, 0),
])
def test_upgrade_clears_old_timeout_wait_but_preserves_server_backoff(result, expected_calls):
    state = state_for({**result, "time": 100, "next": 400, "failures": 5})
    with patch.object(monitor.time, "time", return_value=150), \
            patch.object(monitor, "probe", return_value={"http": 200, "time": 150}) as probe:
        updated = monitor.update_network(state)

    assert probe.call_count == expected_calls
    assert updated["probe"]["next"] == (175 if expected_calls else 400)


def test_missing_measurement_is_yellow_not_a_success():
    text = monitor.render(state_for({}), 110, color=True)

    assert text == "Copilot connection: \033[33mChecking\u2026\033[0m"


def test_corrupt_cache_is_reported_without_technical_details_in_status(tmp_path, monkeypatch, capsys):
    (tmp_path / "network.json").write_text("bad cache")
    monkeypatch.setattr("sys.argv", ["monitor", "--cached", "--cache-dir", str(tmp_path)])

    assert monitor.main() == 1
    output = capsys.readouterr()
    assert "Network monitor error:" in output.err
    assert output.out == ""
