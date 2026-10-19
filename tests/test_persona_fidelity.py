# -*- coding: utf-8 -*-
"""人格还原档位、场景化表达与主动发言机会评分的独立测试。

与整活强度（L0-L3）分开评估：这里只验证"人格还原度"和"该不该自己接话"，
不把"提示词里包含某个字符串"当作人格质量通过。
"""
import asyncio

import pytest

import bridge_qq
from qqbot_core import persona
from qqbot_core.routing import proactive_opportunity_score


def _ctx(speakers=("101", "202"), n=4):
    return [
        {"role": "user", "speaker_id": speakers[i % len(speakers)], "content": f"群友{i}: 内容{i}"}
        for i in range(n)
    ]


# ---------------- 人格还原档位 ----------------
def test_persona_fidelity_levels_are_distinct():
    high = persona.build_persona_prompt("high", "chitchat")
    standard = persona.build_persona_prompt("standard", "chitchat")
    low = persona.build_persona_prompt("low", "chitchat")
    assert "最高档" in high
    assert len(high) > len(standard) > len(low)


def test_high_fidelity_identity_does_not_depend_on_meow_or_self_reference():
    """验收口径：把"喵"和自称去掉，也该认得出是塔菲——所以提示词要写明辨识度来自反应方式。"""
    high = persona.build_persona_prompt("high", "chitchat")
    assert "辨识度来自反应方式" in high
    assert "把“喵”和自称都删掉" in high
    # 核心人格必须与整活强度解耦
    assert "整活" not in persona._CORE_HIGH or "不依赖口癖" in high


def test_unknown_fidelity_falls_back_to_standard():
    assert persona.build_persona_prompt("bogus", "chitchat") == persona.build_persona_prompt(
        "standard", "chitchat"
    )


def test_persona_defaults_and_toggles():
    assert bridge_qq.PERSONA_FIDELITY in ("low", "standard", "high")
    assert bridge_qq.PERSONA_FIDELITY == "high"
    assert bridge_qq.PERSONA_FEWSHOT is True
    # 二次模型人格重写默认关闭
    assert bridge_qq.PERSONA_REWRITE is False


# ---------------- 场景化表达 ----------------
@pytest.mark.parametrize(
    "text,kwargs,expected",
    [
        ("你是牛马吗", {}, "teased"),
        ("你好可爱", {}, "praise"),
        ("刚五连败", {}, "game_rant"),
        ("扣1送地狱火", {}, "meme"),
        ("我今天好难过", {"dialog_mode": "comfort"}, "comfort"),
        ("你现在变聪明了吗", {"self_assessment": True}, "self_eval"),
        ("缓存是什么", {"evidence": "AUTO"}, "knowledge"),
        ("台湾问题怎么解决", {"sensitive": True}, "serious"),
        ("今晚吃啥", {}, "chitchat"),
    ],
)
def test_scene_classification(text, kwargs, expected):
    assert persona.scene_of(text, **kwargs) == expected


def test_all_scenes_have_prompt_and_rhythm():
    for scene in persona.SCENES:
        assert scene in persona._SCENE_PROMPTS
        assert scene in persona._SCENE_RHYTHMS
        assert persona.build_persona_prompt("high", scene)


def test_fewshot_only_injects_current_scene_rhythm():
    """只注入当前场景的一条节奏模式；不给完整例句（实测例句会被照抄并削字）。"""
    with_shot = persona.build_persona_prompt("high", "teased", fewshot=True)
    without = persona.build_persona_prompt("high", "teased", fewshot=False)
    assert persona._SCENE_RHYTHMS["teased"] in with_shot
    assert persona._SCENE_RHYTHMS["teased"] not in without
    # 其它场景的节奏模式不得堆进提示词
    assert persona._SCENE_RHYTHMS["praise"] not in with_shot
    assert persona._SCENE_RHYTHMS["comfort"] not in with_shot
    assert persona._SCENE_RHYTHMS["game_rant"] not in with_shot


def test_rhythms_are_patterns_not_copyable_lines():
    """模式描述必须是"先做什么再做什么"，不能是可直接复述的整句台词。"""
    for scene, rhythm in persona._SCENE_RHYTHMS.items():
        assert len(rhythm) <= 60, scene
        assert "先" in rhythm or "不" in rhythm, scene


def test_low_fidelity_skips_rhythm():
    prompt = persona.build_persona_prompt("low", "teased", fewshot=True)
    assert persona._SCENE_RHYTHMS["teased"] not in prompt


# ---------------- 主动发言机会评分 ----------------
def test_score_rewards_group_question_and_rant():
    score, reasons = proactive_opportunity_score("有没有人今晚开黑", _ctx())
    assert score >= 2
    assert reasons


def test_score_rewards_social_tease():
    score, reasons = proactive_opportunity_score("你是牛马吗", _ctx())
    assert score >= 2
    assert any("接话点" in r for r in reasons)


