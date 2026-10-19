# -*- coding: utf-8 -*-
"""A/B0/B1/C 完整对照实验（离线隔离，不接触线上发送）。

A  = 裸 DeepSeek（无人格、无历史）
B0 = persona/taffy_b0.md（旧人格），无历史/群风格
B1 = persona/taffy.md（新人格），无历史/群风格
C  = persona/taffy.md + 当前完整 QQBot 管线（注入冻结历史，走真实 ask_model）

固定：同模型、同 temperature/top_p/max_tokens；不因失败切换模型；每次独立会话。
"""
import asyncio
import json
import os
import re
import statistics
import sys
import time

BASE = r"<项目根>"
sys.path.insert(0, BASE)
os.environ["QQ_TEST_MODE"] = "1"
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(BASE, ".env"))

import bridge_qq as B  # noqa: E402

# ---------- 冻结的实验配置 ----------
MODEL_REF = "opencode/deepseek-v4.1-flash"      # 单一模型，失败即记录，不切换
TEMPERATURE = B.CASUAL_TEMPERATURE               # 0.85
TOP_P = B.CASUAL_TOP_P                           # 1.0
MAX_TOKENS = B.DEFAULT_MAX_TOKENS                # 10000
REPEATS = 3

PROVIDER, MODEL_NAME = MODEL_REF.split("/", 1)
CFG = B.PROVIDERS.get(PROVIDER, {})
PERSONA_A = "你是一个中文助手，直接回答用户。"
PERSONA_B0 = B.persona_mod._extract_core(
    open(os.path.join(BASE, "persona", "taffy_b0.md"), encoding="utf-8").read())
PERSONA_B1 = B.persona_mod.core_persona_prompt()

# ---------- 冻结的 20 条测试消息 ----------
GREET_HISTORY = [
    {"role": "user", "content": "周六晚上八点约好打王者荣耀"},
    {"role": "assistant", "content": "小菲记下了，八点见"},
]
TOPIC_HISTORY = [
    {"role": "user", "content": "我在纠结考研报江苏还是江西"},
    {"role": "assistant", "content": "看你图稳还是图平台"},
]
TIRED_HISTORY = [
    {"role": "user", "content": "我今天好累啊"},
    {"role": "assistant", "content": "累了就歇会儿，别硬撑"},
]

CASES = [
    ("冷笑话", "我叫你讲个冷笑话", []),
    ("冷笑话", "讲个冷笑话", []),
    ("冷笑话", "来个冷笑话，要好笑的", []),
    ("故事", "讲个短故事", []),
    ("故事", "编一个关于猫的小故事", []),
    ("寒暄", "你好", []),
    ("寒暄", "你现在如何", []),
    ("寒暄", "今天吃了没", []),
    ("调侃", "你是牛马吗", []),
    ("调侃", "塔菲你是猫娘吗", []),
    ("调侃", "你是不是傻", []),
    ("观点", "你觉得考研选江西还是南京", []),
    ("观点", "你觉得AI会取代人类吗", []),
    ("知识", "简单讲讲推荐系统是怎么回事", []),
    ("知识", "什么是缓存", []),
    ("知识", "解释一下量子纠缠", []),
    ("知识", "算一下 123*456", []),
    ("多轮追问", "之前我们约好几点打什么", GREET_HISTORY),
    ("多轮追问", "你觉得呢", TOPIC_HISTORY),
    ("多轮追问", "那你说我该怎么办", TIRED_HISTORY),
]

PROPS = ["游标卡尺", "薯条", "小皮", "菲球", "呆毛", "雏草姬"]
SELF_REF = re.compile(r"小菲|塔菲")
TONE = re.compile(r"喵|呀|啦|诶|嘻嘻")


class S:
    def __init__(self, value=None):
        self.value = value or {}

    def get(self, k):
        return self.value

    def put(self, k, v):
        self.value = v


def new_bridge(history=None):
    b = object.__new__(B.Bridge)
    b.sessions = S({"messages": list(history or []), "summary_index": 0})
    b.style_store = None
    b.observer = None
    b.recent_group_context = {}
    b.recent_replies = []
    b.model_semaphore = None
    b.last_model_ref = ""
    b.api_key = ""
    b.knowledge_store = None
    b.agent_memory = None
    b._agent_memory_lock = None
    b._agent_memory_future = None
    b._agent_memory_executor = None
    b.group_policy = type("P", (), {
        "allows": lambda self, gid, f: True,
        "version": lambda self, gid: 1,
        "revision": lambda self, gid: 1,
    })()
    return b


