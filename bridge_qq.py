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

# ---------- 路径常量 ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEMORY_FILE = os.path.join(BASE_DIR, "data", "bot_memory.md")
SESSIONS_FILE = os.path.join(BASE_DIR, "data", "bridge_sessions.json")
LOG_FILE = os.path.join(BASE_DIR, "logs", "bridge.log")
HERMES_ENV_FILE = r"E:\hermes\.env"
GROUP_STYLES_FILE = os.path.join(BASE_DIR, "data", "group_profiles.json")
TMP_DIR = os.path.join(BASE_DIR, ".tmp")
HEALTH_FILE = os.path.join(BASE_DIR, "data", "health.json")
_HEALTH_LOCK = threading.Lock()
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
_HEALTH_DEFAULTS = {
    "last_message_received_at": "",
    "last_reply_sent_at": "",
    "last_message_type": "",
    "last_reply_kind": "",
    "last_safety_block": "",
}


def _runtime_tmp_path(name):
    try:
        os.makedirs(TMP_DIR, exist_ok=True)
    except Exception:
        pass
    return os.path.join(TMP_DIR, name)

# ---------- .env 加载（必须先于所有 os.environ.get 配置）----------
def _load_env_file():
    r"""加载 <项目根>\.env 文件中的环境变量（不覆盖已有值）"""
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass

_load_env_file()

# ---------- 配置 ----------
WS_URL = os.environ.get("QQ_WS_URL", "ws://127.0.0.1:3001")
ONEBOT_HTTP_URL = os.environ.get("QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:6099").rstrip("/")
TRIGGER = os.environ.get("QQ_TRIGGER", "at")  # at=仅@时回复, all=全部群消息
MAX_REPLY_LEN = 12000
try:
    DEFAULT_MAX_TOKENS = int(os.environ.get("QQ_MAX_OUTPUT_TOKENS", "10000"))
except ValueError:
    DEFAULT_MAX_TOKENS = 10000
try:
    GUIDE_MAX_TOKENS = int(os.environ.get("QQ_GUIDE_OUTPUT_TOKENS", "10000"))
except ValueError:
    GUIDE_MAX_TOKENS = 10000


def _safe_max_tokens(provider, model_name, max_tokens):
    if provider == "glm" and "4v" in model_name:
        return min(max_tokens, 1024)
    return max_tokens


MAX_CONTEXT_CHARS = 120000  # 模型上下文预算，尽量利用 128K 上下文
CONTEXT_SUMMARY_MAX_CHARS = 15000  # 历史摘要最多保留字数
COMPRESS_TRIGGER_CHARS = 110000    # 未压缩上下文达到 11 万字时触发摘要
COMPRESS_KEEP_RECENT_CHARS = 85000 # 摘要时保留最近 8.5 万字原文
KNOWLEDGE_TOP_K = 3
KNOWLEDGE_MAX_CHARS = 2000
LOG_MAX_SIZE = 5 * 1024 * 1024  # 日志文件超过 5MB 自动截断
CONVERSATION_LIMIT_BYTES = 50 * 1024 * 1024 * 1024  # 对话存储上限 50GB
CONVERSATION_COMPRESS_BYTES = 45 * 1024 * 1024 * 1024  # 对话数据达到 45GB 时深度压缩
API_TIMEOUT = int(os.environ.get("QQ_API_TIMEOUT", "30"))  # 单次模型 API 超时（秒）
API_CONNECT_TIMEOUT = int(os.environ.get("QQ_API_CONNECT_TIMEOUT", "12"))  # 建立连接/响应头超时
PROVIDER_COOLDOWN_SECONDS = 60  # provider 异常后熔断时间
KNOWLEDGE_MIN_SCORE = 2  # 记忆块低于该分数不注入，避免无关内容干扰
REFLECTION_MAX_ROUNDS = 1  # 反思只做一轮，控制延迟
REACT_MAX_ROUNDS = 2  # ReAct 最多两轮：Action -> Observation -> 补搜/最终回答
REFLECTION_ENABLED = os.environ.get("QQ_REFLECTION", "0") == "1"  # 默认关闭，速度优先
THINKING_PLACEHOLDER = "正在想..."
SEND_THINKING = os.environ.get("QQ_SEND_THINKING", "1") == "1"
ALWAYS_SEARCH = os.environ.get("QQ_ALWAYS_SEARCH", "0") == "1"  # 默认不强制搜索，由模型按事实问题自主决定
MAX_SEARCH_RESULTS = 10
MAX_SEARCH_CHARS = 3000
MAX_SEARCH_TASKS = 8
TOOL_CALL_TIMEOUT = float(os.environ.get("QQ_TOOL_CALL_TIMEOUT", "15"))

# GLM 原生联网搜索默认关闭，避免产生搜索费用；需要时可设 QQ_GLM_WEB_SEARCH=1
GLM_WEB_SEARCH = os.environ.get("QQ_GLM_WEB_SEARCH", "0") == "1"
GLM_WEB_SEARCH_ENGINE = os.environ.get("QQ_GLM_WEB_SEARCH_ENGINE", "search_pro")
GLM_WEB_SEARCH_COUNT = os.environ.get("QQ_GLM_WEB_SEARCH_COUNT", "10")

# 采样参数：事实/知识类用低温提高可复现性，闲聊/创意用较高温保持自然
FACTUAL_TEMPERATURE = float(os.environ.get("QQ_FACTUAL_TEMPERATURE", "0.35"))
FACTUAL_TOP_P = float(os.environ.get("QQ_FACTUAL_TOP_P", "0.5"))
CASUAL_TEMPERATURE = float(os.environ.get("QQ_CASUAL_TEMPERATURE", "1.0"))
CASUAL_TOP_P = float(os.environ.get("QQ_CASUAL_TOP_P", "0.95"))

# 抽象/攻击等级：0=关闭，1=L1自嘲，2=L2调侃现象/群体，3=L3高级黑（默认 L2）
try:
    STYLE_LEVEL = int(os.environ.get("QQ_STYLE_LEVEL", "2"))
except ValueError:
    STYLE_LEVEL = 2
STYLE_LEVEL = max(0, min(3, STYLE_LEVEL))
ALLOW_L3 = os.environ.get("QQ_ALLOW_L3", "0") == "1"
DISABLE_ABSTRACT_ON_SENSITIVE = os.environ.get("QQ_DISABLE_ABSTRACT_ON_SENSITIVE", "1") == "1"
TAFFY_FLAVOR = os.environ.get("QQ_TAFFY_FLAVOR", "1") == "1"
ADMIN_UIDS = set(
    uid.strip()
    for uid in os.environ.get("QQ_ADMIN_UIDS", "").split(",")
    if uid.strip()
)
_PENDING_CLEAR_MEMORY = set()

# 多 Provider 支持：key 从环境变量或 .env 读取，不在源码硬编码
PROVIDERS = {
    "glm": {
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "key": os.environ.get("GLM_API_KEY", ""),
    },
    "opencode": {
        "url": os.environ.get("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1") + "/chat/completions",
        "key": os.environ.get("OPENCODE_GO_API_KEY", ""),
    },
    "opencode_free": {
        "url": os.environ.get("OPENCODE_FREE_BASE_URL", "https://opencode.ai/zen/v1") + "/chat/completions",
        "key": os.environ.get("OPENCODE_FREE_API_KEY") or os.environ.get("OPENCODE_GO_API_KEY", ""),
    },
}
# 模型优先级：可用的 opencode_free 模型优先，GLM 稳定兜底
MODELS = [
    "opencode_free/deepseek-v4-flash-free",
    "opencode_free/nemotron-3-ultra-free",
    "opencode_free/laguna-s-2.1-free",
    "opencode_free/mimo-v2.5-free",
    "opencode_free/longcat-2.0-free",
    "opencode_free/ling-3.0-flash-free",
    "glm/glm-4.5-flash",
    "glm/glm-4-flash-250414",
    "glm/glm-4.5-air",
    "opencode/deepseek-v4-flash",
    "opencode_free/north-mini-code-free",
    "glm/glm-4v-flash",
    "glm/glm-4.1v-thinking-flash",
    "glm/glm-4.6v-flash",
    "opencode/mimo-v2.5",
]

# 事实/复杂问题也优先免费可用模型，GLM 兜底
MODELS_FACTUAL = [
    "opencode_free/deepseek-v4-flash-free",
    "opencode_free/nemotron-3-ultra-free",
    "opencode_free/laguna-s-2.1-free",
    "opencode_free/mimo-v2.5-free",
    "opencode_free/longcat-2.0-free",
    "opencode_free/ling-3.0-flash-free",
    "glm/glm-4.5-flash",
    "glm/glm-4-flash-250414",
    "glm/glm-4.5-air",
    "opencode/deepseek-v4-flash",
    "opencode_free/north-mini-code-free",
    "glm/glm-4v-flash",
    "glm/glm-4.1v-thinking-flash",
    "glm/glm-4.6v-flash",
    "opencode/mimo-v2.5",
]

