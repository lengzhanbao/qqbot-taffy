"""抽象等级分类测试：L0(敏感/降档) > L1(事实) > L2(闲聊/抽象) > L3(显式抽象+开关)。"""
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


class TestStyleControlChitchat:
    def test_chitchat_default_l2_is_l2(self, monkeypatch):
        _set_style(monkeypatch, level=2)
        level, _, _ = bridge_qq._classify_style_control("你觉得华为怎么样")
        assert level == 2

    def test_opinion_default_l2_is_l2(self, monkeypatch):
        _set_style(monkeypatch, level=2)
        level, _, _ = bridge_qq._classify_style_control("你怎么看bin哥")
        assert level == 2

    def test_analogy_default_l2_is_l2(self, monkeypatch):
        _set_style(monkeypatch, level=2)
        level, _, _ = bridge_qq._classify_style_control("华为类比C罗")
        assert level == 2


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


class TestTaffyStyled:
    def test_identity_and_emotion_passes(self):
        assert bridge_qq._is_taffy_styled("塔菲觉得这个不错喵")

    def test_only_meow_fails(self):
        assert not bridge_qq._is_taffy_styled("不知道喵")

    def test_identity_only_fails(self):
        assert not bridge_qq._is_taffy_styled("塔菲觉得这个不错")
