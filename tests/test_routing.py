import asyncio
import time

import bridge_qq
from qqbot_core.routing import (
    classify_message,
    select_context_messages,
    should_proactively_join_group,
)


class _Sessions:
    def __init__(self, value=None):
        self.value = value or {}
        self.saved = None

    def get(self, key):
        return self.value

    def put(self, key, value):
        self.saved = value


def _bridge_with_final(monkeypatch, store=None, reply="回复", reset_cache=True):
    if reset_cache:
        monkeypatch.setattr(bridge_qq, "_answer_cache_get", lambda *args: None)
    # 2026-10-17：群历史读取要求当前授权有效 + 修订号非空且匹配，
    # 因此共享夹具默认给出授权与修订号。
    monkeypatch.setattr(bridge_qq, "GROUP_STYLE_LEARNING", True)
    bridge = object.__new__(bridge_qq.Bridge)
    bridge.group_policy = type("P", (), {
        "allows": lambda self, gid, feature: True,
        "version": lambda self, gid: 1,
        "revision": lambda self, gid: 1,
    })()
    bridge.sessions = _Sessions(store)
    bridge.style_store = None
    bridge.recent_replies = []
    bridge.last_model_ref = "provider/model"

    async def no_tool_planning(*args, **kwargs):
        raise AssertionError("this route must skip tool planning")

    async def final_reply(system, messages, **kwargs):
        bridge.captured_system = system
        bridge.captured_messages = messages
        bridge.captured_kwargs = kwargs
        return reply, "provider/model"

    bridge._complete_with_tools = no_tool_planning
    bridge._complete = final_reply
    bridge._complete_race = final_reply
    return bridge


def test_required_search_routes():
    for query in (
        "现在北京天气怎么样",
        "塔菲最近直播了什么",
        "你觉得今年世界杯冠军是谁",
        "帮我搜一下最新的 AI 新闻",
    ):
        route = classify_message(query)
        assert route.evidence == "REQUIRED"
        assert route.should_search


def test_never_search_routes_are_not_overridden_by_always_search():
    for query in ("你现在很聪明了吗", "塔菲你现在开心吗"):
        route = classify_message(query, always_search=True)
        assert route.evidence == "NEVER"
        assert not route.should_search


def test_social_meme_reactions_bypass_always_search_but_questions_about_meme_do_not():
    for query in (
        "扣1送地狱火",
        "111大哥真送吗",
        "塔菲你又摸鱼",
    ):
        route = classify_message(query, always_search=True)
        assert route.social_reaction
        assert route.evidence == "NEVER"
        assert not route.should_search

    explanation = classify_message("扣1送地狱火是什么梗", always_search=True)
    assert not explanation.social_reaction
    assert explanation.evidence in ("AUTO", "REQUIRED")
    assert explanation.should_search


def test_time_words_alone_do_not_force_search():
    for query in ("现在", "最近", "最新"):
        route = classify_message(query, always_search=True)
        assert route.evidence == "NEVER"
        assert not route.should_search


def test_model_question_is_local():
    route = classify_message("你现在使用什么模型", always_search=True)
    assert route.evidence == "LOCAL"
    assert route.local_query == "model"
    assert not route.should_search


def test_opinion_does_not_become_search_due_to_always_search():
    route = classify_message("换个话题，你觉得我聪明吗", always_search=True)
    assert route.intent == "opinion"
    assert route.evidence == "NEVER"
    assert route.context == "none"
    assert not route.should_search


def test_agreement_opinion_does_not_become_search_due_to_always_search():
    route = classify_message(
        "你同意DeepSeek是性价比之王吗", always_search=True)
    assert route.intent == "opinion"
    assert route.evidence == "NEVER"
    assert not route.should_search


def test_subjective_analogy_does_not_become_search_due_to_always_search():
    query = "C罗相当于足球界的谁"
    route = classify_message(query, always_search=True)
    assert route.intent == "analogy"
    assert route.evidence == "NEVER"
    assert not route.should_search
    assert bridge_qq._classify_intent(query) == "analogy"


