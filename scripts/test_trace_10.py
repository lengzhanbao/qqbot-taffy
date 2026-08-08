"""Run 10 random questions and write a full pipeline trace report."""

import asyncio
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge_qq


CASES = [
    "介绍一下oner",
    "介绍一下on英雄联盟的选手",
    "on不是辅助吗",
    "介绍一下viper",
    "介绍一下meiko",
    "edg厂长呢",
    "介绍一下faker",
    "那天下雨了歌词是什么",
    "推荐几部好看的电影",
    "推荐几首新歌",
    "推荐东野圭吾的书",
    "2026世界杯冠军是谁",
    "C罗今年多少岁",
    "豆包是什么",
    "玄武口音是什么梗",
]


class FakeSessions:
    def get(self, key):
        return None

    def put(self, key, value):
        pass


def install_trace():
    trace = {}
    first_orig = bridge_qq.Bridge._complete_with_tools

    async def traced_first(self, system, messages, **kwargs):
        result = await first_orig(self, system, messages, **kwargs)
        trace.setdefault("first_round", []).append({
            "model": result[2] if len(result) > 2 else "",
            "tool_calls": result[0],
            "direct_reply": (result[1] or "")[:500],
            "native_search_count": len(result[3]) if len(result) > 3 else 0,
            "last_user": (messages[-1].get("content") or "")[:300] if messages else "",
        })
        return result

    search_orig = bridge_qq.search_web_async

    async def traced_search(query, **kwargs):
        raw = await search_orig(query, **kwargs)
        trace.setdefault("searches", []).append({
            "query": query,
            "raw_len": len(raw or ""),
            "raw_head": (raw or "")[:700],
        })
        return raw

    distill_orig = bridge_qq._distill_search_results

    def traced_distill(raw, query, **kwargs):
        out = distill_orig(raw, query, **kwargs)
        trace.setdefault("distilled", []).append({
            "query": query,
            "distilled": (out or "")[:700],
        })
        return out

    complete_orig = bridge_qq.Bridge._complete

    async def traced_complete(self, system, messages, **kwargs):
        reply, model = await complete_orig(self, system, messages, **kwargs)
        trace.setdefault("model_calls", []).append({
            "model": model,
            "system_head": (system or "")[:150],
            "last_user": (messages[-1].get("content") or "")[:200] if messages else "",
            "reply_head": (reply or "")[:400],
        })
        return reply, model

    race_orig = bridge_qq.Bridge._complete_race

    async def traced_race(self, system, messages, **kwargs):
        reply, model = await race_orig(self, system, messages, **kwargs)
        trace.setdefault("model_calls", []).append({
            "model": model,
            "system_head": (system or "")[:150],
            "last_user": (messages[-1].get("content") or "")[:200] if messages else "",
            "reply_head": (reply or "")[:400],
            "mode": "race",
        })
        return reply, model

    contract_orig = bridge_qq._answer_contract_missing

    def traced_contract(reply, query, source_text):
        out = contract_orig(reply, query, source_text)
        trace.setdefault("contract", []).append({
            "missing": out,
            "reply_head": (reply or "")[:200],
        })
        return out

    bridge_qq.Bridge._complete_with_tools = traced_first
    bridge_qq.search_web_async = traced_search
    bridge_qq._distill_search_results = traced_distill
    bridge_qq.Bridge._complete = traced_complete
    bridge_qq.Bridge._complete_race = traced_race
    bridge_qq._answer_contract_missing = traced_contract
    return trace


def write_report(path, rows):
    with path.open("w", encoding="utf-8") as f:
        f.write("# QQBot 10题全链路追踪\n")
        for row in rows:
            f.write(f"\n## {row['query']}\n")
            f.write(f"- 耗时: {row['seconds']}s，长度: {row['len']}\n")
            f.write(f"- 最终回答:\n{row['reply']}\n")
            f.write("\n### 第一轮模型动作\n")
            for item in row.get("first_round", []):
                f.write(f"- 模型: {item['model']}\n")
                f.write(f"- tool_calls: {item['tool_calls']}\n")
                f.write(f"- direct_reply: {item['direct_reply']}\n")
                f.write(f"- 最后用户消息: {item['last_user']}\n")
            f.write("\n### 实际搜索\n")
            for item in row.get("searches", []):
                f.write(f"- query: {item['query']}\n")
                f.write(f"- 返回长度: {item['raw_len']}\n")
                f.write(f"- 返回内容:\n{item['raw_head']}\n")
            f.write("\n### 凝练结果\n")
            for item in row.get("distilled", []):
                f.write(f"- query: {item['query']}\n")
                f.write(f"- 凝练:\n{item['distilled']}\n")
            f.write("\n### 模型调用/第二轮修改\n")
            for idx, item in enumerate(row.get("model_calls", []), 1):
                f.write(f"- 调用{idx}: {item['model']}\n")
                f.write(f"  system: {item['system_head']}\n")
                f.write(f"  last_user: {item['last_user']}\n")
                f.write(f"  reply: {item['reply_head']}\n")
            f.write("\n### 答案契约\n")
            for item in row.get("contract", []):
                f.write(f"- missing: {item['missing']}\n")
                f.write(f"- reply: {item['reply_head']}\n")


async def run_one(bridge, trace, query):
    trace.clear()
    start = time.time()
    try:
        reply = await asyncio.wait_for(
            bridge.ask_model(
                f"trace_{abs(hash(query))}",
                query,
                speaker="测试用户",
            ),
            timeout=150,
        )
    except Exception as exc:
        reply = f"ERR {type(exc).__name__}: {exc}"
    return {
        "query": query,
        "reply": reply or "",
        "len": len(reply or ""),
        "seconds": round(time.time() - start, 1),
        **{k: list(v) for k, v in trace.items()},
    }


async def main():
    bridge_qq.TOOL_CALL_TIMEOUT = 8
    bridge = bridge_qq.Bridge()
    bridge.sessions = FakeSessions()
    bridge.recent_replies = []

    trace = install_trace()
    picked = random.sample(CASES, 10)
    report_path = (
        Path(__file__).resolve().parents[1]
        / "logs"
        / f"test_trace_10_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    rows = []
    for idx, query in enumerate(picked, 1):
        print(f"[{idx}/10] {query}")
        row = await run_one(bridge, trace, query)
        rows.append(row)
        print(f"  done {row['seconds']}s len={row['len']}")

    write_report(report_path, rows)
    print(f"\nREPORT: {report_path}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
