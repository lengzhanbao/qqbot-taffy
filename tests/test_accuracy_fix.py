"""Accuracy fixes: factual few-shot, serial final calls, GLM thinking. All mocked."""
import asyncio

import bridge_qq


def test_factual_fewshot_keeps_full_entities():
    text = bridge_qq._build_factual_fewshot("梅西 C罗今年多少岁了")
    assert "梅西" in text
    assert "C罗" in text
    assert "字节跳动" in text


def test_factual_fewshot_empty_for_chitchat():
    assert bridge_qq._build_factual_fewshot("你好") == ""


def test_serial_final_decision():
    assert bridge_qq._should_serial_final("2026世界杯冠军是谁")
    assert bridge_qq._should_serial_final("洛克王国世界草系徽章怎么弄")
    assert not bridge_qq._should_serial_final("你好")


def test_glm_body_does_not_force_disable_thinking(monkeypatch):
    bridge = object.__new__(bridge_qq.Bridge)
    bridge.api_key = "test-key"
    bridge.model_semaphore = None
    monkeypatch.setattr(bridge_qq, "_model_health", {})
    monkeypatch.setattr(bridge_qq, "_provider_cooldown", {})
    bodies = []

    def fake(self, body, api_url, api_key):
        bodies.append(body)
        return ("ok", "ok", [], "", [])

    monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
    asyncio.run(bridge._complete(
        "sys", [], model_refs=["glm/glm-4.5-flash"]))

    assert bodies
    assert "thinking" not in bodies[0]


def test_glm_empty_thinking_retries_with_disabled_thinking(monkeypatch):
    bridge = object.__new__(bridge_qq.Bridge)
    bridge.api_key = "test-key"
    bridge.model_semaphore = None
    monkeypatch.setattr(bridge_qq, "_model_health", {})
    monkeypatch.setattr(bridge_qq, "_provider_cooldown", {})
    bodies = []

    def fake(self, body, api_url, api_key):
        bodies.append(dict(body))
        if len(bodies) == 1:
            return ("", "ok", [], "", [])
        return ("思考后没正文，改用直答", "ok", [], "", [])

    monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
    reply, _ = asyncio.run(bridge._complete(
        "sys", [], model_refs=["glm/glm-4.5-flash"]))

    assert reply == "思考后没正文，改用直答"
    assert len(bodies) == 2
    assert "thinking" not in bodies[0]
    assert bodies[1].get("thinking") == {"type": "disabled"}


def test_personal_preference_is_opinion_not_fact_search():
    assert bridge_qq._classify_intent("你好 你喜欢看什么动漫呢") == "opinion"
    assert bridge_qq._classify_intent("你喜欢看什么动漫呢") == "opinion"
    assert bridge_qq._classify_intent("你最喜欢哪部动漫") == "opinion"


def test_real_fact_question_stays_factual():
    assert bridge_qq._classify_intent("2026世界杯冠军是谁") == "fact"
    assert bridge_qq._classify_intent("梅西C罗今年多少岁") == "fact"


