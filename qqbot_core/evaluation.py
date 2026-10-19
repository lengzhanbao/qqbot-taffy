"""Offline comparison only. No model credentials, deployment, or strategy activation."""
import argparse
from collections import Counter
from dataclasses import dataclass, field, replace
import json
import math
import random
import statistics
import re
import uuid


@dataclass(frozen=True)
class DialogueCase:
    prompt: str
    evidence: str
    context: str = "none"
    max_chars: int = 120


FIXED_CASES = tuple(DialogueCase(*case) for case in [
    ("你是牛马吗", "NEVER"), ("塔菲你又摸鱼", "NEVER"),
    ("你是不是笨蛋", "NEVER"), ("扣1送地狱火", "NEVER"),
    ("111大哥真送吗", "NEVER"), ("你现在很聪明了吗", "NEVER"),
    ("塔菲你现在开心吗", "NEVER"), ("你现在使用什么模型", "LOCAL"),
    ("现在北京天气怎么样", "REQUIRED"), ("塔菲最近直播了什么", "REQUIRED"),
    ("你觉得今年世界杯冠军是谁", "REQUIRED"), ("帮我搜一下最新的 AI 新闻", "REQUIRED"),
    ("刚才那个继续说", "NEVER", "same_user"),
    ("换个话题，你觉得我聪明吗", "NEVER"),
    ("你好", "NEVER"), ("早", "NEVER"), ("晚安", "NEVER"),
    ("谢谢", "NEVER"), ("我很难过", "NEVER"), ("安慰我", "NEVER"),
    ("陪陪我", "NEVER"), ("你喜欢音乐吗", "NEVER"),
    ("牛马是什么意思", "AUTO"), ("牛马这个梗最早来自哪里", "AUTO"),
    ("Python装饰器是什么", "AUTO"), ("什么是闭包", "AUTO"),
    ("今天有什么新闻", "AUTO"), ("最新的AI新闻", "REQUIRED"),
    ("帮我写一个短故事", "NEVER"), ("不要联网，陪我聊天", "NEVER"),
    ("群里刚才大家聊的那个，继续说", "NEVER", "group"),
    ("换个话题，晚安", "NEVER"), ("哈哈哈", "NEVER"), ("塔菲你好菜啊", "NEVER"),
])

MULTI_TURN_CASES = (
    (("a", "讨论比赛比分"), ("b", "扣1送地狱火")),
    (("a", "我喜欢足球"), ("b", "我喜欢音乐"), ("a", "刚才那个继续说")),
    (("a", "今天讨论游戏"), ("b", "换个话题，编程")),
)


@dataclass(frozen=True)
class ReplySample:
    case_id: int
    text: str
    latency_ms: float
    selected_group_ids: tuple = ()
    group_id: str = "test"
    authorized: bool = False
    human_scores: dict = field(default_factory=dict)
    model_ref: str = ""
    attempted_models: tuple = ()
    failed_models: tuple = ()
    model_attempts: int = 0
    model_failures: int = 0
    temperature: float = None
    top_p: float = None
    max_tokens: int = None
    finish_reason: str = ""
    raw_chars: int = 0
    completion_tokens: int = None
    prompt_version: str = ""
    context_fingerprint: str = ""
    search_calls: int = 0
    tool_calls: int = 0


_SCORE_AXES = ("persona", "naturalness", "relevance", "meme_fit", "factuality", "groundedness")


