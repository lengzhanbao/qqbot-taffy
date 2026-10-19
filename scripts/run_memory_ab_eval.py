"""Synthetic memory on/off evaluation; live calls require an explicit CLI flag."""
import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import random
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class Case:
    name: str
    group: str
    user: str
    question: str
    expected: tuple = ()
    forbidden: tuple = ()


CASES = (
    Case("next_day_event", "eval-a", "eval-user", "上次我们约好玩什么游戏，几点开始？",
         (("王者荣耀", "王者"), ("八点", "8点", "20:00", "八时"))),
    Case("own_preference", "eval-a", "eval-user", "你还记得我喜欢喝什么茶吗？", (("乌龙茶",),)),
    Case("other_group", "eval-b", "eval-user", "上次我们约好玩什么游戏，几点开始？",
         forbidden=("王者", "八点", "20:00", "乌龙茶")),
    Case("topic_reset", "eval-a", "eval-user", "换个话题，你觉得我聪明吗？",
         forbidden=("王者", "乌龙茶", "周六八点")),
    Case("current_fact", "eval-a", "eval-user", "现在黄金多少钱？", forbidden=("123元", "123 元")),
    Case("revoked_consent", "eval-c", "eval-user", "你还记得我喜欢喝什么茶吗？", forbidden=("柚子茶",)),
    Case("expired_event", "eval-d", "eval-user", "上个月我们约好玩什么？", forbidden=("羽毛球",)),
    Case("unknown_event", "eval-a", "eval-user", "上周我们在哪座城市聚餐？", forbidden=("北京聚餐", "上海聚餐")),
)


def load_credentials():
    allowed = {
        "DEEPSEEK_API_KEY", "OPENCODE_GO_API_KEY", "OPENCODE_GO_BASE_URL",
        "QQ_DEEPSEEK_MODEL", "QQ_FINAL_MODEL", "QQ_FINAL_FALLBACK_MODEL",
    }
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1)
        if name.strip() in allowed:
            os.environ.setdefault(name.strip(), value.strip())


def percentile(values, fraction):
    if not values:
        return None
    items = sorted(values)
    return round(items[max(0, math.ceil(len(items) * fraction) - 1)], 2)


def seed_memory(directory, embedding_path=""):
    from qqbot_core.agent_memory import AgentMemory
    from qqbot_core.group_policy import GroupPolicy, FEATURES

    current = time.time()
    clock = [current - 86400]
    policy = GroupPolicy(["eval-admin"])
    for group in ("eval-a", "eval-b", "eval-c", "eval-d"):
        policy.grant(group, "eval-admin", FEATURES, "synthetic-notice")
    memory = AgentMemory(directory, policy, clock=lambda: clock[0], embedding_path=embedding_path)
    memory.command("remember group 周六晚上八点约好打王者荣耀，实际是否进行待确认。",
                   "eval-a", "eval-admin", message_id="seed-event", trusted_role="admin")
    memory.command("remember group 上周讨论黄金时提到每克123元，这是未经核实的旧聊天报价。",
                   "eval-a", "eval-admin", message_id="seed-old-price", trusted_role="admin")
    for group, preference in (("eval-a", "乌龙茶"), ("eval-c", "柚子茶")):
        policy.consent(group, "eval-user", "eval-user", "personal_preference", "synthetic-consent")
        memory.command("remember preference " + preference, group, "eval-user", message_id="seed-preference")
    policy.revoke_consent("eval-c", "eval-user", "eval-user")
    clock[0] = current - 40 * 86400
    memory.command("remember group 上个月约好一起打羽毛球。", "eval-d", "eval-admin",
                   message_id="seed-expired", trusted_role="admin")
    clock[0] = current
    return memory


