# -*- coding: utf-8 -*-
"""隐藏重启 QQ 桥接：不弹 cmd / powershell 窗口。"""
import os
import ctypes
import json
import subprocess
import sys
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHONW = os.environ.get(
    "PYTHONW",
    sys.executable.replace("python.exe", "pythonw.exe"),
)
BRIDGE = os.path.join(BASE_DIR, "bridge_qq.py")
WORKDIR = BASE_DIR
LOG_FILE = os.path.join(WORKDIR, "logs", "restart_hidden.log")
HEALTH_FILE = os.path.join(WORKDIR, "data", "health.json")
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

PS_FIND = (
    "$ErrorActionPreference = 'Stop'; "
    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
    "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe' OR Name = 'pythonw.exe'\" | "
    "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
)


def _startup_info():
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    return info


def _run_hidden(cmd, timeout=30):
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            startupinfo=_startup_info(),
        )
    except Exception as exc:
        raise RuntimeError("hidden command failed") from exc


def _log(text):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except Exception:
        pass


def _command_line_args(command_line):
    from ctypes import wintypes
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    parse = shell.CommandLineToArgvW
    parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(wintypes.LPWSTR)
    count = ctypes.c_int()
    argv = parse(command_line, ctypes.byref(count))
    if not argv:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [argv[index] for index in range(count.value)]
    finally:
        free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(ctypes.cast(argv, ctypes.c_void_p))


def _is_project_command(command_line):
    if not command_line:
        return False
    args = _command_line_args(command_line)
    index = 1
    while index < len(args):
        arg = args[index]
        if arg in ("-c", "-m", "-"):
            return False
        if arg in ("-W", "-X"):
            index += 2
            continue
        if arg == "--":
            index += 1
            break
        if not arg.startswith("-"):
            break
        index += 1
    if index >= len(args) or not os.path.isabs(args[index]):
        return False
    return os.path.normcase(os.path.realpath(args[index])) == os.path.normcase(os.path.realpath(BRIDGE))


def _find_bridge_pids():
    out = _run_hidden(["powershell", "-NoProfile", "-Command", PS_FIND])
    if out.returncode:
        raise RuntimeError("cannot inspect bridge processes")
    if not out.stdout.strip():
        return []
    rows = json.loads(out.stdout)
    rows = rows if isinstance(rows, list) else [rows]
    return [int(row["ProcessId"]) for row in rows
            if _is_project_command(row.get("CommandLine"))]


def _wait_for_health(proc, started_at, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            live_bridge_pids = set(_find_bridge_pids())
        except RuntimeError:
            live_bridge_pids = set()
        try:
            with open(HEALTH_FILE, encoding="utf-8") as stream:
                state = json.load(stream)
            if (int(state.get("pid") or 0) in live_bridge_pids
                    and state.get("ws_connected") is True
                    and datetime.fromisoformat(state["start_time"]).timestamp() >= started_at
                    and datetime.fromisoformat(state["updated_at"]).timestamp() >= started_at):
                return True
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass
        if proc.poll() is not None and not live_bridge_pids:
            return False
        time.sleep(0.5)
    return False


def main():
    pids = _find_bridge_pids()
    _log(f"重启开始，旧桥接 PID: {pids}")
    for pid in pids:
        if pid not in _find_bridge_pids():
            continue
        result = _run_hidden(["taskkill", "/F", "/PID", str(pid)], timeout=15)
        if result.returncode and pid in _find_bridge_pids():
            raise RuntimeError(f"bridge process did not stop: {pid}")
    if pids:
        time.sleep(2)
        if _find_bridge_pids():
            raise RuntimeError("old bridge is still running; startup aborted")

    log_dir = os.path.dirname(LOG_FILE)
    os.makedirs(log_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stdout_path = os.path.join(log_dir, f"startup_stdout_{stamp}.log")
    stderr_path = os.path.join(log_dir, f"startup_stderr_{stamp}.log")
    child_env = dict(os.environ)
    temp_dir = os.path.join(WORKDIR, ".tmp")
    os.makedirs(temp_dir, exist_ok=True)
    child_env.update(TEMP=temp_dir, TMP=temp_dir, PYTHONDONTWRITEBYTECODE="1")
    started_at = time.time()
    with open(stdout_path, "ab") as stdout, open(stderr_path, "ab") as stderr:
        proc = subprocess.Popen(
            [PYTHONW, BRIDGE],
            cwd=WORKDIR,
            env=child_env,
            creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
            stdout=stdout,
            stderr=stderr,
            startupinfo=_startup_info(),
        )
    _log(f"启动 PID {proc.pid}; stdout={stdout_path}; stderr={stderr_path}")
    if _wait_for_health(proc, started_at):
        _log(f"桥接已重启并连接 WebSocket，PID {proc.pid}")
        return 0
    _log(f"启动未通过 WebSocket 健康检查，PID {proc.pid}; exit_code={proc.poll()}; 请查看启动日志")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        _log(f"重启失败: {repr(exc)}")
        sys.exit(1)
