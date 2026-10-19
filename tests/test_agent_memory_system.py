import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from qqbot_core.agent_memory import AgentMemory, _date_window
from qqbot_core.group_policy import FEATURES, GroupPolicy
from qqbot_core.observer import ObservedMessage
from qqbot_core.routing import MessageRoute, classify_message


BASE = Path(__file__).resolve().parents[1] / ".tmp" / "agent-memory-tests"
BASE.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def env():
    root = BASE / uuid.uuid4().hex
    root.mkdir()
    now = [1_800_000_000.0]
    policy = GroupPolicy(["admin"])
    policy.grant("g1", "admin", FEATURES, "notice-1")
    policy.grant("g2", "admin", FEATURES, "notice-2")
    agent = AgentMemory(root, policy, clock=lambda: now[0])
    return root, policy, agent, now


def msg(mid, text, *, gid="g1", uid="u1", stamp=1_800_000_000, bot=False, topic=3, kind="group"):
    return ObservedMessage(gid, uid, str(mid), stamp, text, (), "", kind, bot, topic, stamp)


def review(agent, item_id):
    assert agent.command(["review", item_id], "g1", "admin", trusted_role="admin") == "已审阅"


def retrieve(agent, q, route=None, gid="g1", uid="u1"):
    return agent.retrieve(gid, uid, q, route or classify_message(q))


def test_followup_and_relative_event_recall_without_review(env):
    _, _, agent, now = env
    assert agent.distill([msg("1", "昨天群里举办了桌游活动", stamp=now[0]),
                          msg("2", "大家一起玩了桌游活动", stamp=now[0]+1)]) == 1
    with agent.store._connection() as db:
        row = db.execute("SELECT id,status FROM episodes").fetchone()
        item_id = row["id"]
    assert row["status"] == "reported"
    now[0] += 86400
    memory, trace = retrieve(agent, "昨天发生了什么？")
    assert memory and "桌游活动" in memory["content"]
    assert memory["role"] == "user" and "不可信" in memory["content"]
    assert trace["selected"] == 1 and item_id in trace["memory_ids"]
    followup, _ = retrieve(agent, "那活动呢？", MessageRoute("chitchat", "NEVER", "same_user", False))
    assert followup and "桌游活动" in followup["content"]


def test_agreement_closure_triggers_early_and_topics_never_mix(env):
    _, _, agent, now = env
    batch=[msg("1","周六一起打游戏吧",stamp=now[0],topic=7),
           msg("2","晚上八点",stamp=now[0]+1,topic=7),
           msg("3","可以就这么定",stamp=now[0]+2,topic=7),
           msg("4","昨天球队友谊赛赢了",stamp=now[0]+3,topic=8)]
    assert agent.distill(batch)==1
    with agent.store._connection() as db:
        row=db.execute("SELECT content,topic_id FROM episodes").fetchone()
    assert row["topic_id"]==7 and "周六一起打游戏吧" in row["content"] and "球队友谊赛" not in row["content"]


def test_style_accumulation_is_independent_and_grant_gated(env):
    _, policy, agent, now=env
    agent.style_learning_enabled=False
    assert agent.distill([msg("1","绝了",stamp=now[0]),msg("2","绝了",stamp=now[0]+1)])==0
    with agent.store._connection() as db:
        assert db.execute("SELECT count(*) FROM style_stats").fetchone()[0]==0
    agent.style_learning_enabled=True
    policy.grant("g1","admin",FEATURES-{"style_learning"},"no-style")
    agent.distill([msg("3","绝了",stamp=now[0]+2),msg("4","绝了",stamp=now[0]+3)])
    with agent.store._connection() as db:
        assert db.execute("SELECT count(*) FROM style_stats").fetchone()[0]==0
    policy.grant("g1","admin",FEATURES,"style-on")
    repeated=[msg("5","绝了",stamp=now[0]+4),msg("6","绝了",stamp=now[0]+5)]
    assert agent.distill(repeated)==0
    assert agent.distill(repeated)==0
    with agent.store._connection() as db:
        assert db.execute("SELECT count(*) FROM episodes").fetchone()[0]==0
        assert db.execute("SELECT status FROM notes WHERE category='style'").fetchone()[0]=="candidate"
        assert db.execute("SELECT count FROM style_stats WHERE category='style'").fetchone()[0]==2


