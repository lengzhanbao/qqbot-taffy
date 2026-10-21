"""Offline regressions for model options, silent attempts, and send accounting."""
import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

import bridge_qq as app
from test_proactive import _ready


@pytest.fixture
def model_bridge(monkeypatch):
    bridge = object.__new__(app.Bridge)
    bridge.api_key = "test"
    bridge.model_semaphore = None
    monkeypatch.setattr(app, "_model_health", {})
    monkeypatch.setattr(app, "_provider_cooldown", {})
    monkeypatch.setattr(app, "_tool_model_cooldown", {})
    monkeypatch.setattr(app, "write_health", lambda **kw: None)
    monkeypatch.setattr(app, "inc_stat", lambda *a: None)
    monkeypatch.setattr(app, "DEEPSEEK_MODEL_REF", "unused/sentinel")
    monkeypatch.setattr(app, "_sort_models", lambda models: list(models))
    monkeypatch.setattr(app, "PROVIDERS", {
        "deepseek": {"url": "https://example.invalid/direct", "key": "test"},
        "opencode": {"url": "https://example.invalid/proxy", "key": "test"},
    })
    return bridge


@pytest.mark.parametrize("ref,expected", [
    ("deepseek/deepseek-flash", True),
    ("opencode/deepseek-v4.1-flash", True),
    ("opencode/longcat-2.5-preview-free", False),
    ("opencode/deepseek-unverified", False),
])
@pytest.mark.parametrize("method", ["_complete", "_complete_race"])
def test_model_options_are_consistent_for_serial_and_race(model_bridge, ref, expected, method):
    calls = []

    async def fake(body, *args):
        calls.append(body)
        return "正文已经生成。", "ok", [], "stop", []

    model_bridge._run_api_call = fake
    reply, model = asyncio.run(getattr(model_bridge, method)(
        "system", [], max_tokens=1024, model_refs=[ref], session_key="group"))
    assert reply == "正文已经生成。" and model == ref
    assert calls[0]["max_tokens"] == 1024
    assert calls[0]["_session_id"] == "group"
    assert ("thinking" in calls[0]) is expected
    if expected:
        assert calls[0]["thinking"] == {"type": "disabled"}


@pytest.mark.parametrize("ref,should_call", [
    ("opencode/deepseek-v4.1-flash", False),
    ("opencode/longcat-2.5-preview-free", True),
])
def test_tool_planner_excludes_deepseek_aliases(model_bridge, ref, should_call):
    calls = []
    tool = {"id": "test-tool", "type": "function",
            "function": {"name": "search_web", "arguments": '{"query":"test"}'}}

    async def fake(body, *args):
        calls.append(body)
        return "", "ok", [tool], "stop", []

    model_bridge._run_api_call = fake
    result = asyncio.run(model_bridge._complete_with_tools("system", [], model_refs=[ref]))
    if should_call:
        assert result[0] == [tool]
        assert calls[0]["tools"]
        assert "thinking" not in calls[0]
    else:
        assert result == ([], "", "", [])
        assert calls == []


def test_empty_length_response_falls_back_without_leaking_options(model_bridge):
    calls = []

    async def fake(body, *args):
        calls.append(dict(body))
        if body["model"] == "deepseek-v4.1-flash":
            return "", "ok", [], "length", []
        return "备用模型正文。", "ok", [], "stop", []

    model_bridge._run_api_call = fake
    reply, model = asyncio.run(model_bridge._complete("system", [], model_refs=[
        "opencode/deepseek-v4.1-flash", "opencode/longcat-2.5-preview-free"]))
    assert reply == "备用模型正文。"
    assert model == "opencode/longcat-2.5-preview-free"
    assert len(calls) == 2
    assert calls[0]["thinking"] == {"type": "disabled"}
    assert "thinking" not in calls[1]


def test_reasoning_only_stream_is_not_sent_as_final_content(model_bridge):
    diagnostics = {}
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"reasoning_content": "private reasoning"}}]}),
        'data: {"choices":[{"delta":{},"finish_reason":"length"}],"usage":{"completion_tokens":64}}',
        "data: [DONE]",
    ]
    result = model_bridge._parse_stream_response(lines, diagnostics)
    assert result[0] == "" and result[3] == "length"
    assert diagnostics["raw_chars"] == 0
    assert diagnostics["completion_tokens"] == 64


def test_success_is_counted_after_send_acknowledgement(monkeypatch):
    bot = _ready(monkeypatch)

    async def send(*args):
        assert bot._proactive_daily_count == 0
        assert bot._proactive_last_global == 0
        assert bot._proactive_last_by_group == {}
        assert bot._proactive_inflight == {"42"}
        return {"status": "ok", "retcode": 0}

    bot.send_group_text = send
    assert asyncio.run(bot._maybe_proactive_group_reply({}, "42", "你是牛马吗"))
    assert bot._proactive_daily_count == 1
    assert bot._proactive_group_counts == {"42": 1}
    assert bot._proactive_hourly_counts == {"42|2026-10-15 12": 1}
    assert bot._proactive_last_global == bot._proactive_last_by_group["42"] > 0
    assert not bot._proactive_inflight


