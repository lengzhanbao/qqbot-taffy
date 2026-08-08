"""Accuracy fixes: factual few-shot, serial final calls, GLM thinking. All mocked."""
import asyncio
from unittest.mock import patch

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
    monkeypatch.setattr(bridge_qq, "_drission_search_html", lambda *a, **k: "")

    result = asyncio.run(bridge_qq.search_web_async(
        "梅西C罗年龄", max_results=2, exact=True))
    assert result == ""


def test_free_models_are_prioritized():
    assert bridge_qq.MODELS[0].startswith("opencode_free/")
    assert bridge_qq.MODELS_FACTUAL[0].startswith("opencode_free/")
    assert bridge_qq.MODELS_CASUAL[0].startswith("opencode_free/")


def test_search_strategy_prompt_is_simple_and_prefers_cn_bing():
    text = bridge_qq.SEARCH_STRATEGY_SYSTEM
    assert "需要联网时" in text
    assert "调用搜索工具" in text
    assert "cn.bing" in text
    assert "不要优先百度百科" in text
    assert "按搜索结果回答" in text


def test_search_tool_fewshot_encourages_multiple_queries():
    text = bridge_qq.SEARCH_TOOL_FEWSHOT
    assert "2026世界杯" in text
    assert "plan_search" in text
    assert "2026世界杯 冠军" in text


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
    assert "短关键词" in text


def test_search_planning_rules_include_positive_example():
    text = bridge_qq.SEARCH_PLANNING_RULES
    assert "短词优于长句" in text
    assert "多个 query" in text
    assert "搜索工具" in text


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


def test_meme_required_terms_are_checked():
    missing = bridge_qq._answer_contract_missing(
        "玄武口音是余承东的梗",
        "玄武口音是什么梗",
        "玄武口音 余承东 竹知了",
    )
    assert "竹知了" in missing


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


def test_extract_lyric_song_title():
    assert bridge_qq._extract_lyric_song_title(
        "周杰伦七月的极光歌词") == "七月的极光"
    assert bridge_qq._extract_lyric_song_title(
        "林俊杰和热狗合唱的加油歌词是什么") == "加油"
    assert bridge_qq._extract_lyric_song_title(
        "《那天下雨了》完整歌词") == "那天下雨了"


def test_jayfc_search_links_parses_html(monkeypatch):
    def fake_fetch(url, timeout=5):
        return (
            '<a href="/songs/228">七月的极光 太阳之子</a>'
        )

    monkeypatch.setattr(bridge_qq, "_fetch_html", fake_fetch)
    links = bridge_qq._jayfc_search_links("周杰伦七月的极光歌词")
    assert links
    assert "jayfc.com/songs/228" in links[0]["url"]


def test_looks_like_lyrics_page_rejects_album_intro():
    text = "\n".join(
        f"七月的极光专辑介绍第{i}段，这里都是专辑宣传语，不是歌曲正文。"
        for i in range(12)
    )
    assert not bridge_qq._looks_like_lyrics_page(
        text, "周杰伦七月的极光歌词")


def test_extract_lyrics_block_and_fallback():
    source = (
        "来源：https://jayfc.com/songs/228\n"
        "标题：七月的极光\n"
        "歌词已迁移\n"
        "歌词\n"
        "夕阳落在加油站的玻璃上\n"
        "我们把冒险的地图都带上\n"
        "Aurora in July\n"
        "在我怀里发呆\n"
    )
    block = bridge_qq._extract_lyrics_block(source)
    assert "夕阳落在加油站的玻璃上" in block
    answer = bridge_qq._build_lyrics_fallback_answer(
        "周杰伦七月的极光歌词是什么", source)
    assert "七月的极光" in answer
    assert "夕阳落在加油站的玻璃上" in answer
    assert not bridge_qq._lyric_reply_suspect(
        answer, "周杰伦七月的极光歌词是什么", source)


