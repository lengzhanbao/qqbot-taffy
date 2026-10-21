"""Proactive group replies are gated, brief, and never persisted as raw chat."""
import asyncio
import pytest
from unittest.mock import AsyncMock

import bridge_qq
from datetime import datetime
from qqbot_core.group_policy import GroupPolicy, FEATURES
from qqbot_core.observer import GroupConversationObserver


def _authorize(bridge):
    bridge.group_policy = GroupPolicy(["admin"])
    bridge.group_policy.grant("42", "admin", FEATURES, "test-notice")
    bridge.observer = GroupConversationObserver(bridge.group_policy)
    bridge._proactive_now = lambda: datetime(2026, 10, 15, 12)
    for i, text in enumerate(("哈哈", "确实", "真有你的", "你是牛马吗")):
        bridge.observer.ingest({"group_id": "42", "user_id": str(101 if i % 2 else 202),
            "message_id": str(i), "message": [{"type": "text", "data": {"text": text}}]})


class _StyleStore:
    def get_group_profile(self, gid):
        return {}

    def reply_hint(self, gid, query, include_style=False):
        return "\n群内习惯偏短句" if include_style else ""


def test_proactive_reply_uses_deepseek_once_and_sends_without_at(monkeypatch):
    monkeypatch.setattr(bridge_qq, "PROACTIVE_GROUP_CHAT", True)
    monkeypatch.setattr(bridge_qq, "GROUP_STYLE_LEARNING", True)
    monkeypatch.setattr(bridge_qq, "write_health", lambda **kwargs: None)
    monkeypatch.setattr(bridge_qq, "inc_stat", lambda *args: None)
    monkeypatch.setattr(bridge_qq, "log", lambda *args: None)

    bridge = object.__new__(bridge_qq.Bridge)
    _authorize(bridge)
    bridge.style_store = _StyleStore()
    bridge.recent_group_context = {
        "42": [
            {"role": "user", "speaker_id": "101", "content": "甲: 哈哈"},
            {"role": "user", "speaker_id": "202", "content": "乙: 确实"},
            {"role": "user", "speaker_id": "101", "content": "甲: 真有你的"},
            {"role": "user", "speaker_id": "202", "content": "乙: 你是牛马吗"},
        ]
    }
    bridge._proactive_last_by_group = {}
    bridge._proactive_last_global = 0.0
    bridge._proactive_day = ""
    bridge._proactive_daily_count = 0
    calls = []
    sent = []
    delivered = []

    async def complete(system, messages, **kwargs):
        calls.append((system, messages, kwargs))
        return "111，大哥真送吗？", bridge_qq.DEEPSEEK_MODEL_REF

    async def send_group_text(gid, text):
        sent.append((gid, text))
        return {"status": "ok", "retcode": 0}

    class _VoiceDelivery:
        async def deliver(self, reply, target, send_record, send_text, log):
            delivered.append((reply, target))
            result = await send_record({"type": "record", "data": {"file": "base64:AA=="}})
            return {**result, "_reply_medium": "voice", "_delivery_state": "sent"}

    bridge._complete = complete
    bridge.send_group_text = send_group_text
    bridge.call_api = AsyncMock(return_value={
        "status": "ok", "retcode": 0, "data": {"message_id": "voice-1"}
    })
    bridge.voice_delivery = _VoiceDelivery()
    # 固定随机数：random.random() < 0.3 时走文字，> 0.3 走语音。
    # 这里固定为 0.5（语音路径），避免测试因随机性不稳定。
    import random as _random
    _orig = _random.random
    _random.random = lambda: 0.5
    event = {"message": [{"type": "text", "data": {"text": "你是牛马吗"}}]}

    first = asyncio.run(bridge._maybe_proactive_group_reply(
        event, "42", "你是牛马吗"
    ))
    second = asyncio.run(bridge._maybe_proactive_group_reply(
        event, "42", "你是牛马吗"
    ))

    assert first is True
    assert second is False
    assert len(calls) == 1
    assert calls[0][2]["model_refs"] == [bridge_qq.DEEPSEEK_MODEL_REF]
    assert calls[0][2]["max_tokens"] == bridge_qq.PROACTIVE_MAX_TOKENS
    assert sent == []
    assert len(delivered) == 1
    assert isinstance(delivered[0][0], bridge_qq.voice_mod.VoiceReply)
    bridge.call_api.assert_awaited_once()
    assert "最近内容" in calls[0][1][0]["content"]
    _random.random = _orig  # 恢复原函数


