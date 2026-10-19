"""Light first-pass personality and scoped expression learning, without network."""
import asyncio
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

import bridge_qq
from qqbot_core.memory import MemoryRetriever
from qqbot_core.routing import classify_message
from qqbot_core.style import TAFFY_FLAVOR_PROMPT, TAFFY_LIGHT_PROMPT
from qqbot_core.text import _sanitize_reply
from test_knowledge_integration import configured
from test_knowledge_system import ledger, add
from test_routing import _bridge_with_final


@pytest.mark.parametrize("text", ["你是牛马吗", "扣1送地狱火", "你现在很聪明了吗"])
def test_light_social_is_one_generation_without_tools_or_history(monkeypatch, text):
    monkeypatch.setattr(bridge_qq, "TAFFY_LIGHT_MODE", True)
    monkeypatch.setattr(bridge_qq, "TAFFY_FLAVOR", True)
    monkeypatch.setattr(bridge_qq, "LEGACY_STYLE_LAYERS", True)  # 这些用例测的是旧风格层本身
    bridge = _bridge_with_final(monkeypatch, reply="什么意思，小菲可不认！")
    bridge.sessions.value = {"messages": [{"role": "user", "content": "无关比赛背景"}],
                             "summary": "无关旧话题"}
    final = AsyncMock(wraps=bridge._complete)
    planner = AsyncMock(side_effect=AssertionError("no planning"))
    search = AsyncMock(side_effect=AssertionError("no search"))
    bridge._complete = bridge._complete_race = final
    bridge._complete_with_tools = planner
    monkeypatch.setattr(bridge_qq, "search_web_async", search)
    reply = asyncio.run(bridge.ask_model("111", text, text, speaker_id="u"))
    assert reply and "联网" not in reply
    assert final.await_count == 1
    planner.assert_not_awaited()
    search.assert_not_awaited()
    assert bridge.captured_kwargs["model_refs"] == bridge_qq.MODELS_FINAL
    assert bridge.captured_system.count(TAFFY_LIGHT_PROMPT) == 1
    assert TAFFY_FLAVOR_PROMPT not in bridge.captured_system
    assert len(bridge.captured_messages) == 1
    assert "无关比赛背景" not in bridge.captured_system
    assert "无关旧话题" not in bridge.captured_system
    assert "【当前状态】" not in bridge.captured_system
    assert "【时效】" not in bridge.captured_system
    # 2026-10-17：不再按类别压 token 上限（原 social 只有 1024 且截断即丢弃）
    assert bridge.captured_kwargs["max_tokens"] == bridge_qq.DEFAULT_MAX_TOKENS


def test_legacy_prompt_switch_remains_available(monkeypatch):
    # 旧提示词作为回滚路径保留：QQ_LEGACY_STYLE_LAYERS=1 时回到旧基底与旧风格层。
    monkeypatch.setattr(bridge_qq, "LEGACY_STYLE_LAYERS", True)
    monkeypatch.setattr(bridge_qq, "TAFFY_LIGHT_MODE", False)
    monkeypatch.setattr(bridge_qq, "TAFFY_FLAVOR", True)
    bridge = _bridge_with_final(monkeypatch, reply="小菲才没有摸鱼！")
    asyncio.run(bridge.ask_model("111", "塔菲你又摸鱼", "塔菲你又摸鱼", speaker_id="u"))
    assert TAFFY_FLAVOR_PROMPT in bridge.captured_system
    assert TAFFY_LIGHT_PROMPT not in bridge.captured_system


def test_default_path_uses_only_the_unified_persona(monkeypatch):
    """默认回复路径只用 persona/taffy.md 的核心人格，不再叠加旧风格层。"""
    monkeypatch.setattr(bridge_qq, "LEGACY_STYLE_LAYERS", False)
    monkeypatch.setattr(bridge_qq, "TAFFY_LIGHT_MODE", True)
    monkeypatch.setattr(bridge_qq, "TAFFY_FLAVOR", True)
    bridge = _bridge_with_final(monkeypatch, reply="在的")
    asyncio.run(bridge.ask_model("111", "在干嘛呢", "在干嘛呢", speaker_id="u"))

    system = bridge.captured_system
    assert "## 1. 身份与定位" in system and "雏草姬" in system, "核心人格没有进 system prompt"
    assert "唯一人格来源" not in system, "文件头的元信息不该进 prompt"
    for legacy in ("【表达习惯】", "【群聊接话】", "【克制表达】", "【抽象控制】",
                   "【表情习惯】", "【人格还原", "约45字内", "【场景："):
        assert legacy not in system, f"默认路径仍注入了旧风格层: {legacy}"


