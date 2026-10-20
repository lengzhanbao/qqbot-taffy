"""Start the resident Taffy service without a console window or QQ traffic."""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "Bert-VITS2-master"
HEALTH_URL = "http://127.0.0.1:17861/health"


def health():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(HEALTH_URL, timeout=1) as response:
        result = json.loads(response.read())
        if result.get("service") != "taffy-v200" or not result.get("ready"):
            raise RuntimeError("Unexpected service at TTS address")
        return result


def main():
    try:
        print(json.dumps(health(), ensure_ascii=False))
        return
    except (OSError, ValueError):
        pass
    executable = SOURCE / ".venv-tts" / "Scripts" / "python.exe"
    if not executable.is_file():
        raise FileNotFoundError(executable)
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "data" / "tts").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    kwargs = {}
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        kwargs.update(startupinfo=startup,
                      creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    with (ROOT / "logs" / "taffy_tts.log").open("ab") as stream:
        process = subprocess.Popen(
            [str(executable), "-B", str(ROOT / "services" / "taffy_tts.py")],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
            close_fds=True, **kwargs,
        )
    deadline = time.monotonic() + 55
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("TTS exited; see E:/qqbot/logs/taffy_tts.log")
        try:
            result = health()
            (ROOT / "data" / "tts" / "service.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False))
            return
        except (OSError, ValueError):
            time.sleep(0.5)
    raise RuntimeError("TTS startup not ready within 55s; process retained; see service log")


if __name__ == "__main__":
    main()