def test_reviewed_generic_style_can_apply_without_query_overlap(env):
    _,_,agent,now=env
    agent.distill([msg("1","绝了",stamp=now[0]),msg("2","绝了",stamp=now[0]+1)])
    with agent.store._connection() as db:
        item=db.execute("SELECT id FROM notes WHERE category='style'").fetchone()[0]
    review(agent,item)
    route=MessageRoute("chitchat","NEVER","none",False)
    memory,_=agent.retrieve("g1","u1","普通闲聊没有重合关键词",route)
    assert memory and "绝了" in memory["content"]
    assert agent.retrieve("g1","u1","换个话题，普通闲聊",route)[0] is None


def test_topic_reset_unrelated_and_required_fresh_fact_do_not_inject(env):
    _, _, agent, now = env
    agent.distill([msg("1", "昨天球队在友谊赛赢了", stamp=now[0]),
                   msg("2", "昨天球队友谊赛结束", stamp=now[0]+1)])
    with agent.store._connection() as db:
        item = db.execute("SELECT id FROM episodes").fetchone()[0]
    assert retrieve(agent, "换个话题，昨天发生了什么？")[0] is None
    assert agent.retrieve("g1","u1","群里昨天下午有什么话题？",MessageRoute("chitchat","AUTO","group",False))[0] is None
    assert retrieve(agent, "昨天球队赢了吗？", classify_message("帮我搜索昨天球队赢了吗？"))[0] is None
    assert retrieve(agent, "今天天气如何？", classify_message("今天天气如何？"))[0] is None
    assert retrieve(agent, "晚饭吃什么？")[0] is None


def test_meme_candidate_never_retrieved_until_reviewed_and_not_fact(env):
    _, _, agent, now = env
    assert agent.distill([msg("1", "扣1送皮肤哈哈", stamp=now[0]),
                          msg("2", "扣1送皮肤哈哈", stamp=now[0]+1)]) == 0
    with agent.store._connection() as db:
        item = db.execute("SELECT id,status FROM notes WHERE category='meme'").fetchone()
    assert item and item["status"] == "candidate"
    assert "候选" not in agent.command(["candidates"], "g1", "u1")
    listed=json.loads(agent.command(["candidates"],"g1","admin",trusted_role="admin"))
    assert any(row["id"]==item["id"] for row in listed)
    assert retrieve(agent, "这个群梗是什么？")[0] is None
    review(agent, item["id"])
    memory, _ = retrieve(agent, "这个群梗是什么？")
    assert memory and memory["content"].startswith("不可信") and '"kind":"meme"' in memory["content"]


def test_group_private_and_preference_scope_with_consent_revoke(env):
    _, policy, agent, now = env
    policy.consent("g1", "u1", "u1", "personal_preference", "consent-1")
    result = agent.command(["remember", "preference", "我喜欢足球"], "g1", "u1", "source-1")
    assert "本人自述偏好" in result
    with agent.store._connection() as db:
        item = db.execute("SELECT id FROM notes WHERE category='personal_preference'").fetchone()[0]
    denied = agent.command(["forget", item], "g1", "u2", "other-user-forget", "member")
    assert "未找到" in denied and "足球" not in denied
    assert json.loads(agent.command(["export"], "g1", "u2"))["notes"] == []
    assert any(note["content"] == "我喜欢足球" for note in json.loads(agent.command(["export"], "g1", "u1"))["notes"])
    for i in range(50):
        agent.store._add_note("g1",f"候选游戏样式记录{i}","meme",
                              [{"kind":"human","group_id":"g1","user_id":"admin","message_id":f"candidate-{i}"}],
                              status="candidate")
    policy.consent("g1","u2","u2","personal_preference","consent-u2")
    for i in range(50):
        agent.store._add_note("g1",f"我喜欢足球{i}","personal_preference",
                              [{"kind":"human","group_id":"g1","user_id":"u2","message_id":f"pref-{i}"}],
                              subject="u2",consent="consent-u2",status="reviewed")
    q = "我喜欢什么？"
    own_memory,_=retrieve(agent, q, classify_message(q), "g1", "u1")
    assert own_memory and item in own_memory["content"]
    other_memory,_=retrieve(agent, q, classify_message(q), "g1", "u2")
    assert other_memory and item not in other_memory["content"]
    assert retrieve(agent, q, classify_message(q), "g2", "u1")[0] is None
    assert retrieve(agent, q, classify_message(q), "private", "u1")[0] is None
    policy.revoke_consent("g1", "u1", "u1")
    assert retrieve(agent, q, classify_message(q), "g1", "u1")[0] is None
    assert json.loads(agent.command(["export"], "g1", "u1"))["notes"] == []
    policy.consent("g1", "u1", "u1", "personal_preference", "consent-2")
    assert retrieve(agent, q, classify_message(q), "g1", "u1")[0] is None