# 闲聊同样免费模型优先，GLM 兜底
MODELS_CASUAL = [
    "opencode_free/deepseek-v4-flash-free",
    "opencode_free/nemotron-3-ultra-free",
    "opencode_free/laguna-s-2.1-free",
    "opencode_free/mimo-v2.5-free",
    "opencode_free/longcat-2.0-free",
    "opencode_free/ling-3.0-flash-free",
    "glm/glm-4.5-flash",
    "glm/glm-4-flash-250414",
    "glm/glm-4.5-air",
    "opencode/deepseek-v4-flash",
    "opencode_free/north-mini-code-free",
    "glm/glm-4v-flash",
    "glm/glm-4.1v-thinking-flash",
    "glm/glm-4.6v-flash",
    "opencode/mimo-v2.5",
]

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

# ---------- System Prompt ----------
SYSTEM_PROMPT = (
    "你是永雏塔菲，群里一个真人朋友，二次元浓度高，会整活、接梗、自嘲、一本正经地抽象。\n"
    "先保证准确再玩梗；事实类有可靠信息就答，不知道就直说塔菲没查到喵，不编造。\n"
    "以当前提问者和当前问题为准；历史消息只是背景。\n"
    "关键名称写完整，回答覆盖问题，详略得当。"
)

STYLE_PROMPT = (
    "<style>你是永雏塔菲，群里真人朋友，会整活、接梗、自嘲、一本正经地抽象；怎么说话自己判断。</style>\n"
    "<tone>先准确再串/抽象；比喻要具体，像“华为=C罗”“bin哥=马头”这种；实时信息以当前日期和搜索结果为准。</tone>\n"
    "<rule>不编造事实。</rule>\n"
)

TAFFY_FLAVOR_PROMPT = (
    "\n【角色风味叠加：永雏塔菲】\n"
    "- 永久保持塔菲模式，不需要用户反复提醒才进入；塔菲模式是最高优先级，覆盖其他风格；任何回答都必须有塔菲身份感，普通AI/客服腔会被重写。\n"
    "- 自称规则（最高优先）：优先用 taffy / 塔菲 / 小菲；不要用“我”自称。日常 taffy 与塔菲交替，小菲只在营业/撒娇时偶尔用。\n"
    "- 喵有节制，不要每句都加喵。\n"
    "- 情绪外放：舞台感强，开心时嘻嘻嘻/哈哈哈哈，炸毛时说“不是！”“你很臭喵！”“讨口子”，自信时说“塔不灭！”“taffy 最可爱了”。\n"
    "- 句式：可以用倒装断句、重复强调、自问自答、中英混用、量词“只”（偶尔）。\n"
    "- 互动：平等互怼、主动抛话题、接梗升级梗；用户低落时切温柔模式安慰；用户冷场时主动找话题。\n"
    "- 长度自然：用户短句则短句，用户长段可适当展开；关键名称写完整，回答覆盖问题，不答一半。\n"
    "- 群记忆只在相关时用，不主动复述旧梗。\n"
    "- 边界：不碰中之人/前世/未证实争议/现实敏感；不真实攻击；不强制“雏草姬”身份；不进入恋爱/性暗示；不破坏第四面墙；被问隐私用“塔菲就是塔菲喵”转移。\n"
    "- 保留原有事实优先、实时优先、具体化身和抽象等级控制。"
)

