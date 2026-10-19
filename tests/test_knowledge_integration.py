import asyncio
from unittest.mock import AsyncMock

import pytest
import bridge_qq
from qqbot_core.group_policy import GroupPolicy, FEATURES
from qqbot_core.knowledge import KnowledgeStore
from qqbot_core.memory import MemoryRetriever
from qqbot_core.observer import GroupConversationObserver
from test_routing import _bridge_with_final


def configured(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_qq, "KNOWLEDGE_SYSTEM", True)
    bridge = _bridge_with_final(monkeypatch, reply="小菲才不是，别想骗我加班！")
    policy = GroupPolicy(["admin"])
    policy.grant("111", "admin", FEATURES, "test-notice")
    store = KnowledgeStore(tmp_path / "integration.sqlite3", policy)
    item = store.add_candidate("style", "111", "本群偏好简短直接回复", [
        {"kind": "human", "group_id": "111", "user_id": "u", "message_id": "test1"}])
    store.transition(item, "reviewed", "admin")
    bridge.group_policy, bridge.knowledge_store = policy, store
    bridge.memory_retriever = MemoryRetriever(store, policy)
    bridge.observer = GroupConversationObserver(policy)
    return bridge, item


def ask(bridge, key="111", text="你是牛马吗"):
    return bridge.ask_model(key, text, question=text, speaker_id="u")


def test_approved_knowledge_reaches_actual_first_generation(monkeypatch, tmp_path):
    bridge, item = configured(monkeypatch, tmp_path)
    search = AsyncMock(side_effect=AssertionError("social message must not search"))
    monkeypatch.setattr(bridge_qq, "search_web_async", search)
    reply = asyncio.run(ask(bridge))
    assert reply and "联网" not in reply
    references = [m for m in bridge.captured_messages if item in m["content"]]
    assert len(references) == 1 and references[0]["role"] == "user"
    assert item not in bridge.captured_system
    assert "不可信" in references[0]["content"]
    search.assert_not_awaited()
    assert bridge.sessions.saved["messages"][-1]["knowledge_revision"] == bridge.group_policy.revision("111")


@pytest.mark.parametrize("mode", ["disabled", "revoked", "cross_group", "fault"])
def test_knowledge_unavailable_does_not_break_normal_answer(monkeypatch, tmp_path, mode):
    bridge, item = configured(monkeypatch, tmp_path)
    key = "111"
    if mode == "disabled":
        monkeypatch.setattr(bridge_qq, "KNOWLEDGE_SYSTEM", False)
    elif mode == "revoked":
        bridge.group_policy.revoke("111", "admin")
    elif mode == "cross_group":
        key = "1111"
    else:
        def fail(*args):
            raise RuntimeError("simulated retrieval failure")
        bridge.memory_retriever.retrieve = fail
    assert asyncio.run(ask(bridge, key))
    assert not any(item in m["content"] for m in bridge.captured_messages)


def test_revocation_during_generation_does_not_emit_or_save_stale_reference(monkeypatch, tmp_path):
    bridge, _ = configured(monkeypatch, tmp_path)
    async def revoke(system, messages, **kwargs):
        bridge.group_policy.revoke("111", "admin")
        return "former learned answer", "provider/model"
    bridge._complete = bridge._complete_race = revoke
    result = asyncio.run(ask(bridge))
    assert "former learned answer" not in result
    assert bridge.sessions.saved is None


@pytest.mark.parametrize("change", ["revoke", "restart"])
def test_revoked_reference_is_excluded_from_followup_history(monkeypatch, tmp_path, change):
    bridge, _ = configured(monkeypatch, tmp_path)
    revision = bridge.group_policy.revision("111")
    bridge.sessions.value = {"messages": [
        {"role": "user", "content": "【QQ群】群(111)\n用户(u): 问题", "speaker_id": "u"},
        {"role": "assistant", "content": "撤销后不应出现的历史知识", "speaker_id": "u", "knowledge_revision": revision}],
        "last_knowledge_revision": revision, "last_answer": "撤销后不应出现的历史知识"}
    if change == "revoke":
        bridge.group_policy.revoke("111", "admin")
    else:
        replacement = GroupPolicy(["admin"])
        replacement.grant("111", "admin", FEATURES, "new-process-notice")
        bridge.group_policy = replacement
        bridge.knowledge_store.policy = replacement
        bridge.memory_retriever = MemoryRetriever(bridge.knowledge_store, replacement)
        bridge.observer = GroupConversationObserver(replacement)
    asyncio.run(ask(bridge, text="刚才那个继续说"))
    assert all("撤销后不应出现的历史知识" not in m["content"] for m in bridge.captured_messages)


