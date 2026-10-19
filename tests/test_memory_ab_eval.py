"""The comparison runner is synthetic, bounded, and offline by default."""
import asyncio
import json
from types import SimpleNamespace

import bridge_qq as app
from scripts import run_memory_ab_eval as runner


def test_offline_comparison_builds_scoped_cases_without_api_calls(monkeypatch, tmp_path):
    async def forbidden(*args, **kwargs):
        raise AssertionError("offline comparison must not call a model")

    monkeypatch.setattr(app.Bridge, "_run_api_call", forbidden)
    args = SimpleNamespace(embedding_path="", model="deepseek/deepseek-flash", live=False, max_cases=8)
    summary = asyncio.run(runner.evaluate(app, args, tmp_path))
    rows = json.loads((tmp_path / "samples.json").read_text(encoding="utf-8"))
    assert len(rows) == 16 and summary["live"] is False
    assert all(row["state"] == "offline" for row in rows)
    on = {row["case"]: row for row in rows if row["arm"] == "on"}
    assert on["next_day_event"]["trace"]["selected"] > 0
    assert on["own_preference"]["trace"]["selected"] > 0
    for case in ("other_group", "topic_reset", "current_fact", "revoked_consent", "expired_event", "unknown_event"):
        assert on[case]["trace"]["selected"] == 0


def test_live_runner_uses_one_model_and_exactly_two_calls_per_case(monkeypatch, tmp_path):
    calls = []

    async def fake(self, body, url, key, deadline, diagnostics=None):
        calls.append(body)
        diagnostics.update(prompt_tokens=10, completion_tokens=2)
        return "没有足够记录。", "ok", [], "stop", []

    monkeypatch.setattr(app.Bridge, "_run_api_call", fake)
    monkeypatch.setattr(app, "PROVIDERS", {
        "opencode": {"url": "https://example.invalid", "key": "synthetic-test-key"}})
    args = SimpleNamespace(embedding_path="", model="opencode/deepseek-v4.1-flash", live=True, max_cases=2)
    summary = asyncio.run(runner.evaluate(app, args, tmp_path))
    assert len(calls) == 4
    assert {body["model"] for body in calls} == {"deepseek-v4.1-flash"}
    assert all(body["max_tokens"] == 1024 for body in calls)
    assert all(body["thinking"] == {"type": "disabled"} for body in calls)
    assert summary["metrics"]["on"]["prompt_tokens_reported"] == 20
    assert summary["metrics"]["off"]["completion_tokens_reported"] == 4


def test_percentile_uses_nearest_rank_without_fabricating_empty_values():
    assert runner.percentile([], .95) is None
    assert runner.percentile([1, 2, 3, 4, 5], .5) == 3
    assert runner.percentile([1, 2, 3, 4, 5], .95) == 5
