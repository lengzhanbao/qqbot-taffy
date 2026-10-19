"""⑤ 参数核实回归：按实测结果夹温度、按模型决定是否关闭思考。

实测依据（2026-10-17，每模型 5 个变体）：
  glm/glm-4.5-flash                  temperature 1.1 / 1.5 → HTTP 400
  glm/glm-4.5-flash                  不传 thinking → 烧完 token 返回空正文
  opencode/longcat-2.5-preview-free  temperature 1.1 → 空正文
  opencode/longcat-2.5-preview-free  显式 thinking: enabled → 空正文
"""
import bridge_qq


def test_temperature_is_clamped_to_one():
    for provider in ("glm", "opencode", "deepseek", "opencode_free"):
        assert bridge_qq._safe_temperature(provider, "any", 1.5) == 1.0
        assert bridge_qq._safe_temperature(provider, "any", 0.85) == 0.85
        assert bridge_qq._safe_temperature(provider, "any", -1) == 0.0


def test_temperature_bad_value_passes_through():
    assert bridge_qq._safe_temperature("glm", "any", "oops") == "oops"


def test_glm_gets_thinking_disabled_on_first_attempt():
    """GLM 不关思考会返回空正文，必须首次就关。"""
    options = bridge_qq._model_thinking_options("glm", "glm-4.5-flash")
    assert options == {"thinking": {"type": "disabled"}}


def test_longcat_gets_no_thinking_param():
    """LongCat 显式开启思考会返回空正文；不传该参数时正常。"""
    options = bridge_qq._model_thinking_options(
        "opencode", "longcat-2.5-preview-free")
    assert options == {}


def test_request_body_applies_both_rules():
    body = bridge_qq._build_model_request(
        "glm", "glm-4.5-flash", "sys", [], 200, temperature=1.5, top_p=1.0)
    assert body["temperature"] == 1.0
    assert body["thinking"] == {"type": "disabled"}

    body = bridge_qq._build_model_request(
        "opencode", "longcat-2.5-preview-free", "sys", [], 200,
        temperature=1.5, top_p=1.0)
    assert body["temperature"] == 1.0
    assert "thinking" not in body


def test_complex_task_keeps_generous_output_budget():
    """复杂问题要有足够正文预算（不因速度砍到很小）。"""
    assert bridge_qq._dynamic_final_max_tokens(True, False) >= 2500
    assert bridge_qq._dynamic_final_max_tokens(False, True) >= 2500
    assert bridge_qq.DEFAULT_MAX_TOKENS >= 2000
