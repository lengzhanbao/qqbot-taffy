import pytest

import bridge_qq
from qqbot_core.group_policy import GroupPolicy, FEATURES
from qqbot_core.knowledge import KnowledgeStore


def source(group="77", user="member", message="m1"):
    return {"kind": "human", "group_id": group, "user_id": user, "message_id": message}


def test_authorization_and_knowledge_survive_process_recreation(tmp_path):
    policy_path = tmp_path / "group_policy.sqlite3"
    knowledge_path = tmp_path / "knowledge.sqlite3"
    first = GroupPolicy(["admin"], persistence_path=policy_path)
    assert not policy_path.exists()
    grant = first.grant("77", "admin", FEATURES - {"proactive_chat"}, "notice:123")
    assert first.grant("77", "admin", FEATURES - {"proactive_chat"}, "notice:123").version == grant.version
    store = KnowledgeStore(knowledge_path, first)
    item_id = store.add_candidate("style", "77", "群里习惯简短接梗", [source()])
    store.transition(item_id, "reviewed", "admin")

    restarted = GroupPolicy(["admin"], persistence_path=policy_path)
    reopened_store = KnowledgeStore(knowledge_path, restarted)
    assert restarted.persistence_ok
    assert restarted.version("77") == grant.version
    assert restarted.allows("77", "knowledge_retrieval")
    assert [item.id for item in reopened_store.eligible("77", {"style"})] == [item_id]


def test_durable_revocation_and_regrant_do_not_reactivate_old_knowledge(tmp_path):
    policy_path = tmp_path / "group_policy.sqlite3"
    knowledge_path = tmp_path / "knowledge.sqlite3"
    policy = GroupPolicy(["admin"], persistence_path=policy_path)
    old_grant = policy.grant("77", "admin", FEATURES, "notice:old")
    store = KnowledgeStore(knowledge_path, policy)
    item = store.add_candidate("style", "77", "旧授权下的群风格", [source()])
    store.transition(item, "reviewed", "admin")

    restarted = GroupPolicy(["admin"], persistence_path=policy_path)
    restarted.revoke("77", "admin")
    revoked = GroupPolicy(["admin"], persistence_path=policy_path)
    assert not revoked.allows("77", "observe")
    assert revoked.version("77") == old_grant.version + 1

    new_grant = revoked.grant("77", "admin", FEATURES, "notice:new")
    reopened = GroupPolicy(["admin"], persistence_path=policy_path)
    assert new_grant.version == old_grant.version + 2
    assert reopened.allows("77", "knowledge_retrieval")
    assert KnowledgeStore(knowledge_path, reopened).eligible("77", {"style"}) == []


def test_persistent_consent_survives_restart_and_revoke(tmp_path):
    path = tmp_path / "group_policy.sqlite3"
    policy = GroupPolicy(["admin"], persistence_path=path)
    policy.grant("77", "admin", FEATURES, "notice:123")
    policy.consent("77", "member", "member", "personal_preference", "consent:9")

    restarted = GroupPolicy(["admin"], persistence_path=path)
    assert restarted.has_consent("77", "member", "personal_preference", "consent:9")
    restarted.revoke("77", "admin")
    assert not GroupPolicy(["admin"], persistence_path=path).has_consent(
        "77", "member", "personal_preference", "consent:9")


def test_corrupt_policy_store_fails_closed(tmp_path):
    path = tmp_path / "group_policy.sqlite3"
    path.write_text("not a sqlite database", encoding="utf-8")
    policy = GroupPolicy(["admin"], persistence_path=path)
    assert not policy.persistence_ok
    assert not policy.allows("77", "observe")
    with pytest.raises(RuntimeError):
        policy.grant("77", "admin", {"observe"}, "notice")


def test_regrant_uses_a_new_style_profile_namespace(tmp_path):
    policy = GroupPolicy(["admin"], persistence_path=tmp_path / "policy.sqlite3")
    bridge = object.__new__(bridge_qq.Bridge)
    bridge.group_policy = policy
    store = bridge_qq.GroupStyleStore(str(tmp_path / "profiles.json"))
    store.learn("77", "旧授权的群聊哈哈哈哈", aggregate_only=True)
    store.save()

    policy.grant("77", "admin", {"style_learning"}, "notice:one")
    first_scope = bridge._style_scope_id("77")
    assert first_scope != "77"
    assert store.get_group_profile(first_scope) == {}
    store.learn(first_scope, "哈哈哈哈", aggregate_only=True)

    policy.revoke("77", "admin")
    policy.grant("77", "admin", {"style_learning"}, "notice:two")
    second_scope = bridge._style_scope_id("77")
    assert second_scope != first_scope
    assert store.get_group_profile(second_scope) == {}
    assert store.get_group_profile("77")
    assert store.get_group_profile(first_scope)


def test_legacy_sync_agent_command_cannot_mutate_policy(tmp_path):
    policy_path = tmp_path / "group_policy.sqlite3"
    bridge = object.__new__(bridge_qq.Bridge)
    bridge.group_policy = GroupPolicy(["configured-admin"], persistence_path=policy_path)
    bridge._cancel_proactive = lambda gid: None
    bridge.observer = None
    context = {"message_type": "group", "group_id": "77", "sender_role": "owner"}

    assert bridge_qq.handle_admin_command("!agent on notice:123", "owner", context, bridge) is None
    assert bridge_qq.handle_admin_command("!agent off", "owner", context, bridge) is None
    assert not bridge.group_policy.allows("77", "knowledge_learning")
    assert not policy_path.exists()
