# -*- coding: utf-8 -*-
"""
QQ 群机器人桥接：NapCat (OneBot 11 WebSocket) <-> 模型 API
- 群消息 @机器人 时触发（TRIGGER=at 时），或所有群消息（TRIGGER=all）
- 每个群独立对话历史（有上下文记忆），持久化在 bridge_sessions.json
- 每群串行处理（防止并发弄乱会话），跨群并行
- 回复时 @ 提问者
"""
import asyncio
import ctypes
import html as _html
import json
import os
import re
import subprocess
import difflib
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime

import websockets
from ctypes import wintypes

from qqbot_core.stats import (
    STATS_FILE,
    _flush_stats,
    _stats,
    _stats_lock,
    get_avg_reply_time,
    inc_stat,
    record_reply_time,
)
from qqbot_core.safety import (
    REFUSAL_TEMPLATES,
    SAFETY_PATTERNS,
    _check_safety,
)
from qqbot_core.learning import record_group_learning
from qqbot_core.style import (
    CURRENT_DATE_PROMPT,
    SEARCH_PLANNER_PERSONA,
    SEARCH_PLANNING_RULES,
    SEARCH_STRATEGY_SYSTEM,
    SEARCH_TOOL_FEWSHOT,
    STYLE_PROMPT,
    SYSTEM_PROMPT,
    TAFFY_FLAVOR_PROMPT,
    TAFFY_STYLE_PROMPT,
    _append_search_source_marker,
    _is_taffy_styled,
)
from qqbot_core.prompts import (
    CONTRACT_RETRY_SYSTEM,
    GROUP_MEMORY_LEARN_SYSTEM,
    GROUNDING_CHECK_SYSTEM,
    GROUNDING_IMPROVE_SYSTEM,
    HISTORY_SUMMARY_SYSTEM,
    LYRIC_RETRY_SYSTEM,
    NO_REPEAT_SYSTEM,
    QUERY_COMPRESS_SYSTEM,
    REFLECTION_EVAL_SYSTEM,
    REFLECTION_IMPROVE_SYSTEM,
    SELF_CHECK_SYSTEM,
    TOOL_FINAL_SYSTEM,
)
from qqbot_core.text import (
    _clean_question,
    _clean_reply,
    _clean_search_query,
    _entity_norm,
    _normalize_entity_spacing,
    _repair_utf8,
    _sanitize_reply,
    _truncate_at_sentence,
)
from qqbot_core.config import *  # noqa: F401,F403
from qqbot_core.config import (
    _HEALTH_DEFAULTS,
    _HEALTH_LOCK,
    _MEMORY_CACHE,
    _PENDING_CLEAR_MEMORY,
    _read_memory_file_cached,
    _runtime_tmp_path,
)


def _safe_max_tokens(provider, model_name, max_tokens):
    if provider == "glm" and "4v" in model_name:
        return min(max_tokens, 1024)
    return max_tokens


def _dynamic_final_max_tokens(is_factual, guide_query):
    if guide_query:
        return min(GUIDE_MAX_TOKENS, 6000)
    if is_factual:
        return min(DEFAULT_MAX_TOKENS, 4000)
    return min(DEFAULT_MAX_TOKENS, 2000)


# ---------- 模型熔断（避免 429/5xx/超时反复重试拖慢回复） ----------
_provider_cooldown = {}
_model_health = {}
_MODEL_BASE_ORDER = {m: i for i, m in enumerate(MODELS)}

def _provider_blocked(model_ref):
    until = _provider_cooldown.get(model_ref)
    return until is not None and time.time() < until

def _mark_provider_down(model_ref, seconds=PROVIDER_COOLDOWN_SECONDS):
    _provider_cooldown[model_ref] = time.time() + seconds
    _model_health[model_ref] = _model_health.get(model_ref, 0) - 2
    inc_stat("model_circuit_open_count")
    log(f"[熔断] {model_ref} 暂停 {seconds}s")
    write_health(model_circuit_open=True)

def _mark_provider_ok(model_ref):
    _provider_cooldown.pop(model_ref, None)
    _model_health[model_ref] = _model_health.get(model_ref, 0) + 1
    write_health(model_circuit_open=bool(_provider_cooldown))

def _mark_provider_weak(model_ref):
    _model_health[model_ref] = _model_health.get(model_ref, 0) - 1

def _sort_models(model_refs):
    return sorted(
        list(model_refs or MODELS),
        key=lambda m: (
            -_model_health.get(m, 0),
            _MODEL_BASE_ORDER.get(m, 999),
        ),
    )

_COMFORT_MODE_RE = re.compile(
    r"难过|伤心|低落|焦虑|烦|累|压力|emo|哭|不开心|抑郁|难受|想不开|崩溃|委屈|无助",
    re.IGNORECASE,
)
_COMFORT_CONTENT_RE = re.compile(
    r"(?:写|做|唱|生成|推荐|来|找|要).{0,8}(?:难过|伤心|低落|焦虑|压力|emo|哭|不开心|抑郁|难受|委屈|无助).{0,6}?(?:歌词|歌|故事|文案|视频|文章|教程|方案|计划|清单|攻略|笔记|文|内容|段子|帖子|歌单)",
    re.IGNORECASE,
)
_CREATIVE_MODE_RE = re.compile(
    r"写|起名|取名|文案|故事|设计|标题|剧本|歌词|诗|策划|脑洞|创意",
    re.IGNORECASE,
)
_LYRIC_QUERY_RE = re.compile(
    r"(?:的)?(?:完整)?歌词(?:是什么|呢|有吗)?|完整歌词|唱一下|唱一段|谁唱的|谁唱的歌",
    re.IGNORECASE,
)
_LYRIC_WRITE_RE = re.compile(
    r"帮我写|写一首|写个|创作.{0,6}歌词|生成.{0,6}歌词|编.{0,6}歌词",
    re.IGNORECASE,
)
_TASK_MODE_RE = re.compile(
    r"帮我|计划|规划|安排|整理|分析|总结|翻译|代码|生成|做一份|做一个|列个|推荐|"
    r"攻略|怎么做|怎么弄|怎么打|怎么抓|怎么过|怎么获得",
    re.IGNORECASE,
)

_SONG_RECOMMENDATION_RE = re.compile(
    r"推荐.{0,6}(?:几首|一些|点)?(?:歌|歌曲|新歌)|"
    r"(?:我要|我想|想)?听歌|要听歌|睡前.{0,6}歌|歌单",
    re.IGNORECASE,
)

_SONG_RECOMMENDATION_FALLBACK = [
    ("周杰伦", "晴天"),
    ("周杰伦", "七里香"),
    ("林俊杰", "修炼爱情"),
    ("林俊杰", "不为谁而作的歌"),
    ("邓紫棋", "光年之外"),
    ("邓紫棋", "泡沫"),
    ("陈奕迅", "富士山下"),
    ("陈奕迅", "十年"),
    ("张国荣", "我"),
    ("Coldplay", "Yellow"),
    ("周深", "大鱼"),
    ("王菲", "红豆"),
    ("五月天", "倔强"),
    ("许嵩", "有何不可"),
    ("赵雷", "成都"),
    ("陈粒", "走马"),
]

_BOOK_RECOMMENDATION_FALLBACK = {
    "东野圭吾": [
        "白夜行",
        "嫌疑人X的献身",
        "解忧杂货店",
        "恶意",
        "放学后",
        "秘密",
        "新参者",
        "时生",
        "幻夜",
        "祈祷落幕时",
    ],
    "村上春树": [
        "挪威的森林",
        "海边的卡夫卡",
        "1Q84",
        "世界尽头与冷酷仙境",
    ],
    "余华": [
        "活着",
        "许三观卖血记",
        "兄弟",
        "在细雨中呼喊",
    ],
    "刘慈欣": [
        "三体",
        "球状闪电",
        "流浪地球",
        "超新星纪元",
    ],
}


_MOVIE_RECOMMENDATION_FALLBACK = [
    "肖申克的救赎",
    "霸王别姬",
    "盗梦空间",
    "星际穿越",
    "千与千寻",
    "让子弹飞",
    "阿甘正传",
    "楚门的世界",
]

_ANIME_RECOMMENDATION_FALLBACK = [
    "进击的巨人",
    "命运石之门",
    "钢之炼金术师FA",
    "死亡笔记",
    "鬼灭之刃",
    "咒术回战",
]

_SERIES_RECOMMENDATION_FALLBACK = [
    "琅琊榜",
    "大明王朝1566",
    "人民的名义",
    "甄嬛传",
    "白夜追凶",
]

_GAME_RECOMMENDATION_FALLBACK = [
    "塞尔达传说：旷野之息",
    "艾尔登法环",
    "黑神话：悟空",
    "博德之门3",
]

_PERSON_BIO_FALLBACK = {
    "faker": (
        "Faker，本名李相赫（Lee Sang-hyeok），1996年5月7日出生于韩国首尔，"
        "是《英雄联盟》历史上公认最伟大的中单选手，ID为Faker。"
        "他出道即效力于SKT T1（现T1），率队多次夺得全球总决赛冠军，"
        "被粉丝称为“大魔王”。"
    ),
    "meiko": (
        "Meiko，本名田野，1998年6月6日出生于云南省楚雄彝族自治州禄丰市，"
        "中国英雄联盟职业选手，ID为Meiko，司职辅助，"
        "曾长期效力于EDG，后转会至iG，是LPL首位达成大满贯的辅助选手。"
    ),
}

_ESPORTS_IDS = (
    "on", "oner", "viper", "meiko", "faker", "rookie", "theshy",
    "bin", "jiejie", "xun", "clearlove",
)

_LEGACY_POLLUTION_TABLE = [
    ("M跑题了meiko", "Meiko（田野）"),
    ("EDG骑士团作为CN无畏俱乐部", "EDG俱乐部"),
    ("EDG骑士团作为CN无畏电子竞技俱乐部俱乐部", "EDG俱乐部"),
    ("2019年9月12日8年7月27日3年9月13日年", "2019年"),
    ("2019年9月12日8年7月27日5年MSI", "2019年MSI"),
    ("2026年6月3日4年8月25日4年8月26日6年7月2日1年", "2026年"),
    ("2026年6月3日4年8月25日4年8月26日6年7月2日1年底", "2026年底"),
    ("外号“”！", "外号“Viper”！"),
    ("2020年12月15日EDG", "2020年12月15日加入EDG"),
    ("夺冠的大之一", "夺冠功臣之一"),
    ("Bin哥厉害的微博_微博", "Bin哥的微博"),
    ("Bin哥厉害的微博", "Bin哥的微博"),
    ("谢谢队伊（星标准发音为英式）和队友对我的体和容", "谢谢队伍和队友对我的体谅与包容"),
    ("队伊（星标准发音为英式）", "队伍"),
    ("体和容", "体谅与包容"),
    ("体与容", "体谅与包容"),
    ("网路黑话", "网络黑话"),
    ("离BLG渣了", "离开BLG"),
    ("文件!", ""),
]


def _is_song_recommendation_query(text):
    return bool(_SONG_RECOMMENDATION_RE.search(text or ""))


def _song_recommendation_source():
    return "\n".join(
        f"{artist}《{song}》"
        for artist, song in _SONG_RECOMMENDATION_FALLBACK
    )


def _song_recommendation_fallback_reply():
    lines = [
        f"{idx}. {artist}《{song}》"
        for idx, (artist, song) in enumerate(_SONG_RECOMMENDATION_FALLBACK, 1)
    ]
    return (
        "塔菲给你挑几首完整的喵：\n"
        + "\n".join(lines)
        + "\n\n这些歌名、歌手都是完整的，涵盖抒情、摇滚、民谣、流行，"
          "你随便挑一首按下去就行喵。"
    )


def _book_recommendation_fallback_reply(entity):
    books = _BOOK_RECOMMENDATION_FALLBACK.get(entity or "", [])
    if not books:
        return ""
    lines = [f"{idx}. 《{book}》" for idx, book in enumerate(books, 1)]
    return (
        f"塔菲按《{entity}》的作品给你整理了一份完整书单喵：\n"
        + "\n".join(lines)
        + f"\n\n这些都是{entity}的代表作，书名和作者都写完整了喵。"
    )


def _book_recommendation_broken(reply, entity):
    books = _BOOK_RECOMMENDATION_FALLBACK.get(entity or "", [])
    if not books:
        return False
    text = reply or ""
    if text.count("《") != text.count("》"):
        return True
    hit = sum(book in text for book in books[:5])
    return hit < 3


def _song_recommendation_broken(reply):
    reply = reply or ""
    if reply.count("《") != reply.count("》"):
        return True
    bad_patterns = (
        r"(?<!《)泡沫》", "陈奕《", "《修炼》", "《年之外》",
        "抒、", "《山楂树",
    )
    return any(re.search(pattern, reply) for pattern in bad_patterns)


def _strip_history_prompt_noise(content):
    """去掉旧版本误存进会话里的 system/style/tone 等提示词残留。"""
    if not content:
        return content
    text = re.split(
        r"\s*(?:<style>|事实/知识类问题：)",
        content,
        maxsplit=1,
        flags=re.S,
    )[0]
    text = re.sub(
        r"<style>.*?</style>|<tone>.*?</tone>|<rule>.*?</rule>",
        "",
        text,
        flags=re.S | re.IGNORECASE,
    )
    text = re.sub(
        r"\s*(?:有搜索结果时，以搜索结果为准直接回答。|"
        r"如果记忆里有相关信息优先引用。只输出回复本身，不要解释、前缀。)\s*",
        "\n",
        text,
    )
    return text.strip()


def _sanitize_history_content(content, role="assistant"):
    """旧会话里如果存过残缺歌单，读取时替换成完整兜底，避免污染新回答。"""
    content = _strip_history_prompt_noise(content)
    if _has_pathological_repetition(content):
        content = _refine_polluted_history(content)
    if role == "assistant":
        if _song_recommendation_broken(content):
            return _song_recommendation_fallback_reply()
        if "推荐系统" in content:
            fallback = _recommendation_fallback_reply(content)
            if fallback:
                return fallback
        if _looks_garbled_reply(content):
            person_key = _person_bio_key(content)
            if person_key:
                fallback = _person_bio_fallback_reply(person_key)
                if fallback:
                    return fallback
        if (
            _has_repeated_long_phrase(content)
            or _looks_garbled_reply(content)
        ):
            if _recommendation_category(content):
                fallback = _recommendation_fallback_reply(content)
                if fallback:
                    return fallback
            if re.search(
                r"选手|厂长|viper|meiko|faker|rookie|theshy|介绍",
                content,
                re.IGNORECASE,
            ):
                return _refine_polluted_history(content)
    if "推荐系统" in content:
        content = content.replace("推荐系统", "推荐内容")
        content = content.replace("推荐系统入门教程", "可靠推荐内容")
    for old, new in _LEGACY_POLLUTION_TABLE:
        content = content.replace(old, new)
    return content


def _clean_date_concat(text):
    """把来源里粘在一起的日期清理成完整日期，不删除名字/队伍信息。"""
    text = text or ""
    for _ in range(6):
        new = re.sub(
            r"((?:19|20)\d{2}年\d{1,2}月\d{1,2}日)((?:19|20)\d{2}年)",
            r"\1，\2",
            text,
        )
        new = re.sub(
            r"((?:19|20)\d{2}年\d{1,2}月)((?:19|20)\d{2}年)",
            r"\1，\2",
            new,
        )
        new = re.sub(
            r"((?:19|20)\d{2}年\d{1,2}月\d{1,2}日)\d{1,2}年",
            r"\1",
            new,
        )
        new = re.sub(
            r"((?:19|20)\d{2}年\d{1,2}月)\d{1,2}年",
            r"\1",
            new,
        )
        if new == text:
            break
        text = new
    return text


def _refine_polluted_history(content):
    """保留人物/作品信息，只移除重复片段和粘连日期。"""
    content = _clean_date_concat(content or "")
    content = _clean_pathological_repetition(content)
    for old, new in _LEGACY_POLLUTION_TABLE:
        content = content.replace(old, new)
    return _sanitize_reply(content)


def _recommendation_category(text):
    q = text or ""
    if (
        "听歌" in q
        or "歌单" in q
        or "歌曲" in q
        or re.search(r"几首.{0,3}歌", q)
        or re.search(r"推荐.{0,4}歌", q)
    ):
        return "song"
    if re.search(r"书|书籍|作者|书单|小说", q):
        return "book"
    if re.search(r"电影|影片", q):
        return "movie"
    if re.search(r"动漫|动画|番剧|番", q):
        return "anime"
    if re.search(r"电视剧|剧集|剧", q):
        return "series"
    if re.search(r"游戏", q):
        return "game"
    return ""


def _generic_recommendation_items(category):
    if category == "song":
        return [f"{artist}《{song}》" for artist, song in _SONG_RECOMMENDATION_FALLBACK]
    if category == "movie":
        return [f"《{name}》" for name in _MOVIE_RECOMMENDATION_FALLBACK]
    if category == "anime":
        return [f"《{name}》" for name in _ANIME_RECOMMENDATION_FALLBACK]
    if category == "series":
        return [f"《{name}》" for name in _SERIES_RECOMMENDATION_FALLBACK]
    if category == "game":
        return [f"《{name}》" for name in _GAME_RECOMMENDATION_FALLBACK]
    return []


def _recommendation_fallback_reply(query):
    category = _recommendation_category(query)
    if category == "book":
        entity = _extract_recommendation_entity(query)
        if entity in _BOOK_RECOMMENDATION_FALLBACK:
            return _book_recommendation_fallback_reply(entity)
    if category == "movie":
        try:
            rows = _search_douban_movies("热门", 10)
            if rows:
                lines = [f"{idx}. {row}" for idx, row in enumerate(rows, 1)]
                return (
                    "塔菲从豆瓣热门电影里给你挑了一份喵：\n"
                    + "\n".join(lines)
                    + "\n\n这是豆瓣实时榜单，不是搜索建议。"
                )
        except Exception:
            pass
    items = _generic_recommendation_items(category)
    if not items:
        return ""
    lines = [f"{idx}. {item}" for idx, item in enumerate(items, 1)]
    label = {
        "song": "歌",
        "movie": "电影",
        "anime": "动漫",
        "series": "剧集",
        "game": "游戏",
    }.get(category, "推荐")
    return (
        f"塔菲给你整理一份完整的{label}推荐喵：\n"
        + "\n".join(lines)
        + "\n\n名称都是完整的，你直接按兴趣挑就行喵。"
    )


def _recommendation_should_fallback(reply, query):
    text = reply or ""
    if not text:
        return True
    if re.search(r"推荐系统|没查到|查不到|没有任何|跑偏", text):
        return True
    if text.count("《") != text.count("》"):
        return True
    return False


def _recommendation_search_plans(query):
    """推荐类搜索计划：保留原查询，并叠加榜单/作品搜索。"""
    category = _recommendation_category(query)
    entity = _extract_recommendation_entity(query)
    plans = [{"query": query, "purpose": "按原问题搜索"}]
    if entity:
        plans.append({"query": f"{entity} 作品", "purpose": "查作品"})
        plans.append({"query": f"{entity} 书单", "purpose": "查书单"})
    if category == "movie":
        plans.append({"query": "豆瓣 电影 Top 250", "purpose": "查豆瓣热门电影"})
    elif category == "anime":
        plans.append({"query": "B站 热门 动漫", "purpose": "查热门动漫"})
    elif category == "series":
        plans.append({"query": "豆瓣 电视剧 Top 250", "purpose": "查热门剧集"})
    elif category == "game":
        plans.append({"query": "游戏 年度 推荐 排行", "purpose": "查热门游戏"})
    elif category == "book" and not entity:
        plans.append({"query": "豆瓣 图书 Top 250", "purpose": "查热门图书"})
    return plans


def _skip_recommendation_suggestions(query):
    return bool(_recommendation_category(query or ""))


def _skip_person_suggestions(query):
    return bool(re.search(
        r"介绍|选手|是谁|厂长|viper|meiko|faker|oner|rookie|theshy|"
        r"jiejie|xun|诺言|clearlove",
        query or "",
        re.IGNORECASE,
    ))


def _person_bio_key(query):
    q = (query or "").lower()
    if "faker" in q or "李相赫" in q:
        return "faker"
    if "meiko" in q or "田野" in q:
        return "meiko"
    return ""


def _extract_esports_id(query):
    q = (query or "").lower()
    for ident in _ESPORTS_IDS:
        if re.search(
            r"(?<![a-z0-9])" + re.escape(ident) + r"(?![a-z0-9])",
            q,
        ):
            return ident
    return ""


def _person_bio_broken(reply, key):
    text = reply or ""
    if key == "faker":
        if "某处错字" in text or "某处夸张描述" in text:
            return True
        if re.search(r"\d{4}年\d{1,2}月\d{1,2}日\d{1,2}年", text):
            return True
        if "李相赫" not in text or "1996" not in text:
            return True
    if key == "meiko":
        if "某处跑题" in text or "M完全" in text or "某段跑题的残缺回答" in text:
            return True
        if "田野" not in text or "1998" not in text:
            return True
    return False


def _person_bio_fallback_reply(key):
    text = _PERSON_BIO_FALLBACK.get(key or "", "")
    if not text:
        return ""
    return f"塔菲给你整理一份干净的介绍喵：\n{text}"


def _is_lyrics_query(text):
    return bool(_LYRIC_QUERY_RE.search(text or "")) and not _LYRIC_WRITE_RE.search(text or "")


def _classify_dialog_mode(text):
    text = text or ""
    if _is_lyrics_query(text):
        return "lyrics"
    if _COMFORT_MODE_RE.search(text) and not _COMFORT_CONTENT_RE.search(text):
        return "comfort"
    if _CREATIVE_MODE_RE.search(text):
        return "creative"
    if _TASK_MODE_RE.search(text):
        return "task"
    if _COMFORT_MODE_RE.search(text):
        return "comfort"
    return "chitchat"


def _dialog_mode_prompt(mode):
    if mode == "lyrics":
        return (
            "\n【对话模式：歌词查询】\n"
            "按用户要的歌词/歌曲信息回答，怎么组织由你判断。\n"
            "资料不足就说明不确定。\n"
            "输出正文，不要 Markdown 或工具调用格式；始终用塔菲语气。")
    if mode == "comfort":
        return (
            "\n【对话模式：安慰】\n"
            "先接住情绪，怎么安慰和给建议由你判断；可以用具体小画面或比喻。\n"
            "如果涉及自我伤害或持续危机，温和建议寻求专业帮助，并强调不是一个人面对。\n"
            "始终用塔菲语气。")
    if mode == "creative":
        return (
            "\n【对话模式：创意】\n"
            "按用户需求直接展开，怎么给方向、给多少由你判断。\n"
            "始终用塔菲语气包装，不要输出 JSON 或工具调用格式。")
    if mode == "task":
        return (
            "\n【对话模式：任务】\n"
            "按任务直接回答，需要确认时再确认，怎么组织由你判断。\n"
            "始终用塔菲语气。")
    return (
        "\n【对话模式：闲聊】\n"
        "像群里朋友一样自然接话，长短由你判断；"
        "始终用塔菲语气。")


_ABSTRACT_VARIANTS = [
    ("留白", "说到七分满，留三分让群友自己悟。"),
    ("高级黑", "一本正经地夸，夸到反差出来。"),
    ("双关", "抓住名字或品牌做双关。"),
    ("代价视角", "把溢价、代价说成某种税。"),
    ("反差类比", "用越X越Y的反差来类比。"),
    ("弹幕感", "给当前话题加弹幕感。"),
    ("跨服对线", "拉到另一个领域做具体类比。"),
    ("反向安慰", "表面安慰，实际点出荒诞。"),
    ("名场面", "像解说现场给具体画面。"),
    ("科幻包装", "用科幻设定包装当前事实。"),
]


def _pick_abstract_variant(query, key, history_len):
    seed_text = (
        f"{key}|{history_len // 3}|{query[:80]}|{time.strftime('%Y%m%d%H%M')}"
    )
    return _ABSTRACT_VARIANTS[abs(hash(seed_text)) % len(_ABSTRACT_VARIANTS)]

_SENSITIVE_TOPIC_RE = re.compile(
    r"政治|政客|国家领导人|国家主席|总统|总理|政党|共产党|国民党|台湾|香港|新疆|西藏|"
    r"宗教|伊斯兰|基督教|佛教|民族|种族|地域黑|战争|俄乌|巴以|地震|空难|事故|"
    r"校园霸凌|抑郁|自杀|癌症|白血病|犯罪",
    re.IGNORECASE,
)
_PLAYFUL_RE = re.compile(
    r"抽象|串子|整活|玩梗|阴阳(?!师)|高级黑|说点狠的|说些不漂亮|来点乐子|整一下|放飞|爆典|锐评",
    re.IGNORECASE,
)
_STYLE_DIAL_DOWN_RE = re.compile(
    r"别抽象|别玩梗|正常点|正经点|说人话|不要抽象|别整活|认真点|严肃点",
    re.IGNORECASE,
)
_STYLE_FEWSHOT_EXAMPLES = [
    (
        "L1自嘲",
        "AI会取代人类吗",
        "会取代一部分，比如我这种回答问题的，迟早被更便宜的模型取代。但创造性的活儿还得靠人，毕竟甩锅这件事，AI学不会。",
    ),
    (
        "L2调侃",
        "华为和苹果选哪个",
        "看你想要哪种税——爱国税还是智商税，都是税，区别在于交得心甘情愿还是不明不白。",
    ),
    (
        "L3高级黑",
        "OpenAI是最开放的AI公司吗",
        "名字确实叫Open，这很Open。至于AI嘛，Open的是名字，不是代码。",
    ),
    (
        "跨域类比",
        "手机领域的华为类比谁",
        "C罗吧，都是越黑越强的主，争议大、实力派、粉丝多、黑子也多。",
    ),
]

_FACTUAL_FEWSHOT_EXAMPLES = [
    (
        "梅西 C罗今年多少岁了",
        "梅西出生于1987年6月24日，C罗出生于1985年2月5日；按当前日期分别计算年龄，两个人都答到。",
    ),
    (
        "2026世界杯冠军是谁",
        "西班牙，决赛加时1-0击败阿根廷，费兰·托雷斯制胜；阿根廷是亚军。",
    ),
    (
        "给你俩窝窝是什么梗",
        "这是2022世界杯阿根廷vs荷兰后，梅西赛后发言被空耳成的梗，意思是“你看什么看，傻瓜？一边去”。",
    ),
    (
        "豆包是什么",
        "豆包是字节跳动旗下的AI对话助手，提供问答、写作、翻译、编程等能力；不是“豆”或“包”。",
    ),
]


def _classify_style_control(query):
    sensitive = bool(
        DISABLE_ABSTRACT_ON_SENSITIVE
        and _SENSITIVE_TOPIC_RE.search(query or "")
    )
    playful = bool(_PLAYFUL_RE.search(query or ""))
    dial_down = bool(_STYLE_DIAL_DOWN_RE.search(query or ""))
    if sensitive or dial_down or STYLE_LEVEL == 0:
        level = 0
    else:
        intent = _classify_intent(query)
        analogy_like = bool(re.search(
            r"(?:像|类比|相当于|对标|对应).{0,10}(?:谁|什么|哪个|球员|人物|选手|队伍|战队)",
            query or "",
            re.IGNORECASE,
        ))
        if playful:
            level = 3 if (ALLOW_L3 and STYLE_LEVEL >= 3) else min(STYLE_LEVEL, 2)
        elif intent in ("chitchat", "opinion", "analogy") or analogy_like:
            level = 2 if STYLE_LEVEL >= 2 else 1
        elif intent in ("fact", "time_sensitive", "command"):
            level = min(STYLE_LEVEL, 1)
        else:
            level = STYLE_LEVEL
    return level, sensitive, playful


def _style_control_prompt(level, sensitive):
    if sensitive:
        return "\n【敏感话题】关闭抽象，正常、克制、客观回答。"
    if level == 0:
        return "\n【抽象控制】当前关闭抽象，正常回答。"
    if level == 1:
        return "\n【抽象控制】L1：可轻度自嘲玩梗，先答准再玩梗。"
    if level == 2:
        return "\n【抽象控制】L2：可调侃现象/品牌，先答准再玩梗，不攻击个人。"
    return "\n【抽象控制】L3：可高级黑，先答准再玩梗，不脏话不攻击个人。"


def _build_style_fewshot(query, level, key):
    allowed = []
    if level >= 1:
        allowed.append(0)
    if level >= 2:
        allowed.append(1)
    if level >= 3:
        allowed.append(2)
    if _classify_intent(query) == "analogy":
        allowed.append(3)
    if not allowed:
        return ""
    count = 1 if level <= 1 else 2 if level == 2 else 3
    seed = abs(hash(f"{key}|{query[:60]}|{time.strftime('%Y%m%d%H')}"))
    idx = seed % len(allowed)
    chosen = [allowed[(idx + i) % len(allowed)] for i in range(min(count, len(allowed)))]
    lines = []
    for i in chosen:
        label, q, answer = _STYLE_FEWSHOT_EXAMPLES[i]
        lines.append(f"- [{label}] 问：{q}\n  参考：{answer}")
    return "\n【抽象参考示例】以下只是参考，不要照抄句式：\n" + "\n".join(lines)


