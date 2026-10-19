"""P0 回归：中文长期记忆的可召回性与隔离。

背景（2026-10-17）：
  - 写入一直正常，但 episode 候选原先被 `if episode_mode else []` 挡住，
    导致"我们考试是什么时候"这类**事实型**问法拿不到群内记录的事件。
  - 之前的"FTS5 中文无效"结论是**测错表**：实际检索走 content_index
    （trigram FTS + content LIKE 子串兜底），中文两字词本来就能命中。
"""
import os
import tempfile
import time

import pytest

from qqbot_core.agent_memory import AgentMemory
from qqbot_core.group_policy import FEATURES, GroupPolicy
from qqbot_core.observer import ObservedMessage
from qqbot_core.routing import classify_message

GROUP_A = "100000001"
GROUP_B = "100000002"


@pytest.fixture()
def memory(tmp_path):
    policy = GroupPolicy({"admin"}, persistence_path=str(tmp_path / "policy.sqlite3"))
    policy.grant(GROUP_A, "admin", FEATURES, "test")
    policy.grant(GROUP_B, "admin", FEATURES, "test")
    mem = AgentMemory(str(tmp_path), policy, limit_bytes=20 * 1024 ** 3)
    now = time.time()

    def msg(text, uid, mid, gid=GROUP_A):
        return ObservedMessage(group_id=gid, user_id=uid, message_id=mid,
                               timestamp=now, text=text, at_targets=(), reply_to="",
                               message_type="group", is_bot_message=False,
                               topic_id=1, observed_at=now)

    produced = mem.distill(
        [msg("我们下周三考试", "u1", "m1"),
         msg("啊这么早", "u2", "m2"),
         msg("那得开始复习了", "u1", "m3")],
        expected_version=policy.version(GROUP_A))
    assert produced == 1, "明确事件应写入一条长期记忆"
    return mem


@pytest.mark.parametrize("query", [
    "我们考试是什么时候",   # 事实型问法（原先拿不到候选）
    "考试是哪天",
    "我们下周三要干嘛",
    "之前说的考试是哪天",   # 回忆型问法（原先就能命中）
])
def test_chinese_recall_for_group_events(memory, query):
    out = str(memory.retrieve(GROUP_A, "u1", query, classify_message(query)))
    assert "考试" in out, f"{query} 应能召回已保存的群事件"


@pytest.mark.parametrize("query", ["推荐个晚饭", "今天天气不错", "讲个冷笑话"])
def test_unrelated_queries_do_not_pull_the_memory(memory, query):
    out = str(memory.retrieve(GROUP_A, "u1", query, classify_message(query)))
    assert "考试" not in out, f"{query} 与记忆无关，不应注入"


def test_memory_is_group_isolated(memory):
    out = str(memory.retrieve(GROUP_B, "u1", "我们考试是什么时候",
                              classify_message("我们考试是什么时候")))
    assert "考试" not in out, "跨群不得泄露记忆"


def test_recalled_memory_keeps_sources(memory, tmp_path):
    """召回时提示词带匿名来源与日期；原始 message_id 保留在库里但不进提示词。"""
    import json
    import sqlite3

    out = str(memory.retrieve(GROUP_A, "u1", "我们考试是什么时候",
                              classify_message("我们考试是什么时候")))
    assert "user_ref" in out, "提示词应带匿名来源标识"
    assert "reported" in out, "提示词应标明这是用户报告、非已证实事实"
    assert "2026-" in out, "提示词应带日期"

    con = sqlite3.connect(f"file:{os.path.join(str(tmp_path), 'memory.sqlite3')}?mode=ro",
                          uri=True)
    sources = json.loads(con.execute("select sources from episodes").fetchone()[0])
    con.close()
    assert sources and sources[0]["message_id"] == "m1", "库里应保留原始消息 ID"
    assert sources[0]["group_id"] == GROUP_A
