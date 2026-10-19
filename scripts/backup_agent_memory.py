"""Create a quota-accounted SQLite snapshot; invoked by scripts/backup.ps1."""
from datetime import datetime
import json
from pathlib import Path
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from qqbot_core.config import (
        AGENT_MEMORY_DIR, AGENT_MEMORY_LIMIT_BYTES, AGENT_MEMORY_WARNING_RATIO,
        GROUP_POLICY_FILE, SESSIONS_FILE, GROUP_STYLES_FILE, KNOWLEDGE_FILE,
    )
    from qqbot_core.agent_memory import AgentMemory
    from qqbot_core.group_policy import GroupPolicy

    root = Path(AGENT_MEMORY_DIR).resolve()
    if root.parent != (ROOT / "data").resolve():
        raise ValueError("snapshot root must be the project agent_memory directory")
    if not (root / "memory.sqlite3").is_file():
        print(json.dumps({"status": "not_initialized"}))
        return
    policy = GroupPolicy(persistence_path=GROUP_POLICY_FILE)
    if not policy.persistence_ok:
        raise RuntimeError("authorization store is unavailable")
    memory = AgentMemory(root, policy, limit_bytes=AGENT_MEMORY_LIMIT_BYTES,
                         warning_ratio=AGENT_MEMORY_WARNING_RATIO,
                         legacy_paths=(SESSIONS_FILE, GROUP_STYLES_FILE, KNOWLEDGE_FILE))
    target = root / "backups" / (datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex + ".sqlite3")
    result = memory.store.snapshot(target)
    if result is None or not Path(result).is_file():
        raise RuntimeError("memory snapshot refused or incomplete; no successful backup recorded")
    print(json.dumps({"status": "ok", "snapshot": str(result)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
