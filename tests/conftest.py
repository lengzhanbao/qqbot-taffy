"""pytest 全局配置，保证 tests/ 能安全 import bridge_qq。"""
import sys
import os
from pathlib import Path

# CI (windows-latest) 的 locale 是 CP936(GBK)，测试里 print 中文会 UnicodeEncodeError。
# 必须在 pytest 收集/输出任何中文之前，把三个标准流强制重配为 UTF-8。
# PYTHONIOENCODING 只影响**新打开**的流，pytest 已经拿到的 sys.stdout 不会变。
def _force_utf8_stdio():
    import io as _io
    for name in ("stdout", "stderr", "stdin"):
        old = getattr(sys, name, None)
        if old is None:
            continue
        enc = getattr(old, "encoding", None) or ""
        if "utf" in enc.lower():
            continue
        setattr(sys, name, _io.TextIOWrapper(
            getattr(old, "buffer", old), encoding="utf-8", errors="replace",
            newline="", write_through=True))


_force_utf8_stdio()

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("QQ_TEST_MODE", "1")
import uuid
os.environ["QQ_TEST_DATA_DIR"] = str(PROJECT_ROOT / ".tmp" / ("test-" + uuid.uuid4().hex))
os.environ["TMP"] = os.environ["QQ_TEST_DATA_DIR"]
os.environ["TEMP"] = os.environ["QQ_TEST_DATA_DIR"]
Path(os.environ["QQ_TEST_DATA_DIR"]).mkdir(parents=True, exist_ok=True)

import pytest


@pytest.fixture(autouse=True)
def _silence_bridge_log(monkeypatch):
    import bridge_qq
    monkeypatch.setattr(bridge_qq, "log", lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def _block_live_network(monkeypatch):
    import socket
    import threading
    state = threading.local()
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    original_pair = socket.socketpair
    def pair(*args, **kwargs):
        state.internal_pair = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            state.internal_pair = False
    def blocked(*args, **kwargs):
        if getattr(state, "internal_pair", False):
            return original_connect(*args, **kwargs)
        raise RuntimeError("live network disabled in offline tests")
    def blocked_ex(*args, **kwargs):
        if getattr(state, "internal_pair", False):
            return original_connect_ex(*args, **kwargs)
        raise RuntimeError("live network disabled in offline tests")
    monkeypatch.setattr(socket, "socketpair", pair)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked_ex)


@pytest.fixture
def sample_group_msg():
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": 123456789,
        "user_id": 900000001,
        "message": [{"type": "at", "data": {"qq": "900000000", "name": "测试机器人"}},
                    {"type": "text", "data": {"text": " 你好"}}],
        "raw_message": "[CQ:at,qq=900000000] 你好",
        "sender": {"nickname": "测试用户", "card": ""},
    }


@pytest.fixture
def sample_private_msg():
    return {
        "post_type": "message",
        "message_type": "private",
        "user_id": 900000001,
        "message": [{"type": "text", "data": {"text": "在吗"}}],
        "raw_message": "在吗",
        "sender": {"nickname": "测试用户"},
    }
