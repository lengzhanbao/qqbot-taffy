import asyncio
from datetime import datetime, timezone, timedelta
import time

import pytest

import bridge_qq
from qqbot_core.routing import classify_message
from qqbot_core.text import (
    _clean_reply,
    _sanitize_reply,
    _truncate_at_sentence,
)


def test_current_date_and_relative_year_use_beijing_time(monkeypatch):
    now = datetime(2026, 10, 16, 0, 30, tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr(bridge_qq, "_beijing_now", lambda: now)

    assert bridge_qq._current_date_text() == "北京时间 2026年10月16日 00:30，星期五"
    assert bridge_qq._build_search_query("今年世界杯冠军是谁") == "2026世界杯 冠军"


@pytest.mark.parametrize("query", [
    "2026世界杯冠军是谁",
    "2025年世俱杯冠军是谁",
    "DeepSeek最近发布了什么模型",
    "Apple最近发布了什么iPhone",
    "OpenAI现任CEO是谁",
])
def test_ended_events_releases_and_current_people_require_external_evidence(query):
    route = classify_message(query)
    assert route.evidence in {"AUTO", "REQUIRED"}
    assert route.should_search


def test_age_fact_uses_beijing_datetime_fields(monkeypatch):
    monkeypatch.setattr(
        bridge_qq,
        "_beijing_now",
        lambda: datetime(2026, 10, 16, tzinfo=timezone(timedelta(hours=8))),
    )
    raw = "【人物资料】出生于2000年10月17日。"
    result = bridge_qq._append_age_fact(raw, "这个人几岁")
    assert "按今天2026年10月16日计算，今年25岁" in result


def test_world_cup_relevance_requires_matching_event_not_just_a_date():
    query = "2026年世界杯冠军是谁"
    official = (
        "【FIFA官方 | fifa.com】2026年世界杯决赛西班牙在加时赛1-0击败阿根廷，赢得冠军。"
    )
    unrelated = "【玄武神话】2026年玄武神话传说与古代神兽。"

    assert "fifa.com" in bridge_qq._distill_search_results(official, query)
    assert "玄武" not in bridge_qq._distill_search_results(unrelated, query)


def test_output_budgets_are_separate_by_task_type():
    social = bridge_qq._reply_output_profile(
        "扣1送地狱火", classify_message("扣1送地狱火"), social_reply=True)
    fact = bridge_qq._reply_output_profile(
        "2026世界杯冠军是谁", classify_message("2026世界杯冠军是谁"))
    code = bridge_qq._reply_output_profile(
        "Python装饰器是什么", classify_message("Python装饰器是什么"))
    story = bridge_qq._reply_output_profile(
        "帮我写一个短故事", classify_message("帮我写一个短故事"))
    guide = bridge_qq._reply_output_profile(
        "这个游戏怎么打", classify_message("这个游戏怎么打"))

    # 2026-10-17：不再按类别压 token 上限，也不再让截断变成丢弃
    # （原 social 只有 1024 token 且 continuation=0）。
    assert social["category"] == "default"
    assert social["max_tokens"] == bridge_qq.DEFAULT_MAX_TOKENS
    assert social["continuation_tokens"] > 0
    assert social["max_chars"] == bridge_qq.MAX_REPLY_LEN
    assert all(p["category"] == "default" for p in (fact, code, story, guide))
    assert all(profile["max_tokens"] == bridge_qq.DEFAULT_MAX_TOKENS
               for profile in (social, fact, code, story, guide))
    assert all(profile["continuation_tokens"] > 0
               for profile in (social, fact, code, story, guide))
    assert all(profile["max_chars"] <= bridge_qq.MAX_REPLY_LEN
               for profile in (social, fact, code, story, guide))


def test_truncation_never_exceeds_hard_limit_and_prefers_sentence_boundary():
    assert _truncate_at_sentence("第一句。第二句还没结束", 8) == "第一句。"
    clipped = _truncate_at_sentence("abcdefghijklmnop", 8)
    assert len(clipped) <= 8
    assert clipped.endswith("…")
    assert _truncate_at_sentence("正文", 0) == ""


def test_clean_and_sanitize_lengths_are_observable_and_bounded():
    raw = "<think>internal note</think> **答案很短。**"
    cleaned = _clean_reply(raw)
    sanitized = _sanitize_reply(cleaned)
    assert len(cleaned) < len(raw)
    assert sanitized == "答案很短。"
    assert len(sanitized) < len(cleaned)


def test_truncation_continuation_is_single_bounded_request(monkeypatch):
    bridge = object.__new__(bridge_qq.Bridge)
    bridge.model_semaphore = asyncio.Semaphore(3)
    calls = []

    async def fake_api(body, url, key, deadline=None):
        calls.append(body)
        if len(calls) == 1:
            return "第一段尚未结束", "ok", [], "length", []
        return "接着写完并结束。", "ok", [], "stop", []

    bridge._run_api_call = fake_api
    monkeypatch.setattr(bridge_qq, "DEEPSEEK_MODEL_REF", "deepseek/test")
    monkeypatch.setattr(bridge_qq, "MODELS", ["deepseek/test"])
    monkeypatch.setattr(bridge_qq, "PROVIDERS", {
        "deepseek": {"url": "https://example.invalid", "key": "test"}
    })
    monkeypatch.setattr(bridge_qq, "_provider_blocked", lambda _: False)

    reply, model = asyncio.run(bridge._complete(
        "system", [{"role": "user", "content": "写一段说明"}],
        model_refs=["deepseek/test"], deadline=time.monotonic() + 10,
        continuation_tokens=768,
    ))

    assert model == "deepseek/test"
    assert reply == "第一段尚未结束接着写完并结束。"
    assert len(calls) == 2
    assert calls[1]["max_tokens"] == 768
    assert calls[1]["messages"][-1]["role"] == "user"


def test_second_truncated_segment_is_not_recursively_retried(monkeypatch):
    bridge = object.__new__(bridge_qq.Bridge)
    bridge.model_semaphore = asyncio.Semaphore(3)
    calls = []

    async def fake_api(body, url, key, deadline=None):
        calls.append(body)
        if len(calls) == 1:
            return "第一段未完", "ok", [], "length", []
        return "第二段仍未完", "ok", [], "length", []

    bridge._run_api_call = fake_api
    monkeypatch.setattr(bridge_qq, "DEEPSEEK_MODEL_REF", "deepseek/test")
    monkeypatch.setattr(bridge_qq, "MODELS", ["deepseek/test"])
    monkeypatch.setattr(bridge_qq, "PROVIDERS", {
        "deepseek": {"url": "https://example.invalid", "key": "test"}
    })
    monkeypatch.setattr(bridge_qq, "_provider_blocked", lambda _: False)

    reply, _ = asyncio.run(bridge._complete(
        "system", [{"role": "user", "content": "长任务"}],
        model_refs=["deepseek/test"], deadline=time.monotonic() + 10,
        continuation_tokens=512,
    ))

    assert reply == ""
    assert len(calls) == 2
