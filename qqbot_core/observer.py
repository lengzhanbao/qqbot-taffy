"""Bounded, authorized, memory-only OneBot conversation windows."""
from dataclasses import dataclass, asdict
from collections import OrderedDict
import json
import math
import re
import time
from .topics import GroupTopicMemory
from .group_policy import policy_locked


@dataclass(frozen=True)
class ObservedMessage:
    group_id: str
    user_id: str
    message_id: str
    timestamp: float
    text: str
    at_targets: tuple
    reply_to: str
    message_type: str
    is_bot_message: bool
    topic_id: int
    observed_at: float
    # 群昵称（只是网名，不是身份）。2026-10-18：用来区分"哪个群友是哪个"，
    # 代替之前的 群友1/群友2 代号；没有昵称时再退回代号。
    user_name: str = ""


def _clean_display_name(value, limit=24):
    """群昵称清洗：去控制字符、限长；纯数字（像 QQ 号）不用。"""
    text = "".join(ch for ch in str(value or "") if ch.isprintable() and ch not in "\r\n\t").strip()
    text = text[:limit].strip()
    if not text or text.isdigit():
        return ""
    return text


class GroupConversationObserver:
    def __init__(self, policy, *, clock=time.time, max_messages=100, ttl=1800,
                 max_groups=64, max_bytes=2 * 1024 * 1024):
        self.policy, self.clock = policy, clock
        self.max_messages, self.ttl = max_messages, ttl
        self.max_groups, self.max_bytes = max_groups, max_bytes
        self._groups = OrderedDict()
        self._versions = {}
        self._policy_versions = {}
        self.topics = GroupTopicMemory(policy, clock=clock, ttl=ttl, max_groups=max_groups)

    def _purge(self):
        now = self.clock()
        for gid in list(self._groups):
            if (not self.policy.allows(gid, "observe") or
                    self._policy_versions.get(gid) != self.policy.version(gid)):
                self._groups.pop(gid, None)
                continue
            self._groups[gid] = [m for m in self._groups[gid] if now - m.timestamp < self.ttl]
            if not self._groups[gid]:
                self._groups.pop(gid, None)
        while len(self._groups) > self.max_groups or self.byte_size > self.max_bytes:
            gid = min(self._groups, key=lambda g: self._groups[g][0].timestamp)
            self._groups[gid].pop(0)
            if not self._groups[gid]:
                self._groups.pop(gid)
        # Keep metadata bounded as well as message bodies.
        for gid in list(self._versions):
            if gid not in self._groups:
                self._versions.pop(gid, None)
                self._policy_versions.pop(gid, None)

    @property
    def byte_size(self):
        return sum(len(json.dumps(asdict(m), ensure_ascii=False).encode("utf-8"))
                   for messages in self._groups.values() for m in messages)

    @policy_locked
    def ingest(self, event, bot_id=""):
        self._purge()
        if event.get("message_type", "group") != "group":
            return None
        gid, uid = str(event.get("group_id", "")), str(event.get("user_id", ""))
        if not gid or not uid or not self.policy.allows(gid, "observe"):
            return None
        now = self.clock()
        try:
            timestamp = float(event.get("time", now))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(timestamp) or timestamp <= now - self.ttl or timestamp > now + 60:
            return None
        mid = str(event.get("message_id", ""))
        # Messages without IDs cannot be deduplicated safely.
        if not mid or any(m.message_id == mid for m in self._groups.get(gid, ())):
            return None
        text, targets, reply, types = [], [], "", []
        for segment in list(event.get("message", ()))[:100]:
            if not isinstance(segment, dict):
                continue
            kind, data = segment.get("type"), segment.get("data") or {}
            types.append(str(kind))
            if kind == "text":
                text.append(str(data.get("text", "")))
            elif kind == "at":
                targets.append(str(data.get("qq", "")))
            elif kind == "reply":
                reply = str(data.get("id", ""))
        body = "".join(text).strip()[:1200]
        # 2026-10-18：纯图片消息原先 body 为空，在短期窗口里显示成"某人:"空行。
        # 记为 [图片]，让上下文能看出"这里有人发了图"。
        if not body and "image" in types:
            body = "[图片]"
        sender = event.get("sender") or {}
        display = _clean_display_name(sender.get("card") or sender.get("nickname") or "")
        messages = self._groups.setdefault(gid, [])
        previous = messages[-1] if messages else None
        referenced = next((m for m in messages if m.message_id == reply), None)
        topic = self._versions.get(gid, 0)
        if referenced:
            topic = referenced.topic_id
        elif not previous or (timestamp >= previous.timestamp and
                (timestamp - previous.timestamp > 300 or re.search(r"换个话题|另一个问题|不说这个|换话题", body))):
            topic += 1
        elif timestamp < previous.timestamp:
            earlier = [m for m in messages if m.timestamp <= timestamp]
            topic = earlier[-1].topic_id if earlier else previous.topic_id
        msg = ObservedMessage(gid, uid, mid, timestamp, body, tuple(targets), reply,
                              ",".join(types), uid == str(bot_id), topic, now,
                              user_name=display)
        messages.append(msg)
        messages.sort(key=lambda m: (m.timestamp, m.message_id))
        self._groups[gid] = messages[-self.max_messages:]
        self._versions[gid] = max(self._versions.get(gid, 0), topic)
        self._policy_versions[gid] = self.policy.version(gid)
        self._purge()
        return msg if gid in self._groups else None

    @policy_locked
    def snapshot(self, group_id, *, topic_only=False):
        self._purge()
        messages = tuple(self._groups.get(str(group_id), ()))
        if topic_only and messages:
            messages = tuple(m for m in messages if m.topic_id == messages[-1].topic_id)
        return messages

    def version(self, group_id):
        messages = self.snapshot(group_id)
        # Changes on every new event, not only explicit topic switches.
        return (self.policy.version(group_id), messages[-1].message_id if messages else "")

    def context(self, group_id, *, reply_to=""):
        messages = self.snapshot(group_id, topic_only=not bool(reply_to))
        if reply_to:
            referenced = next((m for m in messages if m.message_id == str(reply_to)), None)
            messages = tuple(m for m in messages if referenced and m.topic_id == referenced.topic_id)
        # 原始 QQ 号不得进入模型提示词：正文前缀改用组内稳定代号（群友1、群友2…）。
        # speaker_id 仍保留真实 ID —— 它只用于内部按发言人过滤，不会被送进提示词。
        labels = {}
        rows = []
        for m in messages:
            if m.is_bot_message:
                role, speaker = "assistant", ""
            else:
                role = "user"
                speaker = labels.get(str(m.user_id))
                if speaker is None:
                    named = _clean_display_name(getattr(m, "user_name", ""))
                    speaker = labels[str(m.user_id)] = named or f"群友{len(labels) + 1}"
            rows.append({
                "role": role,
                "speaker_id": (m.at_targets[0] if m.is_bot_message and len(m.at_targets) == 1
                               and m.at_targets[0] != "all" else m.user_id),
                "content": f"{speaker}: {m.text}" if speaker else m.text,
            })
        return rows

    @policy_locked
    def groups(self):
        self._purge()
        return tuple(self._groups)
