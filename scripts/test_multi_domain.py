# -*- coding: utf-8 -*-
"""Run multi-domain QQBOT pipeline tests and write a local report."""

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge_qq


CASES = [
    ("AI/科技", "DeepSeek R1 是什么模型？", ["DeepSeek", "R1"]),
    ("金融", "黄金价格最近为什么持续上涨？", ["黄金", "上涨"]),
    ("体育", "C罗现在多少岁？", ["C罗", "岁"]),
    ("影视", "《奥本海默》在2024年奥斯卡获得了几项大奖？", ["奥本海默", "7"]),
    ("游戏", "《黑神话：悟空》是什么类型的游戏？", ["黑神话", "悟空"]),
    ("历史", "秦始皇在哪一年统一六国？", ["秦始皇", "公元前221"]),
    ("地理", "珠穆朗玛峰海拔多少米？", ["珠穆朗玛峰", "8848"]),
    ("健康", "为什么流感疫苗每年都要重新接种？", ["流感疫苗", "每年"]),
    ("科学", "为什么天空是蓝色的？", ["散射", "蓝"]),
    ("教育", "高考志愿填报怎么选专业？", ["志愿", "专业"]),
    ("法律", "校园欺凌最新的法律规定有哪些？", ["校园欺凌", "未成年人"]),
    ("世界", "2026年世界杯冠军是谁？", ["西班牙"]),
    ("人物", "张雪峰老师是谁？", ["张雪峰", "峰学蔚来"]),
    ("梗", "玄武口音是什么梗？", ["玄武口音", "余承东"]),
    ("财经", "英伟达市值最近是多少？", ["英伟达", "市值"]),
    ("航天", "SpaceX星舰第三次试飞结果如何？", ["星舰", "试飞"]),
    ("音乐", "周杰伦《七里香》是哪一年发行的？", ["七里香", "周杰伦"]),
    ("生活", "上海明天天气怎么样？", ["上海", "天气"]),
    ("政策", "延迟退休最新政策是什么？", ["延迟退休", "退休"]),
    ("科技", "苹果M4芯片主要提升在哪里？", ["M4", "芯片"]),
]


class FakeSessions:
    def get(self, key):
        return None

    def put(self, key, value):
        pass


async def run_one(bridge, index, domain, query, expected):
    start = time.time()
    try:
        reply = await asyncio.wait_for(
            bridge.ask_model(
                f"multi_domain_{index}",
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
        "domain": domain,
        "query": query,
        "expected": expected,
        "reply": reply or "",
        "missing": missing,
        "len": len(reply or ""),
        "seconds": round(time.time() - start, 1),
        "garbled": bridge_qq._looks_garbled_reply(reply or "")
        or bridge_qq._has_repeated_long_phrase(reply or ""),
    }


def write_report(path, rows):
    with path.open("w", encoding="utf-8") as f:
        f.write("# QQBOT 多领域测试\n\n")
        for row in rows:
            f.write(f"## {row['index']}. [{row['domain']}] {row['query']}\n")
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
        / f"test_multi_domain_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    batch_size = 5
    for start in range(0, len(CASES), batch_size):
        batch = CASES[start:start + batch_size]
        batch_rows = await asyncio.gather(*[
            run_one(bridge, start + i + 1, domain, query, expected)
            for i, (domain, query, expected) in enumerate(batch)
        ])
        rows.extend(batch_rows)
        for row in batch_rows:
            print(
                f"{row['index']:02d} | {row['domain']} | "
                f"{row['seconds']:5.1f}s | len={row['len']:4d} | "
                f"missing={row['missing']} | garbled={row['garbled']}"
            )
        print(f"batch done {start + len(batch)}/{len(CASES)}")

    write_report(report_path, rows)
    print(f"\nREPORT: {report_path}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