def raw_generate(system, history, query, session_key):
    """单模型直连，不切换、不重试。返回 (raw, state, diag, seconds)。"""
    bridge = object.__new__(B.Bridge)
    messages = [dict(m) for m in history] + [{"role": "user", "content": query}]
    body = B._build_model_request(PROVIDER, MODEL_NAME, system, messages,
                                  MAX_TOKENS, temperature=TEMPERATURE, top_p=TOP_P,
                                  session_key=session_key)
    body.update(B._model_thinking_options(PROVIDER, MODEL_NAME))
    diag = {}
    body["_diagnostics"] = diag
    started = time.monotonic()
    try:
        reply, state, *_ = bridge._api_call(body, CFG.get("url", ""), CFG.get("key", ""))
    except Exception as exc:  # noqa: BLE001
        return "", f"exception:{type(exc).__name__}", {}, time.monotonic() - started
    return reply, state, diag, time.monotonic() - started


async def pipeline_generate(history, query, session_key):
    """C 组：走真实 ask_model（含当前全部上下文与后处理）。"""
    b = new_bridge(history)
    B._answer_cache_get = lambda *a, **k: None
    captured = {}
    real = B.Bridge._api_call

    def spy(self, body, api_url, api_key):
        diag = body.get("_diagnostics")
        result = real(self, body, api_url, api_key)
        captured.setdefault("calls", []).append({
            "model": body.get("model"),
            "temp": body.get("temperature"),
            "top_p": body.get("top_p"),
            "max_tokens": body.get("max_tokens"),
            "raw": result[0],
            "state": result[1],
            "finish_reason": (diag or {}).get("finish_reason"),
            "completion_tokens": (diag or {}).get("completion_tokens"),
        })
        return result

    b._api_call = spy.__get__(b, B.Bridge)
    started = time.monotonic()
    final = await b.ask_model("100000001", query, query, speaker_id="u")
    return final, captured.get("calls", []), time.monotonic() - started, b


def metrics(text, category):
    text = text or ""
    prop_hits = {p: text.count(p) for p in PROPS if p in text}
    out = {
        "chars": len(text),
        "props": prop_hits,
        "prop_total": sum(prop_hits.values()),
        "self_ref": len(SELF_REF.findall(text)),
        "tone": len(TONE.findall(text)),
    }
    if category == "冷笑话":
        # 完整设问+铺垫+包袱：有问句，且问句之后还有实质内容
        parts = re.split(r"[？?]", text)
        tail = parts[-1] if len(parts) > 1 else ""
        out["has_setup"] = "？" in text or "?" in text
        out["has_punchline"] = len(tail.strip()) >= 8
        out["joke_complete"] = bool(out["has_setup"] and out["has_punchline"])
    if category == "故事":
        out["long_enough"] = len(text) >= 200
    if category == "知识":
        out["long_enough"] = len(text) >= 150
    if category == "多轮追问":
        out["uses_history"] = any(
            k in text for k in ("八点", "王者", "江苏", "江西", "累"))
    return out


