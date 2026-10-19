"""Run isolated prompt-only A/B generation and save artifacts under .tmp."""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_eval_credentials():
    """Read only DeepSeek model credentials into this child process."""
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    allowed = {"DEEPSEEK_API_KEY", "QQ_DEEPSEEK_MODEL"}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() in allowed:
            os.environ.setdefault(name.strip(), value.strip())


def _save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _save_jsonl(path, samples):
    path.write_text("".join(json.dumps(asdict(row), ensure_ascii=False) + "\n" for row in samples),
                    encoding="utf-8")


def _save_review_guide(path):
    path.write_text(
        "# 盲评说明\n\n"
        "先只看 `blind_review.json`，不要打开 `answer_key.json`、`old.jsonl` 或 `new.jsonl`。\n"
        "每题分别给 A、B 六项 1-5 分，直接填入 `ratings.A` 与 `ratings.B`。\n\n"
        "- `persona`：角色辨识度稳定，但不靠固定口癖堆砌。\n"
        "- `naturalness`：像群聊即时接话，不像客服或说明书。\n"
        "- `relevance`：针对当前消息，没有无关扩写。\n"
        "- `meme_fit`：玩梗场景接得自然；非玩梗题按是否合适评分。\n"
        "- `factuality`：有依据才下结论；本轮没有搜索证据，实时问题应谨慎表达。\n"
        "- `groundedness`：不编造人物、经历、背景或历史上下文。\n\n"
        "1=明显差，3=可接受，5=明显好。当前评测只比较提示词，不验证联网事实准确率。\n",
        encoding="utf-8",
    )


def _prompt_pair(app, case):
    from qqbot_core.routing import classify_message

    route = classify_message(case.prompt)
    mode = app._classify_dialog_mode(case.prompt)
    date_rule = app.CURRENT_DATE_PROMPT.format(date=app._current_date_text())
    eval_boundary = (
        "【评测边界】仅根据这条模拟消息回答，不使用历史、群聊知识、搜索或工具。"
        "涉及实时事实且没有证据时，明确说明无法核实，不要编造。"
        "直接输出可发送的中文正文。"
    )

    legacy = (
        "\n【角色：永雏塔菲】\n"
        "用 taffy / 塔菲 / 小菲自称；喵有节制；语气活泼可爱，"
        "可以用“喵”“呀”“诶”“嘻嘻”这类语气词，但不要每句都加；"
        "可以整活、玩梗、自嘲、抽象。"
        "\n"
        "你是永雏塔菲，群里一个真人朋友，二次元浓度高，活泼可爱，"
        "会整活、接梗、自嘲、一本正经地抽象。怎么说话由你自己判断。"
    )
    old_system = f"{legacy}\n{date_rule}\n{eval_boundary}"

    social = route.social_reaction or (
        route.evidence == "NEVER" and route.context == "none"
        and mode == "chitchat" and len(case.prompt) <= 48
        and not app._PLAYFUL_RE.search(case.prompt)
    )
    level, sensitive, _ = app._classify_style_control(case.prompt, mode)
    restrained = sensitive or level == 0 or mode == "comfort"
    new_system = (
        f"{app.TAFFY_LIGHT_PROMPT}\n{date_rule}\n"
        f"{app._dialog_mode_prompt(mode, taffy_enabled=True)}"
        f"{app.taffy_light_scene_prompt(route.evidence, social=social, restrained=restrained)}"
        f"\n{eval_boundary}"
    )
    return old_system, new_system


async def _generate(app, client, model_ref, system, prompt, *, semaphore):
    provider, model_name = model_ref.split("/", 1)
    cfg = app.PROVIDERS[provider]
    route = app._message_route(prompt)
    profile = app._reply_output_profile(
        prompt, route, social_reply=route.social_reaction)
    max_tokens = app._safe_max_tokens(provider, model_name, profile["max_tokens"])
    diagnostics = {}
    body = app._build_model_request(
        provider, model_name, system, [{"role": "user", "content": prompt}],
        max_tokens, temperature=1.0, top_p=1.0,
    )
    started = time.perf_counter()
    async with semaphore:
        try:
            reply, state, _, _, _ = await client._run_api_call(
                body, cfg["url"], cfg["key"], time.monotonic() + 8.0,
                diagnostics=diagnostics,
            )
        except Exception as exc:
            reply, state = "", type(exc).__name__
    elapsed_ms = (time.perf_counter() - started) * 1000
    return reply, state, elapsed_ms, diagnostics, max_tokens


