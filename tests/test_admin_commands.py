import bridge_qq
import pytest


@pytest.fixture(autouse=True)
def _restore_admin_state():
    old_style = bridge_qq.STYLE_LEVEL
    old_taffy = bridge_qq.TAFFY_FLAVOR
    old_admins = bridge_qq.ADMIN_UIDS
    old_pending = set(bridge_qq._PENDING_CLEAR_MEMORY)
    yield
    bridge_qq.STYLE_LEVEL = old_style
    bridge_qq.TAFFY_FLAVOR = old_taffy
    bridge_qq.ADMIN_UIDS = old_admins
    bridge_qq._PENDING_CLEAR_MEMORY.clear()
    bridge_qq._PENDING_CLEAR_MEMORY.update(old_pending)


class TestPermissions:
    def test_admin_matches_str_and_int(self):
        bridge_qq.ADMIN_UIDS = {"123"}
        assert bridge_qq.is_admin(123)
        assert bridge_qq.is_admin("123")
        assert not bridge_qq.is_admin("456")

    def test_non_admin_command_ignored(self):
        bridge_qq.ADMIN_UIDS = {"123"}
        assert bridge_qq.handle_admin_command("!status", "456") is None


class TestCommands:
    def test_status(self, monkeypatch):
        bridge_qq.ADMIN_UIDS = {"123"}
        monkeypatch.setattr(bridge_qq, "read_health", lambda: {
            "pid": 1,
            "start_time": "2026-08-07T16:00:00",
            "ws_connected": True,
            "last_error": "",
        })

        reply = bridge_qq.handle_admin_command("!status", "123")
        assert "PID: 1" in reply
        assert "已连接" in reply

    def test_admin_text_with_at_prefix(self, monkeypatch):
        bridge_qq.ADMIN_UIDS = {"123"}
        monkeypatch.setattr(bridge_qq, "read_health", lambda: {
            "pid": 1,
            "start_time": "2026-08-07T16:00:00",
            "ws_connected": True,
            "last_error": "",
        })

        reply = bridge_qq.handle_admin_command("@测试用户 !status", "123")
        assert "PID: 1" in reply

    def test_style(self):
        bridge_qq.ADMIN_UIDS = {"123"}
        reply = bridge_qq.handle_admin_command("!style 1", "123")
        assert "L1" in reply
        assert bridge_qq.STYLE_LEVEL == 1

    def test_taffy_off(self):
        bridge_qq.ADMIN_UIDS = {"123"}
        reply = bridge_qq.handle_admin_command("!taffy off", "123")
        assert "已关闭" in reply
        assert bridge_qq.TAFFY_FLAVOR is False

    def test_model(self):
        bridge_qq.ADMIN_UIDS = {"123"}
        reply = bridge_qq.handle_admin_command("!model", "123")
        assert "opencode_free/deepseek-v4-flash-free" in reply
        assert "当前模型顺序" in reply

    def test_unknown_command(self):
        bridge_qq.ADMIN_UIDS = {"123"}
        reply = bridge_qq.handle_admin_command("!nope", "123")
        assert "未知指令" in reply

    def test_forget_no_data(self, monkeypatch):
        bridge_qq.ADMIN_UIDS = {"123"}
        monkeypatch.setattr(bridge_qq, "_run_backup_hidden", lambda tag: None)
        monkeypatch.setattr(bridge_qq, "_clear_memory_md", lambda scope: False)

        class FakeSessions:
            data = {}

            def save(self):
                pass

        class FakeStyleStore:
            data = {}

            def save(self):
                pass

        class FakeBridge:
            pass

        bridge = FakeBridge()
        bridge.sessions = FakeSessions()
        bridge.style_store = FakeStyleStore()
        reply = bridge_qq.handle_admin_command(
            "!forget",
            "123",
            {"message_type": "group", "group_id": "99"},
            bridge,
        )
        assert "暂无" in reply


class TestClearMemory:
    def test_first_confirmation(self):
        bridge_qq.ADMIN_UIDS = {"123"}
        reply = bridge_qq.handle_admin_command("!clear_memory", "123")
        assert "确认" in reply
        assert "123" in bridge_qq._PENDING_CLEAR_MEMORY

    def test_requires_prior_confirmation(self):
        bridge_qq.ADMIN_UIDS = {"123"}
        reply = bridge_qq.handle_admin_command("!clear_memory yes", "123")
        assert "请先发" in reply

    def test_confirmed_clear(self, monkeypatch):
        bridge_qq.ADMIN_UIDS = {"123"}
        bridge_qq._PENDING_CLEAR_MEMORY.add("123")
        monkeypatch.setattr(bridge_qq, "_run_backup_hidden", lambda tag: None)
        monkeypatch.setattr(bridge_qq, "_reset_memory_md", lambda: True)

        class FakeSessions:
            data = {"g1": {"messages": []}}

            def save(self):
                self.data = {}

        class FakeStyleStore:
            data = {"g1": {"stats": {}}}

            def save(self):
                self.data = {}

        class FakeBridge:
            pass

        bridge = FakeBridge()
        bridge.sessions = FakeSessions()
        bridge.style_store = FakeStyleStore()
        reply = bridge_qq.handle_admin_command(
            "!clear_memory yes",
            "123",
            bridge=bridge,
        )
        assert "已清空所有记忆" in reply
        assert bridge.sessions.data == {}
        assert bridge.style_store.data == {}
