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
        ordered = bridge_qq._sort_models([SECOND, FIRST])
        assert ordered[0] == FIRST
