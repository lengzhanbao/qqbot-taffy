"""模型降级/熔断测试：全部 mock，不发真实网络请求。"""
import asyncio
import time

import pytest

import bridge_qq


FIRST = "glm/glm-4.5-flash"
SECOND = "glm/glm-4-flash-250414"
THIRD = "glm/glm-4.5-air"
FIRST_NAME = "glm-4.5-flash"
SECOND_NAME = "glm-4-flash-250414"
THIRD_NAME = "glm-4.5-air"


def _make_bridge():
    bridge = object.__new__(bridge_qq.Bridge)
    bridge.api_key = "test-key"
    bridge.model_semaphore = None
    return bridge


@pytest.fixture(autouse=True)
def _reset_model_state(monkeypatch):
    monkeypatch.setattr(bridge_qq, "_model_health", {})
    monkeypatch.setattr(bridge_qq, "_provider_cooldown", {})
    monkeypatch.setattr(bridge_qq, "_tool_model_cooldown", {})
    monkeypatch.setattr(bridge_qq, "inc_stat", lambda *args, **kwargs: None)
    monkeypatch.setattr(bridge_qq, "write_health", lambda **kwargs: None)


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=1))


def _ok(reply="正常回复", finish_reason=""):
    return (reply, "ok", [], finish_reason, [])


def _tool_call(name="search_web"):
    return [{
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
        "id": "tool-1",
    }]