def test_followup_requires_same_user_context():
    route = classify_message("刚才那个继续说")
    assert route.context == "same_user"


def test_context_selection_is_gated_and_sender_scoped():
    messages = [
        {"role": "user", "content": "【QQ群】测试群(1000)\n甲(1111): 我喜欢足球"},
        {"role": "assistant", "content": "甲的回复"},
        {"role": "user", "content": "【QQ群】测试群(1000)\n乙(2222): 讲讲篮球"},
        {"role": "assistant", "content": "乙的回复"},
        {"role": "user", "content": "【QQ群】测试群(1000)\n甲(1111): 那梅西呢"},
        {"role": "assistant", "content": "甲的新回复"},
    ]
    independent = classify_message("换个话题，你觉得我聪明吗")
    followup = classify_message("刚才那个继续说")

    assert select_context_messages(messages, independent, "1111", is_group=True) == []
    selected = select_context_messages(messages, followup, "1111", is_group=True)
    assert len(selected) == 4
    assert all("乙" not in item["content"] for item in selected)


def test_explicit_group_context_can_include_other_speakers():
    route = classify_message("群里刚才大家聊的那个，继续说")
    messages = [{"role": "user", "content": str(i)} for i in range(12)]
    selected = select_context_messages(messages, route, "1111", is_group=True, limit=4)
    assert [item["content"] for item in selected] == ["8", "9", "10", "11"]


def test_group_context_defaults_to_last_100_messages():
    route = classify_message("群里刚才大家聊的那个，继续说")
    messages = [{"role": "user", "content": str(i)} for i in range(130)]
    selected = select_context_messages(messages, route, "1111", is_group=True)
    assert len(selected) == 100
    assert selected[0]["content"] == "30"


def test_live_group_context_reaches_followup_prompt(monkeypatch):
    route = classify_message("群里刚才大家聊的那个，继续说")
    assert route.context == "group"
    bridge = _bridge_with_final(monkeypatch)
    # 短期记忆只在已授权"观察"的群里生效，这里给出授权
    bridge.group_policy = type("P", (), {
        "allows": lambda self, gid, feature: True,
        "version": lambda self, gid: 1,
        "revision": lambda self, gid: 1,
    })()
    bridge.recent_group_context = {
        "group-1": [
            {"role": "user", "content": f"成员: 消息{i}", "speaker_id": str(i),
             "policy_version": 1}
            for i in range(120)
        ]
    }

    asyncio.run(bridge.ask_model(
        "group-1", "当前群消息", "群里刚才大家聊的那个，继续说", "测试用户",
        speaker_id="9999",
    ))

    context = "\n".join(item["content"] for item in bridge.captured_messages)
    assert "消息20" in context
    assert "消息19" not in context
    assert "消息119" in context


def test_ambiguous_text_is_not_searched_by_always_search():
    route = classify_message("哈哈", always_search=True)
    assert route.evidence == "NEVER"
    assert not route.should_search


def test_direct_teasing_question_stays_social_with_always_search():
    route = classify_message("你是牛马吗", always_search=True)
    assert route.social_reaction
    assert route.intent == "chitchat"
    assert route.evidence == "NEVER"
    assert not route.should_search


def test_definition_question_about_meme_still_searches():
    route = classify_message("牛马是什么梗", always_search=True)
    assert route.evidence in ("AUTO", "REQUIRED")
    assert route.should_search


def test_proactive_gate_needs_shared_chat_and_no_direct_target():
    messages = [
        {"role": "user", "speaker_id": "111", "content": "甲: 哈哈"},
        {"role": "user", "speaker_id": "222", "content": "乙: 确实"},
        {"role": "user", "speaker_id": "111", "content": "甲: 真有你的"},
        {"role": "user", "speaker_id": "222", "content": "乙: 你是牛马吗"},
    ]
    assert should_proactively_join_group("你是牛马吗", messages)
    assert not should_proactively_join_group(
        "你是牛马吗", messages, has_directed_reference=True
    )
    assert not should_proactively_join_group("牛马是什么梗", messages)
    # 只有一个人自说自话时不插话
    assert not should_proactively_join_group("你是牛马吗", messages[:1])
    # 两个人在聊就可以自然插一句，不要求凑满 4 条
    assert should_proactively_join_group("你是牛马吗", messages[1:3])
    assert should_proactively_join_group("你是牛马吗", messages[:3])


