"""Day11 搜索稳定性测试：缓存、引擎失败统计、自动降级、专名保护。"""
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

    def test_degrade_after_three_fails(self):
        _reset()
        for _ in range(3):
            bridge_qq._record_engine_fail("bing_cn")
        assert bridge_qq._is_engine_degraded("bing_cn") is True

    def test_degrade_expires(self):
        _reset()
        for _ in range(3):
            bridge_qq._record_engine_fail("bing_cn")
        bridge_qq._ENGINE_FAIL_STATS["bing_cn"]["last_fail"] = (
            time.time() - 400
        )
        assert bridge_qq._is_engine_degraded("bing_cn") is False


class TestFallback:
    def test_empty_result_is_failure(self):
        _reset()
        with patch("bridge_qq._search_engine", return_value=[]):
            result = bridge_qq.search_with_resilience("查询")
        assert "失败" in result
        assert bridge_qq._ENGINE_FAIL_STATS["bing_cn"]["fails"] >= 1

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


def test_dynamic_output_tokens_are_limited():
    assert bridge_qq._dynamic_final_max_tokens(False, True) <= 6000
    assert bridge_qq._dynamic_final_max_tokens(True, False) <= 4000
    assert bridge_qq._dynamic_final_max_tokens(False, False) <= 2000
