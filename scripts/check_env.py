"""Check required environment configuration before starting QQBot."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qqbot_core.config import _load_env_file


def main():
    _load_env_file()
    env_file = ROOT / ".env"
    if not env_file.exists():
        print("Missing .env. Copy config/.env.example to .env first.")
        return 1

    required = (
        "GLM_API_KEY",
        "OPENCODE_GO_API_KEY",
        "QQ_ADMIN_UIDS",
    )
    missing = [
        key for key in required
        if not os.environ.get(key)
    ]
    if missing:
        print("Missing env vars: " + ", ".join(missing))
        return 1
    if not (
        os.environ.get("OPENCODE_GO_API_KEY")
        or os.environ.get("OPENCODE_FREE_API_KEY")
    ):
        print("Missing OPENCODE_GO_API_KEY or OPENCODE_FREE_API_KEY")
        return 1

    for path in ("data", "logs"):
        target = ROOT / path
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            print(f"Cannot create {path}: {exc}")
            return 1

    print("ENV OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
