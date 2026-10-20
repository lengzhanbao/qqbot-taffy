"""Final-answer speech tool and bounded, at-most-once OneBot delivery."""
import asyncio
import base64
import io
import json
import sqlite3
import threading
import time
import uuid
import wave
from contextlib import contextmanager
from pathlib import Path

TOOL_NAME = "taffy_speak"
CAPABILITY = (
    "【语音能力】你现在可以用自己的声音直接回复群友。如果觉得在当前对话中开口更自然，"
    "可以调用 taffy_speak，说出你真正想说的完整回答；更适合文字时就正常回复。"
    "群友希望听你说话时可以使用，你也可以主动选择。没有固定频率，保持原有聊天风格。"
    "用户明确希望文字回答时尊重这一偏好。taffy_speak 是最终回答形式，成功后不会再发一份文字。"
)
SPEAK_TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "用塔菲的声音说出完整最终回答。表达方式由你决定；需要其他工具时先完成取证，再决定最终回复媒介。",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "希望亲口说出的完整回答"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
}
INVALID_TOOL_REPLY = "这次语音内容没组织好，请再说一遍你想问的吧。"


class VoiceReply(str):
    """String-compatible visible answer; metadata never enters model content."""
    def __new__(cls, text, reply_id=None):
        obj = super().__new__(cls, text)
        obj.reply_id = reply_id or uuid.uuid4().hex
        return obj


def enable_speech(body):
    """Append one capability and one tool, preserving existing definitions."""
    tools = list(body.get("tools") or [])
    if not any(t.get("function", {}).get("name") == TOOL_NAME for t in tools):
        tools.append(SPEAK_TOOL)
    body["tools"] = tools
    body["tool_choice"] = "auto"
    system = body["messages"][0]
    if CAPABILITY not in system["content"]:
        system["content"] += "\n" + CAPABILITY


def final_speech_reply(calls, content="", finish_reason=""):
    """Consume terminal speech calls without a second model request or side effects."""
    speech = [tc for tc in calls or [] if tc.get("function", {}).get("name") == TOOL_NAME]
    if not speech:
        return None
    # A terminal answer must be complete and unambiguous. Never send a batch of voices.
    if len(speech) == 1 and len(calls) == 1 and finish_reason != "length":
        try:
            args = speech[0]["function"].get("arguments", "")
            args = json.loads(args) if isinstance(args, str) else args
            text = args.get("text") if isinstance(args, dict) else None
            if isinstance(text, str) and text.strip():
                return VoiceReply(text)
        except (ValueError, TypeError):
            pass
    # Plain assistant content from this same generation is a usable fallback.
    if isinstance(content, str) and content.strip() and not any(
        marker in content for marker in ("tool_calls", "<tool_call", "<｜DSML｜", '"arguments"')
    ):
        return content
    return INVALID_TOOL_REPLY


def delivery_state(result):
    if not isinstance(result, dict):
        return "unknown"
    if result.get("_delivery_state") in {"failed", "unknown"}:
        return result["_delivery_state"]
    data = result.get("data") or {}
    if (result.get("status") == "ok" and result.get("retcode", 0) == 0
            and isinstance(data, dict) and data.get("message_id")):
        return "sent"
    if result.get("status") == "failed" and result.get("retcode") not in (None, 0):
        return "failed"
    return "unknown"


def validate_wav(payload):
    with wave.open(io.BytesIO(payload), "rb") as wav:
        if (wav.getnchannels() != 1 or wav.getsampwidth() != 2
                or wav.getframerate() != 44100 or wav.getnframes() < 1):
            raise ValueError("Invalid Taffy PCM WAV")
        frames = wav.readframes(wav.getnframes())
        if len(frames) != wav.getnframes() * 2 or not any(frames):
            raise ValueError("Empty, silent or truncated WAV")