def test_proactive_reply_uses_conservative_limits_and_bounded_context():
    # 规格：每群 15 分钟、每小时最多 2 次、每天最多 8 次。
    # 目标是"在合适的时候自然插话"，而不是让塔菲说得多。
    assert bridge_qq.PROACTIVE_GROUP_COOLDOWN_SECONDS == 900
    assert bridge_qq.PROACTIVE_HOURLY_LIMIT == 2
    assert bridge_qq.PROACTIVE_DAILY_LIMIT == 8
    # 2026-10-17：2400 -> 4000。2400 是硬编码字面量（当时想改成 env 覆盖没生效），
    # 最近 10~20 条群聊常被截掉，模型看不到完整话题。
    # 注意：本用例断言的是**代码默认值**；生产 .env 会覆盖冷却与配额。
    assert bridge_qq.PROACTIVE_CONTEXT_MAX_CHARS == 4000
    # 主动发言默认关闭，必须逐群授权才生效
    assert bridge_qq.PROACTIVE_GROUP_CHAT is False


def test_proactive_reply_skips_messages_directed_at_someone(monkeypatch):
    monkeypatch.setattr(bridge_qq, "PROACTIVE_GROUP_CHAT", True)
    monkeypatch.setattr(bridge_qq, "GROUP_STYLE_LEARNING", True)
    bridge = object.__new__(bridge_qq.Bridge)
    _authorize(bridge)
    bridge.style_store = _StyleStore()
    bridge.recent_group_context = {
        "42": [
            {"role": "user", "speaker_id": "101", "content": "甲: 哈哈"},
            {"role": "user", "speaker_id": "202", "content": "乙: 确实"},
            {"role": "user", "speaker_id": "101", "content": "甲: 真有你的"},
            {"role": "user", "speaker_id": "202", "content": "乙: 你是牛马吗"},
        ]
    }
    event = {"message": [{"type": "at", "data": {"qq": "999"}}]}

    assert not asyncio.run(bridge._maybe_proactive_group_reply(
        event, "42", "你是牛马吗"
    ))