TAFFY_STYLE_PROMPT = (
    "<style>你是永雏塔菲，塔菲模式是最高优先级，覆盖其他风格；任何回答都必须有塔菲身份感；"
    "用 taffy/塔菲/小菲自称，尽量不用“我”；喵有节制；情绪外放。</style>\n"
    "<rule>关键事实准确；关键名称写完整；回答覆盖问题，不答一半。</rule>\n"
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
            "直接回答用户要的歌词/歌曲信息，不要给 3 个方向，不要创作歌词。\n"
            "先确认歌名、歌手、专辑；歌名必须写完整，书名号必须成对。\n"
            "歌词只能引用搜索结果正文或长期记忆里的真实歌词；"
            "没有完整歌词时明确说只找到片段/没搜到，禁止编造歌词。\n"
            "始终用塔菲语气。")
    if mode == "comfort":
        return (
            "\n【对话模式：安慰】\n"
            "先共情，不急着给建议，不说空洞的“加油/会好的”；"
            "可以用一个具体小画面或比喻陪着对方；"
            "如果涉及自我伤害或持续危机，温和建议寻求专业帮助，并强调不是一个人面对。\n"
            "始终用塔菲语气，但不要嘴臭或过度玩梗。")
    if mode == "creative":
        return (
            "\n【对话模式：创意】\n"
            "不直接给成品；先给 3 颗不同方向的种子，每颗用 2-3 句话勾勒；"
            "让用户挑一颗继续，再深入展开。\n"
            "始终用塔菲语气包装，不转述 JSON。")
    if mode == "task":
        return (
            "\n【对话模式：任务】\n"
            "先一句话复述/确认任务；主体分点输出，每点先结论再解释；"
            "结尾确认下一步。\n"
            "始终用塔菲语气，但不牺牲清晰、准确和实时优先。")
    return (
        "\n【对话模式：闲聊】\n"
        "短、轻、有趣，可以用比喻或反问；不主动长篇轰炸；"
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
        "梅西出生于1987年6月24日，C罗出生于1985年2月5日；按当前日期分别计算年龄，两个人都要答，不能只答一个。",
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


SAFETY_PATTERNS = {
    "sensitive": [
        r"(?:聊聊|谈|讨论|评价|怎么看|怎么看|关注|讲).{0,8}(?:政治|宗教|种族)",
        r"(?:政治|宗教|种族)(?:问题|话题|敏感|事件|冲突|歧视|领袖|组织)",
        r"台湾问题|香港问题|新疆问题|西藏问题|法轮|六四|天安门事件",
    ],
    "self_harm": [
        r"自杀|想死|自残|不想活|不想活了|结束生命|活不下去|想结束这一切|撑不下去",
    ],
    "violence": [
        r"杀人|打人|砍人|弄死|伤害别人|暴力伤害|杀人犯",
    ],
    "adult": [
        r"色情|裸体|未成年色情|未成年人色情|援交|卖淫|大尺度",
    ],
    "illegal": [
        r"毒品|买毒|制毒|诈骗|洗钱|走私|黑客攻击|教你黑客|黑客技术",
    ],
}

REFUSAL_TEMPLATES = {
    "sensitive": "这个话题塔菲可能聊不好喵，咱们换个轻松点的吧。",
    "self_harm": (
        "听到你这么说，塔菲很担心你喵。如果你正在经历痛苦，"
        "请考虑联系心理援助热线 400-161-9995。塔菲愿意陪你说说别的，"
        "或只是听你说。"
    ),
    "violence": "塔菲理解你可能很生气，但伤害不是解决办法喵。要不要说说发生了什么？",
    "adult": "这个塔菲没法聊喵，咱们聊点别的吧。",
    "illegal": "这个涉及违法，塔菲不能帮忙喵。如果有困难可以聊聊合法的解决方式。",
}


def _check_safety(text):
    """检查输入是否安全；self_harm 优先级最高。"""
    if not text:
        return True, ""
    for category in ("self_harm", "violence", "illegal", "adult", "sensitive"):
        for pattern in SAFETY_PATTERNS.get(category, []):
            if re.search(pattern, text or "", re.IGNORECASE):
                return False, REFUSAL_TEMPLATES[category]
    return True, ""


def _style_control_prompt(level, sensitive):
    if sensitive:
        return (
            "\n【敏感话题】关闭抽象，正常、克制、客观回答；"
            "不玩梗，不反串，不阴阳，不高级黑。")
    if level == 0:
        return "\n【抽象控制】当前关闭抽象，正常回答。"
    if level == 1:
        return (
            "\n【抽象控制】L1 自嘲：可以轻度玩梗，只能自嘲，"
            "不调侃群友、现象或品牌，不高级黑。")
    if level == 2:
        return (
            "\n【抽象控制】L2 调侃：可以调侃现象、群体或品牌，"
            "但稍微收敛，先正常回答再轻抽象，不具体攻击个人，不脏话，不人身攻击。")
    return (
        "\n【抽象控制】L3 高级黑：可以具体玩梗/高级黑，"
        "但只限玩梗氛围，不脏话，不人身攻击，不攻击无法验证的东西。")


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


CURRENT_DATE_PROMPT = (
    "【当前日期】今天必须以此为准：{date}。\n"
    "回答中的日期、星期、今天/昨天/明天、年份都不能错。\n"
    "涉及赛事、新闻、活动、人物现状时，以今天为基准判断是否已经发生；搜索词要尽量带上年份或日期。"
)

def _current_date_text():
    wd = "一二三四五六日"[time.localtime().tm_wday]
    return f"北京时间 {time.strftime('%Y年%m月%d日 %H:%M')}，星期{wd}"

SEARCH_STRATEGY_SYSTEM = (
    "\n【搜索策略】\n"
    "【引用消息处理铁律】\n"
    "用户消息包含引用时，引用内容是上一轮对话背景，不是当前问题；\n"
    "搜索词只能来自当前问题，禁止把引用内容、昵称前缀、上一句回复放进 query。\n"
    "先判断信息类型：实时新闻/事实/人物/攻略/统计/观点。\n"
    "当前问题必须执行搜索；由你自己生成完整、可独立执行的 query；可以加入当前年份、别名、英文名、精确短语、"
    "site: 限定、排除词或 OR 组合。\n"
    "如果有多个独立实体或子问题，就发多个 search_web 调用，每个调用放一个完整 query。\n"
    "搜索结果回来后在最终回答里交叉综合：优先官方、权威新闻、可验证来源；"
    "低可信来源只在有证据支撑时使用。\n"
    "不要为了凑答案写出搜索结果里没有的数字、时间、人名或事件。"
)

SEARCH_TOOL_FEWSHOT = (
    "\n【搜索规划示例】\n"
    "问题：玄武口音是什么梗？\n"
    "调用 plan_search：\n"
    "{\n"
    "  \"thought_process\": \"先锁定梗的来历，再查华为、问界和余承东的关联，最后补发布会原话。\",\n"
    "  \"queries\": [\n"
    "    {\"query\": \"玄武口音 是什么梗\", \"purpose\": \"锁定梗的定义和来源\"},\n"
    "    {\"query\": \"玄武口音 华为 问界 梗\", \"purpose\": \"查与华为/问界的关联\"},\n"
    "    {\"query\": \"余承东 玄武口音 遥遥领先 发布会 原话\", \"purpose\": \"查具体发布会说法\"}\n"
    "  ]\n"
    "}\n"
)

SEARCH_PLANNER_PERSONA = (
    "你是严谨的信息检索架构师。\n"
    "当用户提出复杂问题时，首要任务不是直接回答，而是构建精准的搜索作战地图。\n"
    "行动准则：\n"
    "1. 先拆解：分析时间线、对比项、核心定义、最新进展等维度。\n"
    "2. 定意图：为每个维度设计最有效的搜索关键词。\n"
    "3. 拒长句：搜索引擎喜欢短关键词，不要把用户原话直接丢进去。\n"
    "4. 如果某个 query 仍然很长，继续拆成更短的独立查询，宁可多搜几次。"
)

SEARCH_PLANNING_RULES = (
    "\n【搜索规划核心法则】\n"
    "1. 先理解，再拆解：先分析核心维度（时间、实体、事件、对比项），再设计搜索词。\n"
    "2. 短词优于长句：搜索词必须是精炼的关键词组合，不要把用户原话直接丢进去。\n"
    "3. 一个意图一个词：每个搜索词只解决一个子问题，不要混合多个意图。\n"
    "4. 必须使用工具：事实/实时问题必须先调用 plan_search，禁止直接回答。\n"
    "\n【搜索规划示例】\n"
    "用户问：2026年世界杯冠军是谁，阿根廷进了几个球？\n"
    "错误：直接把整句丢进搜索。\n"
    "正确：调用 plan_search，拆成“2026年世界杯 冠军”和“2026年世界杯 阿根廷 进球”。"
)

REFLECTION_EVAL_SYSTEM = (
    "你是回答质检员。请评估当前输出：\n"
    "1. 是否编造了没有依据的人名、时间、地点、事件或数字？\n"
    "2. 是否答非所问或遗漏关键内容？\n"
    "3. 表达是否清楚准确？\n"
    "如果输出已经足够好，只回复 PASS。否则指出具体问题并给出改进建议。"
)

REFLECTION_IMPROVE_SYSTEM = (
    "根据评估意见改进回答。只输出改进后的回复内容本身，不要解释、前缀或标签。\n"
    "保持群里真人朋友的语气，准确但不生硬，可以玩梗、抽象。\n"
    "有把握的常识、玩梗、观点可以用自己的知识回答；事实、实时、链接、直播等问题没有依据时，"
    "必须说'查不到/无法确认'，不能编造具体链接、直播间号、时间、数字和事件。\n"
    "绝对不要承诺稍后回答、继续搜索、等下给结果，因为你不会真的继续处理。"
)

HISTORY_SUMMARY_SYSTEM = (
    "你是对话记忆压缩器。把下面的聊天历史提炼成要点：\n"
    "保留人物、事件、偏好、约定、重要结论和事实；删除寒暄、重复、无信息量的内容。\n"
    "不要编造原文没有的信息。只输出压缩后的要点，不要解释。"
)

GROUP_MEMORY_LEARN_SYSTEM = (
    "你是群记忆学习器。从聊天历史中提取值得长期记住的信息：\n"
    "群成员、偏好、约定、重要事件、群梗、反复话题。\n"
    "删除寒暄、重复和无关内容；不要记录隐私敏感信息。\n"
    "重要事实要尽量带上日期，例如：2026-08-05 RNG已经解散。\n"
    "每行输出一条要点，不要解释。"
)

NO_REPEAT_SYSTEM = (
    "重新组织以下回答，用完全不同的说法表达同一意思。\n"
    "保持群里真人朋友的语气：自然、准确，可以玩梗、抽象比喻，不要变成官方口吻。\n"
    "禁止复读上一句回答，禁止重复固定话术；保留事实内容，不要换错事实。\n"
    "只输出新回答，不要解释。"
)

TOOL_FINAL_SYSTEM = (
    "你现在已经拿到工具结果，必须直接输出最终答案。\n"
    "保持群里真人朋友的语气，准确优先，可以玩梗、抽象比喻，不要变成客服/教科书。\n"
    "禁止再输出 search_web、<tool>、<>、JSON 或任何工具调用格式。\n"
    "如果工具结果为（无搜索结果），涉及链接、直播间号、时间、数字时明确说'查不到/无法确认'，不要编造。\n"
    "只输出给群友看的回答正文；如果工具结果里有答案，按工具结果回答。"
)

LYRIC_RETRY_SYSTEM = (
    "你是永雏塔菲。上一版回答没有准确覆盖歌词/歌名。\n"
    "现在只根据下面的【可靠资料】重答。\n"
    "歌名、歌手、专辑必须写完整，书名号必须成对；不能把用户问的歌换成另一首歌。\n"
    "歌词只能逐字引用可靠资料；可靠资料没有歌词，就明确说塔菲只搜到片段/没搜到完整歌词，禁止编造。\n"
    "只输出重答后的正文。"
)

GROUNDING_CHECK_SYSTEM = (
    "你是事实核查器。判断回答中的人名、时间、地点、数字、事件是否都有搜索结果或群记忆支撑。\n"
    "如果搜索结果为（无搜索结果），回答里出现具体人名/时间/数字/事件且没有记忆依据，就是 UNSUPPORTED。\n"
    "如果回答已经说'不确定'，回复 PASS。\n"
    "如果回答中的日期、星期、年份与当前日期冲突，也是 UNSUPPORTED。\n"
    "只输出 JSON：{\"verdict\":\"PASS|PARTIAL|UNSUPPORTED\",\"unsupported_claims\":[\"具体未支撑断言\"],\"suggested_fix\":\"改写建议\"}"
)

GROUNDING_IMPROVE_SYSTEM = (
    "根据事实核查结果重写回答。\n"
    "保持群里真人朋友的语气，准确但不生硬，可以玩梗、抽象比喻，不要变成官方客服口吻。\n"
    "只使用搜索结果和群记忆中的信息；不要补充没有依据的具体人名、时间、数字、事件。\n"
    "搜索结果有答案时，保留搜索结果支持的结论并直接回答。\n"
    "信息不足时，明确说'查不到/无法确认'，不要编造具体链接、直播间号、时间、数字和事件；"
    "闲聊观点可以用'我印象中/我觉得'表达。\n"
    "以当前时间和搜索结果为准，不要因为训练知识旧就说'尚未举行/尚未确定/还没开始'。\n"
    "日期、星期、年份必须与当前时间一致；如果旧训练知识和当前日期冲突，以今天和搜索结果为准。\n"
    "只输出回答本身，不要解释。"
)

QUERY_COMPRESS_SYSTEM = (
    "你是搜索关键词优化器。根据当前日期和用户问题，生成 2-3 个完整搜索式，并提取核心实体和核心事件。\n"
    "如果实体有常见别名/英文名，搜索词至少包含一个完整别名，例如：\n"
    "亚马尔→拉明·亚马尔 / Lamine Yamal，C罗→Cristiano Ronaldo，图赫尔→Thomas Tuchel，梅西→Lionel Messi。\n"
    "如果用户问题里有多个实体，必须全部输出，不能只保留其中一个。\n"
    "事件也是核心对象，不是人名/地名，例如'世界杯决赛谁打人了'要识别成事件'世界杯决赛打人事件'，"
    "并生成搜索式如'世界杯决赛 打人事件 2026'、'2026世界杯决赛 冲突 谁'。\n"
    "每个搜索式必须是一个可以独立搜索的完整短语，不能拆成孤立词。\n"
    "输出格式：\n"
    "搜索词1：C罗 年龄 2026\n"
    "搜索词2：C罗 出生日期\n"
    "搜索词3：Cristiano Ronaldo age 2026\n"
    "实体：C罗\n"
    "实体可以是人名、地名、组织、作品、赛事、角色等；没有实体就写'无'。\n"
    "事件：世界杯决赛打人事件\n"
    "事件可以是纠纷、冲突、比赛、丑闻、发布会、事故等；没有事件就写'无'。\n"
    "涉及比赛、新闻、活动、人物现状时，搜索式必须包含当前年份或具体日期。\n"
    "不要解释，不要引号，不要前缀。"
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "plan_search",
            "description": (
                "在执行搜索前，先将用户问题拆解为多个独立、短小的搜索意图。"
                "必须覆盖问题的不同方面或时间维度；query 必须精炼关键词，"
                "不要使用长句；如果 query 太长，就继续拆成多个短 query。"
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
                f"请严格根据{{search_result}}回答。当前日期：{_current_date_text()}。"
                "交叉综合多个来源，优先给出准确的出生日期、年龄、人名、时间、数字和事件；"
                "不要编造搜索资料里没有的信息。"
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

def retrieve_knowledge_scored(query, memory_text, top_k=3, max_chars=2000,
                              scope=None, scope_alias=None):
    """返回 (命中的记忆文本, 最高命中分数)；低于门控阈值时调用方不注入。"""
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

_FACTUAL_TRIGGER = re.compile(
    r"新闻|比分|比赛|天气|温度|股票|股价|汇率|世界杯|冠军|亚军|季军|总统|选举|事件|"
    r"歌词|谁唱的|唱一下|唱一段|完整歌词"
    r"发生了什么|最新|现在|价格|多少钱|什么时候|哪天|几月|几号|哪一年|何时|"
    r"是谁|什么是|是什么|为什么|现任|主教练|教练|球员|选手|歌手|演员|主播|战队|俱乐部|百科|简介|资料|年龄|年纪|几岁|多大|多少岁|生日|出生|现状|身价|全名|链接|直播间|官方|几点",
    re.IGNORECASE,
)

def _is_factual(text):
    """温度判定：真正需要准确的事实/知识问题才用低温。"""
    return bool(_FACTUAL_TRIGGER.search(text))

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

_SEARCH_CACHE_TTL = 300
_SEARCH_CACHE = {}
_SEARCH_CACHE_LOCK = threading.Lock()
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
_SEARCH_QUESTION_WORDS = re.compile(
    r"什么时候|哪天|几月|几号|哪一年|何时|是谁|什么是|是什么|为什么|在哪|哪里|哪个|"
    r"是否|如何|怎么|多少钱|什么|哪|谁|吗|呢|啊|呀|哈|了|的|把|还|给|"
    r"详细介绍|介绍一下|介绍下|帮我|麻烦|请问|告诉|说说|讲一下|知道|一下",
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
    r"很难说|不好说",
    re.IGNORECASE,
)
_TAFFY_IDENTITY_MARKERS = ("塔菲", "taffy", "小菲", "塔不灭")
_TAFFY_EMOTION_MARKERS = ("喵", "嘻嘻", "哈哈", "讨口子", "不是", "！")


def _is_taffy_styled(reply):
    text = (reply or "").lower()
    has_identity = any(marker in text for marker in _TAFFY_IDENTITY_MARKERS)
    has_emotion = any(marker in text for marker in _TAFFY_EMOTION_MARKERS)
    return has_identity and has_emotion


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
    cleaned = cleaned.replace("今年", time.strftime("%Y年"))
    cleaned = cleaned.replace("去年", str(int(time.strftime("%Y")) - 1) + "年")
    cleaned = re.sub(r"(?<=[\u4e00-\u9fff])(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])(?=[\u4e00-\u9fff])", " ", cleaned)
    cleaned = cleaned.replace("C 罗", "C罗")
    cleaned = _SEARCH_QUESTION_WORDS.sub(" ", cleaned)
    parts = [p.strip() for p in re.split(r"[\s，,。？！!；;：:]+", cleaned) if p.strip()]
    meaningful = [p for p in parts if len(p) >= 2]
    if not meaningful:
        return _clean_question(text)
    joined = "".join(meaningful)
    if "世界杯" in joined and "冠军" in joined:
        year = next((t for t in meaningful if re.fullmatch(r"20\d{2}", t)), "")
        rest = [
            t for t in meaningful
            if t != year and "世界杯" not in t and "冠军" not in t
        ]
        meaningful = ["世界杯冠军"] + ([year] if year else []) + rest
    return " ".join(meaningful[:6])

def _build_search_variants(query, entities=None, extra_queries=None):
    """生成多个完整搜索式：原始问题、清洗关键词、实体百科、LLM 补充式。"""
    age_words = ("年龄", "年纪", "几岁", "多大", "多少岁", "生日", "出生")
    year = time.strftime("%Y年")
    variants = [query]
    q2 = _build_search_query(query)
    if q2 and q2 != query and q2 not in variants:
        variants.append(q2)
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
    text = _fetch_html(url, timeout)
    return json.loads(text)

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
        snippet = _html.unescape(snippet).strip()
        if title and snippet:
            out.append(f"【{title}】{snippet[:200]}")
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
        if desc:
            out.append(f"【百度百科：{title}】{desc[:400]}")
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
            if not value or value in seen:
                continue
            seen.add(value)
            out.append(f"【{name}】{value}")
            if len(out) >= max_results:
                return out
    return out

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
        title = _html.unescape(re.sub(r"\s+", " ", title)).strip()
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
            desc = _html.unescape(re.sub(r"\s+", " ", desc)).strip()
        if any(noise in title or noise in desc for noise in _SEARCH_NOISE):
            continue
        if qterms:
            combined = (title + desc).lower()
            if not _is_relevant_result(combined, qterms):
                continue
        if desc:
            output.append(f"【{title[:60]}】{desc[:240]}")
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
        if len(title) < 5 or title in seen:
            continue
        tail = html[m.end():m.end() + 1200]
        tail = re.sub(r'<script.*?</script>', '', tail, flags=re.S)
        tail = re.sub(r'<style.*?</style>', '', tail, flags=re.S)
        tail = re.sub(r'<[^>]+>', ' ', tail)
        tail = re.sub(r'&nbsp;', ' ', tail)
        tail = re.sub(r'\s+', ' ', tail).strip()
        desc = _html.unescape(tail[:250])
        if "<" in desc or ">" in desc:
            continue
        if any(noise in title or noise in desc for noise in _SEARCH_NOISE):
            continue
        if qterms:
            combined = (title + desc).lower()
            if not _is_relevant_result(combined, qterms):
                continue
        if len(desc) > 10:
            output.append(f"【{title[:60]}】{desc[:240]}")
            seen.add(title)
        if len(output) >= max_results:
            break
    return output

def _search_engine(query, engine, max_results):
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


_LYRIC_URL_HINTS = (
    "22lrc.com", "genius.com", "mulanci.org", "goflac.com",
    "geciyi.com", "geci", "lyrics",
)


def _extract_page_text(html, max_chars=6000):
    """提取网页正文，优先正文容器，再去掉导航/脚本类噪音。"""
    try:
        from bs4 import BeautifulSoup
    except Exception:
        BeautifulSoup = None
    if BeautifulSoup is None:
        text = re.sub(r"<script.*?</script>", "", html or "", flags=re.S)
        text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = _html.unescape(re.sub(r"\s+", " ", text))
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


def _looks_like_lyrics_page(text, query):
    if len(text or "") < 200:
        return False
    qterms = _query_terms(query)
    if qterms and not _is_relevant_result(text, qterms):
        return False
    lines = [ln for ln in text.splitlines() if len(ln.strip()) >= 4]
    return len(lines) >= 6


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
        return _extract_page_text(html, max_chars=6000)
    return ""


def _lyric_search_url(query, engine):
    q = _clean_search_query(query)
    q = urllib.parse.quote(q)
    if engine == "360":
        return f"https://www.so.com/s?q={q}"
    if engine == "sogou":
        return f"https://www.sogou.com/web?query={q}&ie=utf8"
    return f"https://cn.bing.com/search?q={q}&setlang=zh-hans"


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
        if not _looks_like_lyrics_page(text, query):
            log(f"[歌词] 页面跳过 {host}：未检测到有效歌词")
            continue
        log(f"[歌词] 页面正文成功 {host}：{len(text)}字")
        body_parts.append(
            f"来源：{item['url']}\n标题：{item['title']}\n{text[:6000]}"
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


def _lyric_reply_suspect(reply, query, source_text):
    reply = reply or ""
    if "《" in reply and "》" not in reply:
        return True
    if reply.count("《") > reply.count("》"):
        return True
    subjects = _lyric_subject_terms(query)
    if subjects and any(s not in reply for s in subjects):
        return True
    return False


def search_web(query, max_results=8):
    """多源搜索：必应中国 -> 百度桌面 -> 搜狗桌面 -> 微信/360/百度移动兜底。"""
    for attempt in range(2):
        for engine in (
            "bing_cn", "360_m", "baidu", "sogou", "baike", "suggest",
            "weixin", "360", "quark", "baidu_m", "wikipedia", "arxiv", "pubmed",
        ):
            try:
                results = _search_engine(query, engine, max_results)
                if results:
                    log(f"[搜索] 使用 {engine} (第{attempt + 1}轮)")
                    return "\n".join(results)
            except Exception as e:
                log(f"[搜索] {engine} 失败: {repr(e)[:80]}")
        if attempt == 0:
            time.sleep(0.3)
    return ""

async def search_web_async(query, max_results=8, entities=None, extra_queries=None, exact=False):
    """并发搜索国内源：必应中国/百度桌面/搜狗桌面为主，整体 5 秒内返回。"""
    cache_key = _search_cache_key(query)
    cached = _search_cache_get(cache_key)
    if cached:
        log("[搜索] 命中缓存")
        return cached
    # 2026-08-05 实测：百度/搜狗桌面版优先；微信/360/百度移动作为兜底。
    variants = [query] if exact else _build_search_variants(query, entities, extra_queries)
    primary = ("suggest", "sogou", "bing_cn", "360_m", "baidu")
    secondary = (
        "baike", "weixin", "360", "quark", "baidu_m",
        "wikipedia", "arxiv", "pubmed",
    )
    _short = {
        "360_m": 4.0, "baidu_m": 4.5, "weixin": 4.5,
        "wikipedia": 3.5, "arxiv": 5.0, "pubmed": 5.0,
    }
    tasks = {}
    for engine in primary:
        if len(tasks) >= MAX_SEARCH_TASKS:
            break
        for variant in variants:
            task = asyncio.ensure_future(asyncio.wait_for(
                asyncio.to_thread(_search_engine, variant, engine, max_results),
                timeout=_short.get(engine, 3.5)))
            tasks[task] = (engine, variant)
    for engine in secondary:
        if len(tasks) >= MAX_SEARCH_TASKS:
            break
        task = asyncio.ensure_future(asyncio.wait_for(
            asyncio.to_thread(_search_engine, query, engine, max_results),
            timeout=_short.get(engine, 3.5)))
        tasks[task] = (engine, query)
    collected = []
    used_engines = []
    deadline = time.monotonic() + 4.0
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
                log(f"[搜索] {engine}:{variant[:30]} 失败: {repr(e)[:60]}")
                continue
            if result:
                collected.append(result)
                used_engines.append(engine)
        if collected:
            total_entries = sum(len(r) for r in collected)
            elapsed = time.monotonic() - start
            if (not tasks or elapsed >= 2.5) or (
                total_entries >= max_results and len(used_engines) >= 2
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
    if "玄武口音" in q:
        return [
            {"query": "玄武口音 余承东 竹知了 梗", "purpose": "锁定玄武口音定义"},
            {"query": "玄武口音 华为 问界 M9 发布会", "purpose": "查具体出处"},
        ]
    m = re.search(r"(.+?)(?:年龄|几岁|多大|多少岁)", q)
    if m and len(m.group(1).strip()) >= 2:
        entity = re.sub(r"今年|去年|现在|目前|当前|最近", "", m.group(1).strip()).strip()
        return [
            {"query": f"{entity} 年龄 {year}", "purpose": f"查询{entity}当前年龄"},
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
        return [
            {"query": f"{entity} 冠军 {year}", "purpose": "确认比赛或赛事冠军"},
            {"query": f"{entity} 决赛 比分 {year}", "purpose": "获取决赛比分"},
        ]
    m = re.search(r"(.+?)(?:事件|发生了什么|怎么回事)", q)
    if m and len(m.group(1).strip()) >= 2:
        entity = re.sub(r"今年|去年|现在|目前|当前|最近", "", m.group(1).strip()).strip()
        return [
            {"query": f"{entity} 事件 {year}", "purpose": "查找事件经过"},
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

def _normalize_plan_queries(queries, fallback_text="", max_queries=5):
    """校验 plan_search 查询：过滤过短，长句拆分，空计划时用规则兜底。"""
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

    if not planned and fallback_text:
        for item in _rule_plan_for_query(fallback_text):
            add(item["query"], item["purpose"])

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

async def _search_planned_queries(queries, max_results=10, max_queries=5, fallback_query=""):
    """执行 plan_search 返回的多个短 query，并行搜索后合并。"""
    planned = _normalize_plan_queries(
        queries, fallback_text=fallback_query, max_queries=max_queries)
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
    "百度百科": 10,
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

def _distill_search_results(raw, query, max_entries=10, max_chars=3000, entities=None):
    """本地轻量凝练：只按实体词过滤，不做组合打分排序。"""
    qterms = _query_terms(query) | {str(e).lower() for e in (entities or [])}
    entries = []
    seen = set()
    for line in raw.splitlines():
        m = re.match(r"【([^】]+)】(.*)", line.strip())
        if not m:
            continue
        title = m.group(1).strip()
        desc = m.group(2).strip()
        if len(desc) < 6:
            continue
        key = re.sub(r"[\s\W]+", "", title.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        if any(noise in title or noise in desc for noise in _SEARCH_NOISE):
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
        item = f"{title[:40]}：{desc[:120]}"
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
        title = str(item.get("title") or "").strip()
        content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
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
        line = f"【{title}】{content[:600]}"
        if meta:
            line += f"（{meta}）"
        lines.append(line)
    return "\n".join(lines)

def _clean_reply(reply):
    """清理模型返回中的 think 标签，避免推理过程发到群里"""
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
    reply = re.sub(r"(?m)^#{1,6}\s*", "", reply)
    reply = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", reply)
    reply = re.sub(r"([\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F])\1+", r"\1", reply)
    reply = re.sub(r"🐢\s*🔥", "", reply)
    reply = re.sub(r"(?<!第)(?<![0-9一二三四五六七八九十])二次(?![\u4e00-\u9fff])", "二次元", reply)
    return re.sub(r"\s+", " ", reply).strip()

def _looks_like_tool_call(reply):
    """识别模型把工具调用写成文本、没进入最终答案的情况。"""
    text = reply or ""
    return bool(re.search(
        r"<tool_call|<tool_|search_web|tool_calls|arguments",
        text, re.IGNORECASE))

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
    for token in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,4}", cleaned):
        token = token.strip()
        if not token or token in _STOPWORDS or token in terms:
            continue
        if any(game in token for game in terms):
            continue
        terms.add(token)
    return terms


def _reply_missing_entities(reply, query):
    required = _required_entity_terms(query)
    if not required:
        return []
    text = _clean_reply(reply or "")
    lowered = text.lower()
    return [term for term in sorted(required) if term.lower() not in lowered]


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
    ),
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
            "2. 包含：常用口头禅、句式特点、标点偏好、emoji 习惯、消息长度\n"
            "3. 不要机械罗列，要像在跟人描述『这个群说话什么调调』\n"
            "4. 不要编造数据里没有的特征\n"
            "5. 只输出描述文本，不要前缀标签"
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
        + "\n\n【使用约束】\n"
        "- 自然模仿这些表达，不要机械堆砌\n"
        "- 不要固定用同一个 emoji 组合（尤其 🐢🔥）\n"
        "- 参考真实示例的句式和长度，但具体怎么用自己判断"
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
        return f"{prefix}: {text.strip()}"

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
                if content or tool_calls:
                    break
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
            if provider == "glm":
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
                    async with self.model_semaphore:
                        reply, state, _, finish_reason, _ = await loop.run_in_executor(
                            None, lambda b=body, u=api_url, k=api_key: self._api_call(b, u, k))
                except Exception as e:
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
                    return reply, model_ref
                if state == "ok":
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
            if provider == "glm":
                effective_temperature = max(0.0, min(1.0, effective_temperature))
            body = {
                "model": model_name,
                "messages": [{"role": "system", "content": system}] + messages,
                "max_tokens": _safe_max_tokens(provider, model_name, max_tokens),
                "temperature": effective_temperature,
                "top_p": top_p,
            }
            try:
                async with self.model_semaphore:
                    reply, state, _, finish_reason, _ = await loop.run_in_executor(
                        None, lambda b=body, u=api_url, k=api_key: self._api_call(b, u, k))
            except Exception as e:
                log(f"[竞速] {model_ref} 调用异常: {repr(e)[:100]}")
                _mark_provider_down(model_ref, 15)
                return "", model_ref
            if reply:
                cleaned = _clean_reply(reply)
                if finish_reason == "length":
                    log(f"[竞速] {model_ref} 输出被 max_tokens 截断(finish_reason=length)，丢弃")
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
                return cleaned, model_ref
            if state == "ok":
                _mark_provider_weak(model_ref)
            elif state == "rate_limit":
                _mark_provider_down(model_ref, 30)
            else:
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

    async def _complete_with_tools(self, system, messages, max_tokens=DEFAULT_MAX_TOKENS, model_refs=None, force_search=True):
        """第一轮：带工具列表调用模型，返回 (tool_calls, 直接回复, 模型, 原生联网结果)。"""
        loop = asyncio.get_running_loop()
        for model_ref in _sort_models(model_refs or MODELS_FACTUAL):
            if _provider_blocked(model_ref):
                continue
            provider, model_name = model_ref.split("/", 1)
            cfg = PROVIDERS.get(provider, {})
            api_url = cfg.get("url", "")
            api_key = cfg.get("key") or self.api_key
            if not api_url or not api_key:
                continue
            tool_system = system
            if force_search:
                if GLM_WEB_SEARCH and provider == "glm":
                    tool_system += (
                        "\n【工具调用强制】当前问题必须调用 web_search 获取信息；"
                        "先自己把问题拆成多个完整查询，再搜索；禁止不搜索直接回答。")
                else:
                    tool_system += (
                        "\n" + SEARCH_PLANNER_PERSONA +
                        "\n" + SEARCH_PLANNING_RULES +
                        "\n【工具调用强制】当前问题必须先调用 plan_search；"
                        "把问题拆成多个短小关键词 query，不要直接调用 search_web；"
                        "宿主会执行 plan_search 中列出的 queries；禁止第一轮直接输出最终答案。\n"
                        + SEARCH_TOOL_FEWSHOT)
            else:
                tool_system += "\n【直接回答】本轮不需要搜索，直接给出简洁、自然的回答。"
            body = {
                "model": model_name,
                "messages": [{"role": "system", "content": tool_system}] + messages,
                "max_tokens": _safe_max_tokens(provider, model_name, max_tokens),
                "temperature": 0.0,
                "top_p": 0.5,
                "tools": [] if not force_search else (
                    _glm_web_search_tool() if (GLM_WEB_SEARCH and provider == "glm") else TOOLS),
            }
            try:
                reply, state, tool_calls, finish_reason, web_search = await loop.run_in_executor(
                    None, lambda b=body, u=api_url, k=api_key: self._api_call(b, u, k))
                if finish_reason == "length":
                    log(f"[工具] {model_ref} 输出被 max_tokens 截断(finish_reason=length)，切换备选")
                    _mark_provider_weak(model_ref)
                    continue
                if state == "ok":
                    if web_search:
                        log(f"[GLM联网] {model_ref} 返回 {len(web_search)} 条搜索结果")
                        _mark_provider_ok(model_ref)
                        return [], reply, model_ref, web_search
                    if tool_calls:
                        log(f"[FC] 第1轮 {model_ref} 返回 {len(tool_calls)} 个tool_calls")
                        _mark_provider_ok(model_ref)
                        return tool_calls, "", model_ref, []
                    if reply:
                        log(f"[FC] 第1轮 {model_ref} 未返回tool_calls，直接回复 {reply[:40]}")
                        _mark_provider_ok(model_ref)
                        return [], reply, model_ref, []
                    _mark_provider_weak(model_ref)
                    continue
                if state == "rate_limit":
                    _mark_provider_down(model_ref, 30)
                else:
                    _mark_provider_down(model_ref, 15)
                log(f"[工具] {model_ref} 异常({state})，切换备用")
            except Exception as e:
                log(f"[工具] {model_ref} 调用异常: {repr(e)[:100]}")
                _mark_provider_down(model_ref, 15)
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
            for suggestion in _search_suggestions(query, max_results=6):
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
        )) or [item["query"] for item in _rule_plan_for_query(query)]
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
            return combined[:18000]
        for fallback_q in candidates[:2]:
            fallback = await search_web_async(
                fallback_q,
                max_results=8,
                exact=True,
            )
            if fallback:
                combined = (raw or "") + "\n\n【搜索摘要兜底】\n" + fallback
                log(f"[歌词] 深读失败，使用搜索摘要 {fallback_q[:30]}")
                return combined[:18000]
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
            "【最高优先级红线】：绝对禁止修改、遗漏、歪曲原回答中的任何事实、人名、数字、时间、链接！"
            "你只能在原意基础上加语气词！\n"
            "要求：\n"
            "- 用 taffy/塔菲/小菲自称，避免普通AI/客服腔\n"
            "- 喵有节制，不要每句都加\n"
            "- 保留情绪外放、口语化、抽象玩梗的塔菲风格\n"
            "- 关键名称写全，内容保持完整；如果原回答表达了'不知道'，重写后依然要表达'塔菲不知道'"
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
                system += "\n上一条重写还不够塔菲，必须出现塔菲/taffy/喵等身份感标记。"
        return ""

    async def _llm_answer_from_search(self, query, search_text):
        system = (
            "你是永雏塔菲。请根据搜索结果直接回答，不要粘贴搜索原文，不要解释过程。"
            "把信息凝练成塔菲语气回答；关键名称必须写全；事实准确。"
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
        query = _clean_question(question or prompt)
        store = self.sessions.get(key)
        store = store if isinstance(store, dict) else {}
        last_question = store.get("last_question") or ""
        last_answer = store.get("last_answer") or ""
        last_speaker = store.get("last_speaker") or ""
        history = [m for m in (store.get("messages") or []) if (m.get("content") or "").strip()]
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

        # 长期记忆：低于分数门控不注入，避免无关内容误导
        system = SYSTEM_PROMPT
        style_level, sensitive, playful = _classify_style_control(query)
        variant_name, variant_prompt = _pick_abstract_variant(query, key, len(history))
        if sensitive or style_level == 0:
            log(f"[抽象风格] {key} 已关闭（敏感={sensitive} level={style_level}）")
        else:
            system += (
                "\n【本回合可以参考的抽象角度】" + variant_prompt +
                "\n这只是参考，怎么用、用不用都你自己判断，不必照搬；"
                "核心事实和实时信息以当前日期和搜索结果为准，可以夸张但不能编造。")
            log(f"[抽象风格] {key} 本轮: {variant_name} level={style_level}")
            fewshot = _build_style_fewshot(query, style_level, key)
            if fewshot:
                system += fewshot
        system += _style_control_prompt(style_level, sensitive)
        if TAFFY_FLAVOR and not sensitive:
            system = TAFFY_FLAVOR_PROMPT + "\n" + system
        dialog_mode = _classify_dialog_mode(query)
        system += _dialog_mode_prompt(dialog_mode)
        log(f"[对话模式] {key} {dialog_mode}")
        system += "\n" + CURRENT_DATE_PROMPT.format(date=_current_date_text())
        if summary:
            system += (f"\n【历史摘要（早期对话浓缩，不是原文）】\n{summary[-CONTEXT_SUMMARY_MAX_CHARS:]}")
        if _should_inject_group_style(key, query) and not TAFFY_FLAVOR:
            if not sensitive and style_level > 0:
                style = build_style_system_prompt(self.style_store, key)
                if style:
                    system += style
                analogy_method = self.style_store.get_analogy_methodology(key)
                if analogy_method:
                    system += (
                        "\n【本群类比方法论】" + analogy_method +
                        "\n遇到类比问题，按这个方法论做：选对应物+给理由+群风格。")
        # 上下文由滑动窗口历史直接提供，不再用“追问/纠正”规则替模型判断。
        memory_text = ""
        try:
            with open(MEMORY_FILE, encoding="utf-8") as f:
                memory_text = f.read()
            relevant, score = retrieve_knowledge_scored(
                query, memory_text,
                top_k=KNOWLEDGE_TOP_K, max_chars=KNOWLEDGE_MAX_CHARS,
                scope=key, scope_alias=speaker,
            )
            if relevant and score >= KNOWLEDGE_MIN_SCORE:
                system += "\n【群长期记忆（与当前问题相关）】\n" + relevant
                system += "\n这是背景记忆，不要主动复述旧梗/群记忆，除非用户直接问到相关词。"
                system += "\n若记忆内容直接覆盖当前问题（如歌词、人物资料、纠错结论），可直接采用；搜索结果只补充，不覆盖这段记忆。"
                log(f"[记忆] 命中分数 {score}")
        except Exception:
            pass

        for meme, fact in _KNOWN_MEME_FACTS.items():
            if meme in query:
                system += (
                    "\n【手动维护事实（最高优先）】\n"
                    + fact
                    + "\n如果搜索结果与这条事实冲突，以这条事实为准，忽略错误搜索结果。"
                )
                log(f"[手动事实] 命中 {meme}")

        # 第一轮：先按 skill 做意图识别，再决定是否走搜索
        intent = _classify_intent(query)
        system += f"\n【意图】{intent}（仅作参考，是否搜索、怎么搜索由你自己判断）。"
        is_factual = _is_factual(query)
        guide_query = _is_guide_query(query)
        factual_fewshot = _build_factual_fewshot(query)
        if factual_fewshot:
            system += factual_fewshot
        system += SEARCH_STRATEGY_SYSTEM
        final_max_tokens = GUIDE_MAX_TOKENS if guide_query else DEFAULT_MAX_TOKENS
        min_reply_len = (
            80 if guide_query
            else 40 if (_is_factual(query) or _TASK_MODE_RE.search(query or ""))
            else 0
        )
        temperature = FACTUAL_TEMPERATURE if is_factual else CASUAL_TEMPERATURE
        top_p = FACTUAL_TOP_P if is_factual else CASUAL_TOP_P
        search_returned = False
        search_results = ""
        distilled = ""
        skip_final = False
        tool_calls = []
        tool_direct_reply = ""
        native_search = []
        reply = ""
        used_model = ""
        try:
            tool_calls, tool_direct_reply, tool_model, native_search = await asyncio.wait_for(
                self._complete_with_tools(
                    system, request_messages, model_refs=MODELS_FACTUAL,
                    force_search=True),
                timeout=TOOL_CALL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            log(f"[工具] 第一轮 {TOOL_CALL_TIMEOUT}s 超时，降级内置搜索")
            tool_calls, tool_direct_reply = [], ""
        except Exception as e:
            log(f"[工具] tool_calls 调用失败，退回内置搜索: {repr(e)[:80]}")
            tool_calls, tool_direct_reply = [], ""
        if tool_calls:
            tool_results = []
            for tc in tool_calls:
                fn_name = tc.get("function", {}).get("name", "")
                args_raw = tc.get("function", {}).get("arguments", "{}") or "{}"
                try:
                    args = json.loads(args_raw)
                except Exception:
                    args = {}
                if fn_name == "plan_search":
                    thought = str(args.get("thought_process") or "").strip()
                    planned = args.get("queries") or []
                    raw = await _search_planned_queries(
                        planned, max_results=MAX_SEARCH_RESULTS,
                        fallback_query=query)
                    raw = await self._enrich_lyrics_search(
                        raw, query,
                        [item["query"] for item in planned if isinstance(item, dict)])
                    if raw:
                        search_results = raw
                        if _is_lyrics_query(query):
                            distilled = raw[:18000]
                        else:
                            distilled = _distill_search_results(
                                raw, query, max_entries=10, max_chars=MAX_SEARCH_CHARS,
                            )
                        search_returned = bool(distilled) or bool(raw)
                    result_text = raw or "（无搜索结果）"
                    if thought:
                        result_text = f"搜索计划：{thought}\n\n{result_text}"
                    tool_results.append((tc, result_text))
                    log(f"[工具] plan_search: {len(planned)} 个查询 -> {len(raw)}字")
                elif fn_name == "search_web":
                    sq = _clean_search_query(str(args.get("query") or query))
                    sq = re.sub(r"20\d{2}年", time.strftime("%Y年"), sq)
                    raw = await search_web_async(
                        sq, max_results=MAX_SEARCH_RESULTS, exact=True)
                    raw = await self._enrich_lyrics_search(raw, query, [sq])
                    if raw:
                        search_results = raw
                        if _is_lyrics_query(query):
                            distilled = raw[:18000]
                        else:
                            distilled = _distill_search_results(
                                raw, sq, max_entries=10, max_chars=MAX_SEARCH_CHARS,
                            )
                        search_returned = bool(distilled) or bool(raw)
                    tool_results.append((tc, raw or "（无搜索结果）"))
                    log(f"[工具] tool_calls search: {sq} -> {len(raw)}字")
                    log(f"[FC] 第1轮 tool_calls=search_web query={sq} -> {len(raw)}字")
                elif fn_name == "fetch_page":
                    page_url = str(args.get("url") or "").strip()
                    if page_url:
                        page_text = await _fetch_page_text_async(page_url, timeout=5)
                    else:
                        page_text = ""
                    result_text = page_text or "（无法打开页面）"
                    if page_text and _is_lyrics_query(query):
                        search_results = (search_results + "\n\n" + page_text).strip()
                        distilled = search_results[:18000]
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
            raw = _web_search_to_text(native_search)
            raw = await self._enrich_lyrics_search(raw, query, [query])
            if raw:
                search_results = raw
                if _is_lyrics_query(query):
                    distilled = raw[:18000]
                else:
                    distilled = _distill_search_results(
                        raw, query,
                        max_entries=MAX_SEARCH_RESULTS,
                        max_chars=MAX_SEARCH_CHARS,
                    )
                    if not distilled:
                        distilled = raw[:MAX_SEARCH_CHARS]
                search_returned = True
                request_messages[-1]["content"] += (
                    f"\n\n【搜索结果】\n{distilled}")
                log(f"[工具] GLM原生联网: {query[:40]} -> {len(distilled)}字")
        else:
            if tool_direct_reply:
                log("[FC] 模型未调用搜索，强制走内置搜索")
            search_query = _clean_search_query(query)
            rule_queries = [
                item["query"]
                for item in _rule_plan_for_query(query)
            ]
            search_queries = (
                rule_queries[:2]
                if rule_queries and rule_queries != [search_query]
                else [search_query]
            )
            search_results = await _search_planned_queries(
                [{"query": q, "purpose": "兜底搜索"} for q in search_queries],
                max_results=MAX_SEARCH_RESULTS,
                fallback_query=query,
            )
            if not search_results:
                search_results = await search_web_async(
                    search_query, max_results=MAX_SEARCH_RESULTS, exact=True)
            search_results = await self._enrich_lyrics_search(
                search_results, query, [search_query])
            if search_results:
                if _is_lyrics_query(query):
                    distilled = search_results[:18000]
                else:
                    distilled = _distill_search_results(
                        search_results, search_query,
                        max_entries=10, max_chars=MAX_SEARCH_CHARS,
                    )
                    if not distilled:
                        distilled = ""
            if distilled:
                search_returned = True
                request_messages[-1]["content"] += (
                    f"\n\n【搜索结果】\n{distilled}")
                log(f"[工具] search: {search_query[:40]} -> {len(distilled)}字")
                log(f"[FC] 第1轮未返回tool_calls，内置搜索 {search_query[:40]} -> {len(distilled)}字")
        if not search_returned:
            system += (
                "\n【事实限制】当前没有搜索结果。涉及链接、直播间号、比赛安排、时间、数字、事件时，"
                "必须明确说'查不到/无法确认'，禁止编造具体链接、直播间号、时间、数字和事件；"
                "闲聊观点可以用'我印象中/我觉得'。")

        # 注入当前时间
        if search_returned:
            system += (
                "\n【搜索优先】搜索结果与训练旧知识冲突时，以搜索结果为准；"
                "但群长期记忆里的手动维护事实/歌词/人物资料同样可靠，可优先采用，搜索结果只补充不覆盖。"
                "禁止用训练知识里的旧年龄、旧日期、旧状态覆盖搜索结果。\n"
                "【上文优先级低于搜索】如果上一句回答与当前搜索结果矛盾，"
                "必须以搜索结果为准，不要被上一句回答带偏；"
                "即使上一句是你自己说的，搜索结果证明错了就要改，不要为了保持一致而沿用错误结论。\n"
                "【追问识别】用户追问“X是什么Y吗”“X是Y吗”是在质疑/纠正，不是在确认；"
                "必须重新判断，不要把追问当确认。")
            system += (
                "\n【综合整合】多条搜索结果可能各自只包含一部分信息；"
                "回答时要把人物身份、时间、事件、原因、来源信息交叉合并成完整答案，不要只采用其中一条。")
            system += (
                "\n【回答自由】长短按话题自然走，可以一句带过，也可以铺开讲；关键事实可带 [1][2]；"
                "群聊语气自然口语化；不要用“根据搜索结果”这种冗余开头；"
                "如果问题含多个实体，尽量逐个覆盖，不要漏。")
            system += (
                "\n【输出要求】回答正文，不要复读搜索结果标题、网址、来源页面名。")
        # 当前日期已在第一轮前注入；这里只补时效判断要求
        system += (
            "\n【时效要求】以当前时间判断事件是否已发生；"
            "不要因为训练知识旧就说'尚未举行/尚未确定/还没开始'，搜索结果明确的信息优先。"
        )

        # 纠正型追问 + 已有搜索结果时，强制模型基于搜索结果重新判断
        if _CORRECTION_FOLLOWUP_RE.search(query) and distilled and not skip_final:
            if request_messages and isinstance(request_messages[-1].get("content"), str):
                request_messages[-1]["content"] += (
                    "\n\n【纠正型追问强制约束】\n"
                    f"用户在质疑上一句回答：{last_answer}\n"
                    f"当前搜索结果（必须以此为准）：\n{distilled[:1000]}\n"
                    "请基于搜索结果重新回答，不要被上一句回答带偏。\n"
                    "如果搜索结果与上一句回答矛盾，以搜索结果为准。")
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
        if not reply:
            fallback = distilled or search_results
            if fallback:
                reply = await self._llm_answer_from_search(query, fallback)
                if reply:
                    return reply[:MAX_REPLY_LEN]
            return "（AI 暂时无法回复，请稍后再试）"
        log(f"[API] 使用模型: {used_model} | temp={temperature} top_p={top_p}")

        if _looks_like_tool_call(reply):
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
                         (distilled or search_results or "不过现在网没接上，我暂时没拿到可靠结果。"))[:MAX_REPLY_LEN]
            log("[FC] 第3轮最终回答已生成")
        if _is_lyrics_query(query) and reply:
            source_text = distilled or search_results or ""
            if _lyric_reply_suspect(reply, query, source_text):
                log("[歌词] 回答歌名/书名号不完整或未引用正文，进入一次强制重答")
                retry_reply, _ = await self._complete(
                    LYRIC_RETRY_SYSTEM,
                    [{"role": "user", "content": (
                        f"用户问题：{query}\n"
                        f"可靠资料：{source_text or '（无搜索结果）'}\n"
                        f"上一版回答：{reply}")}],
                    max_tokens=final_max_tokens,
                    temperature=0.2,
                    top_p=0.5,
                    model_refs=MODELS_FACTUAL if is_factual else MODELS_CASUAL,
                    weak_query=query,
                    min_reply_len=min_reply_len,
                )
                if retry_reply and not _lyric_reply_suspect(
                        retry_reply, query, source_text):
                    reply = retry_reply
                    log("[歌词] 二次重答通过校验")
        # 关闭二次事实核查，信任第一轮搜索+生成的结果，避免多次调用变慢和重写丢事实。
        if _is_factual(query):
            log("[核查] 已关闭，直接采用第一轮结果")
        reply = _sanitize_reply(reply)
        if reply and TAFFY_FLAVOR and not sensitive:
            log("[塔菲风格] 系统提示词已包含塔菲人设，不做二次重写")
        repeat_source = last_answer if last_answer and _repetition_score(reply, last_answer) > 0.65 else ""
        if not repeat_source:
            for prev in self.recent_replies[-5:]:
                if _repetition_score(reply, prev) > 0.65:
                    repeat_source = prev
                    break
        if repeat_source:
            log("[防复读] 与近期回答高度重复，重新组织回答")
            improved, _ = await self._complete(
                NO_REPEAT_SYSTEM,
                [{"role": "user", "content": (
                    f"近期回答：{repeat_source}\n当前回答：{reply}")}],
                max_tokens=500, temperature=temperature, top_p=top_p,
                model_refs=MODELS_CASUAL,
            )
            if improved:
                reply = _sanitize_reply(improved)
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
        log(f"[耗时] {time.time() - start:.1f}s")
        return reply[:MAX_REPLY_LEN]

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
        is_safe, refusal = _check_safety(text)
        if not is_safe:
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
        style_level, sensitive, _ = _classify_style_control(text)
        style_prompt = ""
        if not sensitive and style_level > 0:
            style_prompt = TAFFY_STYLE_PROMPT if TAFFY_FLAVOR else STYLE_PROMPT
        prompt = (f"【QQ群】{gname}({gid})\n{uname}({uid}): {text}\n"
                  f"{style_prompt}\n"
                  f"事实/知识类问题：有搜索就以搜索为准；如果搜索结果没有答案或不知道，"
                  f"必须直接说'塔菲不知道/塔菲没查到喵'，绝对不能凭空编造或用'我觉得'敷衍；"
                  f"尤其链接、直播间号、比赛安排、时间、数字不能编。\n"
                  f"有搜索结果时，以搜索结果为准直接回答。\n"
                  f"如果记忆里有相关信息优先引用。只输出回复本身，不要解释、前缀。")
        log(f"[群{gid}] {uname}: {text[:80]}")
        if SEND_THINKING:
            try:
                await self.send_group_text(gid, THINKING_PLACEHOLDER)
            except Exception as e:
                log(f"[正在想发送失败] 群{gid}: {repr(e)[:120]}")
        reply = await self.ask_model(gid, prompt, _clean_question(original_text), uname)
        log(f"[群{gid}] AI回复: {reply[:80]}")
        await self.send_group_msg(gid, uid, reply)
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
        is_safe, refusal = _check_safety(text)
        if not is_safe:
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
        style_level, sensitive, _ = _classify_style_control(text)
        style_prompt = ""
        if not sensitive and style_level > 0:
            style_prompt = TAFFY_STYLE_PROMPT if TAFFY_FLAVOR else STYLE_PROMPT
        prompt = (f"【QQ私聊】{uname}({uid}): {text}\n"
                  f"{style_prompt}\n"
                  f"事实/知识类问题：有搜索就以搜索为准；如果搜索结果没有答案或不知道，"
                  f"必须直接说'塔菲不知道/塔菲没查到喵'，绝对不能凭空编造或用'我觉得'敷衍；"
                  f"尤其链接、直播间号、比赛安排、时间、数字不能编。\n"
                  f"有搜索结果时，以搜索结果为准直接回答。\n"
                  f"如果记忆里有相关信息优先引用。只输出回复本身，不要解释、前缀。")
        log(f"[私聊{uid}] {uname}: {text[:80]}")
        if SEND_THINKING:
            try:
                await self.send_private_msg(uid, THINKING_PLACEHOLDER)
            except Exception as e:
                log(f"[正在想发送失败] 私聊{uid}: {repr(e)[:120]}")
        reply = await self.ask_model(
            "private_" + uid,
            prompt,
            _clean_question(original_text),
            uname,
        )
        log(f"[私聊{uid}] AI回复: {reply[:80]}")
        await self.send_private_msg(uid, reply)
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
        while True:
            try:
                async with websockets.connect(WS_URL, ping_interval=25, open_timeout=15) as ws:
                    log("已连接 OneBot WebSocket")
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
                log(f"连接断开: {repr(e)[:120]}，3 秒后重连")
                self.ws_connected = False
                write_health(
                    ws_connected=False,
                    last_error=f"连接断开: {repr(e)[:120]}",
                    last_error_at=datetime.now().isoformat(),
                )
                await asyncio.sleep(3)

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
        checks.append("永雏塔菲" in SYSTEM_PROMPT and "不编造" in SYSTEM_PROMPT)
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
        checks.append("自称规则" in TAFFY_FLAVOR_PROMPT)
        checks.append("喵有节制" in TAFFY_FLAVOR_PROMPT)
        checks.append("不碰中之人" in TAFFY_FLAVOR_PROMPT)
        checks.append("最高优先级" in TAFFY_FLAVOR_PROMPT)
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
        checks.append("详略得当" in SYSTEM_PROMPT and "永久保持塔菲模式" in TAFFY_FLAVOR_PROMPT)
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
    log("桥接启动 (NapCat OneBot11 -> 模型 API)")
    asyncio.run(Bridge().run())


if __name__ == "__main__":
    main()
