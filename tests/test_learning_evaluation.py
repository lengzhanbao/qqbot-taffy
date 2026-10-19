import pytest
from dataclasses import asdict, replace
import json
from qqbot_core.evaluation import (FIXED_CASES, MULTI_TURN_CASES, ReplySample,
    summarize, compare_versions, compare_reply_sample_files, quality_gate,
    build_blind_review, apply_blind_review_scores, propose_strategy)
from qqbot_core.routing import classify_message, select_context_messages


@pytest.mark.parametrize("case", FIXED_CASES, ids=lambda case: case.prompt)
def test_fixed_routing_cases(case):
    route = classify_message(case.prompt)
    assert route.evidence == case.evidence
    assert route.context == case.context


def samples(scores=True, latency=100, **kwargs):
    axes = ("persona", "naturalness", "relevance", "meme_fit", "factuality", "groundedness")
    metadata = {
        "model_ref": "test/model-v1", "attempted_models": ("test/model-v1",),
        "model_attempts": 1, "temperature": 0.3, "top_p": 0.8,
        "max_tokens": 256, "prompt_version": "prompt-v1",
    }
    metadata.update(kwargs)
    return [ReplySample(i, "简短模拟回答", latency, context_fingerprint=f"context-{i}",
                        human_scores=dict.fromkeys(axes, 4) if scores else {}, **metadata)
            for i in range(len(FIXED_CASES))]


def test_offline_gate_requires_full_blind_review_and_never_applies():
    baseline = samples()
    comparison = compare_versions("old", "new", baseline, samples())
    assert quality_gate(comparison) == {"eligible_for_manual_review": True, "reasons": [], "auto_apply": False}
    assert not quality_gate(compare_versions("old", "new", baseline, samples(scores=False)))["eligible_for_manual_review"]
    assert not quality_gate(compare_versions("old", "new", baseline, samples(latency=200)))["eligible_for_manual_review"]
    duplicates = [baseline[0]] * 34
    assert not quality_gate(compare_versions("old", "new", duplicates, duplicates))["eligible_for_manual_review"]
    proposal = propose_strategy("old", "回复过长", max_reply_chars=40)
    assert proposal.status == "pending_evaluation"
    with pytest.raises(ValueError):
        propose_strategy("old", "arbitrary", max_reply_chars=10000)


def test_offline_gate_rejects_meme_fit_regression():
    baseline = samples()
    degraded = samples()
    for sample in degraded:
        sample.human_scores["meme_fit"] = 3
    result = quality_gate(compare_versions("old", "new", baseline, degraded))
    assert "quality_regression:meme_fit" in result["reasons"]


def test_metrics_flag_scope_leaks_suffixes_and_invalid_scores():
    metrics = summarize([ReplySample(0, "没联网喵", 10, ("other",), "here", False), ReplySample(1, "短句", 100)])
    assert metrics["p50_ms"] == 10 and metrics["p95_ms"] == 100
    assert metrics["cross_group_count"] == metrics["unauthorized_count"] == metrics["mechanical_suffix_count"] == 1
    assert all(v is None for v in metrics["human_scores"].values())
    for sample in [ReplySample(999, "", 1), ReplySample(0, "", float("nan")), ReplySample(0, "", 1, human_scores={"persona": 6})]:
        with pytest.raises(ValueError):
            summarize([sample])


def test_metrics_report_api_truncation_and_raw_generation_size():
    metrics = summarize([ReplySample(
        0, "清理后的回复", 20, finish_reason="length",
        raw_chars=30, completion_tokens=12,
    )])
    assert metrics["truncated_outputs"] == 1
    assert metrics["raw_chars"] == 30
    assert metrics["completion_tokens"] == 12


def test_jsonl_comparison_reports_manual_gate_without_auto_apply(tmp_path):
    axes = ("persona", "naturalness", "relevance", "meme_fit", "factuality", "groundedness")
    old_path, new_path = tmp_path / "old.jsonl", tmp_path / "new.jsonl"
    old = [asdict(ReplySample(i, "旧回复", 100, model_ref="test/model-v1",
                              attempted_models=("test/model-v1",), model_attempts=1,
                              temperature=0.3, top_p=0.8, max_tokens=256,
                              prompt_version="prompt-old", context_fingerprint=f"context-{i}",
                              human_scores=dict.fromkeys(axes, 4)))
           for i in range(len(FIXED_CASES))]
    new = [asdict(ReplySample(i, "新回复", 110, model_ref="test/model-v1",
                              attempted_models=("test/model-v1",), model_attempts=1,
                              temperature=0.3, top_p=0.8, max_tokens=256,
                              prompt_version="prompt-new", context_fingerprint=f"context-{i}",
                              human_scores=dict.fromkeys(axes, 4)))
           for i in range(len(FIXED_CASES))]
    old_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in old), encoding="utf-8")
    new_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in new), encoding="utf-8")

    report = compare_reply_sample_files(old_path, new_path, old_version="before", new_version="after")
    assert report["quality_gate"] == {"eligible_for_manual_review": True, "reasons": [], "auto_apply": False}
    assert report["comparison"]["new"]["count"] == len(FIXED_CASES)


