# -*- coding: utf-8 -*-
"""隐藏重启 QQ 桥接：不弹 cmd / powershell 窗口。"""
import os
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHONW = os.environ.get(
    "PYTHONW",
    sys.executable.replace("python.exe", "pythonw.exe"),
)
BRIDGE = os.path.join(BASE_DIR, "bridge_qq.py")
WORKDIR = BASE_DIR
LOG_FILE = os.path.join(WORKDIR, "logs", "restart_hidden.log")
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

PS_FIND = (
    "Get-CimInstance Win32_Process | "
    "Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and $_.CommandLine -match 'bridge_qq\\.py' } | "
    "Select-Object -ExpandProperty ProcessId"
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
            encoding="gbk",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            startupinfo=_startup_info(),
        )
    except Exception as exc:
        return None


def _log(text):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except Exception:
        pass


def _find_bridge_pids():
    out = _run_hidden(["powershell", "-NoProfile", "-Command", PS_FIND])
    if not out or not out.stdout:
        return []
    try:
        return [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    except Exception:
        return []


def main():
    pids = _find_bridge_pids()
    _log(f"重启开始，旧桥接 PID: {pids}")
    for pid in pids:
        _run_hidden(["taskkill", "/F", "/PID", str(pid), "/T"], timeout=15)
    if pids:
        time.sleep(2)

    proc = subprocess.Popen(
        [PYTHONW, BRIDGE],
        cwd=WORKDIR,
        creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        startupinfo=_startup_info(),
    )
    time.sleep(3)
    new_pids = _find_bridge_pids()
    if new_pids:
        _log(f"桥接已重启，PID {new_pids[0]}")
    else:
        _log(f"警告：未检测到新桥接（启动 PID {proc.pid}）")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _log(f"重启失败: {repr(exc)}")
