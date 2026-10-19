"""纯文本工具：问题清洗、实体归一、回复清理。"""
import re


MAX_REPLY_LEN = 12000


def _clean_question(text):
    """去掉引用/表情等标记，并做群内常见指代归一，搜索才更准。"""
    cleaned = re.sub(r"\[[^\]]*\]", "", text)
    cleaned = re.sub(r"@\S+", "", cleaned)
    cleaned = re.sub(r"罗哥|老罗", "C罗 ", cleaned)
    cleaned = re.sub(r"(?i)c罗", "C罗", cleaned)
    cleaned = re.sub(r"图尔赫|图儿赫|图赫尔", "图赫尔", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _clean_search_query(text):
    """清洗搜索词：去掉引用原文、昵称前缀，避免把上一句回答搜进去。"""
    raw = text or ""
    if "[引用原消息]" in raw:
        raw = raw.split("[引用原消息]", 1)[0]
    cleaned = _clean_question(raw)
    reply_marker = re.search(r"（[^）]+）:\s*", cleaned)
    if reply_marker and cleaned[:reply_marker.start()].strip():
        cleaned = cleaned[:reply_marker.start()].strip()
    cleaned = re.sub(r"（[^）]+）:\s*", "", cleaned)
    cleaned = re.sub(r"^[^：:]{1,20}[:：]\s*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or _clean_question(text or "")


def _normalize_entity_spacing(text):
    """把搜索引擎在中文实体里插入的空格去掉，避免“洛克 王国”被拆碎。"""
    if not text:
        return text
    text = re.sub(r"(?<=[\u4e00-\u9fa5])[ \t]+(?=[\u4e00-\u9fa5])", "", text)
    text = re.sub(r"(?<=[\u4e00-\u9fa5])[ \t]+(?=[：:])", "", text)
    text = re.sub(r"(?<=[《【])[ \t]+(?=[\u4e00-\u9fa5])", "", text)
    text = re.sub(r"(?<=[\u4e00-\u9fa5])[ \t]+(?=[》】])", "", text)
    return text


def _entity_norm(text):
    """实体匹配用：去掉空格、书名号、冒号，避免“洛克王国：世界”对不上。"""
    return re.sub(
        r"[《》【】「」『』（）()：:，,。、;；\s]+",
        "",
        _normalize_entity_spacing(text or ""),
    ).lower()


def _clean_reply(reply):
    """清理模型返回中的 think 标签，避免推理过程发到群里。"""
    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.S)
    if "<think>" in reply:
        reply = reply[:reply.find("<think>")]
    return reply.strip()


def _repair_utf8(text):
    """修复 opencode 等接口可能返回的 UTF-8/Latin-1 双重编码乱码。"""
    if not text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except Exception:
        return text


def _sanitize_reply(reply, *, preserve_code=False):
    """只做格式清理，不替换模型已经表达清楚的句意。

    2026-10-17：是否保留代码块改为**看回复内容**，而不是看"这问题属于哪一类"。
    只要正文里真的出现了代码块，就保留其中的格式；问题分类不再参与这个判断。
    """
    if not preserve_code and "```" in str(reply or ""):
        preserve_code = True
    def clean_plain(text, *, strip=True):
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"\*([^*\n]+)\*", r"\1", text)
        text = re.sub(r"__([^_\n]+)__", r"\1", text)
        text = re.sub(r"`([^`\n]+)`", r"\1", text)
        text = text.replace("**", "").replace("*", "").replace("`", "")
        text = re.sub(r"(?m)^#{1,6}\s*", "", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"([\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F])\1+", r"\1", text)
        text = re.sub(r"🐢\s*🔥", "", text)
        text = re.sub(
            r"(?<!第)(?<![0-9一二三四五六七八九十])二次(?![\u4e00-\u9fff])",
            "二次元",
            text,
        )
        text = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text))
        # 群里不需要空行，折叠成单换行；顺带丢掉"只有表情"的孤立行。
        collapsed = re.sub(r"\n{2,}", "\n", text)
        without_emoji_lines = re.sub(
            r"(?m)^[ \t]*[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]+[ \t]*$\n?",
            "",
            collapsed,
        )
        text = without_emoji_lines if without_emoji_lines.strip() else collapsed
        return text.strip() if strip else text

    reply = str(reply or "")
    if preserve_code:
        parts = re.split(r"(```[\s\S]*?(?:```|$)|`[^`\n]+`)", reply)
        reply = "".join(
            part if part.startswith("`") else clean_plain(part, strip=False)
            for part in parts if part
        )
        return re.sub(r"\n{3,}", "\n\n", reply).strip()
    return clean_plain(reply)


def _join_reply_continuation(prefix, continuation, max_overlap=160, *,
                             preserve_whitespace=False):
    """Join one bounded model continuation without repeating its overlap."""
    prefix = str(prefix or "")
    continuation = str(continuation or "")
    if not preserve_whitespace:
        prefix = prefix.rstrip()
        continuation = continuation.lstrip()
    leading = len(continuation) - len(continuation.lstrip())
    comparable = continuation[leading:]
    limit = min(max_overlap, len(prefix), len(comparable))
    for size in range(limit, 2, -1):
        if prefix[-size:] == comparable[:size]:
            continuation = continuation[leading + size:]
            if not preserve_whitespace:
                continuation = continuation.lstrip()
            break
    return prefix + continuation


def _has_complete_reply_ending(text):
    text = str(text or "").rstrip()
    return text.endswith(("。", "！", "？", "!", "?", "…", "；", ";", "```"))


def _trim_to_last_complete_line(text):
    """截断输出保留可用部分：丢掉最后一行（通常是被切半的要点）。

    内部摘要这类调用适用；对用户可见回复不要用，宁可丢弃也不发半句。
    """
    lines = [line.rstrip() for line in str(text or "").splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) <= 1:
        return (lines[0] if lines else "").strip()
    return "\n".join(lines[:-1]).strip()


def _truncate_at_sentence(text, limit=MAX_REPLY_LEN):
    """长度截断只允许在完整句子边界处发生，避免切到词中间。"""
    text = text or ""
    limit = max(0, int(limit))
    if limit == 0:
        return ""
    if len(text) <= limit:
        return text
    head = text[:limit]
    matches = list(re.finditer(r"[。！？!?；;\n]", head))
    if matches:
        return text[:matches[-1].end()]
    return text[:limit - 1].rstrip() + "…"


def _truncate_reply(text, limit=MAX_REPLY_LEN, *, preserve_code=False):
    """Keep task-specific output bounded; close a fenced block after clipping."""
    text = str(text or "")
    limit = max(0, int(limit))
    clipped = _truncate_at_sentence(text, limit)
    if not preserve_code or clipped.count("```") % 2 == 0:
        return clipped
    closing = "\n```"
    if len(clipped) + len(closing) > limit:
        clipped = _truncate_at_sentence(text, max(0, limit - len(closing)))
    if clipped.count("```") % 2:
        clipped = clipped.rstrip() + closing
    return clipped[:limit]
