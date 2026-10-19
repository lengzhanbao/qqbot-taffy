"""抽象等级分类测试：L0(敏感/降档) > L1(事实) > L2(闲聊/抽象) > L3(显式抽象+开关)。"""
import asyncio

import bridge_qq


def _set_style(monkeypatch, level=2, allow_l3=False):
    monkeypatch.setattr(bridge_qq, "STYLE_LEVEL", level)
    monkeypatch.setattr(bridge_qq, "ALLOW_L3", allow_l3)
    monkeypatch.setattr(bridge_qq, "DISABLE_ABSTRACT_ON_SENSITIVE", True)


class TestStyleControlSensitive:
    def test_sensitive_force_l0(self, monkeypatch):
        _set_style(monkeypatch, level=3, allow_l3=True)
        level, sensitive, playful = bridge_qq._classify_style_control("聊聊政治")
        assert level == 0
        assert sensitive
        assert not playful

    def test_sensitive_beats_playful(self, monkeypatch):
        _set_style(monkeypatch, level=3, allow_l3=True)
        level, sensitive, _ = bridge_qq._classify_style_control("来点抽象的政治锐评")
        assert level == 0
        assert sensitive


class TestStyleControlDialDown:
    def test_dial_down_force_l0(self, monkeypatch):
        _set_style(monkeypatch, level=3, allow_l3=True)
        level, sensitive, _ = bridge_qq._classify_style_control("别抽象，正常点")
        assert level == 0
        assert not sensitive

    def test_serious_force_l0(self, monkeypatch):
        _set_style(monkeypatch, level=3, allow_l3=True)
        level, _, _ = bridge_qq._classify_style_control("严肃点回答")
        assert level == 0

    def test_style_level_zero_force_l0(self, monkeypatch):
        _set_style(monkeypatch, level=0, allow_l3=True)
        level, _, _ = bridge_qq._classify_style_control("来点抽象的")
        assert level == 0


class TestStyleControlFact:
    def test_fact_default_l2_is_l1(self, monkeypatch):
        _set_style(monkeypatch, level=2)
        level, _, _ = bridge_qq._classify_style_control("梅西今年多大")
        assert level == 1

    def test_time_sensitive_default_l2_is_l1(self, monkeypatch):
        _set_style(monkeypatch, level=2)
        level, _, _ = bridge_qq._classify_style_control("今天有什么新闻")
        assert level == 1

    def test_command_default_l2_is_l1(self, monkeypatch):
        _set_style(monkeypatch, level=2)
        level, _, _ = bridge_qq._classify_style_control("帮我生成一份计划")
        assert level == 1

    def test_fact_level_one_keeps_l1(self, monkeypatch):
        _set_style(monkeypatch, level=1)
        level, _, _ = bridge_qq._classify_style_control("豆包是什么")
        assert level == 1

    def test_yin_yang_shi_game_is_not_playful(self, monkeypatch):
        _set_style(monkeypatch, level=2)
        level, _, playful = bridge_qq._classify_style_control("阴阳师怎么抽卡")
        assert level == 1
        assert not playful


class TestBotSelfAssessment:
    def test_self_assessment_is_not_a_search_fact(self, monkeypatch):
        query = "你现在很聪明了吗"
        monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", True)

        assert bridge_qq._is_bot_self_assessment_query(query)
        assert bridge_qq._classify_intent(query) == "opinion"
        assert not bridge_qq._is_factual(query)
        assert not bridge_qq._should_search(query)
        assert not bridge_qq._should_attempt_generic_search(query)
        assert not bridge_qq._should_use_single_shot_search(query)

        monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", False)
        assert bridge_qq._should_search("2026世界杯冠军是谁")

    def test_ask_model_keeps_self_assessment_out_of_search(self, monkeypatch):
        _set_style(monkeypatch, level=2)
        monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", True)
        monkeypatch.setattr(bridge_qq, "_answer_cache_get", lambda *a: None)
        captured = {}

        class Sessions:
            def get(self, key):
                return {}

            def put(self, key, value):
                pass

        async def fake_complete_with_tools(*args, **kwargs):
            raise AssertionError("self-assessment must skip tool planning")

        async def fake_complete_race(system, messages, **kwargs):
            captured["final_system"] = system
            return "塔菲还得继续练习喵。", "test-model"

        async def fail_search(*args, **kwargs):
            raise AssertionError("self-assessment query must not search")

        monkeypatch.setattr(bridge_qq, "search_web_async", fail_search)
        bridge = object.__new__(bridge_qq.Bridge)
        bridge.sessions = Sessions()
        bridge.style_store = None
        bridge.recent_replies = []
        bridge._complete_with_tools = fake_complete_with_tools
        bridge._complete_race = fake_complete_race

        reply = asyncio.run(bridge.ask_model(
            "group-1",
            "问题：你现在很聪明了吗",
            "你现在很聪明了吗",
            "测试用户",
        ))

        # 2026-10-17：移除"机器人自我评价"模式提示（它同时带"最多两句"字数限制）。
        # 身份真实性由人格文件的硬边界覆盖，不再靠模式指令。
        assert "机器人自我评价" not in captured["final_system"]
        assert reply.startswith("塔菲还得继续练习")


