"""Offline end-to-end check: 30 chat messages -> candidates -> scoped retrieval."""
import asyncio
from unittest.mock import AsyncMock

import bridge_qq
from qqbot_core.group_policy import GroupPolicy, FEATURES
from qqbot_core.knowledge import KnowledgeStore
from qqbot_core.learning import GroupKnowledgeLearner
from qqbot_core.memory import MemoryRetriever
from qqbot_core.observer import GroupConversationObserver
from qqbot_core.routing import classify_message
from test_group_policy_observer import event
from test_routing import _bridge_with_final


def test_thirty_message_learning_and_memory_toggle_are_group_scoped(
        monkeypatch, tmp_path):
    now = [1000.0]
    policy = GroupPolicy(["admin"])
    for gid in ("111", "222"):
        policy.grant(gid, "admin", FEATURES, "simulation-notice-" + gid)
    observer = GroupConversationObserver(policy, clock=lambda: now[0])
    store = KnowledgeStore(tmp_path / "acceptance.sqlite3", policy,
                           clock=lambda: now[0])
    for i in range(30):
        text = ("扣1送地狱火，游戏里真给吗" if i % 2 == 0
                else "111大哥真送吗，游戏里我先扣了")
        observer.ingest(event(
            str(i), text, gid="111", uid="u" + str(i % 3),
            timestamp=now[0] - 30 + i,
        ))
    observer.ingest(event(
        "candidate-fact", "事实候选：世界杯：阿根廷已经夺冠",
        gid="111", uid="u1", timestamp=now[0],
    ))

    learned_ids = GroupKnowledgeLearner(store, policy).distill(observer.snapshot("111"))
    learned = [store.get(item_id) for item_id in learned_ids]
    assert learned and all(item.evidence_status == "candidate" for item in learned)
    assert any(item.category == "meme" and "不当作真实赠送承诺" in item.content
               for item in learned)
    assert any(item.category == "external_fact" for item in learned)

    retriever = MemoryRetriever(store, policy)
    social_route = classify_message("扣1送地狱火")
    meme_rows = retriever.retrieve(
        "111", "u1", "扣1送地狱火", social_route, lightweight=True)
    assert any(item.category == "meme" for item in meme_rows)
    factual_rows = retriever.retrieve(
        "111", "u1", "世界杯冠军是谁", classify_message("世界杯冠军是谁"),
        lightweight=True,
    )
    assert all(item.category != "external_fact" for item in factual_rows)

    bridge = _bridge_with_final(monkeypatch, reply="111大哥真送吗？")
    bridge.group_policy = policy
    bridge.observer = observer
    bridge.knowledge_store = store
    bridge.memory_retriever = retriever
    monkeypatch.setattr(bridge_qq, "TAFFY_LIGHT_MODE", True)
    monkeypatch.setattr(bridge_qq, "GROUP_STYLE_LEARNING", False)
    monkeypatch.setattr(bridge_qq, "search_web_async", AsyncMock(
        side_effect=AssertionError("social meme must not search")))

    async def ask(memory_enabled, group_id="111"):
        monkeypatch.setattr(bridge_qq, "KNOWLEDGE_SYSTEM", memory_enabled)
        reply = await bridge.ask_model(
            group_id, "扣1送地狱火", "扣1送地狱火", "群友", speaker_id="u1")
        return reply, bridge.captured_messages

    reply_off, messages_off = asyncio.run(ask(False))
    reply_on, messages_on = asyncio.run(ask(True))
    text_off = "\n".join(str(item.get("content", "")) for item in messages_off)
    text_on = "\n".join(str(item.get("content", "")) for item in messages_on)
    assert reply_off and reply_on
    assert "扣1送皮肤式接梗" not in text_off
    assert "扣1送皮肤式接梗" in text_on

    _, other_group_messages = asyncio.run(ask(True, "222"))
    other_group_text = "\n".join(
        str(item.get("content", "")) for item in other_group_messages)
    assert "扣1送皮肤式接梗" not in other_group_text