def test_local_model_status_uses_no_network_or_model(monkeypatch):
    monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", True)
    bridge = _bridge_with_final(monkeypatch)

    async def no_network(*args, **kwargs):
        raise AssertionError("local status must not search")

    monkeypatch.setattr(bridge_qq, "search_web_async", no_network)
    reply = asyncio.run(bridge.ask_model(
        "group-1", "你现在使用什么模型", "你现在使用什么模型",
        "测试用户", speaker_id="1111"))

    assert "provider/model" in reply
    assert "联网" not in reply
    assert bridge.sessions.saved is not None


def test_chitchat_skips_tool_planner_but_keeps_one_final_call(monkeypatch):
    bridge = _bridge_with_final(monkeypatch, reply="哈哈，塔菲在听喵。")
    reply = asyncio.run(bridge.ask_model("private_1", "哈哈", "哈哈", "测试用户"))
    assert reply.startswith("哈哈")
    assert "哈哈" in bridge.captured_messages[-1]["content"]


def test_hard_character_limit_matches_cached_session_and_returned_text(monkeypatch):
    monkeypatch.setattr(bridge_qq, "MAX_REPLY_LEN", 10)
    bridge = _bridge_with_final(monkeypatch, reply="abcdefghijklmnop")

    reply = asyncio.run(bridge.ask_model(
        "private_1", "哈哈", "哈哈", "测试用户"))

    assert len(reply) <= 10
    assert reply.endswith("…")
    assert bridge.sessions.saved["last_answer"] == reply
    assert bridge.recent_replies[-1] == reply


def test_group_short_social_reply_is_brief_no_search_and_uses_no_old_context(monkeypatch):
    monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", True)
    store = {
        "messages": [
            {"role": "user", "speaker_id": "1111", "content": "之前讨论比赛"},
            {"role": "assistant", "speaker_id": "1111", "content": "旧比赛背景"},
        ],
        "summary": "旧话题摘要",
        "last_question": "旧问题",
        "last_answer": "旧回答",
    }
    bridge = _bridge_with_final(monkeypatch, store=store, reply="111大哥真送吗？")

    async def no_search(*args, **kwargs):
        raise AssertionError("social reply must not search")

    monkeypatch.setattr(bridge_qq, "search_web_async", no_search)
    reply = asyncio.run(bridge.ask_model(
        "group-1", "群友甲(1111): 扣1送地狱火", "扣1送地狱火",
        "群友甲", speaker_id="1111"))

    assert "【群聊接话】" not in bridge.captured_system  # 默认路径不再叠加场景风格层
    assert "旧比赛背景" not in str(bridge.captured_messages)
    assert "旧话题摘要" not in bridge.captured_system
    assert bridge.captured_kwargs["max_tokens"] == bridge_qq.DEFAULT_MAX_TOKENS
    assert "联网" not in reply


def test_bot_teasing_question_is_not_explained_or_searched(monkeypatch):
    monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", True)
    bridge = _bridge_with_final(monkeypatch, reply="小菲不当牛马喵")

    async def no_search(*args, **kwargs):
        raise AssertionError("teasing should not search")

    monkeypatch.setattr(bridge_qq, "search_web_async", no_search)
    reply = asyncio.run(bridge.ask_model(
        "group-1", "甲: 你是牛马吗", "你是牛马吗", "甲", speaker_id="1111"
    ))

    assert "【群聊接话】" not in bridge.captured_system  # 默认路径不再叠加场景风格层
    assert "有反应就大声" not in bridge.captured_system  # 表演层已按"少干预"原则删除
    assert "词条" in bridge.captured_system
    assert reply == "小菲不当牛马喵"
    assert "联网" not in reply


