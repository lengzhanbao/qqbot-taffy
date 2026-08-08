"""Offline smoke test for the QQBot project."""
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bridge_qq


def main():
    assert "永雏塔菲" in bridge_qq.SYSTEM_PROMPT
    assert bridge_qq._sanitize_reply("**你好**") == "你好"
    assert bridge_qq._check_safety("我想自杀")[0] is False
    queries = bridge_qq._split_sub_queries(
        "洛克王国世界草系徽章怎么弄")
    assert any("洛克王国" in q for q in queries)
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
