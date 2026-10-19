# -*- coding: utf-8 -*-
"""⑦ 人格 A/B/C 隔离评测。

A = 完整旧管线（LEGACY_STYLE_LAYERS=1 + REJECTION_GATES=1）
B = 统一人格 persona/taffy.md + DeepSeek 直接生成（当前默认）
C = 无塔菲人格的 DeepSeek 基线（TAFFY_FLAVOR=0 → 中性系统提示）

隔离运行：QQ_TEST_MODE=1，不写生产 .env、不覆盖记忆、不重启、不发 QQ 消息。
输出：samples.json（含耗时/模型）、blind.md（匿名盲评用）、key.json（映射）
"""
import argparse
import asyncio
import json
import os
import random
import re
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ["QQ_TEST_MODE"] = "1"
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(BASE, ".env"))

import bridge_qq as B  # noqa: E402

CASES = [
    ("闲聊", "今天吃了没"),
    ("被调侃", "你是牛马吗"),
    ("观点追问", "塔菲你觉得考研该选江西还是南京"),
    ("任务", "算一下 123*456"),
    ("角色自述", "塔菲你多大了"),
    ("角色设定", "塔菲你是猫娘吗"),
    ("知识", "简单介绍一下量子纠缠"),
    ("情绪", "我今天好累啊"),
    ("创作", "讲个冷笑话"),
    ("知识任务", "用三句话解释什么是缓存"),
]

ARMS = {
    "A": {"LEGACY_STYLE_LAYERS": True, "REJECTION_GATES": True, "TAFFY_FLAVOR": True},
    "B": {"LEGACY_STYLE_LAYERS": False, "REJECTION_GATES": False, "TAFFY_FLAVOR": True},
    "C": {"LEGACY_STYLE_LAYERS": False, "REJECTION_GATES": False, "TAFFY_FLAVOR": False},
}


class _Sessions:
    def __init__(self):
        self.value = {}

    def get(self, key):
        return self.value

    def put(self, key, value):
        self.value = value


def _bridge():
    bridge = object.__new__(B.Bridge)
    bridge.sessions = _Sessions()
    bridge.style_store = None
    bridge.observer = None
    bridge.recent_replies = []
    bridge.recent_group_context = {}
    bridge.model_semaphore = None
    bridge.last_model_ref = ""
    bridge.api_key = ""
    bridge.knowledge_store = None
    bridge.agent_memory = None
    bridge._agent_memory_lock = None
    bridge._agent_memory_future = None
    bridge._agent_memory_executor = None
    bridge.group_policy = type("P", (), {
        "allows": lambda self, gid, f: True,
        "version": lambda self, gid: 1,
        "revision": lambda self, gid: 1,
    })()
    return bridge


_EMOJI_TAIL = re.compile(r"[\s\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F~～]+$")
_PUNCT_TAIL = re.compile(r"[。！？!?…；;，,、]+$")
# 结尾悬空标记：这些字结尾时几乎一定缺补语（"比我还"、"跟猫耳"）
_DANGLING = ("还", "比", "跟", "和", "与", "像", "把", "被", "给", "对", "向", "从",
             "为", "由", "非常", "特别", "比较", "只是", "因为", "如果", "虽然",
             "而且", "然后", "所以", "但是", "不过")


def _truncated(reply):
    """真正的完整性信号：剥掉结尾 emoji/标点后，是否停在悬空字上。

    注意：不能用"结尾不是句末标点"来判断——本项目大量回复以 emoji 结尾，
    那样会把正常回复全判成截断（这是我第一版指标的错误）。
    """
    text = _EMOJI_TAIL.sub("", str(reply or "")).strip()
    if not text or _PUNCT_TAIL.search(text):
        return False
    return any(text.endswith(marker) for marker in _DANGLING)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--max-cases", type=int, default=len(CASES))
    parser.add_argument("--seed", type=int, default=20261017)
    args = parser.parse_args()

    cases = CASES[: max(1, args.max_cases)]
    out_dir = os.path.join(BASE, ".tmp", f"persona-ab-{int(time.time())}")
    os.makedirs(out_dir, exist_ok=True)

    rows = []
    for label, query in cases:
        for arm, flags in ARMS.items():
            for sample in range(args.samples):
                for name, value in flags.items():
                    setattr(B, name, value)
                B._answer_cache_get = lambda *a, **k: None
                bridge = _bridge()
                started = time.monotonic()
                try:
                    reply = asyncio.run(
                        bridge.ask_model("100000001", query, query, speaker_id="probe"))
                    err = ""
                except Exception as exc:  # noqa: BLE001
                    reply, err = "", f"{type(exc).__name__}: {exc}"
                rows.append({
                    "case": label, "query": query, "arm": arm, "sample": sample,
                    "reply": reply, "error": err,
                    "seconds": round(time.monotonic() - started, 1),
                    "chars": len(reply or ""),
                    "model": bridge.last_model_ref,
                    "truncated": _truncated(reply),
                    "has_meow": "喵" in (reply or ""),
                })
                print(f"  {label:6} {arm} {len(reply or ''):3}字 {rows[-1]['seconds']:4.1f}s "
                      f"{rows[-1]['model']}")

    # 恢复默认开关，避免影响同进程后续操作
    for name, value in ARMS["B"].items():
        setattr(B, name, value)

    with open(os.path.join(out_dir, "samples.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    # 匿名盲评：每题三组回复打乱顺序，映射写进 key.json
    rng = random.Random(args.seed)
    key, lines = {}, ["# 人格 A/B/C 匿名盲评\n",
                      "每组回复来自不同配置，顺序已打乱。请按下面 6 项打分（1-5）：",
                      "自然度 / 角色辨识度 / 贴题程度 / 事实正确性 / 回复完整性 / 延迟感受\n"]
    for label, query in cases:
        group = [r for r in rows if r["query"] == query and r["sample"] == 0]
        order = list(group)
        rng.shuffle(order)
        lines.append(f"\n## 【{label}】{query}\n")
        for index, row in enumerate(order, 1):
            tag = f"组{index}"
            key.setdefault(query, {})[tag] = row["arm"]
            lines.append(f"**{tag}**：{row['reply'] or '（空）'}\n")
    with open(os.path.join(out_dir, "key.json"), "w", encoding="utf-8") as f:
        json.dump(key, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "blind.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    summary = {}
    for arm in ARMS:
        subset = [r for r in rows if r["arm"] == arm]
        summary[arm] = {
            "回复数": len(subset),
            "空回复": sum(1 for r in subset if not r["reply"]),
            "疑似截断": sum(1 for r in subset if r["truncated"]),
            "含喵": sum(1 for r in subset if r["has_meow"]),
            "平均字数": round(sum(r["chars"] for r in subset) / max(1, len(subset)), 1),
            "P50秒": sorted(r["seconds"] for r in subset)[len(subset) // 2],
            "P95秒": sorted(r["seconds"] for r in subset)[min(len(subset) - 1, int(len(subset) * 0.95))],
        }
    print("\n=== 机械指标（不含自然度/人格分，那部分要你盲评）===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("\n盲评文件:", os.path.join(out_dir, "blind.md"))
    print("映射文件:", os.path.join(out_dir, "key.json"))
    print("原始数据:", os.path.join(out_dir, "samples.json"))


main()