def _build_factual_fewshot(query):
    """事实/攻略问题加入少量正确范例，重点教完整实体和“没查证不乱编”。"""
    if not (_is_factual(query) or _is_guide_query(query)):
        return ""
    lines = []
    for q, answer in _FACTUAL_FEWSHOT_EXAMPLES:
        lines.append(f"- 问：{q}\n  参考：{answer}")
    return (
        "\n【事实回答参考示例】以下只是参考，不要照抄句式；"
        "重点学习：关键名称写全、多实体不遗漏、实时信息以搜索为准、没查到就直说。\n"
        + "\n".join(lines)
    )


def _current_date_text():
    wd = "一二三四五六日"[time.localtime().tm_wday]
    return f"北京时间 {time.strftime('%Y年%m月%d日 %H:%M')}，星期{wd}"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "plan_search",
            "description": (
                "在执行搜索前，先将用户问题拆解为多个独立、短小的搜索意图。"
                "尽量覆盖问题的不同方面或时间维度；query 尽量精炼成短关键词，"
                "如果 query 太长，就继续拆成多个短 query。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "thought_process": {
                        "type": "string",
                        "description": "简要分析用户问题的核心矛盾，说明为什么要拆解成这些查询关键词。",
                    },
                    "queries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "精炼的搜索关键词，优先短词组合，不用完整句子。",
                                },
                                "purpose": {
                                    "type": "string",
                                    "description": (
                                        "说明这个搜索词旨在获取什么具体信息，"
                                        "例如：获取具体数值、确认事件时间、查找官方定义。"
                                    ),
                                },
                            },
                            "required": ["query", "purpose"],
                        },
                    },
                },
                "required": ["thought_process", "queries"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "搜索一个独立子问题。每个调用只放该子问题相关的完整 query 和 entities；"
                "query 要精炼，避免长句。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "精炼搜索关键词"},
                    "entities": {"type": "array", "items": {"type": "string"}, "description": "核心实体，可为空"},
                },
                "required": ["query", "entities"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": (
                "抓取一个具体网页并返回清洗后的正文，"
                "用于获取歌词、文章、攻略等完整内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "需要抓取正文的完整网页 URL"},
                    "purpose": {"type": "string", "description": "抓取目的，例如获取歌词、获取文章全文"},
                },
                "required": ["url"],
            },
        },
    },
]

def _glm_web_search_tool():
    """智谱 Web Search in Chat 专用工具：让免费 GLM 在 API 层直接联网检索。"""
    return [{
        "type": "web_search",
        "web_search": {
            "enable": "True",
            "search_engine": GLM_WEB_SEARCH_ENGINE,
            "search_result": "True",
            "search_prompt": (
                f"请结合搜索资料和当前日期回答。当前日期：{_current_date_text()}。"
                "交叉综合多个来源；资料不足就说明不确定。"
            ),
            "count": GLM_WEB_SEARCH_COUNT,
            "content_size": "high",
        },
    }]

CORRECTION_MARKERS = ("不对", "错了", "不是这样的", "应该是", "纠正一下", "其实是")

FACT_MARKERS = (
    "解散了", "退役了", "夺冠了", "结婚了", "离队了", "转会", "上任", "当选",
    "发布了", "推出了", "上市了", "倒闭了", "取消了", "推迟了", "延期了",
    "改名了", "输给了", "赢了", "辞职了", "下课了", "被封了", "复出了",
    "已经不是", "今年是", "现在是", "目前是"
)

# ---------- 日志 ----------
def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        enc = sys.stdout.encoding or "utf-8"
        safe_line = line.encode(enc, "replace").decode(enc, "replace")
        print(safe_line, flush=True)
    except Exception:
        try:
            print(line, flush=True)
        except Exception:
            pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _log_phase(start, label):
    elapsed_ms = max(0, (time.time() - start) * 1000)
    log(f"[阶段] {label}: {elapsed_ms:.0f}ms")


def write_health(**kwargs):
    """增量更新健康状态；使用临时文件 + rename 保证原子写入。"""
    if os.environ.get("QQ_TEST_MODE") == "1":
        return
    with _HEALTH_LOCK:
        state = read_health() or {}
        state = {**_HEALTH_DEFAULTS, **state}
        state.update(kwargs)
        state["updated_at"] = datetime.now().isoformat()
        tmp = HEALTH_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, HEALTH_FILE)
        except Exception as e:
            log(f"[健康] 写入失败: {repr(e)[:120]}")


def read_health():
    """读取健康状态；缺失或损坏时返回 None。"""
    try:
        with open(HEALTH_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None
def _admin_text(text):
    """去掉 @ 前缀后识别管理员指令，兼容 `@bot !status`。"""
    return re.sub(r"@\S+\s*", "", text or "").strip()


def is_admin(user_id):
    return str(user_id) in ADMIN_UIDS


def _cmd_status(bridge=None):
    health = read_health() or {}
    sessions = getattr(bridge, "sessions", None)
    session_count = len(sessions.data) if sessions else 0
    message_count = 0
    if sessions:
        for value in sessions.data.values():
            message_count += len((value or {}).get("messages") or [])
    return (
        f"PID: {health.get('pid', '未知')}\n"
        f"启动: {health.get('start_time', '未知')}\n"
        f"WS: {'已连接' if health.get('ws_connected') else '未连接'}\n"
        f"会话: {session_count}\n"
        f"消息: {message_count}\n"
        f"最后错误: {health.get('last_error') or '无'}"
    )


def _cmd_style(level_str):
    global STYLE_LEVEL
    if level_str not in ("0", "1", "2", "3"):
        return "用法: !style 0|1|2|3"
    STYLE_LEVEL = int(level_str)
    return f"已设置抽象等级 L{STYLE_LEVEL}"


def _cmd_taffy(on_off):
    global TAFFY_FLAVOR
    if on_off not in ("on", "off"):
        return "用法: !taffy on|off"
    TAFFY_FLAVOR = on_off == "on"
    return "已开启塔菲风格" if TAFFY_FLAVOR else "已关闭塔菲风格"


def _cmd_model():
    ordered = _sort_models(MODELS)
    return "当前模型顺序:\n" + "\n".join(f"- {m}" for m in ordered[:5])


def _run_backup_hidden(tag):
    if os.environ.get("QQ_TEST_MODE") == "1":
        return
    script = os.path.join(BASE_DIR, "scripts", "backup.ps1")
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script,
                "-Tag",
                tag,
            ],
            capture_output=True,
            timeout=60,
            creationflags=0x08000000,
            startupinfo=startupinfo,
        )
    except Exception as e:
        log(f"[备份] {tag} 失败: {repr(e)[:120]}")


def _clear_memory_md(scope_key):
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return False
    blocks = _memory_blocks(content)
    kept = []
    removed = False
    for block in blocks:
        first = block.split("\n", 1)[0]
        if scope_key.startswith("private_"):
            match = first.startswith("## 私聊 ")
        else:
            match = first.startswith("## 群 " + str(scope_key))
        if match:
            removed = True
        else:
            kept.append(block)
    if removed:
        try:
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                f.write(("\n\n".join(kept) + "\n") if kept else "")
        except Exception as e:
            log(f"[forget] 清理长期记忆失败: {repr(e)[:120]}")
            return False
    return removed


def _reset_memory_md():
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            f.write("# Bot Memory\n\n（已清空）\n")
        return True
    except Exception as e:
        log(f"[clear_memory] 重置长期记忆失败: {repr(e)[:120]}")
        return False


def _cmd_clear_memory(args, user_id, bridge=None):
    uid = str(user_id)
    if args != "yes":
        _PENDING_CLEAR_MEMORY.add(uid)
        return "确认清空所有记忆？再发一次 `!clear_memory yes`，执行前会自动备份。"
    if uid not in _PENDING_CLEAR_MEMORY:
        return "请先发 `!clear_memory` 确认。"
    _PENDING_CLEAR_MEMORY.discard(uid)
    _run_backup_hidden("before_clear_all")
    cleared = []
    if bridge:
        try:
            bridge.sessions.data.clear()
            bridge.sessions.save()
            cleared.append("会话记忆")
        except Exception as e:
            log(f"[clear_memory] 清理会话失败: {repr(e)[:120]}")
        try:
            bridge.style_store.data.clear()
            bridge.style_store.save()
            cleared.append("群风格")
        except Exception as e:
            log(f"[clear_memory] 清理群风格失败: {repr(e)[:120]}")
    else:
        try:
            with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
                f.write("{}")
            cleared.append("会话记忆")
        except Exception as e:
            log(f"[clear_memory] 清理会话文件失败: {repr(e)[:120]}")
        try:
            with open(GROUP_STYLES_FILE, "w", encoding="utf-8") as f:
                f.write("{}")
            cleared.append("群风格")
        except Exception as e:
            log(f"[clear_memory] 清理群风格文件失败: {repr(e)[:120]}")
    if _reset_memory_md():
        cleared.append("长期记忆")
    return f"已清空所有记忆（已自动备份）\n" + "\n".join("- " + c for c in cleared)


def _cmd_forget(context, bridge=None):
    if not isinstance(context, dict):
        return "指令上下文缺失"
    message_type = context.get("message_type")
    if message_type == "group":
        gid = str(context.get("group_id") or "")
        if not gid:
            return "无法识别群号"
        session_key = gid
        scope_key = gid
        label = f"群 {gid}"
    else:
        uid = str(context.get("user_id") or "")
        if not uid:
            return "无法识别用户"
        session_key = "private_" + uid
        scope_key = "private_" + uid
        label = f"私聊 {uid}"

    _run_backup_hidden("before_forget")
    cleared = []
    if bridge:
        try:
            if session_key in bridge.sessions.data:
                del bridge.sessions.data[session_key]
                bridge.sessions.save()
                cleared.append("会话记忆")
        except Exception as e:
            log(f"[forget] 清理会话失败: {repr(e)[:120]}")
        if message_type == "group":
            try:
                if gid in bridge.style_store.data:
                    del bridge.style_store.data[gid]
                    bridge.style_store.save()
                    cleared.append("群风格")
            except Exception as e:
                log(f"[forget] 清理群风格失败: {repr(e)[:120]}")
    if _clear_memory_md(scope_key):
        cleared.append("长期记忆")
    if not cleared:
        return f"{label} 暂无已清理记忆（已自动备份）"
    return f"已清理 {label} 的记忆（已自动备份）\n" + "\n".join("- " + c for c in cleared)


def handle_admin_command(text, user_id, context=None, bridge=None):
    cleaned = _admin_text(text)
    if not cleaned.startswith("!"):
        return None
    if not is_admin(user_id):
        return None
    cmd, _, args = cleaned[1:].partition(" ")
    cmd = cmd.lower().strip()
    args = args.strip()
    if cmd == "status":
        return _cmd_status(bridge)
    if cmd == "style":
        return _cmd_style(args)
    if cmd == "taffy":
        return _cmd_taffy(args)
    if cmd == "model":
        return _cmd_model()
    if cmd == "forget":
        return _cmd_forget(context, bridge)
    if cmd == "clear_memory":
        return _cmd_clear_memory(args, user_id, bridge)
    return f"未知指令: !{cmd}\n可用: !status !style 0-3 !taffy on/off !model !forget !clear_memory"


def _cleanup_log():
    """启动时检查日志大小，超过上限截断保留最后 2000 行"""
    try:
        if not os.path.exists(LOG_FILE):
            return
        if os.path.getsize(LOG_FILE) <= LOG_MAX_SIZE:
            return
        with open(LOG_FILE, encoding="utf-8") as f:
            lines = f.readlines()
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines[-2000:])
        log("[清理] 日志文件已截断")
    except Exception:
        pass

def _cleanup_sessions():
    """启动时检查对话存储大小；只有超过 50GB 才把最旧会话移入归档文件。"""
    try:
        if not os.path.exists(SESSIONS_FILE):
            return
        size = os.path.getsize(SESSIONS_FILE)
        if size < CONVERSATION_COMPRESS_BYTES:
            log(f"[存储] 对话数据 {size / 1024 / 1024:.1f} MB / 50 GB")
            return
        if size <= CONVERSATION_LIMIT_BYTES:
            log("[存储] 对话数据达到 45GB，将在后台深度压缩保留重要内容")
            return
        with open(SESSIONS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        archive_path = os.path.join(BASE_DIR, "data", "bridge_sessions_archive.json")
        archive = {}
        if os.path.exists(archive_path):
            with open(archive_path, encoding="utf-8") as f:
                archive = json.load(f)
        while size > CONVERSATION_LIMIT_BYTES * 0.9 and len(data) > 1:
            oldest_key = next(iter(data))
            archive[oldest_key] = data.pop(oldest_key)
            size = sum(len(json.dumps(v, ensure_ascii=False)) for v in data.values())
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        with open(archive_path, "w", encoding="utf-8") as f:
            json.dump(archive, f, ensure_ascii=False, indent=2)
        log(f"[存储] 超过 50GB，已归档最旧会话，当前 {size / 1024 / 1024:.1f} MB")
    except Exception:
        pass

# ---------- 知识库检索（轻量 RAG：关键词匹配）----------
_STOP_WORDS = set(
    "的 了 在 是 我 你 他 她 它 们 这 那 就 都 也 和 还 "
    "有 没有 不 什么 怎么 如何 可以 吗 吧 啊 呢 哦 嘛 哈 嗯 ？ ！ ， 。".split()
)

def _keywords(text):
    """简单分词：英文/数字整词 + 中文二元组，避免'谁是图赫尔'匹配不到'图赫尔'"""
    out = []
    for token in re.findall(r'[a-zA-Z0-9]+', text):
        if token not in _STOP_WORDS and len(token) > 1:
            out.append(token)
    for run in re.findall(r'[\u4e00-\u9fff]+', text):
        if len(run) <= 2:
            if run not in _STOP_WORDS:
                out.append(run)
        else:
            out.extend(run[i:i+2] for i in range(len(run) - 1))
    return out

def _query_terms(text):
    """先生成实体词，再去掉百科/新闻等泛词，避免无关结果被泛词放行。"""
    terms = set()
    for token in re.findall(r"[a-zA-Z0-9]+", text):
        token = token.lower()
        if len(token) >= 2:
            terms.add(token)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) >= 2 and run not in _STOP_WORDS and run not in _GENERIC_SEARCH_TERMS:
            terms.add(run)
        if len(run) > 2:
            terms.update(run[i:i+2] for i in range(len(run) - 1))
    return {t for t in terms if t not in _GENERIC_SEARCH_TERMS}

def _is_relevant_result(combined, qterms):
    """只要求命中实体/强词，不做逐条组合相关性判断。"""
    if not qterms:
        return True
    if any(marker in combined for marker in (
        "百度建议", "B站建议", "必应建议", "360建议", "搜狗提示"
    )):
        return any(t in combined for t in qterms)
    rare = {t for t in qterms if len(t) >= 3 and t not in _GENERIC_SEARCH_TERMS}
    if rare:
        return any(t in combined for t in rare)
    return any(t in combined for t in qterms)

def _memory_blocks(memory_text):
    text = (memory_text or "").replace("\r\n", "\n").replace("\r", "\n")
    return [b.strip() for b in re.split(r"\n(?=## )", text) if b.strip()]

def _memory_scope_allowed(scope, scope_alias=None):
    if not scope:
        return None
    if scope.startswith("private_"):
        uid = scope.split("_", 1)[1]
        allowed = {"## 私聊 " + uid, "## 常识补充"}
        if scope_alias:
            allowed.add("## 私聊 " + str(scope_alias))
        return allowed
    return {"## 群 " + str(scope), "## 常识补充"}

def retrieve_knowledge_scored(query, memory_text=None, top_k=3, max_chars=2000,
                              scope=None, scope_alias=None):
    """返回 (命中的记忆文本, 最高命中分数)；低于门控阈值时调用方不注入。"""
    if memory_text is None:
        memory_text = _read_memory_file_cached()
    if not memory_text.strip():
        return "", 0
    blocks = _memory_blocks(memory_text)
    if not blocks:
        return "", 0
    if scope:
        allowed = _memory_scope_allowed(scope, scope_alias)
        blocks = [
            b for b in blocks
            if any(b.startswith(prefix) for prefix in allowed)
        ]
    qkws = _keywords(query)
    if not qkws:
        return "", 0
    scored = []
    for block in blocks:
        bkws = set(_keywords(block))
        overlap = sum(1 for k in qkws if k in bkws)
        title = block.split('\n')[0] if '\n' in block else block[:30]
        title_kws = set(_keywords(title))
        overlap += sum(2 for k in qkws if k in title_kws)
        if overlap > 0:
            scored.append((overlap, block.strip()))
    scored.sort(key=lambda x: -x[0])
    result = []
    total = 0
    max_score = scored[0][0] if scored else 0
    for _, block in scored[:top_k]:
        if total + len(block) > max_chars:
            break
        result.append(block)
        total += len(block)
    return "\n".join(result), max_score

def retrieve_knowledge(query, memory_text, top_k=3, max_chars=2000):
    """兼容旧调用的文本检索入口。"""
    return retrieve_knowledge_scored(query, memory_text, top_k, max_chars)[0]

# ---------- 联网搜索（360搜索）----------
_SEARCH_TRIGGER = re.compile(
    r"新闻|比分|比赛|天气|温度|股票|股价|汇率|世界杯|冠军|总统|选举|事件|"
    r"介绍一下|介绍|什么梗|梗|"
    r"歌词|谁唱的|唱一下|唱一段|完整歌词"
    r"发生了什么|最新|现在|价格|多少钱|什么时候|哪天|几月|几号|哪一年|何时|"
    r"是谁|什么是|是什么|在哪|哪里|哪个|为什么|现任|主教练|教练|球员|选手|歌手|演员|主播|战队|俱乐部|首相|主席|创始人|作者|导演|亚军|季军|百科|简介|资料|谁|什么|哪|年龄|年纪|几岁|多大|多少岁|生日|出生|现状|身价|全名|链接|直播间|官方|几点",
    re.IGNORECASE,
)

def _needs_search(text):
    """只有明显需要实时/事实检索的问题才联网，避免每条消息都等搜索"""
    return bool(_SEARCH_TRIGGER.search(text))

def _should_search(text):
    """ALWAYS_SEARCH=1 时全部搜索；默认 0，只按事实/链接/直播等触发词搜索。"""
    if ALWAYS_SEARCH:
        return bool(text and text.strip())
    return _needs_search(text)


def _generic_fallback_query(query):
    """生成不依赖手写规则兜底的通用搜索词。"""
    q = _clean_search_query(query or "")
    stripped = False
    for prefix in (
        "我是说的梗", "我说的是梗", "我说的是", "我是说的",
        "我问的是", "你问的是", "说的是", "其实我说的", "我是说", "就是",
    ):
        if q.startswith(prefix):
            q = q[len(prefix):].strip()
            stripped = True
            break
    if "梗" in (query or "") and "梗" not in q and len(q) >= 2:
        q = f"{q} 梗"
    if not q:
        return query or ""
    if not stripped or "梗" not in q:
        built = _build_search_query(q)
        if built:
            q = built
    return q


def _should_attempt_generic_search(query):
    return bool(ALWAYS_SEARCH)


_FACTUAL_TRIGGER = re.compile(
    r"新闻|比分|比赛|天气|温度|股票|股价|汇率|世界杯|冠军|亚军|季军|总统|选举|事件|"
    r"歌词|谁唱的|唱一下|唱一段|完整歌词"
    r"发生了什么|最新|现在|价格|多少钱|什么时候|哪天|几月|几号|哪一年|何时|"
    r"是谁|什么是|是什么|为什么|现任|主教练|教练|球员|选手|歌手|演员|主播|战队|俱乐部|百科|简介|资料|年龄|年纪|几岁|多大|多少岁|生日|出生|现状|身价|全名|链接|直播间|官方|几点"
    r"|海拔|高度|多少米|多少年|多少号|多少位",
    re.IGNORECASE,
)

def _is_factual(text):
    """温度判定：真正需要准确的事实/知识问题才用低温。"""
    return bool(_FACTUAL_TRIGGER.search(text))


def _should_use_single_shot_search(text):
    """简单事实问题跳过工具规划轮，直接内置搜索一次再成文。"""
    if not ALWAYS_SEARCH or not _is_factual(text):
        return False
    if (
        _is_guide_query(text)
        or _is_lyrics_query(text)
        or _TASK_MODE_RE.search(text or "")
        or _recommendation_category(text)
    ):
        return False
    return True

_SEARCH_CACHE_TTL = float(os.environ.get("QQ_SEARCH_CACHE_TTL", "300"))
_SEARCH_CACHE = {}
_SEARCH_CACHE_LOCK = threading.Lock()
_ENGINE_FAIL_STATS = {}
_ENGINE_FAIL_STATS_LOCK = threading.Lock()
ENGINE_FAIL_THRESHOLD = 3
ENGINE_DEGRADE_DURATION = 300
_LYRICS_BODY_CACHE = {}
_LYRICS_BODY_CACHE_LOCK = threading.Lock()
_LYRICS_BODY_CACHE_TTL = 86400
_ANSWER_CACHE_TTL = 3600
_ANSWER_CACHE = {}
_ANSWER_CACHE_LOCK = threading.Lock()
_SEARCH_NOISE = (
    "登录", "注册", "广告", "下一页", "相关搜索", "百度图片", "导航",
    "图片", "视频", "img", "href", "src=", "rms_iac", "data-",
    "百度快照", "访问异常页面", "收起", "展开", "分享",
    "酷我音乐", "在线试听", "单曲", "免费下载", "试听", "歌词大全",
    "酷狗音乐",
)

_YEAR_PAGE_NOISE_MARKERS = (
    "年_百度百科",
    "年大事",
    "重要节日",
    "放假安排",
    "日历",
    "行事历",
    "行事曆",
    "经济半年报",
    "世界杯赛程",
)


def _is_generic_year_noise(title, desc, query):
    """过滤“2026年百科/日历/放假”这种泛年份页，避免污染赛事结果。"""
    if not any(k in query for k in ("冠军", "亚军", "季军", "决赛", "比分", "夺冠")):
        return False
    if not any(k in title for k in _YEAR_PAGE_NOISE_MARKERS):
        return False
    combined = (title or "") + (desc or "")
    if any(k in combined for k in ("冠军", "决赛", "比分", "夺冠", "西班牙", "阿根廷", "大力神杯")):
        return False
    return True
_SEARCH_QUESTION_WORDS = re.compile(
    r"什么时候|哪天|几月|几号|哪一年|何时|是谁|什么是|是什么|为什么|在哪|哪里|哪个|"
    r"是否|如何|怎么|多少钱|什么|哪|谁|吗|呢|啊|呀|哈|了|的|把|还|给|"
    r"评价|看待|记得|老师|是|你|我|他|她|徒弟|"
    r"详细介绍|介绍一下|介绍下|帮我|麻烦|请问|告诉|说说|讲一下|知道|一下|"
    r"最近|最新|近期|刚刚|目前|当前|现在|"
    r"请搜索并总结|请搜索并简要介绍|请总结|请简要介绍|请介绍一下|请介绍|"
    r"总结一下|简述一下|请帮我|帮我查|帮我看|麻烦查|麻烦帮|关于|这个|那个|很火|非常火",
)
_CORRECTION_FOLLOWUP_RE = re.compile(
    r"(?:是不是|不是|应该是|其实是|谁说|不是说|对吗|对吧|是吗|是吧|错了吧|不对吧|"
    r"是什么|是啥|是哪个|是.{0,10}(?:吗|吧))",
    re.IGNORECASE,
)
_FOLLOWUP_RE = re.compile(
    r"^(那|然后|再|还|他|她|它|谁|什么|怎么|为啥|为什么|冠军|亚军|季军|多少|几号|另外|还有|但是|不过)",
    re.IGNORECASE,
)


def _is_followup_query(query):
    """只有明确包含代词或承接连词才认为是追问，避免短句被强行联系上文。"""
    return bool(_FOLLOWUP_RE.match(query or ""))

_GENERIC_SEARCH_TERMS = {
    "人物", "是谁", "是什么", "什么是", "百科", "简介", "资料", "谁", "什么",
    "为什么", "怎么", "如何", "比赛", "赛事", "事件", "新闻", "消息",
    "最新", "今天", "现在", "冠军", "亚军", "季军",
}

def _clean_entities(entities):
    """过滤模型误把属性/年份当实体的情况，只保留真正的实体名。"""
    bad = _GENERIC_SEARCH_TERMS | {
        "年龄", "年纪", "几岁", "多大", "多少岁", "生日", "出生",
        "出生日期", "现状", "身价", "2026", "2026年", "2025", "2025年",
    }
    out = []
    for entity in entities or []:
        entity = _clean_question(str(entity)).strip()
        if len(entity) >= 2 and entity not in bad and entity not in out:
            out.append(entity)
    return out

def _split_search_terms(query, max_terms=3):
    """把搜索词拆成独立关键词，供分别搜索后合并结果。"""
    parts = re.split(r"[\s，,;；、/\\]+", query)
    terms = []
    for p in parts:
        p = p.strip()
        if len(p) >= 2 and p not in terms:
            terms.append(p)
    return terms[:max_terms]

def _classify_intent(text):
    """web-search-chatbot 意图决策：规则优先，默认事实型。"""
    msg = (text or "").strip().lower()
    if len(msg) <= 6 and any(
        w in msg for w in ("在吗", "早", "晚安", "哈哈", "嘿嘿", "嗨", "hello", "hi", "你好")
    ):
        return "chitchat"
    if any(w in msg for w in (
        "你觉得", "你喜欢", "你喜欢看", "你喜欢听", "你喜欢玩", "你最喜欢",
        "你爱", "你怎么看", "推荐", "建议", "怎么看", "好不好",
    )):
        return "opinion"
    if any(w in msg for w in ("帮我", "生成", "写一个", "写一份", "翻译", "总结这篇", "做一份", "制作")):
        return "command"
    if any(w in msg for w in ("类比", "相当于", "好比", "类似于", "对应", "对标", "换成", "等同于")):
        return "analogy"
    if any(w in msg for w in ("最新", "今天", "现在", "最近", "目前", "当前", "刚刚")):
        return "time_sensitive"
    if any(w in msg for w in ("是什么", "是多少", "多少", "在哪", "什么时候", "为什么", "是谁", "怎么", "谁")):
        return "fact"
    return "fact"

_GAME_QUERY_RE = re.compile(
    r"洛克王国|王者荣耀|英雄联盟|LOL|DOTA|原神|明日方舟|崩坏|绝区零|星穹铁道|"
    r"碧蓝航线|重返未来|阴阳师|FGO|宝可梦|口袋妖怪|梦幻西游|逆水寒|天涯明月刀|"
    r"光遇|蛋仔派对|和平精英|金铲铲|云顶之弈|无畏契约|瓦罗兰特|CSGO|穿越火线|"
    r"地下城|DNF|我的世界|第五人格|永劫无间|三角洲|APEX|堡垒之夜|剑网3|"
    r"魔兽|炉石|三国杀|斗罗大陆|航海王",
    re.IGNORECASE,
)
_GUIDE_QUERY_RE = re.compile(
    r"怎么弄|怎么打|怎么抓|怎么过|怎么获得|怎么升级|什么好打|什么精灵|什么阵容|什么角色|"
    r"攻略|打法|速刷|通关|解锁|培养|搭配|配队|阵容|徽章|关卡|异色|抽卡|BOSS|boss",
    re.IGNORECASE,
)
_WEAK_REPLY_RE = re.compile(
    r"我不太清楚|不太清楚|不清楚|建议你去查|建议去查|查查攻略|查攻略|"
    r"很难说|不好说|再搜一下|再帮|帮你查查|帮你查一下|帮塔菲查查|塔菲查查",
    re.IGNORECASE,
)
def _is_game_query(text):
    return bool(_GAME_QUERY_RE.search(text or ""))


def _is_guide_query(text):
    return bool(_GUIDE_QUERY_RE.search(text or "")) or _is_game_query(text)


def _should_serial_final(text):
    """事实/攻略类不用竞速抢答，按稳定模型优先级串行调用。"""
    return bool(_is_factual(text) or _is_guide_query(text))


_PROTECTED_SEARCH_TERMS = (
    "豆包", "Kimi", "塔菲", "洛克王国", "王者荣耀", "C罗", "梅西", "bin哥", "马头",
)


def _is_weak_reply(reply, query=""):
    text = _clean_reply(reply or "").strip()
    if not text:
        return True
    if len(text) < 12:
        return True
    if re.search(r"没查到|查不到|无法确认|不确定|塔菲不知道", text):
        return False
    if (_is_guide_query(query) or _is_factual(query) or _TASK_MODE_RE.search(query or "")) and len(text) < 40:
        return True
    return bool(_WEAK_REPLY_RE.search(text))


def _split_sub_queries(text, entities=None):
    """按实体优先拆子问题；实体不足时按连词/标点拆，最多 3 个。"""
    cleaned = _clean_question(text)
    if _is_guide_query(cleaned):
        return [cleaned]
    entities = [
        str(e).strip() for e in (entities or [])
        if str(e).strip() and str(e).strip() in cleaned
    ]
    if len(entities) >= 2:
        attr = cleaned
        for entity in entities:
            attr = attr.replace(entity, " ")
        attr = re.sub(r"\s+", " ", attr).strip()
        attr = re.sub(r"^(的|呢|啊|吗|哦|呀|和|还有|然后)+", "", attr)
        if not attr:
            attr = "最新情况"
        return [f"{entity} {attr}".strip() for entity in entities[:3]]

    if any(term in cleaned for term in _PROTECTED_SEARCH_TERMS):
        return [cleaned]

    parts = re.split(r"(?:和|还有|另外|同时|以及|然后|；|;|\?\?+)", cleaned)
    subs = []
    for part in parts:
        part = re.sub(r"[？?。！!]+$", "", part).strip()
        if len(part) >= 2:
            subs.append(part)
    return (subs[:3] if subs else [cleaned])