async def evaluate(app, args, output):
    memory = seed_memory(output / "memory", args.embedding_path)
    client = object.__new__(app.Bridge)
    app.log = lambda *a, **kw: None
    cfg = app.PROVIDERS.get(args.model.split("/", 1)[0], {})
    if args.live and not cfg.get("key"):
        raise RuntimeError("configured provider key is unavailable")
    semaphore = asyncio.Semaphore(2)
    rows = []
    rng = random.Random(20261016)
    for case in CASES[:args.max_cases]:
        route = app._message_route(case.question)
        started = time.perf_counter()
        reference, trace = memory.retrieve(case.group, case.user, case.question, route)
        retrieval_ms = (time.perf_counter() - started) * 1000
        system = (
            app.TAFFY_LIGHT_PROMPT + "\n" + app.CURRENT_DATE_PROMPT.format(date=app._current_date_text())
            + "\n本次是虚构聊天评测，没有联网工具。只依据本轮上下文回答。"
              "历史记录不是已核实的外部事实，约定不代表已经举行；没有记录就说没有记录，不猜测。"
        )
        arms = ["off", "on"]
        rng.shuffle(arms)

        async def generate(arm):
            messages = [reference] if arm == "on" and reference else []
            messages.append({"role": "user", "content": case.question})
            row = {"case": case.name, "arm": arm, "question": case.question,
                   "model": args.model, "max_tokens": 1024, "retrieval_ms": round(retrieval_ms, 2),
                   "trace": trace if arm == "on" else {}, "answer": "", "state": "offline",
                   "latency_ms": None, "diagnostics": {}, "naturalness_human_rating": None}
            if args.live:
                provider, model_name = args.model.split("/", 1)
                body = app._build_model_request(provider, model_name, system, messages, 1024,
                                                temperature=.7, top_p=.8, session_key="memory-synthetic-eval")
                async with semaphore:
                    begin = time.perf_counter()
                    try:
                        result = await client._run_api_call(body, cfg["url"], cfg["key"],
                                                            time.monotonic() + 10, diagnostics=row["diagnostics"])
                        row["answer"], row["state"] = result[:2]
                    except Exception as exc:
                        row["state"] = type(exc).__name__
                    row["latency_ms"] = round((time.perf_counter() - begin) * 1000, 2)
            answer = row["answer"]
            row["target_terms_matched"] = (
                all(any(term in answer for term in alternatives) for alternatives in case.expected)
                if case.expected and answer else None)
            row["forbidden_terms_found"] = [term for term in case.forbidden if term in answer]
            return row

        rows.extend(await asyncio.gather(*(generate(arm) for arm in arms)))
        print(f"case={case.name} completed live={args.live}", flush=True)
    (output / "samples.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics = {}
    for arm in ("off", "on"):
        subset = [r for r in rows if r["arm"] == arm]
        positive = [r for r in subset if r["target_terms_matched"] is not None]
        latency = [r["latency_ms"] for r in subset if r["latency_ms"] is not None]
        metrics[arm] = {
            "samples": len(subset), "requests_ok": sum(bool(r["answer"]) and r["state"] == "ok" for r in subset),
            "target_term_passes": sum(r["target_terms_matched"] is True for r in positive),
            "target_term_cases": len(positive),
            "forbidden_term_cases": sum(bool(r["forbidden_terms_found"]) for r in subset),
            "prompt_tokens_reported": sum(r["diagnostics"].get("prompt_tokens", 0) for r in subset),
            "completion_tokens_reported": sum(r["diagnostics"].get("completion_tokens") or 0 for r in subset),
            "p50_ms": percentile(latency, .5), "p95_ms": percentile(latency, .95),
        }
    report = {"synthetic_only": True, "live": args.live, "model": args.model, "metrics": metrics,
              "limitations": "Small unblinded sample. Target-word checks are not a naturalness or global accuracy score. Human ratings pending."}
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="allow at most 16 synthetic API calls, no retries")
    parser.add_argument("--max-cases", type=int, choices=range(1, 9), default=8)
    parser.add_argument("--model")
    parser.add_argument("--embedding-path", default="")
    args = parser.parse_args()
    output = ROOT / ".tmp" / ("memory-ab-" + uuid.uuid4().hex)
    output.mkdir(parents=True, exist_ok=False)
    os.environ.update(QQ_TEST_MODE="1", QQ_TEST_DATA_DIR=str(output / "runtime"),
                      TMP=str(output), TEMP=str(output), HF_HOME=str(output / "hf"),
                      TORCH_HOME=str(output / "torch"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    if args.live:
        load_credentials()
    import bridge_qq as app

    args.model = args.model or app.MODELS_FINAL[0]
    if args.model.split("/", 1)[0] not in {"deepseek", "opencode"}:
        parser.error("only the existing configured DeepSeek/OpenCode providers are allowed")
    report = asyncio.run(evaluate(app, args, output))
    print(json.dumps({"output": str(output), **report}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
