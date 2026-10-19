# -*- coding: utf-8 -*-
"""只读整理：塔菲 QQBot 工程审查包。

原则：
  - 只读生产文件与数据库；只写入 .tmp/review_*/ 暂存目录。
  - 不打包 .env / 密钥 / 登录态 / 媒体缓存 / 完整生产库。
  - 所有导出文本经脱敏：QQ 号、群号、图片 URL、密钥、Cookie 一律替换。
"""
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile

BASE = os.environ.get("QQBOT_ROOT") or os.getcwd()
STAMP = "20261018"
OUT_NAME = f"taffy_qqbot_review_{STAMP}.zip"
STAGE = os.path.join(BASE, ".tmp", f"review_{STAMP}")

sys.path.insert(0, BASE)
os.environ.pop("QQ_TEST_MODE", None)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(BASE, ".env"))
from qqbot_core import config as C  # noqa: E402

# ---------------- 脱敏 ----------------
QQ_RE = re.compile(r"(?<!\d)\d{6,12}(?!\d)")
GROUP_RE = re.compile(r"(?<!\d)\d{8,12}(?!\d)")
IMG_RE = re.compile(r"https?://[^\s\"'<>]*multimedia[^\s\"'<>]*")
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
KEY_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{12,})", re.I)


def scrub(text):
    """统一脱敏：密钥 → 图片链接 → 普通链接 → 长数字。"""
    t = str(text or "")
    t = KEY_RE.sub("[密钥已脱敏]", t)
    t = IMG_RE.sub("[图片URL]", t)
    t = URL_RE.sub("[链接]", t)
    t = GROUP_RE.sub("[群号]", t)
    t = QQ_RE.sub("[QQ号]", t)
    return t


def write(rel, text):
    path = os.path.join(STAGE, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    io.open(path, "w", encoding="utf-8", newline="").write(str(text))


def read(path, limit=None):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            return f.read() if limit is None else f.read(limit)
    except Exception as exc:  # noqa: BLE001
        return f"（读取失败：{type(exc).__name__}）"


def sh(cmd, env=None):
    try:
        r = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True, timeout=300,
                           env=env)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return f"（执行失败：{exc}）"


# ================= 0. 目录准备 =================
if os.path.isdir(STAGE):
    shutil.rmtree(STAGE)
os.makedirs(STAGE, exist_ok=True)
print("暂存目录:", STAGE)

# ================= 1. 复制代码（只读） =================
CODE_FILES = ["bridge_qq.py", "requirements.txt", "requirements-memory.txt",
              "restart_bridge_hidden.py"]
CODE_DIRS = ["qqbot_core", "persona", "tests"]
COPIED = []
for name in CODE_FILES:
    src = os.path.join(BASE, name)
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(STAGE, name))
        COPIED.append(name)