async def _run(app, args, output_dir):
    from qqbot_core.evaluation import FIXED_CASES, ReplySample, build_blind_review

    model_ref = app.DEEPSEEK_MODEL_REF
    if not model_ref.startswith("deepseek/"):
        raise RuntimeError("configured final model is not DeepSeek")
    provider = model_ref.split("/", 1)[0]
    if not app.PROVIDERS.get(provider, {}).get("key"):
        raise RuntimeError("DeepSeek key is not configured in this process")

    client = object.__new__(app.Bridge)
    semaphore = asyncio.Semaphore(2)
    rng = random.Random(args.seed)
    common_context = "no-history:no-group-knowledge:no-search:no-tools:v1"
    context_fingerprint = hashlib.sha256(common_context.encode()).hexdigest()
    old_samples, new_samples = [], []
    completed = 0
    limit = min(args.max_cases, len(FIXED_CASES))

    for case_id in range(limit):
        case = FIXED_CASES[case_id]
        old_system, new_system = _prompt_pair(app, case)
        variants = [("old", old_system), ("new", new_system)]
        rng.shuffle(variants)

        async def generate_variant(label, system):
            return label, await _generate(app, client, model_ref, system, case.prompt, semaphore=semaphore)

        results = await asyncio.gather(*(generate_variant(*variant) for variant in variants))
        for label, (reply, state, latency_ms, diagnostics, max_tokens) in results:
            ok = bool(reply.strip()) and state == "ok"
            row = ReplySample(
                case_id=case_id,
                text=reply.strip(),
                latency_ms=latency_ms,
                model_ref=model_ref,
                attempted_models=(model_ref,),
                failed_models=() if ok else (model_ref,),
                model_attempts=1,
                model_failures=0 if ok else 1,
                temperature=1.0,
                top_p=1.0,
                max_tokens=max_tokens,
                finish_reason=str(diagnostics.get("finish_reason") or ""),
                raw_chars=int(diagnostics.get("raw_chars") or 0),
                completion_tokens=diagnostics.get("completion_tokens"),
                prompt_version=("legacy-persona@f3aa1dd" if label == "old" else "working-tree-light-persona"),
                context_fingerprint=context_fingerprint,
                search_calls=0,
                tool_calls=0,
            )
            (old_samples if label == "old" else new_samples).append(row)
            completed += 1
            print(f"{completed}/ {limit * 2} {label} case={case_id} status={'ok' if ok else state} {latency_ms:.0f}ms",
                  flush=True)
        _save_jsonl(output_dir / "old.jsonl", sorted(old_samples, key=lambda item: item.case_id))
        _save_jsonl(output_dir / "new.jsonl", sorted(new_samples, key=lambda item: item.case_id))

    if limit == len(FIXED_CASES):
        blind = build_blind_review(
            old_samples, new_samples,
            old_version="legacy-persona@f3aa1dd",
            new_version="working-tree-light-persona",
            seed=args.seed,
        )
        _save_json(output_dir / "blind_review.json", blind["review_rows"])
        _save_json(output_dir / "answer_key.json", blind["answer_key"])
        _save_review_guide(output_dir / "review_guide.md")
    summary = {
        "scope": "prompt-only generation; no search, tools, history, group data, or production files",
        "model_ref": model_ref,
        "temperature": 1.0,
        "top_p": 1.0,
        "max_tokens_by_case": {
            str(case_id): app._reply_output_profile(
                FIXED_CASES[case_id].prompt,
                app._message_route(FIXED_CASES[case_id].prompt),
            )["max_tokens"]
            for case_id in range(limit)
        },
        "case_count_per_version": limit,
        "request_count": completed,
        "failed_requests": sum(bool(row.model_failures) for row in old_samples + new_samples),
        "output_dir": str(output_dir),
        "baseline_revision": "f3aa1dd",
        "new_prompt": "current working-tree TAFFY_LIGHT_PROMPT plus scene prompt",
        "review_status": "unrated; keep answer_key.json separate during blind review",
    }
    _save_json(output_dir / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-model-calls", action="store_true",
                        help="allow up to 68 DeepSeek API requests")
    parser.add_argument("--max-cases", type=int, default=34, choices=range(1, 35))
    parser.add_argument("--seed", type=int, default=20261016)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if not args.confirm_model_calls:
        parser.error("model calls are disabled unless --confirm-model-calls is provided")

    os.environ["QQ_TEST_MODE"] = "1"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    runtime = ROOT / ".tmp" / "taffy-ab-runtime"
    os.environ["QQ_TEST_DATA_DIR"] = str(runtime)
    _load_eval_credentials()

    from qqbot_core.evaluation import FIXED_CASES
    import bridge_qq as app

    output_dir = args.output_dir or (ROOT / ".tmp" / f"taffy-ab-{datetime.now():%Y%m%d-%H%M%S}")
    output_dir = output_dir.resolve()
    temp_root = (ROOT / ".tmp").resolve()
    if temp_root not in output_dir.parents:
        parser.error("output directory must stay under <项目根>\\.tmp")
    output_dir.mkdir(parents=True, exist_ok=False)
    print(f"model={app.DEEPSEEK_MODEL_REF} cases={min(args.max_cases, len(FIXED_CASES))} per_version max_tokens=180 output={output_dir}",
          flush=True)
    summary = asyncio.run(_run(app, args, output_dir))
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