def _validate_sample(sample):
    if (not isinstance(sample.case_id, int) or isinstance(sample.case_id, bool)
            or not 0 <= sample.case_id < len(FIXED_CASES)
            or not isinstance(sample.text, str)
            or not isinstance(sample.latency_ms, (int, float))
            or isinstance(sample.latency_ms, bool)
            or not math.isfinite(sample.latency_ms) or sample.latency_ms < 0
            or not isinstance(sample.human_scores, dict)):
        raise ValueError("invalid case or latency")
    for name in ("model_attempts", "model_failures", "search_calls", "tool_calls"):
        value = getattr(sample, name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("invalid attempt or tool counters")
    if sample.model_failures > sample.model_attempts:
        raise ValueError("model_failures cannot exceed model_attempts")
    for name in ("attempted_models", "failed_models", "selected_group_ids"):
        values = getattr(sample, name)
        if not isinstance(values, (tuple, list)) or any(not isinstance(value, str) for value in values):
            raise ValueError(name + " must be an array of strings")
    for name in ("model_ref", "prompt_version", "context_fingerprint"):
        if not isinstance(getattr(sample, name), str):
            raise ValueError(name + " must be a string")
    for name, lower, upper in (("temperature", 0, 2), ("top_p", 0, 1)):
        value = getattr(sample, name)
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)
                                  or not math.isfinite(value) or not lower <= value <= upper):
            raise ValueError("invalid " + name)
    if sample.max_tokens is not None and (
            not isinstance(sample.max_tokens, int) or isinstance(sample.max_tokens, bool)
            or sample.max_tokens < 1):
        raise ValueError("invalid max_tokens")
    if not isinstance(sample.finish_reason, str):
        raise ValueError("finish_reason must be a string")
    if (not isinstance(sample.raw_chars, int) or isinstance(sample.raw_chars, bool)
            or sample.raw_chars < 0):
        raise ValueError("raw_chars must be a non-negative integer")
    if sample.completion_tokens is not None and (
            not isinstance(sample.completion_tokens, int)
            or isinstance(sample.completion_tokens, bool)
            or sample.completion_tokens < 0):
        raise ValueError("completion_tokens must be a non-negative integer")
    if any(axis not in _SCORE_AXES or not isinstance(score, (int, float)) or isinstance(score, bool)
           or not math.isfinite(score) or not 1 <= score <= 5
           for axis, score in sample.human_scores.items()):
        raise ValueError("human scores must be finite values between 1 and 5")


def _metadata_complete(sample):
    return bool(sample.model_ref and sample.attempted_models and all(sample.attempted_models)
                and all(sample.failed_models) and sample.model_attempts >= 1
                and sample.temperature is not None and sample.top_p is not None
                and sample.max_tokens is not None and sample.prompt_version
                and sample.context_fingerprint)


def _generation_profile(sample):
    return (sample.model_ref, sample.temperature, sample.top_p, sample.max_tokens)


def summarize(samples):
    """Human scores are supplied by blinded reviewers, never inferred here."""
    samples = list(samples)
    for sample in samples:
        _validate_sample(sample)
    delays = sorted(s.latency_ms for s in samples)
    def percentile(p):
        return delays[max(0, math.ceil(len(delays) * p) - 1)] if delays else None
    model_counts = Counter(s.model_ref for s in samples if s.model_ref)
    profiles = sorted({_generation_profile(s) for s in samples if _metadata_complete(s)}, key=repr)
    case_counts = Counter(s.case_id for s in samples)
    return {
        "count": len(samples), "p50_ms": percentile(.5), "p95_ms": percentile(.95),
        "case_ids": sorted({s.case_id for s in samples}),
        "case_counts": {str(case_id): count for case_id, count in sorted(case_counts.items())},
        "complete_review": bool(samples) and all(all(axis in s.human_scores for axis in _SCORE_AXES) for s in samples),
        "metadata_complete": bool(samples) and all(_metadata_complete(s) for s in samples),
        "model_counts": dict(sorted(model_counts.items())),
        "generation_profiles": [dict(model_ref=p[0], temperature=p[1], top_p=p[2], max_tokens=p[3])
                                for p in profiles],
        "model_attempts": sum(s.model_attempts for s in samples),
        "model_failures": sum(s.model_failures for s in samples),
        "truncated_outputs": sum(s.finish_reason == "length" for s in samples),
        "raw_chars": sum(s.raw_chars for s in samples),
        "completion_tokens": sum(
            s.completion_tokens or 0 for s in samples
        ),
        "failed_models": dict(sorted(Counter(m for s in samples for m in s.failed_models).items())),
        "search_calls": sum(s.search_calls for s in samples),
        "tool_calls": sum(s.tool_calls for s in samples),
        "evidence_violations": sum(
            s.search_calls > 0 or s.tool_calls > 0
            for s in samples if FIXED_CASES[s.case_id].evidence in {"NEVER", "LOCAL"}),
        "mean_chars": statistics.mean(len(s.text) for s in samples) if samples else None,
        "overlength_count": sum(len(s.text) > FIXED_CASES[s.case_id].max_chars for s in samples),
        "cross_group_count": sum(any(str(g) != str(s.group_id) for g in s.selected_group_ids) for s in samples),
        "unauthorized_count": sum(bool(s.selected_group_ids) and not s.authorized for s in samples),
        "mechanical_suffix_count": sum(bool(re.search(r"没联网|凭现有知识答", s.text)) and
                                       FIXED_CASES[s.case_id].evidence == "NEVER" for s in samples),
        "human_scores": {axis: statistics.mean(values) if (values := [
            s.human_scores[axis] for s in samples if axis in s.human_scores]) else None
                          for axis in _SCORE_AXES},
    }


