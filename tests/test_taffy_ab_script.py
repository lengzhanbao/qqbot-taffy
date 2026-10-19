import asyncio

import bridge_qq
from qqbot_core.evaluation import FIXED_CASES
from scripts import run_taffy_ab_eval


def test_prompt_pair_has_no_history_or_live_search_dependency():
    old, new = run_taffy_ab_eval._prompt_pair(bridge_qq, FIXED_CASES[0])
    assert old and new and old != new
    assert "不使用历史、群聊知识、搜索或工具" in new
    assert "TAFFY_LIGHT" not in new


def test_generator_uses_single_configured_provider_with_bounded_parameters():
    captured = {}

    class Client:
        async def _run_api_call(self, body, url, key, deadline, diagnostics=None):
            captured.update(body=body, url=url, key=key, deadline=deadline)
            diagnostics.update(raw_chars=10, finish_reason="stop", completion_tokens=8)
            return "小菲才不是。😼", "ok", [], "stop", []

    reply, state, latency, diagnostics, max_tokens = asyncio.run(run_taffy_ab_eval._generate(
        bridge_qq, Client(), "deepseek/test-model", "system", "你是牛马吗",
        semaphore=asyncio.Semaphore(1),
    ))
    assert reply == "小菲才不是。😼" and state == "ok" and latency >= 0
    assert diagnostics == {
        "raw_chars": 10, "finish_reason": "stop", "completion_tokens": 8
    }
    assert max_tokens == bridge_qq.DEFAULT_MAX_TOKENS  # 不再按类别压到 1024
    assert captured["body"]["model"] == "test-model"
    assert captured["body"]["max_tokens"] == bridge_qq.DEFAULT_MAX_TOKENS
    assert captured["body"]["temperature"] == 1.0
    assert captured["body"]["top_p"] == 1.0
    assert "thinking" not in captured["body"]
    assert [message["role"] for message in captured["body"]["messages"]] == ["system", "user"]


def test_generator_uses_longform_budget_instead_of_social_budget():
    class Client:
        async def _run_api_call(self, body, url, key, deadline, diagnostics=None):
            diagnostics.update(raw_chars=2000, finish_reason="stop", completion_tokens=1500)
            return "故事正文", "ok", [], "stop", []

    result = asyncio.run(run_taffy_ab_eval._generate(
        bridge_qq, Client(), "deepseek/test-model", "system", "帮我写一个短故事",
        semaphore=asyncio.Semaphore(1),
    ))
    assert result[4] == bridge_qq.DEFAULT_MAX_TOKENS  # 长文与闲聊共用同一预算
