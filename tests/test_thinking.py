import asyncio

import bridge_qq


class _FakeStyleStore:
    def learn(self, *args, **kwargs):
        pass


class _FakeSessions:
    def get(self, key):
        return {"messages": []}

    def put(self, *args, **kwargs):
        pass


def test_group_message_sends_only_final_reply_by_default(monkeypatch):
    monkeypatch.setattr(bridge_qq, "_try_self_improve", lambda *a, **k: None)
    assert bridge_qq.SEND_THINKING is False

    bridge = object.__new__(bridge_qq.Bridge)
    bridge.self_id = "900000000"
    bridge.style_store = _FakeStyleStore()
    bridge.sessions = _FakeSessions()
    bridge.recent_replies = []
    calls = []

    async def fake_send_group_text(gid, text):
        calls.append(("thinking", gid, text))

    async def fake_send_group_msg(gid, uid, text):
        calls.append(("reply", gid, uid, text))

    async def fake_ask_model(*args, **kwargs):
        return "test reply"

    bridge.send_group_text = fake_send_group_text
    bridge.send_group_msg = fake_send_group_msg
    bridge.ask_model = fake_ask_model

    event = {
        "post_type": "message",
        "message_type": "group",
        "group_id": 123,
        "user_id": 456,
        "self_id": "900000000",
        "message": [
            {"type": "text", "data": {"text": "hi"}},
            {"type": "at", "data": {"qq": "900000000"}},
        ],
        "sender": {"nickname": "tester"},
    }

    asyncio.run(bridge._process_group(event))

    assert calls == [("reply", "123", "456", "test reply")]
