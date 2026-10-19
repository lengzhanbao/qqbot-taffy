"""Offline acceptance corpus for search relevance vs answer evidence."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import bridge_qq
from test_routing import _bridge_with_final


SEARCH_CASES = (
    ("2026世界杯冠军是谁", "【FIFA官方 | fifa.com】2026年世界杯决赛，西班牙加时1比0击败阿根廷，获得冠军。", True, "西班牙"),
    ("2026世界杯冠军是谁", "【2026世界杯赛程 | fifa.com】官方公布小组赛和淘汰赛赛程。", False, ""),
    ("2026世界杯冠军是谁", "【FIFA | fifa.com】2026世界杯冠军预测：西班牙是夺冠热门，赔率领先。", False, ""),
    ("2025世俱杯冠军是谁", "【FIFA | fifa.com】2025世俱杯决赛，切尔西3比0战胜巴黎圣日耳曼夺冠。", True, "切尔西"),
    ("2025世俱杯冠军是谁", "【FIFA | fifa.com】2025世俱杯决赛时间和比赛地点已经公布。", False, ""),
    ("DeepSeek最近发布了什么模型", "【DeepSeek | deepseek.com】DeepSeek-V4于2026年9月发布。", True, "DeepSeek-V4"),
    ("DeepSeek最近发布了什么模型", "【DeepSeek | deepseek.com】DeepSeek公司成立于2023年，专注人工智能研究。", False, ""),
    ("DeepSeek最近发布了什么模型", "【DeepSeek | deepseek.com】DeepSeek-V3于2024年12月发布。", False, ""),
    ("Apple最近发布了什么iPhone", "【Apple | apple.com】Apple于2026年9月发布iPhone 18系列。", True, "iPhone 18"),
    ("Apple最近发布了什么iPhone", "【Apple | apple.com】iPhone支持多种配色和充电功能。", False, ""),
    ("OpenAI现任CEO是谁", "【OpenAI | openai.com】截至2026-10-15，Sam Altman is CEO of OpenAI.", True, "Sam Altman"),
    ("OpenAI现任CEO是谁", "【OpenAI | openai.com】OpenAI creates artificial intelligence tools.", False, ""),
    ("OpenAI现任CEO是谁", "【OpenAI | openai.com】2024-01-10: Sam Altman is CEO of OpenAI.", False, ""),
    ("玄武口音是什么梗", "【萌娘百科】玄武口音是关于余承东的一种网络梗。", True, "余承东"),
    ("玄武口音是什么梗", "【百科】玄武是中国古代神话中的神兽。", False, ""),
    ("北京今天天气怎么样", "【中国天气网 | weather.com.cn】北京今天多云，最高气温22度。", True, "多云"),
    ("北京今天天气怎么样", "【中国天气网 | weather.com.cn】北京气候四季分明，冬冷夏热。", False, ""),
    ("塔菲最近直播了什么", "【B站 | bilibili.com】塔菲10月15日直播了游戏和日常话题。", True, "游戏"),
    ("塔菲最近直播了什么", "【B站 | bilibili.com】永雏塔菲是虚拟主播，直播内容包括游戏。", False, ""),
    ("塔菲最近直播了什么", "【B站 | bilibili.com】塔菲2026年4月12日直播了游戏。", False, ""),
    ("2026年世界杯决赛日期是什么时候", "【FIFA | fifa.com】2026年7月19日举行世界杯决赛。", True, "7月19日"),
    ("2026年世界杯决赛日期是什么时候", "【FIFA | fifa.com】2026年世界杯赛事安排和购票页面。", False, ""),
    ("2026年诺贝尔物理学奖得主是谁", "【Nobel Prize | nobelprize.org】2026年诺贝尔物理学奖授予X研究团队。", True, "X研究团队"),
    ("2026年诺贝尔物理学奖得主是谁", "【百度建议】2026诺贝尔物理学奖名单和结果。", False, ""),
    ("最新AI新闻", "【Reuters | reuters.com】2026-10-15: AI researchers announced a new benchmark.", True, "benchmark"),
    ("最新AI新闻", "【Reuters | reuters.com】2026-05-01: AI researchers announced a new benchmark.", False, ""),
)


@pytest.mark.parametrize("query,raw,expected,marker", SEARCH_CASES)
def test_search_corpus_distinguishes_answer_evidence_from_topical_matches(
        monkeypatch, query, raw, expected, marker):
    monkeypatch.setattr(
        bridge_qq, "_beijing_now",
        lambda: datetime(2026, 10, 16, 12, tzinfo=timezone(timedelta(hours=8))),
    )
    evidence = bridge_qq._search_evidence_text(raw)
    distilled = bridge_qq._distill_search_results(evidence, query)
    assert bool(distilled) is expected
    if marker:
        assert marker in distilled


def test_official_domain_does_not_make_non_answer_page_evidence():
    raw = "【FIFA官方 | fifa.com】2026世界杯小组赛赛程及购票信息。"
    assert bridge_qq._source_rank("FIFA官方 | fifa.com") > 0
    assert not bridge_qq._distill_search_results(raw, "2026世界杯冠军是谁")


def test_unsupported_topical_snippet_fails_closed_in_real_fact_route(monkeypatch):
    monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", False)
    monkeypatch.setattr(bridge_qq, "_should_use_single_shot_search", lambda *_: True)
    bridge = _bridge_with_final(monkeypatch, reply="阿根廷获得冠军。")
    searches = []

    async def schedule_only(query, **kwargs):
        searches.append(query)
        return "【FIFA官方 | fifa.com】2026世界杯小组赛和淘汰赛赛程。"

    async def forbidden_generation(*args, **kwargs):
        raise AssertionError("topical-only snippets must not reach answer generation")

    bridge._complete = bridge._complete_race = forbidden_generation
    monkeypatch.setattr(bridge_qq, "search_web_async", schedule_only)
    reply = asyncio.run(bridge.ask_model(
        "private_1", "2026世界杯冠军是谁", "2026世界杯冠军是谁", "user"))

    assert searches
    assert "不确定" in reply or "无法确认" in reply
    assert "阿根廷" not in reply
