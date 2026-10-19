"""Day11 搜索稳定性测试：缓存、引擎失败统计、自动降级、专名保护。"""
import asyncio
import threading
import time

from unittest.mock import patch

import bridge_qq


def _reset():
    bridge_qq._SEARCH_CACHE.clear()
    bridge_qq._ENGINE_FAIL_STATS.clear()


class TestSearchCache:
    def test_cache_hit_does_not_call_engine_twice(self):
        _reset()
        with patch("bridge_qq._search_engine", return_value=["结果"]) as mock:
            first = bridge_qq.search_with_resilience("测试查询")
            second = bridge_qq.search_with_resilience("测试查询")
        assert first == "结果"
        assert second == "结果"
        assert mock.call_count == 1

    def test_expired_cache_is_replaced(self):
        _reset()
        key = bridge_qq._search_cache_key("过期查询")
        bridge_qq._SEARCH_CACHE[key] = (time.time() - 1000, "旧结果")
        with patch("bridge_qq._search_engine", return_value=["新结果"]):
            result = bridge_qq.search_with_resilience("过期查询")
        assert result == "新结果"


class TestEngineDegrade:
    def test_record_fail(self):
        _reset()
        bridge_qq._record_engine_fail("bing_cn")
        assert bridge_qq._ENGINE_FAIL_STATS["bing_cn"]["fails"] == 1

    def test_degrade_after_three_fails(self, monkeypatch):
        _reset()
        monkeypatch.setattr(bridge_qq, "write_health", lambda **kwargs: None)
        for _ in range(3):
            bridge_qq._record_engine_fail("bing_cn")
        assert bridge_qq._is_engine_degraded("bing_cn") is True

    def test_degrade_expires(self, monkeypatch):
        _reset()
        monkeypatch.setattr(bridge_qq, "write_health", lambda **kwargs: None)
        for _ in range(3):
            bridge_qq._record_engine_fail("bing_cn")
        bridge_qq._ENGINE_FAIL_STATS["bing_cn"]["last_fail"] = (
            time.time() - 400
        )
        assert bridge_qq._is_engine_degraded("bing_cn") is False


class TestFallback:
    def test_empty_result_is_failure_but_not_engine_outage(self, monkeypatch):
        _reset()
        stats = []
        monkeypatch.setattr(bridge_qq, "inc_stat", lambda key, n=1: stats.append(key))
        monkeypatch.setattr(bridge_qq.time, "sleep", lambda *_: None)
        with patch("bridge_qq._search_engine", return_value=[]):
            result = bridge_qq.search_with_resilience("查询")
        assert "失败" in result
        assert "search_fail" in stats
        assert bridge_qq._ENGINE_FAIL_STATS == {}

    def test_first_engine_fail_uses_second(self):
        _reset()
        with patch("bridge_qq._search_engine") as mock:
            mock.side_effect = [Exception("失败"), ["成功结果"]]
            result = bridge_qq.search_with_resilience("查询")
        assert "成功" in result

    def test_all_engines_fail_returns_message(self):
        _reset()
        with patch("bridge_qq._search_engine") as mock:
            mock.side_effect = [Exception("失败") for _ in range(20)]
            result = bridge_qq.search_with_resilience("查询")
        assert "失败" in result


def test_luoke_kingdom_query_kept_intact():
    queries = bridge_qq._split_sub_queries(
        "洛克王国世界草系徽章怎么弄")
    assert any("洛克王国" in q for q in queries)


def test_relevance_requires_rare_term():
    qterms = bridge_qq._query_terms("玄武口音 竹知了 事件")
    assert not bridge_qq._is_relevant_result("玄武 中国古代神话", qterms)
    assert bridge_qq._is_relevant_result("竹知了事件 余承东", qterms)


def test_relevance_accepts_multiple_matching_chinese_fragments():
    qterms = bridge_qq._query_terms("现在北京天气怎么样")
    assert bridge_qq._is_relevant_result("北京天气 今天北京多云", qterms)
    assert not bridge_qq._is_relevant_result("玄武是中国古代神话中的神兽", qterms)


