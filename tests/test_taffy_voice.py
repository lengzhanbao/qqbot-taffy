"""Offline speech-tool, delivery, history, and overload regressions. No QQ traffic."""
import asyncio
import io
import json
import os
import threading
import types
import uuid
import wave
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import bridge_qq as app
from qqbot_core import voice
from services.taffy_tts import TaffyHandler, text_chunks
from test_routing import _bridge_with_final


def call(text="晚上好呀，我是塔菲喵！", arguments=None):
    return {"id": "call_voice", "type": "function", "function": {
        "name": "taffy_speak", "arguments": (
            json.dumps({"text": text}, ensure_ascii=False) if arguments is None else arguments),
    }}


def ok(mid=123):
    return {"status": "ok", "retcode": 0, "data": {"message_id": mid}}


@pytest.fixture
def workspace():
    path = Path(os.environ["QQ_TEST_DATA_DIR"]) / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


@pytest.fixture
def model(monkeypatch):
    bridge = object.__new__(app.Bridge)
    bridge.api_key = "offline"
    bridge.model_semaphore = None
    monkeypatch.setattr(app, "_model_health", {})
    monkeypatch.setattr(app, "_provider_cooldown", {})
    monkeypatch.setattr(app, "_tool_model_cooldown", {})
    monkeypatch.setattr(app, "inc_stat", lambda *a: None)
    monkeypatch.setattr(app, "write_health", lambda **k: None)
    monkeypatch.setattr(app, "PROVIDERS", {"deepseek": {"key": "offline", "url": "https://example.invalid"}})
    return bridge


@pytest.mark.parametrize("speech", [False, True])
def test_same_final_generation_decides_medium(model, speech):
    async def api(body, *args):
        assert body["tool_choice"] == "auto"
        assert body["messages"][0]["content"].count(voice.CAPABILITY) == 1
        assert body["max_tokens"] == 2400
        return "用文字也可以。", "ok", [call()] if speech else [], "stop", []
    model._run_api_call = AsyncMock(side_effect=api)
    reply, _ = asyncio.run(model._complete(
        "完整塔菲人格", [{"role": "user", "content": "你想说话吗？"}],
        model_refs=["deepseek/deepseek-flash"], max_tokens=2400, allow_voice=True))
    assert isinstance(reply, voice.VoiceReply) is speech
    assert reply == ("晚上好呀，我是塔菲喵！" if speech else "用文字也可以。")
    assert model._run_api_call.await_count == 1


def test_tool_planner_filter_does_not_remove_final_speech_tool(model):
    async def api(body, *args):
        assert body["model"] == "deepseek-flash"
        assert any(tool["function"]["name"] == "taffy_speak" for tool in body["tools"])
        return "", "ok", [call()], "tool_calls", []

    model._run_api_call = AsyncMock(side_effect=api)
    reply, _ = asyncio.run(model._complete(
        "人格", [], model_refs=["deepseek/deepseek-flash"],
        max_tokens=2400, allow_voice=True))
    assert reply == "晚上好呀，我是塔菲喵！"


def test_empty_search_planner_uses_search_then_keeps_voice_final(monkeypatch):
    spoken = voice.VoiceReply("今天北京多云，塔菲给你播报喵。")
    bridge = _bridge_with_final(monkeypatch, reply=spoken)
    monkeypatch.setattr(app, "ALWAYS_SEARCH", False)
    monkeypatch.setattr(app, "MODELS_TOOL_PLANNING", [])
    monkeypatch.setattr(app, "_should_use_light_search", lambda *_: False)
    monkeypatch.setattr(app, "_should_use_single_shot_search", lambda *_: False)
    planned = AsyncMock(return_value=([], "", "", []))
    bridge._complete_with_tools = planned
    searches = []

    async def search(query, **kwargs):
        searches.append(query)
        return "【北京天气】今天北京多云，气温适宜。"

    monkeypatch.setattr(app, "search_web_async", search)
    reply = asyncio.run(bridge.ask_model(
        "private_u", "现在北京天气怎么样", "现在北京天气怎么样", "测试用户"))

    assert searches == [app._generic_fallback_query("现在北京天气怎么样")]
    assert planned.await_count == 1
    assert planned.await_args.kwargs["model_refs"] == []
    assert bridge.captured_kwargs["allow_voice"] is True
    assert isinstance(reply, voice.VoiceReply)


def test_internal_generation_has_no_speech_tool(model):
    model._run_api_call = AsyncMock(return_value=("内部摘要。", "ok", [], "stop", []))
    asyncio.run(model._complete("摘要", [], model_refs=["deepseek/deepseek-flash"]))
    body = model._run_api_call.await_args.args[0]
    assert "tools" not in body and voice.CAPABILITY not in body["messages"][0]["content"]


def test_existing_tool_definitions_survive():
    body = {"messages": [{"role": "system", "content": "人格"}], "tools": list(app.TOOLS)}
    voice.enable_speech(body)
    voice.enable_speech(body)
    assert body["tools"][:-1] == app.TOOLS
    assert body["tools"][-1] == voice.SPEAK_TOOL
    assert body["messages"][0]["content"].count(voice.CAPABILITY) == 1