def test_lyric_fallback_ignores_parenthesis_in_memory():
    source = "歌词\nAurora in July 像我对你的依赖\n"
    memory = "《七月的极光》真实歌词：\"（Aurora in July 像我对你的依赖）\""
    answer = bridge_qq._build_lyrics_fallback_answer(
        "周杰伦《七月的极光》歌词是什么", source, memory)
    assert not bridge_qq._lyric_reply_suspect(
        answer, "周杰伦《七月的极光》歌词是什么", source, memory)


def test_lyric_reply_suspect_checks_memory_key_lines():
    memory = (
        "- 《加油》：林俊杰 & MC HotDog 热狗演唱。真实歌词段落："
        "\"全世界都在逆转 人开始反向思考 / 曾经流过的泪 湿了伤口 就让阳光晒干而褪\""
    )
    bad = "《加油》... 都在逆转 人开始反向思考 ... 就让晒干而褪"
    assert bridge_qq._lyric_reply_suspect(
        bad, "林俊杰和热狗合唱的加油歌词是什么", "", memory)
    good = (
        "《加油》... 全世界都在逆转 人开始反向思考 ... "
        "曾经流过的泪 湿了伤口 就让阳光晒干而褪"
    )
    assert not bridge_qq._lyric_reply_suspect(
        good, "林俊杰和热狗合唱的加油歌词是什么", "", memory)


def test_known_phrase_error_fix_xuanwu():
    assert bridge_qq._fix_known_phrase_errors(
        "不是玄武神，也不是南京玄武区", "玄武口音是什么梗") == \
        "不是玄武神兽，也不是南京玄武区"
    assert "费兰·托雷斯" in bridge_qq._fix_known_phrase_errors(
        "干·托雷斯打进制胜球", "2026世界杯冠军是谁")
    assert "5.6万" in bridge_qq._fix_known_phrase_errors(
        "但.6万条二创", "竹知了是什么梗")


def test_numeric_fact_preservation_and_prompt():
    assert "永雏塔菲" in bridge_qq.SYSTEM_PROMPT
    source = "鸿蒙智行监测到5.6万余条，实际投诉171条，下架144条"
    assert "5.6万余条" in bridge_qq._ensure_numeric_facts_preserved(
        "但.6万余条二创", source)
    assert "171条" in bridge_qq._ensure_numeric_facts_preserved(
        "实际投诉71条", source)
    assert "144条" in bridge_qq._ensure_numeric_facts_preserved(
        "下架44条", source)
    assert bridge_qq.MAX_PAGE_TEXT_CHARS >= 12000
    assert bridge_qq.MAX_LYRIC_CONTEXT_CHARS >= 24000


def test_unsupported_numeric_phrases_detect_hallucination():
    unsupported = bridge_qq._unsupported_numeric_phrases(
        "201EDG.Y开启职业生涯",
        "2001年10月27日出生，2019年加入EDG.Y",
    )
    assert "201" in unsupported
    assert bridge_qq._unsupported_numeric_phrases(
        "2019年加入EDG.Y",
        "2019年加入EDG.Y",
    ) == []


def test_entity_fact_preservation_fixes_jax():
    reply = bridge_qq._ensure_entity_facts_preserved(
        "青钢影人柱力，某选手名残缺这些英雄玩得贼溜",
        "贾克斯、青钢影人柱力、剑姬",
    )
    assert "贾克斯" in reply
    unchanged = bridge_qq._ensure_entity_facts_preserved(
        "贾玲导演的《你好，李焕英》",
        "贾克斯",
    )
    assert "贾玲" in unchanged


def test_entity_fact_preservation_fixes_song_names():
    reply = bridge_qq._ensure_entity_facts_preserved(
        "邓紫棋《年之外》，陈奕《富士山下》，林俊杰《修炼》",
        "邓紫棋《光年之外》，陈奕迅《富士山下》，林俊杰《修炼爱情》",
    )
    assert "光年之外" in reply
    assert "陈奕迅" in reply
    assert "修炼爱情" in reply


