"""Day13 集成测试：群@、私聊、名片、敏感、管理员命令，全部 mock。"""
import asyncio
import json

from unittest.mock import AsyncMock, MagicMock, patch

import bridge_qq


def _make_bridge(monkeypatch):
    bridge = object.__new__(bridge_qq.Bridge)
    bridge.self_id = "900000000"
    bridge.sessions = MagicMock()
    bridge.sessions.get.return_value = {}
    bridge.sessions.data = {}
    bridge.style_store = MagicMock()
    bridge.recent_replies = []
    bridge.ask_model = AsyncMock(return_value="这是 mock 的回复喵")
    bridge.send_group_msg = AsyncMock()
    bridge.send_private_msg = AsyncMock()
    bridge.send_group_text = AsyncMock()
    bridge._fetch_reply_context = AsyncMock(return_value="")
    monkeypatch.setattr(bridge_qq, "_try_self_improve", lambda *a, **k: None)
    return bridge


def _run(coro):
    return asyncio.run(coro)


def test_group_at_message_triggers_llm_and_reply(monkeypatch):
    bridge = _make_bridge(monkeypatch)
    ev = {
        "message_type": "group",
        "group_id": 123456,
        "user_id": 10001,
        "message": [
            {"type": "at", "data": {"qq": "900000000"}},
            {"type": "text", "data": {"text": " 你好"}},
        ],
        "raw_message": "[CQ:at,qq=900000000] 你好",
        "sender": {"nickname": "用户A"},
    }
    _run(bridge._process_group(ev))
    bridge.send_group_msg.assert_awaited_once()
    bridge.ask_model.assert_awaited_once()


def test_private_message_triggers_llm_and_reply(monkeypatch):
    bridge = _make_bridge(monkeypatch)
    monkeypatch.setattr(bridge_qq, "SEND_THINKING", False)
    ev = {
        "message_type": "private",
        "user_id": 10001,
        "message": [{"type": "text", "data": {"text": "在吗"}}],
        "raw_message": "在吗",
        "sender": {"nickname": "用户A"},
    }
    _run(bridge._process_private(ev))
    bridge.send_private_msg.assert_awaited_once()
    bridge.ask_model.assert_awaited_once()


def test_bilibili_card_is_parsed_into_context(monkeypatch):
    bridge = _make_bridge(monkeypatch)
    monkeypatch.setattr(bridge_qq, "TRIGGER", "all")
    card_data = json.dumps({
        "meta": {
            "detail_1": {
                "title": "测试视频",
                "qqdocurl": "https://bilibili.com/video/BV123",
                "host": "bilibili.com",
            }
        }
    })
    ev = {
        "message_type": "group",
        "group_id": 123456,
        "user_id": 10001,
        "message": [{"type": "json", "data": {"data": card_data}}],
        "raw_message": "",
        "sender": {"nickname": "用户A"},
    }
    _run(bridge._process_group(ev))
    bridge.ask_model.assert_awaited_once()
    prompt = bridge.ask_model.await_args.args[1]
    assert "bilibili.com" in prompt.lower() or "分享" in prompt


def test_self_harm_is_blocked_without_llm(monkeypatch):
    bridge = _make_bridge(monkeypatch)
    ev = {
        "message_type": "private",
        "user_id": 10001,
        "message": [{"type": "text", "data": {"text": "我想自杀"}}],
        "raw_message": "我想自杀",
        "sender": {"nickname": "用户A"},
    }
    _run(bridge._process_private(ev))
    bridge.send_private_msg.assert_awaited_once()
    sent = bridge.send_private_msg.await_args.args[1]
    assert "400-161-9995" in sent
    bridge.ask_model.assert_not_awaited()


def test_admin_command_does_not_call_llm(monkeypatch):
    bridge = _make_bridge(monkeypatch)
    monkeypatch.setattr(bridge_qq, "ADMIN_UIDS", {"900000001"})
    ev = {
        "message_type": "private",
        "user_id": 900000001,
        "message": [{"type": "text", "data": {"text": "!status"}}],
        "raw_message": "!status",
        "sender": {"nickname": "管理员"},
    }
    _run(bridge._process_private(ev))
    bridge.send_private_msg.assert_awaited_once()
    assert "PID" in bridge.send_private_msg.await_args.args[1]
    bridge.ask_model.assert_not_awaited()