@pytest.mark.parametrize("args", ["{", "[]", "{}", '{"text":"  "}', '{"text":42}'])
def test_invalid_arguments_produce_one_text_fallback(model, args):
    normalized = app._normalize_tool_calls([call(arguments=args)])
    assert normalized, "malformed speech calls must reach the terminal fallback"
    model._run_api_call = AsyncMock(return_value=("", "ok", normalized, "stop", []))
    reply, _ = asyncio.run(model._complete("人格", [], model_refs=["deepseek/deepseek-flash"], allow_voice=True))
    assert reply == voice.INVALID_TOOL_REPLY and not isinstance(reply, voice.VoiceReply)
    assert model._run_api_call.await_count == 1


def test_ambiguous_and_truncated_calls_never_send_multiple_voices():
    research = {"function": {"name": "search_web", "arguments": "{}"}}
    assert voice.final_speech_reply([research, call()]) == voice.INVALID_TOOL_REPLY
    assert voice.final_speech_reply([call(), call()]) == voice.INVALID_TOOL_REPLY
    assert voice.final_speech_reply([call()], finish_reason="length") == voice.INVALID_TOOL_REPLY
    assert voice.final_speech_reply([call(arguments="bad")], "同次生成的文字回答。") == "同次生成的文字回答。"


def test_streamed_arguments_are_reassembled_without_leaking_protocol(model):
    lines = []
    for fragment in [
        {"index": 0, "id": "v", "type": "function", "function": {"name": "taffy_speak", "arguments": '{"text":"晚上'}},
        {"index": 0, "function": {"arguments": '好呀！"}'}},
    ]:
        lines.append("data: " + json.dumps({"choices": [{"delta": {"tool_calls": [fragment]}}]}, ensure_ascii=False))
    lines += ['data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}', "data: [DONE]"]
    _, state, calls, reason, _ = model._parse_stream_response(lines)
    reply = voice.final_speech_reply(calls, finish_reason=reason)
    assert state == "ok" and reply == "晚上好呀！" and isinstance(reply, voice.VoiceReply)


@pytest.mark.parametrize("key", ["111", "private_u"])
def test_voice_history_is_plain_text_and_next_turn_can_recall(monkeypatch, key):
    spoken = voice.VoiceReply("晚上好呀，我刚刚在听音乐。")
    bridge = _bridge_with_final(monkeypatch, reply=spoken)
    reply = asyncio.run(bridge.ask_model(key, "你好呀", "你好呀", speaker_id="u"))
    saved = bridge.sessions.saved
    assistant = saved["messages"][-1]
    assert reply is spoken and assistant["content"] == str(spoken)
    assert type(assistant["content"]) is str
    assert assistant["reply_medium"] == "voice" and assistant["reply_id"] == spoken.reply_id
    assert "tool_calls" not in assistant and saved["last_answer"] == str(spoken)
    assert bridge.captured_kwargs["allow_voice"] is True
    bridge.sessions.value = saved
    asyncio.run(bridge.ask_model(key, "刚才那个继续说", "刚才那个继续说", speaker_id="u"))
    context = bridge.captured_messages
    assert any(m.get("role") == "assistant" and m["content"] == str(spoken) for m in context)
    assert all(set(m) <= {"role", "content"} for m in context)


def test_voice_text_not_truncated_or_rewritten(monkeypatch):
    spoken = voice.VoiceReply("原始完整回答，" * 2000)
    bridge = _bridge_with_final(monkeypatch, reply=spoken)
    reply = asyncio.run(bridge.ask_model("111", "你好呀", "你好呀", speaker_id="u"))
    assert reply is spoken and bridge.sessions.saved["last_answer"] == str(spoken)


@pytest.mark.parametrize("record_result,expected,medium,text_calls", [
    (ok(), "sent", "voice", 0),
    ({"status": "unknown", "_delivery_state": "unknown"}, "unknown", "voice", 0),
    ({"status": "ok", "retcode": 0, "data": {}}, "unknown", "voice", 0),
    ({"status": "failed", "retcode": 1200}, "sent", "text", 1),
])
def test_record_status_dedup_and_restart(workspace, record_result, expected, medium, text_calls):
    delivery = voice.VoiceDelivery(workspace)
    delivery.synthesize = MagicMock(return_value={"type": "record", "data": {"file": "base64://mock"}})
    spoken = voice.VoiceReply("保留我真正说的完整内容。")
    send_record = AsyncMock(return_value=record_result)
    send_text = AsyncMock(return_value=ok(456))
    async def run():
        first = await delivery.deliver(spoken, "111", send_record, send_text, lambda _: None)
        restarted = voice.VoiceDelivery(workspace)
        second = await restarted.deliver(spoken, "111", send_record, send_text, lambda _: None)
        return first, second
    first, second = asyncio.run(run())
    assert first["_delivery_state"] == expected and first["_reply_medium"] == medium
    assert second["_duplicate"] is True
    send_record.assert_awaited_once()
    assert send_text.await_count == text_calls
    if text_calls:
        assert send_text.await_args.args[0] == str(spoken)


