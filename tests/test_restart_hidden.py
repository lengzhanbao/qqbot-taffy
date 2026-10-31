"""Restart verification uses fake processes and isolated health/log files only."""
import json
import os
import subprocess
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import restart_bridge_hidden as launcher


@pytest.mark.skipif(os.name != "nt", reason="Windows command-line parsing")
@pytest.mark.parametrize("arguments,expected", [
    (["pythonw.exe", launcher.BRIDGE], True),
    (["python.exe", "-u", "-X", "utf8", launcher.BRIDGE], True),
    (["python.exe", "-c", "print('bridge_qq.py')", launcher.BRIDGE], False),
    (["python.exe", "-m", "bridge_qq"], False),
    (["python.exe", "bridge_qq.py"], False),
    (["python.exe", os.path.join(launcher.BASE_DIR, "other", "bridge_qq.py")], False),
    (["python.exe", launcher.BRIDGE + ".bak"], False),
    (["python.exe", "other.py", launcher.BRIDGE], False),
])
def test_process_matching_requires_the_project_script_as_entrypoint(arguments, expected):
    assert launcher._is_project_command(subprocess.list2cmdline(arguments)) is expected


def test_process_discovery_filters_project_and_fails_closed(monkeypatch):
    monkeypatch.setattr(launcher, "_is_project_command", lambda line: line == "our-project")
    rows = [{"ProcessId": 10, "CommandLine": "other-project"},
            {"ProcessId": 20, "CommandLine": "our-project"}]
    monkeypatch.setattr(launcher, "_run_hidden", lambda *a: SimpleNamespace(
        returncode=0, stdout=json.dumps(rows)))
    assert launcher._find_bridge_pids() == [20]
    monkeypatch.setattr(launcher, "_run_hidden", lambda *a: SimpleNamespace(
        returncode=1, stdout=""))
    with pytest.raises(RuntimeError):
        launcher._find_bridge_pids()


@pytest.mark.parametrize("mode,expected", [
    ("fresh", True), ("child_pid", True), ("old_pid", False), ("stale_start", False),
    ("stale_update", False), ("disconnected", False), ("dead", False), ("invalid", False),
])
def test_health_requires_fresh_connected_state_for_the_new_pid(monkeypatch, tmp_path, mode, expected):
    started = datetime(2026, 10, 16, 20).timestamp()
    state = {"pid": 20, "ws_connected": True,
             "start_time": datetime.fromtimestamp(started + 1).isoformat(),
             "updated_at": datetime.fromtimestamp(started + 2).isoformat()}
    if mode == "old_pid":
        state["pid"] = 10
    if mode == "stale_start":
        state["start_time"] = datetime.fromtimestamp(started - 1).isoformat()
    if mode == "stale_update":
        state["updated_at"] = datetime.fromtimestamp(started - 1).isoformat()
    if mode == "disconnected":
        state["ws_connected"] = False
    health = tmp_path / "health.json"
    health.write_text("invalid" if mode == "invalid" else json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(launcher, "HEALTH_FILE", str(health))
    live_pids = {10, 20} if mode == "child_pid" else ({20} if mode != "dead" else set())
    monkeypatch.setattr(launcher, "_find_bridge_pids", lambda: sorted(live_pids))
    clock = iter([0, 0.1, 2])
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(launcher.time, "sleep", lambda _: None)
    proc = SimpleNamespace(pid=10 if mode == "child_pid" else 20,
                           poll=lambda: 1 if mode == "dead" else None)
    assert launcher._wait_for_health(proc, started, timeout=1) is expected


@pytest.mark.parametrize("healthy", [True, False])
def test_restart_preserves_startup_logs_and_reports_ws_verification(monkeypatch, tmp_path, healthy):
    messages, commands, launched = [], [], []
    discovery = iter([[10], [10], []])
    monkeypatch.setattr(launcher, "_find_bridge_pids", lambda: next(discovery))
    monkeypatch.setattr(launcher, "_run_hidden", lambda cmd, **kw: (
        commands.append(cmd) or SimpleNamespace(returncode=0)))
    monkeypatch.setattr(launcher, "_startup_info", lambda: None)
    monkeypatch.setattr(launcher, "_log", messages.append)
    monkeypatch.setattr(launcher.time, "sleep", lambda _: None)
    monkeypatch.setattr(launcher, "WORKDIR", str(tmp_path))
    monkeypatch.setattr(launcher, "LOG_FILE", str(tmp_path / "logs" / "restart.log"))
    proc = SimpleNamespace(pid=20, poll=lambda: None)

    def popen(args, **kwargs):
        assert not kwargs["stdout"].closed and not kwargs["stderr"].closed
        launched.append((args, kwargs))
        return proc

    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    monkeypatch.setattr(launcher, "_wait_for_health", lambda p, started: healthy)
    assert launcher.main() == (0 if healthy else 1)
    assert commands == [["taskkill", "/F", "/PID", "10"]]
    args, kwargs = launched[0]
    assert args == [launcher.PYTHONW, launcher.BRIDGE]
    assert kwargs["creationflags"] & launcher.CREATE_NO_WINDOW
    assert kwargs["env"]["TMP"] == str(tmp_path / ".tmp")
    assert kwargs["stdout"].closed and kwargs["stderr"].closed
    assert len(list((tmp_path / "logs").glob("startup_*.log"))) == 2
    assert any("已重启并连接" in line for line in messages) is healthy


def test_discovery_failure_does_not_launch_or_kill_any_process(monkeypatch):
    monkeypatch.setattr(launcher, "_find_bridge_pids", Mock(side_effect=RuntimeError("inspect failed")))
    launch, run = Mock(), Mock()
    monkeypatch.setattr(launcher.subprocess, "Popen", launch)
    monkeypatch.setattr(launcher, "_run_hidden", run)
    with pytest.raises(RuntimeError):
        launcher.main()
    launch.assert_not_called()
    run.assert_not_called()