def test_search_parser_preserves_external_source_domain_for_verification():
    html = (
        '<li class="b_algo"><h2><a href="https://www.fifa.com/en/worldcup/final">'
        '2026 World Cup Final</a></h2><p>'
        '2026年世界杯决赛，西班牙加时1-0击败阿根廷。</p></li>'
    )
    rows = bridge_qq._extract_bing_results(html, 5, "2026年世界杯冠军")
    assert len(rows) == 1
    assert "fifa.com" in rows[0]
    assert "加时1-0击败阿根廷" in rows[0]


def test_dynamic_output_tokens_are_limited():
    assert bridge_qq._dynamic_final_max_tokens(False, True) <= 6000
    assert bridge_qq._dynamic_final_max_tokens(True, False) <= 4000
    assert bridge_qq._dynamic_final_max_tokens(False, False) <= 2000


def test_async_search_tries_all_sources_and_merges_by_priority(monkeypatch):
    _reset()
    bridge_qq._SEARCH_CACHE.clear()
    calls = []

    def fake_engine(query, engine, max_results, timeout=None):
        calls.append(engine)
        if engine == "bing_cn":
            time.sleep(0.03)
        return [f"【{engine}】结果"]

    def fake_ddg(query, max_results, time_range=None, timeout=7):
        calls.append("duckduckgo")
        return ["【duckduckgo】结果"]

    monkeypatch.setattr(bridge_qq, "_search_engine", fake_engine)
    monkeypatch.setattr(bridge_qq, "_search_duckduckgo", fake_ddg)
    monkeypatch.setattr(bridge_qq, "DRISSION_SEARCH_ENABLED", False)

    result = asyncio.run(bridge_qq.search_web_async("测试查询", exact=True))

    expected = list(bridge_qq._SEARCH_ENGINE_ORDER) + ["duckduckgo"]
    assert sorted(calls) == sorted(expected)
    assert len(calls) == len(expected)
    rows = result.splitlines()
    assert rows[0] == "【bing_cn】结果"
    assert rows[1] == "【sogou】结果"
    assert rows[-1] == "【duckduckgo】结果"


def test_async_search_returns_relevant_bing_result_before_fallback(monkeypatch):
    _reset()
    calls = []

    def fake_engine(query, engine, max_results, timeout=None):
        calls.append(engine)
        if engine == "bing_cn":
            return ["【竹知了事件报道】竹知了事件的相关公开信息与事件背景。"]
        return [f"【{engine}】其他结果描述内容"]

    monkeypatch.setattr(bridge_qq, "_search_engine", fake_engine)
    monkeypatch.setattr(bridge_qq, "DRISSION_SEARCH_ENABLED", False)
    result = asyncio.run(bridge_qq.search_web_async("竹知了事件", exact=True))

    assert calls == ["bing_cn"]
    assert "竹知了事件" in result


def test_bing_suggestion_is_not_treated_as_a_successful_hit(monkeypatch):
    _reset()
    calls = []

    def fake_engine(query, engine, max_results, timeout=None):
        calls.append(engine)
        if engine == "bing_cn":
            return ["【必应建议】竹知了事件相关新闻"]
        if engine == "sogou":
            return ["【搜狗】竹知了事件的相关公开报道和背景信息。"]
        return []

    monkeypatch.setattr(bridge_qq, "_search_engine", fake_engine)
    monkeypatch.setattr(bridge_qq, "_search_duckduckgo", lambda *a, **k: [])
    monkeypatch.setattr(bridge_qq, "DRISSION_SEARCH_ENABLED", False)

    result = asyncio.run(bridge_qq.search_web_async("竹知了事件", exact=True))

    assert "bing_cn" in calls
    assert "sogou" in calls
    assert "搜狗" in result