class TestStyleControlChitchat:
    def test_chitchat_default_l2_is_l2(self, monkeypatch):
        _set_style(monkeypatch, level=2)
        level, _, _ = bridge_qq._classify_style_control("你觉得华为怎么样")
        assert level == 2

    def test_opinion_default_l2_is_l2(self, monkeypatch):
        _set_style(monkeypatch, level=2)
        level, _, _ = bridge_qq._classify_style_control("你怎么看bin哥")
        assert level == 2

    def test_short_social_prompt_prefers_generic_emoji_without_group_emoji_leak(self, monkeypatch):
        _set_style(monkeypatch, level=2)

        class StyleStore:
            def get_group_profile(self, gid):
                return {"per_user": {"user-1": {"emojis": {"😎": 4}}}}

        prompt = bridge_qq._build_response_style_prompt(
            "扣1送地狱火", "group-1", 4, StyleStore(), "user-1",
            social_reaction=True,
        )
        assert "【群聊接话】" in prompt
        assert "优先一句" not in prompt and "约45字内" not in prompt  # 字数限制已删除
        assert "不补造人物、比赛、地点或事件背景" in prompt
        assert "优先自然带一个贴题表情" not in prompt  # 强制表情要求已删除
        assert "😎" not in prompt

    def test_analogy_default_l2_is_l2(self, monkeypatch):
        _set_style(monkeypatch, level=2)
        level, _, _ = bridge_qq._classify_style_control("华为类比C罗")
        assert level == 2

    def test_unmarked_chitchat_statement_uses_l2(self, monkeypatch):
        _set_style(monkeypatch, level=2)
        level, _, _ = bridge_qq._classify_style_control("这也太离谱了")
        assert level == 2

    def test_factual_question_stays_l1_in_chitchat_scene(self, monkeypatch):
        _set_style(monkeypatch, level=2)
        level, _, _ = bridge_qq._classify_style_control("梅西今年多大")
        assert level == 1


class TestStyleControlComfort:
    def test_comfort_mode_overrides_explicit_playfulness(self, monkeypatch):
        _set_style(monkeypatch, level=3, allow_l3=True)
        level, _, playful = bridge_qq._classify_style_control(
            "我最近很烦，来点抽象的")
        assert level == 0
        assert playful

    def test_comfort_prompt_has_no_persona_when_disabled(self):
        prompt = bridge_qq._dialog_mode_prompt("comfort", taffy_enabled=False)
        assert "接住情绪" in prompt
        assert "塔菲" not in prompt


