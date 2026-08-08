"""Run the 20-question QQBOT pipeline test and write a local report."""

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge_qq


CASES = [
    (
        "日本央行在2024年3月做出了什么历史性的货币政策决定？",
        ["负利率", "0.1%", "17年"],
    ),
    (
        "最近美国通过的对TikTok的法案核心内容是什么？",
        ["字节跳动", "剥离", "禁令"],
    ),
    (
        "2024年清明节假期国内旅游出游人数大概多少？相比往年如何？",
        ["1.19亿", "2019", "11.5%"],
    ),
    (
        "最近黄金价格为何持续大涨？",
        ["避险", "降息", "央行", "黄金"],
    ),
    (
        "伊朗总统莱希最近发生了什么事？",
        ["莱希", "直升机", "遇难"],
    ),
    (
        "OpenAI发布的Sora模型是什么？有什么特点？",
        ["Sora", "文生视频", "60秒", "三维"],
    ),
    (
        "小米SU7发布后的市场表现如何？起售价是多少？",
        ["21.59万", "抢购", "锁单"],
    ),
    (
        "英伟达市值最近突破了多少？为什么会增长这么快？",
        ["2万亿", "AI", "H100"],
    ),
    (
        "苹果公司最近发布的M4芯片主要提升在哪里？",
        ["M4", "3纳米", "神经引擎", "NPU"],
    ),
    (
        "请总结一下SpaceX星舰第三次试飞的结果。",
        ["星舰", "热分离", "再入"],
    ),
    (
        "2024年中超联赛的冠名商是谁？",
        ["华润怡宝"],
    ),
    (
        "电影《奥本海默》在2024年奥斯卡获得了几项大奖？",
        ["7项", "最佳影片", "最佳导演"],
    ),
    (
        "泰勒·斯威夫特最新的专辑名字是什么？发行后打破了什么纪录？",
        ["The Tortured Poets Department", "Spotify", "Billboard"],
    ),
    (
        "2024年多哈世泳赛中，中国队获得奖牌数第一吗？",
        ["23", "8", "2", "金牌榜", "奖牌榜"],
    ),
    (
        "请搜索并简要介绍最近很火的国产游戏《黑神话：悟空》的发售日期。",
        ["2024年8月20日", "动作角色扮演", "3A"],
    ),
    (
        "请总结一下新质生产力这个概念的核心含义。",
        ["创新", "高科技", "高效能", "高质量"],
    ),
    (
        "对比一下Kimi智能助手和文心一言的特点。",
        ["Kimi", "文心一言", "长文本", "百度"],
    ),
    (
        "2024年电动车市场的主要竞争趋势是什么？",
        ["价格战", "NOA", "800V", "插电"],
    ),
    (
        "最近关于校园霸凌的法律动态有哪些？",
        ["校园霸凌", "监护人", "刑责年龄", "未成年人"],
    ),
    (
        "请搜索并总结最近关于延迟退休的最新政策动向。",
        ["延迟退休", "小步调整", "弹性实施", "分类推进"],
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
                f"twenty_question_{index}",
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
        / f"test_20_questions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as f:
        f.write("# QQBOT 20题批量测试\n")

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
