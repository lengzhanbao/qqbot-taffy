"""Exercise source-only recovery in a disposable E-drive project, never the bot."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(os.name != "nt", reason="PowerShell recovery script")
def test_code_only_restore_preserves_configuration_and_all_memory_files(tmp_path):
    root = tmp_path / "isolated-project"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "scripts" / "restore.ps1"
    shutil.copyfile(source, scripts / "restore.ps1")
    backup = root / "backups" / "test-snapshot"
    backup.mkdir(parents=True)
    originals = {
        "bridge_qq.py": "new source",
        ".env": "keep-secret-placeholder",
        "data/bridge_sessions.json": "keep-session",
        "data/bot_memory.md": "keep-memory",
        "data/group_policy.sqlite3": "keep-authorization",
    }
    for name, content in originals.items():
        current, old = root / name, backup / name
        current.parent.mkdir(parents=True, exist_ok=True)
        old.parent.mkdir(parents=True, exist_ok=True)
        current.write_text(content, encoding="utf-8")
        old.write_text("old backup", encoding="utf-8")
    (backup / "manifest.json").write_text(json.dumps({"files": list(originals)}), encoding="utf-8")
    quote = lambda path: "'" + str(path).replace("'", "''") + "'"
    command = (
        "function Get-CimInstance { @() }; function Read-Host { 'YES' }; "
        "& " + quote(scripts / "restore.ps1") + " -CodeOnly -BackupPath " + quote(backup)
    )
    result = subprocess.run(
        [shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert (root / "bridge_qq.py").read_text(encoding="utf-8") == "old backup"
    for name, content in originals.items():
        if name != "bridge_qq.py":
            assert (root / name).read_text(encoding="utf-8") == content