def _pairing(old_samples, new_samples):
    old_counts = Counter(s.case_id for s in old_samples)
    new_counts = Counter(s.case_id for s in new_samples)
    expected = set(range(len(FIXED_CASES)))
    old_map = {s.case_id: s for s in old_samples if old_counts[s.case_id] == 1}
    new_map = {s.case_id: s for s in new_samples if new_counts[s.case_id] == 1}
    common = sorted(set(old_map) & set(new_map))
    model_mismatches, parameter_mismatches, context_mismatches = [], [], []
    for case_id in common:
        old, new = old_map[case_id], new_map[case_id]
        if old.model_ref != new.model_ref:
            model_mismatches.append(case_id)
        if (old.temperature, old.top_p, old.max_tokens) != (new.temperature, new.top_p, new.max_tokens):
            parameter_mismatches.append(case_id)
        if old.context_fingerprint != new.context_fingerprint:
            context_mismatches.append(case_id)
    old_duplicates = sorted(case_id for case_id, count in old_counts.items() if count != 1)
    new_duplicates = sorted(case_id for case_id, count in new_counts.items() if count != 1)
    complete = (len(old_samples) == len(FIXED_CASES) and len(new_samples) == len(FIXED_CASES)
                and set(old_counts) == expected and set(new_counts) == expected
                and not old_duplicates and not new_duplicates)
    return {
        "complete": complete,
        "matched_case_ids": common,
        "old_missing": sorted(expected - set(old_counts)),
        "new_missing": sorted(expected - set(new_counts)),
        "old_duplicates": old_duplicates,
        "new_duplicates": new_duplicates,
        "model_mismatches": model_mismatches,
        "parameter_mismatches": parameter_mismatches,
        "context_mismatches": context_mismatches,
    }


def build_blind_review(old_samples, new_samples, *, old_version="old", new_version="new", seed=None):
    """Return randomized A/B rows and a separate key; caller must keep key from reviewers."""
    if not isinstance(old_version, str) or not isinstance(new_version, str) or old_version == new_version:
        raise ValueError("distinct version labels required")
    old_samples, new_samples = list(old_samples), list(new_samples)
    pairing = _pairing(old_samples, new_samples)
    if not pairing["complete"]:
        raise ValueError("blind review requires exactly one sample for each of 34 cases per version")
    old_map = {s.case_id: s for s in old_samples}
    new_map = {s.case_id: s for s in new_samples}
    rng = random.Random(seed)
    rows, key = [], {}
    for case_id in range(len(FIXED_CASES)):
        old_first = bool(rng.getrandbits(1))
        a, b = ((old_map[case_id], new_map[case_id]) if old_first
                else (new_map[case_id], old_map[case_id]))
        rows.append({"case_id": case_id, "prompt": FIXED_CASES[case_id].prompt,
                     "A": a.text, "B": b.text, "ratings": {"A": {}, "B": {}}})
        key[str(case_id)] = {"A": old_version if old_first else new_version,
                             "B": new_version if old_first else old_version}
    rng.shuffle(rows)
    return {"review_rows": rows,
            "answer_key": {"old_version": old_version, "new_version": new_version, "cases": key}}