def test_group_followup_uses_only_current_speaker_history(monkeypatch):
    store = {
        "messages": [
            {"role": "user", "speaker_id": "1111", "content": "甲(1111): 我刚才问足球",
             "knowledge_revision": 1},
            {"role": "assistant", "speaker_id": "1111", "content": "甲的足球回答",
             "knowledge_revision": 1},
            {"role": "user", "speaker_id": "2222", "content": "乙(2222): 我问篮球",
             "knowledge_revision": 1},
            {"role": "assistant", "speaker_id": "2222", "content": "乙的篮球回答",
             "knowledge_revision": 1},
        ],
        "summary": "群摘要里有乙的篮球信息",
        "summary_index": 0,
        "last_question": "乙的上一问",
        "last_answer": "乙的上一答",
        "last_speaker_id": "2222",
    }
    bridge = _bridge_with_final(monkeypatch, store=store)
    asyncio.run(bridge.ask_model(
        "group-1", "甲(1111): 刚才那个继续说", "刚才那个继续说",
        "甲", speaker_id="1111"))

    prompt_text = "\n".join(str(item.get("content", "")) for item in bridge.captured_messages)
    assert "甲的足球回答" in prompt_text
    assert "乙" not in prompt_text
    assert "群摘要" not in bridge.captured_system


def test_topic_reset_drops_old_context(monkeypatch):
    monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", True)
    store = {
        "messages": [
            {"role": "user", "speaker_id": "1111", "content": "甲之前问过旧话题"},
            {"role": "assistant", "speaker_id": "1111", "content": "旧答案"},
        ],
        "summary": "不应进入当前回答的旧摘要",
        "last_question": "旧问题",
        "last_answer": "旧回答",
    }
    bridge = _bridge_with_final(monkeypatch, store=store)

    async def no_search(*args, **kwargs):
        raise AssertionError("a topic-reset opinion must not search")

    monkeypatch.setattr(bridge_qq, "search_web_async", no_search)
    asyncio.run(bridge.ask_model(
        "group-1", "甲(1111): 换个话题，你觉得我聪明吗",
        "换个话题，你觉得我聪明吗", "甲", speaker_id="1111"))

    prompt_text = "\n".join(str(item.get("content", "")) for item in bridge.captured_messages)
    assert "旧话题" not in prompt_text
    assert "旧摘要" not in bridge.captured_system
    assert "旧问题" not in bridge.captured_system


def test_group_answer_cache_is_sender_scoped():
    assert bridge_qq._answer_cache_key("group-1", "C罗多大", "1111") != (
        bridge_qq._answer_cache_key("group-1", "C罗多大", "2222")
    )


def test_empty_followup_context_keeps_only_current_message(monkeypatch):
    bridge = _bridge_with_final(monkeypatch)
    asyncio.run(bridge.ask_model(
        "group-1", "甲(1111): 刚才那个继续说", "刚才那个继续说",
        "甲", speaker_id="1111"))
    assert len(bridge.captured_messages) == 1
    assert "刚才那个继续说" in bridge.captured_messages[0]["content"]


def test_unrelated_search_results_are_not_injected(monkeypatch):
    monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", False)
    bridge = _bridge_with_final(monkeypatch)
    searched = []

    async def fake_search(query, **kwargs):
        searched.append(query)
        return "【玄武】玄武是中国古代神话中的神兽，与龟蛇形象相关。"

    monkeypatch.setattr(bridge_qq, "search_web_async", fake_search)
    reply = asyncio.run(bridge.ask_model(
        "private_1", "竹知了事件是什么", "竹知了事件是什么", "测试用户"))

    assert len(searched) == 1
    assert "玄武" not in reply
    assert "暂时不确定喵" in reply
    assert not hasattr(bridge, "captured_messages")


