"""Offline long-output acceptance across story, guide and code profiles."""
import asyncio
import time
from unittest.mock import AsyncMock

import pytest
import bridge_qq
from qqbot_core.routing import classify_message
from qqbot_core.text import _sanitize_reply, _truncate_reply
from test_routing import _bridge_with_final


LONG_CASES = (
    (
        "写一个短故事",
        "从前，小菲推开一扇门，里面只剩一盏灯",
        "里面只剩一盏灯亮着。",
        "creative",
        "一盏灯亮着。",
    ),
    (
        "洛克王国怎么打这个徽章",
        "步骤一：打开设置。\n步骤二：找到网络选项",
        "找到网络选项后，保存并重启。",
        "guide",
        "保存并重启。",
    ),
    (
        "Python装饰器是什么",
        "下面是示例：\n```python\ndef add(a, b):\n    return a + b",
        "    return a + b\n\nprint(add(2, 3))\n```",
        "code",
        "print(add(2, 3))",
    ),
)


@pytest.mark.parametrize("query,partial,continuation,category,ending", LONG_CASES)
def test_one_bounded_continuation_completes_each_long_output_profile(
        monkeypatch, query, partial, continuation, category, ending):
    profile = bridge_qq._reply_output_profile(query, classify_message(query))
    # 2026-10-17：输出预算不再对问题分类，所有问题共用同一份预算。
    # 参数里的 category 只作为用例标签保留。
    assert profile["category"] == "default"
    assert profile["max_tokens"] == bridge_qq.DEFAULT_MAX_TOKENS
    assert profile["continuation_tokens"] > 0
    bridge = object.__new__(bridge_qq.Bridge)
    bridge.model_semaphore = asyncio.Semaphore(3)
    calls = []

    async def fake_api(body, url, key, deadline=None):
        calls.append(body)
        if len(calls) == 1:
            return partial, "ok", [], "length", []
        return continuation, "ok", [], "stop", []

    bridge._run_api_call = fake_api
    monkeypatch.setattr(bridge_qq, "DEEPSEEK_MODEL_REF", "deepseek/test")
    monkeypatch.setattr(bridge_qq, "MODELS", ["deepseek/test"])
    monkeypatch.setattr(bridge_qq, "PROVIDERS", {
        "deepseek": {"url": "https://example.invalid", "key": "test"}
    })
    monkeypatch.setattr(bridge_qq, "_provider_blocked", lambda _: False)
    preserve_code = category == "code"

    raw, model = asyncio.run(bridge._complete(
        "system", [{"role": "user", "content": query}],
        max_tokens=profile["max_tokens"], model_refs=["deepseek/test"],
        deadline=time.monotonic() + 10,
        continuation_tokens=profile["continuation_tokens"],
        preserve_code=preserve_code,
    ))
    clean = _sanitize_reply(raw, preserve_code=preserve_code)
    output = _truncate_reply(clean, profile["max_chars"], preserve_code=preserve_code)

    assert model == "deepseek/test"
    assert len(calls) == 2
    assert (ending in output if preserve_code else output.endswith(ending))
    assert len(output) <= profile["max_chars"]
    assert output.count("return a + b") == (1 if preserve_code else 0)
    if preserve_code:
        assert output.count("```") == 2


def test_code_profile_preserves_fences_inline_code_and_closes_after_hard_cap():
    source = (
        "重点是 **保留代码**，调用 `run()`。\n"
        "```python\n" + "print('ok')\n" * 30 + "```"
    )
    cleaned = _sanitize_reply(source, preserve_code=True)
    assert "```python" in cleaned and "`run()`" in cleaned
    clipped = _truncate_reply(cleaned, 100, preserve_code=True)
    assert len(clipped) <= 100
    assert clipped.count("```") % 2 == 0
    assert clipped.endswith("```")


def test_non_code_cleanup_keeps_existing_plain_text_contract():
    assert _sanitize_reply("**重点**和`代码`") == "重点和代码"


def test_real_fact_route_preserves_code_fences_through_final_sanitization(monkeypatch):
    query = "Python装饰器是什么"
    code = "用函数包装另一个函数：\n```python\ndef f():\n    return 1\n```"
    bridge = _bridge_with_final(monkeypatch, reply=code)
    monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", False)
    monkeypatch.setattr(bridge_qq, "_should_use_single_shot_search", lambda *_: True)
    monkeypatch.setattr(bridge_qq, "search_web_async", AsyncMock(return_value=(
        "【Python文档 | python.org】装饰器可以扩展函数行为。")))

    reply = asyncio.run(bridge.ask_model("private_1", query, query, "user"))

    assert "```python" in reply
    assert "    return 1" in reply
    assert reply.count("```") == 2

    sent = {}

    async def fake_send(action, params):
        sent.update(action=action, params=params)
        return {"status": "ok"}

    bridge.call_api = fake_send
    asyncio.run(bridge.send_group_msg("100", "200", reply))
    payload = sent["params"]["message"][1]["data"]["text"]
    assert sent["action"] == "send_group_msg"
    assert payload.lstrip() == reply
    assert "```python" in payload and payload.count("```") == 2
