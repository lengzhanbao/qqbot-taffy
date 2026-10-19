# -*- coding: utf-8 -*-
"""人格还原档位 A/B 评估（真实模型输出，供人工复核）。

用法：
    python scripts/eval_persona_fidelity.py [--limit N] [--model opencode/deepseek-v4.1-flash]

输出：docs/persona_fidelity_eval_<时间戳>.md
说明：不发送任何 QQ 消息，不启动/停止机器人；只调用模型 API 做离线对比。
      结果需要人工盲评，脚本里的"口癖剥离后仍可辨认"只是量化代理指标。
"""
import argparse
import asyncio
import os
import re
import sys
import time
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault("QQ_TEST_MODE", "1")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(BASE, ".env"))

import bridge_qq as B  # noqa: E402
from qqbot_core import persona as P  # noqa: E402
from qqbot_core import style as S  # noqa: E402

# 覆盖 9 类场景；每条一个问题。想做完整 50 条评测就往这里加。
CASES = [
    ("chitchat", "今天累死了"),
    ("chitchat", "在干嘛呢"),
    ("meme", "扣1送地狱火"),
    ("teased", "你是牛马吗"),
    ("teased", "你怎么这么菜"),
    ("self_eval", "你现在变聪明了吗"),
    ("praise", "你好可爱"),
    ("game_rant", "刚五连败，不想玩了"),
    ("comfort", "我今天好难过"),
    ("knowledge", "缓存是什么意思"),
    ("serious", "说个正经事，我最近压力很大"),
    ("chitchat", "晚饭吃什么好"),
]

# 口癖与自称：用于"剥离后是否仍可辨认"的代理指标
_TIC_RE = re.compile(r"喵|呀|诶|嘻嘻|哈哈|taffy|塔菲|小菲", re.IGNORECASE)
# 反应方式标记：反问、顶回去、得意、共情、感叹——这些才是"辨识度"
_REACTION_MARKERS = (
    "？", "?", "！", "!", "才不", "明明", "谁", "别", "来啊", "行吧", "稳",
    "得意", "眼光", "准了", "阿", "吧", "呢",
)
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")


def strip_tics(text):
    return _TIC_RE.sub("", str(text or ""))


def reaction_score(text):
    """剥离口癖后，还剩多少"反应方式"特征（0-3）。"""
    stripped = strip_tics(text)
    if not stripped:
        return 0
    hits = sum(1 for m in _REACTION_MARKERS if m in stripped)
    has_emoji = bool(_EMOJI_RE.search(stripped))
    short = len(stripped) <= 45
    return min(3, (1 if hits >= 2 else 0) + (1 if has_emoji else 0) + (1 if short else 0))


def build_system(fidelity, scene):
    return (
        S.TAFFY_LIGHT_PROMPT
        + "\n【当前日期】" + datetime.now().strftime("%Y年%m月%d日")
        + P.build_persona_prompt(fidelity, scene, fewshot=B.PERSONA_FEWSHOT)
    )


async def run(limit=None, model=None):
    bridge = object.__new__(B.Bridge)
    bridge.model_semaphore = None
    bridge.last_model_ref = ""
    bridge.api_key = ""
    model_refs = [model] if model else B.MODELS_FINAL

    cases = CASES[: limit or len(CASES)]
    rows = []
    calls = 0
    started = time.monotonic()
    for scene, question in cases:
        for fidelity in ("high", "standard"):
            t0 = time.monotonic()
            reply, used = await bridge._complete(
                build_system(fidelity, scene),
                [{"role": "user", "content": question}],
                max_tokens=180,
                temperature=1.1,
                top_p=1.0,
                model_refs=model_refs,
                session_key="eval-" + scene,
            )
            calls += 1
            rows.append({
                "scene": scene,
                "question": question,
                "fidelity": fidelity,
                "reply": reply,
                "model": used,
                "seconds": round(time.monotonic() - t0, 1),
                "chars": len(reply),
                "has_tic": bool(_TIC_RE.search(reply)),
                "reaction_after_strip": reaction_score(reply),
                "off_persona": B._looks_off_persona(reply, question),
                "english_dominant": B._looks_english_dominant(reply),
            })

    elapsed = round(time.monotonic() - started, 1)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(BASE, "docs", f"persona_fidelity_eval_{stamp}.md")

    def avg(key, fidelity):
        vals = [r[key] for r in rows if r["fidelity"] == fidelity]
        return round(sum(vals) / len(vals), 2) if vals else 0

    lines = [
        "# 人格还原档位 A/B 评估（真实模型输出）",
        "",
        f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- 模型：{rows[0]['model'] if rows else 'n/a'}",
        f"- 用例数：{len(cases)}（每例跑 high / standard 两档），模型调用 {calls} 次，耗时 {elapsed}s",
        "- 说明：示例为原创风格参考；结果需人工盲评，“口癖剥离后反应分”只是量化代理指标。",
        "",
        "## 汇总",
        "",
        "| 指标 | high | standard |",
        "| --- | --- | --- |",
        f"| 平均字数 | {avg('chars','high')} | {avg('chars','standard')} |",
        f"| 出现口癖比例 | "
        f"{round(sum(r['has_tic'] for r in rows if r['fidelity']=='high')/max(1,len(cases)),2)} | "
        f"{round(sum(r['has_tic'] for r in rows if r['fidelity']=='standard')/max(1,len(cases)),2)} |",
        f"| 剥离口癖后反应分(0-3) | {avg('reaction_after_strip','high')} | {avg('reaction_after_strip','standard')} |",
        f"| 破人设次数 | {sum(r['off_persona'] for r in rows if r['fidelity']=='high')} | "
        f"{sum(r['off_persona'] for r in rows if r['fidelity']=='standard')} |",
        f"| 英文为主次数 | {sum(r['english_dominant'] for r in rows if r['fidelity']=='high')} | "
        f"{sum(r['english_dominant'] for r in rows if r['fidelity']=='standard')} |",
        "",
        "## 逐条对比",
        "",
        "| 场景 | 提问 | 档位 | 耗时 | 字数 | 剥离口癖后反应分 | 回复 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        reply = str(r["reply"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {r['scene']} | {r['question']} | {r['fidelity']} | {r['seconds']}s | "
            f"{r['chars']} | {r['reaction_after_strip']} | {reply} |"
        )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("WROTE " + out_path)
    print(f"calls={calls} elapsed={elapsed}s model={rows[0]['model'] if rows else 'n/a'}")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    asyncio.run(run(limit=args.limit, model=args.model))
