"""pytest 全局配置，保证 tests/ 能安全 import bridge_qq。"""
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("QQ_TEST_MODE", "1")

import pytest


@pytest.fixture(autouse=True)
def _silence_bridge_log(monkeypatch):
    import bridge_qq
    monkeypatch.setattr(bridge_qq, "log", lambda *args, **kwargs: None)


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
