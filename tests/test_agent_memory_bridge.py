"""The production ask_model path consumes scoped persistent references offline."""
import asyncio
import hashlib
import threading
import time
from unittest.mock import AsyncMock

import pytest

import bridge_qq as app
from qqbot_core.group_policy import GroupPolicy, FEATURES
from qqbot_core.knowledge import KnowledgeStore
from qqbot_core.observer import GroupConversationObserver
from qqbot_core.routing import classify_message
from test_routing import _bridge_with_final


@pytest.fixture
def memory_bridge(monkeypatch, tmp_path):
    from qqbot_core.agent_memory import AgentMemory

    monkeypatch.setattr(app, "AGENT_MEMORY_ENABLED", True)
    monkeypatch.setattr(app, "KNOWLEDGE_SYSTEM", True)
    monkeypatch.setattr(app, "KNOWLEDGE_FILE", str(tmp_path / "absent-legacy.sqlite3"))
    monkeypatch.setattr(app, "GROUP_STYLE_LEARNING", False)
    monkeypatch.setattr(app, "AGENT_MEMORY_RETRIEVAL_TIMEOUT", 2)
    policy = GroupPolicy(["admin"])
    policy.grant("111", "admin", FEATURES, "test-notice")
    policy.grant("222", "admin", FEATURES, "other-test-notice")
    bridge = _bridge_with_final(monkeypatch, reply="记录里约的是周六八点打王者荣耀，还没确认有没有变更。")
    bridge.group_policy = policy
    bridge.observer = GroupConversationObserver(policy)
    bridge.agent_memory = AgentMemory(tmp_path / "memory", policy)
    bridge.agent_memory.command(
        "remember group 周六晚上八点约好打王者荣耀，实际是否进行待确认。",
        "111", "admin", message_id="synthetic-message", trusted_role="admin")
    yield bridge
    executor = getattr(bridge, "_agent_memory_executor", None)
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.parametrize("query", ["上次我们讨论的游戏是什么", "前几天我们约好几点打王者"])
def test_persistent_reference_reaches_first_generation_without_search(monkeypatch, memory_bridge, query):
    bridge = memory_bridge
    bridge.sessions.value = {"messages": [
        {"role": "user", "speaker_id": "u", "content": "无关的今日比赛"},
        {"role": "assistant", "speaker_id": "u", "content": "无关比赛的旧答案"},
    ]}
    search = AsyncMock(side_effect=AssertionError("memory recall must not search the web"))
    monkeypatch.setattr(app, "search_web_async", search)
    reply = asyncio.run(bridge.ask_model("111", query, query, speaker_id="u"))
    references = [m for m in bridge.captured_messages if "王者荣耀" in m["content"]]
    assert references and all(m["role"] == "user" for m in references)
    assert "王者荣耀" not in bridge.captured_system
    assert "八点" in reply
    assert bridge.captured_kwargs["model_refs"] == app.MODELS_FINAL
    search.assert_not_awaited()
    if "前几天" in query:
        assert "无关的今日比赛" not in str(bridge.captured_messages)


@pytest.mark.parametrize("query,key", [
    ("换个话题，你觉得我聪明吗", "111"),
    ("上次我们讨论的游戏是什么", "222"),
    ("上次我们讨论的游戏是什么", "private_u"),
])
def test_new_topic_other_group_and_private_chat_do_not_receive_the_record(memory_bridge, query, key):
    bridge = memory_bridge
    asyncio.run(bridge.ask_model(key, query, query, speaker_id="u"))
    assert "周六晚上八点" not in str(bridge.captured_messages)


def test_memory_error_degrades_to_no_record_instruction(memory_bridge, monkeypatch):
    bridge = memory_bridge
    monkeypatch.setattr(bridge.agent_memory, "retrieve", lambda *a, **kw: (_ for _ in ()).throw(OSError("mock failure")))
    query = "上次我们讨论过什么"
    assert asyncio.run(bridge.ask_model("111", query, query, speaker_id="u"))
    assert "未提供可用记录" in str(bridge.captured_messages)


def test_memory_reference_requires_verbatim_specifics_and_no_real_world_claim(memory_bridge):
    """回归：检索到游戏名和时间后仍被答成模糊说法；且不得暗示机器人会真的参加现实活动。"""
    bridge = memory_bridge
    query = "上次我们讨论的游戏是什么"
    asyncio.run(bridge.ask_model("111", query, query, speaker_id="u"))
    reference = next(
        m["content"] for m in bridge.captured_messages if "王者荣耀" in m["content"]
    )
    assert "必须原样写出其中的具体信息" in reference
    assert "不要暗示你会真的参加现实活动" in reference


