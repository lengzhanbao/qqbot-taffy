"""小菲风格模型测试。"""
import bridge_qq


def test_taffy_system_prompt_keeps_persona():
    assert "永雏塔菲" in bridge_qq.SYSTEM_PROMPT
    assert "活泼可爱" in bridge_qq.SYSTEM_PROMPT


def test_taffy_flavor_keeps_self_refs_and_miao_limit():
    assert "用 taffy / 塔菲 / 小菲自称" in bridge_qq.TAFFY_FLAVOR_PROMPT
    assert "喵有节制" in bridge_qq.TAFFY_FLAVOR_PROMPT


def test_is_taffy_styled_positive_and_negative():
    assert bridge_qq._is_taffy_styled("塔菲觉得这个不错喵")
    assert not bridge_qq._is_taffy_styled(
        "根据搜索结果，这是一个AI回答")


def test_search_source_marker_states():
    assert bridge_qq._append_search_source_marker(
        "答案", True).endswith("（联网搜过喵）")
    assert bridge_qq._append_search_source_marker(
        "答案", False, True).endswith(
        "（联网搜了但没查到，塔菲凭现有知识答的喵）")
    assert bridge_qq._append_search_source_marker(
        "答案", False).endswith(
        "（没联网，塔菲凭现有知识答的喵）")