@pytest.mark.parametrize("failure", ["offline", "queue_full", "invalid_wav"])
def test_tts_failure_falls_back_to_original_text(workspace, failure):
    delivery = voice.VoiceDelivery(workspace)
    if failure == "queue_full":
        delivery.pending = delivery.MAX_PENDING
    delivery.synthesize = MagicMock(side_effect=ValueError(failure))
    text = "**原样回答**，不用二次改写。"
    send_text, send_record = AsyncMock(return_value=ok()), AsyncMock()
    result = asyncio.run(delivery.deliver(voice.VoiceReply(text), "111", send_record, send_text, lambda _: None))
    send_text.assert_awaited_once_with(text)
    send_record.assert_not_awaited()
    assert result["_reply_medium"] == "text"


def test_cancellation_after_dispatch_stays_unknown_on_retry(workspace):
    delivery = voice.VoiceDelivery(workspace)
    delivery.synthesize = MagicMock(return_value={"type": "record"})
    send_record = AsyncMock(side_effect=asyncio.CancelledError)
    send_text = AsyncMock()
    spoken = voice.VoiceReply("取消时可能已经送达。")
    async def run():
        with pytest.raises(asyncio.CancelledError):
            await delivery.deliver(spoken, "111", send_record, send_text, lambda _: None)
        return await delivery.deliver(spoken, "111", send_record, send_text, lambda _: None)
    result = asyncio.run(run())
    assert result["_delivery_state"] == "unknown"
    send_record.assert_awaited_once()
    send_text.assert_not_awaited()


def test_bridge_sends_only_record_and_observes_spoken_text(workspace, monkeypatch):
    bridge = object.__new__(app.Bridge)
    bridge.sessions = app.SessionStore(str(workspace / "sessions.json"))
    spoken = voice.VoiceReply("我是本轮的完整语音回答。")
    bridge.sessions.put("111", {"messages": [{"role": "assistant", "content": str(spoken), "reply_id": spoken.reply_id}]})
    bridge.voice_delivery = voice.VoiceDelivery(workspace)
    bridge.voice_delivery.synthesize = MagicMock(return_value={"type": "record", "data": {"file": "base64://mock"}})
    bridge.call_api = AsyncMock(return_value=ok())
    bridge.send_group_msg = AsyncMock()
    bridge._observe_sent_group_message = MagicMock()
    monkeypatch.setattr(app, "write_health", lambda **k: None)
    asyncio.run(bridge._send_model_reply("111", "222", spoken))
    args, kwargs = bridge.call_api.await_args
    assert args[0] == "send_group_msg" and kwargs == {"delivery_once": True}
    assert args[1]["message"][0]["type"] == "record"
    bridge.send_group_msg.assert_not_awaited()
    assert bridge._observe_sent_group_message.call_args.args[1] == str(spoken)
    stored = bridge.sessions.get("111")["messages"][-1]
    assert stored["reply_medium"] == "voice" and stored["delivery_state"] == "sent"


def test_onebot_timeout_does_not_retry_but_text_keeps_five_attempts(monkeypatch):
    bridge = object.__new__(app.Bridge)
    urlopen = MagicMock(side_effect=TimeoutError("confirmation lost"))
    monkeypatch.setattr(app.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(app, "write_health", lambda **k: None)
    monkeypatch.setattr(app.asyncio, "sleep", AsyncMock())
    result = asyncio.run(bridge.call_api("send_group_msg", {}, delivery_once=True))
    assert result["_delivery_state"] == "unknown" and urlopen.call_count == 1
    urlopen.reset_mock()
    asyncio.run(bridge.call_api("send_group_msg", {}))
    assert urlopen.call_count == 5


def test_server_queue_full_and_invalid_input_do_not_infer():
    handler = object.__new__(TaffyHandler)
    engine = types.SimpleNamespace(synthesize=MagicMock())
    handler.path = "/synthesize"
    handler.server = types.SimpleNamespace(queue_slots=threading.BoundedSemaphore(1), engine=engine)
    handler.respond = MagicMock()
    handler.server.queue_slots.acquire()
    handler.do_POST()
    assert handler.respond.call_args.args[0] == 503
    handler.server.queue_slots.release()
    handler.headers = {"Content-Length": "11"}
    handler.rfile = io.BytesIO(b'{"text":42}')
    handler.do_POST()
    assert handler.respond.call_args.args[0] == 400
    engine.synthesize.assert_not_called()


def test_chunking_keeps_entire_answer_and_wav_validation():
    text = "完整文本，" * 100 + "结尾。"
    chunks = list(text_chunks(text))
    assert "".join(chunks) == text and all(len(chunk) <= 120 for chunk in chunks)
    with pytest.raises((ValueError, wave.Error, EOFError)):
        voice.validate_wav(b"not a wav")