def test_policy_revoke_regrant_and_restart_do_not_revive(env):
    root, policy, agent, now = env
    agent.distill([msg("1", "昨天小组完成了活动安排", stamp=now[0]-100),
                   msg("2", "昨天小组活动安排完成", stamp=now[0]-99)])
    with agent.store._connection() as db:
        item = db.execute("SELECT id FROM episodes").fetchone()[0]
    review(agent, item)
    policy.revoke("g1", "admin")
    policy.grant("g1", "admin", FEATURES, "new-notice")
    reopened = AgentMemory(root, policy, clock=lambda: now[0])
    assert retrieve(reopened, "昨天活动安排是什么？")[0] is None


def test_snapshot_forget_requires_two_confirms_and_tombstone_wins(env):
    root, _, agent, now = env
    agent.distill([msg("1", "昨天组织了读书活动", stamp=now[0]-100),
                   msg("2", "昨天读书活动开始", stamp=now[0]-99)])
    with agent.store._connection() as db:
        item = db.execute("SELECT id FROM episodes").fetchone()[0]
    review(agent, item)
    snapshot = root / "snapshot-a.sqlite3"
    agent.store.snapshot(snapshot)
    assert "确认逻辑撤回" in agent.command(["forget", item], "g1", "admin", "forget-1", "admin")
    assert "再执行一次" in agent.command(["forget", item, "confirm"], "g1", "admin", "forget-2", "admin")
    assert "已逻辑撤回" in agent.command(["forget", item, "confirm"], "g1", "admin", "forget-3", "admin")
    agent.store.restore_snapshot(snapshot)
    with agent.store._connection() as db:
        assert db.execute("SELECT 1 FROM tombstones WHERE id=?", (item,)).fetchone()
    assert retrieve(agent, "昨天那场读书活动是什么？")[0] is None


def test_capacity_refuses_write_without_database_damage(env):
    root, policy, _, now = env
    small = AgentMemory(root / "tiny", policy, clock=lambda: now[0])
    small.store.limit_bytes = 1024
    before = sqlite3.connect(small.store.path).execute("PRAGMA integrity_check").fetchone()[0]
    assert small.distill([msg("1", "昨天活动开始了")]) == 0
    after = sqlite3.connect(small.store.path).execute("PRAGMA integrity_check").fetchone()[0]
    assert before == after == "ok"


def test_embedding_load_failure_falls_back_to_keyword(env):
    root, policy, _, now = env
    fallback = AgentMemory(root / "fallback", policy, embedding_path=str(root / "missing"), clock=lambda: now[0])
    assert fallback.embedder.model is None
    assert fallback.embedder.error == "not_loaded"
    assert fallback.embedder.encode(["keyword fallback"]) is None
    assert fallback.embedder.error == "path_unavailable"
    assert fallback.distill([msg("1", "昨天社区进行了活动"),msg("2", "昨天社区活动结束",stamp=1_800_000_001)]) == 1


def test_duplicate_bot_unsafe_and_personal_text_excluded(env):
    _, _, agent, now = env
    rows = [msg("1", "昨天群里举行了活动"), msg("1", "昨天群里举行了活动"),
            msg("5", "昨天群里活动开始了"), msg("2", "昨天机器人举办了活动", bot=True),
            msg("3", "昨天手机号13800138000活动"), msg("4", "我昨天参加了活动"),
            msg("6", "我叫张三，昨天参加活动"), msg("7", "他喜欢的足球活动")]
    rows.extend([msg("8", "张三喜欢足球活动"), msg("9", "QQ号12345678活动")])
    assert agent.distill(rows) == 1
    with agent.store._connection() as db:
        count = db.execute("SELECT count(*) FROM episodes").fetchone()[0]
        sources = json.loads(db.execute("SELECT sources FROM episodes").fetchone()[0])
    assert count == 1 and {source["message_id"] for source in sources} == {"1","5"}


def test_commands_permissions_source_requirements_and_external_review(env):
    _, policy, agent, _ = env
    assert "可信管理员" in agent.command(["remember", "group", "群约定少刷屏"], "g1", "u1", "m1")
    assert "message_id" in agent.command(["remember", "group", "群约定少刷屏"], "g1", "admin")
    assert "已记录群约定" in agent.command(["remember", "group", "群约定少刷屏"], "g1", "admin", "m1", "admin")
    with agent.store._connection() as db:
        group_note = db.execute("SELECT id FROM notes WHERE category='group_note'").fetchone()[0]
    assert agent.command(["review", group_note], "g1", "admin", trusted_role="admin") == "已审阅"
    policy.consent("g1", "admin", "admin", "personal_preference", "c1")
    res = agent.command(["remember", "preference", "我喜欢编程"], "g1", "admin", "m2")
    assert "本人自述偏好" in res
    assert "仅支持群聊" in agent.command(["status"], "", "admin")
    assert json.loads(agent.command(["status"], "g1", "admin"))["enabled"] is True