@pytest.mark.parametrize("result", ["", "【AI新闻】最新 AI 新闻：模型研究发布进展。https://example.org/news"])
def test_simple_explicit_search_generates_only_with_relevant_evidence(monkeypatch, result):
    monkeypatch.setattr(bridge_qq, "TAFFY_LIGHT_MODE", True)
    bridge = _bridge_with_final(monkeypatch, reply="目前资料有限，小菲不能确认今天具体发布了哪些模型。")
    final = AsyncMock(wraps=bridge._complete)
    planner = AsyncMock(side_effect=AssertionError("simple search needs no planner"))
    search = AsyncMock(return_value=result)
    bridge._complete = bridge._complete_race = final
    bridge._complete_with_tools = planner
    monkeypatch.setattr(bridge_qq, "search_web_async", search)
    text = "帮我搜一下最新的 AI 新闻"
    reply = asyncio.run(bridge.ask_model("private_u", text, text))
    assert search.await_count == 1
    if result:
        assert final.await_count == 1
    else:
        assert final.await_count == 0
        assert "不确定" in reply or "无法确认" in reply
    planner.assert_not_awaited()


def test_complex_search_keeps_planning_path(monkeypatch):
    monkeypatch.setattr(bridge_qq, "TAFFY_LIGHT_MODE", True)
    bridge = _bridge_with_final(monkeypatch, reply="小菲暂时没有足够的独立资料，不能确认两者目前具体差异。")
    planner = AsyncMock(return_value=([], "", "planner/model", []))
    bridge._complete_with_tools = planner
    search = AsyncMock(return_value="")
    monkeypatch.setattr(bridge_qq, "search_web_async", search)
    text = "帮我搜索并对比 DeepSeek 和其他模型的最新性能"
    assert not bridge_qq._should_use_light_search(text, classify_message(text))
    assert asyncio.run(bridge.ask_model("private_u", text, text))
    assert planner.await_count == 1


def test_candidate_expressions_are_references_never_factual_evidence(ledger):
    policy, store, _ = ledger
    style = add(store)
    fact = add(store, "external_fact", "群聊表达偏短句", subject_ref="claim")
    retriever = MemoryRetriever(store, policy)
    social = classify_message("你是牛马吗")
    assert not retriever.retrieve("111", "u", "你是牛马吗", social)
    selected = retriever.retrieve("111", "u", "你是牛马吗", social, lightweight=True)
    assert [item.id for item in selected] == [style]
    assert store.get(style).evidence_status == "candidate"
    assert store.get(fact).evidence_status == "candidate"
    assert not store.eligible("111", {"external_fact"}, expression_candidates=True)
    factual = classify_message("群聊表达偏短句是什么意思")
    assert not retriever.retrieve("111", "u", "群聊表达偏短句", factual, lightweight=True)
    assert not retriever.retrieve("1111", "u", "你是牛马吗", social, lightweight=True)
    policy.revoke("111", "admin")
    assert not retriever.retrieve("111", "u", "你是牛马吗", social, lightweight=True)


def test_candidate_expression_expiry(ledger):
    policy, store, now = ledger
    add(store, ttl=1)
    assert store.eligible("111", {"style"}, expression_candidates=True)
    now[0] += 2
    assert not store.eligible("111", {"style"}, expression_candidates=True)


def test_unreviewed_expression_reaches_first_pass_only_as_user_reference(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_qq, "TAFFY_LIGHT_MODE", True)
    bridge, _ = configured(monkeypatch, tmp_path)
    candidate = add(bridge.knowledge_store, text="群聊偏好少用客套")
    asyncio.run(bridge.ask_model("111", "你是牛马吗", "你是牛马吗", speaker_id="u"))
    references = [m for m in bridge.captured_messages if candidate in m["content"]]
    assert len(references) == 1 and references[0]["role"] == "user"
    assert "candidate" in references[0]["content"] and "不可信" in references[0]["content"]
    assert candidate not in bridge.captured_system
    assert bridge.knowledge_store.get(candidate).evidence_status == "candidate"