def apply_blind_review_scores(old_samples, new_samples, review_rows, answer_key):
    """Validate anonymous ratings and attach them to their source version."""
    old_samples, new_samples, review_rows = list(old_samples), list(new_samples), list(review_rows)
    if not _pairing(old_samples, new_samples)["complete"]:
        raise ValueError("score import requires exactly one sample for each of 34 cases per version")
    expected_ids = set(range(len(FIXED_CASES)))
    rows = {}
    for row in review_rows:
        if (not isinstance(row, dict) or not isinstance(row.get("case_id"), int)
                or isinstance(row.get("case_id"), bool)):
            raise ValueError("invalid blind review row")
        case_id = row["case_id"]
        if case_id in rows or case_id not in expected_ids or row.get("prompt") != FIXED_CASES[case_id].prompt:
            raise ValueError("duplicate or mismatched blind review case")
        rows[case_id] = row
    if set(rows) != expected_ids:
        raise ValueError("blind review must contain all 34 cases")
    if (not isinstance(answer_key, dict) or not isinstance(answer_key.get("cases"), dict)
            or not isinstance(answer_key.get("old_version"), str)
            or not isinstance(answer_key.get("new_version"), str)
            or answer_key["old_version"] == answer_key["new_version"]):
        raise ValueError("invalid blind review key")
    if set(answer_key["cases"]) != {str(case_id) for case_id in expected_ids}:
        raise ValueError("blind review key must contain all 34 cases")

    old_map = {sample.case_id: sample for sample in old_samples}
    new_map = {sample.case_id: sample for sample in new_samples}
    old_scores, new_scores = {}, {}
    for case_id, row in rows.items():
        labels = answer_key["cases"][str(case_id)]
        ratings = row.get("ratings")
        if (not isinstance(labels, dict) or set(labels) != {"A", "B"}
                or any(not isinstance(value, str) for value in labels.values())
                or set(labels.values()) != {answer_key.get("old_version"), answer_key.get("new_version")}
                or not isinstance(ratings, dict) or set(ratings) != {"A", "B"}):
            raise ValueError("invalid blind labels or ratings")
        for variant in ("A", "B"):
            version = labels[variant]
            source = "old" if version == answer_key["old_version"] else "new"
            expected_text = old_map[case_id].text if source == "old" else new_map[case_id].text
            if row.get(variant) != expected_text:
                raise ValueError("blind review text does not match answer key")
            score = ratings[variant]
            if not isinstance(score, dict) or set(score) != set(_SCORE_AXES):
                raise ValueError("each blind response needs all six human scores")
            _validate_sample(replace(old_map[case_id] if source == "old" else new_map[case_id],
                                     human_scores=score))
            (old_scores if source == "old" else new_scores)[case_id] = score
    return ([replace(sample, human_scores=old_scores[sample.case_id])
             for sample in sorted(old_samples, key=lambda item: item.case_id)],
            [replace(sample, human_scores=new_scores[sample.case_id])
             for sample in sorted(new_samples, key=lambda item: item.case_id)])


def compare_versions(old_version, new_version, old_samples, new_samples):
    old_samples, new_samples = list(old_samples), list(new_samples)
    return {"old_version": old_version, "new_version": new_version,
            "old": summarize(old_samples), "new": summarize(new_samples),
            "pairing": _pairing(old_samples, new_samples),
            "deployment": "manual_approval_required"}


@dataclass(frozen=True)
class StrategyProposal:
    id: str
    baseline_version: str
    proposed_version: str
    reason: str
    parameters: tuple
    status: str = "pending_evaluation"


def propose_strategy(baseline_version, reason, *, max_reply_chars=45, analogy_strength=0):
    if not 20 <= max_reply_chars <= 120 or analogy_strength not in (0, 1, 2):
        raise ValueError("strategy parameters outside approved bounds")
    return StrategyProposal(uuid.uuid4().hex, baseline_version, uuid.uuid4().hex,
                            reason[:200], (("max_reply_chars", max_reply_chars),
                                          ("analogy_strength", analogy_strength)))