class TestComplete:
    def test_final_user_facing_pool_contains_only_deepseek(self):
        assert bridge_qq.MODELS_FINAL == [bridge_qq.DEEPSEEK_MODEL_REF]

    def test_returns_first_valid_reply(self, monkeypatch):
        bridge = _make_bridge()
        calls = []

        def fake(self, body, api_url, api_key):
            calls.append(body["model"])
            return _ok("这是完整回复")

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        reply, model = _run(bridge._complete("sys", [], model_refs=[FIRST]))
        assert reply == "这是完整回复"
        assert model == FIRST
        assert calls == [FIRST_NAME]
        assert bridge_qq._model_health[FIRST] == 1

    def test_model_queue_timeout_respects_turn_deadline_without_cooling_model(self, monkeypatch):
        bridge = _make_bridge()
        bridge.model_semaphore = asyncio.Semaphore(0)
        calls = []
        monkeypatch.setattr(bridge_qq, "PROVIDERS", {
            "glm": {"url": "https://example.invalid/model", "key": "test"},
        })

        async def fake(body, *args):
            calls.append(body)
            return _ok()

        bridge._run_api_call = fake
        reply, model = _run(bridge._complete(
            "sys", [], model_refs=[FIRST], deadline=time.monotonic() + 0.03))
        assert (reply, model) == ("", "")
        assert calls == []
        assert FIRST not in bridge_qq._provider_cooldown
        assert bridge_qq._model_health.get(FIRST, 0) == 0

    def test_falls_back_when_empty_ok(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            if body["model"] == FIRST_NAME:
                return ("", "ok", [], "", [])
            return _ok("备用回复")

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        reply, model = _run(bridge._complete(
            "sys", [], model_refs=[FIRST, SECOND]))
        assert reply == "备用回复"
        assert model == SECOND
        assert bridge_qq._model_health[FIRST] == -1

    def test_rate_limit_tries_next(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            if body["model"] == FIRST_NAME:
                return ("", "rate_limit", [], "", [])
            return _ok("限流后兜底")

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        reply, model = _run(bridge._complete(
            "sys", [], model_refs=[FIRST, SECOND]))
        assert reply == "限流后兜底"
        assert model == SECOND
        assert bridge_qq._provider_cooldown[FIRST] > time.time()
        assert bridge_qq._model_health[FIRST] == -2

    def test_network_error_tries_next(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            if body["model"] == FIRST_NAME:
                return ("", "network", [], "", [])
            return _ok("网络失败后兜底")

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        reply, model = _run(bridge._complete(
            "sys", [], model_refs=[FIRST, SECOND]))
        assert reply == "网络失败后兜底"
        assert model == SECOND
        assert FIRST in bridge_qq._provider_cooldown

    def test_discards_truncated_reply(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            if body["model"] == FIRST_NAME:
                return ("半截回复", "ok", [], "length", [])
            return _ok("完整备用回复")

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        reply, model = _run(bridge._complete(
            "sys", [], model_refs=[FIRST, SECOND]))
        assert reply == "完整备用回复"
        assert model == SECOND
        assert bridge_qq._model_health[FIRST] == -1

    def test_truncated_penalty_is_single(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            if body["model"] == FIRST_NAME:
                return ("半截回复", "ok", [], "length", [])
            return _ok("完整备用回复")

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        _, model = _run(bridge._complete(
            "sys", [], model_refs=[FIRST, SECOND]))
        assert model == SECOND
        assert bridge_qq._model_health[FIRST] == -1

    def test_discards_weak_reply(self, monkeypatch):
        monkeypatch.setattr(bridge_qq, "REJECTION_GATES", True)  # 旧拒答行为，属回滚路径
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            if body["model"] == FIRST_NAME:
                return _ok("太短")
            return _ok("洛克王国世界草系徽章要先完成草系道馆挑战，再去徽章密室击败守护精灵，成功拿到草系徽章才算解锁。")

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        reply, model = _run(bridge._complete(
            "sys", [], model_refs=[FIRST, SECOND],
            weak_query="洛克王国世界草系徽章怎么弄"))
        assert model == SECOND
        assert "洛克王国" in reply

    def test_weak_penalty_is_single(self, monkeypatch):
        monkeypatch.setattr(bridge_qq, "REJECTION_GATES", True)  # 旧拒答行为，属回滚路径
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            if body["model"] == FIRST_NAME:
                return _ok("太短")
            return _ok("洛克王国世界草系徽章要先完成草系道馆挑战，再去徽章密室击败守护精灵，成功拿到草系徽章才算解锁。")

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        _, model = _run(bridge._complete(
            "sys", [], model_refs=[FIRST, SECOND],
            weak_query="洛克王国世界草系徽章怎么弄"))
        assert model == SECOND
        assert bridge_qq._model_health[FIRST] == -1

    def test_exception_tries_next_and_marks_down(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            if body["model"] == FIRST_NAME:
                raise RuntimeError("mock api boom")
            return _ok("异常后兜底")

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        reply, model = _run(bridge._complete(
            "sys", [], model_refs=[FIRST, SECOND]))
        assert reply == "异常后兜底"
        assert model == SECOND
        assert FIRST in bridge_qq._provider_cooldown

    def test_discards_below_min_length(self, monkeypatch):
        monkeypatch.setattr(bridge_qq, "REJECTION_GATES", True)  # 旧长度拒答，属回滚路径
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            if body["model"] == FIRST_NAME:
                return _ok("太短")
            return _ok("这是一条足够长的完整回答内容，可以覆盖任务问题的关键信息，并继续解释后续步骤和注意事项，同时给出清晰的结论和下一步建议。")

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        reply, model = _run(bridge._complete(
            "sys", [], model_refs=[FIRST, SECOND], min_reply_len=50))
        assert model == SECOND
        assert reply

    def test_all_fail_returns_empty(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            return ("", "network", [], "", [])

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        reply, model = _run(bridge._complete(
            "sys", [], model_refs=[FIRST, SECOND]))
        assert reply == ""
        assert model == ""

    def test_skips_blocked_model(self, monkeypatch):
        bridge = _make_bridge()
        bridge_qq._provider_cooldown[FIRST] = time.time() + 60
        calls = []

        def fake(self, body, api_url, api_key):
            calls.append(body["model"])
            return _ok("跳过熔断后的回复")

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        reply, model = _run(bridge._complete(
            "sys", [], model_refs=[FIRST, SECOND]))
        assert calls == [SECOND_NAME]
        assert model == SECOND
        assert reply == "跳过熔断后的回复"


class TestCompleteRace:
    def test_falls_back_to_rest_when_candidates_fail(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            if body["model"] == THIRD_NAME:
                return _ok("候选失败后的兜底")
            return ("", "network", [], "", [])

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        reply, model = _run(bridge._complete_race(
            "sys", [], model_refs=[FIRST, SECOND, THIRD]))
        assert reply == "候选失败后的兜底"
        assert model == THIRD

    def test_skips_blocked_candidates_and_uses_rest(self, monkeypatch):
        bridge = _make_bridge()
        bridge_qq._provider_cooldown[FIRST] = time.time() + 60
        bridge_qq._provider_cooldown[SECOND] = time.time() + 60
        calls = []

        def fake(self, body, api_url, api_key):
            calls.append(body["model"])
            return _ok("只剩兜底可用")

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        reply, model = _run(bridge._complete_race(
            "sys", [], model_refs=[FIRST, SECOND, THIRD]))
        assert calls == [THIRD_NAME]
        assert model == THIRD

    def test_all_fail_returns_empty(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            return ("", "network", [], "", [])

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        reply, model = _run(bridge._complete_race(
            "sys", [], model_refs=[FIRST, SECOND, THIRD]))
        assert reply == ""
        assert model == ""

    def test_truncated_is_not_marked_ok(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            if body["model"] == FIRST_NAME:
                return ("半截回复", "ok", [], "length", [])
            return _ok("竞速完整备用回复")

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        reply, model = _run(bridge._complete_race(
            "sys", [], model_refs=[FIRST, SECOND]))
        assert model == SECOND
        assert reply == "竞速完整备用回复"
        assert bridge_qq._model_health[FIRST] == -1

    def test_empty_ok_is_weak_not_circuit_breaker(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            if body["model"] == FIRST_NAME:
                return ("", "ok", [], "", [])
            return _ok("竞速空回复后兜底")

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        reply, model = _run(bridge._complete_race(
            "sys", [], model_refs=[FIRST, SECOND]))
        assert model == SECOND
        assert reply == "竞速空回复后兜底"
        assert FIRST not in bridge_qq._provider_cooldown
        assert bridge_qq._model_health[FIRST] == -1

    def test_exception_tries_next_and_marks_down(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            if body["model"] == FIRST_NAME:
                raise RuntimeError("mock race boom")
            return _ok("竞速异常后兜底")

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        reply, model = _run(bridge._complete_race(
            "sys", [], model_refs=[FIRST, SECOND]))
        assert reply == "竞速异常后兜底"
        assert model == SECOND
        assert FIRST in bridge_qq._provider_cooldown


class TestCompleteWithTools:
    def test_empty_tool_planner_pool_does_not_fall_back_to_final_pool(self, monkeypatch):
        bridge = _make_bridge()
        calls = []
        monkeypatch.setattr(bridge_qq, "MODELS_TOOL_PLANNING", [])
        monkeypatch.setattr(bridge_qq, "MODELS_FACTUAL", [FIRST])

        async def fake(body, *args):
            calls.append(body)
            return _ok()

        bridge._run_api_call = fake
        result = _run(bridge._complete_with_tools("sys", []))
        assert result == ([], "", "", [])
        assert calls == []

    def test_filters_deepseek_direct_and_proxy_refs_from_planning(self, monkeypatch):
        bridge = _make_bridge()
        calls = []
        monkeypatch.setattr(bridge_qq, "PROVIDERS", {
            "deepseek": {"url": "https://example.invalid/direct", "key": "test"},
            "opencode": {"url": "https://example.invalid/proxy", "key": "test"},
        })

        async def fake(body, *args):
            calls.append(body["model"])
            return ("", "ok", _tool_call(), "tool_calls", [])

        bridge._run_api_call = fake
        refs = [
            "deepseek/deepseek-flash",
            "opencode/deepseek-v4.1-flash",
            "opencode/search-planner",
        ]
        result = _run(bridge._complete_with_tools("sys", [], model_refs=refs))
        assert calls == ["search-planner"]
        assert result[0] == _tool_call()
        assert result[2] == "opencode/search-planner"

    def test_returns_tool_calls(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            return ("", "ok", _tool_call(), "", [])

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        tool_calls, reply, model, web = _run(bridge._complete_with_tools(
            "sys", [], model_refs=[FIRST]))
        assert len(tool_calls) == 1
        assert reply == ""
        assert model == FIRST
        assert web == []

    def test_returns_web_search(self, monkeypatch):
        bridge = _make_bridge()
        search = [{"title": "搜索标题"}]

        def fake(self, body, api_url, api_key):
            return ("", "ok", [], "", search)

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        tool_calls, reply, model, web = _run(bridge._complete_with_tools(
            "sys", [], model_refs=[FIRST]))
        assert tool_calls == []
        assert reply == ""
        assert model == FIRST
        assert web == search

    def test_returns_direct_reply(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            return _ok("直接回答")

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        tool_calls, reply, model, web = _run(bridge._complete_with_tools(
            "sys", [], model_refs=[FIRST], force_search=False))
        assert tool_calls == []
        assert reply == "直接回答"
        assert model == FIRST
        assert web == []

    def test_skips_truncated_and_uses_next(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            if body["model"] == FIRST_NAME:
                return ("半截", "ok", [], "length", [])
            return ("", "ok", _tool_call(), "", [])

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        tool_calls, reply, model, web = _run(bridge._complete_with_tools(
            "sys", [], model_refs=[FIRST, SECOND]))
        assert len(tool_calls) == 1
        assert model == SECOND
        assert bridge_qq._model_health[FIRST] == -1

    def test_all_fail_returns_empty(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            return ("", "network", [], "", [])

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        tool_calls, reply, model, web = _run(bridge._complete_with_tools(
            "sys", [], model_refs=[FIRST, SECOND]))
        assert tool_calls == []
        assert reply == ""
        assert model == ""
        assert web == []

    def test_marks_error_as_circuit_breaker(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            if body["model"] == FIRST_NAME:
                return ("", "network", [], "", [])
            return ("", "ok", _tool_call(), "", [])

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        _, _, model, _ = _run(bridge._complete_with_tools(
            "sys", [], model_refs=[FIRST, SECOND]))
        assert model == SECOND
        assert FIRST in bridge_qq._provider_cooldown
        assert bridge_qq._model_health[FIRST] == -2

    def test_marks_success_ok(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            return ("", "ok", _tool_call(), "", [])

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        _, _, model, _ = _run(bridge._complete_with_tools(
            "sys", [], model_refs=[FIRST]))
        assert model == FIRST
        assert bridge_qq._model_health[FIRST] == 1

    def test_exception_tries_next_and_marks_down(self, monkeypatch):
        bridge = _make_bridge()

        def fake(self, body, api_url, api_key):
            if body["model"] == FIRST_NAME:
                raise RuntimeError("mock tools boom")
            return ("", "ok", _tool_call(), "", [])

        monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
        _, _, model, _ = _run(bridge._complete_with_tools(
            "sys", [], model_refs=[FIRST, SECOND]))
        assert model == SECOND
        assert FIRST in bridge_qq._provider_cooldown


class TestSortModels:
    def test_health_reorders_models(self, monkeypatch):
        bridge_qq._model_health[SECOND] = 5
        ordered = bridge_qq._sort_models([FIRST, SECOND])
        assert ordered[0] == SECOND

    def test_base_order_when_health_tied(self, monkeypatch):
        """健康度相同时按基准顺序（基准来自 MODELS_FINAL，这里直接给定基准）。

        不依赖运行环境：测试模式不加载 .env，模型池可能只有一个模型。
        """
        monkeypatch.setattr(bridge_qq, "_MODEL_BASE_ORDER",
                            {"glm/first-choice": 0, "glm/second-choice": 1})
        ordered = bridge_qq._sort_models(["glm/second-choice", "glm/first-choice"])
        assert ordered[0] == "glm/first-choice"


def test_expired_turn_deadline_skips_provider_attempts(monkeypatch):
    bridge = _make_bridge()
    calls = []

    def fake(self, body, api_url, api_key):
        calls.append(body["model"])
        return _ok()

    monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)
    reply, model = _run(bridge._complete(
        "sys", [], model_refs=[FIRST, SECOND], deadline=time.monotonic() - 1))

    assert reply == ""
    assert model == ""
    assert calls == []


def test_auth_failure_cools_down_provider_family(monkeypatch):
    bridge_qq._mark_model_failure(FIRST, "http_401")
    assert bridge_qq._provider_blocked(FIRST)
    assert bridge_qq._provider_blocked(SECOND)
    assert not bridge_qq._provider_blocked("opencode_free/example")


def test_model_pool_is_the_current_model_without_free_tier_entries():
    """2026-10-16：所有调用统一走当前模型，不再混用免费池。

    实测免费池大量返回 400/403，一次会话摘要被拖到 39.8 秒。
    """
    assert bridge_qq.MODELS == bridge_qq.MODELS_FINAL
    assert not any(m.startswith("opencode_free/") for m in bridge_qq.MODELS)
    for broken in ("opencode_free/mimo-v2.5-free",
                   "opencode_free/mimo-v2.6-flash-free",
                   "opencode_free/laguna-s-2.1-free",
                   "opencode_free/longcat-2.5-preview-free"):
        assert broken not in bridge_qq.MODELS
    assert "opencode_free/longcat-2.0-free" not in bridge_qq.MODELS
    assert "opencode_free/ling-3.0-flash-free" not in bridge_qq.MODELS


def test_tool_schema_failure_only_cools_down_tool_planning():
    bridge_qq._mark_model_failure(FIRST, "http_400", with_tools=True)
    assert bridge_qq._tool_model_cooldown[FIRST] > time.time()
    assert not bridge_qq._provider_blocked(FIRST)


def test_model_semaphore_caps_concurrent_api_calls(monkeypatch):
    bridge = _make_bridge()
    bridge.model_semaphore = asyncio.Semaphore(2)
    lock = __import__("threading").Lock()
    state = {"active": 0, "peak": 0}

    def fake(self, body, api_url, api_key):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.03)
        with lock:
            state["active"] -= 1
        return _ok("这是一个足够完整的测试回复")

    monkeypatch.setattr(bridge_qq.Bridge, "_api_call", fake)

    async def run_many():
        return await asyncio.gather(*[
            bridge._complete("sys", [], model_refs=[FIRST])
            for _ in range(8)
        ])

    results = _run(run_many())
    assert all(reply for reply, _ in results)
    assert state["peak"] <= 2


def test_cancelled_api_call_closes_open_response(monkeypatch):
    bridge = _make_bridge()

    class Response:
        closed = False

        def close(self):
            self.closed = True

    response = Response()

    def blocked(self, body, api_url, api_key):
        body["_response_holder"]["response"] = response
        body["_cancel_event"].wait(1)
        return "", "network", [], "", []

    monkeypatch.setattr(bridge_qq.Bridge, "_api_call", blocked)

    async def cancel_call():
        task = asyncio.create_task(bridge._run_api_call(
            {}, "https://example.invalid", "test-key",
            deadline=time.monotonic() + 2,
        ))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(cancel_call())
    assert response.closed