def test_exact_search_skips_local_variant_builder(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("exact search should not build local variants")

    monkeypatch.setattr(bridge_qq, "_build_search_variants", boom)
    monkeypatch.setattr(bridge_qq, "_search_engine", lambda *a, **k: [])

    result = asyncio.run(bridge_qq.search_web_async(
        "梅西C罗年龄", max_results=2, exact=True))
    assert result == ""


def test_free_models_are_prioritized():
    assert bridge_qq.MODELS[0].startswith("opencode_free/")
    assert bridge_qq.MODELS_FACTUAL[0].startswith("opencode_free/")
    assert bridge_qq.MODELS_CASUAL[0].startswith("opencode_free/")


def test_search_strategy_prompt_has_operators_and_synthesis():
    text = bridge_qq.SEARCH_STRATEGY_SYSTEM
    assert "引用消息处理铁律" in text
    assert "禁止把引用内容" in text
    assert "必须执行搜索" in text
    assert "site:" in text
    assert "多个 search_web" in text
    assert "交叉综合" in text
    assert "官方" in text


def test_search_tool_fewshot_encourages_multiple_queries():
    text = bridge_qq.SEARCH_TOOL_FEWSHOT
    assert "玄武口音" in text
    assert "余承东" in text
    assert "plan_search" in text
    assert "玄武口音 华为 问界 梗" in text


def test_tools_include_plan_search_and_search_web():
    names = [tool["function"]["name"] for tool in bridge_qq.TOOLS]
    assert "plan_search" in names
    assert "search_web" in names
    plan = next(tool["function"] for tool in bridge_qq.TOOLS
                if tool["function"]["name"] == "plan_search")
    assert "thought_process" in plan["parameters"]["required"]
    assert "queries" in plan["parameters"]["required"]


def test_search_planner_persona_prefers_short_keywords():
    text = bridge_qq.SEARCH_PLANNER_PERSONA
    assert "信息检索架构师" in text
    assert "拒长句" in text
    assert "多搜几次" in text


def test_search_planning_rules_include_positive_example():
    text = bridge_qq.SEARCH_PLANNING_RULES
    assert "先理解，再拆解" in text
    assert "2026年世界杯 冠军" in text
    assert "plan_search" in text


def test_long_plan_query_can_split():
    parts = bridge_qq._split_planned_query(
        "林俊杰和MC HotDog热狗合作的加油这首歌的完整歌词是什么")
    assert len(parts) >= 2


def test_rule_plan_for_age_query():
    plans = bridge_qq._rule_plan_for_query("C罗今年多少岁")
    assert any("C罗 年龄" in p["query"] for p in plans)
    assert any("出生日期" in p["query"] for p in plans)


def test_rule_plan_for_lyric_query():
    plans = bridge_qq._rule_plan_for_query("林俊杰和热狗合唱的加油歌词是什么")
    assert any("加油" in p["query"] and "歌词" in p["query"] for p in plans)
    assert any("林俊杰" in p["query"] for p in plans)
    assert any("热狗" in p["query"] for p in plans)
    simple = bridge_qq._rule_plan_for_query("加油的歌词是什么")
    assert any("最近你好吗" in p["query"] for p in simple)
    assert any("林俊杰" in p["query"] for p in simple)
    assert any("热狗" in p["query"] for p in simple)


def test_rule_plan_for_that_day_rained_lyric_query():
    plans = bridge_qq._rule_plan_for_query("周杰伦那天下雨了的歌词")
    assert any("那天下雨了" in p["query"] for p in plans)
    assert any("太阳之子" in p["query"] for p in plans)
    assert any("车子缓缓的开" in p["query"] for p in plans)


def test_rule_plan_for_xuanwu_accent():
    plans = bridge_qq._rule_plan_for_query("玄武口音是什么")
    assert any("余承东" in p["query"] and "竹知了" in p["query"] for p in plans)
    assert any("问界" in p["query"] for p in plans)


def test_known_meme_facts_include_xuanwu():
    assert "玄武口音" in bridge_qq._KNOWN_MEME_FACTS
    assert "竹知了" in bridge_qq._KNOWN_MEME_FACTS


def test_known_meme_search_prefers_baidu_suggest():
    raw = (
        "【玄武 中国古代神话中的四大神兽】玄武百科内容\n"
        "【百度建议】余承东竹知了口音对比\n"
    )
    distilled = bridge_qq._distill_search_results(
        raw,
        "玄武口音是什么",
        max_entries=5,
        max_chars=500,
    )
    assert distilled.startswith("百度建议")


def test_any_search_prefers_suggest_and_sogou():
    raw = (
        "【普通百科】普通内容\n"
        "【百度建议】天气 上海 明天\n"
        "【搜狗提示】天气 上海\n"
    )
    distilled = bridge_qq._distill_search_results(
        raw,
        "上海明天天气",
        max_entries=5,
        max_chars=500,
    )
    assert distilled.startswith("百度建议")


def test_lyric_intent_is_not_creative_mode():
    assert bridge_qq._is_lyrics_query("周杰伦那天下雨了的歌词")
    assert not bridge_qq._is_lyrics_query("帮我写一首歌词")
    assert bridge_qq._classify_dialog_mode("周杰伦那天下雨了的歌词") == "lyrics"
    assert bridge_qq._classify_dialog_mode("帮我写一首歌词") == "creative"


def test_lyric_subject_and_reply_suspect_check():
    assert "那天下雨了" in bridge_qq._lyric_subject_terms("周杰伦那天下雨了的歌词")
    assert "加油" in bridge_qq._lyric_subject_terms("林俊杰和热狗合唱的加油歌词是什么")
    assert bridge_qq._lyric_reply_suspect(
        "跟《听见下雨不一样",
        "周杰伦那天下雨了的歌词",
        "",
    )
    assert not bridge_qq._lyric_reply_suspect(
        "《那天下雨了》歌词如下",
        "周杰伦那天下雨了的歌词",
        "那天下雨了",
    )


def test_extract_page_text_removes_nav_script():
    html = (
        "<html><script>bad()</script><body>"
        "<nav>导航</nav><article><p>车子缓缓的开</p></article></body></html>"
    )
    text = bridge_qq._extract_page_text(html)
    assert "车子缓缓的开" in text
    assert "bad()" not in text
    assert "导航" not in text


def test_extract_search_links_prefers_lyric_domains():
    html = (
        '<li class="b_algo"><h2><a href="https://www.example.com/geci">'
        "那天下雨了 歌词</a></h2></li>"
    )
    links = bridge_qq._extract_search_links(html)
    assert links
    assert "example.com/geci" in links[0]["url"]


def test_extract_js_redirect():
    html = '<script>window.location.replace("http://www.22lrc.com/geci/1")</script>'
    assert bridge_qq._extract_js_redirect(html) == "http://www.22lrc.com/geci/1"


def test_tools_include_fetch_page():
    assert any(
        tool.get("function", {}).get("name") == "fetch_page"
        for tool in bridge_qq.TOOLS
    )


def test_enrich_lyrics_search_appends_page_body(monkeypatch):
    async def fake_body(q, max_pages=2, max_links=6):
        return "车子缓缓的开 你慢慢走来"

    monkeypatch.setattr(bridge_qq, "_fetch_lyrics_body", fake_body)
    bridge = object.__new__(bridge_qq.Bridge)
    result = asyncio.run(bridge._enrich_lyrics_search(
        "旧结果",
        "周杰伦那天下雨了的歌词",
        ["那天下雨了 歌词"],
    ))
    assert "【歌词页面正文】" in result
    assert "车子缓缓的开" in result


def test_normalize_plan_uses_rule_fallback_when_empty():
    plans = bridge_qq._normalize_plan_queries([], fallback_text="C罗今年多少岁")
    assert plans
    assert any("C罗 年龄" in p["query"] for p in plans)


def test_search_planned_queries_runs_each_short_query(monkeypatch):
    calls = []

    async def fake_search(q, max_results=10, exact=False):
        calls.append(q)
        return f"【结果】{q}"

    monkeypatch.setattr(bridge_qq, "search_web_async", fake_search)
    result = asyncio.run(bridge_qq._search_planned_queries([
        {"query": "玄武口音 是什么梗", "purpose": "定义"},
        {"query": "玄武口音 华为 问界 梗", "purpose": "关联"},
    ]))
    assert len(calls) == 2
    assert "玄武口音 是什么梗" in result
    assert "玄武口音 华为 问界 梗" in result


def test_source_rank_prefers_authoritative_sources():
    assert bridge_qq._SOURCE_RANK["官方"] >= 9
    assert bridge_qq._SOURCE_RANK["Reuters"] >= 9
    assert bridge_qq._SOURCE_RANK["arXiv"] >= 8
    assert bridge_qq._SOURCE_RANK["PubMed"] >= 8


def test_search_query_removes_quoted_previous_reply():
    text = "歌词呢[引用原消息]（测试用户）: 塔菲之前回复了很长内容"
    assert bridge_qq._clean_search_query(text) == "歌词呢"


def test_search_query_removes_previous_reply_after_bracket_clean():
    text = bridge_qq._clean_question(
        "歌词呢\n[引用原消息]（测试用户）: 塔菲之前回复了很长内容")
    assert bridge_qq._clean_search_query(text) == "歌词呢"


def test_distill_drops_music_page_title_noise():
    raw = "【加油 - 林俊杰&MC HOTDOG 热狗__壹路星空联合之声_单曲在线试听_酷我音乐】酷我音乐页面标题"
    assert bridge_qq._distill_search_results(raw, "林俊杰 加油 歌词") == ""
    raw_kugou = "【加油 - 林俊杰&MC HOTDOG 热狗_酷狗音乐】酷狗音乐页面标题"
    assert bridge_qq._distill_search_results(raw_kugou, "林俊杰 加油 歌词") == ""