def _ready(monkeypatch):
    monkeypatch.setattr(bridge_qq, "PROACTIVE_GROUP_CHAT", True)
    monkeypatch.setattr(bridge_qq, "GROUP_STYLE_LEARNING", False)
    # 2026-10-17：防抖会让主动接话先等 2.5 秒；测试里置 0，避免拖慢和超时。
    monkeypatch.setattr(bridge_qq, "PROACTIVE_DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(bridge_qq, "write_health", lambda **kwargs: None)
    monkeypatch.setattr(bridge_qq, "inc_stat", lambda *args: None)
    bridge = object.__new__(bridge_qq.Bridge)
    _authorize(bridge)
    bridge.style_store = _StyleStore()
    bridge._proactive_last_by_group, bridge._proactive_last_global = {}, 0.0
    bridge._proactive_day, bridge._proactive_daily_count = "", 0
    bridge._complete = AsyncMock(return_value=("小菲可没答应加班！", bridge_qq.DEEPSEEK_MODEL_REF))
    bridge.send_group_text = AsyncMock(return_value={"status": "ok", "retcode": 0})

    class _TextDelivery:
        async def deliver(self, reply, target, send_record, send_text, log):
            try:
                result = await send_text(str(reply))
            except Exception:
                return {"_reply_medium": "text", "_delivery_state": "unknown"}
            state = ("sent" if isinstance(result, dict)
                     and result.get("status") == "ok" and result.get("retcode", 0) == 0
                     else "failed")
            return {**(result or {}), "_reply_medium": "text", "_delivery_state": state}

    bridge.voice_delivery = _TextDelivery()
    return bridge


def test_proactive_voice_text_fallback_records_monotonic_cooldowns(monkeypatch):
    bridge = _ready(monkeypatch)
    monkeypatch.setattr(bridge_qq, "PROACTIVE_GROUP_COOLDOWN_SECONDS", 30)
    voice_reply = bridge_qq.voice_mod.VoiceReply("塔菲也要来凑个热闹！")
    bridge._complete = AsyncMock(return_value=(voice_reply, bridge_qq.DEEPSEEK_MODEL_REF))

    class _VoiceFallback:
        async def deliver(self, reply, group_id, send_record, send_text, log):
            await send_text(str(reply))
            return {"_reply_medium": "text", "_delivery_state": "sent"}

    bridge.voice_delivery = _VoiceFallback()
    assert asyncio.run(bridge._maybe_proactive_group_reply({}, "42", "你们在聊什么"))

    sent_at = bridge._proactive_last_by_group["42"]
    assert bridge._proactive_last_global == sent_at
    assert 0 <= __import__("time").monotonic() - sent_at < 2
    assert bridge._proactive_daily_count == 1
    assert bridge._proactive_group_counts == {"42": 1}
    assert bridge._proactive_hourly_counts == {"42|2026-10-15 12": 1}
    assert asyncio.run(bridge._maybe_proactive_group_reply({}, "42", "再接一句")) is False
    assert bridge._complete.await_count == 1


# 2026-10-17：不再有"没被邀请就不调模型"这条规则判断——
# 是否开口交给模型的 __QUIET__。因此 not_invited 从"不调模型"清单里移除，
# 只保留工程边界（授权/夜间/配额/忙/安全）。
# 2026-10-18：去掉"群内不欢迎机器人或话题敏感"的关键词硬边界——
# 实测误伤很重（"让他们吵架"、"这个耗token吗"都会让接下来十几条静默）。
# 这类判断交给模型，因此 unwelcome / serious 从"不调模型"清单移除。
@pytest.mark.parametrize("mode", ["unauthorized", "night", "daily_limit", "busy"])
def test_proactive_silence_without_model_calls(monkeypatch, mode):
    bridge = _ready(monkeypatch)
    text = "你是牛马吗"
    if mode == "unauthorized":
        bridge.group_policy.revoke("42", "admin")
    elif mode == "night":
        bridge._proactive_now = lambda: datetime(2026, 10, 15, 23)
    elif mode == "daily_limit":
        bridge._proactive_day = "2026-10-15"
        bridge._proactive_group_counts = {"42": bridge_qq.PROACTIVE_DAILY_LIMIT}
    elif mode == "busy":
        bridge._direct_groups = {"another-group"}
    assert not asyncio.run(bridge._maybe_proactive_group_reply({}, "42", text))
    bridge._complete.assert_not_awaited()
    bridge.send_group_text.assert_not_awaited()


@pytest.mark.parametrize("change", ["revoke", "topic", "busy", "quiet", "error"])
def test_proactive_rechecks_state_before_send(monkeypatch, change):
    # 2026-10-17：主动发言不再限制字数（"too_long" 用例已移除，
    # 长回复现在应正常发出，不再被 45 字上限丢弃）。
    bridge = _ready(monkeypatch)
    async def complete(*args, **kwargs):
        if change == "revoke":
            bridge.group_policy.revoke("42", "admin")
        elif change == "topic":
            bridge.observer.ingest({"group_id": "42", "user_id": "202", "message_id": "new", "message": [{"type": "text", "data": {"text": "换个话题"}}]})
        elif change == "busy":
            queue = asyncio.Queue()
            queue.put_nowait("direct-question")
            bridge.workers = {"direct": queue}
        elif change == "error":
            raise RuntimeError("model unavailable")
        reply = "__QUIET__" if change == "quiet" else "字" * 46 if change == "too_long" else "小菲没答应加班！"
        return reply, bridge_qq.DEEPSEEK_MODEL_REF
    bridge._complete = complete
    assert not asyncio.run(bridge._maybe_proactive_group_reply({}, "42", "你是牛马吗"))
    bridge.send_group_text.assert_not_awaited()


def test_proactive_is_independent_of_learning_and_directed_reply_preempts(monkeypatch):
    bridge = _ready(monkeypatch)
    bridge.self_id = "bot"
    async def run():
        entered = asyncio.Event()
        async def slow(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        bridge._complete = slow
        bridge._schedule_proactive({}, "42", "你是牛马吗")
        await asyncio.wait_for(entered.wait(), .5)
        task = bridge._proactive_tasks["42"]
        bridge._process_group_impl = AsyncMock()
        await asyncio.wait_for(bridge._process_group({"group_id": "42", "message": [{"type": "at", "data": {"qq": "bot"}}]}), .5)
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
        bridge._process_group_impl.assert_awaited_once()
        bridge.send_group_text.assert_not_awaited()
    asyncio.run(run())


def test_plain_chatter_is_left_to_the_model(monkeypatch):
    """普通闲聊不再被规则拦下，而是交给模型判断（它可以选择 __QUIET__）。"""
    bridge = _ready(monkeypatch)
    bridge._complete = AsyncMock(return_value=("__QUIET__", bridge_qq.DEEPSEEK_MODEL_REF))
    assert not asyncio.run(bridge._maybe_proactive_group_reply({}, "42", "今天天气不错"))
    bridge._complete.assert_awaited()
    bridge.send_group_text.assert_not_awaited()


@pytest.mark.parametrize("marker", ["别插话", "有人住院了", "让他们吵架", "这个耗token吗"])
def test_sensitive_words_are_left_to_the_model(monkeypatch, marker):
    """这些词不再触发关键词硬边界，而是交给模型判断（它可以选 __QUIET__）。"""
    bridge = _ready(monkeypatch)
    bridge.observer.ingest({"group_id": "42", "user_id": "202", "message_id": "m1",
                            "message": [{"type": "text", "data": {"text": marker}}]})
    bridge._complete = AsyncMock(return_value=("__QUIET__", bridge_qq.DEEPSEEK_MODEL_REF))
    assert not asyncio.run(bridge._maybe_proactive_group_reply({}, "42", marker))
    bridge._complete.assert_awaited()
    bridge.send_group_text.assert_not_awaited()
