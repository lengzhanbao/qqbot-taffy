"""Deterministic batch distillation and bounded background scheduling."""
import asyncio
import re
import time
from collections import defaultdict
from .knowledge import normalized, safe_content


class BackgroundLearningQueue:
    def __init__(self, handler, *, capacity=8, concurrency=1, call_timeout=1,
                 task_timeout=3, retries=1, busy=lambda: False):
        self.handler, self.busy = handler, busy
        self.queue = asyncio.Queue(maxsize=max(1, capacity))
        self.concurrency = max(1, min(concurrency, 4))
        self.call_timeout, self.task_timeout = call_timeout, task_timeout
        self.retries = max(0, min(retries, 2))
        self.tasks, self.dropped, self.failed = [], 0, 0

    def submit(self, batch):
        if self.queue.full():
            self.dropped += 1
            return False
        if not self.tasks:
            self.tasks = [asyncio.create_task(self._worker()) for _ in range(self.concurrency)]
        self.queue.put_nowait((time.monotonic() + self.task_timeout, batch))
        return True

    async def _worker(self):
        while True:
            deadline, batch = await self.queue.get()
            try:
                while self.busy() and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
                for attempt in range(self.retries + 1):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.dropped += 1
                        break
                    try:
                        await asyncio.wait_for(self.handler(batch), min(self.call_timeout, remaining))
                        break
                    except Exception:
                        if attempt == self.retries:
                            self.failed += 1
            finally:
                self.queue.task_done()

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
            self.dropped += 1


class GroupKnowledgeLearner:
    def __init__(self, store, policy, *, expected_policy_version=None):
        self.store, self.policy = store, policy
        self.expected_policy_version = expected_policy_version

    def _add(self, *args, **kwargs):
        return self.store.add_candidate(*args, **kwargs, expected_policy_version=self.expected_policy_version)

    def distill(self, messages):
        """No model calls, no auto-review, no bot text as evidence."""
        bot_ids = {(m.group_id, m.message_id) for m in messages if m.is_bot_message}
        now = self.store.clock()
        messages = [m for m in messages if not m.is_bot_message and safe_content(m.text)
                    and now - 1800 < m.timestamp <= now + 60]
        if not messages:
            return []
        gid = messages[0].group_id
        if not self.policy.allows(gid, "knowledge_learning"):
            return []
        messages = [m for m in messages if m.group_id == gid][-100:]
        refs = lambda ms: [{"kind": "human", "group_id": gid,
                           "user_id": m.user_id, "message_id": m.message_id} for m in ms[:20]]
        result = []
        # Store aggregate patterns, never whole chat excerpts.
        if len(messages) >= 4 and len({m.user_id for m in messages}) >= 2:
            average = sum(len(m.text) for m in messages) / len(messages)
            style = "群聊表达偏短句" if average <= 25 else "群聊表达偏完整句"
            result.append(self._add("style", gid, style, refs(messages), subject_ref="group_sentence_length"))
        for term, pattern in (("扣1送皮肤式接梗", r"扣\s*1.*送|1+.*大哥.*真送"),
                              ("直接调侃机器人", r"(?:你|塔菲).{0,6}(?:牛马|摸鱼|笨蛋)")):
            matched = [m for m in messages if re.search(pattern, m.text)]
            if len(matched) >= 2 and len({m.user_id for m in matched}) >= 2:
                hint = "适合简短接话，不当作真实赠送承诺" if "扣1" in term else "优先互动，不解释词义"
                result.append(self._add("meme", gid, term + "；" + hint, refs(matched)))
        repeated = defaultdict(list)
        for m in messages:
            if 2 <= len(m.text) <= 32:
                repeated[normalized(m.text)].append(m)
        for matched in sorted(repeated.values(), key=len, reverse=True)[:3]:
            if len(matched) >= 3 and len({m.user_id for m in matched}) >= 2:
                result.append(self._add("meme", gid,
                    "群内重复短句（仅表达样本，非事实）：" + matched[0].text, refs(matched)))
        # Topic preferences are group habits, never biographical facts.
        for term in ("游戏", "足球", "音乐", "编程"):
            matched = [m for m in messages if term in m.text]
            if len(matched) >= 3 and len({m.user_id for m in matched}) >= 2:
                result.append(self._add("style", gid, f"群聊话题偏好：{term}", refs(matched)))
        for msg in messages:
            consent = self.policy.consent_reference(gid, msg.user_id, "personal_preference")
            preference = re.fullmatch(r"我喜欢(.{1,40})[。！!]?", msg.text)
            if preference and consent:
                result.append(self._add(
                    "personal_preference", gid, preference.group(1), refs([msg]),
                    subject_ref=msg.user_id, consent_ref=consent, applicability="personal"))
            fact = re.fullmatch(r"事实候选[：:]([^：:]{1,30})[：:](.{1,160})", msg.text)
            if fact:
                result.append(self._add("external_fact", gid,
                    fact.group(2), refs([msg]), subject_ref=fact.group(1)))
            for marker, experience in (("太长", "回复应更简短"),
                                       ("不是问意思", "直接调侃优先互动"),
                                       ("别编", "避免无依据扩写")):
                directed = re.search(r"你|塔菲|小菲|机器人|回复|回答", msg.text) or (gid, msg.reply_to) in bot_ids
                if marker in msg.text and directed:
                    result.append(self._add("reply_experience", gid, experience, refs([msg])))
        return result


def record_group_learning(scope, user, text, reply="", *, store=None,
                          source_ref=None, supersedes_id=""):
    """Legacy call stays inert without explicit authorized candidate storage.

    Corrections retain an original assertion ID. A bot answer is never evidence.
    """
    if store is None or source_ref is None or not supersedes_id or not safe_content(text):
        return None
    if not re.search(r"不是|应该是|其实是|你错了|纠正|记错", text):
        return None
    return store.add_candidate("correction", str(scope), text, [source_ref],
                               supersedes_id=supersedes_id)