def test_no_record_instruction_forbids_claiming_it_never_happened(memory_bridge, monkeypatch):
    """回归：无记录时被说成"没约过"——没找到记录不能断言没发生。"""
    bridge = memory_bridge
    monkeypatch.setattr(bridge.agent_memory, "retrieve", lambda *a, **kw: (None, {}))
    query = "上次我们讨论过什么"
    asyncio.run(bridge.ask_model("111", query, query, speaker_id="u"))
    payload = str(bridge.captured_messages)
    assert "只说明没有查到相关记录" in payload
    assert "没找到记录不等于没发生" in payload


def test_disabled_memory_command_is_diagnostic_and_does_not_create_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "AGENT_MEMORY_ENABLED", False)
    monkeypatch.setattr(app, "AGENT_MEMORY_DIR", str(tmp_path / "must-not-exist"))
    bridge = _bridge_with_final(monkeypatch)
    event = {"message_type": "group", "group_id": "111", "message_id": "test-command"}
    result = asyncio.run(bridge._handle_agent_command("!memory status", "u", event))
    assert "关闭" in result
    assert not (tmp_path / "must-not-exist").exists()


def test_timeout_does_not_spawn_unbounded_memory_threads(monkeypatch):
    monkeypatch.setattr(app, "AGENT_MEMORY_RETRIEVAL_TIMEOUT", 0.02)
    bridge = object.__new__(app.Bridge)
    entered, release = threading.Event(), threading.Event()
    calls = []

    class SlowMemory:
        def retrieve(self):
            calls.append(1)
            entered.set()
            release.wait(2)
            return None, {}

    bridge._get_agent_memory = lambda: SlowMemory()

    async def run():
        assert await bridge._agent_memory_call("retrieve") is None
        assert entered.is_set()
        assert await bridge._agent_memory_call("retrieve") is None
        assert calls == [1]
        release.set()
        await asyncio.wait_for(asyncio.shield(bridge._agent_memory_future), 1)
        await bridge.close_learning()

    try:
        asyncio.run(run())
    finally:
        release.set()
        bridge._agent_memory_executor.shutdown(wait=True) if bridge._agent_memory_executor else None


def test_legacy_store_read_only_mode_cannot_mutate_original(tmp_path):
    policy = GroupPolicy(["admin"])
    policy.grant("111", "admin", FEATURES, "notice")
    path = tmp_path / "legacy.sqlite3"
    original = KnowledgeStore(path, policy)
    original.add_candidate("style", "111", "群里喜欢短句", [
        {"kind": "human", "group_id": "111", "message_id": "old", "user_id": "u"}])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    read_only = KnowledgeStore(path, policy, read_only=True)
    assert read_only.eligible("111", {"style"}, expression_candidates=True)
    with pytest.raises(Exception):
        read_only.add_candidate("style", "111", "另一条风格", [
            {"kind": "human", "group_id": "111", "message_id": "new", "user_id": "u"}])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("query", [
    "刚才我们聊了什么", "上次我们讨论的游戏是什么", "上周约过什么", "你还记得我喜欢什么",
])
def test_history_recall_is_distinct_from_external_fact_search(query):
    route = classify_message(query, always_search=True)
    assert route.intent == "memory" and route.evidence == "NEVER"
    assert not route.should_search


@pytest.mark.parametrize("query", ["现在黄金价格多少钱", "帮我搜一下上周游戏新闻", "你还记得今年世界杯冠军是谁吗"])
def test_memory_words_do_not_suppress_explicit_or_current_fact_search(query):
    assert classify_message(query).should_search


@pytest.mark.parametrize("query", ["现在黄金多少钱", "今天黄金多少钱", "实时金价是多少"])
def test_current_prices_require_fresh_evidence_not_historical_memory(query):
    route = classify_message(query)
    assert route.evidence == "REQUIRED"
    assert route.should_search


def test_private_event_cannot_be_observed_even_with_a_group_id_field():
    policy = GroupPolicy(["admin"])
    policy.grant("111", "admin", FEATURES, "notice")
    observer = GroupConversationObserver(policy)
    event = {"message_type": "private", "group_id": "111", "user_id": "u", "message_id": "private",
             "message": [{"type": "text", "data": {"text": "this must not enter the group"}}]}
    assert observer.ingest(event) is None
    assert not observer.snapshot("111")