def test_source_phrase_preservation_fixes_jiejie():
    reply = bridge_qq._ensure_source_phrases_preserved(
        "201EDG.Y开启职业生涯，同年G一队",
        "2019年加入EDG.Y，同年上调至EDG一队",
    )
    assert "2019年加入EDG.Y" in reply
    assert "同年上调至EDG一队" in reply


def test_song_recommendation_exact_names():
    assert bridge_qq._is_song_recommendation_query("我要听歌喵")
    assert bridge_qq._is_song_recommendation_query("推荐几首新歌")
    reply = bridge_qq._ensure_source_phrases_preserved(
        "陈奕《富士山下》，林俊杰《修炼》，邓紫棋《年之外》",
        bridge_qq._song_recommendation_source(),
    )
    assert "陈奕迅" in reply
    assert "修炼爱情" in reply
    assert "光年之外" in reply
    assert bridge_qq._song_recommendation_broken(
        "某歌曲名残缺")
    assert bridge_qq._song_recommendation_broken(
        "这十首涵盖抒、摇滚、民谣")
    assert not bridge_qq._song_recommendation_broken(
        "邓紫棋《光年之外》、陈奕迅《十年》")
    fallback = bridge_qq._song_recommendation_fallback_reply()
    assert "《泡沫》" in fallback
    assert "陈奕迅《十年》" in fallback
    assert "抒情" in fallback
    assert not bridge_qq._song_recommendation_broken(fallback)
    assert bridge_qq._sanitize_history_content(
        "某歌曲名残缺 陈奕《十年》") == fallback


def test_truncate_only_at_sentence_boundary():
    assert bridge_qq._truncate_at_sentence(
        "第一句。第二句还没写完", 5) == "第一句。"
    assert bridge_qq._truncate_at_sentence(
        "没有句号的长内容没有句号的长内容", 5) == \
        "没有句号的长内容没有句号的长内容"


def test_search_query_clean_and_tool_residue():
    q = bridge_qq._build_search_query(
        "最近美国通过的对TikTok的法案核心内容是什么")
    assert "最近" not in q
    assert "TikTok" in q and "法案" in q
    variants = bridge_qq._build_search_variants(
        "最近美国通过的对TikTok的法案核心内容是什么")
    assert variants[0] == q
    assert bridge_qq._looks_like_tool_call(
        '{"queries": ["日本央行 加息"]}')
    assert bridge_qq._looks_like_tool_call(
        '{"query": "NVIDIA market cap", "recency_days": 30}')
    assert bridge_qq._is_weak_reply(
        "塔菲再搜一下最新数据！", "英伟达市值")


def test_normalize_entity_spacing():
    assert bridge_qq._normalize_entity_spacing(
        "洛克 王国：世界") == "洛克王国：世界"
    assert bridge_qq._normalize_entity_spacing(
        "《 洛克 王国：世界》") == "《洛克王国：世界》"
    assert bridge_qq._normalize_entity_spacing(
        "洛克 王国世界WIKI") == "洛克王国世界WIKI"


def test_rule_plan_for_tiktok_and_bank():
    tiktok = bridge_qq._rule_plan_for_query(
        "最近美国通过的对TikTok的法案核心内容是什么")
    assert any("divestment" in p["query"] for p in tiktok)
    bank = bridge_qq._rule_plan_for_query(
        "日本央行在2024年3月做出了什么历史性的货币政策决定")
    assert any("负利率" in p["query"] for p in bank)


def test_recommendation_adds_entity_works_search():
    query = "请你推荐几本东野圭吾的书"
    assert bridge_qq._extract_recommendation_entity(query) == "东野圭吾"
    variants = bridge_qq._build_search_variants(query)
    assert query in variants
    assert "东野圭吾 作品" in variants
    plans = bridge_qq._rule_plan_for_query(query)
    assert any(p["query"] == query for p in plans)
    assert any("东野圭吾 作品" in p["query"] for p in plans)
    assert not any("百科" in p["query"] for p in plans)


