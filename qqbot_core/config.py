"""QQBot 配置模块：路径、环境变量、模型、搜索和采样参数。"""
import os
import threading


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_FILE = os.path.join(BASE_DIR, "data", "bot_memory.md")
SESSIONS_FILE = os.path.join(BASE_DIR, "data", "bridge_sessions.json")
LOG_FILE = os.path.join(BASE_DIR, "logs", "bridge.log")
HERMES_ENV_FILE = os.environ.get(
    "HERMES_ENV_FILE",
    os.path.abspath(os.path.join(BASE_DIR, "..", "hermes", ".env")),
)
GROUP_STYLES_FILE = os.path.join(BASE_DIR, "data", "group_profiles.json")
TMP_DIR = os.path.join(BASE_DIR, ".tmp")
HEALTH_FILE = os.path.join(BASE_DIR, "data", "health.json")
_HEALTH_LOCK = threading.Lock()
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
_MEMORY_CACHE = {"content": "", "mtime": 0.0}
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
        mtime = os.path.getmtime(MEMORY_FILE)
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

WS_URL = os.environ.get("QQ_WS_URL", "ws://127.0.0.1:3001")
ONEBOT_HTTP_URL = os.environ.get(
    "QQ_ONEBOT_HTTP_URL", "http://127.0.0.1:6099"
).rstrip("/")
TRIGGER = os.environ.get("QQ_TRIGGER", "at")
MAX_REPLY_LEN = 12000
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
COMPRESS_TRIGGER_CHARS = 110000
COMPRESS_KEEP_RECENT_CHARS = 85000
KNOWLEDGE_TOP_K = 3
KNOWLEDGE_MAX_CHARS = 2000
LOG_MAX_SIZE = 5 * 1024 * 1024
CONVERSATION_LIMIT_BYTES = 50 * 1024 * 1024 * 1024
CONVERSATION_COMPRESS_BYTES = 45 * 1024 * 1024 * 1024
API_TIMEOUT = int(os.environ.get("QQ_API_TIMEOUT", "30"))
API_CONNECT_TIMEOUT = int(os.environ.get("QQ_API_CONNECT_TIMEOUT", "12"))
PROVIDER_COOLDOWN_SECONDS = 60
KNOWLEDGE_MIN_SCORE = 2
REFLECTION_MAX_ROUNDS = 1
REACT_MAX_ROUNDS = 2
REFLECTION_ENABLED = os.environ.get("QQ_REFLECTION", "0") == "1"
THINKING_PLACEHOLDER = "正在想..."
SEND_THINKING = os.environ.get("QQ_SEND_THINKING", "1") == "1"
ALWAYS_SEARCH = os.environ.get("QQ_ALWAYS_SEARCH", "0") == "1"
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
MAX_SEARCH_TASKS = 8
TOOL_CALL_TIMEOUT = float(os.environ.get("QQ_TOOL_CALL_TIMEOUT", "15"))
DRISSION_SEARCH_ENABLED = os.environ.get("QQ_DRISSION_SEARCH", "1") == "1"
DRISSION_SEARCH_TIMEOUT = float(os.environ.get("QQ_DRISSION_SEARCH_TIMEOUT", "8"))
GLM_WEB_SEARCH = os.environ.get("QQ_GLM_WEB_SEARCH", "0") == "1"
GLM_WEB_SEARCH_ENGINE = os.environ.get("QQ_GLM_WEB_SEARCH_ENGINE", "search_pro")
GLM_WEB_SEARCH_COUNT = os.environ.get("QQ_GLM_WEB_SEARCH_COUNT", "10")
FACTUAL_TEMPERATURE = float(os.environ.get("QQ_FACTUAL_TEMPERATURE", "0.7"))
FACTUAL_TOP_P = float(os.environ.get("QQ_FACTUAL_TOP_P", "0.8"))
CASUAL_TEMPERATURE = float(os.environ.get("QQ_CASUAL_TEMPERATURE", "1.1"))
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
    "glm": {
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "key": os.environ.get("GLM_API_KEY", ""),
    },
    "opencode": {
        "url": os.environ.get(
            "OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1"
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

MODELS_FACTUAL = list(MODELS)
MODELS_CASUAL = list(MODELS)