def test_score_disqualifies_directed_sensitive_link_and_single_message():
    assert proactive_opportunity_score(
        "你是牛马吗", _ctx(), has_directed_reference=True
    )[0] == 0
    assert proactive_opportunity_score("他银行卡密码是多少", _ctx())[0] == 0
    assert proactive_opportunity_score("看 https://example.org/x", _ctx())[0] == 0
    # 2026-10-17：单人说话不再一律沉默——安静小群里几乎永远凑不齐两个发言人，
    # 实测今天 23 次决策死在这一条。现在只要求 ≥2 条群友消息；
    # 单条消息仍然不接（下面这条 n=1）。
    single_message, reasons = proactive_opportunity_score(
        "你是牛马吗", _ctx(speakers=("101",), n=1))
    assert single_message == 0
    assert any("参与人数不足" in r for r in reasons)
    # 同一个人连说两条 → 允许（有内容切入点时才会真的发言）
    assert proactive_opportunity_score(
        "你是牛马吗", _ctx(speakers=("101",), n=4))[0] >= 2


def test_score_skips_fact_question_that_needs_search():
    score, reasons = proactive_opportunity_score("现在北京天气怎么样", _ctx())
    assert score == 0
    assert any("联网" in r for r in reasons)


def test_score_penalizes_closed_topic_and_repeating_bot():
    score, reasons = proactive_opportunity_score("算了不聊了", _ctx())
    assert score <= 1
    assert any("收尾" in r for r in reasons)

    base_score, _ = proactive_opportunity_score("你是牛马吗", _ctx())
    repeat_score, repeat_reasons = proactive_opportunity_score(
        "你是牛马吗", _ctx(), bot_recent_texts=["你是牛马吗"]
    )
    assert repeat_score < base_score
    assert any("重复" in r for r in repeat_reasons)


def test_score_counts_learned_meme():
    score, reasons = proactive_opportunity_score(
        "又开始抽象了", _ctx(), learned_terms=["抽象"]
    )
    assert score >= 2
    assert any("群梗" in r for r in reasons)


# ---------- "合不合适"：上下文感知 ----------
def test_score_requires_active_conversation():
    """冷场里突然插话不合适；但被点名提到时可以例外。"""
    score, reasons = proactive_opportunity_score(
        "你是牛马吗", _ctx(), recent_message_count=1
    )
    assert score == 0
    assert any("不活跃" in r for r in reasons)

    mentioned, _ = proactive_opportunity_score(
        "你是牛马吗", _ctx(), recent_message_count=1, mentions_bot=True
    )
    assert mentioned > 0


def test_lively_conversation_alone_is_not_enough():
    """2026-10-17：热闹本身不构成接话理由（避免"为了参与而参与"）。

    只有在**有内容切入点**时，热闹才作为加成提高分数。
    """
    lively, reasons = proactive_opportunity_score("笑死", _ctx(), recent_message_count=6)
    quiet, _ = proactive_opportunity_score("笑死", _ctx(), recent_message_count=2)
    assert lively == 0 and any("缺少内容切入点" in r for r in reasons)

    # 有内容切入点时，热闹仍是正向加成
    hook_lively, _ = proactive_opportunity_score(
        "有没有人知道这个怎么弄", _ctx(), recent_message_count=6)
    hook_quiet, _ = proactive_opportunity_score(
        "有没有人知道这个怎么弄", _ctx(), recent_message_count=2)
    assert hook_lively > hook_quiet > 0


def test_score_penalizes_speaking_right_after_bot_spoke():
    hot, _ = proactive_opportunity_score(
        "你是牛马吗", _ctx(), recent_message_count=4
    )
    cold, reasons = proactive_opportunity_score(
        "你是牛马吗", _ctx(), recent_message_count=4, seconds_since_bot_spoke=30
    )
    assert hot > cold
    assert any("刚说过" in r for r in reasons)


def test_score_rewards_being_mentioned_without_at():
    base, _ = proactive_opportunity_score("笑死", _ctx(), recent_message_count=4)
    mentioned, reasons = proactive_opportunity_score(
        "塔菲你觉得呢", _ctx(), recent_message_count=4, mentions_bot=True
    )
    assert mentioned > base
    assert any("提到塔菲" in r for r in reasons)