def test_recommendation_search_uses_douban_and_avoids_baike():
    query = "推荐几部好看的电影"
    plans = bridge_qq._recommendation_search_plans(query)
    assert any("豆瓣 电影 Top 250" in p["query"] for p in plans)
    assert not any("百度百科" in p["query"] for p in plans)
    variants = bridge_qq._build_search_variants(query)
    assert any("豆瓣 电影 Top 250" in v for v in variants)
    assert bridge_qq._skip_recommendation_suggestions(query)
    assert not bridge_qq._skip_recommendation_suggestions(
        "介绍一下东野圭吾")


def test_book_recommendation_fallback_for_author():
    reply = bridge_qq._book_recommendation_fallback_reply("东野圭吾")
    assert "《白夜行》" in reply
    assert "《嫌疑人X的献身》" in reply
    assert "《解忧杂货店》" in reply
    assert bridge_qq._book_recommendation_broken("塔菲没查到", "东野圭吾")
    assert not bridge_qq._book_recommendation_broken(
        "白夜行、嫌疑人X的献身、解忧杂货店、恶意",
        "东野圭吾",
    )


def test_generic_recommendation_fallback():
    assert bridge_qq._recommendation_category(
        "推荐几个好看的电影") == "movie"
    with patch("bridge_qq._search_douban_movies", return_value=["《肖申克的救赎》（豆瓣 9.7）"]):
        reply = bridge_qq._recommendation_fallback_reply(
            "推荐几个好看的电影")
    assert "《" in reply and "豆瓣" in reply
    assert bridge_qq._recommendation_should_fallback(
        "搜出来都是推荐系统教程，没查到电影", "推荐几个好看的电影")
    sanitized = bridge_qq._sanitize_history_content(
        "搜出来都是推荐系统教程，没查到东野圭吾的书", "assistant")
    assert "《白夜行》" in sanitized


def test_person_bio_fallback_for_faker():
    assert bridge_qq._person_bio_key("faker介绍一下呢") == "faker"
    assert bridge_qq._person_bio_broken(
        "某处错字T1中单Faker某处夸张描述，1996年5月7日6年7月8日",
        "faker",
    )
    reply = bridge_qq._person_bio_fallback_reply("faker")
    assert "李相赫" in reply
    assert "1996" in reply


def test_person_bio_fallback_for_meiko():
    assert bridge_qq._person_bio_key("介绍一下meiko") == "meiko"
    assert bridge_qq._person_bio_broken(
        "M跑题了meiko，1998年6月6日",
        "meiko",
    )
    assert bridge_qq._looks_garbled_reply(
        "某段跑题的残缺回答：M跑题了meiko")
    sanitized = bridge_qq._sanitize_history_content(
        "某段跑题的残缺回答：M跑题了meiko，1998年6月6日",
        "assistant",
    )
    assert "田野" in sanitized


def test_refine_polluted_history_keeps_info():
    dirty = (
        "明凯（游戏ID：Clearlove），2019年9月12日8年7月27日5年MSI，"
        "EDG骑士团作为CN无畏俱乐部官方网站"
    )
    refined = bridge_qq._refine_polluted_history(dirty)
    assert "明凯" in refined
    assert "Clearlove" in refined
    assert "MSI" in refined
    assert "2019年9月12日8年" not in refined
    assert "塔菲没查到" not in refined