def test_answer_cache_hit_skips_search_and_generation(monkeypatch):
    cache_calls = []
    monkeypatch.setattr(
        bridge_qq, "_answer_cache_get",
        lambda key, intent: cache_calls.append((key, intent)) or "缓存答案",
    )
    bridge = _bridge_with_final(monkeypatch, reset_cache=False)

    async def no_search(*args, **kwargs):
        raise AssertionError("cache hit must not search")

    monkeypatch.setattr(bridge_qq, "search_web_async", no_search)
    reply = asyncio.run(bridge.ask_model(
        "private_1", "图赫尔是谁", "图赫尔是谁", "测试用户"))

    assert reply == "缓存答案"
    assert cache_calls[0][1] == "fact"


def test_search_intent_without_subject_is_stopped_before_network(monkeypatch):
    monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", False)
    bridge = _bridge_with_final(monkeypatch)

    async def no_search(*args, **kwargs):
        raise AssertionError("invalid search target must not reach the network")

    monkeypatch.setattr(bridge_qq, "search_web_async", no_search)
    reply = asyncio.run(bridge.ask_model(
        "private_1", "帮我搜一下最新的", "帮我搜一下最新的", "测试用户"))

    assert reply
    assert "帮我搜一下最新的" in bridge.captured_messages[-1]["content"]


def test_search_query_subject_validation():
    assert not bridge_qq._search_query_has_subject("帮我搜一下最新的")
    assert bridge_qq._search_query_has_subject("帮我搜一下最新的 AI 新闻")


def test_realtime_questions_search_and_inject_relevant_results(monkeypatch):
    monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", False)
    bridge = _bridge_with_final(monkeypatch)
    searched = []

    async def fake_search(query, **kwargs):
        searched.append(query)
        if "北京" in query:
            return "【北京天气】今天北京多云，气温约二十度。"
        return "【塔菲直播动态】塔菲最近一次直播聊了游戏和日常。"

    monkeypatch.setattr(bridge_qq, "search_web_async", fake_search)
    for query, expected in (
        ("现在北京天气怎么样", "北京天气"),
        ("塔菲最近直播了什么", "塔菲直播动态"),
    ):
        reply = asyncio.run(bridge.ask_model(
            "private_1", query, query, "测试用户"))
        final_input = "\n".join(
            str(item.get("content", "")) for item in bridge.captured_messages)
        assert expected in final_input
        assert "联网搜过" in reply

    assert len(searched) == 2


def test_ended_world_cup_result_is_retrieved_with_official_source(monkeypatch):
    monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", False)
    monkeypatch.setattr(bridge_qq, "_should_use_single_shot_search", lambda *_: True)
    monkeypatch.setattr(
        bridge_qq,
        "_beijing_now",
        lambda: bridge_qq.datetime(2026, 10, 16, 12, 0, tzinfo=bridge_qq.timezone(
            bridge_qq.timedelta(hours=8))),
    )
    bridge = _bridge_with_final(monkeypatch, reply="西班牙加时1比0击败阿根廷。")
    searched = []

    async def fake_search(query, **kwargs):
        searched.append(query)
        return (
            "【FIFA官方 | fifa.com】2026年世界杯决赛西班牙在加时赛1-0击败阿根廷，赢得冠军。\n"
            "【玄武神话】玄武是中国古代神话中的神兽。"
        )

    monkeypatch.setattr(bridge_qq, "search_web_async", fake_search)
    reply = asyncio.run(bridge.ask_model(
        "private_1", "2026年世界杯冠军是谁", "2026年世界杯冠军是谁", "测试用户"))

    final_input = "\n".join(str(item.get("content", "")) for item in bridge.captured_messages)
    assert len(searched) == 1
    assert "2026世界杯" in searched[0].replace(" ", "")
    assert "fifa.com" in final_input
    assert "西班牙" in final_input and "阿根廷" in final_input
    assert "玄武" not in final_input
    assert "北京时间 2026年10月16日" in bridge.captured_system
    assert "西班牙" in reply