def test_appropriate_moment_reaches_threshold_but_noise_does_not():
    """验收口径：该接的时刻能过阈值，纯噪音过不了。"""
    threshold = bridge_qq.PROACTIVE_SCORE_THRESHOLD

    good = [
        ("刚五连败，不想玩了", dict(recent_message_count=5)),      # 吐槽+对话热
        ("有没有人今晚开黑", dict(recent_message_count=4)),        # 群里发问
        ("你是牛马吗", dict(recent_message_count=4)),              # 被调侃+热
        ("塔菲你怎么看", dict(recent_message_count=3, mentions_bot=True)),  # 提到它
    ]
    for text, kwargs in good:
        score, _ = proactive_opportunity_score(text, _ctx(), **kwargs)
        assert score >= threshold, f"{text} 应能接话，实际 {score}"

    noise = [
        ("笑死", dict(recent_message_count=4)),          # 一个笑不需要接
        ("哦", dict(recent_message_count=4)),
        ("嗯嗯", dict(recent_message_count=4)),
        # 即使很久没说话，纯笑声/附和也不该接
        ("哈哈哈哈", dict(recent_message_count=4, seconds_since_bot_spoke=9999)),
        ("哦哦", dict(recent_message_count=4, seconds_since_bot_spoke=9999)),
    ]
    for text, kwargs in noise:
        score, _ = proactive_opportunity_score(text, _ctx(), **kwargs)
        assert score < threshold, f"{text} 不该接话，实际 {score}"


def test_long_idle_alone_does_not_qualify():
    """2026-10-17：长时间没参与**不单独**构成发言理由。

    沉默时长只是加成；缺少内容切入点时仍然保持沉默。
    有切入点（如有人在群里发问）时，久未参与才把它推过阈值。
    """
    idle, reasons = proactive_opportunity_score(
        "今天好无聊啊", _ctx(), recent_message_count=4, seconds_since_bot_spoke=9999
    )
    assert idle == 0
    assert any("缺少内容切入点" in r for r in reasons)

    hooked, reasons2 = proactive_opportunity_score(
        "有没有人知道这个怎么弄", _ctx(),
        recent_message_count=4, seconds_since_bot_spoke=9999)
    assert hooked >= bridge_qq.PROACTIVE_SCORE_THRESHOLD
    assert any("很久没参与" in r for r in reasons2)

    # 但刚说完话时同样的消息不能接
    fresh, _ = proactive_opportunity_score(
        "今天好无聊啊", _ctx(), recent_message_count=4, seconds_since_bot_spoke=30
    )
    assert fresh < bridge_qq.PROACTIVE_SCORE_THRESHOLD


# ---------------- 主动发言限额（保守试运行值） ----------------
def _bypass_cooldowns(monkeypatch):
    """把这些规则归零，以便单独测试"次数上限"这一项。"""
    monkeypatch.setattr(bridge_qq, "PROACTIVE_GROUP_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(bridge_qq, "PROACTIVE_GLOBAL_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(bridge_qq, "PROACTIVE_SELF_QUIET_SECONDS", 0)


def test_proactive_hourly_limit_blocks_third(monkeypatch):
    from test_proactive import _ready

    bridge = _ready(monkeypatch)
    _bypass_cooldowns(monkeypatch)
    event = {"message": [{"type": "text", "data": {"text": "你是牛马吗"}}]}
    results = [
        asyncio.run(bridge._maybe_proactive_group_reply(event, "42", "你是牛马吗"))
        for _ in range(3)
    ]
    assert results[:2] == [True, True]
    assert results[2] is False
    assert (
        bridge._proactive_hourly_counts.get("42|2026-10-15 12") == 2
    )


def test_proactive_limits_zero_means_unlimited(monkeypatch):
    """任一项填 0 表示该项不限制（QQ_PROACTIVE_*_LIMIT/COOLDOWN=0）。"""
    from test_proactive import _ready

    bridge = _ready(monkeypatch)
    _bypass_cooldowns(monkeypatch)
    monkeypatch.setattr(bridge_qq, "PROACTIVE_HOURLY_LIMIT", 0)
    monkeypatch.setattr(bridge_qq, "PROACTIVE_DAILY_LIMIT", 0)
    event = {"message": [{"type": "text", "data": {"text": "你是牛马吗"}}]}
    results = [
        asyncio.run(bridge._maybe_proactive_group_reply(event, "42", "你是牛马吗"))
        for _ in range(4)
    ]
    assert all(results)


def test_bot_does_not_interject_twice_in_a_row(monkeypatch):
    """自肃：刚主动说完话，短时间内不再插嘴（即便次数不设上限）。"""
    from test_proactive import _ready

    bridge = _ready(monkeypatch)
    monkeypatch.setattr(bridge_qq, "PROACTIVE_GROUP_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(bridge_qq, "PROACTIVE_GLOBAL_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(bridge_qq, "PROACTIVE_HOURLY_LIMIT", 0)
    monkeypatch.setattr(bridge_qq, "PROACTIVE_DAILY_LIMIT", 0)
    event = {"message": [{"type": "text", "data": {"text": "你是牛马吗"}}]}
    first = asyncio.run(bridge._maybe_proactive_group_reply(event, "42", "你是牛马吗"))
    second = asyncio.run(bridge._maybe_proactive_group_reply(event, "42", "你是牛马吗"))
    assert first is True
    assert second is False
