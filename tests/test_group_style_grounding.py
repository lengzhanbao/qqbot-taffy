# -*- coding: utf-8 -*-
"""群习惯问答回归：被问到群聊习惯时要注入真实统计，并且只在被问到时注入。"""
import asyncio
import json
import os

import bridge_qq


def _write_profiles(tmp_path):
    path = os.path.join(str(tmp_path), "group_profiles.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "100000001": {
                "stats": {
                    "fragments": {"666": 1, "6": 5, "牛马": 1},
                    "emojis": {"😼": 2},
                    "patterns": {},
                    "particles": {"吧": 4, "呢": 1},
                    "addresses": {},
                    "punct": {"question": 2},
                    "length": {"sum": 1179, "count": 145, "short_count": 135},
                    "emoji_positions": {"mid": 0, "end": 0},
                },
                "last_updated": 1792075156.1,
            }
        }, f, ensure_ascii=False)
    return path


class _Sessions:
    def __init__(self):
        self.value = {}

    def get(self, key):
        return self.value

    def put(self, key, value):
        self.value = value


def _bridge(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_qq, "_answer_cache_get", lambda *a, **k: None)
    bridge = object.__new__(bridge_qq.Bridge)
    bridge.sessions = _Sessions()
    bridge.style_store = bridge_qq.GroupStyleStore(_write_profiles(tmp_path))
    bridge.observer = None
    bridge.recent_replies = []
    bridge.model_semaphore = None
    bridge.last_model_ref = ""
    bridge.api_key = ""
    bridge.knowledge_store = None

    async def final_reply(system, messages, **kwargs):
        bridge.captured_system = system
        return "回复", "provider/model"

    bridge._complete_with_tools = final_reply
    bridge._complete = final_reply
    bridge._complete_race = final_reply
    return bridge


def test_group_style_question_injects_real_stats(monkeypatch, tmp_path):
    """回归：这个问题以前只会让模型现编。"""
    bridge = _bridge(monkeypatch, tmp_path)
    asyncio.run(bridge.ask_model("100000001", "这个群有什么聊天习惯", "这个群有什么聊天习惯",
                                 speaker_id="u"))

    system = bridge.captured_system
    assert "群聊习惯（真实统计）" in system
    assert "只依据上面的统计回答" in system
    # 真实统计必须进提示词：145 条里 135 条短消息 → 平均 8 字、93% 短消息
    assert "93%是短消息" in system
    assert "6、666、牛马" in system


def test_ordinary_chat_does_not_get_style_stats(monkeypatch, tmp_path):
    bridge = _bridge(monkeypatch, tmp_path)
    asyncio.run(bridge.ask_model("100000001", "今天吃了没", "今天吃了没", speaker_id="u"))

    assert "群聊习惯（真实统计）" not in bridge.captured_system


def test_style_stats_not_injected_in_private(monkeypatch, tmp_path):
    bridge = _bridge(monkeypatch, tmp_path)
    asyncio.run(bridge.ask_model("private_1", "这个群有什么聊天习惯",
                                 "这个群有什么聊天习惯", speaker_id="u"))

    assert "群聊习惯（真实统计）" not in bridge.captured_system