def test_attempted_fact_search_without_relevant_results_requires_uncertainty(monkeypatch):
    monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", False)
    monkeypatch.setattr(bridge_qq, "_should_use_single_shot_search", lambda *_: True)
    bridge = _bridge_with_final(monkeypatch, reply="西班牙是冠军。")
    searched = []
    generated = []
    cached = []

    async def forbidden_generation(*args, **kwargs):
        generated.append(True)
        raise AssertionError("failed external fact lookup must not be guessed")

    bridge._complete = forbidden_generation
    bridge._complete_race = forbidden_generation

    async def no_results(query, **kwargs):
        searched.append(query)
        return ""

    monkeypatch.setattr(bridge_qq, "search_web_async", no_results)
    monkeypatch.setattr(bridge_qq, "_answer_cache_set", lambda *args: cached.append(args))
    reply = asyncio.run(bridge.ask_model(
        "private_1", "2026年世界杯冠军是谁", "2026年世界杯冠军是谁", "测试用户"))

    assert searched
    assert not generated
    assert not cached
    assert "暂时不敢确认" in reply or "暂时无法确认" in reply
    assert "西班牙" not in reply


def test_failed_search_marker_does_not_claim_answer_from_model_memory():
    reply = bridge_qq._append_search_source_marker("暂时无法确认", False, True)
    assert reply.endswith("（联网未找到可靠结果，塔菲暂时不确定喵）")
    assert "凭现有知识答" not in reply


def test_ask_model_deadline_bounds_search_and_skips_late_model_call(monkeypatch):
    monkeypatch.setattr(bridge_qq, "ASK_MODEL_TIMEOUT", 0.03)
    monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", False)
    bridge = _bridge_with_final(monkeypatch)
    calls = []

    async def slow_search(*args, **kwargs):
        calls.append("search")
        await asyncio.sleep(1)
        return ""

    async def late_model(system, messages, deadline=None, **kwargs):
        assert deadline <= __import__("time").monotonic()
        calls.append("model")
        return "", ""

    monkeypatch.setattr(bridge_qq, "search_web_async", slow_search)
    bridge._complete = late_model
    started = __import__("time").monotonic()
    reply = asyncio.run(bridge.ask_model(
        "private_1", "图赫尔是谁", "图赫尔是谁", "测试用户"))
    elapsed = __import__("time").monotonic() - started

    assert calls == ["search"]
    assert elapsed < 0.5
    assert "暂时无法确认" in reply or "暂时不敢确认" in reply


def test_no_search_chat_has_short_deadline_but_fact_keeps_full_budget(monkeypatch):
    monkeypatch.setattr(bridge_qq, "ASK_MODEL_TIMEOUT", 35.0)
    # 闲聊预算由 QQ_CHITCHAT_DEADLINE 控制，这里跟着常量断言，避免调参就挂测试
    monkeypatch.setattr(bridge_qq, "CHITCHAT_DEADLINE_SECONDS", 25.0)
    monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", False)

    chat = _bridge_with_final(monkeypatch)
    asyncio.run(chat.ask_model("private_1", "1433223", "1433223", "测试用户"))
    chat_remaining = chat.captured_kwargs["deadline"] - time.monotonic()
    assert 0 < chat_remaining <= 25.0

    fact = _bridge_with_final(monkeypatch)

    async def relevant_search(*args, **kwargs):
        return "【图赫尔 | fifa.com】图赫尔是德国足球教练，曾执教切尔西。"

    monkeypatch.setattr(bridge_qq, "search_web_async", relevant_search)
    asyncio.run(fact.ask_model("private_1", "图赫尔是谁", "图赫尔是谁", "测试用户"))
    fact_remaining = fact.captured_kwargs["deadline"] - time.monotonic()
    assert fact_remaining > 25.0


# ---------- 明确的执行指令必须真的执行，不能被当成短闲聊接梗 ----------