def test_memory_enabled_fact_bypasses_legacy_answer_cache(monkeypatch, tmp_path):
    bridge, _ = configured(monkeypatch, tmp_path)
    monkeypatch.setattr(bridge_qq, "_answer_cache_get", lambda *args: pytest.fail("memory route must bypass old cache"))
    monkeypatch.setattr(bridge_qq, "search_web_async", AsyncMock(return_value=""))
    bridge._complete_with_tools = bridge._complete
    result = asyncio.run(ask(bridge, text="牛马是什么意思"))
    assert result


def test_admin_off_uses_metadata_not_text_and_purges_window(monkeypatch, tmp_path):
    bridge, _ = configured(monkeypatch, tmp_path)
    policy = GroupPolicy(["admin"], persistence_path=tmp_path / "policy.sqlite3")
    policy.grant("111", "admin", FEATURES, "notice:test")
    bridge.group_policy = policy
    bridge.observer.policy = policy
    bridge.observer.ingest({"group_id": "111", "user_id": "u", "message_id": "1", "message": [{"type": "text", "data": {"text": "你好"}}]})
    assert bridge.observer.snapshot("111")
    assert bridge_qq.handle_admin_command("!agent off", "u", {"group_id": "111", "sender_role": "owner"}, bridge) is None
    assert bridge.group_policy.allows("111", "observe")
    reply = asyncio.run(bridge._handle_agent_command(
        "!agent off", "admin", {"message_type": "group", "group_id": "111"}))
    assert "撤销" in reply and not bridge.observer.snapshot("111")


def test_bounded_background_executor_integrates_distillation(monkeypatch, tmp_path):
    bridge, _ = configured(monkeypatch, tmp_path)
    async def run():
        for i in range(4):
            bridge.observer.ingest({"group_id": "111", "user_id": str(i % 2), "message_id": str(i), "message": [{"type": "text", "data": {"text": "游戏哈哈"}}]})
        batch = ("111", bridge.group_policy.version("111"), bridge.observer.snapshot("111"))
        await bridge._distill_observed_batch(batch)
        assert bridge._learning_io_future.done()
        bridge._learning_executor.shutdown(wait=True)
        candidates = bridge.knowledge_store.get(bridge._learning_io_future.result()[0])
        assert candidates.evidence_status == "candidate"
    asyncio.run(run())


def test_group_style_words_are_reference_data_not_system_instructions(monkeypatch, tmp_path):
    bridge, _ = configured(monkeypatch, tmp_path)
    monkeypatch.setattr(bridge_qq, "GROUP_STYLE_LEARNING", True)
    class Style:
        def get_group_profile(self, gid):
            return {}
        def reply_hint(self, gid, query="", **kwargs):
            # 2026-10-17：群风格注入改为**按相关性**（必须命中当前消息的词项），
            # 因此这里让提示内容带上当前问题，模拟"确实相关"的情形。
            return "本群专用虚拟口头禅 " + str(query)
    bridge.style_store = Style()
    asyncio.run(ask(bridge))
    # 2026-10-17：群风格模仿已移出默认运行链路——塔菲有稳定人格，
    # 不再每句都模仿群成员的表达习惯。风格资料既不入 system，也不入 messages。
    assert "本群专用虚拟口头禅" not in bridge.captured_system
    assert not any("本群专用虚拟口头禅" in str(m.get("content"))
                   for m in bridge.captured_messages)


def test_authorized_style_collection_does_not_create_second_raw_window(monkeypatch, tmp_path):
    bridge, _ = configured(monkeypatch, tmp_path)
    monkeypatch.setattr(bridge_qq, "KNOWLEDGE_SYSTEM", False)
    monkeypatch.setattr(bridge_qq, "GROUP_STYLE_LEARNING", True)
    bridge.style_store = bridge_qq.GroupStyleStore(str(tmp_path / "style.json"))
    bridge._observe_group_event({"group_id": "111", "user_id": "u", "message_id": "1", "message": [{"type": "text", "data": {"text": "哈哈哈哈"}}]})
    assert bridge.observer.snapshot("111")
    # 观察器仍然只保留一份原始窗口（不因为学习再存一份原文）
    assert not bridge.style_store._recent_texts
    # 2026-10-17：自动群风格学习已移出默认路径，因此不再累积统计。
    profile = bridge.style_store.get_group_profile(bridge._style_scope_id("111"))
    assert (profile.get("stats") or {}).get("length", {}).get("count", 0) == 0
