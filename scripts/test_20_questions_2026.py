"""Run the 20-question 2025-2026 QQBOT pipeline test and write a report."""

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge_qq


CASES = [
    (
        "2026年男足世界杯将在哪些国家举办？参赛球队有多少支？",
        ["美国", "加拿大", "墨西哥", "48支"],
    ),
    (
        "2026年世界杯揭幕战和决赛分别在哪个城市和球场举行？",
        ["阿兹特克", "MetLife"],
    ),
    (
        "在2026年世界杯亚洲区预选赛18强赛中，中国队最终成绩如何？有没有出线？",
        ["3胜7负", "9分", "小组第五", "无缘"],
    ),
    (
        "通过2026世界杯亚洲区预选赛，直接晋级世界杯正赛的六支亚洲球队是哪些？",
        ["伊朗", "乌兹别克斯坦", "韩国", "约旦", "日本", "澳大利亚"],
    ),
    (
        "2025年中国电影《哪吒2》创造了什么票房纪录？大致票房是多少？",
        ["150亿", "动画电影"],
    ),
    (
        "2025年国际金价在几月份首次突破每盎司4000美元？背后有哪些主要原因？",
        ["2025年10月", "4000", "美联储", "央行"],
    ),
    (
        "2025年中国国产大模型DeepSeek R1在哪一月发布？它以什么特点震撼全球AI圈？",
        ["2025年1月", "开源", "560万", "o1"],
    ),
    (
        "2025年中国在空间探索领域有哪些重大进展？至少举出两件事。",
        ["神舟", "天问二号"],
    ),
    (
        "2026年APEC中国年的主题是什么？第一次高官会在哪个城市举行？",
        ["建设亚太共同体", "广州"],
    ),
    (
        "挪威PRIO的研究显示，2025年全球冲突数量有什么特点？大致有多少起冲突？",
        ["65起", "24.5万", "80年"],
    ),
    (
        "2025年联合国在人工智能治理方面启动了什么新机制？",
        ["全球对话", "科学小组"],
    ),
    (
        "2025年关于中东局势和巴以冲突，有哪些关键节点或趋势？请简要概括。",
        ["加沙", "以色列", "哈马斯"],
    ),
    (
        "2026世界人工智能大会在哪个城市举办？主会场有哪些？主题是什么？",
        ["上海", "世博", "智能伙伴"],
    ),
    (
        "2026年中国将迎来哪个重要历史事件的纪念活动？",
        ["105周年"],
    ),
    (
        "请用不超过200字，总结2025年国际金价狂飙的三大主要原因。",
        ["避险", "降息", "央行", "4000"],
    ),
    (
        "请简要对比2025年DeepSeek R1与传统大模型的主要差异。",
        ["低成本", "开源", "推理", "闭源"],
    ),
    (
        "请用一段话，概括2025年中国在科技与产业方面的几大突破。",
        ["DeepSeek", "天问二号", "福建舰", "奋斗者"],
    ),
    (
        "2025年10月，纽约黄金期货价格首次突破每盎司多少美元？当天创出的历史新高价位大致是多少？",
        ["4000", "4014.6"],
    ),
    (
        "2026年世界杯一共有多少座主办城市？美国、加拿大、墨西哥各有几座？",
        ["16座", "11", "3", "2"],
    ),
    (
        "2025年哪一部国产动画电影成为全球票房最高的动画电影？其全球票房大致达到多少亿元人民币？",
        ["150亿", "动画电影"],
    ),
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
                f"twenty_question_2026_{index}",
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
    }


def write_report(path, rows):
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(f"\n### {row['index']}. {row['query']}\n")
            f.write(f"- 耗时: {row['seconds']}s，长度: {row['len']}\n")
            f.write(f"- 缺少参考关键词: {row['missing']}\n")
            f.write(f"- 回答:\n{row['reply']}\n")


async def main():
    bridge_qq.TOOL_CALL_TIMEOUT = 8
    bridge = bridge_qq.Bridge()
    bridge.sessions = FakeSessions()
    bridge.recent_replies = []

    report_path = (
        Path(__file__).resolve().parents[1]
        / "logs"
        / f"test_20_questions_2026_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as f:
        f.write("# QQBOT 20题2025-2026批量测试\n")

    batch_size = 5
    for start in range(0, len(CASES), batch_size):
        batch = CASES[start:start + batch_size]
        rows = await asyncio.gather(*[
            run_one(bridge, start + i + 1, query, expected)
            for i, (query, expected) in enumerate(batch)
        ])
        write_report(report_path, rows)
        for row in rows:
            print(
                f"{row['index']:02d} | {row['seconds']:5.1f}s | "
                f"len={row['len']:4d} | missing={row['missing']}"
            )
        print(f"batch done {start + len(batch)}/20")

    print(f"\nREPORT: {report_path}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
