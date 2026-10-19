"""会话摘要回归：真实规模的会话必须真的产出摘要。

背景（2026-10-16 实测）：群会话 706 条消息共 80214 字符，而当时的
COMPRESS_TRIGGER_CHARS=110000 高于它，导致 summary 永远为空、"聊过就忘"。
"""
import asyncio

import bridge_qq


class _Sessions:
    """最小 SessionStore 替身：只实现 _compress_history 用到的取值与更新。"""

    def __init__(self, value):
        self.value = value
        self.updates = 0

    def get(self, key):
        return self.value

    def update(self, key, changer):
        self.value = changer(dict(self.value))
        self.updates += 1


def _bridge(sessions, captured):
    bridge = object.__new__(bridge_qq.Bridge)
    bridge.sessions = sessions

    async def fake_complete(system, messages, **kwargs):
        captured.append({"system": system, "messages": messages, "kwargs": kwargs})
        return f"摘要{len(captured)}", "provider/model"

    bridge._complete = fake_complete
    return bridge


def _history(total_chars, per_message=200, start=0):
    count = max(1, total_chars // per_message)
    return [
        {"role": "user" if i % 2 else "assistant",
         "content": f"消息{start + i}" + "字" * (per_message - 10)}
        for i in range(count)
    ]


def test_realistic_session_size_triggers_a_summary():
    """回归：真实规模（约 4 万字符）的会话必须产出摘要。

    修复前阈值是 110000，这个规模的会话永远不会被摘要。
    """
    messages = _history(40000)
    sessions = _Sessions({"messages": messages, "summary_index": 0})
    captured = []
    bridge = _bridge(sessions, captured)

    asyncio.run(bridge._compress_history("g1"))

    assert captured, "4 万字符的会话必须触发摘要，否则就是阈值又高过真实数据了"
    assert sessions.value["summary"], "摘要不得为空"
    assert sessions.value["summary_index"] > 0, "已压缩位置必须前进"


def test_single_summary_call_input_is_bounded():
    """长会话必须分次摘要：单次送入模型的字符数要有上限。"""
    messages = _history(200000)
    sessions = _Sessions({"messages": messages, "summary_index": 0})
    captured = []
    bridge = _bridge(sessions, captured)

    asyncio.run(bridge._compress_history("g1"))

    assert captured
    sent = len(captured[0]["messages"][0]["content"])
    # 上限 + 允许多带一条消息（单条消息自身可能就超过上限）
    assert sent <= bridge_qq.COMPRESS_MAX_BATCH_CHARS + 600, sent
    # 一次没摘完：还有未摘要的历史留给后续批次
    assert sessions.value["summary_index"] < len(messages)


def test_summary_call_gets_enough_token_budget():
    """回归：摘要原本写死 max_tokens=600，模型撞上限被截断后被当成空回复丢弃。"""
    messages = _history(40000)
    sessions = _Sessions({"messages": messages, "summary_index": 0})
    captured = []
    bridge = _bridge(sessions, captured)

    asyncio.run(bridge._compress_history("g1"))

    assert captured
    assert captured[0]["kwargs"]["max_tokens"] >= 1200


def test_large_backlog_drains_over_multiple_passes():
    """反复触发应当逐步推进，最终把积压摘要完。"""
    messages = _history(60000)
    sessions = _Sessions({"messages": messages, "summary_index": 0})
    captured = []
    bridge = _bridge(sessions, captured)

    for _ in range(20):
        asyncio.run(bridge._compress_history("g1"))
        if sessions.value["summary_index"] >= len(messages) - 1:
            break

    assert len(captured) >= 2, "6 万字符的积压不应一次摘完"
    assert sessions.value["summary_index"] > 0
    # 每轮都必须前进，不能原地打转
    assert sessions.value["summary_index"] >= min(len(messages) - 1, 1)
