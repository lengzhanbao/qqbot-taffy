import asyncio
import urllib.parse
from unittest.mock import AsyncMock

import bridge_qq
from qqbot_core.group_policy import FEATURES, GroupPolicy


NOTICE = {
    "notice_id": "notice-42",
    "message": {
        "text": "本群群聊消息将用于短期话题上下文和群风格、群梗学习；个人偏好仅在本人同意后学习；可发送 !agent off 撤回并停止观察。"
    },
}


def command_bridge(tmp_path, admins=("configured-admin",)):
    bridge = object.__new__(bridge_qq.Bridge)
    bridge.group_policy = GroupPolicy(
        admins, persistence_path=tmp_path / "group_policy.sqlite3"
    )
    bridge._cancel_proactive = lambda _gid: None
    bridge.observer = None
    bridge.call_api = AsyncMock(return_value={"status": "ok", "data": [NOTICE]})
    return bridge


def event(**overrides):
    return {
        "message_type": "group",
        "group_id": "77",
        "message_id": "500",
        **overrides,
    }


def run_command(bridge, text, uid="configured-admin", ctx=None):
    return asyncio.run(bridge._handle_agent_command(text, uid, ctx or event()))


def test_admin_grant_requires_notice_from_same_group_and_disclosure(tmp_path):
    bridge = command_bridge(tmp_path)
    reply = run_command(bridge, "!agent on https://example.invalid/notice?notice_id=notice-42")

    assert "已授权" in reply
    assert bridge.group_policy.allows("77", "knowledge_learning")
    assert not bridge.group_policy.allows("77", "proactive_chat")
    assert bridge.group_policy._grants["77"].notice_ref == "notice:notice-42"
    bridge.call_api.assert_awaited_once_with("_get_group_notice", {"group_id": "77"}, retries=1)


def test_unknown_notice_id_does_not_change_existing_policy(tmp_path):
    bridge = command_bridge(tmp_path)
    bridge.group_policy.grant("77", "configured-admin", {"observe"}, "notice:old")
    reply = run_command(bridge, "!agent on another-id")

    assert "不在当前群" in reply
    assert bridge.group_policy._grants["77"].notice_ref == "notice:old"
    assert bridge.group_policy._grants["77"].features == frozenset({"observe"})


def test_notice_without_required_disclosures_is_rejected(tmp_path):
    bridge = command_bridge(tmp_path)
    bridge.call_api = AsyncMock(return_value={
        "status": "ok",
        "data": [{"notice_id": "notice-42", "message": {"text": "欢迎加入本群！"}}],
    })
    reply = run_command(bridge, "!agent on notice-42")

    assert "公告内容缺少" in reply
    assert not bridge.group_policy.allows("77", "observe")


def test_url_encoded_notice_text_is_checked_after_decoding(tmp_path):
    bridge = command_bridge(tmp_path)
    bridge.call_api = AsyncMock(return_value={
        "status": "ok",
        "data": [{
            "notice_id": "notice-42",
            "message": {"text": urllib.parse.quote(NOTICE["message"]["text"])},
        }],
    })
    reply = run_command(bridge, "!agent on notice-42")

    assert "已授权" in reply


def test_dynamic_admin_role_must_be_fresh_and_event_role_is_ignored(tmp_path):
    bridge = command_bridge(tmp_path, admins=())
    bridge.call_api = AsyncMock(side_effect=[
        {"status": "ok", "data": {"role": "member"}},
        {"status": "ok", "data": {"role": "admin"}},
        {"status": "ok", "data": [NOTICE]},
    ])

    denied = run_command(bridge, "!agent on notice-42", "u1", event(sender_role="owner"))
    assert "无法验证" in denied
    assert bridge.call_api.await_count == 1
    assert not bridge.group_policy.allows("77", "observe")

    bridge.call_api.reset_mock()
    bridge.call_api.side_effect = [
        {"status": "ok", "data": {"role": "admin"}},
        {"status": "ok", "data": [NOTICE]},
    ]
    granted = run_command(bridge, "!agent on notice-42", "u1", event(sender_role="member"))
    assert "已授权" in granted
    assert bridge.call_api.await_args_list[0].args == (
        "get_group_member_info",
        {"group_id": "77", "user_id": "u1", "no_cache": True},
    )


def test_api_failure_fails_closed_without_mutation(tmp_path):
    bridge = command_bridge(tmp_path)
    bridge.call_api = AsyncMock(return_value={"status": "failed", "retcode": 1})
    reply = run_command(bridge, "!agent on notice-42")

    assert "NapCat" in reply
    assert not bridge.group_policy.allows("77", "observe")


def test_member_personal_consent_is_self_only_persistent_and_revocable(tmp_path):
    bridge = command_bridge(tmp_path)
    bridge.group_policy.grant("77", "configured-admin", FEATURES - {"proactive_chat"}, "notice:42")

    reply = run_command(bridge, "!agent consent on", "member", event(message_id="consent-1"))
    assert "已记录你本人同意" in reply
    assert bridge.group_policy.has_consent(
        "77", "member", "personal_preference", "consent:consent-1"
    )
    assert not bridge.group_policy.consent_reference("77", "other", "personal_preference")
    assert "尚未同意" in run_command(bridge, "!agent consent status", "other")

    assert "已撤回" in run_command(bridge, "!agent consent off", "member")
    assert not bridge.group_policy.consent_reference("77", "member", "personal_preference")
    reloaded = GroupPolicy(["configured-admin"], persistence_path=tmp_path / "group_policy.sqlite3")
    assert not reloaded.consent_reference("77", "member", "personal_preference")


def test_admin_cannot_record_consent_for_another_member(tmp_path):
    bridge = command_bridge(tmp_path)
    bridge.group_policy.grant("77", "configured-admin", FEATURES, "notice:42")

    reply = run_command(bridge, "!agent consent on member", "configured-admin")
    assert "用法" in reply
    assert not bridge.group_policy.consent_reference("77", "member", "personal_preference")


def test_consent_requires_group_grant_durable_storage_and_group_command(tmp_path):
    no_grant = command_bridge(tmp_path)
    assert "未获准" in run_command(no_grant, "!agent consent on", "member")

    volatile = object.__new__(bridge_qq.Bridge)
    volatile.group_policy = GroupPolicy()
    assert "未持久化" in run_command(volatile, "!agent consent on", "member")

    private = command_bridge(tmp_path)
    assert "只能在目标群" in run_command(
        private, "!agent consent on", "member", {"message_type": "private", "user_id": "member"}
    )