def test_non_admin_command_is_ignored(monkeypatch):
    bridge = _make_bridge(monkeypatch)
    monkeypatch.setattr(bridge_qq, "ADMIN_UIDS", {"900000001"})
    ev = {
        "message_type": "private",
        "user_id": 99999,
        "message": [{"type": "text", "data": {"text": "!status"}}],
        "raw_message": "!status",
        "sender": {"nickname": "普通用户"},
    }
    _run(bridge._process_private(ev))
    bridge.send_private_msg.assert_not_awaited()
    bridge.ask_model.assert_not_awaited()


def test_reply_message_context_is_kept_in_prompt(monkeypatch):
    bridge = _make_bridge(monkeypatch)
    monkeypatch.setattr(bridge_qq, "SEND_THINKING", False)
    bridge._fetch_reply_context = AsyncMock(
        return_value="这是被引用的原始内容")
    ev = {
        "message_type": "private",
        "user_id": 10001,
        "message": [
            {"type": "reply", "data": {"id": 123}},
            {"type": "text", "data": {"text": "再解释一下"}},
        ],
        "raw_message": "[CQ:reply,id=123] 再解释一下",
        "sender": {"nickname": "用户A"},
    }
    _run(bridge._process_private(ev))
    bridge.ask_model.assert_awaited_once()
    prompt = bridge.ask_model.await_args.args[1]
    assert "这是被引用的原始内容" in prompt


def test_share_card_is_parsed_into_context(monkeypatch):
    bridge = _make_bridge(monkeypatch)
    monkeypatch.setattr(bridge_qq, "TRIGGER", "all")
    ev = {
        "message_type": "group",
        "group_id": 123456,
        "user_id": 10001,
        "message": [{
            "type": "share",
            "data": {
                "title": "测试分享",
                "url": "https://example.com/123",
            },
        }],
        "raw_message": "",
        "sender": {"nickname": "用户A"},
    }
    _run(bridge._process_group(ev))
    bridge.ask_model.assert_awaited_once()
    prompt = bridge.ask_model.await_args.args[1]
    assert "https://example.com/123" in prompt


def test_forget_command_clears_current_session(monkeypatch):
    bridge = _make_bridge(monkeypatch)
    monkeypatch.setattr(bridge_qq, "ADMIN_UIDS", {"900000001"})
    monkeypatch.setattr(bridge_qq, "_clear_memory_md", lambda *a, **k: False)
    bridge.sessions.data["private_900000001"] = {"messages": []}
    ev = {
        "message_type": "private",
        "user_id": 900000001,
        "message": [{"type": "text", "data": {"text": "!forget"}}],
        "raw_message": "!forget",
        "sender": {"nickname": "管理员"},
    }
    _run(bridge._process_private(ev))
    bridge.send_private_msg.assert_awaited_once()
    assert "private_900000001" not in bridge.sessions.data
    bridge.ask_model.assert_not_awaited()


def test_clear_memory_requires_confirm_and_clears(monkeypatch):
    bridge = _make_bridge(monkeypatch)
    monkeypatch.setattr(bridge_qq, "ADMIN_UIDS", {"900000001"})
    monkeypatch.setattr(bridge_qq, "_reset_memory_md", lambda: True)
    uid = 900000001
    first_ev = {
        "message_type": "private",
        "user_id": uid,
        "message": [{"type": "text", "data": {"text": "!clear_memory"}}],
        "raw_message": "!clear_memory",
        "sender": {"nickname": "管理员"},
    }
    _run(bridge._process_private(first_ev))
    assert "确认清空所有记忆" in bridge.send_private_msg.await_args.args[1]

    bridge.send_private_msg.reset_mock()
    second_ev = {
        "message_type": "private",
        "user_id": uid,
        "message": [{"type": "text", "data": {"text": "!clear_memory yes"}}],
        "raw_message": "!clear_memory yes",
        "sender": {"nickname": "管理员"},
    }
    _run(bridge._process_private(second_ev))
    assert "已清空所有记忆" in bridge.send_private_msg.await_args.args[1]
    bridge.ask_model.assert_not_awaited()
    bridge_qq._PENDING_CLEAR_MEMORY.discard(str(uid))