def test_fallback_success_does_not_wait_for_slow_sources(monkeypatch):
    _reset()
    monkeypatch.setattr(bridge_qq, "_SEARCH_TOTAL_BUDGET", 1.0)
    monkeypatch.setattr(bridge_qq, "_BING_SEARCH_HEADSTART", 0.1)
    monkeypatch.setattr(bridge_qq, "_SEARCH_FALLBACK_GRACE", 0.05)
    monkeypatch.setattr(bridge_qq, "DRISSION_SEARCH_ENABLED", False)

    def fake_engine(query, engine, max_results, timeout=None):
        if engine == "bing_cn":
            raise RuntimeError("Bing unavailable")
        if engine == "sogou":
            return ["【搜狗】竹知了事件的相关公开报道和背景信息。"]
        time.sleep(0.25)
        return []

    monkeypatch.setattr(bridge_qq, "_search_engine", fake_engine)
    monkeypatch.setattr(
        bridge_qq, "_search_duckduckgo",
        lambda *args, **kwargs: (time.sleep(0.25) or []),
    )

    async def search_with_elapsed_time():
        started = time.monotonic()
        result = await bridge_qq.search_web_async("竹知了事件", exact=True)
        return time.monotonic() - started, result

    elapsed, result = asyncio.run(search_with_elapsed_time())
    assert elapsed < 0.2
    assert "竹知了事件" in result


def test_async_search_skips_degraded_engine(monkeypatch):
    _reset()
    monkeypatch.setattr(bridge_qq, "write_health", lambda **kwargs: None)
    for _ in range(bridge_qq.ENGINE_FAIL_THRESHOLD):
        bridge_qq._record_engine_fail("bing_cn")
    calls = []

    def fake_engine(query, engine, max_results, timeout=None):
        calls.append(engine)
        return [f"【{engine}】竹知了事件相关公开报道和背景信息。"]

    monkeypatch.setattr(bridge_qq, "_search_engine", fake_engine)
    monkeypatch.setattr(
        bridge_qq, "_search_duckduckgo",
        lambda *args, **kwargs: ["【duckduckgo】竹知了事件相关公开报道和背景信息。"],
    )
    monkeypatch.setattr(bridge_qq, "DRISSION_SEARCH_ENABLED", False)

    result = asyncio.run(bridge_qq.search_web_async("竹知了事件", exact=True))

    assert "bing_cn" not in calls
    assert "竹知了事件" in result


def test_async_empty_results_count_as_failures_without_degrading_sources(monkeypatch):
    _reset()
    stats = []
    monkeypatch.setattr(bridge_qq, "inc_stat", lambda key, n=1: stats.append(key))
    monkeypatch.setattr(bridge_qq, "_search_engine", lambda *a, **k: [])
    monkeypatch.setattr(bridge_qq, "_search_duckduckgo", lambda *a, **k: [])
    monkeypatch.setattr(bridge_qq, "DRISSION_SEARCH_ENABLED", False)

    asyncio.run(bridge_qq.search_web_async("无结果查询", exact=True))

    assert "search_fail" in stats
    assert bridge_qq._ENGINE_FAIL_STATS == {}


def test_five_planned_queries_respect_global_io_concurrency_limit(monkeypatch):
    _reset()
    monkeypatch.setattr(bridge_qq, "MAX_SEARCH_TASKS", 2)
    monkeypatch.setattr(bridge_qq, "DRISSION_SEARCH_ENABLED", False)
    lock = threading.Lock()
    active = 0
    max_active = 0

    def track_call(result):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.01)
            return result
        finally:
            with lock:
                active -= 1

    def fake_engine(query, engine, max_results, timeout=None):
        return track_call([f"【{engine}】无关结果"])

    def fake_ddg(*args, **kwargs):
        return track_call(["【duckduckgo】无关结果"])

    monkeypatch.setattr(bridge_qq, "_search_engine", fake_engine)
    monkeypatch.setattr(bridge_qq, "_search_duckduckgo", fake_ddg)

    async def run_queries():
        return await bridge_qq._search_planned_queries([
            {"query": f"query-{name}", "purpose": "并发上限测试"}
            for name in ("alpha", "beta", "gamma", "delta", "epsilon")
        ])

    asyncio.run(run_queries())
    assert max_active <= 2