def quality_gate(comparison):
    old, new = comparison["old"], comparison["new"]
    pairing = comparison.get("pairing", {})
    reasons = []
    expected = list(range(len(FIXED_CASES)))
    if (not pairing.get("complete") or old["case_ids"] != expected or new["case_ids"] != expected
            or any(count != 1 for count in old.get("case_counts", {}).values())
            or any(count != 1 for count in new.get("case_counts", {}).values())):
        reasons.append("incomplete_or_unmatched_coverage")
    if not old.get("metadata_complete") or not new.get("metadata_complete"):
        reasons.append("missing_generation_metadata")
    if pairing.get("model_mismatches"):
        reasons.append("paired_model_mismatch")
    if pairing.get("parameter_mismatches"):
        reasons.append("paired_parameter_mismatch")
    if pairing.get("context_mismatches"):
        reasons.append("paired_context_mismatch")
    old_profiles, new_profiles = old.get("generation_profiles", []), new.get("generation_profiles", [])
    if len(old_profiles) != 1 or len(new_profiles) != 1 or old_profiles != new_profiles:
        reasons.append("inconsistent_run_configuration")
    if new.get("model_failures", 0) > old.get("model_failures", 0):
        reasons.append("model_failure_regression")
    if new.get("evidence_violations", 0):
        reasons.append("evidence_policy_violation")
    if not old["complete_review"] or not new["complete_review"]:
        reasons.append("incomplete_blind_review")
    if new["cross_group_count"] or new["unauthorized_count"]:
        reasons.append("scope_violation")
    if new["overlength_count"] > old["overlength_count"] or new["mechanical_suffix_count"] > old["mechanical_suffix_count"]:
        reasons.append("output_regression")
    for axis in ("persona", "naturalness", "relevance", "meme_fit", "factuality", "groundedness"):
        before, after = old["human_scores"][axis], new["human_scores"][axis]
        if before is None or after is None:
            reasons.append("missing_blind_review:" + axis)
        elif after < before:
            reasons.append("quality_regression:" + axis)
    if old["p95_ms"] is not None and new["p95_ms"] > old["p95_ms"] * 1.1:
        reasons.append("latency_regression")
    return {"eligible_for_manual_review": not reasons, "reasons": reasons,
            "auto_apply": False}


def load_reply_samples_jsonl(path):
    """Load manually collected, blinded evaluation rows; never calls a model."""
    samples = []
    allowed = set(ReplySample.__dataclass_fields__)
    with open(path, encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict) or set(value) - allowed:
                    raise ValueError("unexpected sample fields")
                groups = value.get("selected_group_ids", ())
                if not isinstance(groups, (list, tuple)):
                    raise ValueError("selected_group_ids must be an array")
                value["selected_group_ids"] = tuple(map(str, groups))
                for name in ("attempted_models", "failed_models"):
                    models = value.get(name, ())
                    if not isinstance(models, (list, tuple)):
                        raise ValueError(name + " must be an array")
                    value[name] = tuple(models)
                sample = ReplySample(**value)
                summarize([sample])
                samples.append(sample)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid sample at line {line_number}: {exc}") from exc
    return samples


def compare_reply_sample_files(old_path, new_path, *, old_version="old", new_version="new"):
    comparison = compare_versions(
        old_version, new_version,
        load_reply_samples_jsonl(old_path), load_reply_samples_jsonl(new_path))
    return {"comparison": comparison, "quality_gate": quality_gate(comparison)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare manually collected QQBot reply samples offline.")
    parser.add_argument("old_jsonl")
    parser.add_argument("new_jsonl")
    parser.add_argument("--old-version", default="old")
    parser.add_argument("--new-version", default="new")
    args = parser.parse_args(argv)
    result = compare_reply_sample_files(args.old_jsonl, args.new_jsonl,
                                        old_version=args.old_version,
                                        new_version=args.new_version)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["quality_gate"]["eligible_for_manual_review"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