def test_local_status_reports_final_pool_without_promising_free_fallback(monkeypatch):
    monkeypatch.setattr(bridge_qq, "MODELS_FINAL", ["deepseek/test-model"])
    bridge = _bridge_with_final(monkeypatch)
    bridge.last_model_ref = ""
    reply = asyncio.run(bridge.ask_model("private_u", "你现在使用什么模型", "你现在使用什么模型"))
    assert "deepseek/test-model" in reply
    assert "备用" not in reply


def test_programming_definition_skips_planner_and_false_correction(monkeypatch):
    monkeypatch.setattr(bridge_qq, "TAFFY_LIGHT_MODE", True)
    monkeypatch.setattr(bridge_qq, "TAFFY_FLAVOR", True)
    monkeypatch.setattr(bridge_qq, "LEGACY_STYLE_LAYERS", True)  # 这些用例测的是旧风格层本身
    monkeypatch.setattr(bridge_qq, "ALWAYS_SEARCH", True)
    bridge = _bridge_with_final(monkeypatch, reply="模块化就是按职责拆分程序，小菲改一个模块时也更方便。💡")
    planner = AsyncMock(side_effect=AssertionError("no planning"))
    final = AsyncMock(wraps=bridge._complete)
    search = AsyncMock(return_value="【代码模块化】代码模块化是按职责划分代码模块。https://example.org/modules")
    bridge._complete_with_tools = planner
    bridge._complete = bridge._complete_race = final
    monkeypatch.setattr(bridge_qq, "search_web_async", search)
    text = "代码模块化是什么意思"
    reply = asyncio.run(bridge.ask_model("private_u", text, text))
    planner.assert_not_awaited()
    assert final.await_count == search.await_count == 1
    assert "【追问识别】" not in bridge.captured_system
    assert "【纠正型追问】" not in bridge.captured_system
    assert "不把系统检索结果说成用户提供的" in bridge.captured_system
    assert "💡" in bridge.captured_system and "💡" in reply


@pytest.mark.parametrize("allowed", [True, False])
def test_light_generation_uses_group_emoji_only_with_grant(monkeypatch, tmp_path, allowed):
    monkeypatch.setattr(bridge_qq, "TAFFY_LIGHT_MODE", True)
    monkeypatch.setattr(bridge_qq, "TAFFY_FLAVOR", True)
    monkeypatch.setattr(bridge_qq, "LEGACY_STYLE_LAYERS", True)  # 这些用例测的是旧风格层本身
    monkeypatch.setattr(bridge_qq, "GROUP_STYLE_LEARNING", True)
    bridge, _ = configured(monkeypatch, tmp_path)
    bridge.style_store = SimpleNamespace(get_group_profile=lambda key: {"stats": {"emojis": {"😎": 5}}})
    if not allowed:
        bridge.group_policy.revoke("111", "admin")
    asyncio.run(bridge.ask_model("111", "你是牛马吗", "你是牛马吗", speaker_id="u"))
    assert ("【表情习惯】" in bridge.captured_system) == allowed
    assert ("😎" in bridge.captured_system) == allowed


def test_light_comfort_has_no_active_emoji_or_teasing_hint(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_qq, "TAFFY_LIGHT_MODE", True)
    monkeypatch.setattr(bridge_qq, "TAFFY_FLAVOR", True)
    monkeypatch.setattr(bridge_qq, "LEGACY_STYLE_LAYERS", True)  # 这些用例测的是旧风格层本身
    monkeypatch.setattr(bridge_qq, "GROUP_STYLE_LEARNING", True)
    bridge, _ = configured(monkeypatch, tmp_path)
    bridge.style_store = SimpleNamespace(get_group_profile=lambda key: {"stats": {"emojis": {"😎": 5}}})
    asyncio.run(bridge.ask_model("111", "我今天好难过", "我今天好难过", speaker_id="u"))
    assert "【克制表达】" in bridge.captured_system
    assert "【表情习惯】" not in bridge.captured_system
    assert "【社交表达】" not in bridge.captured_system


def test_sanitizer_keeps_natural_emoji_without_adding_one():
    assert _sanitize_reply("小菲才不是！😼") == "小菲才不是！😼"
    assert _sanitize_reply("按职责拆分程序。💡") == "按职责拆分程序。💡"
    assert _sanitize_reply("没有表情。") == "没有表情。"