class VoiceDelivery:
    MAX_AUDIO_BYTES = 64 * 1024 * 1024
    MAX_CACHE_BYTES = 512 * 1024 * 1024
    MAX_PENDING = 4

    def __init__(self, runtime_dir, url="http://127.0.0.1:17861"):
        self.root = Path(runtime_dir) / "data" / "tts"
        self.url = url
        self.pending = 0
        self._cache_lock = threading.Lock()

    @contextmanager
    def _db(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / "deliveries.sqlite3", timeout=5)
        try:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY, target TEXT, "
                           "state TEXT, medium TEXT, updated REAL, result TEXT)")
                yield db
        finally:
            db.close()

    def _claim(self, reply_id, target):
        with self._db() as db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO deliveries VALUES (?, ?, 'preparing', 'voice', ?, '{}')",
                (reply_id, target, time.time()),
            ).rowcount
            if inserted:
                return None
            row = db.execute("SELECT state, medium, result FROM deliveries WHERE id=?", (reply_id,)).fetchone()
            result = json.loads(row[2])
            result.update(_delivery_state=row[0] if row[0] in ("sent", "failed") else "unknown",
                          _reply_medium=row[1], _duplicate=True)
            return result

    def _record(self, reply_id, state, medium, result):
        try:
            with self._db() as db:
                db.execute("UPDATE deliveries SET state=?, medium=?, updated=?, result=? WHERE id=?",
                           (state, medium, time.time(), json.dumps(result, ensure_ascii=False), reply_id))
            return True
        except (OSError, sqlite3.Error):
            # Never trigger another visible answer after a possibly successful send.
            return False

    def synthesize(self, text, reply_id):
        import urllib.request
        output = self.root / "audio"
        output.mkdir(parents=True, exist_ok=True)
        used = sum(p.stat().st_size for p in output.glob("*.wav"))
        if used + self.MAX_AUDIO_BYTES > self.MAX_CACHE_BYTES:
            raise RuntimeError("Audio retention limit reached; cleanup awaits deletion consent")
        request = urllib.request.Request(
            self.url + "/synthesize", data=json.dumps({"text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        # Local only: do not honor system HTTP proxies or follow redirects.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=150) as response:
            if response.status != 200 or response.headers.get_content_type() != "audio/wav":
                raise ValueError("TTS did not return WAV")
            payload = response.read(self.MAX_AUDIO_BYTES + 1)
        if len(payload) > self.MAX_AUDIO_BYTES:
            raise ValueError("TTS response exceeds transport limit")
        validate_wav(payload)
        # A random name, exclusive creation, and Base64 transport avoid path/race problems.
        path = output / (uuid.uuid4().hex + ".wav")
        with self._cache_lock:
            if sum(p.stat().st_size for p in output.glob("*.wav")) + len(payload) > self.MAX_CACHE_BYTES:
                raise RuntimeError("Audio retention limit reached")
            with path.open("xb") as stream:
                stream.write(payload)
        return {"type": "record", "data": {"file": "base64://" + base64.b64encode(payload).decode("ascii")}}

    async def deliver(self, reply, target, send_record, send_text, log):
        try:
            existing = self._claim(reply.reply_id, target)
        except (OSError, sqlite3.Error):
            log("[语音] delivery journal unavailable; text fallback before dispatch")
            result = await send_text(str(reply))
            return {**(result or {}), "_reply_medium": "text", "_delivery_state": delivery_state(result)}
        if existing is not None:
            return existing

        async def text_fallback(reason):
            log(f"[语音] id={reply.reply_id} text_fallback={reason}")
            self._record(reply.reply_id, "sending", "text", {})
            try:
                result = await send_text(str(reply))
            except Exception as exc:
                result = {"status": "unknown", "_delivery_state": "unknown", "msg": type(exc).__name__}
            state = delivery_state(result)
            result = {**(result or {}), "_delivery_state": state, "_reply_medium": "text"}
            self._record(reply.reply_id, state, "text", result)
            return result

        if self.pending >= self.MAX_PENDING:
            return await text_fallback("queue_full")
        self.pending += 1
        try:
            try:
                segment = await asyncio.to_thread(self.synthesize, str(reply), reply.reply_id)
            except Exception as exc:
                return await text_fallback(type(exc).__name__)
            if not self._record(reply.reply_id, "sending", "voice", {}):
                return await text_fallback("journal_unavailable")
            try:
                result = await send_record(segment)
            except Exception as exc:
                result = {"status": "unknown", "_delivery_state": "unknown", "msg": type(exc).__name__}
            state = delivery_state(result)
            if state == "failed":
                return await text_fallback("onebot_rejected")
            result = {**(result or {}), "_delivery_state": state, "_reply_medium": "voice"}
            self._record(reply.reply_id, state, "voice", result)
            log(f"[语音] id={reply.reply_id} state={state}")
            return result
        finally:
            self.pending -= 1