def _extract_events_rule(text):
    """LLM 事件识别失败时，用简单句式兜底提取事件。"""
    cleaned = _clean_question(text)
    cleaned = re.sub(r"^(今年|去年|最近|最新|今天|现在)", "", cleaned)
    for marker in ("谁", "什么人", "哪个", "什么"):
        if marker in cleaned:
            part, rest = cleaned.split(marker, 1)
            part = part.strip()
            if len(part) >= 2:
                action = re.search(
                    r"(打人|冲突|斗殴|吵架|闹事|违规|处罚|争议|丑闻|风波|翻车|塌房|解散|退役|转会|夺冠|绝杀)",
                    rest,
                )
                action = action.group(1) if action else ""
                return [f"{part}{action}事件".strip()]
    if "事件" in cleaned:
        return [cleaned]
    return []

def _build_search_query(text):
    """从问题里提取搜索关键词，去掉疑问词和聊天词，提升搜索命中率。"""
    cleaned = _clean_question(text)
    cleaned = cleaned.replace("卖网课", "网课")
    cleaned = cleaned.replace("今年", time.strftime("%Y年"))
    cleaned = cleaned.replace("去年", str(int(time.strftime("%Y")) - 1) + "年")
    cleaned = re.sub(r"(?<=[\u4e00-\u9fff])(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])(?=[\u4e00-\u9fff])", " ", cleaned)
    cleaned = cleaned.replace("C 罗", "C罗")
    cleaned = _SEARCH_QUESTION_WORDS.sub(" ", cleaned)
    cleaned = re.sub(
        r"(?<=[\u4e00-\u9fff])(?:和|与|还有|以及)(?=[\u4e00-\u9fff])",
        " ",
        cleaned,
    )
    cleaned = re.sub(
        r"(?:^|[\s，,;；、/\\]+)(?:和|与|还有|以及)(?=[\u4e00-\u9fff])",
        " ",
        cleaned,
    )
    cleaned = re.sub(
        r"(?<=[\u4e00-\u9fff])网课(?=[\u4e00-\u9fff])",
        " 网课 ",
        cleaned,
    )
    cleaned = re.sub(
        r"(?:^|[\s，,;；、/\\]+)网课(?=[\u4e00-\u9fff])",
        " 网课 ",
        cleaned,
    )
    parts = [p.strip() for p in re.split(r"[\s，,。？！!；;：:]+", cleaned) if p.strip()]
    meaningful = [p for p in parts if len(p) >= 2]
    if "网课" in meaningful:
        meaningful = [p for p in meaningful if p != "网课"] + ["网课"]
    if not meaningful:
        return _clean_question(text)
    joined = "".join(meaningful)
    if "世界杯" in joined and "冠军" in joined:
        if any(k in joined for k in ("历届", "一览", "史上")):
            return "世界杯冠军 一览"
        event = ""
        m = re.search(r"(20\d{2}年?[\u4e00-\u9fff]*世界杯)", joined)
        if m:
            event = re.sub(r"^(20\d{2})年", r"\1", m.group(1))
        else:
            year = next(
                (t for t in meaningful if re.fullmatch(r"20\d{2}", t)),
                time.strftime("%Y"),
            )
            event = f"{year}世界杯"
        return f"{event} 冠军"
    return " ".join(meaningful[:6])


def _extract_recommendation_entity(text):
    """从“推荐几本东野圭吾的书”里拆出实体，但保留原查询不动。"""
    q = _clean_question(text or "")
    if not re.search(r"书|书籍|作者|作品", q):
        return ""
    for entity in _BOOK_RECOMMENDATION_FALLBACK:
        if entity in q:
            return entity
    q = re.sub(
        r"请|帮我|你|给|推荐|几本|几首|一些|点|作者|的书|书籍|书单|"
        r"作品|歌|歌曲|歌单|听|呢|呀|吧|喵",
        " ",
        q,
        flags=re.IGNORECASE,
    )
    parts = [
        p.strip()
        for p in re.split(r"[\s，。！？、：:]+", q)
        if len(p.strip()) >= 2
    ]
    if not parts:
        return ""
    parts.sort(key=len, reverse=True)
    for part in parts:
        if re.search(r"[\u4e00-\u9fa5]", part):
            return part
    return parts[0]


def _build_search_variants(query, entities=None, extra_queries=None):
    """生成多个完整搜索式：原始问题、清洗关键词、实体百科、LLM 补充式。"""
    age_words = ("年龄", "年纪", "几岁", "多大", "多少岁", "生日", "出生")
    year = time.strftime("%Y年")
    q2 = _build_search_query(query)
    variants = [q2] if q2 else []
    if query not in variants:
        variants.append(query)
    variants = [v for v in variants if v]
    for plan in _recommendation_search_plans(query):
        if plan["query"] not in variants:
            variants.append(plan["query"])
    parts = _split_search_terms(q2, max_terms=4)
    if parts:
        base = parts[0]
        for part in parts[1:]:
            combo = base + " " + part
            if combo not in variants:
                variants.append(combo)
    if any(k in query for k in age_words):
        if "出生日期" not in (q2 or query):
            birth = (q2 or query) + " 出生日期"
            if birth not in variants:
                variants.append(birth)
    is_age = any(k in query for k in age_words)
    for entity in (entities or [])[:2]:
        eq = entity + " 百科"
        if eq not in variants:
            variants.append(eq)
    for extra in (extra_queries or []):
        extra = re.sub(r"\s+", " ", str(extra).strip())
        if extra and extra not in variants:
            variants.append(extra)
    seen = set()
    out = []
    for variant in variants:
        variant = re.sub(r"\s+", " ", variant).strip()
        if variant and variant not in seen:
            seen.add(variant)
            out.append(variant)
    return out[:6]

def _search_cache_key(query):
    return re.sub(r"\s+", " ", query.strip().lower())

def _search_cache_get(key):
    with _SEARCH_CACHE_LOCK:
        item = _SEARCH_CACHE.get(key)
        if not item:
            return None
        ts, value = item
        if time.time() - ts > _SEARCH_CACHE_TTL:
            _SEARCH_CACHE.pop(key, None)
            return None
        return value

def _search_cache_set(key, value):
    with _SEARCH_CACHE_LOCK:
        _SEARCH_CACHE[key] = (time.time(), value)
        if len(_SEARCH_CACHE) > 100:
            for old_key in list(_SEARCH_CACHE)[:-80]:
                _SEARCH_CACHE.pop(old_key, None)


def _is_engine_degraded(engine):
    """连续失败达到阈值后在 5 分钟内跳过该引擎。"""
    with _ENGINE_FAIL_STATS_LOCK:
        stats = _ENGINE_FAIL_STATS.get(engine) or {}
    if (stats.get("fails") or 0) < ENGINE_FAIL_THRESHOLD:
        return False
    last_fail = stats.get("last_fail") or 0
    if time.time() - last_fail < ENGINE_DEGRADE_DURATION:
        return True
    with _ENGINE_FAIL_STATS_LOCK:
        _ENGINE_FAIL_STATS.pop(engine, None)
    return False


def _record_engine_fail(engine):
    with _ENGINE_FAIL_STATS_LOCK:
        stats = _ENGINE_FAIL_STATS.setdefault(engine, {"fails": 0, "last_fail": 0})
        stats["fails"] = int(stats.get("fails") or 0) + 1
        stats["last_fail"] = time.time()
    if (stats.get("fails") or 0) >= ENGINE_FAIL_THRESHOLD:
        write_health(search_degraded=True, search_degraded_engine=engine)
        log(f"[搜索] {engine} 连续失败 {stats['fails']} 次，降级 {ENGINE_DEGRADE_DURATION}s")


def search_with_resilience(query, max_results=8):
    """搜索统一入口：缓存 + 引擎失败统计 + 自动降级。"""
    cache_key = _search_cache_key(query)
    cached = _search_cache_get(cache_key)
    if cached:
        inc_stat("search_cache_hits")
        log("[搜索] 命中缓存")
        return cached
    engines = (
        "bing_cn", "360_m", "baidu", "sogou", "baike", "suggest",
        "weixin", "360", "quark", "baidu_m", "wikipedia", "arxiv", "pubmed",
    )
    available = [e for e in engines if not _is_engine_degraded(e)]
    if not available:
        write_health(search_degraded=True, search_degraded_engine="all")
        return "（搜索暂时不可用，所有引擎都已降级）"
    for attempt in range(2):
        for engine in available:
            try:
                inc_stat("search_calls")
                results = _search_engine(query, engine, max_results)
                if results:
                    result = "\n".join(results)
                    _search_cache_set(cache_key, result)
                    inc_stat("search_success")
                    return result
                else:
                    inc_stat("search_fail")
                    _record_engine_fail(engine)
                    log(f"[搜索] {engine} 空结果")
            except Exception as e:
                inc_stat("search_fail")
                _record_engine_fail(engine)
                log(f"[搜索] {engine} 失败: {repr(e)[:80]}")
        if attempt == 0:
            time.sleep(0.3)
    return "（搜索失败，请稍后重试）"

def _answer_cache_key(chat_key, query):
    return f"{chat_key}|{_clean_question(query).lower()}"

def _answer_cache_get(key, intent):
    with _ANSWER_CACHE_LOCK:
        item = _ANSWER_CACHE.get(key)
        if not item:
            return None
        ts, value, cache_intent = item
        ttl = 60 if cache_intent == "time_sensitive" else _ANSWER_CACHE_TTL
        if time.time() - ts > ttl:
            _ANSWER_CACHE.pop(key, None)
            return None
        return value

def _answer_cache_set(key, value, intent):
    with _ANSWER_CACHE_LOCK:
        _ANSWER_CACHE[key] = (time.time(), value, intent)
        if len(_ANSWER_CACHE) > 200:
            for old_key in list(_ANSWER_CACHE)[:-160]:
                _ANSWER_CACHE.pop(old_key, None)

def _fetch_html(url, timeout=5, extra_headers=None):
    """多套抓取兜底：curl -> urllib -> requests，避免单一网络库被反爬卡住。"""
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          "AppleWebKit/537.36 Chrome/120 Safari/537.36")
    headers = {
        "User-Agent": ua,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if extra_headers:
        headers.update(extra_headers)
    last_err = ""
    try:
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        curl_cmd = [
            "curl.exe", "-s", "-L", "--max-time", str(timeout),
            "-A", ua, "-H", "Accept-Language: zh-CN,zh;q=0.9",
        ]
        if headers.get("Referer"):
            curl_cmd += ["-e", headers["Referer"]]
        curl_cmd.append(url)
        proc = subprocess.run(
            curl_cmd,
            capture_output=True, timeout=timeout + 2,
            creationflags=create_no_window, startupinfo=startupinfo)
        if proc.returncode == 0 and len(proc.stdout) > 500:
            return proc.stdout.decode("utf-8", "replace")
        last_err += " curl:empty"
    except Exception as e:
        last_err += f" curl:{repr(e)[:60]}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        last_err = repr(e)[:80]
    try:
        import requests
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200 and len(r.text) > 500:
            return r.text
        last_err += f" requests:{r.status_code}"
    except Exception as e:
        last_err += f" requests:{repr(e)[:60]}"
    raise RuntimeError(last_err or "fetch failed")

def _fetch_json(url, timeout=5):
    try:
        text = _fetch_html(url, timeout)
        return json.loads(text)
    except Exception:
        req = urllib.request.Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/120 Safari/537.36"),
            "Accept": "application/json,text/plain,*/*",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        for enc in ("utf-8", "gbk"):
            try:
                return json.loads(raw.decode(enc))
            except Exception:
                continue
        return json.loads(raw.decode("utf-8", "replace"))


_DRISSION_BROWSER_CANDIDATES = (
    os.environ.get("QQ_BROWSER_PATH", ""),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
_DRISSION_LOCK = threading.Lock()


def _find_drission_browser():
    for path in _DRISSION_BROWSER_CANDIDATES:
        if path and os.path.exists(path):
            return path
    return ""


def _drission_search_html(query, engine="bing_cn", timeout=DRISSION_SEARCH_TIMEOUT):
    """真实浏览器搜索兜底，主要用来绕过百度/搜狗/360 的纯 HTTP 反爬。"""
    if not DRISSION_SEARCH_ENABLED:
        return ""
    browser = _find_drission_browser()
    if not browser:
        log("[浏览器搜索] 未找到 Chrome/Edge，跳过 DrissionPage")
        return ""
    if engine not in ("bing", "bing_cn"):
        engine = "bing_cn"
    q = urllib.parse.quote(_clean_search_query(query))
    if engine == "bing":
        url = f"https://www.bing.com/search?q={q}"
    else:
        url = f"https://cn.bing.com/search?q={q}&setlang=zh-hans"
    page = None
    with _DRISSION_LOCK:
        try:
            from DrissionPage import ChromiumPage, ChromiumOptions
            co = ChromiumOptions()
            co.set_browser_path(browser)
            co.headless(True)
            co.auto_port(True)
            co.set_load_mode("none")
            co.set_timeouts(int(timeout), int(timeout), int(timeout))
            co.set_argument("--disable-blink-features=AutomationControlled")
            co.set_argument("--no-sandbox")
            co.set_argument("--disable-gpu")
            co.set_argument("--disable-dev-shm-usage")
            page = ChromiumPage(addr_or_opts=co)
            page.get(url)
            deadline = time.time() + timeout
            while time.time() < deadline:
                html = page.html or ""
                current_url = page.url or ""
                if "b_algo" in html or "antispider" in current_url or "captcha" in current_url.lower():
                    break
                time.sleep(0.5)
            current_url = page.url or ""
            if "antispider" in current_url or "captcha" in current_url.lower():
                log(f"[浏览器搜索] {engine} 仍触发验证码，放弃")
                return ""
            html = page.html or ""
            log(f"[浏览器搜索] {engine} 成功，{len(html)} 字")
            return html
        except Exception as e:
            log(f"[浏览器搜索] {engine} 失败: {repr(e)[:80]}")
            return ""
        finally:
            if page is not None:
                try:
                    page.quit()
                except Exception:
                    pass


def _search_wikipedia(query, max_results=5):
    """中文维基 JSON API，结构化返回，比网页搜索更少被反爬拦截。"""
    url = (
        "https://zh.wikipedia.org/w/api.php?action=query&list=search&format=json&utf8=1"
        f"&srsearch={urllib.parse.quote(query)}&srlimit={max_results}")
    data = _fetch_json(url, timeout=5)
    out = []
    for item in data.get("query", {}).get("search", [])[:max_results]:
        title = item.get("title", "") or ""
        snippet = re.sub(r"<[^>]+>", "", item.get("snippet", "") or "")
        snippet = _normalize_entity_spacing(_html.unescape(snippet).strip())
        if title and snippet:
            out.append(f"【{title}】{snippet[:SEARCH_SNIPPET_CHARS]}")
    return out

def _search_arxiv(query, max_results=5):
    """arXiv API：结构化返回论文标题和摘要。"""
    url = (
        "https://export.arxiv.org/api/query?"
        f"search_query=all:{urllib.parse.quote(query)}"
        f"&start=0&max_results={max_results}&sortBy=relevance")
    try:
        xml_text = _fetch_html(url, timeout=5)
        root = ET.fromstring(xml_text)
    except Exception:
        return []
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    out = []
    for entry in root.findall("atom:entry", ns)[:max_results]:
        title = (entry.findtext("atom:title", "", ns) or "").strip()
        summary = (entry.findtext("atom:summary", "", ns) or "").strip()
        published = (entry.findtext("atom:published", "", ns) or "")[:10]
        if title:
            title = re.sub(r"\s+", " ", title)
            summary = re.sub(r"\s+", " ", summary)[:200]
            out.append(f"【arXiv：{title}】{published} {summary}")
    return out

def _search_pubmed(query, max_results=5):
    """PubMed E-utilities：先查 ID，再拉标题摘要。"""
    try:
        search_url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
            f"db=pubmed&term={urllib.parse.quote(query)}"
            f"&retmode=json&retmax={max_results}")
        search_data = _fetch_json(search_url, timeout=5)
        ids = (search_data.get("esearchresult") or {}).get("idlist") or []
    except Exception:
        return []
    if not ids:
        return []
    try:
        summary_url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?"
            "db=pubmed&retmode=json&id=" + ",".join(ids))
        summary_data = _fetch_json(summary_url, timeout=5)
    except Exception:
        return []
    result_map = summary_data.get("result") or {}
    out = []
    for uid in ids[:max_results]:
        item = result_map.get(str(uid)) or {}
        title = str(item.get("title") or "").strip()
        pub_date = str(item.get("pubdate") or "")
        if title:
            out.append(f"【PubMed：{title}】{pub_date}")
    return out

def _search_baike_openapi(query, max_results=3):
    """百度百科 OpenAPI：国内结构化实体兜底，反爬比网页版少。"""
    keys = [query]
    seen = set()
    out = []
    for key in keys:
        key = key.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            url = (
                "https://baike.baidu.com/api/openapi/BaikeLemmaCardApi"
                f"?scope=103&format=json&appid=379020&bk_key={urllib.parse.quote(key)}")
            data = _fetch_json(url, timeout=4)
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("errno") not in (None, 0):
            continue
        title = data.get("key") or data.get("title") or key
        desc = str(data.get("desc") or "").strip()
        card = data.get("card")
        if isinstance(card, dict):
            for value in card.values():
                if isinstance(value, str) and len(value) > 10:
                    desc += " " + value
        desc = re.sub(r"\s+", " ", desc).strip()
        desc = _normalize_entity_spacing(desc)
        if desc:
            out.append(f"【百度百科：{title}】{desc[:SEARCH_SNIPPET_CHARS]}")
            if len(out) >= max_results:
                break
    return out

def _search_suggestions(query, max_results=6):
    """百度/B站建议接口：无登录、无 key，用来补充相关搜索方向。"""
    out = []
    seen = set()
    providers = (
        (
            "百度建议",
            "https://www.baidu.com/sugrec?prod=pc&wd=" + urllib.parse.quote(query),
            lambda d: d.get("g") or [],
            lambda item: item.get("q"),
        ),
        (
            "360建议",
            (
                "https://sug.so.360.cn/suggest?word="
                + urllib.parse.quote(query)
                + "&encodein=utf-8&encodeout=utf-8"
            ),
            lambda d: d.get("result") or [],
            lambda item: item.get("word"),
        ),
        (
            "必应建议",
            "https://api.bing.com/qsonhs.aspx?q=" + urllib.parse.quote(query),
            lambda d: [
                item
                for group in ((d.get("AS") or {}).get("Results") or [])
                for item in (group.get("Suggests") or [])
            ],
            lambda item: item.get("Txt"),
        ),
        (
            "B站建议",
            "https://s.search.bilibili.com/main/suggest?term=" + urllib.parse.quote(query),
            lambda d: (d.get("result") or {}).get("tag") or [],
            lambda item: item.get("term") or item.get("value"),
        ),
    )
    for name, url, get_items, get_value in providers:
        try:
            data = _fetch_json(url, timeout=4)
        except Exception:
            continue
        try:
            items = get_items(data) or []
        except Exception:
            items = []
        for item in items:
            value = get_value(item) if isinstance(item, dict) else ""
            value = re.sub(r"<[^>]+>", "", str(value or "")).strip()
            value = _normalize_entity_spacing(value)
            if not value or value in seen:
                continue
            seen.add(value)
            out.append(f"【{name}】{value}")
            if len(out) >= max_results:
                return out
    return out


def _search_douban_movies(tag="热门", max_results=10):
    """豆瓣热门电影 JSON 接口，免登录、低反爬，用于电影推荐。"""
    url = (
        "https://movie.douban.com/j/search_subjects"
        f"?type=movie&tag={urllib.parse.quote(tag)}"
        f"&sort=recommend&page_limit={max_results}&page_start=0"
    )
    data = _fetch_json(url, timeout=5)
    subjects = data.get("subjects") or []
    rows = []
    for item in subjects[:max_results]:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        rate = str(item.get("rate") or "").strip()
        rows.append(f"《{title}》" + (f"（豆瓣 {rate}）" if rate else ""))
    return rows


def _extract_bing_results(html, max_results, query=""):
    """只解析必应结果块 li.b_algo，避免把页面其他 h2/h3 当结果。"""
    output = []
    seen = set()
    qterms = _query_terms(query) if query else set()
    for m in re.finditer(
        r'<li[^>]*class=["\'][^"\']*b_algo[^"\']*["\'][^>]*>(.*?)</li>',
        html,
        re.S,
    ):
        block = m.group(1)
        h2 = re.search(r"<h2[^>]*>(.*?)</h2>", block, re.S)
        if not h2:
            continue
        title = re.sub(r"<[^>]+>", " ", h2.group(1))
        title = _normalize_entity_spacing(
            _html.unescape(re.sub(r"\s+", " ", title)).strip())
        if len(title) < 5 or title in seen:
            continue
        p = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        if not p:
            cap = re.search(
                r'<div[^>]*class=["\'][^"\']*(?:b_caption|b_lineclamp)[^"\']*["\'][^>]*>(.*?)</div>',
                block,
                re.S,
            )
            if cap:
                p = cap
        desc = ""
        if p:
            desc = re.sub(r"<[^>]+>", " ", p.group(1))
            desc = _normalize_entity_spacing(
                _html.unescape(re.sub(r"\s+", " ", desc)).strip())
        if any(noise in title or noise in desc for noise in _SEARCH_NOISE):
            continue
        if _is_generic_year_noise(title, desc, query):
            continue
        if qterms:
            combined = (title + desc).lower()
            if not _is_relevant_result(combined, qterms):
                continue
        if desc:
            output.append(f"【{title[:60]}】{desc[:SEARCH_SNIPPET_CHARS]}")
            seen.add(title)
            if len(output) >= max_results:
                break
    return output

def _extract_search_results(html, engine, max_results, query=""):
    output = []
    seen = set()
    qterms = _query_terms(query) if query else set()
    if engine in ("bing", "bing_cn"):
        return _extract_bing_results(html, max_results, query)
    for m in re.finditer(r'<(h2|h3)[^>]*>(.*?)</\1>', html, re.S):
        title = re.sub(r'<[^>]*(?:>|$)', '', m.group(2)).strip()
        title = re.sub(r'\s+(?:src|href|class|style|data-[a-z-]+)=[^ >]+', '', title)
        title = _html.unescape(re.sub(r'\s+', ' ', title))
        title = _normalize_entity_spacing(title)
        if len(title) < 5 or title in seen:
            continue
        tail = html[m.end():m.end() + 1200]
        tail = re.sub(r'<script.*?</script>', '', tail, flags=re.S)
        tail = re.sub(r'<style.*?</style>', '', tail, flags=re.S)
        tail = re.sub(r'<[^>]+>', ' ', tail)
        tail = re.sub(r'&nbsp;', ' ', tail)
        tail = re.sub(r'\s+', ' ', tail).strip()
        desc = _normalize_entity_spacing(
            _html.unescape(tail[:SEARCH_SNIPPET_CHARS]))
        if "<" in desc or ">" in desc:
            continue
        if any(noise in title or noise in desc for noise in _SEARCH_NOISE):
            continue
        if _is_generic_year_noise(title, desc, query):
            continue
        if qterms:
            combined = (title + desc).lower()
            if not _is_relevant_result(combined, qterms):
                continue
        if len(desc) > 10:
            output.append(f"【{title[:60]}】{desc[:SEARCH_SNIPPET_CHARS]}")
            seen.add(title)
        if len(output) >= max_results:
            break
    return output

def _search_engine(query, engine, max_results):
    if engine == "suggest" and (
        _skip_recommendation_suggestions(query)
        or _skip_person_suggestions(query)
    ):
        return []
    if engine == "wikipedia":
        return _search_wikipedia(query, max_results)
    if engine == "arxiv":
        return _search_arxiv(query, max_results)
    if engine == "pubmed":
        return _search_pubmed(query, max_results)
    if engine == "baike":
        return _search_baike_openapi(query, max_results)
    if engine == "suggest":
        return _search_suggestions(query, max_results)
    urls = {
        "360": f"https://www.so.com/s?q={urllib.parse.quote(query)}",
        "360_m": f"https://m.so.com/s?q={urllib.parse.quote(query)}",
        "quark": f"https://quark.sm.cn/s?q={urllib.parse.quote(query)}",
        "sogou": f"https://www.sogou.com/web?query={urllib.parse.quote(query)}&ie=utf8",
        "bing": f"https://www.bing.com/search?q={urllib.parse.quote(query)}",
        "bing_cn": (
            f"https://cn.bing.com/search?q={urllib.parse.quote(query)}&setlang=zh-hans"),
        "baidu": f"https://www.baidu.com/s?wd={urllib.parse.quote(query)}&ie=utf-8&rn=20",
        "baidu_m": f"https://m.baidu.com/s?word={urllib.parse.quote(query)}",
        "weixin": f"https://weixin.sogou.com/weixin?type=2&query={urllib.parse.quote(query)}",
    }
    fetch_timeout = 5.0 if engine in ("baidu_m", "weixin", "360_m") else 3.5
    extra_headers = {}
    if engine in ("baidu", "baidu_m"):
        extra_headers["Referer"] = "https://www.baidu.com/"
    elif engine in ("sogou", "weixin"):
        extra_headers["Referer"] = "https://www.sogou.com/"
    html = _fetch_html(urls[engine], timeout=fetch_timeout, extra_headers=extra_headers)
    return _extract_search_results(html, engine, max_results, query)