def test_sanitize_history_strips_old_prompt_noise():
    dirty = (
        "【QQ群】测试群(1) 测试用户(900000001): 神在哪 "
        "<style>你是永雏塔菲，塔菲模式最高优先级。</style> "
        "<tone>先准确再串/抽象。</tone> "
        "事实/知识类问题：有搜索就以搜索为准；如果搜索结果没有答案或不知道，"
        "必须直接说塔菲没查到。 有搜索结果时，以搜索结果为准直接回答。"
    )
    cleaned = bridge_qq._sanitize_history_content(dirty, "user")
    assert "神在哪" in cleaned
    assert "<style>" not in cleaned
    assert "<tone>" not in cleaned
    assert "事实/知识类问题" not in cleaned
    assert "有搜索结果时" not in cleaned


def test_self_check_trigger_for_garbled_person_reply():
    assert bridge_qq._looks_garbled_reply(
        "某处错字T1中单Faker某处夸张描述，1996年5月7日6年7月8日")
    assert bridge_qq._needs_self_check(
        "某处错字T1中单Faker某处夸张描述，1996年5月7日6年7月8日",
        "faker介绍一下呢",
    )
    assert bridge_qq._has_repeated_long_phrase(
        "EDG骑士团作为CN无畏俱乐部官方网站EDG骑士团作为CN无畏俱乐部官方网站")
    assert bridge_qq._needs_self_check(
        "Viper外号“”！2020年12月15日EDG",
        "那viper呢",
        "厂长明凯是EDG传奇打野",
    )


def test_answer_contract_generic_checks():
    assert "TikTok" in bridge_qq._required_entity_terms(
        "最近美国通过的对TikTok的法案核心内容是什么")
    assert bridge_qq._answer_contract_missing(
        "塔菲没查到",
        "最近美国通过的对TikTok的法案核心内容是什么",
        "TikTok 法案",
    )
    assert not bridge_qq._answer_contract_missing(
        "小米SU7起售价21.59万，预订火爆",
        "小米SU7发布后的市场表现如何？起售价是多少？",
        "小米SU7 21.59万 预订",
    )
    assert "16座" in bridge_qq._source_required_numbers(
        "美国11座，加拿大2座，墨西哥3座，共16座")
    assert "48支" in bridge_qq._source_required_numbers("参赛球队48支")
    assert "39岁" in bridge_qq._source_required_numbers(
        "2024年梅西39岁，C罗41岁")
    assert "1985.02" not in bridge_qq._source_required_numbers(
        "1985.02 C罗41岁 2岁")
    assert "2岁" not in bridge_qq._source_required_numbers(
        "1985.02 C罗41岁 2岁")
    assert "4万亿" in bridge_qq._source_required_numbers(
        "英伟达市值突破4万亿美元")
    assert "数字48支" in bridge_qq._answer_contract_missing(
        "2026世界杯美国、加拿大、墨西哥联合举办",
        "2026世界杯举办国和参赛球队有多少支？",
        "美国 加拿大 墨西哥 48支",
    )
    completed = bridge_qq._complete_missing_fields(
        "洛克王国世界草系徽章怎么拿",
        ["洛克王国世界草系徽章"],
        "洛克王国世界草系徽章",
    )
    assert "洛克王国世界草系徽章" in completed
    missing = bridge_qq._answer_contract_missing(
        "洛克王国",
        "洛克王国世界呢",
        "洛克王国世界是腾讯魔方工作室开发的精灵游戏",
    )
    assert not any(
        term.startswith("数字") or term.startswith("日期")
        for term in missing
    )
    colon_missing = bridge_qq._answer_contract_missing(
        "洛克王国",
        "洛克王国世界呢",
        "《洛克王国：世界》是腾讯魔方工作室开发的精灵游戏",
    )
    assert any("世界" in term for term in colon_missing)


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


def test_normalize_plan_does_not_rule_fallback_when_empty():
    plans = bridge_qq._normalize_plan_queries([])
    assert plans == []
    assert asyncio.run(bridge_qq._search_planned_queries([])) == ""


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


