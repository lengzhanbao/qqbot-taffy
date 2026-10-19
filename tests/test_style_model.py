"""小菲风格模型测试。"""
import bridge_qq


def test_taffy_system_prompt_keeps_persona():
    assert "永雏塔菲" in bridge_qq.SYSTEM_PROMPT
    assert "活泼可爱" in bridge_qq.SYSTEM_PROMPT
    assert "同人 AI 助手" in bridge_qq.SYSTEM_PROMPT


def test_taffy_flavor_keeps_self_refs_and_miao_limit():
    assert "自然需要角色自称时，用 taffy / 塔菲 / 小菲自称" in bridge_qq.TAFFY_FLAVOR_PROMPT
    assert "喵有节制" in bridge_qq.TAFFY_FLAVOR_PROMPT
    assert "直播间即时接话" in bridge_qq.TAFFY_FLAVOR_PROMPT
    assert "不强行玩梗" in bridge_qq.TAFFY_FLAVOR_PROMPT
    assert "短梗一拍接住，不解释、不续编背景" in bridge_qq.TAFFY_FLAVOR_PROMPT
    assert "不是永雏塔菲本人或官方账号" in bridge_qq.TAFFY_FLAVOR_PROMPT


def test_taffy_flavor_prompt_does_not_repeat_role_heading():
    assert "【表达习惯】" in bridge_qq.TAFFY_FLAVOR_PROMPT
    assert "【角色：永雏塔菲】" not in bridge_qq.TAFFY_FLAVOR_PROMPT


def test_is_taffy_styled_positive_and_negative():
    assert bridge_qq._is_taffy_styled("塔菲觉得这个不错喵")
    assert not bridge_qq._is_taffy_styled(
        "根据搜索结果，这是一个AI回答")


def test_search_source_marker_states():
    assert bridge_qq._append_search_source_marker(
        "答案", True).endswith("（联网搜过喵）")
    assert bridge_qq._append_search_source_marker(
        "答案", False, True).endswith(
        "（联网未找到可靠结果，塔菲暂时不确定喵）")
    assert bridge_qq._append_search_source_marker(
        "答案", False) == "答案"


def test_search_source_markers_can_be_neutral():
    assert bridge_qq._append_search_source_marker(
        "答案", True, taffy_enabled=False).endswith("（已联网搜索）")
    assert bridge_qq._append_search_source_marker(
        "答案", False, True, taffy_enabled=False).endswith(
        "（已尝试联网，但未找到有效结果）")
    assert bridge_qq._append_search_source_marker(
        "答案", False, taffy_enabled=False) == "答案"
    neutralized = bridge_qq._append_search_source_marker(
        "答案\n\n（联网搜过喵）", False, taffy_enabled=False)
    assert neutralized == "答案"
    assert "喵" not in neutralized


def test_answer_cache_key_separates_taffy_toggle(monkeypatch):
    monkeypatch.setattr(bridge_qq, "TAFFY_FLAVOR", True)
    taffy_key = bridge_qq._answer_cache_key("group", "同一问题")
    monkeypatch.setattr(bridge_qq, "TAFFY_FLAVOR", False)
    neutral_key = bridge_qq._answer_cache_key("group", "同一问题")
    assert taffy_key != neutral_key
