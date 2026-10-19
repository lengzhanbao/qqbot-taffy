"""③ DeepSeek 直接生成：正常回答不再被风格/质量闸门拒绝。

设计（2026-10-17）：只有 API 失败、空内容、协议无效（工具 JSON/乱码）、
明确截断、语言不匹配和安全身份边界才处理。不因回复平淡、没有"喵"、
没有表情、偏短而丢弃或换模型——那会把能正常回复的答案换成更差模型的答案。
"""
import asyncio
import os
import sys

import pytest

import bridge_qq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_model_fallback import FIRST, _make_bridge, _ok, _run  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_model_state(monkeypatch):
    """隔离模型熔断状态，避免被其他测试文件留下的状态影响。"""
    monkeypatch.setattr(bridge_qq, "_model_health", {})
    monkeypatch.setattr(bridge_qq, "_provider_cooldown", {})
    monkeypatch.setattr(bridge_qq, "_tool_model_cooldown", {})
    monkeypatch.setattr(bridge_qq, "inc_stat", lambda *args, **kwargs: None)
    monkeypatch.setattr(bridge_qq, "write_health", lambda **kwargs: None)


def test_plain_reply_is_not_rejected(monkeypatch):
    """平淡但正常的回答必须被接受，不能丢弃、不能换模型。"""
    monkeypatch.setattr(bridge_qq, "REJECTION_GATES", False)
    bridge = _make_bridge()
    calls = []

    def fake(self, body, api_url, api_key):
        calls.append(body["model"])
        return _ok("在的，刚吃完饭。")

    monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
    reply, model = _run(bridge._complete(
        "sys", [{"role": "user", "content": "在干嘛"}],
        model_refs=[FIRST], weak_query="在干嘛",
        min_reply_len=40, deadline=None,
    ))

    assert reply == "在的，刚吃完饭。"
    assert len(calls) == 1, f"应当只调用一次且不被丢弃: {calls}"


def test_rejection_gates_can_be_restored(monkeypatch):
    """回滚开关：QQ_REJECTION_GATES=1 时恢复旧的弱回复拒答。"""
    monkeypatch.setattr(bridge_qq, "REJECTION_GATES", True)
    bridge = _make_bridge()
    calls = []

    def fake(self, body, api_url, api_key):
        calls.append(body["model"])
        return _ok("豆")

    monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
    reply, _ = _run(bridge._complete(
        "sys", [{"role": "user", "content": "豆包是什么"}],
        model_refs=[FIRST], weak_query="豆包是什么",
        deadline=None,
    ))

    assert reply == "", "打开开关后应恢复旧的弱回复拒答行为"


def test_protocol_gate_still_applies(monkeypatch):
    """协议类闸门不受开关影响：工具调用 JSON 泄漏仍必须被拦下。"""
    monkeypatch.setattr(bridge_qq, "REJECTION_GATES", False)
    bridge = _make_bridge()

    def fake(self, body, api_url, api_key):
        return _ok('{"name": "search_web", "arguments": {"query": "x"}}')

    monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
    reply, _ = _run(bridge._complete(
        "sys", [{"role": "user", "content": "在吗"}],
        model_refs=[FIRST], deadline=None,
    ))

    assert reply == "", "工具 JSON 泄漏必须仍被拦下"


def test_english_dominant_still_rejected(monkeypatch):
    """语言不匹配不是风格偏好，仍应处理。"""
    monkeypatch.setattr(bridge_qq, "REJECTION_GATES", False)
    bridge = _make_bridge()

    def fake(self, body, api_url, api_key):
        return _ok("Sure, that sounds good to me, let me check the details for you.")

    monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
    reply, _ = _run(bridge._complete(
        "sys", [{"role": "user", "content": "在吗"}],
        model_refs=[FIRST], deadline=None,
    ))

    assert reply == "", "中文群聊回整段英文仍应处理"