def test_jsonl_comparison_rejects_malformed_rows(tmp_path):
    old_path, new_path = tmp_path / "old.jsonl", tmp_path / "new.jsonl"
    old_path.write_text("not-json", encoding="utf-8")
    new_path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        compare_reply_sample_files(old_path, new_path)


@pytest.mark.parametrize("sequence", MULTI_TURN_CASES)
def test_multi_turn_context_excludes_other_speakers(sequence):
    history = []
    for uid, text in sequence:
        route = classify_message(text)
        context = select_context_messages(history, route, uid, is_group=True)
        if route.context == "none":
            assert not context
        elif route.context == "same_user":
            assert all(m.get("speaker_id") == uid for m in context)
        history.append({"role": "user", "speaker_id": uid, "content": text})


def test_quality_gate_requires_exact_34_unique_pairs():
    complete = samples()
    missing = complete[:-1]
    assert "incomplete_or_unmatched_coverage" in quality_gate(
        compare_versions("old", "new", missing, missing))["reasons"]
    duplicate = complete[:-1] + [complete[0]]
    assert "incomplete_or_unmatched_coverage" in quality_gate(
        compare_versions("old", "new", duplicate, duplicate))["reasons"]


@pytest.mark.parametrize(("field", "reason"), [
    ("model_ref", "paired_model_mismatch"),
    ("temperature", "paired_parameter_mismatch"),
    ("context_fingerprint", "paired_context_mismatch"),
])
def test_quality_gate_rejects_unmatched_generation_conditions(field, reason):
    old = samples()
    new = samples()
    new[0] = replace(new[0], **{field: "different" if field != "temperature" else 0.7})
    assert reason in quality_gate(compare_versions("old", "new", old, new))["reasons"]


def test_quality_gate_requires_complete_model_failure_and_route_metrics():
    old, new = samples(), samples()
    new[0] = replace(new[0], model_attempts=2, model_failures=1,
                     attempted_models=("test/model-v1", "fallback/model"))
    assert "model_failure_regression" in quality_gate(
        compare_versions("old", "new", old, new))["reasons"]
    new = samples()
    new[0] = replace(new[0], search_calls=1)
    assert "evidence_policy_violation" in quality_gate(
        compare_versions("old", "new", old, new))["reasons"]


def test_blind_review_randomizes_labels_and_separates_answer_key():
    package = build_blind_review(samples(), samples(), old_version="before", new_version="after", seed=27)
    rows, key = package["review_rows"], package["answer_key"]
    assert len(rows) == len(FIXED_CASES)
    assert {row["case_id"] for row in rows} == set(range(len(FIXED_CASES)))
    assert all(set(row) == {"case_id", "prompt", "A", "B", "ratings"} for row in rows)
    assert all(set(value.values()) == {"before", "after"} for value in key["cases"].values())
    assert any(value["A"] == "before" for value in key["cases"].values())
    assert any(value["A"] == "after" for value in key["cases"].values())
    with pytest.raises(ValueError, match="34 cases"):
        build_blind_review(samples()[:-1], samples())


def test_blind_scores_round_trip_only_after_using_separate_key():
    old, new = samples(), samples()
    package = build_blind_review(old, new, old_version="before", new_version="after", seed=12)
    for row in package["review_rows"]:
        row["ratings"] = {
            "A": dict.fromkeys(("persona", "naturalness", "relevance", "meme_fit", "factuality", "groundedness"), 4),
            "B": dict.fromkeys(("persona", "naturalness", "relevance", "meme_fit", "factuality", "groundedness"), 3),
        }
    old_scored, new_scored = apply_blind_review_scores(
        old, new, package["review_rows"], package["answer_key"])
    assert len(old_scored) == len(new_scored) == 34
    assert {sample.human_scores["persona"] for sample in old_scored} == {3, 4}
    assert {sample.human_scores["persona"] for sample in new_scored} == {3, 4}
    comparison = compare_versions("before", "after", old_scored, new_scored)
    assert comparison["old"]["complete_review"] and comparison["new"]["complete_review"]


def test_blind_score_import_rejects_tampered_or_incomplete_review():
    old, new = samples(), samples()
    package = build_blind_review(old, new, old_version="before", new_version="after", seed=8)
    with pytest.raises(ValueError, match="all six"):
        apply_blind_review_scores(old, new, package["review_rows"], package["answer_key"])
    package["review_rows"][0]["A"] = "tampered"
    axes = ("persona", "naturalness", "relevance", "meme_fit", "factuality", "groundedness")
    for row in package["review_rows"]:
        row["ratings"] = {"A": dict.fromkeys(axes, 4), "B": dict.fromkeys(axes, 4)}
    with pytest.raises(ValueError, match="does not match"):
        apply_blind_review_scores(old, new, package["review_rows"], package["answer_key"])