def test_elided_followup_questions_inherit_group_context():
    """回归：群里讨论正热时问"你觉得呢"，曾因 context=none 只看到四个字而答非所问。"""
    for query in ("你觉得呢", "大家怎么看", "你怎么看", "你呢"):
        route = classify_message(query)
        assert route.context == "group", query
    # 不能把普通短句也当成追问
    for query in ("今天吃了没", "哈哈哈哈", "太野了", "算一下 123+456"):
        assert classify_message(query).context == "none", query


def test_elided_followup_carries_recent_group_history(monkeypatch):
    """省略主语的追问要带上近期群聊，并且不再套"45字内"社交提示。"""
    store = {
        "messages": [
            {"role": "user", "speaker_id": "1111", "content": "甲: 考研报江苏还是江西好",
             "knowledge_revision": 1},
            {"role": "assistant", "speaker_id": "1111", "content": "甲的回答",
             "knowledge_revision": 1},
        ],
        "summary_index": 0,
    }
    bridge = _bridge_with_final(monkeypatch, store=store)
    asyncio.run(bridge.ask_model("group-1", "你觉得呢", "你觉得呢", "甲", speaker_id="1111"))

    prompt_text = "\n".join(str(m.get("content", "")) for m in bridge.captured_messages)
    assert "考研报江苏还是江西好" in prompt_text, "追问没有带上群聊上下文"
    assert "【群聊接话】" not in bridge.captured_system, "追问仍被当作社交接话层"


def test_self_fact_questions_do_not_search():
    """回归：'塔菲你多大了' 曾触发联网，把答案说成"永远的 217 岁"。"""
    for query in ("塔菲你多大了", "小菲几岁了", "塔菲喜欢吃什么",
                  "塔菲的粉丝叫什么", "你是什么设定"):
        route = classify_message(query)
        assert route.evidence == "NEVER", query
        assert route.intent in ("opinion", "chitchat"), query

    # 但他人的年龄仍是事实问题，不能被这条规则吞掉
    assert classify_message("梅西今年多大").evidence != "NEVER"


def test_imperative_tasks_route_to_command_not_social():
    """回归：'背诵圆周率1000位'曾被判成闲聊，只回一句俏皮话而不执行。"""
    for query in (
        "背诵圆周率1000位",
        "证明勾股定理",
        "算一下 123+456",
        "翻译这句话",
        "列出今天的待办",
        "总结一下上面那段",
    ):
        route = classify_message(query)
        assert route.intent == "command", query
        assert route.evidence == "NEVER", query
        assert route.social_reaction is False, query


def test_imperative_task_gets_task_budget_without_social_prompt(monkeypatch):
    bridge = _bridge_with_final(monkeypatch)
    asyncio.run(bridge.ask_model(
        "group-1", "背诵圆周率1000位", "背诵圆周率1000位", speaker_id="u"))
    # 任务预算而不是闲聊预算
    assert bridge.captured_kwargs["max_tokens"] > 1024
    # 任务不应套社交接话层
    assert "【群聊接话】" not in bridge.captured_system
    assert "不解释笑点" not in bridge.captured_system


def test_social_messages_are_not_stolen_by_task_rule():
    """反向保护：社交调侃、玩梗、纯笑声、话题收尾仍走短互动，不被任务规则吞掉。"""
    for query in (
        "你是牛马吗",
        "扣1送地狱火",
        "太野了",
        "今天吃了没",
        "算了不聊了",
        "哈哈哈哈",
    ):
        route = classify_message(query)
        assert route.intent in ("chitchat", "opinion"), query
        assert route.evidence == "NEVER", query


def test_short_casual_group_reply_keeps_social_prompt(monkeypatch):
    """真正的短闲聊仍应使用短互动提示，改动不能把社交场景一起改掉。"""
    monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", False)
    bridge = _bridge_with_final(monkeypatch)
    asyncio.run(bridge.ask_model("group-1", "今天吃了没", "今天吃了没", speaker_id="u"))
    assert "【群聊接话】" not in bridge.captured_system  # 默认路径不再叠加场景风格层
    assert bridge.captured_kwargs["max_tokens"] >= 2000  # 不再限制字数

