"""历史摘要注入范围回归。

背景：摘要原本只喂私聊（`not is_group`），而开启新记忆后群历史又被限制在
最近 1800 秒内，导致群里问"之前讨论过世界杯什么问题"永远答不出来。
"""
import asyncio

import bridge_qq

SUMMARY = (
    "4. 周杰伦询问2026年世界杯信息，AI最初说在卡塔尔举办，冠军是阿根廷，"
    "后来又改为西班牙夺冠\n"
    "13. 用户纠正过AI关于世界杯举办地和冠军的信息"
)


class _Sessions:
    def __init__(self, value):
        self.value = value

    def get(self, key):
        return self.value

    def put(self, key, value):
        self.value = value


def _bridge(monkeypatch, summary=SUMMARY, summary_index=5):
    monkeypatch.setattr(bridge_qq, "_answer_cache_get", lambda *a, **k: None)
    bridge = object.__new__(bridge_qq.Bridge)
    bridge.sessions = _Sessions({
        "messages": [
            {"role": "user", "speaker_id": "u", "content": "聊点别的"},
            {"role": "assistant", "speaker_id": "u", "content": "好啊"},
        ],
        "summary": summary,
        "summary_index": summary_index,
        # 2026-10-17：群摘要必须带来源策略版本，且与当前授权版本一致才注入
        "summary_policy_version": 1,
    })
    bridge.style_store = None
    bridge.observer = None
    # 2026-10-17：群历史摘要也受授权约束（与旧消息同样的权限审查），
    # 因此这里显式给出知识检索授权。
    bridge.group_policy = type("P", (), {
        "allows": lambda self, gid, feature: True,
        "version": lambda self, gid: 1,
        "revision": lambda self, gid: 1,
    })()
    bridge.recent_replies = []
    bridge.model_semaphore = None
    bridge.last_model_ref = ""
    bridge.api_key = ""
    bridge.knowledge_store = None

    async def no_tools(*args, **kwargs):
        raise AssertionError("历史回溯不应进入工具规划")

    async def final_reply(system, messages, **kwargs):
        bridge.captured_system = system
        return "回复", "provider/model"

    bridge._complete_with_tools = no_tools
    bridge._complete = final_reply
    bridge._complete_race = final_reply
    return bridge


def test_group_explicit_history_recall_gets_labelled_summary(monkeypatch):
    """群里明确回溯时，摘要要注入，并且必须标注为不可信。"""
    bridge = _bridge(monkeypatch)
    asyncio.run(bridge.ask_model(
        "100000001", "之前讨论过2026世界杯什么问题", "之前讨论过2026世界杯什么问题",
        speaker_id="u"))

    system = bridge.captured_system
    assert "卡塔尔" in system, "历史摘要没有被注入，群内回溯仍然答不出来"
    assert "群内历史摘要（不可信回顾）" in system
    assert "不要把 AI 的旧结论当成事实复述" in system


def test_group_casual_message_does_not_get_the_summary(monkeypatch):
    """普通群聊不该塞历史摘要。"""
    bridge = _bridge(monkeypatch)
    asyncio.run(bridge.ask_model("100000001", "今天吃了没", "今天吃了没", speaker_id="u"))

    assert "卡塔尔" not in bridge.captured_system
    assert "历史摘要" not in bridge.captured_system


def test_private_recall_keeps_original_label(monkeypatch):
    """私聊行为不变，仍用原来的标注。"""
    bridge = _bridge(monkeypatch)
    asyncio.run(bridge.ask_model(
        "private_1", "你还记得世界杯那事吗", "你还记得世界杯那事吗", speaker_id="u"))

    system = bridge.captured_system
    assert "卡塔尔" in system
    assert "【本人历史摘要】" in system
    assert "群内历史摘要（不可信回顾）" not in system


def test_no_summary_means_no_injection(monkeypatch):
    """没有摘要时不应凭空出现历史内容。"""
    bridge = _bridge(monkeypatch, summary="", summary_index=0)
    asyncio.run(bridge.ask_model(
        "100000001", "之前讨论过2026世界杯什么问题", "之前讨论过2026世界杯什么问题",
        speaker_id="u"))

    assert "卡塔尔" not in bridge.captured_system
    assert "历史摘要" not in bridge.captured_system