for name in CODE_DIRS:
    src = os.path.join(BASE, name)
    if os.path.isdir(src):
        dst = os.path.join(STAGE, name)
        shutil.copytree(src, dst,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
        COPIED.append(name + "/")
# 非敏感示例配置
for name in ("config/.env.example", ".env.example", "AGENTS.md"):
    src = os.path.join(BASE, name)
    if os.path.isfile(src):
        rel = name.replace("config/", "")
        os.makedirs(os.path.dirname(os.path.join(STAGE, rel)) or STAGE, exist_ok=True)
        shutil.copy2(src, os.path.join(STAGE, rel))
        COPIED.append(rel)
print("已复制:", ", ".join(COPIED))

# ================= 2. README_审查说明.md =================
readme = f"""# 塔菲 QQBot 工程审查包

生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}（只读导出，未修改任何生产代码/数据/授权）

## 这个包是什么

一个 QQ 群聊机器人：**NapCat(OneBot11) → Python 桥接 → DeepSeek API**。
人格是**永雏塔菲**（中文虚拟主播）的同人 AI 群友，自称 `taffy`/`塔菲`/`小菲`，
明确不是本人、不是官方账号。

设计方向：**多保留人物特征，少干预 DeepSeek 的表达自由。**

## 目录

| 路径 | 内容 |
| --- | --- |
| `bridge_qq.py` | 主程序（约 9100 行）：消息接收、请求组装、识图、主动接话、记忆调度 |
| `qqbot_core/` | 路由、人格、观察器、记忆、学习队列、群授权、安全、文本处理 |
| `persona/` | 人格文件（`taffy.md` 是唯一来源；`taffy_b0/b1/b2/b3.md` 是历史版本存档） |
| `tests/` | pytest 测试（当前 **854 项通过**） |
| `requirements*.txt` | 依赖清单 |
| `README_审查说明.md` | 本文件 |
| `01_项目结构.md` | 目录树、git commit、未提交改动、生产入口 |
| `02_配置快照.md` | 线上真实生效的配置（已脱敏） |
| `03_日志样本.md` | 启动/主动接话/凝练/检索/识图/失败的日志样本（已脱敏） |
| `04_记忆样本/` | 短期上下文、会话历史、长期记忆的表结构+统计+脱敏样本 |
| `05_真实聊天案例.md` | 代表性真实聊天（已脱敏） |
| `06_测试结果.md` | pytest 运行摘要 |

## 没有打包什么（按你的要求）

`.env` 与真实密钥 · QQ 登录凭据/Cookie/Token · NapCat 登录缓存 · 完整生产数据库 ·
未脱敏群聊/私聊记录 · 图片与媒体缓存 · 虚拟环境 · 完整日志

## 脱敏规则

| 原始 | 替换为 |
| --- | --- |
| API Key / Bearer Token | `[密钥已脱敏]` |
| 图片下载 URL（multimedia.nt.qq.com.cn…） | `[图片URL]` |
| 其他 http(s) 链接 | `[链接]` |
| 群号（8-12 位数字） | `[群号]` |
| QQ 号（6-12 位数字） | `[QQ号]` |
| 群昵称 | **保留**（审查需要"谁说了什么"；群昵称只是网名，不是真实身份，也不含 QQ 号） |

## 当前真正启用的功能

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 塔菲人格 | ✅ 启用 | 单一来源 `persona/taffy.md`（3486 字），作为 system prompt |
| 短期记忆 | ✅ 启用 | 近期群聊窗口（30 分钟 / 每群 60 条），落盘 `recent_group_context.json` |
| 长期记忆 | ✅ 启用 | 凝练入库 + 检索注入；**命中率低**（只认明确事件/约定） |
| 识图 | ✅ 启用（仅群聊、被 @ 且带图） | 取图 → 缩到 768px → 多模态请求 |
| @ 回复 | ✅ 启用 | `TRIGGER=at` |
| 主动接话 | ✅ 启用（仅群聊） | 由**模型自己判断**，输出 `__QUIET__` 则沉默 |
| 群风格学习 | ❌ 已移除 | 代码里没有调用点（`GROUP_STYLE_LEARNING` 是空开关） |
| L0–L3 风格层 / 场景模式 / 二次改写 / 主观闸门 / 字数限制 | ❌ 已移除 | 旧代码保留在 `LEGACY_STYLE_LAYERS=1` / `REJECTION_GATES=1` 之后作回滚 |

## 已知问题（截至导出时）

1. **长期记忆命中率低** —— 提取器只认"明确事件/约定"（考试、报名、说定了…），
   这个群聊的是游戏梗和吐槽，很少命中；且已存记忆是"拼接原话"形式、存在重叠。
2. **凝练频率偏高** —— 空闲 180 秒即尝试一次，日志噪音大（产出多为 0 条）。
3. **偶发 `generation_empty`** —— 模型返回空且备选模型预算耗尽（实测约 2/24 次）。
4. **`GROUP_STYLE_LEARNING` 是空开关** —— 两个消费点都已移除，配置与实际不一致。
5. **夜间静默硬编码** —— `hour < 8 or >= 23` 写死在代码里，不是配置项。
6. **NapCat 会自行掉线** —— 桥接会正确拒绝启动（启动自检含 OneBot HTTP 探测）。

## 建议审查重点

- `bridge_qq.py` 里是否有**废弃逻辑 / 重复处理 / 互相冲突的判断**
  （历史上反复改过路由、上下文组装、主动决策、记忆调度）
- `qqbot_core/agent_memory.py` 的记忆链路（写入 → 索引 → 召回 → 注入）
- 主动接话的**实际模型调用次数与 token 成本**
- 人格是否限制模型自由表达（对比 `persona/taffy.md` 与 `ace-taffy-skill`）
"""
write("README_审查说明.md", readme)

# ================= 3. 项目结构 =================
lines = ["# 项目结构与版本", "",
         f"导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", "",
         "## Git", "",
         "```",
         "HEAD: " + sh("git rev-parse HEAD").strip(),
         sh("git log --oneline -8").strip(),
         "```", "",
         "## 未提交改动（生产当前状态与 commit 的差异）", "",
         "```", sh("git status --porcelain").strip()[:6000], "```", "",
         "## 生产入口与运行方式", "",
         "- 入口：`bridge_qq.py`（`main()`）",
         "- 启动脚本：`restart_bridge_hidden.py`（隐藏窗口启动 + 启动自检）",
         "- 连接：NapCat OneBot11 WebSocket（群消息）+ HTTP API（发送/取历史/取图）",
         "- 运行目录：项目根；数据在 `data/`，日志在 `logs/`", "",
         "## 文件规模", ""]
for name in COPIED:
    p = os.path.join(STAGE, name.rstrip("/"))
    if os.path.isdir(p):
        fs = [f for f in os.listdir(p) if os.path.isfile(os.path.join(p, f))]
        lines.append(f"- `{name}` — {len(fs)} 个文件")
    elif os.path.isfile(p):
        lines.append(f"- `{name}` — {os.path.getsize(p)} B")
lines += ["", "## 目录树（前 3 层，忽略缓存）", "", "```"]
for root, dirs, files in os.walk(BASE):
    dirs[:] = [d for d in dirs if d not in
               {"__pycache__", ".git", ".tmp", "backups", "venv", ".venv",
                "node_modules", "shell", "data", "logs"}]
    rel = os.path.relpath(root, BASE)
    depth = 0 if rel == "." else rel.count(os.sep) + 1
    if depth > 2:
        dirs[:] = []
        continue
    if rel != ".":
        lines.append(("  " * (depth - 1)) + os.path.basename(root) + "/")
    for f in sorted(files)[:12]:
        lines.append(("  " * depth) + f)
    if len(files) > 12:
        lines.append(("  " * depth) + f"…（共 {len(files)} 个文件）")
lines += ["```", ""]
write("01_项目结构.md", "\n".join(lines))

# ================= 4. 配置快照（脱敏） =================
import bridge_qq as B  # noqa: E402

cfg_names = sorted(n for n in dir(B)
                   if n.isupper() and not n.startswith("_")
                   and isinstance(getattr(B, n), (int, float, str, bool, list, tuple, frozenset)))
cfg_lines = ["# 线上生效配置（脱敏）", "",
             f"导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", "",
             "> 这些值来自**加载了 `.env` 之后的真实模块状态**，即线上运行时的取值。", "",
             "| 配置项 | 值 |", "| --- | --- |"]
for n in cfg_names:
    v = getattr(B, n)
    if isinstance(v, (list, tuple)):
        v = ", ".join(str(x) for x in v)
    elif isinstance(v, frozenset):
        v = ", ".join(sorted(str(x) for x in v))
    # 数值型不脱敏（否则 AGENT_MEMORY_LIMIT_BYTES 这种大数会被当成群号）
    s = str(v) if isinstance(v, (int, float, bool)) else scrub(str(v))
    if len(s) > 160:
        s = s[:160] + "…"
    if any(k in n for k in ("KEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE")):
        s = "[已脱敏]"
    cfg_lines.append(f"| `{n}` | {s} |")
cfg_lines += ["", "## .env 中显式设置的键（仅键名，不含值）", "", "```"]
for line in read(os.path.join(BASE, ".env")).splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k = line.split("=", 1)[0].strip()
        if any(t in k.upper() for t in ("KEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE")):
            cfg_lines.append(f"{k}=[已脱敏]")
        else:
            cfg_lines.append(scrub(line.strip())[:120])
cfg_lines += ["```", ""]
write("02_配置快照.md", "\n".join(cfg_lines))

# ================= 5. 日志样本（脱敏） =================
log_path = os.path.join(BASE, "logs", "bridge.log")
log_text = read(log_path)
log_lines = log_text.splitlines()
PATTERNS = {
    "启动与连接": r"启动检查|桥接启动|已连接 OneBot|生命周期|短期记忆|群学习",
    "主动决策与接话": r"主动决策|主动接话",
    "凝练": r"凝练",
    "记忆检索": r"\[记忆\]",
    "识图": r"识图",
    "API 与模型": r"\[API|API原始输出|输出预算|使用模型",
    "失败与异常": r"失败|异常|错误|TimeoutError|generation_empty|status=send_failed|Error",
}
out = ["# 日志样本（脱敏）", "",
       f"来源：`logs/bridge.log`，共 {len(log_lines)} 行；导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
       "", "> 每类只取最近若干条；QQ 号、群号、图片 URL、密钥已替换。", ""]
for title, pat in PATTERNS.items():
    rx = re.compile(pat)
    hits = [ln for ln in log_lines if rx.search(ln)][-40:]
    out += [f"## {title}（{len(hits)} 条）", "", "```"]
    out += [scrub(h)[:220] for h in hits]
    out += ["```", ""]
write("03_日志样本.md", "\n".join(out))

# ================= 6. 记忆样本 =================
mem_dir = os.path.join(STAGE, "04_记忆样本")
os.makedirs(mem_dir, exist_ok=True)

# 6.1 短期上下文
ctx_path = C.RECENT_GROUP_CONTEXT_FILE
try:
    ctx = json.load(io.open(ctx_path, encoding="utf-8"))
except Exception:
    ctx = {}
st = ["# 短期记忆样本（近期群聊窗口）", "",
      "来源：`data/recent_group_context.json`；TTL 30 分钟 / 每群最多 60 条。",
      "", f"共 {len(ctx)} 个群；导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
for gid, items in ctx.items():
    st += [f"## 群 [群号]（{len(items)} 条）", "", "| 距今 | 内容 |", "| --- | --- |"]
    now = time.time()
    for it in items[-20:]:
        try:
            age = int(now - float(it.get("at") or 0))
        except Exception:
            age = -1
        st.append(f"| {age}s | {scrub(it.get('content'))[:200]} |")
    st.append("")
io.open(os.path.join(mem_dir, "短期记忆_样本.md"), "w", encoding="utf-8", newline="").write("\n".join(st))

# 6.2 会话历史
sess_path = C.SESSIONS_FILE
try:
    sess = json.load(io.open(sess_path, encoding="utf-8"))
except Exception:
    sess = {}
sl = ["# 会话历史样本", "", "来源：`data/bridge_sessions.json`。", "",
      "| 会话 | 消息数 | 摘要长度 | summary_index | last_reply_at |", "| --- | --- | --- | --- | --- |"]
for key, store in sess.items():
    store = store or {}
    msgs = store.get("messages") or []
    tag = "[私聊]" if str(key).startswith("private_") else "[群号]"
    sl.append(f"| {tag} | {len(msgs)} | {len(store.get('summary') or '')} | "
              f"{store.get('summary_index')} | {store.get('last_reply_at')} |")
sl += ["", "## 最近对话样本（脱敏）", ""]
for key, store in sess.items():
    msgs = (store or {}).get("messages") or []
    tag = "[私聊]" if str(key).startswith("private_") else "[群号]"
    sl += [f"### {tag}（共 {len(msgs)} 条，显示最后 12 条）", ""]
    for m in msgs[-12:]:
        role = "群友" if m.get("role") == "user" else "塔菲"
        content = re.sub(r"^【QQ群】[^\n]*\n", "", str(m.get("content") or ""))
        sl.append(f"- **{role}**：{scrub(content)[:240]}")
    sl.append("")
io.open(os.path.join(mem_dir, "会话历史_样本.md"), "w", encoding="utf-8", newline="").write("\n".join(sl))

# 6.3 长期记忆（结构 + 统计 + 脱敏记录）
lt = ["# 长期记忆样本", "", f"库：`data/agent_memory/memory.sqlite3`（只读查询）", ""]
db = os.path.join(C.AGENT_MEMORY_DIR, "memory.sqlite3")
if os.path.isfile(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    tables = [r[0] for r in con.execute(
        "select name from sqlite_master where type='table' order by name")]
    lt += ["## 表结构与行数", ""]
    for t in tables:
        try:
            n = con.execute(f"select count(*) from {t}").fetchone()[0]
        except Exception:
            n = "?"
        cols = [d[1] for d in con.execute(f"pragma table_info({t})").fetchall()]
        lt.append(f"- `{t}`：**{n} 行** —— 列：{', '.join(cols) if cols else '（虚拟表）'}")
    lt += ["", "## episodes（长期记忆）记录（脱敏）", ""]
    try:
        for r in con.execute("select content, status, created_at, sources from episodes"):
            lt += [f"- **status={r[1]}**（{time.strftime('%m-%d %H:%M', time.localtime(float(r[2] or 0)))}）",
                   f"  - 内容：{scrub(r[0])[:300]}"]
            try:
                src = json.loads(r[3] or "[]")
                refs = [str(s.get("user_ref") or s.get("user_id") or "?") for s in src[:5]]
                lt.append(f"  - 出处：{len(src)} 条 —— {scrub(', '.join(refs))}")
            except Exception:
                pass
    except Exception as exc:
        lt.append(f"（查询失败：{exc}）")
    lt += ["", "## notes（候选记忆）", ""]
    try:
        for r in con.execute("select category, content, status from notes limit 20"):
            lt.append(f"- `{r[0]}` {scrub(r[1])[:160]}（{r[2]}）")
    except Exception:
        lt.append("（无记录或查询失败）")
    con.close()
else:
    lt.append("（库不存在）")
io.open(os.path.join(mem_dir, "长期记忆_样本.md"), "w", encoding="utf-8", newline="").write("\n".join(lt))

# 6.4 群授权摘要（脱敏）
gp = ["# 群授权摘要（脱敏）", "", "来源：`data/group_policy.sqlite3`（只读）。", ""]
gpf = C.GROUP_POLICY_FILE
if os.path.isfile(gpf):
    con = sqlite3.connect(f"file:{gpf}?mode=ro", uri=True)
    tabs = [r[0] for r in con.execute(
        "select name from sqlite_master where type='table' order by name")]
    gp.append(f"表：{', '.join(tabs)}")
    gp.append("")
    try:
        from qqbot_core.group_policy import FEATURES
        gp += ["| 群 | " + " | ".join(sorted(FEATURES)) + " |", "| --- |" +
               " --- |" * len(FEATURES)]
        for gid in sorted({str(r[0]) for r in con.execute("select distinct group_id from group_grants")}):
            from qqbot_core.group_policy import GroupPolicy
            pol = GroupPolicy(persistence_path=gpf)
            cells = ["✅" if pol.allows(gid, f) else "❌" for f in sorted(FEATURES)]
            gp.append("| [群号] | " + " | ".join(cells) + " |")
    except Exception as exc:
        gp.append(f"（摘要失败：{exc}）")
    con.close()
io.open(os.path.join(mem_dir, "群授权_摘要.md"), "w", encoding="utf-8", newline="").write("\n".join(gp))

# ================= 7. 真实聊天案例 =================
cases = ["# 真实聊天案例（脱敏）", "",
         "来源：`bridge_sessions.json`（被 @ 的问答）+ `recent_group_context.json`"
         "（非 @ 群聊与主动接话）。", "",
         "> 选取原则：最近、有代表性。用于判断自然度、接话时机、上下文理解、人格一致性。", ""]
n = 0
for key, store in sess.items():
    msgs = (store or {}).get("messages") or []
    tag = "私聊" if str(key).startswith("private_") else "群"
    for m in msgs[-24:]:
        role = "群友" if m.get("role") == "user" else "塔菲"
        content = re.sub(r"^【QQ群】[^\n]*\n", "", str(m.get("content") or ""))
        cases.append(f"- **[{tag}] {role}**：{scrub(content)[:300]}")
        n += 1
cases += ["", "## 短期窗口（含非 @ 群聊与主动接话）", ""]
now = time.time()
for gid, items in ctx.items():
    for it in items[-24:]:
        try:
            age = int(now - float(it.get("at") or 0))
        except Exception:
            age = -1
        cases.append(f"- `{age}s前` {scrub(it.get('content'))[:300]}")
        n += 1
cases.insert(4, f"共 {n} 条（去重前）")
write("05_真实聊天案例.md", "\n".join(cases))

# ================= 8. 测试结果 =================
# 用与项目一致的测试环境跑（否则会因为缺环境变量而全是 collection error）
_env_backup = {k: os.environ.get(k) for k in
               ("QQ_TEST_MODE", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "TMP", "TEMP")}
# 关键：用**干净环境**跑测试。否则子进程会继承上面 load_dotenv 带进来的
# 生产配置（QQ_AGENT_MEMORY / QQ_PROACTIVE_* 等），干扰测试收集。
_test_env = {
    "PATH": os.environ.get("PATH", ""),
    "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
    "SYSTEMDRIVE": os.environ.get("SYSTEMDRIVE", "C:"),
    "TEMP": os.path.join(BASE, ".tmp"),
    "TMP": os.path.join(BASE, ".tmp"),
    "PYTHONIOENCODING": "utf-8",
    "PYTHONDONTWRITEBYTECODE": "1",
    "QQ_TEST_MODE": "1",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
}
os.makedirs(os.path.join(BASE, ".tmp"), exist_ok=True)
# 必须显式指定 `tests`：不带路径时 pytest 会从根目录收集，
# 在这个项目里会产生 58 个收集错误（带路径则 854 项全通过）。
test_out = sh([sys.executable, "-B", "-m", "pytest", "tests", "-q",
               "-p", "no:cacheprovider", "--tb=no"], env=_test_env)
write("06_测试结果.md", "\n".join([
    "# 测试结果", "",
    f"运行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", "",
    "命令：`python -B -m pytest tests -q -p no:cacheprovider --tb=no`",
    "（必须带 `tests` 参数；不带时本项目会出现 58 个收集错误）", "",
    "```", scrub(test_out.strip()[-3000:]), "```", "",
    "## 测试目录", "",
    "`tests/` 已随包提供（131 个文件）。", ""]))

# ================= 9. 打包 =================
zip_path = os.path.join(BASE, "..", OUT_NAME)
zip_path = os.path.abspath(zip_path)
if os.path.exists(zip_path):
    os.remove(zip_path)
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(STAGE):
        dirs[:] = [d for d in dirs if d not in {"__pycache__", ".pytest_cache"}]
        for f in files:
            if f.endswith((".pyc",)):
                continue
            full = os.path.join(root, f)
            z.write(full, os.path.relpath(full, STAGE))
print("审查包:", zip_path)
print("大小: %.1f MB" % (os.path.getsize(zip_path) / 1024 / 1024))
with zipfile.ZipFile(zip_path) as z:
    names = z.namelist()
print("文件数:", len(names))
print("顶层:", sorted({x.split("/")[0] for x in names}))
