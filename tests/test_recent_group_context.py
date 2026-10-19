"""短期记忆（近期群聊窗口）必须落盘并在重启后仍可用。

背景：设计原文写明"短期记忆：bridge_sessions.json（按群/私聊分 key）"，
而实际实现把被动群聊只放在内存观察器里，重启即清空，导致群里追问
"你觉得呢"因为拿不到上下文而答非所问。
"""
import asyncio
import json
import os
import time

import bridge_qq


def _empty_bridge():
    bridge = object.__new__(bridge_qq.Bridge)
    bridge.sessions = None
    bridge.style_store = None
    bridge.observer = None
    bridge.recent_replies = []
    bridge.model_semaphore = None
    bridge.last_model_ref = ""
    bridge.api_key = ""
    bridge.knowledge_store = None
    bridge.agent_memory = None
    bridge._recent_group_flushed_at = 0.0
    bridge.recent_group_context = {}
    bridge.group_policy = type("P", (), {
        "version": lambda self, gid: 1,
        "revision": lambda self, gid: 1,
    })()
    bridge._group_allowed = lambda gid, feature: True
    return bridge


def test_recent_group_context_persists_across_restart(monkeypatch, tmp_path):
    path = tmp_path / "recent_group_context.json"
    monkeypatch.setattr(bridge_qq, "RECENT_GROUP_CONTEXT_FILE", str(path))

    first = _empty_bridge()
    first._append_recent_group_context("100000001", "3000000001", "群友甲", "我感觉江西考研比南京简单")
    first._append_recent_group_context("100000001", "3000000002", "群友乙", "考完去实习")
    first._flush_recent_group_context(force=True)

    assert path.exists(), "短期记忆必须落盘"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(saved["100000001"]) == 2

    second = _empty_bridge()   # 模拟重启后的新进程
    loaded = second._load_recent_group_context()
    assert [m["content"] for m in loaded["100000001"]] == [
        "群友甲: 我感觉江西考研比南京简单",
        "群友乙: 考完去实习",
    ]


def test_expired_recent_messages_are_not_restored(monkeypatch, tmp_path):
    path = tmp_path / "recent_group_context.json"
    monkeypatch.setattr(bridge_qq, "RECENT_GROUP_CONTEXT_FILE", str(path))
    monkeypatch.setattr(bridge_qq, "RECENT_GROUP_TTL_SECONDS", 60)

    path.write_text(json.dumps({"100000001": [
        {"role": "user", "content": "太老的消息", "speaker_id": "u",
         "at": time.time() - 9999, "policy_version": 1},
        {"role": "user", "content": "刚说的", "speaker_id": "u",
         "at": time.time() - 5, "policy_version": 1},
    ]}, ensure_ascii=False), encoding="utf-8")

    loaded = _empty_bridge()._load_recent_group_context()
    assert [m["content"] for m in loaded["100000001"]] == ["刚说的"]


def test_unauthorized_group_is_not_recorded(monkeypatch, tmp_path):
    path = tmp_path / "recent_group_context.json"
    monkeypatch.setattr(bridge_qq, "RECENT_GROUP_CONTEXT_FILE", str(path))

    bridge = _empty_bridge()
    bridge._group_allowed = lambda gid, feature: False
    bridge._append_recent_group_context("999", "u", "某人", "不该被记住")
    bridge._flush_recent_group_context(force=True)

    assert bridge.recent_group_context == {}
    assert not path.exists() or "999" not in path.read_text(encoding="utf-8")


def test_restart_keeps_group_context_reaching_the_model(monkeypatch, tmp_path):
    """端到端：重启（内存观察器为空）后，群聊上下文仍要送进模型。"""
    path = tmp_path / "recent_group_context.json"
    monkeypatch.setattr(bridge_qq, "RECENT_GROUP_CONTEXT_FILE", str(path))
    monkeypatch.setattr(bridge_qq, "AGENT_MEMORY_ENABLED", False)
    monkeypatch.setattr(bridge_qq, "KNOWLEDGE_SYSTEM", False)
    monkeypatch.setattr(bridge_qq, "_answer_cache_get", lambda *a, **k: None)

    writer = _empty_bridge()
    writer._append_recent_group_context("100000001", "3000000001", "群友甲", "我感觉江西考研比南京简单")
    writer._append_recent_group_context("100000001", "3000000002", "群友乙", "考完去实习")
    writer._flush_recent_group_context(force=True)

    bridge = _empty_bridge()
    bridge.sessions = type("S", (), {"get": lambda self, k: {"messages": [], "summary_index": 0},
                                     "put": lambda self, k, v: None})()
    bridge.recent_group_context = bridge._load_recent_group_context()
    captured = {}

    async def fake_complete(system, messages, **kwargs):
        captured["messages"] = messages
        captured["system"] = system
        return "模拟", "provider/model"

    bridge._complete = fake_complete
    bridge._complete_race = fake_complete
    bridge._complete_with_tools = fake_complete

    asyncio.run(bridge.ask_model("100000001", "你觉得呢", "你觉得呢", speaker_id="3000000001"))

    joined = "\n".join(str(m.get("content")) for m in captured.get("messages") or [])
    assert "江西考研比南京简单" in joined, f"重启后上下文没有送进模型: {joined!r}"
    assert "考完去实习" in joined
    assert "【群聊接话】" not in captured.get("system", "")


def test_window_is_isolated_by_authorization_version(monkeypatch, tmp_path):
    """P0（2026-10-18）：撤销授权再重新授权后，上一个授权周期的群聊不得复用。"""
    path = tmp_path / "recent_group_context.json"
    monkeypatch.setattr(bridge_qq, "RECENT_GROUP_CONTEXT_FILE", str(path))

    bridge = _empty_bridge()          # 策略版本 = 1
    bridge._append_recent_group_context("100000001", "u1", "甲", "旧授权周期说的话")
    bridge._flush_recent_group_context(force=True)

    # 同版本：能读到
    assert [m["content"] for m in bridge._recent_group_window("100000001")] == [
        "甲: 旧授权周期说的话"]

    # 模拟"撤销后重新授权"：版本变成 2
    bridge.group_policy = type("P", (), {
        "version": lambda self, gid: 2,
        "revision": lambda self, gid: 2,
    })()
    assert bridge._recent_group_window("100000001") == [], "旧版本的窗口不得复用"

    # 落盘也要清掉旧版本条目
    bridge._flush_recent_group_context(force=True)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "100000001" not in saved or not saved["100000001"]


def test_entries_without_version_are_not_reused(monkeypatch, tmp_path):
    """缺版本的旧数据（本改动之前写入的）一律不加载——无法确认授权周期。"""
    path = tmp_path / "recent_group_context.json"
    monkeypatch.setattr(bridge_qq, "RECENT_GROUP_CONTEXT_FILE", str(path))
    path.write_text(json.dumps({"100000001": [
        {"role": "user", "content": "没有版本号的老数据",
         "speaker_id": "u", "at": time.time() - 5},
    ]}, ensure_ascii=False), encoding="utf-8")

    loaded = _empty_bridge()._load_recent_group_context()
    assert "100000001" not in loaded