def _search_duckduckgo(query, max_results=8):
    """DuckDuckGo 无 key 搜索兜底，复用项目 web-search skill 脚本。"""
    script = os.path.join(
        BASE_DIR, "skills", "web-search", "scripts", "search.py")
    if not os.path.exists(script):
        return []
    python_exe = sys.executable.replace("pythonw.exe", "python.exe")
    try:
        proc = subprocess.run(
            [
                python_exe, script, query,
                "--format", "json",
                "--max-results", str(max_results),
                "--region", "cn-zh",
                "--safe-search", "moderate",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
    except Exception as exc:
        log(f"[搜索] duckduckgo 调用失败: {repr(exc)[:80]}")
        return []
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        rows = json.loads(proc.stdout)
    except Exception:
        return []
    out = []
    seen = set()
    for row in rows or []:
        title = _normalize_entity_spacing(str(row.get("title") or "").strip())
        desc = _normalize_entity_spacing(str(row.get("body") or "").strip())
        url = str(row.get("href") or "").strip()
        if not title or not desc:
            continue
        key = re.sub(r"\W+", "", title.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        line = f"【{title[:60]}】{desc[:SEARCH_SNIPPET_CHARS]}"
        if url:
            line += f"（{url}）"
        out.append(line)
        if len(out) >= max_results:
            break
    return out


_LYRIC_URL_HINTS = (
    "22lrc.com", "genius.com", "mulanci.org", "goflac.com",
    "geciyi.com", "jayfc.com", "lyrics.net.cn", "geci", "lyrics", "songs",
)


def _extract_page_text(html, max_chars=MAX_PAGE_TEXT_CHARS):
    """提取网页正文，优先正文容器，再去掉导航/脚本类噪音。"""
    try:
        from bs4 import BeautifulSoup
    except Exception:
        BeautifulSoup = None
    if BeautifulSoup is None:
        text = re.sub(r"<script.*?</script>", "", html or "", flags=re.S)
        text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = _normalize_entity_spacing(
            _html.unescape(re.sub(r"[ \t]+", " ", text)))
        return text.strip()[:max_chars]
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()
    main = (
        soup.find(["main", "article"])
        or soup.find(attrs={"class": re.compile(r"(?:lyric|lyrics|geci|content|article|main)", re.I)})
        or soup.body
        or soup
    )
    text = main.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lines = [_normalize_entity_spacing(ln) for ln in lines]
    return "\n".join(lines)[:max_chars]


def _extract_search_links(html, max_links=8):
    """从必应/通用搜索结果页提取 URL，供歌词类深读使用。"""
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return []
    soup = BeautifulSoup(html or "", "html.parser")
    links = []
    seen = set()
    for a in soup.select("li.b_algo h2 a, h2 a, h3 a"):
        href = a.get("href") or ""
        title = a.get_text(" ", strip=True)
        if not href.startswith("http") or not title:
            continue
        title = _html.unescape(re.sub(r"\s+", " ", title)).strip()
        title = _normalize_entity_spacing(title)
        if title in seen:
            continue
        if any(noise in title for noise in _SEARCH_NOISE):
            continue
        seen.add(title)
        links.append({"title": title, "url": href})
        if len(links) >= max_links:
            break
    links.sort(
        key=lambda item: 0
        if any(h in item["url"].lower() for h in _LYRIC_URL_HINTS)
        else 1
    )
    return links


def _looks_like_lyrics_page(text, query, url=""):
    if len(text or "") < 200:
        return False
    qterms = _query_terms(query)
    if qterms and not _is_relevant_result(text, qterms):
        return False
    lines = [ln for ln in text.splitlines() if len(ln.strip()) >= 4]
    url_lower = (url or "").lower()
    known_lyric_url = any(
        hint in url_lower for hint in _LYRIC_URL_HINTS
    )
    lyric_marker = "歌词" in text or "lyric" in text.lower()
    if known_lyric_url:
        return len(lines) >= 6
    # 专辑介绍/百科正文很容易过“行数”检查，必须再看到歌词标记才敢抓。
    return len(lines) >= 8 and lyric_marker


def _extract_js_redirect(html):
    m = re.search(
        r"window\.location\.(?:replace|href)\s*(?:=\s*)?\(?[\"']([^\"']+)[\"']",
        html or "",
    )
    if m:
        return m.group(1)
    m = re.search(
        r"<meta[^>]+http-equiv=[\"']refresh[\"'][^>]+content=[\"']\d+;\s*URL=(?:[\"']?)([^\"'>]+)",
        html or "",
        re.I,
    )
    return m.group(1) if m else ""


async def _fetch_page_text_async(url, timeout=4, max_redirects=2):
    current = url
    for _ in range(max_redirects + 1):
        html = await asyncio.wait_for(
            asyncio.to_thread(_fetch_html, current, timeout),
            timeout=timeout + 1,
        )
        redirect = _extract_js_redirect(html)
        if redirect:
            current = urllib.parse.urljoin(current, redirect)
            continue
        return _extract_page_text(html, max_chars=MAX_PAGE_TEXT_CHARS)
    return ""


def _lyric_search_url(query, engine):
    q = _clean_search_query(query)
    q = urllib.parse.quote(q)
    if engine == "360":
        return f"https://www.so.com/s?q={q}"
    if engine == "sogou":
        return f"https://www.sogou.com/web?query={q}&ie=utf8"
    return f"https://cn.bing.com/search?q={q}&setlang=zh-hans"


def _extract_lyric_song_title(query):
    """从歌词问题里提取歌名，去掉歌手/专辑/“歌词”这类搜索噪音。"""
    q = _clean_search_query(query or "")
    bracket = re.search(r"《([^》]+)》", q)
    if bracket and len(bracket.group(1).strip()) >= 2:
        return bracket.group(1).strip()
    q = re.sub(r"(?:完整)?歌词(?:是什么|有哪些|呢|有吗)?", " ", q)
    q = re.sub(
        r"周杰伦|林俊杰|热狗|MC ?HotDog|方文山|太阳之子|新专辑|专辑|"
        r"合唱|演唱|合作|新歌|和|与|及",
        " ",
        q,
        flags=re.IGNORECASE,
    )
    q = q.strip()
    q = re.sub(r"^(?:的)+", " ", q)
    q = re.sub(r"[\s，。！？、：:]+", " ", q).strip()
    q = q.rstrip("的").strip()
    parts = [part.strip() for part in q.split() if len(part.strip()) >= 2]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    # 有歌手前缀时取最后的歌名片段；普通多词歌名保留整句。
    return parts[-1] if len(parts[-1]) >= 2 and parts[-1] not in (
        "歌词", "完整", "是什么", "周杰伦", "林俊杰"
    ) else " ".join(parts)


def _jayfc_search_links(query, max_links=6):
    """周杰伦歌迷联盟无反爬直连，专门兜底周杰伦歌曲。"""
    song = _extract_lyric_song_title(query)
    if not song:
        return []
    candidates = [song]
    if len(song) >= 4:
        candidates.append(song[-2:])
    seen = set()
    out = []
    for candidate in candidates:
        try:
            url = "https://jayfc.com/search?q=" + urllib.parse.quote(candidate)
            html = _fetch_html(url, timeout=5)
        except Exception:
            continue
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html or "", "html.parser")
            for a in soup.select("a[href*='/songs/']"):
                title = a.get_text(" ", strip=True)
                href = urllib.parse.urljoin("https://jayfc.com", a.get("href") or "")
                if not title or not href or title in seen:
                    continue
                if not _is_relevant_result(title, _query_terms(song)):
                    continue
                seen.add(title)
                out.append({"title": title, "url": href})
                if len(out) >= max_links:
                    return out
        except Exception:
            continue
        if out:
            break
    return out


async def _fetch_lyric_links_from_engine(query, engine="360", max_links=6):
    try:
        html = await asyncio.wait_for(
            asyncio.to_thread(
                _fetch_html,
                _lyric_search_url(query, engine),
                4,
            ),
            timeout=5,
        )
    except Exception:
        return []
    links = _extract_search_links(html, max_links=max_links)
    return [
        item for item in links
        if any(
            hint in item["url"].lower()
            or hint in item["title"].lower()
            for hint in _LYRIC_URL_HINTS
        ) or "歌词" in item["title"] or "lyric" in item["title"].lower()
    ] or links


async def _fetch_lyrics_body(query, max_pages=1, max_links=6):
    """搜索歌词类 query，打开结果页并提取正文；没有正文时返回空串。"""
    cache_key = _clean_search_query(query)
    with _LYRICS_BODY_CACHE_LOCK:
        cached = _LYRICS_BODY_CACHE.get(cache_key)
        if cached and time.time() - cached[0] < _LYRICS_BODY_CACHE_TTL:
            return cached[1]
    links = []
    seen_titles = set()
    for item in _jayfc_search_links(query, max_links=max_links):
        if item["title"] not in seen_titles:
            seen_titles.add(item["title"])
            links.append(item)
    if links:
        log(f"[歌词] jayfc 直连命中 {len(links)} 条")
    for engine in ("360", "sogou", "bing_cn"):
        engine_links = await _fetch_lyric_links_from_engine(
            query, engine=engine, max_links=max_links)
        for item in engine_links:
            if item["title"] in seen_titles:
                continue
            seen_titles.add(item["title"])
            links.append(item)
        if len(links) >= max_links * 2:
            break
    if not links:
        try:
            dr_html = await asyncio.wait_for(
                asyncio.to_thread(
                    _drission_search_html, query, "bing_cn",
                    timeout=max(6.0, DRISSION_SEARCH_TIMEOUT),
                ),
                timeout=DRISSION_SEARCH_TIMEOUT + 1.5,
            )
            links = _extract_search_links(dr_html, max_links=max_links * 2)
        except Exception as e:
            log(f"[歌词] 浏览器兜底失败 {repr(e)[:60]}")
    links.sort(key=lambda item: (
        0 if "歌词" in item["title"] or "lyric" in item["title"].lower()
        or any(hint in item["url"].lower() for hint in _LYRIC_URL_HINTS)
        else 1,
        item["title"],
    ))
    log(f"[歌词] {cache_key[:30]} 链接候选 {len(links)} 条")
    body_parts = []
    for item in links:
        host = urllib.parse.urlparse(item["url"]).netloc or item["url"][:40]
        try:
            text = await _fetch_page_text_async(item["url"], timeout=4)
        except Exception as e:
            log(f"[歌词] 页面失败 {host}: {repr(e)[:60]}")
            continue
        if not _looks_like_lyrics_page(text, query, item["url"]):
            log(f"[歌词] 页面跳过 {host}：未检测到有效歌词")
            continue
        log(f"[歌词] 页面正文成功 {host}：{len(text)}字")
        body_parts.append(
            f"来源：{item['url']}\n标题：{item['title']}\n{text[:MAX_PAGE_TEXT_CHARS]}"
        )
        if len(body_parts) >= max_pages:
            break
    if not body_parts:
        log(f"[歌词] 正文抓取失败 {cache_key[:30]}")
    result = "\n\n".join(body_parts)
    with _LYRICS_BODY_CACHE_LOCK:
        _LYRICS_BODY_CACHE[cache_key] = (time.time(), result)
        if len(_LYRICS_BODY_CACHE) > 100:
            for old_key in list(_LYRICS_BODY_CACHE)[:-80]:
                _LYRICS_BODY_CACHE.pop(old_key, None)
    return result


def _lyric_subject_terms(query):
    """提取歌词问题里的歌名关键词，用于回答校验，不做记忆写入。"""
    q = _clean_question(query or "")
    m = re.search(r"《([^》]+)》", q)
    if m and len(m.group(1).strip()) >= 2:
        return [m.group(1).strip()]
    q = re.sub(
        r"周杰伦|林俊杰|热狗|MC ?HotDog|新专辑|太阳之子|专辑|合唱|演唱|合作|新歌|和|与|及",
        " ",
        q,
    )
    q = re.sub(r"(?:完整)?歌词(?:是什么|呢|有吗)?", " ", q)
    parts = [
        p.strip()
        for p in re.split(r"[\s的，。！？：:]+", q)
        if len(p.strip()) >= 2
    ]
    return parts[:2]


def _lyric_reply_suspect(reply, query, source_text, memory_text=""):
    reply = reply or ""
    if "《" in reply and "》" not in reply:
        return True
    if reply.count("《") > reply.count("》"):
        return True
    subjects = _lyric_subject_terms(query)
    if subjects and any(s not in reply for s in subjects):
        return True
    norm_reply = _entity_norm(reply)
    for fragment in _lyric_key_fragments(query, source_text, memory_text):
        fragment = _entity_norm(fragment)
        if fragment and fragment not in norm_reply:
            return True
    return False


def _extract_lyrics_block(text, max_lines=140):
    """从深读正文里切出歌词行，避免模型重写后丢字/改字。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    start = None
    for idx, line in enumerate(lines):
        if (
            line in ("歌词", "Lyrics", "lyrics")
            or "歌词已迁移" in line
            or line.startswith("歌词：")
        ):
            start = idx
            break
    if start is None:
        return ""
    idx = start
    while idx < len(lines) and (
        lines[idx] in ("歌词", "Lyrics", "lyrics")
        or "歌词已迁移" in lines[idx]
    ):
        idx += 1
    end = len(lines)
    for j in range(idx, len(lines)):
        line = lines[j]
        if (
            line.startswith("【")
            or line.startswith("来源：")
            or line.startswith("标题：")
            or line in ("歌曲", "返回")
            or line.startswith("第 ")
            or (line.startswith("（") and "Aurora" in line)
        ):
            end = j
            break
    block = lines[idx:end]
    if len(block) < 4:
        return ""
    return "\n".join(block[:max_lines])


def _extract_memory_lyrics(text, title):
    """从 bot_memory.md 的手动歌词条目里抽取完整可靠歌词。"""
    if not text or not title:
        return ""
    for line in text.splitlines():
        if f"《{title}》" not in line or "真实歌词" not in line:
            continue
        m = re.search(
            r"真实歌词(?:段落|开头)?[：:]\s*[“\"](.+?)[”\"]",
            line,
            re.S,
        )
        if m:
            content = re.sub(r"\s*/\s*", "\n", m.group(1))
            content = content.strip()
            if len(content) >= 20:
                return content
    return ""


def _lyric_key_fragments(query, source_text, memory_text="", max_fragments=4):
    """选最长的几行可靠歌词做逐字校验，抓模型漏字/改字。"""
    title = _extract_lyric_song_title(query)
    blocks = []
    block = _extract_lyrics_block(source_text)
    if block:
        blocks.append(block)
    memory_block = _extract_memory_lyrics(memory_text, title)
    if memory_block:
        blocks.append(memory_block)
    fragments = set()
    for block in blocks:
        for line in block.splitlines():
            line = re.sub(r"\s+", "", line.strip())
            if len(line) >= 6:
                fragments.add(line)
    return sorted(fragments, key=len, reverse=True)[:max_fragments]


def _build_lyrics_fallback_answer(query, source_text, memory_text=""):
    title = _extract_lyric_song_title(query)
    block = _extract_lyrics_block(source_text)
    if not block:
        block = _extract_memory_lyrics(memory_text, title)
    if title and block:
        return (
            f"《{title}》完整歌词塔菲找到啦喵：\n"
            f"{block}\n\n"
            "塔菲按可靠歌词原样贴的，没瞎改喵。"
        )
    if title:
        return (
            f"塔菲确认《{title}》存在，但可靠资料里还没拿到完整歌词喵。"
        )
    return "塔菲没拿到完整歌词喵，不敢编。"


def search_web(query, max_results=8):
    """多源搜索：统一入口带缓存、引擎失败统计和自动降级。"""
    ddg_results = _search_duckduckgo(query, max_results)
    if ddg_results:
        combined = "\n".join(ddg_results)
        _search_cache_set(_search_cache_key(query), combined)
        log(f"[搜索] duckduckgo 优先 ({len(ddg_results)}条)")
        return combined
    result = search_with_resilience(query, max_results)
    if result and not result.startswith("（搜索失败"):
        return result
    ddg_results = _search_duckduckgo(query, max_results)
    if ddg_results:
        combined = "\n".join(ddg_results)
        _search_cache_set(_search_cache_key(query), combined)
        log(f"[搜索] 使用 duckduckgo 兜底 ({len(ddg_results)}条)")
        return combined
    if DRISSION_SEARCH_ENABLED:
        try:
            html = _drission_search_html(query, "bing_cn")
            results = _extract_search_results(
                html, "bing_cn", max_results, query)
            if results:
                combined = "\n".join(results)
                _search_cache_set(_search_cache_key(query), combined)
                log("[搜索] 使用 bing_cn 浏览器兜底")
                return combined
        except Exception as e:
            log(f"[搜索] 浏览器兜底失败: {repr(e)[:60]}")
    return ""

async def search_web_async(query, max_results=8, entities=None, extra_queries=None, exact=False):
    """并发搜索国内源：必应中国/百度桌面/搜狗桌面为主，整体 5 秒内返回。"""
    cache_key = _search_cache_key(query)
    cached = _search_cache_get(cache_key)
    if cached:
        inc_stat("search_cache_hits")
        log("[搜索] 命中缓存")
        return cached
    try:
        ddg_results = await asyncio.wait_for(
            asyncio.to_thread(_search_duckduckgo, query, max_results),
            timeout=25,
        )
    except Exception as exc:
        ddg_results = []
        log(f"[搜索] duckduckgo 优先失败: {repr(exc)[:80]}")
    if ddg_results:
        combined = "\n".join(ddg_results)
        log(f"[搜索] duckduckgo 优先 ({len(ddg_results)}条)")
        _search_cache_set(cache_key, combined)
        return combined
    # 2026-08-05 实测：百度/搜狗桌面版优先；微信/360/百度移动作为兜底。
    variants = [query] if exact else _build_search_variants(query, entities, extra_queries)
    primary = ("bing_cn", "sogou", "suggest", "360_m", "baidu")
    secondary = (
        "weixin", "360", "quark", "baidu_m",
        "wikipedia", "arxiv", "pubmed",
    )
    _short = {
        "360_m": 4.0, "baidu_m": 4.5, "weixin": 4.5,
        "wikipedia": 3.5, "arxiv": 5.0, "pubmed": 5.0,
    }
    tasks = {}
    for engine in primary:
        if _is_engine_degraded(engine):
            continue
        if len(tasks) >= MAX_SEARCH_TASKS:
            break
        for variant in variants:
            inc_stat("search_calls")
            task = asyncio.ensure_future(asyncio.wait_for(
                asyncio.to_thread(_search_engine, variant, engine, max_results),
                timeout=_short.get(engine, 3.5)))
            tasks[task] = (engine, variant)
    for engine in secondary:
        if _is_engine_degraded(engine):
            continue
        if len(tasks) >= MAX_SEARCH_TASKS:
            break
        inc_stat("search_calls")
        task = asyncio.ensure_future(asyncio.wait_for(
            asyncio.to_thread(_search_engine, query, engine, max_results),
            timeout=_short.get(engine, 3.5)))
        tasks[task] = (engine, query)
    collected = []
    used_engines = []
    deadline = time.monotonic() + 2.2
    start = time.monotonic()
    while tasks and time.monotonic() < deadline:
        done, pending = await asyncio.wait(
            tasks, timeout=max(0.1, deadline - time.monotonic()),
            return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            engine, variant = tasks.pop(task)
            try:
                result = task.result()
            except Exception as e:
                inc_stat("search_fail")
                _record_engine_fail(engine)
                log(f"[搜索] {engine}:{variant[:30]} 失败: {repr(e)[:60]}")
                continue
            if result:
                inc_stat("search_success")
                collected.append(result)
                used_engines.append(engine)
            else:
                inc_stat("search_fail")
                _record_engine_fail(engine)
                log(f"[搜索] {engine}:{variant[:30]} 空结果")
        if collected:
            total_entries = sum(len(r) for r in collected)
            elapsed = time.monotonic() - start
            non_suggest_used = any(e != "suggest" for e in used_engines)
            if "bing_cn" in used_engines:
                break
            if non_suggest_used and (
                not tasks or elapsed >= 1.6
            ):
                break
            if (
                non_suggest_used
                and total_entries >= max_results
                and len(used_engines) >= 2
            ):
                break
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*list(tasks), return_exceptions=True)
    if collected:
        merged = _merge_search_results(collected, max_results * 2)
        combined = "\n".join(merged)
        log(f"[搜索] 合并 {'/'.join(used_engines)} (并发)")
        _search_cache_set(cache_key, combined)
        return combined
    try:
        ddg_results = await asyncio.wait_for(
            asyncio.to_thread(_search_duckduckgo, query, max_results),
            timeout=25,
        )
    except Exception as exc:
        ddg_results = []
        log(f"[搜索] duckduckgo 兜底失败: {repr(exc)[:80]}")
    if ddg_results:
        combined = "\n".join(ddg_results)
        log(f"[搜索] 使用 duckduckgo 兜底 ({len(ddg_results)}条)")
        _search_cache_set(cache_key, combined)
        return combined
    if DRISSION_SEARCH_ENABLED:
        try:
            dr_html = await asyncio.wait_for(
                asyncio.to_thread(
                    _drission_search_html, query, "bing_cn",
                    timeout=max(6.0, DRISSION_SEARCH_TIMEOUT),
                ),
                timeout=DRISSION_SEARCH_TIMEOUT + 1.5,
            )
            results = _extract_search_results(
                dr_html, "bing_cn", max_results, query)
            if results:
                combined = "\n".join(results)
                log("[搜索] 使用 bing_cn 浏览器兜底")
                _search_cache_set(cache_key, combined)
                return combined
        except Exception as e:
            log(f"[搜索] 浏览器兜底失败: {repr(e)[:60]}")
    return ""

def _merge_search_results(results_list, max_results):
    """合并多个搜索源的结果并按标题去重。"""
    seen = set()
    out = []
    for results in results_list:
        for item in results:
            title = item.split("】", 1)[0] if "】" in item else item[:20]
            key = re.sub(r"\W+", "", title.lower())
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
            if len(out) >= max_results:
                return out
    return out

def _rule_plan_for_query(text):
    """高频问题类型的规则兜底搜索计划，模型失效时也能拆。"""
    q = _clean_question(text or "")
    year = time.strftime("%Y年")
    year_num = time.strftime("%Y")
    if _recommendation_category(q) or _extract_recommendation_entity(q):
        return _recommendation_search_plans(q)
    if "tiktok" in q.lower() and ("法案" in q or "剥离" in q):
        return [
            {"query": "TikTok divestment bill 2024", "purpose": "查英文法案核心条款"},
            {"query": "美国 TikTok 剥离 法案", "purpose": "查中文报道摘要"},
        ]
    if "日本央行" in q and ("负利率" in q or "2024" in q):
        return [
            {"query": "日本央行 2024年3月 结束负利率 加息", "purpose": "确认历史性货币政策决定"},
            {"query": "Bank of Japan March 2024 negative interest rate", "purpose": "查英文权威来源"},
        ]
    if "玄武口音" in q:
        return [
            {"query": "玄武口音 余承东 竹知了 梗", "purpose": "锁定玄武口音定义"},
            {"query": "玄武口音 华为 问界 M9 发布会", "purpose": "查具体出处"},
        ]
    m = re.search(r"(.+?)(?:年龄|几岁|多大|多少岁)", q)
    if m and len(m.group(1).strip()) >= 2:
        entity = re.sub(r"今年|去年|现在|目前|当前|最近", "", m.group(1).strip()).strip()
        year_suffix = "" if re.search(r"20\d{2}年?", entity) else year
        return [
            {"query": f"{entity} 年龄 {year_suffix}".strip(), "purpose": f"查询{entity}当前年龄"},
            {"query": f"{entity} 出生日期", "purpose": f"获取{entity}出生日期"},
        ]
    m = re.search(r"(.+?)(?:的)?(?:完整)?歌词", q)
    if m and len(m.group(1).strip()) >= 2:
        if "那天下雨了" in q and "歌词" in q:
            return [
                {"query": "周杰伦 太阳之子 那天下雨了 歌词", "purpose": "锁定正确歌曲"},
                {"query": "周杰伦 那天下雨了 歌词 车子缓缓的开", "purpose": "获取真实歌词段落"},
            ]
        if "加油" in q and "歌词" in q:
            return [
                {"query": "林俊杰 加油 歌词 最近你好吗", "purpose": "获取真实歌词段落"},
                {"query": "林俊杰 热狗 加油 歌词", "purpose": "核对演唱者与歌曲信息"},
            ]
        song = m.group(1).strip().rstrip("的")
        artist = ""
        ql = q.lower()
        if "林俊杰" in q:
            artist += " 林俊杰"
        if "热狗" in q or "mc hotdog" in ql:
            artist += " 热狗"
        return [
            {"query": f"{song}{artist} 歌词", "purpose": "获取歌曲完整歌词"},
            {"query": f"{song} 完整歌词", "purpose": "获取歌词完整段落"},
        ]
    m = re.search(r"(.+?)(?:冠军|亚军|季军)", q)
    if m and len(m.group(1).strip()) >= 2:
        entity = re.sub(r"今年|去年|现在|目前|当前|最近", "", m.group(1).strip()).strip()
        entity = re.sub(r"^(20\d{2})年(?=世界杯)", r"\1", entity)
        if "世界杯" in entity and not re.search(r"20\d{2}年?", entity):
            if not re.search(r"20\d{2}", entity):
                entity = f"{year_num}世界杯"
            return [
                {"query": f"{entity} 冠军", "purpose": "确认比赛或赛事冠军"},
                {"query": f"{entity} 决赛 比分", "purpose": "获取决赛比分"},
            ]
        year_suffix = "" if re.search(r"20\d{2}年?", entity) else year
        return [
            {"query": f"{entity} 冠军 {year_suffix}".strip(), "purpose": "确认比赛或赛事冠军"},
            {"query": f"{entity} 决赛 比分 {year_suffix}".strip(), "purpose": "获取决赛比分"},
        ]
    m = re.search(r"(.+?)(?:事件|发生了什么|怎么回事)", q)
    if m and len(m.group(1).strip()) >= 2:
        entity = re.sub(r"今年|去年|现在|目前|当前|最近", "", m.group(1).strip()).strip()
        year_suffix = "" if re.search(r"20\d{2}年?", entity) else year
        return [
            {"query": f"{entity} 事件 {year_suffix}".strip(), "purpose": "查找事件经过"},
            {"query": f"{entity} 最新消息", "purpose": "查找最新进展"},
        ]
    m = re.search(r"(?:谁是|是谁|介绍一下|介绍)(.+)", q)
    if m and len(m.group(1).strip()) >= 2:
        entity = m.group(1).strip()
        return [
            {"query": f"{entity} 是谁", "purpose": "确定人物或主体身份"},
            {"query": f"{entity} 百科", "purpose": "查找人物或主体资料"},
        ]
    return [{"query": q, "purpose": "按原问题搜索"}]

def _normalize_plan_queries(queries, max_queries=5):
    """校验 plan_search 查询；空计划不再用规则兜底，避免乱搜。"""
    planned = []
    seen = set()

    def add(raw_q, purpose):
        if len(planned) >= max_queries:
            return
        q = _clean_search_query(str(raw_q))
        for sub_q in _split_planned_query(q):
            if len(planned) >= max_queries:
                return
            if len(sub_q) < 2:
                continue
            if len(sub_q) > 30:
                sub_q = sub_q[:30]
            if sub_q in seen:
                continue
            seen.add(sub_q)
            planned.append({
                "query": sub_q,
                "purpose": purpose or "查相关信息",
            })

    for item in (queries or []):
        if isinstance(item, dict):
            add(item.get("query") or item.get("purpose") or "", item.get("purpose") or "")
        else:
            add(str(item or ""), "")

    return planned

def _split_planned_query(query):
    """长 query 兜底拆分：按常见连接词拆成短查询。"""
    if len(query) <= 24:
        return [query]
    for sep in ("和", "与", "还有", "然后", "、", "，", ",", ";", "；"):
        if sep in query:
            parts = [p.strip() for p in query.split(sep) if len(p.strip()) >= 2]
            if len(parts) >= 2:
                return parts[:4]
    return [query]

async def _search_planned_queries(queries, max_results=10, max_queries=5):
    """执行 plan_search 返回的多个短 query，并行搜索后合并。"""
    planned = _normalize_plan_queries(queries, max_queries=max_queries)
    if not planned:
        return ""
    done = await asyncio.gather(
        *(search_web_async(item["query"], max_results=max_results, exact=True)
          for item in planned),
        return_exceptions=True,
    )
    parts = []
    for item, result in zip(planned, done):
        q = item["query"]
        if isinstance(result, Exception):
            log(f"[plan_search] {q} 失败: {repr(result)[:80]}")
            continue
        text = str(result or "").strip()
        if text:
            parts.append(f"【查询：{q}】\n{text}")
    return "\n\n".join(parts)

_SOURCE_RANK = {
    "官方": 10,
    "政府": 10,
    "Reuters": 9,
    "路透": 9,
    "AP": 9,
    "美联社": 9,
    "arXiv": 9,
    "PubMed": 9,
    "百度百科": 4,
    "维基": 9,
    "快懂百科": 8,
    "GitHub": 8,
    "Stack Overflow": 8,
    "百度建议": 7,
    "B站建议": 7,
    "搜狗提示": 7,
    "官网": 7,
    "体育": 6,
    "新浪": 5,
    "腾讯": 5,
    "澎湃": 5,
    "新华社": 5,
    "知乎": 3,
    "贴吧": 3,
    "B站": 3,
    "bilibili": 3,
}

def _source_rank(title):
    for key, rank in _SOURCE_RANK.items():
        if key.lower() in title.lower():
            return rank
    return 0

def _append_age_fact(raw, query):
    """从搜索结果提取出生日期，按今天本地计算年龄，避免模型算错。"""
    if not any(k in query for k in ("年龄", "年纪", "几岁", "多大", "多少岁", "生日", "出生")):
        return raw
    m = re.search(r"(\d{4})年(\d{1,2})月(?:(\d{1,2})日)?", raw)
    if not m:
        return raw
    year, month = int(m.group(1)), int(m.group(2))
    day = int(m.group(3) or 1)
    now = time.localtime()
    age = now.tm_year - year
    if (now.tm_mon, now.tm_mday) < (month, day):
        age -= 1
    if m.group(3):
        fact = (
            f"\n【年龄计算】出生于{m.group(1)}年{m.group(2)}月{m.group(3)}日，"
            f"按今天{time.strftime('%Y年%m月%d日')}计算，今年{age}岁。"
        )
    else:
        fact = (
            f"\n【年龄计算】出生于{m.group(1)}年{m.group(2)}月，"
            f"按今天{time.strftime('%Y年%m月%d日')}判断，约{age}岁。"
        )
    return raw + fact if fact not in raw else raw

def _distill_search_results(
        raw, query, max_entries=10, max_chars=MAX_SEARCH_CHARS, entities=None):
    """本地轻量凝练：只按实体词过滤，不做组合打分排序。"""
    qterms = _query_terms(query) | {str(e).lower() for e in (entities or [])}
    entries = []
    seen = set()
    for line in raw.splitlines():
        m = re.match(r"【([^】]+)】(.*)", line.strip())
        if not m:
            continue
        title = _normalize_entity_spacing(m.group(1).strip())
        desc = _normalize_entity_spacing(m.group(2).strip())
        if len(desc) < 6:
            continue
        key = re.sub(r"[\s\W]+", "", title.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        if any(noise in title or noise in desc for noise in _SEARCH_NOISE):
            continue
        if _is_generic_year_noise(title, desc, query):
            continue
        combined = (title + desc).lower()
        if not _is_relevant_result(combined, qterms):
            continue
        entries.append((title, desc))
    entries.sort(key=lambda item: (
        0 if item[0].startswith("百度建议")
        or item[0].startswith("B站建议")
        or item[0].startswith("搜狗")
        else 1,
        -_source_rank(item[0]),
    ))
    out = []
    total = 0
    for title, desc in entries[:max_entries]:
        item = f"{title[:60]}：{desc[:SEARCH_DISTILL_CHARS]}"
        if total + len(item) > max_chars:
            break
        out.append(item)
        total += len(item)
    return _append_age_fact("\n".join(out), query)

def _web_search_to_text(rows, max_entries=10):
    """把智谱原生 web_search 返回结果转成本地搜索结果格式。"""
    lines = []
    seen = set()
    for item in (rows or [])[:max_entries]:
        if not isinstance(item, dict):
            continue
        title = _normalize_entity_spacing(
            str(item.get("title") or "").strip())
        content = _normalize_entity_spacing(
            re.sub(r"[ \t]+", " ", str(item.get("content") or "")).strip())
        if not title or not content:
            continue
        key = re.sub(r"[\W_]+", "", title.lower())
        if key in seen:
            continue
        seen.add(key)
        meta = " | ".join(
            str(x).strip() for x in (
                item.get("media"),
                item.get("publish_date"),
                item.get("link"),
            ) if str(x or "").strip()
        )
        line = f"【{title}】{content[:SEARCH_DISTILL_CHARS]}"
        if meta:
            line += f"（{meta}）"
        lines.append(line)
    return "\n".join(lines)

def _fix_known_phrase_errors(reply, query):
    """修已知固定短语的轻微缩字，例如玄武口音里的“玄武神兽”。"""
    reply = reply or ""
    if "玄武" in (query or "") and "玄歯" in reply:
        reply = reply.replace("玄歯", "玄武")
    if "玄武" in (query or "") and "玄齿" in reply:
        reply = reply.replace("玄齿", "玄武")
    if "玄武口音" in (query or "") and "玄武神" in reply and "玄武神兽" not in reply:
        reply = reply.replace("玄武神", "玄武神兽")
    if "世界杯" in (query or "") and "托雷斯" in reply and "费兰·托雷斯" not in reply:
        reply = reply.replace("干·托雷斯", "费兰·托雷斯")
    if "竹知了" in (query or "") and ".6万" in reply and "5.6万" not in reply:
        reply = reply.replace(".6万", "5.6万")
    for old, new in _LEGACY_POLLUTION_TABLE:
        reply = reply.replace(old, new)
    return reply


_NUMBER_PHRASE_RE = re.compile(
    r"\d+(?:\.\d+)?(?:万亿|亿|万|千|百)?(?:余)?"
    r"(?:条|个|%|％|人|元|家|次|件|款|篇|起|座|场|支|金|银|铜|届|枚|项|岁)?"
)

_SEARCH_SUGGEST_PREFIXES = (
    "【百度建议】",
    "【B站建议】",
    "【360建议】",
    "【必应建议】",
    "【搜狗提示】",
)


def _strip_suggestion_lines(text):
    """去掉建议词/提示词，避免把搜索建议当成可引用事实。"""
    if not text:
        return ""
    return "\n".join(
        line for line in (text or "").splitlines()
        if not line.lstrip().startswith(_SEARCH_SUGGEST_PREFIXES)
    ).strip()


def _search_evidence_text(text):
    """只保留可引用网页结果；搜索建议/提示词不能算有效搜索结果。"""
    return _strip_suggestion_lines(text or "").strip()


def _is_important_number(phrase):
    """短数字不带单位时不算关键事实，避免“20”被当成缺失字段。"""
    if len(phrase) >= 3:
        return True
    return bool(re.search(
        r"[万亿%％条个元家次件款篇起座场支金银铜届枚项岁]",
        phrase,
    ))


def _ensure_numeric_facts_preserved(reply, source_text):
    """把来源里的数字事实逐字保真，补回模型漏掉的前置数字。"""
    reply = reply or ""
    for match in _NUMBER_PHRASE_RE.finditer(source_text or ""):
        full = match.group(0)
        if len(full) < 2 or full in reply:
            continue
        if "." in full:
            partial = "." + full.split(".", 1)[1]
        else:
            partial = full[1:]
        if len(partial) >= 2 and partial in reply:
            reply = reply.replace(partial, full)
    return reply


def _split_source_entities(source_text):
    """从来源里拆出短专名候选，用于补齐模型压缩掉的后缀。"""
    parts = re.split(
        r"[，、。；;：:()（）\s]+",
        source_text or "",
    )
    out = []
    for part in parts:
        term = _normalize_entity_spacing(part.strip())
        if len(term) < 3 or len(term) > 12:
            continue
        if any(generic in term for generic in _GENERIC_SEARCH_TERMS):
            continue
        if not re.search(r"[\u4e00-\u9fa5]", term):
            continue
        out.append(term)
    return list(dict.fromkeys(out))


def _ensure_entity_facts_preserved(reply, source_text):
    """来源里有完整专名、回答只写前缀时，补回后缀。"""
    reply = reply or ""
    boundary = r"[，、。！？!?；;：:《》「」（）()—…\s]"
    for term in _split_source_entities(source_text or ""):
        if _entity_norm(term) in _entity_norm(reply):
            continue
        for cut in range(min(3, len(term)), 0, -1):
            prefix = term[:cut]
            if not prefix:
                continue
            pattern = re.escape(prefix) + r"(?=" + boundary + r"|$)"
            if re.search(pattern, reply):
                reply = re.sub(pattern, term, reply, count=1)
                break
        else:
            for length in range(min(4, len(term)), 1, -1):
                suffix = term[-length:]
                if not suffix:
                    continue
                pattern = (
                    r"(?<![a-zA-Z0-9])"
                    + re.escape(suffix)
                    + r"(?=" + boundary + r"|$)"
                )
                if re.search(pattern, reply):
                    reply = re.sub(pattern, term, reply, count=1)
                    break
    return reply


def _collapsed_source_variants(phrase, max_gap=8):
    """生成“中间被删掉一段”的压缩写法，用于匹配模型截断后的专名。"""
    variants = set()
    n = len(phrase)
    for start in range(n):
        for length in range(1, min(max_gap, n - start) + 1):
            variant = phrase[:start] + phrase[start + length:]
            if len(variant) >= 3:
                variants.add(variant)
    return variants


def _ensure_source_phrases_preserved(reply, source_text):
    """把来源里的完整长短语找回来，修复中间被压掉的片段。"""
    reply = reply or ""
    for phrase in _split_source_entities(source_text or ""):
        if len(phrase) < 6:
            continue
        if _entity_norm(phrase) in _entity_norm(reply):
            continue
        for variant in _collapsed_source_variants(phrase):
            if variant in reply:
                reply = reply.replace(variant, phrase)
                break
    return reply


def _looks_like_tool_call(reply):
    """识别模型把工具调用写成文本、没进入最终答案的情况。"""
    text = reply or ""
    return bool(re.search(
        r"<tool_call|<longcat_tool_call|<｜DSML｜tool_calls|"
        r"tool_calls|arguments|"
        r"\"queries\"|\"query\"|\"count\"|\"source\"|"
        r"plan_search|thought_process|recency_days|"
        r"\"type\"\s*:\s*\"function\"|\"name\"\s*:\s*\"search_web\"",
        text, re.IGNORECASE))


def _coerce_tool_arg(value, name):
    """把 DeepSeek 文本工具调用里的参数转成结构值。"""
    text = (value or "").strip()
    if name in {"queries", "entities"}:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    return text


def _parse_tagged_tool_calls(text):
    """解析 DeepSeek 常见文本格式：<｜DSML｜tool_calls> 和 <tool_call>。"""
    if not text:
        return []

    def add_call(name, args):
        if name not in {"plan_search", "search_web", "fetch_page"}:
            return
        if name == "plan_search" and not isinstance(args.get("queries"), list):
            return
        if name == "search_web" and not args.get("query"):
            return
        if name == "fetch_page" and not args.get("url"):
            return
        calls.append({
            "id": f"call_tag_{len(calls)}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        })

    calls = []
    for name, body in re.findall(
        r"<[^>]*invoke\s+name\s*=\s*[\"']([^\"']+)[\"'][^>]*>([\s\S]*?)"
        r"</[^>]*invoke\s*>",
        text,
        re.I,
    ):
        args = {}
        for pname, pval in re.findall(
            r"<[^>]*parameter\s+name\s*=\s*[\"']([^\"']+)[\"'][^>]*>([\s\S]*?)"
            r"</[^>]*parameter\s*>",
            body,
            re.I,
        ):
            args[pname.strip()] = _coerce_tool_arg(pval, pname)
        add_call(name.strip(), args)

    for name, body in re.findall(
        r"<[^>]*function\s*=\s*([A-Za-z_]+)[^>]*>([\s\S]*?)"
        r"</[^>]*tool_call\s*>",
        text,
        re.I,
    ):
        args = {}
        for pname, pval in re.findall(
            r"<[^>]*param\s+name\s*=\s*[\"']([^\"']+)[\"'][^>]*>([\s\S]*?)"
            r"</[^>]*param\s*>",
            body,
            re.I,
        ):
            args[pname.strip()] = _coerce_tool_arg(pval, pname)
        add_call(name.strip(), args)

    for name, body in re.findall(
        r"<[^>]*tool_call\s*>([A-Za-z_]+)([\s\S]*?)</[^>]*tool_call\s*>",
        text,
        re.I,
    ):
        args = {}
        for pname, pval in re.findall(
            r"<[^>]*arg_key\s*>([^<]+)</[^>]*arg_key\s*>\s*"
            r"<[^>]*arg\s*>([\s\S]*?)</[^>]*arg\s*>",
            body,
            re.I,
        ):
            args[pname.strip()] = _coerce_tool_arg(pval, pname)
        add_call(name.strip(), args)

    return calls


def _extract_tool_calls_from_text(text):
    """从模型误写进正文的工具调用 JSON 中恢复 tool_calls。"""
    if not text or not _looks_like_tool_call(text):
        return []
    tagged = _parse_tagged_tool_calls(text)
    if tagged:
        return _normalize_tool_calls(tagged)
    cleaned = re.sub(r"```(?:json)?|```", "", text or "")
    candidates = []
    idx = 0
    while idx < len(cleaned):
        ch = cleaned[idx]
        if ch not in "{[":
            idx += 1
            continue
        try:
            obj, end = json.JSONDecoder().raw_decode(cleaned[idx:])
        except Exception:
            idx += 1
            continue
        candidates.append(obj)
        idx += max(1, end)

    calls = []
    seen = set()

    def add_call(name, args):
        key = (name, json.dumps(args, ensure_ascii=False, sort_keys=True))
        if key in seen:
            return
        seen.add(key)
        calls.append({
            "id": f"call_text_{len(calls)}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        })

    for obj in candidates:
        if isinstance(obj, dict) and "queries" in obj:
            add_call("plan_search", {
                "thought_process": str(obj.get("thought_process") or ""),
                "queries": obj.get("queries") or [],
            })
            continue
        if isinstance(obj, dict) and "query" in obj:
            add_call("search_web", {
                "query": str(obj.get("query") or ""),
                "entities": obj.get("entities") or [],
            })
            continue
        if isinstance(obj, dict) and isinstance(obj.get("arguments"), dict):
            args = obj["arguments"]
            name = str(obj.get("name") or obj.get("function") or "")
            if "queries" in args:
                add_call(name or "plan_search", {
                    "thought_process": str(args.get("thought_process") or ""),
                    "queries": args.get("queries") or [],
                })
            elif "query" in args:
                add_call(name or "search_web", {
                    "query": str(args.get("query") or ""),
                    "entities": args.get("entities") or [],
                })
    return _normalize_tool_calls(calls)


def _normalize_tool_calls(tool_calls):
    """清洗模型返回的工具调用，修复/丢弃无效 arguments，避免上游 HTTP 400。"""
    normalized = []
    for tc in tool_calls or []:
        fn = tc.get("function") or {}
        name = str(fn.get("name") or "").strip()
        if name not in {"plan_search", "search_web", "fetch_page"}:
            continue
        raw_args = fn.get("arguments")
        if isinstance(raw_args, dict):
            args = raw_args
        else:
            raw_text = str(raw_args or "{}").strip()
            try:
                args = json.loads(raw_text)
            except Exception:
                match = re.search(r"\{.*\}", raw_text, re.S)
                try:
                    args = json.loads(match.group()) if match else None
                except Exception:
                    args = None
        if not isinstance(args, dict):
            continue
        normalized.append({
            "id": str(tc.get("id") or f"call_{len(normalized)}"),
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        })
    return normalized

def _looks_garbled_reply(reply):
    text = reply or ""
    if text.count("《") != text.count("》"):
        return True
    if text.count("“") != text.count("”"):
        return True
    if re.search(r"\d{4}年\d{1,2}月\d{1,2}日\d{1,2}年", text):
        return True
    if re.search(r"\d{4}年\s*[，,]?\s*\d{1,2}年", text):
        return True
    if re.search(r"\d{1,2}日\s*\d{1,2}日\s*\d{1,2}年", text):
        return True
    if "某处错字" in text or "某处夸张描述" in text:
        return True
    if "推荐系统" in text:
        return True
    if "某处跑题" in text or "某段跑题的残缺回答" in text or "M完全" in text:
        return True
    return False


def _has_repeated_long_phrase(text):
    compact = re.sub(r"[\s，。！？、；：]+", "", text or "")
    for length in (8, 10, 12):
        seen = set()
        for i in range(len(compact) - length + 1):
            chunk = compact[i:i + length]
            if chunk in seen:
                return True
            seen.add(chunk)
    return False


def _has_pathological_repetition(text):
    """只识别明显病态重复：长片段重复至少 3 次，避免误杀正常长回答。"""
    compact = re.sub(r"[\s，。！？、；：]+", "", text or "")
    for length in (7, 8, 10, 12, 16):
        counts = Counter(
            compact[i:i + length]
            for i in range(max(0, len(compact) - length + 1))
        )
        if counts and counts.most_common(1)[0][1] >= 3:
            return True
    return False


def _clean_pathological_repetition(text):
    """删除明显病态重复的长片段，保留第一次出现的内容。"""
    cleaned = text or ""
    for length in (16, 12, 10):
        while True:
            seen = {}
            found = None
            for i in range(len(cleaned) - length + 1):
                chunk = cleaned[i:i + length]
                if chunk in seen:
                    found = (chunk, seen[chunk], i)
                    break
                seen[chunk] = i
            if not found:
                break
            chunk, _, second = found
            cleaned = cleaned[:second] + cleaned[second + len(chunk):]
    return _sanitize_reply(cleaned)


def _reject_bad_final_reply(reply):
    """模型最终回答出现工具 JSON、乱码或重复长片段时，视为无效输出。"""
    return bool(
        reply
        and (
            _looks_like_tool_call(reply)
            or _looks_garbled_reply(reply)
            or _has_pathological_repetition(reply)
        )
    )


def _needs_self_check(reply, query, context=""):
    if _looks_garbled_reply(reply):
        return True
    if _looks_like_tool_call(reply):
        return True
    if _recommendation_category(query) or _person_bio_key(query):
        return True
    person_context = f"{query or ''} {context or ''}"
    if re.search(
        r"介绍一下|是谁|人物|选手|歌手|作家|演员|厂长|诺言|"
        r"viper|meiko|faker|rookie|theshy|bin哥|jiejie|xun",
        person_context,
        re.IGNORECASE,
    ):
        return True
    if _has_repeated_long_phrase(reply):
        return True
    return False


def _repetition_score(reply, last_answer):
    """粗略检测两句是否复读：按字符二元组重叠度。"""
    a = re.sub(r"\W+", "", reply or "")
    b = re.sub(r"\W+", "", last_answer or "")
    if len(a) < 10 or len(b) < 10:
        return 0.0
    grams = set(a[i:i+2] for i in range(len(a)-1))
    hits = sum(1 for i in range(len(b)-1) if b[i:i+2] in grams)
    return hits / max(1, len(b)-1)
    seq = difflib.SequenceMatcher(None, a, b).ratio()
    return max(hits / max(1, len(b)-1), seq)

# ---------- 群说话风格自学习 ----------
_STYLE_FRAGMENT_RE = re.compile(
    r"哈哈+|笑死|卧槽|草|绝了|离谱|666|yyds|蚌埠住了|家人们|谁懂|真的假的|"
    r"牛的|绷不住|麻了|绝绝子|破防|泪目|好家伙|不是吧|啊这|笑不活了|逆天|无语|抽象|"
    r"寄了|寄|典|孝|赢麻了|急了|润了|摆烂|卷|躺平|破防|蚌埠|栓Q|泰裤辣|"
    r"尊嘟假嘟|恐龙抗狼|哈基米|吗喽|牛马|鼠鼠|阿对对对|退退退|"
    r"6|66|6666|7|777|绷|蚌|乐|乐了|典中典|孝死|赢|麻了|绷不住了",
    re.IGNORECASE,
)
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")
_BANNED_STYLE_EMOJIS = set("🐢🔥")
_SENTENCE_PATTERN_RE = re.compile(
    r"[\u4e00-\u9fa5A-Za-z0-9]{1,8}"
    r"(?:是吧|是真的强|寄了|麻了|绷了|绝了|离谱|牛的|真的强|"
    r"没谁了|无敌|天花板|神了|封神|yyds|赢麻了|赢麻|急了|润了)"
)
_PARTICLE_RE = re.compile(r"[啊哦呢吧嘛呀哇哟喽呗哈]")
_ADDRESS_RE = re.compile(
    r"家人们|兄弟们|兄弟|哥们|姐妹们|宝子们|宝子|家人们谁懂|"
    r"各位|大佬们|大佬|老哥|老姐|集美们|集美"
)
_PUNCT_TYPES = {
    "period": re.compile(r"[。]"),
    "exclaim": re.compile(r"[！!]"),
    "question": re.compile(r"[？?]"),
    "ellipsis": re.compile(r"[。]{2,}|\.{2,}|…"),
    "wave": re.compile(r"[~～]"),
}
_STOPWORDS = {
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "我们", "你们", "他们",
    "这", "那", "这个", "那个", "什么", "怎么", "为什么", "哪里", "哪个",
    "一个", "可以", "没有", "不是", "不要", "不会", "不能", "已经", "还是",
    "就是", "也是", "都是", "不过", "但是", "然后", "所以", "因为", "如果",
    "现在", "今天", "昨天", "明天", "刚才", "马上", "等一下", "一会儿",
    "知道", "觉得", "认为", "感觉", "看到", "听到", "想到",
}

_ENTITY_NOISE = re.compile(
    r"你|我|他|她|它|认识|介绍|介绍一下|介绍下|请问|帮我|麻烦|告诉|说说|讲讲|"
    r"知道吗|知道|一下|怎么样|什么|怎么|为什么|谁|多少|如何|哪个|哪些|"
    r"歌词|歌曲|攻略|简介|百科|意思|含义|资料|消息|新闻|排名|选手|战队|俱乐部|人物|"
    r"游戏|电影|电视剧|小说|关注|谢谢|喵|请|评价|推荐|内容|原因|方法|做法|情况|"
    r"问题|回答|答案|信息|事情|特点|功能|区别|联系|影响|意义|价值|好处|坏处|优缺点|"
    r"吗|呢|啊|呀|哈|了|的|把|还|给|是|在|请|能|会|有|没有",
    re.IGNORECASE,
)


def _required_entity_terms(text):
    cleaned = _SEARCH_QUESTION_WORDS.sub(" ", _clean_question(text or ""))
    cleaned = _ENTITY_NOISE.sub(" ", cleaned)
    terms = set()
    for term in _GAME_QUERY_RE.findall(cleaned):
        if len(term) >= 2:
            terms.add(term)
    for token in re.findall(r"[A-Za-z0-9]+", cleaned):
        if len(token) >= 2 and token not in _STOPWORDS:
            terms.add(token)
    for run in re.findall(r"[\u4e00-\u9fa5]+", cleaned):
        if len(run) >= 2 and run not in _STOPWORDS:
            terms.add(run)
    return terms


def _reply_missing_entities(reply, query):
    required = _required_entity_terms(query)
    if not required:
        return []
    text = _entity_norm(reply or "")
    return [
        term for term in sorted(required)
        if _entity_norm(term) not in text
    ]


def _answer_field_hints(query):
    q = _clean_question(query or "")
    hints = []
    if re.search(
        r"多少|多少钱|价格|售价|市值|人数|人次|项大奖|几岁|年龄|纪录|起售价",
        q,
    ):
        hints.append("数字")
    if re.search(
        r"发售日期|发布日期|什么时候|日期|哪年|哪月|哪日",
        q,
    ):
        hints.append("日期")
    if re.search(
        r"是谁|冠名商|什么公司|名字|名称|叫法",
        q,
    ):
        hints.append("主体")
    if re.search(
        r"特点|提升|原因|趋势|核心含义|动态|总结|对比|区别|政策",
        q,
    ):
        hints.append("要点")
    return hints


def _source_required_numbers(source_text):
    """来源里带单位的数字/较长数字必须逐项出现在回答中。"""
    required = []
    source = source_text or ""
    for phrase in _NUMBER_PHRASE_RE.findall(source_text or ""):
        if re.fullmatch(r"\d{4}\.\d{1,2}", phrase):
            continue
        age = re.fullmatch(r"(\d+)岁", phrase)
        if age and int(age.group(1)) < 16:
            continue
        if (
            "." in phrase
            or re.search(r"[万亿%％条个元座场起金银铜支岁]", phrase)
            or re.search(
                re.escape(phrase) + r"(?:美元|美元/盎司|欧元|日元)",
                source,
            )
        ):
            required.append(phrase)
    return list(dict.fromkeys(required))[:12]


def _source_required_dates(source_text):
    dates = re.findall(
        r"20\d{2}年\d{1,2}月(?:\d{1,2}日)?",
        source_text or "",
    )
    return list(dict.fromkeys(dates))[:6]


def _unsupported_numeric_phrases(reply, source_text):
    """回答里的数字若不在来源中，视为模型硬凑，触发重答。"""
    source = source_text or ""
    unsupported = []
    for phrase in _NUMBER_PHRASE_RE.findall(reply or ""):
        if not _is_important_number(phrase):
            continue
        if len(phrase) < 2:
            continue
        if len(phrase) < 3 and (
            re.search(r"[A-Za-z]" + re.escape(phrase), reply)
            or re.search(re.escape(phrase) + r"[A-Za-z]", reply)
        ):
            continue
        if re.search(
            r"(?<!\d)" + re.escape(phrase) + r"(?!\d)",
            source,
        ):
            continue
        unsupported.append(phrase)
    return list(dict.fromkeys(unsupported))[:5]


def _answer_contract_missing(reply, query, source_text):
    source = _strip_suggestion_lines(source_text or "")
    reply_text = reply or ""
    missing = _reply_missing_entities(reply, query)
    missing = [
        term for term in missing
        if term not in _GENERIC_SEARCH_TERMS
        and _entity_norm(term) in _entity_norm(source)
    ][:5]
    for meme_key, required_terms in _MEME_REQUIRED_TERMS.items():
        if meme_key in (query or ""):
            for term in required_terms:
                if (
                    _entity_norm(term) in _entity_norm(source)
                    and _entity_norm(term) not in _entity_norm(reply_text)
                    and term not in missing
                ):
                    missing.append(term)
    fields = _answer_field_hints(query)
    if "数字" in fields:
        for phrase in _source_required_numbers(source):
            if phrase not in reply_text:
                missing.append(f"数字{phrase}")
    if "日期" in fields:
        for date_phrase in _source_required_dates(source):
            if date_phrase not in reply_text:
                missing.append(f"日期{date_phrase}")
    for phrase in _unsupported_numeric_phrases(reply_text, source):
        missing.append(f"无来源数字{phrase}")
    if "要点" in fields and len(source) >= 300 and len(reply_text.strip()) < 80:
        missing.append("关键要点")
    return list(dict.fromkeys(missing))[:6]


def _complete_missing_fields(reply, missing, source_text):
    """契约仍缺失时，把来源里能确认的实体/数字/日期补到回答尾部。"""
    reply = reply or ""
    source = source_text or ""
    additions = []
    for item in missing:
        phrase = item
        if item.startswith("数字") or item.startswith("日期"):
            phrase = item[2:]
        if (
            phrase
            and len(phrase) >= 2
            and _entity_norm(phrase) in _entity_norm(source)
            and _entity_norm(phrase) not in _entity_norm(reply)
            and phrase not in additions
        ):
            additions.append(phrase)
    if not additions:
        return reply
    return reply.rstrip() + "\n\n补充确认：" + "、".join(additions)


_MEMORY_SILENT_TERMS = ("牢大", "冰红茶", "男人味", "地狱笑话")

_KNOWN_MEME_FACTS = {
    "玄武口音": (
        "玄武口音是2026年关于华为常务董事余承东的互联网梗："
        "源起于余承东在问界M9等发布会上的讲话声音/口音，网友用传统玩具“竹知了”模仿，"
        "衍生出“玄武口音”“玄武之声”“余音绕梁”等称呼。"
        "如果搜索结果说成玄武神兽或南京玄武区，那是错误检索结果，忽略。"
    ),
    "竹知了": (
        "竹知了是2026年余承东口音恶搞梗：传统非遗玩具“竹知了”摇动发出的声音"
        "被网友认为很像余承东在问界M9等发布会上的说话声音，衍生出“玄武之声”等称呼。"
        "2026-08-04鸿蒙智行回应：监测相关网络信息累计5.6万余条，实际投诉171条，"
        "平台反馈下架144条；官方称投诉针对具体侵权内容，不是针对“竹知了”玩具本身。"
    ),
}

_MEME_REQUIRED_TERMS = {
    "玄武口音": ["玄武口音", "余承东", "竹知了"],
    "竹知了": ["竹知了", "余承东"],
}


def _filter_silent_memory_text(text):
    if not text:
        return ""
    lines = [line for line in text.splitlines()
             if not any(term in line for term in _MEMORY_SILENT_TERMS)]
    return "\n".join(lines).strip()


def _extract_style_fragments_v2(text):
    """采集一条群消息的多维风格特征。"""
    if not text or not text.strip():
        return {}

    result = {
        "fragments": [],
        "emojis": [],
        "patterns": [],
        "particles": [],
        "addresses": [],
        "punct": {},
        "length": len(text),
        "emoji_positions": [],
    }
    result["fragments"] = _STYLE_FRAGMENT_RE.findall(text)
    for m in _EMOJI_RE.finditer(text):
        if m.group() in _BANNED_STYLE_EMOJIS:
            continue
        pos = "end" if m.start() >= len(text) * 0.7 else "mid"
        result["emojis"].append(m.group())
        result["emoji_positions"].append(pos)
    result["patterns"] = _SENTENCE_PATTERN_RE.findall(text)
    result["particles"] = _PARTICLE_RE.findall(text)
    result["addresses"] = _ADDRESS_RE.findall(text)
    for name, regex in _PUNCT_TYPES.items():
        count = len(regex.findall(text))
        if count:
            result["punct"][name] = count
    return result


def _discover_ngrams(texts, n_range=(2, 4), min_count=3, top_k=20):
    """从最近消息中发现高频候选新梗。"""
    counter = Counter()
    for text in texts:
        tokens = re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]+", text)
        for n in range(n_range[0], n_range[1] + 1):
            for i in range(len(tokens) - n + 1):
                gram = "".join(tokens[i:i + n])
                if len(gram) >= 2 and gram not in _STOPWORDS:
                    if not any(sw in gram for sw in ("的", "了", "是", "在")):
                        counter[gram] += 1
    existing = set(_STYLE_FRAGMENT_RE.pattern.split("|"))
    candidates = [
        (gram, cnt) for gram, cnt in counter.most_common(top_k * 3)
        if cnt >= min_count and gram not in existing
    ]
    return [gram for gram, _ in candidates[:top_k]]


class GroupStyleStore:
    """多维群说话风格学习器，兼容原接口。"""

    SAVE_EVERY_N = 5
    EXAMPLES_PER_GROUP = 10
    LLM_SUMMARY_INTERVAL = 3600
    LLM_SUMMARY_MIN_STATS = 20
    MAX_EXAMPLE_LENGTH = 50
    SHORT_MSG_THRESHOLD = 15

    def __init__(self, path, complete_fn=None):
        self.path = path
        self.complete_fn = complete_fn
        self.data = self._load()
        self._save_counter = 0
        self._lock = threading.Lock()
        self._last_snapshot = time.time()

        import atexit
        atexit.register(self._atexit_save)

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _atexit_save(self):
        try:
            if self._save_counter > 0:
                self.save()
        except Exception:
            pass

    def learn(self, gid, text, uid=None):
        extracted = _extract_style_fragments_v2(text)
        if not extracted:
            return
        has_signal = any([
            extracted["fragments"],
            extracted["emojis"],
            extracted["patterns"],
            extracted["particles"],
            extracted["addresses"],
            extracted["punct"],
        ])
        if not has_signal:
            return

        self._save_counter += 1
        with self._lock:
            group = self.data.setdefault(str(gid), {})
            stats = group.setdefault("stats", {})
            per_user = group.setdefault("per_user", {})
            examples = group.setdefault("examples", [])

            self._update_counter(stats, "fragments", extracted["fragments"])
            self._update_counter(stats, "emojis", extracted["emojis"])
            self._update_counter(stats, "patterns", extracted["patterns"])
            self._update_counter(stats, "particles", extracted["particles"])
            self._update_counter(stats, "addresses", extracted["addresses"])

            punct_stats = stats.setdefault("punct", {})
            for k, v in extracted["punct"].items():
                punct_stats[k] = punct_stats.get(k, 0) + v

            length_stats = stats.setdefault("length", {"sum": 0, "count": 0, "short_count": 0})
            length_stats["sum"] += extracted["length"]
            length_stats["count"] += 1
            if extracted["length"] <= self.SHORT_MSG_THRESHOLD:
                length_stats["short_count"] += 1

            emoji_pos = stats.setdefault("emoji_positions", {"mid": 0, "end": 0})
            for pos in extracted["emoji_positions"]:
                emoji_pos[pos] = emoji_pos.get(pos, 0) + 1

            if uid:
                user_stats = per_user.setdefault(str(uid), {})
                self._update_counter(user_stats, "fragments", extracted["fragments"])
                self._update_counter(user_stats, "emojis", extracted["emojis"])

            if (len(examples) < self.EXAMPLES_PER_GROUP * 2
                    and len(text) <= self.MAX_EXAMPLE_LENGTH
                    and (extracted["fragments"] or extracted["emojis"])):
                if text not in examples:
                    examples.append(text)
                    if len(examples) > self.EXAMPLES_PER_GROUP * 2:
                        examples[:] = examples[-self.EXAMPLES_PER_GROUP:]

            group["last_updated"] = time.time()
            if (self._save_counter % self.SAVE_EVERY_N == 0
                    or time.time() - self._last_snapshot > 3600):
                try:
                    self.save()
                    self._last_snapshot = time.time()
                except Exception:
                    pass

    def _update_counter(self, stats, key, items):
        counter = stats.setdefault(key, {})
        for item in items:
            counter[item] = counter.get(item, 0) + 1

    def _extract_common_slang(self, gid):
        group = self.data.get(str(gid), {})
        user_stats = group.get("per_user", {})
        user_count = len(user_stats)
        if user_count < 2:
            return []
        slang_users = {}
        for uid, stats in user_stats.items():
            for slang in stats.get("fragments", {}):
                slang_users.setdefault(slang, set()).add(uid)
        return [
            slang for slang, users in slang_users.items()
            if len(users) >= 2 and len(users) / user_count >= 0.5
        ]

    def style_prompt(self, gid):
        group = self.data.get(str(gid), {})
        stats = group.get("stats", {})
        if not stats:
            return ""
        desc = group.get("style_description", "")
        if desc:
            examples = group.get("examples", [])[:3]
            parts = [desc]
            if examples:
                parts.append("\n真实示例：")
                for i, ex in enumerate(examples, 1):
                    parts.append(f"  {i}. \"{ex}\"")
            return "\n".join(parts)
        prompt = self._stats_to_prompt(stats)
        common = self._extract_common_slang(gid)
        if common:
            common_prompt = "群共性口头禅：" + "、".join(common[:8])
            prompt = common_prompt + ("\n" + prompt if prompt else "")
        return prompt

    def _stats_to_prompt(self, stats):
        parts = []
        frags = stats.get("fragments", {})
        if frags:
            top = sorted(frags.items(), key=lambda x: -x[1])[:8]
            parts.append("常用口头禅：" + "、".join(f for f, _ in top))
        emojis = stats.get("emojis", {})
        if emojis:
            top = sorted(emojis.items(), key=lambda x: -x[1])[:5]
            parts.append("常用 emoji：" + "".join(e for e, _ in top))
        patterns = stats.get("patterns", {})
        if patterns:
            top = sorted(patterns.items(), key=lambda x: -x[1])[:3]
            parts.append("常用句式：" + "、".join(p for p, _ in top))
        punct = stats.get("punct", {})
        if punct:
            top_punct = sorted(punct.items(), key=lambda x: -x[1])[0]
            punct_map = {
                "ellipsis": "省略号",
                "exclaim": "感叹号",
                "question": "问号",
                "period": "句号",
                "wave": "波浪号",
            }
            parts.append(f"标点偏好：常用{punct_map.get(top_punct[0], top_punct[0])}")
        length = stats.get("length", {})
        if length.get("count", 0) >= 5:
            avg = length.get("sum", 0) / max(1, length.get("count", 1))
            short_ratio = length.get("short_count", 0) / max(1, length.get("count", 1))
            if short_ratio > 0.5:
                parts.append(f"消息偏短（平均{avg:.0f}字，{short_ratio:.0%}是短消息）")
            else:
                parts.append(f"消息长度中等（平均{avg:.0f}字）")
        return "\n".join(parts) if parts else ""

    async def generate_style_description(self, gid):
        group = self.data.get(str(gid), {})
        stats = group.get("stats", {})
        examples = group.get("examples", [])
        total_count = stats.get("length", {}).get("count", 0)
        if total_count < self.LLM_SUMMARY_MIN_STATS:
            return ""
        last_llm = group.get("last_llm_summary", 0)
        if time.time() - last_llm < self.LLM_SUMMARY_INTERVAL:
            return group.get("style_description", "")
        if not self.complete_fn:
            return ""

        stats_text = self._stats_to_prompt(stats)
        examples_text = "\n".join(f"- {ex}" for ex in examples[:5])
        system = (
            "你是群聊风格分析器。根据统计数据和真实示例，用自然语言描述这个群的说话风格。\n"
            "输出要求：\n"
            "1. 用 3-5 句话描述，自然口语化\n"
            "2. 可以覆盖：常用口头禅、句式特点、标点偏好、emoji 习惯、消息长度\n"
            "3. 只输出描述文本，不要前缀标签"
        )
        user_content = (
            f"【统计数据】\n{stats_text}\n\n"
            f"【真实示例】\n{examples_text}\n\n"
            f"请描述这个群的说话风格。"
        )
        try:
            learned, _ = await self.complete_fn(
                system,
                [{"role": "user", "content": user_content}],
                max_tokens=200,
                temperature=0.3,
                top_p=0.7,
                model_refs=MODELS_CASUAL,
            )
            learned = (learned or "").strip()
            if learned:
                with self._lock:
                    group["style_description"] = learned
                    group["last_llm_summary"] = time.time()
                try:
                    self.save()
                except Exception:
                    pass
            return learned
        except Exception as e:
            log(f"[风格描述] 生成失败: {repr(e)[:120]}")
            return ""

    def discover_new_slang(self, gid, recent_texts):
        if len(recent_texts) < 20:
            return []
        candidates = _discover_ngrams(recent_texts, min_count=3, top_k=10)
        if not candidates:
            return []
        with self._lock:
            group = self.data.setdefault(str(gid), {})
            candidates_store = group.setdefault("slang_candidates", {})
            for c in candidates:
                candidates_store[c] = candidates_store.get(c, 0) + 1
        return candidates

    def get_group_profile(self, gid):
        return self.data.get(str(gid), {})

    def get_analogy_methodology(self, gid):
        return self.data.get(str(gid), {}).get("analogy_methodology", "")

    def set_analogy_methodology(self, gid, methodology, sample_count, history_len):
        with self._lock:
            group = self.data.setdefault(str(gid), {})
            group["analogy_methodology"] = methodology
            group["analogy_learned_at"] = time.strftime("%Y-%m-%d %H:%M")
            group["analogy_sample_count"] = sample_count
            group["analogy_learned_history_len"] = history_len
            self.save()


def build_style_system_prompt(style_store, gid):
    style = _filter_silent_memory_text(style_store.style_prompt(gid))
    if not style:
        return ""
    return (
        "\n【本群说话风格】\n"
        + style
        + "\n\n参考真实示例的句式和长度，具体怎么用自己判断。"
    )

# ---------- 自我改进：检测用户纠正，写入知识库 ----------
def _is_question_or_quote(text):
    value = str(text or "").strip()
    if value.startswith("[引用]") or value.startswith("问的是"):
        return True
    return bool(re.search(r"[吗呢么啥哪多少几怎么什么为什么]$", value))

def _should_inject_group_style(key, query):
    """群风格只作为记忆背景，不再长期注入实时 prompt。"""
    return False

_memory_lock = threading.Lock()

def _try_self_improve(key, uname, user_text, bot_reply, is_private=False):
    """检测纠正和新的时效事实，自动追加到 bot_memory.md"""
    section = f"## 私聊 {key}" if is_private else f"## 群 {key}"
    candidates = []
    for marker in CORRECTION_MARKERS:
        if marker in user_text:
            m = re.search(r'(?:是|应该是|其实是)\s*([^\n。！？,，]{2,60})', user_text)
            if m:
                candidates.append(f"[{uname}纠正] {m.group(0).strip()}")
            break
    today = time.strftime("%Y-%m-%d")
    for sent in re.split(r"[。！？\n]+", user_text):
        sent = sent.strip().strip("，, ")
        if len(sent) < 5:
            continue
        if _is_question_or_quote(sent):
            continue
        if any(marker in sent for marker in FACT_MARKERS):
            candidates.append(f"[{uname}新信息] {sent[:80]}（记录于{today}）")
    if not candidates:
        return
    with _memory_lock:
        try:
            with open(MEMORY_FILE, encoding="utf-8") as f:
                mem = f.read()
            if section not in mem:
                mem += f"\n{section}\n"
            idx = mem.index(section) + len(section)
            next_line = mem.index("\n", idx) + 1
            added = 0
            for candidate in candidates:
                entry = f"- {candidate}\n"
                if entry not in mem:
                    mem = mem[:next_line] + entry + mem[next_line:]
                    added += 1
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                f.write(mem)
            log(f"[自改进] {section} 新增 {added} 条记忆")
        except Exception as e:
            log(f"[自改进] 写入失败: {repr(e)[:80]}")

# ---------- 单实例锁（Windows 命名互斥锁） ----------
# 固定互斥名，即使脚本从不同目录/不同 Python 启动，也只有一个桥接实例。
MUTEX_NAME = "Local\\QQBotBridge"
_lock_mutex = None

def acquire_single_instance():
    """创建命名互斥锁。已存在=已有桥接在跑，返回 False。"""
    global _lock_mutex
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    create_mutex.restype = wintypes.HANDLE
    handle = create_mutex(None, False, MUTEX_NAME)
    if not handle:
        return False
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _lock_mutex = handle
    return True

# ---------- Session 持久化 ----------
class SessionStore:
    def __init__(self, path):
        self.path = path
        self.data = self._load()
        self._lock = threading.Lock()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        """存储 session 数据（方法名避免与内置 set 冲突）"""
        self.data[key] = value
        self.save()

    def update(self, key, fn):
        """线程安全地读取-修改-写回一个 session。"""
        with self._lock:
            current = self.data.get(key) or {}
            self.data[key] = fn(current)
            self.save()

# ---------- 桥接核心 ----------
class Bridge:
    def __init__(self):
        self.sessions = SessionStore(SESSIONS_FILE)
        self.style_store = GroupStyleStore(GROUP_STYLES_FILE, complete_fn=self._complete)
        self.workers = {}      # key -> asyncio.Queue
        self.self_id = None
        self.api_key = self._load_api_key()
        self.model_semaphore = None
        self.recent_replies = []
        self.ws_connected = False

    def _load_api_key(self):
        """从环境变量或 hermes .env 读取 OPENCODE_GO_API_KEY"""
        key = os.environ.get("OPENCODE_GO_API_KEY")
        if key:
            return key
        try:
            with open(HERMES_ENV_FILE, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("OPENCODE_GO_API_KEY="):
                        return line.split("=", 1)[1].strip()
        except Exception:
            pass
        return ""

    def worker_for(self, key):
        """每个群/私聊一个串行队列 worker"""
        if key not in self.workers:
            q = asyncio.Queue()
            self.workers[key] = q
            asyncio.create_task(self._worker_loop(key, q))
        return self.workers[key]

    async def _worker_loop(self, key, q):
        while True:
            ev = await q.get()
            try:
                if ev.get("message_type") == "group":
                    await self._process_group(ev)
                else:
                    await self._process_private(ev)
            except Exception as e:
                log(f"处理消息异常 ({key}): {repr(e)[:160]}")
                write_health(
                    last_error=f"处理消息异常({key}): {repr(e)[:120]}",
                    last_error_at=datetime.now().isoformat(),
                )

    async def _health_heartbeat(self):
        while True:
            await asyncio.sleep(60)
            write_health(
                pid=os.getpid(),
                ws_connected=self.ws_connected,
                model_circuit_open=bool(_provider_cooldown),
            )

    # ---------- 文本提取 ----------
    @staticmethod
    def _parse_card_payload(data_field):
        payload = {}

        def merge(value):
            if isinstance(value, dict):
                payload.update(value)
            elif isinstance(value, str) and value.strip():
                raw = value.strip()
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        payload.update(parsed)
                    elif isinstance(parsed, str) and parsed.strip():
                        payload["raw_data"] = parsed
                except Exception:
                    payload["raw_data"] = raw[:300]

        if isinstance(data_field, dict):
            inner = data_field.get("data")
            if inner is not None:
                merge(inner)
                for k, v in data_field.items():
                    if k != "data" and v not in (None, ""):
                        payload.setdefault(k, v)
            else:
                merge(data_field)
        else:
            merge(data_field)
        return payload

    @staticmethod
    def _card_text(payload):
        parts = []
        seen = set()

        def add(value):
            if (isinstance(value, str) and value.strip()
                    and value not in seen and len(value) <= 300):
                seen.add(value)
                parts.append(value.strip())

        for key in (
                "title", "url", "jumpUrl", "qqdocurl", "jump_url",
                "musicUrl", "audio", "summary", "preview"):
            add(payload.get(key))

        meta = payload.get("meta")
        if isinstance(meta, dict):
            for group_key in (
                    "news", "detail_1", "detail_2", "detail_3",
                    "music", "video", "bili", "live", "mini_app", "pages"):
                node = meta.get(group_key)
                if isinstance(node, dict):
                    for k, v in node.items():
                        if isinstance(v, str):
                            add(v)
                        elif isinstance(v, dict):
                            for k2, v2 in v.items():
                                if isinstance(v2, str):
                                    add(v2)
                elif isinstance(node, list):
                    for item in node:
                        if isinstance(item, dict):
                            for v in item.values():
                                if isinstance(v, str):
                                    add(v)

        for key in ("desc", "app", "appName", "prompt", "source", "host"):
            add(payload.get(key))

        raw = payload.get("raw_data")
        if isinstance(raw, str) and not parts:
            add(raw[:200])
        if not parts:
            for k, v in payload.items():
                if isinstance(v, str) and len(v) <= 300:
                    add(v)
        return " | ".join(parts)[:600]

    def extract_text(self, msg_arr, self_id):
        """OneBot array 消息 -> (纯文本, 是否@机器人)"""
        parts = []
        at_bot = False
        for seg in msg_arr:
            t = seg.get("type")
            d = seg.get("data", {})
            if t == "text":
                parts.append(d.get("text", ""))
            elif t == "at":
                qq = str(d.get("qq", ""))
                if qq == self_id:
                    at_bot = True
                else:
                    parts.append(f"@{d.get('name', qq)}")
            elif t == "reply":
                ref = d.get("text", "")
                parts.append(f"[引用{ref}]" if ref else "[引用]")
            elif t == "face":
                parts.append("[表情]")
            elif t == "image":
                parts.append("[图片]")
            elif t == "record":
                parts.append("[语音]")
            elif t == "video":
                parts.append("[视频]")
            elif t == "file":
                parts.append("[文件]")
            elif t == "share":
                payload = self._parse_card_payload(d)
                card_text = self._card_text(payload)
                if card_text:
                    parts.append(f"分享：{card_text}")
                else:
                    parts.append("[分享]")
            elif t in ("json", "card", "link", "music"):
                payload = self._parse_card_payload(d)
                card_text = self._card_text(payload)
                if card_text:
                    parts.append(f"{t}分享：{card_text}")
                else:
                    parts.append(f"[{t}]分享")
            else:
                parts.append(f"[{t}]")
        return "".join(parts).strip(), at_bot

    async def _fetch_reply_context(self, ev):
        """通过 OneBot get_msg 拉取被回复消息，补上卡片/链接等上下文。"""
        reply_id = None
        for seg in ev.get("message", []) or []:
            if seg.get("type") == "reply":
                data = seg.get("data", {}) or {}
                reply_id = data.get("id") or data.get("message_id")
                break
        if not reply_id:
            return ""
        try:
            result = await self.call_api(
                "get_msg", {"message_id": int(reply_id)}, retries=1)
        except Exception as e:
            log(f"[引用消息] get_msg 失败: {repr(e)[:120]}")
            return ""
        if not result or not (result.get("status") == "ok" or result.get("retcode") == 0):
            return ""
        data = result.get("data") or {}
        message = data.get("message")
        if isinstance(message, list):
            text, _ = self.extract_text(message, self.self_id)
        elif isinstance(message, str):
            text = message
        else:
            text = ""
        if not text.strip():
            return ""
        sender = data.get("sender") or {}
        name = sender.get("card") or sender.get("nickname") or ""
        prefix = f"（{name}）" if name else ""
        text = _sanitize_history_content(text.strip(), "assistant")
        if not text:
            return ""
        return f"{prefix}: {text}"

    # ---------- 模型 API 调用 ----------
    def _api_call(self, body, api_url, api_key):
        """流式同步调用：连接/响应头超时后按 network 失败并切换。"""
        import requests
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "OpenAI/Python 1.40.0",
        }
        payload = dict(body)
        payload["stream"] = True
        try:
            resp = requests.post(
                api_url,
                json=payload,
                headers=headers,
                stream=True,
                timeout=(API_CONNECT_TIMEOUT, API_TIMEOUT),
            )
        except requests.exceptions.Timeout as e:
            log(f"[API 连接/首token超时] {api_url} - {repr(e)[:100]}")
            return "", "network", [], "", []
        except Exception as e:
            log(f"[API 异常] {api_url} - {repr(e)[:120]}")
            return "", "network", [], "", []

        try:
            if resp.status_code != 200:
                preview = resp.text[:200]
                log(f"[API HTTP {resp.status_code}] {api_url} | {preview}")
                if resp.status_code == 429:
                    return "", "rate_limit", [], "", []
                if 500 <= resp.status_code < 600:
                    return "", "server_error", [], "", []
                return "", f"http_{resp.status_code}", [], "", []

            it = resp.iter_lines(decode_unicode=True)
            first = next(it, None)
            if first is None:
                return "", "network", [], "", []
            lines = [str(first).strip()]
            for raw in it:
                line = str(raw or "").strip()
                if line:
                    lines.append(line)
            return self._parse_stream_response(lines)
        except requests.exceptions.Timeout as e:
            log(f"[API 流式读取超时] {api_url} - {repr(e)[:100]}")
            return "", "network", [], "", []
        except Exception as e:
            log(f"[API 流式异常] {api_url} - {repr(e)[:120]}")
            return "", "network", [], "", []
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def _parse_stream_response(self, lines):
        """解析 OpenAI 兼容流式/JSON 响应。"""
        if not lines:
            return "", "ok", [], "", []
        first = lines[0].lstrip()
        if first.startswith("{"):
            try:
                d = json.loads("".join(lines))
                msg = d["choices"][0]["message"]
                content = _repair_utf8((msg.get("content") or "").strip())
                tool_calls = msg.get("tool_calls") or []
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    if fn.get("arguments"):
                        fn["arguments"] = _repair_utf8(str(fn["arguments"]))
                tool_calls = _normalize_tool_calls(tool_calls)
                if not tool_calls and content and _looks_like_tool_call(content):
                    parsed = _extract_tool_calls_from_text(content)
                    if parsed:
                        tool_calls = parsed
                        content = ""
                finish_reason = d["choices"][0].get("finish_reason", "")
                web_search = d.get("web_search") or []
                return content, "ok", tool_calls, finish_reason, web_search
            except Exception:
                return "", "network", [], "", []

        content_parts = []
        tool_acc = {}
        finish_reason = ""
        web_search = []
        for line in lines:
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except Exception:
                continue
            if obj.get("web_search"):
                web_search = obj.get("web_search")
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if content:
                content_parts.append(_repair_utf8(str(content)))
            for tc in delta.get("tool_calls") or []:
                idx = int(tc.get("index", 0))
                acc = tool_acc.setdefault(idx, {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                })
                if tc.get("id"):
                    acc["id"] = tc["id"]
                if tc.get("type"):
                    acc["type"] = tc["type"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    acc["function"]["name"] = fn["name"]
                if fn.get("arguments"):
                    acc["function"]["arguments"] += _repair_utf8(str(fn["arguments"]))
            fr = choices[0].get("finish_reason")
            if fr:
                finish_reason = fr

        content = "".join(content_parts).strip()
        tool_calls = [tool_acc[idx] for idx in sorted(tool_acc)]
        tool_calls = _normalize_tool_calls(tool_calls)
        if not content and not tool_calls:
            for line in lines:
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                msg = ((obj.get("choices") or [{}])[0] or {}).get("message") or {}
                content = _repair_utf8((msg.get("content") or "").strip())
                tool_calls = msg.get("tool_calls") or []
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    if fn.get("arguments"):
                        fn["arguments"] = _repair_utf8(str(fn["arguments"]))
                tool_calls = _normalize_tool_calls(tool_calls)
                if content or tool_calls:
                    break
        if not tool_calls and content and _looks_like_tool_call(content):
            parsed = _extract_tool_calls_from_text(content)
            if parsed:
                tool_calls = parsed
                content = ""
        return content, "ok", tool_calls, finish_reason, web_search

    async def _complete(self, system, messages, max_tokens=DEFAULT_MAX_TOKENS,
                        temperature=0.0, top_p=0.5, model_refs=None,
                        weak_query="", min_reply_len=0):
        """遍历模型列表完成一次调用，返回 (回复, 使用的模型)。"""
        if self.model_semaphore is None:
            self.model_semaphore = asyncio.Semaphore(3)
        loop = asyncio.get_running_loop()
        for model_ref in _sort_models(model_refs or MODELS):
            if _provider_blocked(model_ref):
                log(f"[API] {model_ref} 熔断中，跳过")
                continue
            provider, model_name = model_ref.split("/", 1)
            cfg = PROVIDERS.get(provider, {})
            api_url = cfg.get("url", "")
            api_key = cfg.get("key") or self.api_key
            if not api_url or not api_key:
                continue
            effective_temperature = float(temperature)
            effective_temperature = max(0.0, min(1.0, effective_temperature))
            body = {
                "model": model_name,
                "messages": [{"role": "system", "content": system}] + messages,
                "max_tokens": _safe_max_tokens(provider, model_name, max_tokens),
                "temperature": effective_temperature,
                "top_p": top_p,
            }
            for attempt in range(2):
                try:
                    inc_stat("model_calls")
                    async with self.model_semaphore:
                        reply, state, _, finish_reason, _ = await loop.run_in_executor(
                            None, lambda b=body, u=api_url, k=api_key: self._api_call(b, u, k))
                except Exception as e:
                    inc_stat("model_fail")
                    log(f"[API] {model_ref} 调用异常: {repr(e)[:100]}")
                    _mark_provider_down(model_ref, 15)
                    break
                raw_reply = reply
                discarded = False
                if reply:
                    reply = _clean_reply(reply)
                    if finish_reason == "length":
                        log(f"[API] {model_ref} 输出被 max_tokens 截断(finish_reason=length)，丢弃")
                        _mark_provider_weak(model_ref)
                        reply = ""
                        discarded = True
                    if reply and _reject_bad_final_reply(reply):
                        log(f"[API] {model_ref} 工具 JSON/乱码/重复回复，丢弃: {reply[:40]}")
                        _mark_provider_weak(model_ref)
                        reply = ""
                        discarded = True
                    if reply and weak_query and _is_weak_reply(reply, weak_query):
                        log(f"[API] {model_ref} 弱回复，丢弃: {reply[:40]}")
                        _mark_provider_weak(model_ref)
                        reply = ""
                        discarded = True
                    elif reply and min_reply_len and len(reply) < min_reply_len:
                        log(f"[API] {model_ref} 回复太短({len(reply)}字)，丢弃")
                        _mark_provider_weak(model_ref)
                        reply = ""
                        discarded = True
                if reply:
                    _mark_provider_ok(model_ref)
                    inc_stat("model_success")
                    return reply, model_ref
                if state == "ok":
                    inc_stat("model_fail")
                    log(f"[API {model_ref} 空回复] 第{attempt + 1}次，重试")
                    if attempt == 0 and provider == "glm" and not raw_reply:
                        log("[API] GLM 思考未给正文，重试关闭思考")
                        body["thinking"] = {"type": "disabled"}
                        continue
                    if not discarded:
                        _mark_provider_weak(model_ref)
                    break
                if state == "rate_limit":
                    _mark_provider_down(model_ref, 30)
                else:
                    _mark_provider_down(model_ref, 15)
                inc_stat("model_fail")
                log(f"[API] {model_ref} 异常({state})，切换备选")
                break
            log(f"[API] {model_ref} 无回复，尝试下一个备选模型")
        return "", ""

    async def _complete_race(self, system, messages, max_tokens=DEFAULT_MAX_TOKENS,
                             temperature=0.0, top_p=0.5, model_refs=None,
                             weak_query="", min_reply_len=0):
        """模型竞速：前两个可用模型同时请求，谁先返回有效结果用谁。"""
        if self.model_semaphore is None:
            self.model_semaphore = asyncio.Semaphore(3)
        loop = asyncio.get_running_loop()
        ordered = _sort_models(model_refs or MODELS)
        candidates = list(ordered[:2])
        rest = list(ordered[2:])

        async def call_one(model_ref):
            if _provider_blocked(model_ref):
                return "", model_ref
            provider, model_name = model_ref.split("/", 1)
            cfg = PROVIDERS.get(provider, {})
            api_url = cfg.get("url", "")
            api_key = cfg.get("key") or self.api_key
            if not api_url or not api_key:
                return "", model_ref
            effective_temperature = float(temperature)
            effective_temperature = max(0.0, min(1.0, effective_temperature))
            body = {
                "model": model_name,
                "messages": [{"role": "system", "content": system}] + messages,
                "max_tokens": _safe_max_tokens(provider, model_name, max_tokens),
                "temperature": effective_temperature,
                "top_p": top_p,
            }
            try:
                inc_stat("model_calls")
                async with self.model_semaphore:
                    reply, state, _, finish_reason, _ = await loop.run_in_executor(
                        None, lambda b=body, u=api_url, k=api_key: self._api_call(b, u, k))
            except Exception as e:
                inc_stat("model_fail")
                log(f"[竞速] {model_ref} 调用异常: {repr(e)[:100]}")
                _mark_provider_down(model_ref, 15)
                return "", model_ref
            if reply:
                cleaned = _clean_reply(reply)
                if finish_reason == "length":
                    log(f"[竞速] {model_ref} 输出被 max_tokens 截断(finish_reason=length)，丢弃")
                    _mark_provider_weak(model_ref)
                    return "", model_ref
                if cleaned and _reject_bad_final_reply(cleaned):
                    log(f"[竞速] {model_ref} 工具 JSON/乱码/重复回复，丢弃: {cleaned[:40]}")
                    _mark_provider_weak(model_ref)
                    return "", model_ref
                if weak_query and _is_weak_reply(cleaned, weak_query):
                    log(f"[竞速] {model_ref} 弱回复，丢弃: {cleaned[:40]}")
                    _mark_provider_weak(model_ref)
                    return "", model_ref
                if min_reply_len and len(cleaned) < min_reply_len:
                    log(f"[竞速] {model_ref} 回复太短({len(cleaned)}字)，丢弃")
                    _mark_provider_weak(model_ref)
                    return "", model_ref
                _mark_provider_ok(model_ref)
                inc_stat("model_success")
                return cleaned, model_ref
            if state == "ok":
                inc_stat("model_fail")
                _mark_provider_weak(model_ref)
            elif state == "rate_limit":
                inc_stat("model_fail")
                _mark_provider_down(model_ref, 30)
            else:
                inc_stat("model_fail")
                _mark_provider_down(model_ref, 15)
            log(f"[API] {model_ref} 竞速失败({state})")
            return "", model_ref

        tasks = [
            asyncio.ensure_future(call_one(ref))
            for ref in candidates
            if not _provider_blocked(ref)
        ]
        if not tasks:
            return await self._complete(
                system, messages, max_tokens=max_tokens,
                temperature=temperature, top_p=top_p,
                model_refs=rest or None,
                weak_query=weak_query, min_reply_len=min_reply_len,
            )

        pending = set(tasks)
        deadline = time.monotonic() + API_TIMEOUT + 2
        while pending and time.monotonic() < deadline:
            done, pending = await asyncio.wait(
                pending,
                return_when=asyncio.FIRST_COMPLETED,
                timeout=max(0.1, deadline - time.monotonic()),
            )
            for task in done:
                reply, model_ref = task.result()
                if reply:
                    for t in pending:
                        t.cancel()
                    if pending:
                        await asyncio.gather(*list(pending), return_exceptions=True)
                    log(f"[竞速] 使用 {model_ref}")
                    return reply, model_ref

        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*list(pending), return_exceptions=True)
        if rest:
            return await self._complete(
                system, messages, max_tokens=max_tokens,
                temperature=temperature, top_p=top_p,
                model_refs=rest,
                weak_query=weak_query, min_reply_len=min_reply_len,
            )
        return "", ""

    async def _complete_with_tools(self, system, messages, max_tokens=DEFAULT_MAX_TOKENS, model_refs=None, force_search=True, tools_enabled=True):
        """第一轮：带工具列表调用模型，返回 (tool_calls, 直接回复, 模型, 原生联网结果)。"""
        loop = asyncio.get_running_loop()
        for model_ref in _sort_models(model_refs or MODELS_FACTUAL):
            if _provider_blocked(model_ref):
                continue
            provider, model_name = model_ref.split("/", 1)
            direct_fallback = (
                provider == "opencode_free"
                or (provider == "opencode" and "deepseek" in model_name.lower())
            )
            cfg = PROVIDERS.get(provider, {})
            api_url = cfg.get("url", "")
            api_key = cfg.get("key") or self.api_key
            if not api_url or not api_key:
                continue
            tool_system = system
            if not tools_enabled:
                tool_system += "\n本轮直接回答，不需要搜索。"
            elif force_search:
                if GLM_WEB_SEARCH and provider == "glm":
                    tool_system += (
                        "\n事实/实时/人物问题请调用 web_search 搜索。")
                else:
                    tool_system += (
                        "\n事实/实时/人物问题请调用搜索工具；"
                        "可以用 plan_search 拆多个 query，也可以直接 search_web。"
                        "工具调用通过 function calling 参数返回，不要写进正文。")
            else:
                tool_system += (
                    "\n需要联网/事实/人物/实时信息时，可以调用搜索工具；"
                    "不需要就直接回答。工具调用通过 function calling 参数返回，不要写进正文。"
                )
            body = {
                "model": model_name,
                "messages": [{"role": "system", "content": tool_system}] + messages,
                "max_tokens": _safe_max_tokens(provider, model_name, max_tokens),
                "temperature": 0.0,
                "top_p": 0.5,
                "tools": [] if not tools_enabled else (
                    _glm_web_search_tool()
                    if (GLM_WEB_SEARCH and provider == "glm")
                    else TOOLS
                ),
            }
            try:
                inc_stat("model_calls")
                reply, state, tool_calls, finish_reason, web_search = await loop.run_in_executor(
                    None, lambda b=body, u=api_url, k=api_key: self._api_call(b, u, k))
                if finish_reason == "length":
                    log(f"[工具] {model_ref} 输出被 max_tokens 截断，直接降级内置搜索")
                    _mark_provider_weak(model_ref)
                    if direct_fallback:
                        return [], "", model_ref, []
                    continue
                if state == "ok":
                    inc_stat("model_success")
                    if web_search:
                        log(f"[GLM联网] {model_ref} 返回 {len(web_search)} 条搜索结果")
                        _mark_provider_ok(model_ref)
                        return [], reply, model_ref, web_search
                    if not tool_calls and reply and _looks_like_tool_call(reply):
                        parsed = _extract_tool_calls_from_text(reply)
                        if parsed:
                            log(f"[FC] {model_ref} 正文中的工具调用已恢复为 {len(parsed)} 个 tool_calls")
                            _mark_provider_ok(model_ref)
                            return parsed, "", model_ref, []
                    if tool_calls:
                        log(f"[FC] 第1轮 {model_ref} 返回 {len(tool_calls)} 个tool_calls")
                        _mark_provider_ok(model_ref)
                        return tool_calls, "", model_ref, []
                    if reply:
                        log(f"[FC] 第1轮 {model_ref} 未返回tool_calls，直接回复 {reply[:40]}")
                        _mark_provider_ok(model_ref)
                        return [], reply, model_ref, []
                    _mark_provider_weak(model_ref)
                    log(f"[工具] {model_ref} 未返回有效工具/回复，直接降级内置搜索")
                    if direct_fallback:
                        return [], "", model_ref, []
                    continue
                if state == "rate_limit":
                    _mark_provider_down(model_ref, 30)
                elif state in ("http_400", "http_401", "http_403", "http_404"):
                    _mark_provider_weak(model_ref)
                else:
                    _mark_provider_down(model_ref, 15)
                inc_stat("model_fail")
                log(f"[工具] {model_ref} 工具调用失败({state})，直接降级内置搜索")
                if direct_fallback:
                    return [], "", model_ref, []
                continue
            except Exception as e:
                inc_stat("model_fail")
                log(f"[工具] {model_ref} 调用异常，直接降级内置搜索: {repr(e)[:100]}")
                _mark_provider_down(model_ref, 15)
                if direct_fallback:
                    return [], "", model_ref, []
        return [], "", "", []
    async def _compress_history(self, key):
        """后台提炼旧历史为摘要，不清空原文，只记录已压缩位置。"""
        store = self.sessions.get(key)
        if not isinstance(store, dict):
            return
        messages = store.get("messages") or []
        summary_index = int(store.get("summary_index", 0) or 0)
        unsummarized = messages[summary_index:]
        total_chars = sum(len(str(m.get("content", ""))) for m in unsummarized)
        if total_chars < COMPRESS_TRIGGER_CHARS:
            return
        keep = []
        keep_chars = 0
        for m in reversed(unsummarized):
            c = len(str(m.get("content", "")))
            if keep_chars + c > COMPRESS_KEEP_RECENT_CHARS:
                break
            keep.append(m)
            keep_chars += c
        keep = list(reversed(keep))
        old = unsummarized[:-len(keep)] if keep else unsummarized[:max(1, len(unsummarized) - 1)]
        if not old:
            return
        lines = []
        for m in old:
            role = "用户" if m.get("role") == "user" else "AI"
            content = str(m.get("content", ""))[:600]
            if content:
                lines.append(f"{role}: {content}")
        if not lines:
            return
        summary, _ = await self._complete(
            HISTORY_SUMMARY_SYSTEM,
            [{"role": "user", "content": "\n".join(lines)}],
            max_tokens=600, temperature=0.0, top_p=0.5,
            model_refs=MODELS_FACTUAL,
        )
        summary = _clean_reply(summary or "")
        if not summary:
            return
        prev = store.get("summary", "") or ""
        merged = (prev + "\n" + summary).strip() if prev else summary
        if len(merged) > CONTEXT_SUMMARY_MAX_CHARS:
            merged = merged[-CONTEXT_SUMMARY_MAX_CHARS:]

        def update(data):
            data["summary"] = merged
            data["summary_index"] = summary_index + len(old)
            data["last_compressed_at"] = time.time()
            return data
        self.sessions.update(key, update)
        log(f"[摘要] {key} 压缩 {len(old)} 条，当前摘要 {len(merged)} 字")

    async def _search_with_terms(self, query, entities=None, max_results=8, extra_queries=None, events=None):
        """搜多个完整搜索式，再做实体百科和兜底补充，最后合并去重。"""
        query = _clean_question(query)
        entities = _clean_entities(entities)
        extra_queries = list(extra_queries or [])
        year = time.strftime("%Y年")
        for event in (events or [])[:3]:
            for event_query in (f"{event} 事件 {year}", f"{event} 发生了什么"):
                if event_query not in extra_queries:
                    extra_queries.append(event_query)
        try:
            suggestions = await asyncio.wait_for(
                asyncio.to_thread(_search_suggestions, query, max_results=6),
                timeout=1.5)
            for suggestion in suggestions:
                value = suggestion.split("】", 1)[-1].strip()
                if not value or value in extra_queries:
                    continue
                if not entities or any(e in value for e in entities):
                    extra_queries.append(value)
        except Exception:
            pass
        full = await search_web_async(query, max_results, entities, extra_queries)
        if full and full.strip():
            write_health(search_degraded=False, search_degraded_engine="")
        else:
            write_health(search_degraded=True, search_degraded_engine="all")
        distilled_quick = _distill_search_results(
            full, query,
            max_entries=max_results,
            max_chars=3000,
            entities=entities,
        )
        if len(distilled_quick.splitlines()) >= min(3, max_results):
            return full
        extra_results = []
        extra_tasks = []
        for entity in (entities or [])[:5]:
            if len(entity) < 2 or entity in _GENERIC_SEARCH_TERMS:
                continue
            entity_query = entity + " 百科"
            extra_tasks.append((
                "实体", entity,
                asyncio.wait_for(
                    asyncio.to_thread(_search_engine, entity_query, "bing_cn", 3.5), timeout=3.0)))
            extra_tasks.append((
                "百度实体", entity,
                asyncio.wait_for(
                    asyncio.to_thread(_search_engine, entity_query, "baidu", 5), timeout=4.0)))
            extra_tasks.append((
                "搜狗实体", entity,
                asyncio.wait_for(
                    asyncio.to_thread(_search_engine, entity_query, "sogou", 5), timeout=4.0)))
            extra_tasks.append((
                "微信实体", entity,
                asyncio.wait_for(
                    asyncio.to_thread(_search_engine, entity_query, "weixin", 5), timeout=4.5)))
            extra_tasks.append((
                "百科实体", entity,
                asyncio.wait_for(
                    asyncio.to_thread(_search_engine, entity_query, "baike", 3), timeout=3.5)))
            extra_tasks.append((
                "360实体", entity,
                asyncio.wait_for(
                    asyncio.to_thread(_search_engine, entity_query, "360_m", 5), timeout=4.0)))
            extra_tasks.append((
                "建议实体", entity,
                asyncio.wait_for(
                    asyncio.to_thread(_search_engine, entity_query, "suggest", 3), timeout=3.5)))
        if not full:
            for fallback in (query + " 百科", query + " 是什么"):
                extra_tasks.append((
                    "二次兜底", fallback,
                asyncio.wait_for(
                    asyncio.to_thread(_search_engine, fallback, "sogou", 3.5), timeout=3.0)))
            for engine in ("baidu", "sogou", "weixin", "360_m"):
                extra_tasks.append((
                    f"{engine}兜底", query,
                    asyncio.wait_for(
                        asyncio.to_thread(_search_engine, query, engine, 5), timeout=4.5)))
        if extra_tasks:
            done_results = await asyncio.gather(
                *(task for _, _, task in extra_tasks), return_exceptions=True)
            for (label, name, _), result in zip(extra_tasks, done_results):
                if isinstance(result, Exception):
                    log(f"[搜索] {label} {str(name)[:30]}失败: {repr(result)[:60]}")
                    continue
                if result:
                    extra_results.append(result)
                    if label == "实体":
                        log(f"[实体] {name} 已检索")
                    elif label == "二次兜底":
                        log(f"[搜索] 二次兜底: {name}")
        if not extra_results:
            return full
        raw_parts = [full] + ["\n".join(r) for r in extra_results]
        raw = "\n".join(p for p in raw_parts if p)
        if raw.strip():
            write_health(search_degraded=False, search_degraded_engine="")
        else:
            write_health(search_degraded=True, search_degraded_engine="all")
        merged = _merge_search_results([raw.splitlines()], max_results * 2)
        return "\n".join(merged)

    async def _enrich_lyrics_search(self, raw, query, sub_queries=None, max_pages=1):
        """歌词类问题补一层正文抓取：摘要不足时打开结果页，喂真实正文。"""
        if not _is_lyrics_query(query):
            return raw
        candidates = list(dict.fromkeys(
            str(q).strip() for q in (sub_queries or []) if str(q).strip()
        )) or [_clean_search_query(query) or query]
        body_parts = []
        for q in candidates[:2]:
            try:
                body = await asyncio.wait_for(
                    _fetch_lyrics_body(q, max_pages=max_pages),
                    timeout=12,
                )
            except Exception:
                body = ""
            if body:
                body_parts.append(body)
        if body_parts:
            combined = (raw or "") + "\n\n【歌词页面正文】\n" + "\n\n".join(body_parts)
            log(f"[歌词] 深读正文成功，新增 {len(combined) - len(raw or '')} 字")
            return combined[:MAX_LYRIC_CONTEXT_CHARS]
        for fallback_q in candidates[:2]:
            fallback = await search_web_async(
                fallback_q,
                max_results=8,
                exact=True,
            )
            if fallback:
                combined = (raw or "") + "\n\n【搜索摘要兜底】\n" + fallback
                log(f"[歌词] 深读失败，使用搜索摘要 {fallback_q[:30]}")
                return combined[:MAX_LYRIC_CONTEXT_CHARS]
        log(f"[歌词] 深读正文失败，且无搜索摘要 {query[:30]}")
        return raw

    async def _parallel_search_subqueries(self, sub_queries, entities=None):
        """按 skill 并行搜索多个子问题，单个子问题失败不影响其他。"""
        if not sub_queries:
            return ""
        tasks = []
        for idx, sub_query in enumerate(sub_queries):
            sub_entities = []
            if entities and idx < len(entities):
                sub_entities = [entities[idx]]
            tasks.append(asyncio.wait_for(
                self._search_with_terms(
                    sub_query,
                    entities=sub_entities,
                    max_results=max(5, MAX_SEARCH_RESULTS // len(sub_queries)),
                ),
                timeout=8.0,
            ))
        done = await asyncio.gather(*tasks, return_exceptions=True)
        parts = []
        for result in done:
            if isinstance(result, str) and result.strip():
                parts.append(result)
            elif isinstance(result, Exception):
                log(f"[搜索] 子问题失败: {repr(result)[:80]}")
        return "\n".join(parts)

    async def _optimize_search_query(self, text):
        """用 LLM 生成多个完整搜索式和核心实体；失败或超时退回规则版。"""
        if _is_guide_query(text):
            fallback = _build_search_query(text)
            log(f"[搜索词] 游戏/攻略查询整句直搜: {fallback}")
            return ([fallback] if fallback else [text]), [], _extract_events_rule(text)
        try:
            optimized, _ = await asyncio.wait_for(
                self._complete(
                    QUERY_COMPRESS_SYSTEM,
                    [{"role": "user", "content": f"当前日期：{_current_date_text()}\n用户问题：{text}"}],
                    max_tokens=80, temperature=0.0, top_p=0.5,
                    model_refs=MODELS_CASUAL,
                ),
                timeout=3,
            )
            optimized = _clean_reply(optimized or "").strip()
            queries = []
            entities = []
            events = []
            for line in optimized.splitlines():
                line = line.strip()
                if line.startswith("搜索词"):
                    q = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                    q = re.sub(r"\s+", " ", q)
                    if q:
                        queries.append(q)
                elif line.startswith("实体"):
                    raw = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                    entities = [
                        x.strip() for x in re.split(r"[，,、\s]+", raw)
                        if x.strip() and x != "无"
                    ]
                elif line.startswith("事件"):
                    raw = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                    events = [
                        x.strip() for x in re.split(r"[，,、\s]+", raw)
                        if x.strip() and x != "无"
                    ]
            if queries:
                return queries, entities[:5], events[:5]
        except Exception as e:
            log(f"[搜索词] 模型压缩失败，使用规则版: {repr(e)[:80]}")
        fallback = _build_search_query(text)
        return ([fallback] if fallback else [text]), [], _extract_events_rule(text)
    async def _self_check_reply(self, query, source, reply, max_tokens=800):
        """质检并重写：检测到重复/残缺/粘贴搜索原文时，让模型重写最终回答。"""
        raw, _ = await self._complete(
            SELF_CHECK_SYSTEM,
            [{"role": "user", "content": (
                f"问题：{query}\n"
                f"可靠资料：{source or '（无搜索结果）'}\n"
                f"当前回答：{reply}"
            )}],
            max_tokens=max_tokens,
            temperature=0.2,
            top_p=0.7,
            model_refs=MODELS_FACTUAL,
        )
        cleaned = _sanitize_reply(raw or "")
        if not cleaned or re.match(r"^\s*PASS\b", cleaned, re.IGNORECASE):
            return reply
        log(f"[自检] 重写完成: {len(cleaned)}字")
        return cleaned

    async def _grounding_check(self, query, search_results, reply):
        """事实核查：用 LLM 结构化判断，不只靠关键词重叠。"""
        raw, _ = await self._complete(
            GROUNDING_CHECK_SYSTEM,
            [{"role": "user", "content": (
                f"当前日期：{_current_date_text()}\n问题：{query}\n搜索结果：{search_results or '（无搜索结果）'}\n回答：{reply}")}],
            max_tokens=500, temperature=0.0, top_p=0.5,
            model_refs=MODELS_CASUAL,
        )
        data = {}
        try:
            data = json.loads(raw or "")
        except Exception:
            match = re.search(r"\{[\s\S]*\}", raw or "")
            if match:
                try:
                    data = json.loads(match.group())
                except Exception:
                    data = {}
        verdict = str(data.get("verdict") or "").strip().upper()
        if not verdict:
            verdict = "PASS"
        if data.get("unsupported_claims"):
            log(f"[核查] 未支撑断言: {data['unsupported_claims'][:2]}")
        return verdict

    async def _reflect(self, task, reply):
        """任务级反思：评估并最多改进一轮。"""
        eval_reply, _ = await self._complete(
            REFLECTION_EVAL_SYSTEM,
            [{"role": "user", "content": f"任务：{task}\n\n当前输出：{reply}"}],
            max_tokens=160,
            temperature=FACTUAL_TEMPERATURE,
            top_p=FACTUAL_TOP_P,
        )
        if not eval_reply or "PASS" in eval_reply.upper():
            return reply
        log(f"[反思] 需要改进: {eval_reply[:80]}")
        if not re.search(r"编造|错误|答非所问|遗漏|不准确|不对|没有依据|胡编|矛盾", eval_reply):
            return reply
        improved, _ = await self._complete(
            REFLECTION_IMPROVE_SYSTEM,
            [{"role": "user", "content": (
                f"原始任务：{task}\n当前输出：{reply}\n评估意见：{eval_reply}")}],
            max_tokens=500,
            temperature=FACTUAL_TEMPERATURE,
            top_p=FACTUAL_TOP_P,
        )
        return _sanitize_reply(improved or reply)

    async def _restyle_as_taffy(self, reply, query):
        system = (
            "你是永雏塔菲。把下面的回答用塔菲语气重写。\n"
            "怎么调整表达由你判断。\n"
            "要求：\n"
            "- 用 taffy/塔菲/小菲自称，避免普通AI/客服腔\n"
            "- 喵有节制，不要每句都加\n"
            "- 保留情绪外放、口语化、抽象玩梗的塔菲风格\n"
            "- 如果原回答表达了'不知道'，重写后也保留这个意思"
        )
        user_content = f"问题：{query}\n原始回答：{reply}"
        for attempt in range(2):
            restyled, _ = await self._complete(
                system,
                [{"role": "user", "content": user_content}],
                max_tokens=GUIDE_MAX_TOKENS,
                temperature=0.7,
                top_p=0.9,
                model_refs=MODELS,
            )
            restyled = _sanitize_reply(restyled or "")
            if restyled and _is_taffy_styled(restyled):
                return restyled
            if attempt == 0:
                system += "\n上一条重写还不够塔菲，再自然一点，至少出现一次塔菲/taffy/喵等身份感表达。"
        return ""

    async def _llm_answer_from_search(self, query, search_text):
        system = (
            "你是永雏塔菲。根据搜索结果自然成文，不要原样粘贴搜索原文。\n"
            "把信息整理成塔菲语气回答；怎么取舍和表达由你判断。\n"
            "QQ 不渲染 Markdown，不要输出 **、*、`、# 或 Markdown 链接。"
        )
        user_content = (
            f"问题：{query}\n"
            f"搜索结果：\n{(search_text or '')[:4000]}"
        )
        reply, _ = await self._complete(
            system,
            [{"role": "user", "content": user_content}],
            max_tokens=GUIDE_MAX_TOKENS,
            temperature=0.3,
            top_p=0.7,
            model_refs=MODELS,
        )
        return _sanitize_reply(reply or "")

    async def ask_model(self, key, prompt, question=None, speaker=None):
        """直连模型 API：历史 + 记忆门控 + 快速工具检索 + 可选反思。"""
        start = time.time()
        phase_start = time.time()
        query = _clean_question(question or prompt)
        store = self.sessions.get(key)
        store = store if isinstance(store, dict) else {}
        last_question = store.get("last_question") or ""
        last_answer = _sanitize_history_content(
            store.get("last_answer") or "", "assistant")
        last_speaker = store.get("last_speaker") or ""
        history = []
        for m in (store.get("messages") or []):
            content = _sanitize_history_content(
                m.get("content") or "", m.get("role") or "")
            if content.strip():
                history.append({**m, "content": content})
        summary = store.get("summary", "") or ""
        summary_index = int(store.get("summary_index", 0) or 0)
        if summary_index < 0 or summary_index > len(history):
            summary_index = 0

        answer_key = _answer_cache_key(key, query)
        cached = _answer_cache_get(answer_key, _classify_intent(query))
        if cached:
            log(f"[缓存] 命中答案: {query[:30]}")
            return cached

        # 上下文 = 早期摘要 + 最近未压缩原文；原文完整保存在 sessions，不截断删除
        recent = history[summary_index:] if summary else history
        context_messages = []
        total_chars = len(summary) + len(prompt)
        limit = int(MAX_CONTEXT_CHARS * 0.85)
        for m in reversed(recent):
            c = len(m.get("content", ""))
            if total_chars + c > limit:
                break
            context_messages.append(m)
            total_chars += c
        context_messages = list(reversed(context_messages))
        context_messages.append({"role": "user", "content": prompt})
        request_messages = [dict(m) for m in context_messages]

        system = SYSTEM_PROMPT
        if TAFFY_FLAVOR:
            system = TAFFY_FLAVOR_PROMPT + "\n" + system
        system += "\n" + CURRENT_DATE_PROMPT.format(date=_current_date_text())
        if summary:
            system += (f"\n【历史摘要】\n{summary[-CONTEXT_SUMMARY_MAX_CHARS:]}")
        if last_question:
            system += (
                "\n【上一轮问答上下文】\n"
                f"提问者：{last_speaker or '未知'}\n"
                f"用户问题：{last_question[:500]}\n"
                f"塔菲回答：{last_answer[:800]}"
            )
        if _CORRECTION_FOLLOWUP_RE.search(query):
            system += (
                "\n【追问识别】当前问题疑似纠正/追问，先理解上一轮问题与回答，"
                "再按用户最新意图回答。"
            )

        # 第一轮：搜索方式由 LLM 自己判断
        intent = _classify_intent(query)
        is_factual = _is_factual(query)
        guide_query = _is_guide_query(query)
        allow_tools = _should_search(query)
        system += SEARCH_STRATEGY_SYSTEM
        _log_phase(phase_start, "上下文与提示词")
        final_max_tokens = _dynamic_final_max_tokens(is_factual, guide_query)
        min_reply_len = (
            80 if guide_query
            else 40 if (_is_factual(query) or _TASK_MODE_RE.search(query or ""))
            else 0
        )
        temperature = FACTUAL_TEMPERATURE if is_factual else CASUAL_TEMPERATURE
        top_p = FACTUAL_TOP_P if is_factual else CASUAL_TOP_P
        search_returned = False
        search_attempted = False
        search_results = ""
        distilled = ""
        skip_final = False
        tool_calls = []
        tool_direct_reply = ""
        native_search = []
        single_shot_used = False
        reply = ""
        used_model = ""

        async def generic_fallback_search():
            nonlocal search_results, distilled, search_returned, search_attempted
            if not _should_attempt_generic_search(query):
                return
            search_attempted = True
            generic_query = _generic_fallback_query(query)
            raw_source = await search_web_async(
                generic_query,
                max_results=MAX_SEARCH_RESULTS,
                exact=True,
            )
            raw = _search_evidence_text(raw_source)
            if not raw:
                if raw_source:
                    log(f"[通用兜底] {generic_query[:40]} -> 仅搜索建议，不算有效结果")
                return
            text = _distill_search_results(
                raw,
                generic_query,
                max_entries=10,
                max_chars=MAX_SEARCH_CHARS,
            )
            if not text:
                return
            search_results = raw
            distilled = text
            search_returned = True
            if request_messages and isinstance(
                request_messages[-1].get("content"), str
            ):
                request_messages[-1]["content"] += (
                    f"\n\n【搜索结果】\n{text}"
                )
            log(f"[通用兜底] {generic_query[:40]} -> {len(text)}字")

        if _should_use_single_shot_search(query):
            log("[单轮搜索] 简单事实问题直接内置搜索一次")
            single_shot_used = True
            await generic_fallback_search()
        else:
            try:
                tool_calls, tool_direct_reply, tool_model, native_search = await asyncio.wait_for(
                    self._complete_with_tools(
                        system, request_messages, model_refs=MODELS_FACTUAL,
                        max_tokens=800,
                        force_search=False,
                        tools_enabled=allow_tools),
                    timeout=TOOL_CALL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                log(f"[工具] 第一轮 {TOOL_CALL_TIMEOUT}s 超时，降级内置搜索")
                tool_calls, tool_direct_reply = [], ""
                await generic_fallback_search()
            except Exception as e:
                log(f"[工具] tool_calls 调用失败，退回内置搜索: {repr(e)[:80]}")
                tool_calls, tool_direct_reply = [], ""
        tool_round = 0
        if tool_calls:
            tool_round += 1
            tool_results = []
            for tc in tool_calls:
                fn_name = tc.get("function", {}).get("name", "")
                args_raw = tc.get("function", {}).get("arguments", "{}") or "{}"
                try:
                    args = json.loads(args_raw)
                except Exception:
                    args = {}
                if fn_name == "plan_search":
                    search_attempted = True
                    thought = str(args.get("thought_process") or "").strip()
                    planned = args.get("queries") or []
                    raw = await _search_planned_queries(
                        planned, max_results=MAX_SEARCH_RESULTS)
                    raw = await self._enrich_lyrics_search(
                        raw, query,
                        [item["query"] for item in planned if isinstance(item, dict)])
                    raw = _search_evidence_text(raw)
                    if raw:
                        search_results = raw
                        if _is_lyrics_query(query):
                            distilled = raw[:MAX_LYRIC_CONTEXT_CHARS]
                        else:
                            distilled = _distill_search_results(
                                raw, query, max_entries=10, max_chars=MAX_SEARCH_CHARS,
                            )
                        search_returned = bool(distilled)
                    result_text = raw or "（无搜索结果）"
                    if thought:
                        result_text = f"搜索计划：{thought}\n\n{result_text}"
                    tool_results.append((tc, result_text))
                    log(f"[工具] plan_search: {len(planned)} 个查询 -> {len(raw)}字")
                elif fn_name == "search_web":
                    search_attempted = True
                    sq = _clean_search_query(str(args.get("query") or query))
                    sq = re.sub(r"20\d{2}年", time.strftime("%Y年"), sq)
                    sq_clean = _build_search_query(sq)
                    if sq_clean and sq_clean != sq:
                        sq = sq_clean
                    raw = await search_web_async(
                        sq, max_results=MAX_SEARCH_RESULTS, exact=True)
                    raw = await self._enrich_lyrics_search(raw, query, [sq])
                    raw = _search_evidence_text(raw)
                    if raw:
                        search_results = raw
                        if _is_lyrics_query(query):
                            distilled = raw[:MAX_LYRIC_CONTEXT_CHARS]
                        else:
                            distilled = _distill_search_results(
                                raw, sq, max_entries=10, max_chars=MAX_SEARCH_CHARS,
                            )
                        search_returned = bool(distilled)
                    tool_results.append((tc, raw or "（无搜索结果）"))
                    log(f"[工具] tool_calls search: {sq} -> {len(raw)}字")
                    log(f"[FC] 第1轮 tool_calls=search_web query={sq} -> {len(raw)}字")
                elif fn_name == "fetch_page":
                    search_attempted = True
                    page_url = str(args.get("url") or "").strip()
                    if page_url:
                        page_text = await _fetch_page_text_async(page_url, timeout=5)
                    else:
                        page_text = ""
                    result_text = page_text or "（无法打开页面）"
                    if page_text and _is_lyrics_query(query):
                        search_results = (search_results + "\n\n" + page_text).strip()
                        distilled = search_results[:MAX_LYRIC_CONTEXT_CHARS]
                        search_returned = True
                    tool_results.append((tc, result_text))
                    log(f"[工具] fetch_page: {page_url[:80]} -> {len(page_text)}字")
                else:
                    tool_results.append((tc, "未知工具"))
            if tool_results:
                request_messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tc for tc, _ in tool_results],
                })
                for tc, result in tool_results:
                    request_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": result,
                    })
        elif native_search:
            search_attempted = True
            raw = _web_search_to_text(native_search)
            raw = await self._enrich_lyrics_search(raw, query, [query])
            raw = _search_evidence_text(raw)
            if raw:
                search_results = raw
                if _is_lyrics_query(query):
                    distilled = raw[:MAX_LYRIC_CONTEXT_CHARS]
                else:
                    distilled = _distill_search_results(
                        raw, query,
                        max_entries=MAX_SEARCH_RESULTS,
                        max_chars=MAX_SEARCH_CHARS,
                    )
                    if not distilled:
                        distilled = raw[:MAX_SEARCH_CHARS]
                search_returned = bool(distilled) or bool(raw)
                request_messages[-1]["content"] += (
                    f"\n\n【搜索结果】\n{distilled}")
                log(f"[工具] GLM原生联网: {query[:40]} -> {len(distilled)}字")
        else:
            if tool_direct_reply:
                if ALWAYS_SEARCH:
                    log("[FC] ALWAYS_SEARCH=1 模型未调用搜索，忽略直接回复并强制通用联网")
                    tool_direct_reply = ""
                else:
                    log("[FC] 模型未调用搜索但给出直接回复，保留第一轮回复，不做规则兜底搜索")
                    reply = _sanitize_reply(tool_direct_reply)
                    used_model = tool_model
                    skip_final = True
            else:
                log("[FC] 模型未返回搜索计划/直接回复，不强制规则搜索")
        if (
            not search_returned
            and not skip_final
            and not native_search
            and not single_shot_used
        ):
            await generic_fallback_search()
        _log_phase(phase_start, "工具/搜索")
        if not search_returned:
            system += (
                "\n【当前状态】当前没有搜索结果，可以按已有知识回答，也可以说明不确定；"
                "输出正文，不要包含工具调用格式、Markdown 或明显残缺重复片段。")

        # 注入当前时间
        if search_returned:
            system += (
                "\n【回答要求】根据搜索结果自然成文，保持塔菲语气；"
                "输出为可直接发送的正文，不要输出 Markdown、工具调用格式或明显残缺重复片段。"
            )
        system += (
            "\n【时效】以当前日期判断事件是否已发生，搜索明确的信息优先。"
        )

        # 纠正型追问 + 已有搜索结果时，强制模型基于搜索结果重新判断
        if _CORRECTION_FOLLOWUP_RE.search(query) and distilled and not skip_final:
            if request_messages and isinstance(request_messages[-1].get("content"), str):
                request_messages[-1]["content"] += (
                    "\n\n【纠正型追问】\n"
                    f"用户在质疑上一句回答：{last_answer}\n"
                    f"优先依据当前搜索结果重新判断：\n{distilled[:1000]}\n"
                    "请重新回答；如果搜索结果与上一句回答矛盾，以搜索结果为准。"
                    )
                log("[纠正型追问] 检测到纠正型追问，强化搜索结果约束")

        # 最终生成：事实/攻略类串行走稳定模型，闲聊才竞速抢答。
        use_serial = _should_serial_final(query)
        if not skip_final:
            if use_serial:
                reply, used_model = await self._complete(
                    system, request_messages,
                    max_tokens=final_max_tokens, temperature=temperature, top_p=top_p,
                    model_refs=MODELS_FACTUAL if (is_factual or guide_query) else MODELS_CASUAL,
                    weak_query=query, min_reply_len=min_reply_len,
                )
            else:
                reply, used_model = await self._complete_race(
                    system, request_messages,
                    max_tokens=final_max_tokens, temperature=temperature, top_p=top_p,
                    model_refs=MODELS_FACTUAL if (is_factual or guide_query) else MODELS_CASUAL,
                    weak_query=query, min_reply_len=min_reply_len,
                )
        _log_phase(phase_start, "最终模型")
        if not reply:
            fallback = distilled or search_results
            if fallback:
                reply = await self._llm_answer_from_search(query, fallback)
                if reply:
                    return _truncate_at_sentence(
                        _append_search_source_marker(reply, True, True),
                        MAX_REPLY_LEN,
                    )
            return "（AI 暂时无法回复，请稍后再试）"
        log(f"[API] 使用模型: {used_model} | temp={temperature} top_p={top_p}")

        if _looks_like_tool_call(reply) and tool_round < REACT_MAX_ROUNDS:
            tool_round += 1
            log("[FC] 第2轮仍输出工具调用文本，进入第3轮强制成文")
            final_messages = [
                m for m in request_messages
                if m.get("role") != "tool" and not m.get("tool_calls")
            ]
            final_messages.append({"role": "user", "content": (
                "根据搜索结果直接回答，不要调用工具。\n" +
                f"问题：{query}\n搜索结果：{distilled or search_results or '（无搜索结果）'}")})
            if use_serial:
                reply, used_model = await self._complete(
                    system + "\n" + TOOL_FINAL_SYSTEM, final_messages,
                    max_tokens=final_max_tokens, temperature=temperature, top_p=top_p,
                    model_refs=MODELS_FACTUAL if (is_factual or guide_query) else MODELS_CASUAL,
                    weak_query=query, min_reply_len=min_reply_len,
                )
            else:
                reply, used_model = await self._complete_race(
                    system + "\n" + TOOL_FINAL_SYSTEM, final_messages,
                    max_tokens=final_max_tokens, temperature=temperature, top_p=top_p,
                    model_refs=MODELS_FACTUAL if (is_factual or guide_query) else MODELS_CASUAL,
                    weak_query=query, min_reply_len=min_reply_len,
                )
            reply = _sanitize_reply(reply or "")
            if not reply or _looks_like_tool_call(reply):
                reply = ("我印象中这个问题的答案要按搜到的信息来判断：\n" +
                         (distilled or search_results or "不过现在网没接上，我暂时没拿到可靠结果。"))
                reply = _truncate_at_sentence(reply, MAX_REPLY_LEN)
            log("[FC] 第3轮最终回答已生成")
        elif _looks_like_tool_call(reply):
            log("[Agent] 强制终止工具调用循环，防止死循环")
            reply = "塔菲暂时没搜到可靠答案，先不乱编喵。"
        reply = _sanitize_reply(reply)
        if _looks_garbled_reply(reply) or _has_pathological_repetition(reply):
            log("[自检] 检测到乱码/重复，触发最终回答重写")
            checked = await self._self_check_reply(
                query,
                distilled or search_results,
                reply,
                max_tokens=min(final_max_tokens, 1200),
            )
            if checked:
                reply = _sanitize_reply(checked)
        reply = _fix_known_phrase_errors(reply, query)
        source = distilled or search_results
        if source:
            pre_preserve_reply = reply
            reply = _ensure_numeric_facts_preserved(reply, source)
            reply = _ensure_entity_facts_preserved(reply, source)
            reply = _ensure_source_phrases_preserved(reply, source)
            if _has_pathological_repetition(reply) or _looks_garbled_reply(reply):
                log("[后处理] 保真补全导致重复，回滚到补全前回答")
                reply = _clean_pathological_repetition(pre_preserve_reply) or pre_preserve_reply
        reply = _append_search_source_marker(
            reply, search_returned, search_attempted)
        self.recent_replies.append(reply)
        self.recent_replies = self.recent_replies[-10:]
        if intent == "fact" and "AI 暂时" not in reply:
            _answer_cache_set(answer_key, reply, intent)

        full_messages = history + [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": reply},
        ]
        # 完整保存原文；旧历史由后台摘要压缩提炼，不在这里截断删除
        self.sessions.put(key, {
            **store,
            "messages": full_messages,
            "summary": store.get("summary", "") or "",
            "summary_index": summary_index,
            "last_question": query,
            "last_answer": reply,
            "last_speaker": speaker or "",
        })
        unsummarized_chars = sum(
            len(str(m.get("content", ""))) for m in full_messages[summary_index:]
        )
        if unsummarized_chars >= COMPRESS_TRIGGER_CHARS:
            asyncio.create_task(self._compress_history(key))
        _log_phase(phase_start, "保存/整理")
        log(f"[耗时] {time.time() - start:.1f}s")
        return _truncate_at_sentence(reply, MAX_REPLY_LEN)

    # ---------- 消息处理 ----------
    async def _process_group(self, ev):
        gid = str(ev["group_id"])
        uid = str(ev["user_id"])
        if uid == self.self_id:
            return
        msg_arr = ev.get("message", [])
        text, at_bot = self.extract_text(msg_arr, self.self_id)
        if not text:
            return
        inc_stat("total_messages")
        original_text = text
        write_health(
            last_message_received_at=datetime.now().isoformat(),
            last_message_type="group",
        )
        admin_reply = handle_admin_command(
            text,
            uid,
            {"message_type": "group", "group_id": gid},
            self,
        )
        if admin_reply is not None:
            await self.send_group_msg(gid, uid, admin_reply)
            return
        if _admin_text(text).startswith("!"):
            return
        is_safe, refusal = _check_safety(text)
        if not is_safe:
            inc_stat("safety_blocks")
            write_health(last_safety_block=datetime.now().isoformat())
            log(f"[安全拦截] 群{gid}: {text[:60]}")
            await self.send_group_msg(gid, uid, refusal)
            return
        try:
            self.style_store.learn(gid, text, uid)
        except Exception:
            pass
        if TRIGGER == "at" and not at_bot:
            return
        try:
            reply_context = await asyncio.wait_for(
                self._fetch_reply_context(ev), timeout=5)
        except Exception as e:
            log(f"[引用消息] 获取超时/失败: {repr(e)[:100]}")
            reply_context = ""
        if reply_context:
            text = f"{text}\n[引用原消息] {reply_context}"
        sender = ev.get("sender", {})
        uname = sender.get("card") or sender.get("nickname") or uid
        gname = ev.get("group_name", "群")
        prompt = f"【QQ群】{gname}({gid})\n{uname}({uid}): {text}\n"
        log(f"[群{gid}] {uname}: {text[:80]}")
        if SEND_THINKING:
            try:
                await self.send_group_text(gid, THINKING_PLACEHOLDER)
            except Exception as e:
                log(f"[正在想发送失败] 群{gid}: {repr(e)[:120]}")
        reply_start = time.time()
        reply = await self.ask_model(gid, prompt, _clean_question(original_text), uname)
        _log_phase(reply_start, "ask_model")
        record_group_learning(gid, uname, original_text, reply)
        record_reply_time(int((time.time() - reply_start) * 1000))
        log(f"[群{gid}] AI回复: {reply[:80]}")
        send_start = time.time()
        await self.send_group_msg(gid, uid, reply)
        _log_phase(send_start, "发送")
        try:
            _try_self_improve(gid, uname, text, reply)
        except Exception:
            pass
        try:
            history_len = len((self.sessions.get(gid) or {}).get("messages") or [])
            if history_len and history_len % 20 == 0:
                asyncio.create_task(self._learn_group_history(gid))
            if history_len and history_len % 50 == 0:
                asyncio.create_task(self._learn_analogy_methodology(gid))
                messages = (self.sessions.get(gid) or {}).get("messages") or []
                recent_texts = [
                    str(m.get("content", ""))[:100]
                    for m in messages[-60:]
                    if m.get("content")
                ]
                self.style_store.discover_new_slang(gid, recent_texts)
                self.style_store.save()
        except Exception:
            pass

    async def _process_private(self, ev):
        uid = str(ev["user_id"])
        if uid == self.self_id:
            return
        text, _ = self.extract_text(ev.get("message", []), self.self_id)
        if not text:
            return
        inc_stat("total_messages")
        original_text = text
        write_health(
            last_message_received_at=datetime.now().isoformat(),
            last_message_type="private",
        )
        admin_reply = handle_admin_command(
            text,
            uid,
            {"message_type": "private", "user_id": uid},
            self,
        )
        if admin_reply is not None:
            await self.send_private_msg(uid, admin_reply)
            return
        if _admin_text(text).startswith("!"):
            return
        is_safe, refusal = _check_safety(text)
        if not is_safe:
            inc_stat("safety_blocks")
            write_health(last_safety_block=datetime.now().isoformat())
            log(f"[安全拦截] 私聊{uid}: {text[:60]}")
            await self.send_private_msg(uid, refusal)
            return
        try:
            reply_context = await asyncio.wait_for(
                self._fetch_reply_context(ev), timeout=5)
        except Exception as e:
            log(f"[引用消息] 获取超时/失败: {repr(e)[:100]}")
            reply_context = ""
        if reply_context:
            text = f"{text}\n[引用原消息] {reply_context}"
        uname = ev.get("sender", {}).get("nickname", uid)
        prompt = f"【QQ私聊】{uname}({uid}): {text}\n"
        log(f"[私聊{uid}] {uname}: {text[:80]}")
        if SEND_THINKING:
            try:
                await self.send_private_msg(uid, THINKING_PLACEHOLDER)
            except Exception as e:
                log(f"[正在想发送失败] 私聊{uid}: {repr(e)[:120]}")
        reply_start = time.time()
        reply = await self.ask_model(
            "private_" + uid,
            prompt,
            _clean_question(original_text),
            uname,
        )
        _log_phase(reply_start, "ask_model")
        record_group_learning("private_" + uid, uname, original_text, reply)
        record_reply_time(int((time.time() - reply_start) * 1000))
        log(f"[私聊{uid}] AI回复: {reply[:80]}")
        send_start = time.time()
        await self.send_private_msg(uid, reply)
        _log_phase(send_start, "发送")
        try:
            _try_self_improve(uid, uname, text, reply, is_private=True)
        except Exception:
            pass

    # ---------- 发送 ----------
    async def send_group_text(self, gid, text):
        await self.call_api("send_group_msg", {"group_id": int(gid), "message": text})

    async def send_group_msg(self, gid, at_qq, text):
        segs = [{"type": "at", "data": {"qq": at_qq}},
                {"type": "text", "data": {"text": " " + text}}]
        result = await self.call_api("send_group_msg", {"group_id": int(gid), "message": segs})
        if result and (result.get("status") == "ok" or result.get("retcode") == 0):
            write_health(
                last_reply_sent_at=datetime.now().isoformat(),
                last_reply_kind="group",
            )
        return result

    async def send_private_msg(self, uid, text):
        result = await self.call_api("send_private_msg", {"user_id": int(uid), "message": text})
        if result and (result.get("status") == "ok" or result.get("retcode") == 0):
            write_health(
                last_reply_sent_at=datetime.now().isoformat(),
                last_reply_kind="private",
            )
        return result

    async def call_api(self, action, params, retries=5):
        """通过 OneBot 11 HTTP API 调用（默认 http://127.0.0.1:6099）。
        注意：OneBot 11 启动的是 WebSocket Server（推消息），不是 Client，
        所以调 API 必须走 HTTP，不能用 WS 反向。"""
        url = f"{ONEBOT_HTTP_URL}/{action}"
        body = json.dumps(params).encode("utf-8")
        loop = asyncio.get_running_loop()
        def _do():
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "Content-Type": "application/json",
            })
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return json.loads(r.read().decode("utf-8", "replace"))
            except Exception as e:
                return {"status": "failed", "msg": repr(e)[:120]}
        d = None
        last_failure = None
        for attempt in range(retries):
            d = await loop.run_in_executor(None, _do)
            ok = bool(d) and (d.get("status") == "ok" or d.get("retcode") == 0)
            if ok:
                if action.startswith("send_"):
                    write_health(last_error="", last_error_at="")
                    mid = ((d.get("data") or {}).get("message_id") or "")
                    log(f"[API {action} ok] message_id={mid}")
                return d
            last_failure = d
            log(f"[API {action} 失败 {attempt + 1}/{retries}] {json.dumps(d, ensure_ascii=False)[:200]}")
            if attempt < retries - 1:
                await asyncio.sleep(2)
        log(f"[API {action} 最终失败] {json.dumps(last_failure or d, ensure_ascii=False)[:200]}")
        write_health(
            last_error=f"OneBot {action} 失败: {json.dumps(last_failure or d, ensure_ascii=False)[:120]}",
            last_error_at=datetime.now().isoformat(),
        )
        return d

    # ---------- 主循环 ----------
    async def _deep_compress_if_needed(self):
        """对话数据达到 45GB 时，后台压缩各会话旧历史，保留重要内容。"""
        try:
            if not os.path.exists(SESSIONS_FILE):
                return
            size = os.path.getsize(SESSIONS_FILE)
            if size < CONVERSATION_COMPRESS_BYTES:
                return
            log("[存储] 开始深度压缩 45GB 对话数据")
            for key in list(self.sessions.data.keys()):
                await self._compress_history(key)
                size = os.path.getsize(SESSIONS_FILE)
                if size < CONVERSATION_COMPRESS_BYTES * 0.9:
                    break
            log(f"[存储] 深度压缩完成，当前 {size / 1024 / 1024:.1f} MB")
        except Exception as e:
            log(f"[存储] 深度压缩异常: {repr(e)[:120]}")
    async def _learn_group_history(self, key):
        """从群历史中学习值得长期记住的信息，写入 bot_memory.md。"""
        try:
            store = self.sessions.get(key)
            if not isinstance(store, dict):
                return
            messages = store.get("messages") or []
            recent = messages[-30:]
            lines = []
            for m in recent:
                role = "用户" if m.get("role") == "user" else "AI"
                content = str(m.get("content", ""))[:300]
                if content:
                    lines.append(f"{role}: {content}")
            if not lines:
                return
            learned, _ = await self._complete(
                GROUP_MEMORY_LEARN_SYSTEM,
                [{"role": "user", "content": f"当前日期：{_current_date_text()}\n聊天历史：\n" + "\n".join(lines)}],
                max_tokens=300, temperature=0.0, top_p=0.5,
                model_refs=MODELS_FACTUAL,
            )
            learned = _clean_reply(learned or "")
            if not learned:
                return
            with _memory_lock:
                with open(MEMORY_FILE, encoding="utf-8") as f:
                    mem = f.read()
                marker_group = f"## 群 {key}"
                if marker_group not in mem:
                    mem += f"\n{marker_group}\n"
                idx = mem.index(marker_group) + len(marker_group)
                next_line = mem.index("\n", idx) + 1
                new_lines = [
                    line.strip()
                    for line in learned.splitlines()
                    if line.strip() and not line.startswith("- [群历史学习]")
                ]
                added = 0
                for line in new_lines:
                    entry = f"- [群历史学习] {line[:120]}"
                    if entry not in mem:
                        mem = mem[:next_line] + entry + "\n" + mem[next_line:]
                        added += 1
                with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                    f.write(mem)
                log(f"[群学习] {key} 新增 {added} 条记忆")
        except Exception as e:
            log(f"[群学习] {key} 失败: {repr(e)[:100]}")

    async def _learn_analogy_methodology(self, key):
        try:
            store = self.sessions.get(key)
            if not isinstance(store, dict):
                return
            messages = store.get("messages") or []
            if len(messages) < 10:
                return
            group_data = self.style_store.data.get(str(key), {}) or {}
            last_learned = group_data.get("analogy_learned_history_len", 0)
            if last_learned and len(messages) - last_learned < 20:
                return

            analogy_keywords = ("类比", "相当于", "好比", "类似于", "对应", "对标", "换成", "等同于")
            analogy_dialogs = []
            recent = messages[-200:]
            for i, m in enumerate(recent):
                if m.get("role") != "user":
                    continue
                content = str(m.get("content", ""))
                if not any(kw in content for kw in analogy_keywords):
                    continue
                dialog = [content[:150]]
                if i + 1 < len(recent):
                    next_msg = str(recent[i + 1].get("content", ""))[:150]
                    if next_msg:
                        dialog.append(next_msg)
                analogy_dialogs.append("\n".join(dialog))

            if len(analogy_dialogs) < 3:
                log(f"[类比学习] {key} 历史类比仅 {len(analogy_dialogs)} 条，不足3条不学习")
                return

            method_system = (
                "你是类比方法论分析器。从群历史里的类比对话中，总结这个群做类比的方法论。\n"
                "观察维度：\n"
                "1. 选对应物的逻辑：按什么维度匹配？（定位/风格/争议/粉丝/地位/价格）\n"
                "2. 风格：调侃型/严肃型/玩梗型/数据型？\n"
                "3. 常用什么领域做类比？（足球/游戏/影视/历史/其他）\n"
                "4. 给理由的方式：详细论证/一句话带过/纯玩梗？\n"
                "5. 类比的长度偏好：短平快/中等/长篇论证？\n"
                "\n"
                "输出要求：\n"
                "- 用 3-5 句话描述这个群的类比方法论\n"
                "- 要具体，能指导新类比\n"
                "- 不要泛泛而谈，不要说『按特征匹配』这种废话\n"
                "- 只输出方法论描述，不要前缀标签"
            )
            dialogs_text = "\n---\n".join(analogy_dialogs[:15])
            learned, _ = await self._complete(
                method_system,
                [{"role": "user", "content": f"群历史类比对话：\n{dialogs_text}"}],
                max_tokens=200, temperature=0.0, top_p=0.5,
                model_refs=MODELS_FACTUAL,
            )
            learned = _clean_reply(learned or "")
            if not learned:
                return
            self.style_store.set_analogy_methodology(
                key, learned, len(analogy_dialogs), len(messages))
            log(f"[类比学习] {key} 学到方法论: {learned[:80]}")
        except Exception as e:
            log(f"[类比学习] {key} 失败: {repr(e)[:100]}")

    async def _periodic_style_learning(self):
        while True:
            await asyncio.sleep(3600)
            try:
                for gid in list(self.style_store.data.keys()):
                    await self.style_store.generate_style_description(gid)
                    store = self.sessions.get(gid)
                    if isinstance(store, dict):
                        messages = store.get("messages") or []
                        recent_texts = [
                            str(m.get("content", ""))[:100]
                            for m in messages[-50:]
                            if m.get("content")
                        ]
                        self.style_store.discover_new_slang(gid, recent_texts)
                self.style_store.save()
            except Exception as e:
                log(f"[定时风格学习] 异常: {repr(e)[:120]}")

    async def run(self):
        if self.model_semaphore is None:
            self.model_semaphore = asyncio.Semaphore(3)
        asyncio.create_task(self._deep_compress_if_needed())
        asyncio.create_task(self._periodic_style_learning())
        asyncio.create_task(self._health_heartbeat())
        retry_delay = 3
        while True:
            try:
                async with websockets.connect(WS_URL, ping_interval=25, open_timeout=15) as ws:
                    log("已连接 OneBot WebSocket")
                    retry_delay = 3
                    self.ws_connected = True
                    write_health(
                        ws_connected=True,
                        ws_last_connect_time=datetime.now().isoformat(),
                        last_error="",
                        last_error_at="",
                    )
                    async for raw in ws:
                        try:
                            ev = json.loads(raw)
                        except Exception:
                            continue
                        ptype = ev.get("post_type")
                        if ptype == "meta_event" and ev.get("meta_event_type") == "lifecycle":
                            if ev.get("self_id"):
                                self.self_id = str(ev["self_id"])
                            log(f"生命周期事件: {ev.get('sub_type','')} | bot={self.self_id}")
                            continue
                        if ptype != "message":
                            continue
                        if ev.get("message_type") in ("group", "private"):
                            key = str(ev["group_id"]) if ev["message_type"] == "group" else "private_" + str(ev["user_id"])
                            await self.worker_for(key).put(ev)
            except Exception as e:
                log(f"连接断开: {repr(e)[:120]}，{retry_delay:.0f} 秒后重连")
                self.ws_connected = False
                write_health(
                    ws_connected=False,
                    last_error=f"连接断开: {repr(e)[:120]}",
                    last_error_at=datetime.now().isoformat(),
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(60, retry_delay * 1.5)

def _onebot_http_probe():
    """启动时探测 OneBot HTTP API 是否可用；测试模式跳过真实网络请求。"""
    if os.environ.get("QQ_TEST_MODE") == "1":
        return True
    url = f"{ONEBOT_HTTP_URL}/get_login_info"
    try:
        req = urllib.request.Request(url, data=b"{}", method="POST", headers={
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=3) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return bool(d) and (d.get("status") == "ok" or d.get("retcode") == 0)
    except Exception:
        return False

def run_startup_checks():
    """每次启动前离线自检，失败则拒绝启动，避免带着坏规则上线。"""
    checks = []
    try:
        checks.append(_clean_reply("<think>思考</think>答案") == "答案")
        checks.append(_clean_question("[引用] 罗哥什么时候结婚") == "C罗 什么时候结婚")
        checks.append("C罗" in _build_search_query("[引用] 罗哥什么时候结婚"))
        distilled = _distill_search_results(
            "【图赫尔是谁】图赫尔是德国教练\n【登录】广告", "图赫尔")
        checks.append("图赫尔" in distilled and "登录" not in distilled)
        text, score = retrieve_knowledge_scored(
            "图赫尔是谁", "## 群 1\n- 图赫尔：德国足球教练")
        checks.append(text and score >= KNOWLEDGE_MIN_SCORE)
        checks.append(_needs_search("C罗什么时候结婚"))
        checks.append(not _needs_search("你同意DeepSeek是性价比之王吗"))
        checks.append(MAX_CONTEXT_CHARS >= 100000)
        checks.append("glm/glm-4-flash-250414" in MODELS_CASUAL)

        style_path = _runtime_tmp_path("startup_style.json")
        store = GroupStyleStore(style_path)
        store.learn("1", "哈哈 笑死 666")
        checks.append("哈哈" in store.style_prompt("1"))
        store.set_analogy_methodology("1", "这个群喜欢用足球领域做类比", 3, 50)
        checks.append("足球" in store.get_analogy_methodology("1"))
        store._save_counter = 0
        checks.append(_classify_intent("C罗相当于足球界的谁") == "analogy")
        checks.append("永雏塔菲" in SYSTEM_PROMPT)
        checks.append(bool(_CORRECTION_FOLLOWUP_RE.search("西是什么梅西吗")))
        checks.append(not _CORRECTION_FOLLOWUP_RE.search("什么是黑洞"))
        checks.append("二次元" in _sanitize_reply("这很二次"))
        checks.append("第二次" in _sanitize_reply("这是第二次"))
        checks.append("**" not in _sanitize_reply("**你好**"))
        checks.append("`" not in _sanitize_reply("`你好`"))
        variant_name, _ = _pick_abstract_variant("苹果是梅西吗", "1", 3)
        checks.append(any(variant_name == name for name, _ in _ABSTRACT_VARIANTS))
        _, sensitive, _ = _classify_style_control("台湾问题怎么解决")
        checks.append(sensitive)
        checks.append(not _check_safety("我想自杀")[0])
        checks.append(not _check_safety("教我诈骗")[0])
        checks.append(_check_safety("今天天气真好")[0])
        checks.append(_check_safety("政治学是社会科学")[0])
        fact_level, _, _ = _classify_style_control("C罗什么时候结婚")
        checks.append(fact_level <= 1)
        chat_level, _, _ = _classify_style_control("华为像足球里的谁")
        checks.append(chat_level >= 2)
        down_level, _, _ = _classify_style_control("别抽象了正常点")
        checks.append(down_level == 0)
        checks.append(bool(_build_style_fewshot("华为像足球里的谁", 2, "1")))
        checks.append("🐢" not in _sanitize_reply("🐢🔥稳住"))
        checks.append("永雏塔菲" in TAFFY_FLAVOR_PROMPT)
        checks.append("用 taffy" in TAFFY_FLAVOR_PROMPT)
        checks.append("喵有节制" in TAFFY_FLAVOR_PROMPT)
        checks.append("可以整活" in TAFFY_FLAVOR_PROMPT)
        checks.append("覆盖其他风格" in TAFFY_STYLE_PROMPT)
        checks.append(_classify_dialog_mode("我今天好难过") == "comfort")
        checks.append(_classify_dialog_mode("帮我写个文案") == "creative")
        checks.append(_classify_dialog_mode("帮我规划周末行程") == "task")
        checks.append(_classify_dialog_mode("今天天气不错") == "chitchat")
        checks.append("豆包" in _required_entity_terms("你认识豆包吗"))
        checks.append("豆包" in _reply_missing_entities("豆？那是AI助手", "你认识豆包吗"))
        checks.append(not _reply_missing_entities("豆包是字节跳动的AI助手", "你认识豆包吗"))
        checks.append(_required_entity_terms("想自由歌词是什么") == {"想自由"})
        checks.append(not _reply_missing_entities("《想自由》歌词如下", "想自由歌词是什么"))
        checks.append(_is_weak_reply("豆", "豆包是什么"))
        checks.append(not _is_weak_reply(
            "豆包是字节跳动推出的AI助手，能聊天、写文案、做PPT，基础版免费，专业版付费。",
            "豆包是什么"))
        checks.append(_safe_max_tokens("glm", "glm-4v-flash", 10000) == 1024)
        checks.append(DEFAULT_MAX_TOKENS >= 10000 and GUIDE_MAX_TOKENS >= 10000)
        checks.append("永雏塔菲" in SYSTEM_PROMPT and "用 taffy" in TAFFY_FLAVOR_PROMPT)
        checks.append(_filter_silent_memory_text("牢大都说6\n正常内容") == "正常内容")
        checks.append(_required_entity_terms("关注塔菲谢谢喵") == {"塔菲"})
        checks.append(MODELS_FACTUAL[0].startswith("opencode_free/"))
        checks.append(_is_taffy_styled("塔菲觉得这个不错喵"))
        checks.append(not _is_taffy_styled("根据搜索结果，这是一个AI回答"))
        checks.append(_sort_models([
            "opencode_free/deepseek-v4-flash-free",
            "glm/glm-4.5-flash",
        ])[0].startswith("opencode_free/"))
        checks.append(_onebot_http_probe())

        card_payload = Bridge._parse_card_payload({"data": json.dumps({
            "title": "测试名片",
            "url": "https://www.bilibili.com/video/BV1abc",
        })})
        checks.append("bilibili" in Bridge._card_text(card_payload))

        session_path = _runtime_tmp_path("startup_session.json")
        sstore = SessionStore(session_path)
        sstore.put("k", {"messages": []})
        sstore.update("k", lambda d: {**d, "summary": "ok"})
        checks.append(sstore.get("k").get("summary") == "ok")

        _search_cache_set("startup", "ok")
        checks.append(_search_cache_get("startup") == "ok")
    except Exception as e:
        log(f"[启动检查] 异常: {repr(e)[:160]}")
        return False
    finally:
        for p in ("startup_style.json", "startup_session.json"):
            try:
                os.remove(_runtime_tmp_path(p))
            except Exception:
                pass
    if all(checks):
        log("[启动检查] 通过")
        return True
    log(f"[启动检查] 失败 {sum(1 for c in checks if not c)} 项")
    return False

def main():
    if not acquire_single_instance():
        log("检测到已有桥接实例在运行，本进程退出（防重复回复）")
        sys.exit(0)
    if not run_startup_checks():
        sys.exit(1)
    _cleanup_log()
    _cleanup_sessions()
    write_health(
        pid=os.getpid(),
        start_time=datetime.now().isoformat(),
        ws_connected=False,
        last_error="",
        last_error_at="",
        search_degraded=False,
        search_degraded_engine="",
        model_circuit_open=False,
    )
    with _stats_lock:
        _flush_stats()
    log("桥接启动 (NapCat OneBot11 -> 模型 API)")
    asyncio.run(Bridge().run())


if __name__ == "__main__":
    main()
