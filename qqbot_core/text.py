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


def _sanitize_reply(reply):
    """只做格式清理，不替换模型已经表达清楚的句意。"""
    reply = re.sub(r"\*\*(.+?)\*\*", r"\1", reply)
    reply = re.sub(r"\*([^*\n]+)\*", r"\1", reply)
    reply = re.sub(r"__([^_\n]+)__", r"\1", reply)
    reply = re.sub(r"`([^`\n]+)`", r"\1", reply)
    reply = reply.replace("**", "").replace("*", "").replace("`", "")
    reply = re.sub(r"(?m)^#{1,6}\s*", "", reply)
    reply = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", reply)
    reply = re.sub(r"([\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F])\1+", r"\1", reply)
    reply = re.sub(r"🐢\s*🔥", "", reply)
    reply = re.sub(
        r"(?<!第)(?<![0-9一二三四五六七八九十])二次(?![\u4e00-\u9fff])",
        "二次元",
        reply,
    )
    reply = re.sub(r"[ \t]+", " ", reply).strip()
    return re.sub(r"\n{3,}", "\n\n", reply)


def _truncate_at_sentence(text, limit=MAX_REPLY_LEN):
    """长度截断只允许在完整句子边界处发生，避免切到词中间。"""
    text = text or ""
    if len(text) <= limit:
        return text
    head = text[:limit]
    matches = list(re.finditer(r"[。！？!?；;\n]", head))
    if matches:
        return text[:matches[-1].end()]
    return text