def test_short_followup_keeps_the_previous_assistant_answer(memory_bridge):
    bridge = memory_bridge
    now = time.time()
    # 2026-10-17：群历史只保留写入时带当前知识修订号的消息
    # （来源授权无法确认的旧消息不得继承当前群授权），夹具需带上修订号。
    revision = bridge.group_policy.revision("111")
    bridge.sessions.value = {"messages": [
        {"role": "user", "speaker_id": "u", "content": "讲个冷笑话",
         "recorded_at": now, "knowledge_revision": revision},
        {"role": "assistant", "speaker_id": "u", "content": "上一句笑话的实际正文。",
         "recorded_at": now, "knowledge_revision": revision},
    ]}
    bridge.observer.ingest({"group_id": "111", "user_id": "u", "message_id": "latest",
                            "message": [{"type": "text", "data": {"text": "笑点解析"}}]})
    asyncio.run(bridge.ask_model("111", "笑点解析", "笑点解析", speaker_id="u"))
    assert "上一句笑话的实际正文" in str(bridge.captured_messages)
    assert "不解释笑点" not in bridge.captured_system
    assert "【历史追问】" in bridge.captured_system


def test_revoked_preference_cannot_return_from_raw_session_or_live_context(memory_bridge):
    bridge = memory_bridge
    now = time.time()
    bridge.sessions.value = {"messages": [
        {"role": "user", "speaker_id": "u", "content": "我喜欢秘密口味的茶", "recorded_at": now},
        {"role": "assistant", "speaker_id": "u", "content": "记住秘密口味的茶了", "recorded_at": now},
    ], "last_question": "我喜欢秘密口味的茶", "last_answer": "记住秘密口味的茶了",
        "last_speaker_id": "u", "last_reply_at": now}
    bridge.observer.ingest({"group_id": "111", "user_id": "u", "message_id": "preference",
                            "message": [{"type": "text", "data": {"text": "我喜欢秘密口味的茶"}}]})
    query = "你还记得我喜欢什么吗"
    asyncio.run(bridge.ask_model("111", query, query, speaker_id="u"))
    assert "秘密口味" not in str(bridge.captured_messages)


@pytest.mark.parametrize("success", [True, False])
def test_sent_bot_reply_enters_only_the_authorized_short_window(monkeypatch, memory_bridge, success):
    bridge = memory_bridge
    bridge.self_id = "test-bot"
    bridge.call_api = AsyncMock(return_value={
        "status": "ok" if success else "failed", "retcode": 0 if success else 1,
        "data": {"message_id": "synthetic-bot-message"}})
    asyncio.run(bridge.send_group_msg("111", "u", "上一句笑话的正文。"))
    context = bridge.observer.context("111")
    assert bool(context) is success
    if success:
        assert context[0]["role"] == "assistant"
        assert context[0]["speaker_id"] == "u"
        assert "上一句笑话" in context[0]["content"]


def test_observed_agreement_is_distilled_and_recalled_after_restart(monkeypatch, memory_bridge, tmp_path):
    from qqbot_core.agent_memory import AgentMemory

    bridge = memory_bridge
    policy_path = tmp_path / "persistent-policy.sqlite3"
    policy = GroupPolicy(["admin"], persistence_path=policy_path)
    policy.grant("111", "admin", FEATURES, "synthetic-public-notice")
    clock = [time.time() - 86400]
    bridge.group_policy = policy
    bridge.observer = GroupConversationObserver(policy, clock=lambda: clock[0])
    bridge.agent_memory = AgentMemory(tmp_path / "e2e-memory", policy, clock=lambda: clock[0])
    bridge.knowledge_store = None
    monkeypatch.setattr(bridge, "_ensure_knowledge", lambda: True)
    for i, text in enumerate(("周六一起打王者荣耀吧", "晚上八点怎么样", "可以，就这么定", "活动先这样安排")):
        bridge.observer.ingest({"message_type": "group", "group_id": "111", "user_id": f"member-{i % 2}",
                                "message_id": f"agreement-{i}", "time": clock[0] + i,
                                "message": [{"type": "text", "data": {"text": text}}]})

    async def distill():
        await bridge._distill_observed_batch(("111", policy.version("111"), bridge.observer.snapshot("111")))
        await bridge.close_learning()

    asyncio.run(distill())
    clock[0] += 86400
    # Reopen both stores: persistence must not depend on an old in-memory policy object.
    reopened_policy = GroupPolicy(["admin"], persistence_path=policy_path)
    bridge.group_policy = reopened_policy
    bridge.agent_memory = AgentMemory(tmp_path / "e2e-memory", reopened_policy, clock=lambda: clock[0])
    bridge.observer = GroupConversationObserver(reopened_policy, clock=lambda: clock[0])
    bridge.sessions.value = {"messages": []}
    query = "昨天我们约好几点打王者荣耀"
    reference, trace = bridge.agent_memory.retrieve("111", "u", query, classify_message(query))
    assert reference is not None, trace
    asyncio.run(bridge.ask_model("111", query, query, speaker_id="u"))
    references = [m["content"] for m in bridge.captured_messages if "晚上八点怎么样" in m["content"]]
    assert references and "周六一起打王者荣耀吧" in references[0], bridge.captured_messages
    assert "reported" in references[0]
    assert "member-0" not in references[0]