class TestStyleControlPlayful:
    def test_playful_default_l2_raises_to_l2(self, monkeypatch):
        _set_style(monkeypatch, level=2)
        level, _, playful = bridge_qq._classify_style_control("抽象一点")
        assert level == 2
        assert playful

    def test_playful_l3_allowed(self, monkeypatch):
        _set_style(monkeypatch, level=3, allow_l3=True)
        level, _, playful = bridge_qq._classify_style_control("说点狠的")
        assert level == 3
        assert playful

    def test_playful_l3_not_allowed_caps_l2(self, monkeypatch):
        _set_style(monkeypatch, level=3, allow_l3=False)
        level, _, playful = bridge_qq._classify_style_control("抽象一点")
        assert level == 2
        assert playful

    def test_playful_l1_stays_l1(self, monkeypatch):
        _set_style(monkeypatch, level=1)
        level, _, playful = bridge_qq._classify_style_control("抽象一点")
        assert level == 1
        assert playful

    def test_explicit_abstract_prompt_uses_group_emojis(self, monkeypatch):
        _set_style(monkeypatch, level=2)
        monkeypatch.setattr(bridge_qq, "GROUP_STYLE_LEARNING", True)

        class StyleStore:
            def get_group_profile(self, gid):
                assert gid == "group-1"
                return {
                    "per_user": {
                        "user-1": {"emojis": {"😎": 4, "🐢": 8, "🔥": 7}}
                    },
                    "stats": {"emojis": {"🤡": 5}},
                }

        prompt = bridge_qq._build_response_style_prompt(
            "今天来点抽象的",
            "group-1",
            history_len=6,
            style_store=StyleStore(),
            speaker_id="user-1",
        )
        assert "明确抽象请求" in prompt
        assert "本轮可尝试" in prompt
        assert "🤡" in prompt
        assert "😎" not in prompt
        assert "🐢" not in prompt and "🔥" not in prompt

    def test_fact_or_dial_down_does_not_inject_emojis(self, monkeypatch):
        _set_style(monkeypatch, level=2)
        monkeypatch.setattr(bridge_qq, "GROUP_STYLE_LEARNING", True)

        class StyleStore:
            def get_group_profile(self, gid):
                return {
                    "per_user": {"user-1": {"emojis": {"😎": 4}}},
                    "stats": {"emojis": {"😎": 4}},
                }

        factual = bridge_qq._build_response_style_prompt(
            "梅西今年多少岁", "group-1", 3, StyleStore(), "user-1")
        dialed_down = bridge_qq._build_response_style_prompt(
            "别抽象，正常回答", "group-1", 3, StyleStore(), "user-1")
        assert "😎" not in factual
        assert "😎" not in dialed_down
        assert "明确抽象请求" not in dialed_down

    def test_ask_model_injects_abstract_style_prompt(self, monkeypatch):
        _set_style(monkeypatch, level=2)
        # 本用例验证的是旧的 L0-L3 抽象控制（回滚路径），需显式打开旧风格层。
        monkeypatch.setattr(bridge_qq, "LEGACY_STYLE_LAYERS", True)
        monkeypatch.setattr(bridge_qq, "GROUP_STYLE_LEARNING", True)
        monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", False)
        monkeypatch.setattr(bridge_qq, "_answer_cache_get", lambda *a: None)
        captured = {}

        class Sessions:
            def get(self, key):
                return {}

            def put(self, key, value):
                pass

        class StyleStore:
            def get_group_profile(self, gid):
                return {
                    "per_user": {"user-1": {"emojis": {"😎": 4}}},
                    "stats": {"emojis": {"😎": 4}},
                }

        async def fake_complete_with_tools(system, messages, **kwargs):
            captured["system"] = system
            return [], "塔菲觉得这波很抽象喵", "test-model", []

        async def fake_complete_race(system, messages, **kwargs):
            captured["system"] = system
            return "\u5854\u83f2\u89c9\u5f97\u8fd9\u6ce2\u5f88\u62bd\u8c61\u55b5", "test-model"

        bridge = object.__new__(bridge_qq.Bridge)
        bridge.sessions = Sessions()
        bridge.style_store = StyleStore()
        from qqbot_core.group_policy import GroupPolicy
        bridge.group_policy = GroupPolicy(["test-admin"])
        bridge.group_policy.grant("group-1", "test-admin", {"style_learning"}, "mock-notice")
        bridge.recent_replies = []
        bridge._complete_with_tools = fake_complete_with_tools
        bridge._complete_race = fake_complete_race
        reply = asyncio.run(bridge.ask_model(
            "group-1",
            "问题：今天来点抽象的",
            "今天来点抽象的",
            "测试用户",
            speaker_id="user-1",
        ))
        assert "明确抽象请求" in captured["system"]
        assert "【对话模式：闲聊】" in captured["system"]
        assert "用 taffy / 塔菲 / 小菲自称" in captured["system"]
        assert "保持塔菲语气" in captured["system"]
        assert "😎" in captured["system"]
        assert "塔菲觉得" in reply

    def test_ask_model_taffy_off_uses_neutral_prompt_and_marker(self, monkeypatch):
        monkeypatch.setattr(bridge_qq, "TAFFY_FLAVOR", False)
        monkeypatch.setattr(bridge_qq, "STYLE_LEVEL", 0)
        monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", False)
        monkeypatch.setattr(bridge_qq, "_answer_cache_get", lambda *a: None)
        captured = {}

        class Sessions:
            def get(self, key):
                return {}

            def put(self, key, value):
                pass

        async def fake_complete_with_tools(system, messages, **kwargs):
            captured["system"] = system
            return [], "你好呀", "test-model", []

        async def fake_complete_race(system, messages, **kwargs):
            captured["system"] = system
            return "\u4f60\u597d\u5440", "test-model"

        bridge = object.__new__(bridge_qq.Bridge)
        bridge.sessions = Sessions()
        bridge.style_store = None
        bridge.recent_replies = []
        bridge._complete_with_tools = fake_complete_with_tools
        bridge._complete_race = fake_complete_race
        reply = asyncio.run(bridge.ask_model(
            "group-1", "问题：你好呀", "你好呀", "测试用户"))

        assert bridge_qq.NEUTRAL_SYSTEM_PROMPT in captured["system"]
        assert "永雏塔菲" not in captured["system"]
        assert "保持塔菲语气" not in captured["system"]
        assert reply == "你好呀"
        assert "塔菲" not in reply and "喵" not in reply


class TestTaffyStyled:
    def test_identity_and_emotion_passes(self):
        assert bridge_qq._is_taffy_styled("塔菲觉得这个不错喵")

    def test_only_meow_fails(self):
        assert not bridge_qq._is_taffy_styled("不知道喵")

    def test_identity_only_fails(self):
        assert not bridge_qq._is_taffy_styled("塔菲觉得这个不错")


def test_canon_persona_is_merged_into_both_prompts():
    """《永雏塔菲》角色提示词已提炼融入，且不引入会被当成现实的设定。"""
    for prompt in (bridge_qq.TAFFY_LIGHT_PROMPT, bridge_qq.TAFFY_FLAVOR_PROMPT):
        assert "雏草姬" in prompt
        assert "游标卡尺" in prompt
        assert "永远的 17 岁" in prompt
        # 口癖要有节制：不能每句加"喵"
        assert "调料不是句尾标点" in prompt
        # 设定只能当玩笑梗，不能宣称成现实
        assert "不要当作真实经历或现实事实宣布" in prompt
        # 不得把出生年份写成事实
        assert "不要给出任何现实出生年份" in prompt