def test_manual_external_fact_requires_explicit_https_review(env):
    _, _, agent, _ = env
    denied = agent.command(["remember", "fact", "赛事规则:每队五人"], "g1", "u1", "source-1")
    assert "可信管理员" in denied
    response = agent.command(["remember", "fact", "赛事规则:每队五人"], "g1", "admin", "source-1", "admin")
    item = response.rsplit(" ", 1)[-1]
    route = MessageRoute("fact", "AUTO", "none", True)
    assert agent.retrieve("g1", "u1", "赛事规则是什么？", route)[0] is None
    assert "https://" in agent.command(["review", item], "g1", "admin", trusted_role="admin")
    assert "管理员已登记核验来源" in agent.command(["review", item, "https://rules.example.org/official"], "g1", "admin", trusted_role="admin")
    memory, _ = agent.retrieve("g1", "u1", "赛事规则是什么？", route)
    assert memory and "赛事规则:每队五人" in memory["content"]
    assert agent.retrieve("g1", "u1", "赛事规则是什么？", MessageRoute("fact", "REQUIRED", "none", True))[0] is None


def test_legacy_verified_fact_is_available_only_for_auto(env):
    _, _, agent, now = env

    class LegacyItem:
        id = "legacy-verified"
        group_id = "g1"
        policy_version = agent.policy.version("g1")
        expires_at = now[0] + 3600
        category = "external_fact"
        evidence_status = "verified"
        content = "羽毛球比赛每局二十一分"
        source_refs = ()
        created_at = now[0]

    class LegacyReader:
        def retrieve(self, *args, **kwargs):
            return [LegacyItem()]

    agent.legacy_retriever = LegacyReader()
    route = MessageRoute("fact", "AUTO", "none", True)
    memory, _ = agent.retrieve("g1", "u1", "羽毛球比赛规则是什么？", route)
    assert memory and "羽毛球比赛每局二十一分" in memory["content"]
    assert agent.retrieve("g1", "u1", "羽毛球比赛规则是什么？",
                          MessageRoute("fact", "REQUIRED", "none", True))[0] is None


def test_relative_date_index_finds_old_match_past_newer_records(env):
    _, _, agent, now = env
    old = now[0] - 86400 - 600
    source = [{"kind": "human", "group_id": "g1", "user_id": "admin", "message_id": "old"}]
    target = agent.store._add_note("g1", "昨天足球活动决赛比分三比一", "group_note", source,
                                   status="reviewed", ttl=30*86400)
    for i in range(55):
        note_id = agent.store._add_note("g1", f"今天足球活动记录第{i}", "group_note",
                                        [{"kind": "human", "group_id": "g1", "user_id": "admin", "message_id": f"new-{i}"}],
                                        status="reviewed", ttl=30*86400)
        with agent.store._connection() as db:
            stamp = now[0] - 300 + i
            db.execute("UPDATE notes SET created_at=? WHERE id=?", (stamp, note_id))
            db.execute("UPDATE content_index SET created_at=? WHERE item_id=?", (stamp, note_id))
    with agent.store._connection() as db:
        db.execute("UPDATE notes SET created_at=? WHERE id=?", (old, target))
        db.execute("UPDATE content_index SET created_at=? WHERE item_id=?", (old, target))
    memory, trace = agent.retrieve("g1", "u1", "昨天足球活动比分是什么？",
                                   MessageRoute("memory", "NEVER", "group", False))
    assert memory and "昨天足球活动决赛比分三比一" in memory["content"]
    assert target in trace["memory_ids"]


def test_relative_day_uses_shanghai_midnight_boundary():
    now=datetime(2026,10,16,16,30,tzinfo=timezone.utc).timestamp()
    start,end=_date_window("昨天",now)
    tz=ZoneInfo("Asia/Shanghai")
    assert datetime.fromtimestamp(start,tz).isoformat().startswith("2026-10-16T00:00:00")
    assert datetime.fromtimestamp(end,tz).isoformat().startswith("2026-10-17T00:00:00")


