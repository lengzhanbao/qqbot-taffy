"""原始 QQ 号不得进入模型提示词（长期记忆/短期上下文都适用）。"""
import re
import time

import bridge_qq
from qqbot_core.group_policy import FEATURES, GroupPolicy
from qqbot_core.observer import GroupConversationObserver

QQ_ID = re.compile(r"\d{6,}")


def _observer():
    policy = GroupPolicy({"admin"})
    policy.grant("100000001", "admin", FEATURES, "notice")
    return GroupConversationObserver(policy)


def _ingest(observer, uid, mid, text):
    observer.ingest({
        "group_id": "100000001", "user_id": uid, "message_id": mid,
        "message_type": "group", "time": time.time(),
        "message": [{"type": "text", "data": {"text": text}}],
    }, "")


def test_observer_context_hides_raw_qq_ids():
    observer = _observer()
    _ingest(observer, "3000000001", "m1", "我感觉江西考研比南京简单")
    _ingest(observer, "3000000002", "m2", "考完去实习")

    rows = observer.context("100000001")

    joined = "\n".join(row["content"] for row in rows)
    assert "3000000001" not in joined
    assert "3000000002" not in joined
    assert not QQ_ID.search(joined), f"提示词里出现了疑似 QQ 号: {joined!r}"
    # 但要保留"谁在说话"的可区分性
    assert "群友1" in joined and "群友2" in joined
    assert "考研" in joined
    # 内部过滤仍能用真实 ID（speaker_id 不进提示词）
    assert {row["speaker_id"] for row in rows} == {"3000000001", "3000000002"}


def test_same_speaker_keeps_one_label():
    observer = _observer()
    _ingest(observer, "3000000001", "m1", "第一句")
    _ingest(observer, "3000000002", "m2", "插一句")
    _ingest(observer, "3000000001", "m3", "第三句")

    rows = observer.context("100000001")
    labels = [row["content"].split(":")[0] for row in rows]
    assert labels == ["群友1", "群友2", "群友1"]


def test_recent_group_context_does_not_fall_back_to_uid(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_qq, "RECENT_GROUP_CONTEXT_FILE", str(tmp_path / "rg.json"))
    bridge = object.__new__(bridge_qq.Bridge)
    bridge.recent_group_context = {}
    bridge._recent_group_flushed_at = 0.0
    bridge._group_allowed = lambda gid, feature: True

    bridge._append_recent_group_context("100000001", "3000000001", "", "没有昵称的消息")
    stored = list(bridge.recent_group_context["100000001"])[0]["content"]

    assert "3000000001" not in stored
    assert "群友" in stored