@pytest.mark.parametrize("outcome", [
    "quiet", "empty", "generate_error", "stale", "busy", "invalid", "send_error", "send_exception",
])
def test_unsent_attempt_never_counts_as_success(monkeypatch, outcome):
    bot = _ready(monkeypatch)
    logs = []
    monkeypatch.setattr(app, "log", logs.append)

    async def complete(*args, **kwargs):
        if outcome == "generate_error":
            raise RuntimeError("mock model failure")
        if outcome == "stale":
            bot.observer.ingest({"group_id": "42", "user_id": "202", "message_id": "new-topic",
                                 "message": [{"type": "text", "data": {"text": "换个话题"}}]})
        if outcome == "busy":
            queue = asyncio.Queue()
            queue.put_nowait("direct-question")
            bot.workers = {"direct": queue}
        return {"quiet": "__QUIET__", "empty": "", "invalid": "@不要发送"}.get(
            outcome, "小菲可没答应加班！"), "test/model"

    bot._complete = complete
    if outcome == "send_error":
        # A contradictory acknowledgement must not be accepted as success.
        bot.send_group_text = AsyncMock(return_value={"status": "failed", "retcode": 0})
    elif outcome == "send_exception":
        bot.send_group_text = AsyncMock(side_effect=RuntimeError("send failed"))
    assert not asyncio.run(bot._maybe_proactive_group_reply({}, "42", "你是牛马吗"))
    assert bot._proactive_daily_count == 0
    assert bot._proactive_last_global == 0
    assert bot._proactive_last_by_group == {}
    assert bot._proactive_group_counts == {}
    assert bot._proactive_hourly_counts == {}
    assert "42" in bot._proactive_last_attempt
    assert not bot._proactive_inflight
    if outcome not in {"send_error", "send_exception"}:
        bot.send_group_text.assert_not_awaited()
    if outcome == "quiet":
        assert any("status=quiet" in line for line in logs)
        assert not any("failed" in line for line in logs)


def test_attempt_backoff_expires_without_consuming_send_cooldown(monkeypatch):
    bot = _ready(monkeypatch)
    bot._complete = AsyncMock(return_value=("__QUIET__", "test/model"))

    async def run():
        assert not await bot._maybe_proactive_group_reply({}, "42", "你是牛马吗")
        assert not await bot._maybe_proactive_group_reply({}, "42", "你是牛马吗")
        assert bot._complete.await_count == 1
        bot._proactive_last_attempt["42"] -= app.PROACTIVE_ATTEMPT_BACKOFF_SECONDS + 1
        bot._complete.return_value = ("小菲可没答应加班！", "test/model")
        assert await bot._maybe_proactive_group_reply({}, "42", "你是牛马吗")
        assert bot._complete.await_count == 2

    asyncio.run(run())
    assert bot._proactive_daily_count == 1


def test_inflight_excludes_duplicate_generation_and_cancellation_releases_it(monkeypatch):
    bot = _ready(monkeypatch)

    async def run():
        entered = asyncio.Event()

        async def slow(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        bot._complete = slow
        first = asyncio.create_task(bot._maybe_proactive_group_reply({}, "42", "你是牛马吗"))
        await asyncio.wait_for(entered.wait(), 0.5)
        assert not await bot._maybe_proactive_group_reply({}, "42", "你是牛马吗")
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not bot._proactive_inflight

    asyncio.run(run())
    assert bot._proactive_daily_count == 0
    bot.send_group_text.assert_not_awaited()


def test_queued_direct_question_prevents_model_admission(monkeypatch):
    bot = _ready(monkeypatch)
    queue = asyncio.Queue()
    queue.put_nowait("direct-question")
    bot.workers = {"other": queue}
    assert not asyncio.run(bot._maybe_proactive_group_reply({}, "42", "你是牛马吗"))
    bot._complete.assert_not_awaited()
    assert not getattr(bot, "_proactive_last_attempt", {})


def test_success_uses_send_hour_instead_of_generation_hour(monkeypatch):
    bot = _ready(monkeypatch)
    now = [datetime(2026, 10, 15, 12, 59, 59)]
    bot._proactive_now = lambda: now[0]

    async def send(*args):
        now[0] = datetime(2026, 10, 15, 13)
        return {"status": "ok", "retcode": 0}

    bot.send_group_text = send
    assert asyncio.run(bot._maybe_proactive_group_reply({}, "42", "你是牛马吗"))
    assert bot._proactive_hourly_counts == {"42|2026-10-15 13": 1}