def test_explicit_group_note_is_reported_and_recalled_but_conflict_is_withheld(env):
    _, _, agent, _ = env
    text = "周六晚上八点约好打王者荣耀，实际是否进行待确认。"
    response = agent.command("remember group " + text, "g1", "admin", "source-1", "admin")
    assert "已记录群约定" in response
    with agent.store._connection() as db:
        note = db.execute("SELECT id,status,evidence_status FROM notes WHERE category='group_note'").fetchone()
    assert note["status"] == "reviewed" and note["evidence_status"] == "user_reported"
    memory, _ = retrieve(agent, "之前约好打王者荣耀吗？", MessageRoute("memory", "NEVER", "group", False))
    assert memory and "user_reported" in memory["content"] and "王者荣耀" in memory["content"]
    agent.command("remember group 周日晚上九点约好打王者荣耀，实际是否进行待确认。", "g1", "admin", "source-2", "admin")
    assert retrieve(agent, "之前约好打王者荣耀吗？", MessageRoute("memory", "NEVER", "group", False))[0] is None


def test_why_cache_invalidated_by_policy_change(env):
    _, policy, agent, now = env
    agent.distill([msg("1", "昨天社区完成了活动", stamp=now[0]-50),
                   msg("2", "昨天社区活动已经结束", stamp=now[0]-49)])
    with agent.store._connection() as db:
        item = db.execute("SELECT id FROM episodes").fetchone()[0]
    review(agent, item)
    retrieve(agent, "刚才活动发生什么？")
    assert '"selected": 1' in agent.command(["why"], "g1", "u1")
    policy.revoke("g1", "admin")
    assert "scope_or_policy_changed" in agent.command(["why"], "g1", "u1")


def test_policy_change_during_embedding_rerank_blocks_injection(env,monkeypatch):
    _,policy,agent,now=env
    agent.distill([msg("1","昨天社区完成了活动",stamp=now[0]-50),msg("2","昨天社区活动结束",stamp=now[0]-49)])
    original=agent._semantic_rank
    def revoke_after_rank(gid,version,query,items):
        result=original(gid,version,query,items)
        policy.touch(gid)
        return result
    monkeypatch.setattr(agent,"_semantic_rank",revoke_after_rank)
    memory,trace=retrieve(agent,"昨天社区活动安排是什么？",MessageRoute("memory","NEVER","group",False))
    assert memory is None and trace["selected"]==0 and trace["policy"]=="denied"


def test_restore_skips_malformed_snapshot_rows(env):
    root,policy,agent,now=env
    agent.distill([msg("1","昨天组织了读书活动",stamp=now[0]-100),msg("2","昨天读书活动开始",stamp=now[0]-99)])
    other_root=root/"other"
    other_root.mkdir()
    snapshot=other_root/"malformed.sqlite3"
    assert agent.store.snapshot(snapshot)
    snap=sqlite3.connect(snapshot)
    snap.execute("UPDATE episodes SET content=?,sources=?",("手机号13800138000","not-json"))
    snap.commit();snap.close()
    other=AgentMemory(other_root,policy,clock=lambda:now[0])
    other.store.restore_snapshot(snapshot)
    with other.store._connection() as db:
        assert db.execute("SELECT count(*) FROM episodes").fetchone()[0]==0


def test_tiny_quota_fails_before_initializing_database(env):
    root, policy, _, now = env
    too_small = root / "too-small"
    too_small.mkdir()
    with pytest.raises(OSError):
        AgentMemory(too_small, policy, limit_bytes=1024, clock=lambda: now[0])
    assert not (too_small / "memory.sqlite3").exists()


def test_existing_database_opens_for_reads_when_quota_is_full(env):
    root, policy, agent, now = env
    assert agent.distill([msg("1", "昨天群里举办了桌游活动", stamp=now[0]),
                          msg("2", "大家一起玩了桌游活动", stamp=now[0] + 1)]) == 1
    current_usage = agent.store._usage()
    reopened = AgentMemory(root, policy, limit_bytes=max(1024, current_usage),
                           clock=lambda: now[0])
    now[0] += 86400
    memory, trace = retrieve(reopened, "昨天发生了什么？")
    assert memory and "桌游活动" in memory["content"]
    assert trace["selected"] == 1


def test_concurrent_distill_is_idempotent(env):
    _, _, agent, now = env
    batch = [msg("1", "昨天活动顺利开始了", stamp=now[0]-100),
             msg("2", "昨天活动顺利结束了", stamp=now[0]-99)]
    results=[]
    workers=[threading.Thread(target=lambda: results.append(agent.distill(batch))) for _ in range(8)]
    for worker in workers: worker.start()
    for worker in workers: worker.join(timeout=3)
    assert all(not worker.is_alive() for worker in workers)
    assert sum(results) == 1
    with agent.store._connection() as db:
        assert db.execute("SELECT count(*) FROM episodes").fetchone()[0] == 1

