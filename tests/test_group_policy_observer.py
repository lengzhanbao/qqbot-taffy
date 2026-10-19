import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
import bridge_qq
from qqbot_core.group_policy import GroupPolicy, FEATURES
from qqbot_core.observer import GroupConversationObserver


def event(mid="1", text="你好", gid="111", uid="u", timestamp=1000, extra=()):
    return {"group_id": gid, "user_id": uid, "message_id": mid, "time": timestamp,
            "message": [{"type": "text", "data": {"text": text}}, *extra]}


def authorized():
    policy = GroupPolicy(["admin"])
    policy.grant("111", "admin", FEATURES, "notice:test:1")
    return policy


def test_policy_default_deny_and_trusted_identity():
    p = GroupPolicy(["admin"])
    assert not any(p.allows("111", f) for f in FEATURES)
    with pytest.raises(PermissionError):
        p.grant("111", "I am admin", FEATURES, "notice")
    with pytest.raises(ValueError):
        p.grant("111", "admin", FEATURES, "")
    initial_version = p.version("111")
    p.grant("111", "admin", {"observe"}, "notice")
    assert p.allows("111", "observe") and not p.allows("1111", "observe")
    assert not p.allows("111", "proactive_chat")
    p.revoke("111", "admin")
    assert p.version("111") == initial_version + 2 and not p.allows("111", "observe")


def test_policy_epoch_changes_across_process_recreation():
    first = authorized()
    first_version, first_revision = first.version("111"), first.revision("111")
    second = authorized()
    assert second.version("111") != first_version
    assert second.revision("111") != first_revision


def test_member_consent_cannot_be_granted_by_group_admin():
    p = authorized()
    with pytest.raises(PermissionError):
        p.consent("111", "member", "admin", "personal_preference", "ref")
    p.consent("111", "member", "member", "personal_preference", "ref")
    assert p.has_consent("111", "member", "personal_preference", "ref")
    p.revoke("111", "admin")
    assert not p.has_consent("111", "member", "personal_preference", "ref")


def test_observer_dedup_order_quote_topic_and_scope():
    p = authorized()
    o = GroupConversationObserver(p, clock=lambda: 1000)
    first = o.ingest(event("1", "讨论游戏", timestamp=980))
    assert o.ingest(event("1")) is None
    switched = o.ingest(event("3", "换个话题，音乐", timestamp=995))
    late = o.ingest(event("2", "继续游戏", timestamp=990))
    assert late.topic_id == first.topic_id < switched.topic_id
    quote = o.ingest(event("4", "接刚才", extra=({"type": "reply", "data": {"id": "1"}},
                       {"type": "at", "data": {"qq": "other"}})))
    assert quote.topic_id == first.topic_id
    assert quote.reply_to == "1" and quote.at_targets == ("other",)
    assert [m.message_id for m in o.snapshot("111")] == ["1", "2", "3", "4"]
    assert not o.snapshot("1111") and o.ingest(event(gid="1111")) is None


def test_observer_ttl_revocation_and_capacity():
    now = [1000]
    p = authorized()
    o = GroupConversationObserver(p, clock=lambda: now[0], max_messages=2, max_groups=1, max_bytes=2000)
    for i in range(4):
        o.ingest(event(str(i), timestamp=990+i))
    assert len(o.snapshot("111")) == 2 and o.byte_size <= 2000
    now[0] = 3000
    assert not o.snapshot("111")
    assert o.ingest(event(timestamp=500)) is None
    o.ingest(event(timestamp=3000))
    p.revoke("111", "admin")
    assert not o.snapshot("111")
    p.grant("111", "admin", FEATURES, "new notice")
    assert not o.snapshot("111")


def test_observer_group_and_total_byte_eviction():
    p = authorized()
    p.grant("222", "admin", FEATURES, "notice2")
    o = GroupConversationObserver(p, clock=lambda: 1000, max_groups=1, max_bytes=600)
    o.ingest(event())
    o.ingest(event(gid="222", timestamp=1000))
    assert not o.snapshot("111") and len(o.snapshot("222")) == 1
    o.ingest(event("2", "字" * 1200, gid="222"))
    assert o.byte_size <= 600


@pytest.mark.parametrize("at_bot", [False, True])
def test_safety_refusal_only_for_directed_messages(monkeypatch, at_bot):
    b = object.__new__(bridge_qq.Bridge)
    b.self_id, b.style_store = "bot", MagicMock()
    b.group_policy = GroupPolicy(["admin"])
    b.observer = GroupConversationObserver(b.group_policy)
    b.send_group_msg, b.ask_model = AsyncMock(), AsyncMock()
    monkeypatch.setattr(bridge_qq, "TRIGGER", "at")
    monkeypatch.setattr(bridge_qq, "GROUP_STYLE_LEARNING", True)
    monkeypatch.setattr(bridge_qq, "PROACTIVE_GROUP_CHAT", True)
    ev = event(text="我想自杀", extra=({"type": "at", "data": {"qq": "bot"}},) if at_bot else ())
    asyncio.run(b._process_group(ev))
    assert b.send_group_msg.await_count == int(at_bot)
    b.ask_model.assert_not_awaited()
    b.style_store.learn.assert_not_called()
    assert not b.observer.snapshot("111")


def test_exact_legacy_scope_prevents_prefix_leak():
    text, _ = bridge_qq.retrieve_knowledge_scored("秘密苹果", "## 群 1111\n秘密苹果\n## 群 111\n普通香蕉", scope="111")
    assert "秘密苹果" not in text