async def main():
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(BASE, ".tmp", f"abx-{stamp}")
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    total_tokens = 0
    failures = []
    budget = len(CASES) * REPEATS * 4
    done = 0
    print(f"预算 {budget} 次生成，模型 {MODEL_REF} temp={TEMPERATURE} "
          f"top_p={TOP_P} max_tokens={MAX_TOKENS}", flush=True)

    for case_no, (category, query, history) in enumerate(CASES, 1):
        for rep in range(REPEATS):
            session_key = f"abx-{case_no}-{rep}-{int(time.time()*1000)%100000}"
            for arm in ("A", "B0", "B1", "C"):
                done += 1
                system = {"A": PERSONA_A, "B0": PERSONA_B0,
                          "B1": PERSONA_B1, "C": PERSONA_B1}[arm]
                record = {"case_no": case_no, "category": category, "query": query,
                          "rep": rep, "arm": arm, "history_len": len(history)}
                if arm == "C":
                    final, calls, secs, bridge = await pipeline_generate(
                        history, query, session_key)
                    record["seconds"] = round(secs, 1)
                    if calls:
                        c = calls[0]
                        record.update({
                            "model": c["model"], "temp": c["temp"], "top_p": c["top_p"],
                            "max_tokens": c["max_tokens"], "state": c["state"],
                            "finish_reason": c["finish_reason"],
                            "completion_tokens": c["completion_tokens"],
                            "raw": c["raw"], "api_calls": len(calls),
                        })
                        total_tokens += int(c["completion_tokens"] or 0)
                    else:
                        record.update({"state": "no_api_call", "raw": ""})
                    record["system_chars"] = len(getattr(bridge, "_last_system", "") or "")
                    record["final"] = final
                else:
                    raw, state, diag, secs = raw_generate(
                        system, history, query, session_key)
                    record.update({
                        "model": MODEL_NAME, "temp": TEMPERATURE, "top_p": TOP_P,
                        "max_tokens": MAX_TOKENS, "state": state,
                        "finish_reason": diag.get("finish_reason"),
                        "completion_tokens": diag.get("completion_tokens"),
                        "raw": raw, "final": raw, "seconds": round(secs, 1),
                        "api_calls": 1,
                    })
                    total_tokens += int(diag.get("completion_tokens") or 0)
                if not record.get("final") or record.get("state") not in ("ok",):
                    failures.append({"case_no": case_no, "arm": arm, "rep": rep,
                                     "state": record.get("state")})
                record.update(metrics(record.get("final") or "", category))
                rows.append(record)
                print(f"  [{done}/{budget}] case{case_no:02d} {category} {arm} "
                      f"{record.get('chars', 0)}字 state={record.get('state')}",
                      flush=True)

    with open(os.path.join(out_dir, "samples.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    # 汇总
    summary = {}
    for arm in ("A", "B0", "B1", "C"):
        sub = [r for r in rows if r["arm"] == arm]
        ok = [r for r in sub if r.get("final")]
        jokes = [r for r in ok if r["category"] == "冷笑话"]
        summary[arm] = {
            "生成数": len(sub),
            "成功": len(ok),
            "失败": len(sub) - len(ok),
            "平均字数": round(statistics.mean([r["chars"] for r in ok]), 1) if ok else 0,
            "字数中位数": statistics.median([r["chars"] for r in ok]) if ok else 0,
            "道具命中率": round(sum(1 for r in ok if r["prop_total"] > 0) / len(ok), 2) if ok else 0,
            "平均道具次数": round(statistics.mean([r["prop_total"] for r in ok]), 2) if ok else 0,
            "自称率": round(sum(1 for r in ok if r["self_ref"] > 0) / len(ok), 2) if ok else 0,
            "语气词率": round(sum(1 for r in ok if r["tone"] > 0) / len(ok), 2) if ok else 0,
            "冷笑话完整率": round(sum(1 for r in jokes if r.get("joke_complete")) / len(jokes), 2) if jokes else 0,
            "故事够长率": round(sum(1 for r in ok if r["category"] == "故事" and r.get("long_enough")) /
                            max(1, len([r for r in ok if r["category"] == "故事"])), 2),
            "知识够长率": round(sum(1 for r in ok if r["category"] == "知识" and r.get("long_enough")) /
                            max(1, len([r for r in ok if r["category"] == "知识"])), 2),
            "追问用到历史率": round(sum(1 for r in ok if r["category"] == "多轮追问" and r.get("uses_history")) /
                              max(1, len([r for r in ok if r["category"] == "多轮追问"])), 2),
            "平均耗时秒": round(statistics.mean([r["seconds"] for r in ok]), 1) if ok else 0,
        }
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # 重复度检查（同一条同一组 3 次是否高度重复）
    dup = {}
    for case_no in range(1, len(CASES) + 1):
        for arm in ("A", "B0", "B1", "C"):
            texts = [r["final"] for r in rows
                     if r["case_no"] == case_no and r["arm"] == arm and r.get("final")]
            if len(texts) >= 2:
                same = sum(1 for t in texts[1:] if t == texts[0])
                dup.setdefault(arm, []).append(same / (len(texts) - 1))
    with open(os.path.join(out_dir, "duplication.json"), "w", encoding="utf-8") as f:
        json.dump({a: round(statistics.mean(v), 2) for a, v in dup.items()}, f,
                  ensure_ascii=False, indent=2)

    # 匿名盲评
    import random
    rng = random.Random(20261017)
    key, lines = {}, ["# A/B0/B1/C 匿名盲评\n",
                      "每题 4 组回复顺序已打乱。评分（1-5）：内容完整度 / 内容丰富度 / "
                      "自然度 / 塔菲辨识度\n"]
    for case_no, (category, query, _h) in enumerate(CASES, 1):
        group = [r for r in rows if r["case_no"] == case_no and r["rep"] == 0]
        rng.shuffle(group)
        lines.append(f"\n## 【{category}】{query}\n")
        for idx, r in enumerate(group, 1):
            key.setdefault(query, {})[f"组{idx}"] = r["arm"]
            lines.append(f"**组{idx}**：{r.get('final') or '（空）'}\n")
    with open(os.path.join(out_dir, "key.json"), "w", encoding="utf-8") as f:
        json.dump(key, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "blind.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("\n=== 汇总 ===", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"\n总 completion_tokens: {total_tokens}", flush=True)
    print(f"失败样本: {len(failures)}", flush=True)
    print(f"输出目录: {out_dir}", flush=True)


asyncio.run(main())
