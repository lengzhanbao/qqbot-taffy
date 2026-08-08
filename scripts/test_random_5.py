"""Randomly run 5 QQBOT pipeline samples and check output completeness."""

import asyncio
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge_qq


CASES = [
    ("洛克王国世界S3赛季怎么玩", ["洛克王国世界", "S3", "赛季"]),
    ("豆包是什么", ["豆包", "字节跳动"]),
    ("梅西C罗今年多少岁", ["梅西", "C罗"]),
    ("周杰伦《七月的极光》歌词是什么", ["七月的极光", "夕阳落在加油站的玻璃上"]),
    ("2026世界杯冠军是谁", ["西班牙"]),
    ("玄武口音是什么梗", ["玄武口音", "余承东", "竹知了"]),
    ("C罗今年多少岁", ["C罗", "41"]),
    ("《那天下雨了》是周杰伦的吗", ["那天下雨了", "周杰伦"]),
    ("英伟达市值最近突破了多少", ["英伟达", "市值"]),
    ("DeepSeek R1是什么", ["DeepSeek", "R1"]),
]


class FakeSessions:
    def get(self, key):
        return None

    def put(self, key, value):
        pass


async def run_one(bridge, query, expected):
    start = time.time()
    try:
        reply = await asyncio.wait_for(
            bridge.ask_model(
                f"random_five_{abs(hash(query))}",
                query,
                speaker="测试用户",
            ),
            timeout=120,
        )
    except Exception as exc:
        reply = f"ERR {type(exc).__name__}: {exc}"
    missing = [key for key in expected if key not in (reply or "")]
    return {
        "query": query,
        "reply": reply or "",
        "missing": missing,
        "len": len(reply or ""),
        "seconds": round(time.time() - start, 1),
        "book_ok": (reply or "").count("《") == (reply or "").count("》"),
    }


def write_report(path, rows):
    with path.open("w", encoding="utf-8") as f:
        f.write("# QQBOT 随机5题完整性测试\n")
        for row in rows:
            f.write(f"\n## {row['query']}\n")
            f.write(
                f"- 耗时: {row['seconds']}s，长度: {row['len']}，"
                f"缺词: {row['missing']}，书名号成对: {row['book_ok']}\n"
            )
            f.write(f"- 回答:\n{row['reply']}\n")


async def main():
    bridge_qq.TOOL_CALL_TIMEOUT = 8
    bridge = bridge_qq.Bridge()
    bridge.sessions = FakeSessions()
    bridge.recent_replies = []

    count = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    picked = random.sample(CASES, min(count, len(CASES)))
    rows = await asyncio.gather(*[
        run_one(bridge, query, expected)
        for query, expected in picked
    ])

    report_path = (
        Path(__file__).resolve().parents[1]
        / "logs"
        / f"test_random_{count}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    write_report(report_path, rows)

    for row in rows:
        print("=" * 45)
        print("QUERY:", row["query"])
        print("TIME:", row["seconds"], "LEN:", row["len"], "MISS:", row["missing"], "BOOK_OK:", row["book_ok"])
        print(row["reply"])
    print("\nREPORT:", report_path)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
