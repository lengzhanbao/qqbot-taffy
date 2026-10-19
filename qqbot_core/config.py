"""QQBot 配置模块：路径、环境变量、模型、搜索和采样参数。"""
import os
import threading


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_DIR = (os.environ.get("QQ_TEST_DATA_DIR") or os.path.join(BASE_DIR, ".tmp", "test-runtime")) if os.environ.get("QQ_TEST_MODE") == "1" else BASE_DIR
MEMORY_FILE = os.path.join(RUNTIME_DIR, "data", "bot_memory.md")
SESSIONS_FILE = os.path.join(RUNTIME_DIR, "data", "bridge_sessions.json")
LOG_FILE = os.path.join(RUNTIME_DIR, "logs", "bridge.log")
HERMES_ENV_FILE = os.environ.get(
    "HERMES_ENV_FILE",
    os.path.abspath(os.path.join(BASE_DIR, "..", "hermes", ".env")),
)
GROUP_STYLES_FILE = os.path.join(RUNTIME_DIR, "data", "group_profiles.json")
TMP_DIR = os.path.join(RUNTIME_DIR, ".tmp")
HEALTH_FILE = os.path.join(RUNTIME_DIR, "data", "health.json")
KNOWLEDGE_FILE = os.path.join(RUNTIME_DIR, "data", "group_knowledge.sqlite3")
GROUP_POLICY_FILE = os.path.join(RUNTIME_DIR, "data", "group_policy.sqlite3")
_HEALTH_LOCK = threading.Lock()
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
_MEMORY_CACHE = {"content": "", "mtime": (0, 0)}
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