def test_world_cup_search_query_keeps_event_year_attached():
    assert bridge_qq._build_search_query(
        "2026世界杯冠军是谁") == "2026世界杯 冠军"
    assert bridge_qq._build_search_query(
        "2026年世界杯冠军是谁") == "2026世界杯 冠军"
    plans = bridge_qq._rule_plan_for_query("2026世界杯冠军是谁")
    assert any(p["query"] == "2026世界杯 冠军" for p in plans)
    assert not any("2026年" in p["query"] for p in plans)


def test_generic_fallback_query_uses_llm_friendly_meme_query():
    assert bridge_qq._generic_fallback_query("介绍一下曼波") == "曼波"
    assert bridge_qq._generic_fallback_query(
        "我是说的梗曼波小马") == "曼波小马 梗"
    assert bridge_qq._needs_search("介绍一下曼波")
    assert bridge_qq._needs_search("我是说的梗曼波小马")


def test_search_source_marker_is_appended():
    assert bridge_qq._append_search_source_marker(
        "答案是西班牙", True).endswith("（联网搜过喵）")
    assert bridge_qq._append_search_source_marker(
        "塔菲没搜到可靠结果", False, True).endswith(
        "（联网搜了但没查到，塔菲凭现有知识答的喵）")
    assert bridge_qq._append_search_source_marker(
        "塔菲凭知识答", False).endswith(
        "（没联网，塔菲凭现有知识答的喵）")


def test_distill_drops_generic_year_pages_for_champion_query():
    raw = "\n".join([
        "【2026 年_百度百科】联合国决议本年为可持续发展志愿者国际年。",
        "【2026 年大事、要事、重要节日一览表（附放假安排）】2026年共有9次连假。",
        "【2026 年世界杯赛程_ 2026 年美加墨世界杯】2026年世界杯赛程报道。",
        "【2026世界杯最终大结局！48国完整排名出炉：西班牙封神】"
        "决赛终极结局：西班牙加时赛绝杀1-0战胜阿根廷，时隔16年夺冠。",
    ])
    distilled = bridge_qq._distill_search_results(
        raw,
        "2026世界杯冠军是谁",
        max_entries=5,
        max_chars=1000,
    )
    assert "年_百度百科" not in distilled
    assert "放假安排" not in distilled
    assert "世界杯赛程" not in distilled
    assert "西班牙" in distilled


def test_answer_contract_does_not_invent_missing_fields():
    missing = bridge_qq._answer_contract_missing(
        "西班牙1比0阿根廷，夺得2026世界杯冠军。",
        "2026世界杯冠军是谁",
        "2026世界杯冠军是西班牙，决赛1比0战胜阿根廷。",
    )
    assert not any(
        term.startswith("数字") or term.startswith("日期")
        or term.startswith("无来源数字")
        for term in missing
    )
    assert not bridge_qq._unsupported_numeric_phrases(
        "塔菲查到20",
        "2026世界杯 决赛 西班牙",
    )
    stripped = bridge_qq._strip_suggestion_lines(
        "【百度建议】2026世界杯冠军权威分析\n"
        "【2026世界杯最终大结局】西班牙1比0阿根廷夺冠。"
    )
    assert "百度建议" not in stripped
    assert "西班牙" in stripped


def test_extract_tool_calls_from_content_text():
    text = (
        '```json\n'
        '{"thought_process":"拆解问题","queries":['
        '{"query":"2026世界杯 冠军","purpose":"冠军"}]}\n'
        '```'
    )
    calls = bridge_qq._extract_tool_calls_from_text(text)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "plan_search"
    assert "2026世界杯 冠军" in calls[0]["function"]["arguments"]
    assert not bridge_qq._looks_like_tool_call(
        "塔菲用search_web搜了一下")


def test_clean_date_concat_splits_adjacent_full_dates():
    cleaned = bridge_qq._clean_date_concat(
        "1996年5月7日1996年7月8日2019年EDG")
    assert "1996年5月7日，1996年7月8日，2019年EDG" in cleaned
