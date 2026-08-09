# -*- coding: utf-8 -*-
"""Run recent QQBot questions offline with FakeSessions; no real session writes."""

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge_qq


CASES = [
    ("圣枪哥加入BLG了", ["圣枪哥", "BLG"]),
    ("康康 状态低迷", ["康康", "状态低迷"]),
    ("张雪峰老师是谁", ["张雪峰", "峰学蔚来"]),
    ("2026世界杯冠军是谁", ["西班牙"]),
    ("黄金价格最近为什么持续上涨", ["黄金"]),
    ("珠穆朗玛峰海拔多少米", ["珠穆朗玛峰", "8848"]),
    ("C罗现在多少岁", ["C罗", "41"]),
    ("苹果M4芯片主要提升在哪里", ["M4", "芯片"]),
    ("周杰伦《七里香》是哪一年发行的", ["七里香", "周杰伦"]),
    ("玄武口音是什么梗", ["玄武口音", "余承东"]),
]


class FakeSessions:
    def get(self, key):
        return None

    def put(self, key, value):
        pass


async def run_one(bridge, index, query, expected):
    start = time.time()
    try:
        reply = await asyncio.wait_for(
            bridge.ask_model(
                f"recent_offline_{index}",
                query,
                speaker="测试用户",
            ),
            timeout=120,
        )
    except Exception as exc:
        reply = f"ERR {type(exc).__name__}: {exc}"
    missing = [key for key in expected if key not in (reply or "")]
    return {
        "index": index,
        "query": query,
        "expected": expected,
        "reply": reply or "",
        "missing": missing,
        "len": len(reply or ""),
        "seconds": round(time.time() - start, 1),
        "garbled": bridge_qq._looks_garbled_reply(reply or "")
        or bridge_qq._has_pathological_repetition(reply or ""),
    }


def write_report(path, rows):
    with path.open("w", encoding="utf-8") as f:
        f.write("# QQBOT 最近问题离线测试\n\n")
        for row in rows:
            f.write(f"## {row['index']}. {row['query']}\n")
            f.write(
                f"- 耗时: {row['seconds']}s，长度: {row['len']}，"
                f"缺词: {row['missing']}，乱码/重复: {row['garbled']}\n"
            )
            f.write(f"- 回答:\n{row['reply']}\n\n")


async def main():
    bridge_qq.TOOL_CALL_TIMEOUT = 8
    bridge = bridge_qq.Bridge()
    bridge.sessions = FakeSessions()
    bridge.recent_replies = []

    report_path = (
        Path(__file__).resolve().parents[1]
        / "logs"
        / f"test_recent_questions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    batch_size = 5
    for start in range(0, len(CASES), batch_size):
        batch = CASES[start:start + batch_size]
        batch_rows = await asyncio.gather(*[
            run_one(bridge, start + i + 1, query, expected)
            for i, (query, expected) in enumerate(batch)
        ])
        rows.extend(batch_rows)
        for row in batch_rows:
            print(
                f"{row['index']:02d} | {row['seconds']:5.1f}s | "
                f"len={row['len']:4d} | missing={row['missing']} | "
                f"garbled={row['garbled']}"
            )
        print(f"batch done {start + len(batch)}/{len(CASES)}")

    write_report(report_path, rows)
    print(f"\nREPORT: {report_path}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
