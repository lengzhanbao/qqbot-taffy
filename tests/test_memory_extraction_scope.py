"""长期记忆提取范围（固定样本）。

2026-10-17：提取器原先词表过窄 + 要求同话题 ≥2 条命中，导致
"我们下周三考试"这类明确事件也存不进去。现在：
  - 扩展 _EVENT / _AGREEMENT 的真实群聊表达
  - 对"明确主体 + 时间 + 事件/约定"的单条消息开强触发通道
  - 普通闲聊仍保留多条证据门槛
  - _PERSONAL 的同意边界不动：未经同意的个人偏好/联系方式不自动入库
"""
import pytest

from qqbot_core import agent_memory as AM


# (消息, 是否应构成单条记忆候选)
SAMPLES = [
    ("我们下周三考试", True),
    ("下周五报名截止", True),
    ("说定了周六下午", True),
    ("讲好了这周日一起复习", True),
    ("明天上午九点体检", True),
    ("3月15号答辩", True),
    # 普通吐槽 / 闲聊 / 无时间指向：不应成为具体日程
    ("考试真烦", False),
    ("一起去吧", False),
    ("我刚刚排到一个李信", False),
    ("这缩招也太多了吧", False),
    ("大保研时代了", False),
    ("那你能保研去哪里", False),
    ("群里以后每周五开黑", False),   # 无具体时间点，仍需多条证据
]


@pytest.mark.parametrize("text,expected", SAMPLES)
def test_single_trigger_scope(text, expected):
    assert AM._strong_group_event(text) is expected


def test_personal_preferences_still_excluded_without_consent():
    """个人偏好与联系方式仍由 _PERSONAL 拦下，不因扩词表而漏出。"""
    for text in ("我喜欢吃薯条", "我讨厌香菜", "我的QQ号是123456789",
                 "我电话13800138000"):
        assert AM._PERSONAL.search(text), text
        assert not AM._strong_group_event(text), text


def test_extended_vocabulary_matches_real_group_events():
    for text in ("我们下周三考试", "下周五报名截止", "说定了周六下午"):
        assert AM._EVENT.search(text) or AM._AGREEMENT.search(text), text


def test_negative_emotion_does_not_become_schedule():
    """"考试真烦"含事件词，但带情绪否定 → 不单独成记忆。"""
    assert AM._STRONG_EVENT.search("考试真烦")
    assert AM._EVENT_NEGATIVE.search("考试真烦")
    assert not AM._strong_group_event("考试真烦")


def test_consent_reference_api_still_available():
    """同意机制保留：提取个人偏好时必须能取到 consent_reference。"""
    from qqbot_core.group_policy import GroupPolicy
    assert hasattr(GroupPolicy, "consent_reference")


# ---------------- P1（2026-10-18）----------------

def test_short_term_window_does_not_store_same_message_twice(monkeypatch, tmp_path):
    """@ 消息历史上会被"观察器"和"直答路径"各记一次，窗口里出现两遍。"""
    import bridge_qq

    path = tmp_path / "recent_group_context.json"
    monkeypatch.setattr(bridge_qq, "RECENT_GROUP_CONTEXT_FILE", str(path))
    bridge = object.__new__(bridge_qq.Bridge)
    bridge.recent_group_context = {}
    bridge._recent_group_flushed_at = 0.0
    bridge.group_policy = type("P", (), {
        "version": lambda self, gid: 1, "revision": lambda self, gid: 1})()
    bridge._group_allowed = lambda gid, feature: True

    bridge._append_recent_group_context("G", "u1", "甲", "塔菲在吗", message_id="M1")
    bridge._append_recent_group_context("G", "u1", "甲", "塔菲在吗", message_id="M1")
    assert len(bridge._recent_group_window("G")) == 1

    bridge._append_recent_group_context("G", "u2", "乙", "在的", message_id="M2")
    assert len(bridge._recent_group_window("G")) == 2


def test_episode_evidence_is_trigger_plus_adjacent_only():
    """记忆内容应围绕触发消息，而不是机械取话题窗口的最后 6 条。"""
    import time

    from qqbot_core.agent_memory import _select_episode_evidence
    from qqbot_core.observer import ObservedMessage

    now = time.time()

    def message(text, mid, offset):
        return ObservedMessage(group_id="G", user_id="u", message_id=mid,
                               timestamp=now + offset, text=text, at_targets=(),
                               reply_to="", message_type="group", is_bot_message=False,
                               topic_id=1, observed_at=now)

    window = [message("今天天气不错", "a1", -600), message("中午吃了什么", "a2", -580),
              message("随便吃了点", "a3", -560), message("我们下周三考试", "a4", -10),
              message("啊这么早", "a5", -5)]
    picked = _select_episode_evidence(window, [window[3]], False, True)
    texts = [m.text for m in picked]
    assert "我们下周三考试" in texts
    assert "啊这么早" in texts
    assert "今天天气不错" not in texts
    assert "中午吃了什么" not in texts


def test_overlapping_evidence_is_not_saved_as_a_second_episode(tmp_path):
    """第二条记忆不得把第一条整个包含一遍。"""
    import time

    from qqbot_core.agent_memory import AgentMemory
    from qqbot_core.group_policy import FEATURES, GroupPolicy
    from qqbot_core.observer import ObservedMessage

    policy = GroupPolicy({"admin"}, persistence_path=str(tmp_path / "p.sqlite3"))
    policy.grant("G", "admin", FEATURES, "t")
    memory = AgentMemory(str(tmp_path), policy, limit_bytes=20 * 1024 ** 3)
    now = time.time()

    def message(text, mid, offset):
        return ObservedMessage(group_id="G", user_id="u", message_id=mid,
                               timestamp=now + offset, text=text, at_targets=(),
                               reply_to="", message_type="group", is_bot_message=False,
                               topic_id=1, observed_at=now)

    first = [message("我们下周三考试", "a4", -10), message("啊这么早", "a5", -5)]
    assert memory.distill(first, expected_version=policy.version("G")) >= 1

    overlap = [message("我们下周三考试", "a4", -10), message("啊这么早", "a5", -5),
               message("那得开始复习了", "a6", 2)]
    memory.distill(overlap, expected_version=policy.version("G"))

    import glob
    import sqlite3
    db = [p for p in glob.glob(str(tmp_path / "**" / "*.sqlite3"), recursive=True)
          if "p.sqlite3" not in p][0]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    count = con.execute("select count(*) from episodes").fetchone()[0]
    con.close()
    assert count == 1, f"重叠内容被重复保存（episodes={count}）"