def _read_memory_file_cached():
    try:
        stat = os.stat(MEMORY_FILE)
        # mtime_ns + size：秒级 getmtime 在快速连续写入时分辨率不够，会误判缓存新鲜
        mtime = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return ""
    if mtime != _MEMORY_CACHE["mtime"]:
        try:
            with open(MEMORY_FILE, encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return _MEMORY_CACHE["content"]
        _MEMORY_CACHE["content"] = content
        _MEMORY_CACHE["mtime"] = mtime
    return _MEMORY_CACHE["content"]


def _load_env_file():
    """加载项目根目录 .env 文件中的环境变量（不覆盖已有值）。"""
    if os.environ.get("QQ_TEST_MODE") == "1":
        return
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
KNOWLEDGE_SYSTEM = os.environ.get("QQ_KNOWLEDGE_SYSTEM", "0") == "1"
AGENT_MEMORY_ENABLED = os.environ.get("QQ_AGENT_MEMORY", "0") == "1"
AGENT_MEMORY_DIR = os.path.join(RUNTIME_DIR, "data", "agent_memory")
try:
    AGENT_MEMORY_LIMIT_BYTES = min(
        20 * 1024 ** 3,
        max(1024 ** 2, int(float(os.environ.get("QQ_AGENT_MEMORY_LIMIT_GB", "20")) * 1024 ** 3)),
    )
except (ValueError, OverflowError):
    AGENT_MEMORY_LIMIT_BYTES = 20 * 1024 ** 3
AGENT_MEMORY_WARNING_RATIO = 0.9
AGENT_MEMORY_RETRIEVAL_TIMEOUT = 0.8
# 短期记忆：近期群聊窗口。设计原文（QQBot_15天升级计划_超详细.md）写明
# "短期记忆：bridge_sessions.json（按群/私聊分 key）"，即短期记忆应当落盘到项目根目录，
# 而不是只存在内存里——否则每次重启后一段时间内机器人对群聊"失忆"，
# 群里追问"你觉得呢"会因为没有上下文而答非所问。
# 这里把它单独落盘，并沿用授权与 TTL 约束，保持"短期"语义。
RECENT_GROUP_CONTEXT_FILE = os.path.join(
    RUNTIME_DIR, "data", "recent_group_context.json"
)
try:
    RECENT_GROUP_TTL_SECONDS = max(
        60, int(os.environ.get("QQ_RECENT_GROUP_TTL", "1800"))
    )
except ValueError:
    RECENT_GROUP_TTL_SECONDS = 1800
try:
    RECENT_GROUP_MAX_MESSAGES = min(
        200, max(1, int(os.environ.get("QQ_RECENT_GROUP_MAX_MESSAGES", "60")))
    )
except ValueError:
    RECENT_GROUP_MAX_MESSAGES = 60
try:
    RECENT_GROUP_MAX_GROUPS = min(
        64, max(1, int(os.environ.get("QQ_RECENT_GROUP_MAX_GROUPS", "32")))
    )
except ValueError:
    RECENT_GROUP_MAX_GROUPS = 32
RECENT_GROUP_MAX_MESSAGE_CHARS = 1200
RECENT_GROUP_FLUSH_INTERVAL = 10.0
AGENT_MEMORY_EMBEDDING_PATH = os.environ.get(
    "QQ_AGENT_MEMORY_EMBEDDING_PATH",
    os.path.join(AGENT_MEMORY_DIR, "models", "bge-small-zh-v1.5"),
)

WS_URL = os.environ.get("QQ_WS_URL", "ws://127.0.0.1:3001")
ONEBOT_HTTP_URL = os.environ.get(
    "QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:6099"
).rstrip("/")
TRIGGER = os.environ.get("QQ_TRIGGER", "at")
# 单条 QQ 消息的协议上限（约 4500 字）。这是**发送通道的物理上限**，
# 不是风格/字数偏好：生成侧不再按类别压 token 或按风格截断，只在超出发送
# 能力时按句子边界截断（`_truncate_at_sentence`）。可用 QQ_MAX_REPLY_LEN 覆盖。
try:
    MAX_REPLY_LEN = max(200, int(os.environ.get("QQ_MAX_REPLY_LEN", "4500")))
except ValueError:
    MAX_REPLY_LEN = 4500
try:
    DEFAULT_MAX_TOKENS = int(os.environ.get("QQ_MAX_OUTPUT_TOKENS", "10000"))
except ValueError:
    DEFAULT_MAX_TOKENS = 10000
try:
    GUIDE_MAX_TOKENS = int(os.environ.get("QQ_GUIDE_OUTPUT_TOKENS", "10000"))
except ValueError:
    GUIDE_MAX_TOKENS = 10000

MAX_CONTEXT_CHARS = 120000
CONTEXT_SUMMARY_MAX_CHARS = 15000
try:
    MAX_CONTEXT_MESSAGES = min(
        100, max(1, int(os.environ.get("QQ_CONTEXT_MAX_MESSAGES", "100")))
    )
except ValueError:
    MAX_CONTEXT_MESSAGES = 100
try:
    MAX_HISTORY_CONTEXT_CHARS = min(
        50000, max(1000, int(os.environ.get("QQ_HISTORY_CONTEXT_MAX_CHARS", "18000")))
    )
except ValueError:
    MAX_HISTORY_CONTEXT_CHARS = 18000
# 历史摘要触发阈值。原值 110000 高于真实最大会话（2026-10-16 实测：群会话 706 条
# 仅 80214 字符），导致 summary 永远为空、"聊过就忘"。按真实数据下调到会真正触发。
# 可用 QQ_COMPRESS_TRIGGER_CHARS / QQ_COMPRESS_KEEP_RECENT_CHARS 覆盖。
try:
    COMPRESS_TRIGGER_CHARS = max(
        2000, int(os.environ.get("QQ_COMPRESS_TRIGGER_CHARS", "30000"))
    )
except ValueError:
    COMPRESS_TRIGGER_CHARS = 30000
try:
    COMPRESS_KEEP_RECENT_CHARS = max(
        1000, int(os.environ.get("QQ_COMPRESS_KEEP_RECENT_CHARS", "20000"))
    )
except ValueError:
    COMPRESS_KEEP_RECENT_CHARS = 20000
# 单次摘要调用的输入上限：长会话分多次增量摘要，避免一次塞进超大 prompt。
try:
    COMPRESS_MAX_BATCH_CHARS = max(
        1000, int(os.environ.get("QQ_COMPRESS_MAX_BATCH_CHARS", "12000"))
    )
except ValueError:
    COMPRESS_MAX_BATCH_CHARS = 12000
# 单次摘要生成的 token 预算。原来是写死的 600，实测三个模型都会撞到上限被
# finish_reason=length 截断，而摘要路径不允许续写，截断输出会被当成"空回复"丢弃，
# 于是摘要时有时无。按实际输出规模（单个批次约 700-900 字）留足余量。
try:
    SUMMARY_MAX_TOKENS = max(
        200, int(os.environ.get("QQ_SUMMARY_MAX_TOKENS", "1500"))
    )
except ValueError:
    SUMMARY_MAX_TOKENS = 1500
KNOWLEDGE_TOP_K = 3
KNOWLEDGE_MAX_CHARS = 2000
LOG_MAX_SIZE = 5 * 1024 * 1024
# 会话存储阈值（MB）：到 COMPRESS 触发深度压缩，超过 LIMIT 把最旧会话移入归档。
# 旧值 45GB/50GB 是形同虚设的（实测会话文件只有几百 KB，永远不会触发）。
CONVERSATION_COMPRESS_MB = int(os.environ.get("QQ_CONVERSATION_COMPRESS_MB", "200"))
CONVERSATION_LIMIT_MB = int(os.environ.get("QQ_CONVERSATION_LIMIT_MB", "400"))
CONVERSATION_LIMIT_BYTES = CONVERSATION_LIMIT_MB * 1024 * 1024
CONVERSATION_COMPRESS_BYTES = CONVERSATION_COMPRESS_MB * 1024 * 1024
API_TIMEOUT = int(os.environ.get("QQ_API_TIMEOUT", "30"))
API_CONNECT_TIMEOUT = int(os.environ.get("QQ_API_CONNECT_TIMEOUT", "12"))
try:
    ASK_MODEL_TIMEOUT = max(5.0, float(os.environ.get("QQ_ASK_MODEL_TIMEOUT", "35")))
except ValueError:
    ASK_MODEL_TIMEOUT = 35.0
# 闲聊/情绪类回复的整轮预算。太短会让"免费模型慢 + 重试"吃光预算，
# 导致后面的兜底模型来不及接上，于是直接发出「暂时无法回复」。
try:
    CHITCHAT_DEADLINE_SECONDS = max(
        5.0, float(os.environ.get("QQ_CHITCHAT_DEADLINE", "25"))
    )
except ValueError:
    CHITCHAT_DEADLINE_SECONDS = 25.0
PROVIDER_COOLDOWN_SECONDS = 60
KNOWLEDGE_MIN_SCORE = 2
REFLECTION_MAX_ROUNDS = 1
REACT_MAX_ROUNDS = 2
REFLECTION_ENABLED = os.environ.get("QQ_REFLECTION", "0") == "1"
THINKING_PLACEHOLDER = "正在想..."
SEND_THINKING = os.environ.get("QQ_SEND_THINKING", "1") == "1"
ALWAYS_SEARCH = os.environ.get("QQ_ALWAYS_SEARCH", "0") == "1"
ENABLE_GENERATED_MEMORY = os.environ.get("QQ_GENERATED_MEMORY", "0") == "1"
GROUP_STYLE_LEARNING = os.environ.get("QQ_GROUP_STYLE_LEARNING", "0") == "1"
PROACTIVE_GROUP_CHAT = os.environ.get("QQ_PROACTIVE_GROUP_CHAT", "0") == "1"

# ---- 塔菲人格还原档位（与 L0-L3 整活强度解耦）----
# L3 只代表"整活/抽象"强度，不代表人格还原度。核心人格可以一直是 high，
# 而整活强度按场景在 L0-L3 之间动态变化。
PERSONA_FIDELITY = (os.environ.get("QQ_PERSONA_FIDELITY", "high").strip().lower() or "high")
if PERSONA_FIDELITY not in ("low", "standard", "high"):
    PERSONA_FIDELITY = "standard"
PERSONA_FEWSHOT = os.environ.get("QQ_PERSONA_FEWSHOT", "1") == "1"
# 二次模型人格重写默认关闭：多一次调用、更慢，且会放大失真的风险。
PERSONA_REWRITE = os.environ.get("QQ_PERSONA_REWRITE", "0") == "1"
# 2026-10-17 方向调整：默认只使用统一人格主档案 persona/taffy.md，
# 不再叠加 L0-L3、场景等级、模式化提示、few-shot 例句等风格层
# （它们互相冲突，会把回答压成表演或太极）。
# 设为 1 可恢复旧的多层风格提示，用于回滚与 A/B 对照。
LEGACY_STYLE_LAYERS = os.environ.get("QQ_LEGACY_STYLE_LAYERS", "0") == "1"
# 2026-10-17：默认不再用"破人设/弱回复/太短"这类风格与质量评分拒绝正常回答，
# 也不因此切换模型（那会把能正常回复的答案丢掉、换成更差模型的答案）。
# 设为 1 可恢复旧的拒答行为，用于回滚与 A/B 对照。
# 注意：协议类闸门（工具 JSON/乱码、空内容、截断）和语言不匹配不受此开关影响。
REJECTION_GATES = os.environ.get("QQ_REJECTION_GATES", "0") == "1"

# ---- 主动发言限额（保守试运行值，全部可配置）----
# 目标不是让塔菲说得多，而是说得"合适"；默认保持关闭，需逐群授权才生效。
# 注意：任何一项填 0 表示该项"不限制"（例如 QQ_PROACTIVE_DAILY_LIMIT=0 即不限每日次数）。
# 这些限额防的是刷屏和被管理员禁言，与模型是否免费无关。
PROACTIVE_GROUP_COOLDOWN_SECONDS = int(
    os.environ.get("QQ_PROACTIVE_GROUP_COOLDOWN", "900")
)
PROACTIVE_GLOBAL_COOLDOWN_SECONDS = int(
    os.environ.get("QQ_PROACTIVE_GLOBAL_COOLDOWN", "60")
)
PROACTIVE_HOURLY_LIMIT = int(os.environ.get("QQ_PROACTIVE_HOURLY_LIMIT", "2"))
PROACTIVE_DAILY_LIMIT = int(os.environ.get("QQ_PROACTIVE_DAILY_LIMIT", "8"))
PROACTIVE_SCORE_THRESHOLD = int(os.environ.get("QQ_PROACTIVE_SCORE_THRESHOLD", "3"))
# 判断"合不合适"用到的上下文窗口：近段消息条数与统计窗口
PROACTIVE_RECENT_WINDOW_SECONDS = int(
    os.environ.get("QQ_PROACTIVE_RECENT_WINDOW", "180")
)
# 2026-10-17：近段最少消息数 2 → 1。
# 安静群里 180 秒内常常只有 1 条消息，原值会让"对话不活跃"直接拦掉机会
# （实测今天有 6 次死在这一条）。单条消息只要有内容切入点仍可接；
# "缺少内容切入点"的环境类门槛和冷却/退避保持不变。
# 2026-10-17：主动接话防抖（秒）。群友连发几句时先等一小段，
# 避免"一句话还没说完就判断"，同时减少无意义的 API 调用。
PROACTIVE_DEBOUNCE_SECONDS = float(
    os.environ.get("QQ_PROACTIVE_DEBOUNCE", "2.5")
)
PROACTIVE_MIN_RECENT_MESSAGES = int(
    os.environ.get("QQ_PROACTIVE_MIN_RECENT", "1")
)
# 机器人刚在本群说过话后，这段时间内不主动接（避免连续插话）
PROACTIVE_SELF_QUIET_SECONDS = int(
    os.environ.get("QQ_PROACTIVE_SELF_QUIET", "120")
)
# 超过这段时间没参与，普通闲聊也可以自然接一句（让它不至于完全不说话）。
# 只对"有点内容"的消息生效，纯笑声/单字不算。
PROACTIVE_IDLE_BONUS_SECONDS = int(
    os.environ.get("QQ_PROACTIVE_IDLE_BONUS", "300")
)
# 主动接话传给模型的群聊现场上限（字符）。
# 2026-10-17：2400 -> 4000。2400 是硬编码字面量（之前想改成 env 覆盖没生效），
# 最近 10~20 条群聊常被截掉，模型看不到完整话题。
PROACTIVE_CONTEXT_MAX_CHARS = int(
    os.environ.get("QQ_PROACTIVE_CONTEXT_MAX_CHARS", "4000")
)
# 生成预算与发送字数分开；仅已确认兼容的模型关闭思考。
# （原注释提到"发送前仍检查 45 字"，该限制已移除，勿再据此设限。）
PROACTIVE_MAX_TOKENS = int(os.environ.get("QQ_PROACTIVE_MAX_TOKENS", "1024"))
PROACTIVE_ATTEMPT_BACKOFF_SECONDS = max(
    0.0, float(os.environ.get("QQ_PROACTIVE_ATTEMPT_BACKOFF", "15"))
)
MAX_SEARCH_RESULTS = 10
try:
    MAX_SEARCH_CHARS = int(os.environ.get("QQ_MAX_SEARCH_CHARS", "6000"))
except ValueError:
    MAX_SEARCH_CHARS = 6000
try:
    SEARCH_SNIPPET_CHARS = int(os.environ.get("QQ_SEARCH_SNIPPET_CHARS", "600"))
except ValueError:
    SEARCH_SNIPPET_CHARS = 600
try:
    SEARCH_DISTILL_CHARS = int(os.environ.get("QQ_SEARCH_DISTILL_CHARS", "600"))
except ValueError:
    SEARCH_DISTILL_CHARS = 600
try:
    MAX_PAGE_TEXT_CHARS = int(os.environ.get("QQ_MAX_PAGE_TEXT_CHARS", "12000"))
except ValueError:
    MAX_PAGE_TEXT_CHARS = 12000
try:
    MAX_LYRIC_CONTEXT_CHARS = int(
        os.environ.get("QQ_MAX_LYRIC_CONTEXT_CHARS", "24000")
    )
except ValueError:
    MAX_LYRIC_CONTEXT_CHARS = 24000
MAX_SEARCH_TASKS = 14  # 全局并发搜索 I/O 上限，含多查询和 DuckDuckGo
TOOL_CALL_TIMEOUT = float(os.environ.get("QQ_TOOL_CALL_TIMEOUT", "15"))
DRISSION_SEARCH_ENABLED = os.environ.get("QQ_DRISSION_SEARCH", "1") == "1"
DRISSION_SEARCH_TIMEOUT = float(os.environ.get("QQ_DRISSION_SEARCH_TIMEOUT", "8"))
GLM_WEB_SEARCH = os.environ.get("QQ_GLM_WEB_SEARCH", "0") == "1"
GLM_WEB_SEARCH_ENGINE = os.environ.get("QQ_GLM_WEB_SEARCH_ENGINE", "search_pro")
GLM_WEB_SEARCH_COUNT = os.environ.get("QQ_GLM_WEB_SEARCH_COUNT", "10")
FACTUAL_TEMPERATURE = float(os.environ.get("QQ_FACTUAL_TEMPERATURE", "0.7"))
FACTUAL_TOP_P = float(os.environ.get("QQ_FACTUAL_TOP_P", "0.8"))
CASUAL_TEMPERATURE = float(os.environ.get("QQ_CASUAL_TEMPERATURE", "0.85"))
CASUAL_TOP_P = float(os.environ.get("QQ_CASUAL_TOP_P", "1.0"))
try:
    STYLE_LEVEL = int(os.environ.get("QQ_STYLE_LEVEL", "2"))
except ValueError:
    STYLE_LEVEL = 2
STYLE_LEVEL = max(0, min(3, STYLE_LEVEL))
ALLOW_L3 = os.environ.get("QQ_ALLOW_L3", "0") == "1"
DISABLE_ABSTRACT_ON_SENSITIVE = os.environ.get(
    "QQ_DISABLE_ABSTRACT_ON_SENSITIVE", "1"
) == "1"
TAFFY_FLAVOR = os.environ.get("QQ_TAFFY_FLAVOR", "1") == "1"
ADMIN_UIDS = set(
    uid.strip()
    for uid in os.environ.get("QQ_ADMIN_UIDS", "").split(",")
    if uid.strip()
)
_PENDING_CLEAR_MEMORY = set()

PROVIDERS = {
    "deepseek": {
        "url": "https://api.deepseek.com/chat/completions",
        "key": os.environ.get("DEEPSEEK_API_KEY", ""),
    },
    "glm": {
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "key": os.environ.get("GLM_API_KEY", ""),
    },
    "opencode": {
        # OpenCode Go 套餐的 OpenAI 兼容端点（官方文档里 openai-compatible 模型用的就是这个）。
        "url": os.environ.get(
            "OPENCODE_GO_BASE_URL", "https://opencode.ai/inference/go/openai/v1"
        ) + "/chat/completions",
        "key": os.environ.get("OPENCODE_GO_API_KEY", ""),
    },
    "opencode_free": {
        "url": os.environ.get(
            "OPENCODE_FREE_BASE_URL", "https://opencode.ai/zen/v1"
        ) + "/chat/completions",
        "key": (
            os.environ.get("OPENCODE_FREE_API_KEY")
            or os.environ.get("OPENCODE_GO_API_KEY", "")
        ),
    },
}

# 旧的"免费优先"候选池。2026-10-16 起不再用于实际调用：
# 实测这些免费模型大量返回 400/403，一次会话摘要被拖到 39.8 秒。
# 保留作为参考与 _MODEL_BASE_ORDER 的排序兜底。
LEGACY_FREE_MODELS = [
    "opencode_free/deepseek-v4-flash-free",
    "opencode_free/nemotron-3-ultra-free",
    "opencode_free/mimo-v2.6-flash-free",
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

# 最终用户可见回复所用的模型（不参与工具规划）。
# QQ_FINAL_MODEL 支持逗号分隔的优先级列表，例如「免费的优先」：
#   QQ_FINAL_MODEL=opencode_free/space-bunny-free,opencode_free/cat-jam-free
#   QQ_FINAL_FALLBACK_MODEL=deepseek/deepseek-flash
# 未设置时保持原来的 deepseek/ 直连。
TAFFY_LIGHT_MODE = os.environ.get("QQ_TAFFY_LIGHT_MODE", "1") == "1"


def _model_list_env(name):
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


_FINAL_MODELS_ENV = _model_list_env("QQ_FINAL_MODEL")
DEEPSEEK_MODEL_REF = _FINAL_MODELS_ENV[0] if _FINAL_MODELS_ENV else (
    "deepseek/"
    + (os.environ.get("QQ_DEEPSEEK_MODEL", "deepseek-flash").strip() or "deepseek-flash")
)
# 可读别名：这个名字才是它现在真正的含义。
FINAL_MODEL_REF = DEEPSEEK_MODEL_REF
MODELS_FINAL = list(_FINAL_MODELS_ENV) or [DEEPSEEK_MODEL_REF]
# 可选兜底：主模型不可用时（例如订阅失效、免费额度被锁、额度耗尽）自动改用备用
# 模型，避免整个机器人哑掉。同样支持逗号分隔的列表。
for _fallback_ref in _model_list_env("QQ_FINAL_FALLBACK_MODEL"):
    if _fallback_ref not in MODELS_FINAL:
        MODELS_FINAL.append(_fallback_ref)
# 全部辅助调用（会话摘要、搜索成文、事实/闲聊生成）统一走当前模型，
# 不再混用免费池：免费池大量 400/403，会把一次摘要拖到近 40 秒。
MODELS = list(MODELS_FINAL)
MODELS_FACTUAL = list(MODELS_FINAL)
MODELS_CASUAL = list(MODELS_FINAL)

# 调用大模型时使用的客户端标识。OpenCode Go 明确要求用自有 user agent，
# 而不是通用 SDK/HTTP 库名（如 OpenAI/Python 1.40.0），否则会被判定为异常流量。
API_USER_AGENT = os.environ.get(
    "QQ_API_USER_AGENT",
    "QQBot-Taffy/1.0 (+https://github.com/lengzhanbao/qqbot-taffy)",
)
