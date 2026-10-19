# -*- coding: utf-8 -*-
"""生成给外部 AI 评审用的对话与状态整理稿。

隐私处理：不输出原始 QQ 号、群号、消息 ID；昵称保留（用户已知）。
"""
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
# 注意：**不能**设 QQ_TEST_MODE=1 —— 那会把 RUNTIME_DIR 指到 .tmp/test-runtime，
# 读不到真实的 bridge_sessions.json / recent_group_context.json。
os.environ.pop("QQ_TEST_MODE", None)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(BASE, ".env"))
from qqbot_core import config as C  # noqa: E402

now = time.time()


def ago(ts):
    try:
        d = int(now - float(ts))
    except Exception:
        return "?"
    if d < 60:
        return f"{d}秒前"
    if d < 3600:
        return f"{d//60}分钟前"
    if d < 86400:
        return f"{d//3600}小时前"
    return f"{d//86400}天前"


def clock(ts):
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(ts)))
    except Exception:
        return "?"


QQ = re.compile(r"\b\d{6,12}\b")
out = []
out.append("# 塔菲 QQBot：近期真实对话与状态（供外部评审）")
out.append("")
out.append(f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
out.append("")
out.append("## 0. 背景（评审前请先读）")
out.append("")
out.append("- 这是一个**群聊机器人**：NapCat(OneBot11) → Python 桥接 → DeepSeek API。")
out.append("- 人格：**永雏塔菲**（中文虚拟主播）的同人 AI 群友，自称「小菲」，粉丝称「雏草姬」。")
out.append("  明确不是本人、不是官方账号。人格写在单一文件 `persona/taffy.md` 里，作为 system prompt。")
out.append("- 设计原则：**多保留人物特征，少干预 DeepSeek 的表达自由**。不写字数限制、不写口癖配额、")
out.append("  不做主观风格闸门、不做二次改写，正常回答直接发送（只做安全/协议/格式处理）。")
out.append("- 目标四项能力：①塔菲人格 ②短期记忆（近期群聊上下文）③长期记忆（自动凝练并检索）")
out.append("  ④被 @ 正常回答 + 未被 @ 时偶尔自然接话。")
out.append("")
out.append("当前配置：")
out.append("")
out.append("| 项 | 值 |")
out.append("| --- | --- |")
out.append("| 触发方式 | `TRIGGER=at`（只回被 @ 的消息；未被 @ 时走主动接话） |")
out.append("| 采样 | `temperature=0.85`（事实类 0.7）、`top_p=1.0`（事实类 0.8） |")
out.append("| 输出预算 | 统一 `max_tokens=10000`，字符上限 4500（= QQ 单条协议上限） |")
out.append("| 主动接话 | 阈值 score≥2、群冷却 15 秒、自我安静 60 秒 |")
out.append("| 记忆 | 短期：近期窗口 30 分钟/60 条（落盘）；长期：凝练入库 + 语义检索 |")
out.append("")
out.append("---")
out.append("")
out.append("## 0.1 修复时间线（判断记录时请对照）")
out.append("")
out.append("今天（10-17）做了多轮修复，**A 段里较早的记录属于修复前**，不代表当前行为：")
out.append("")
out.append("| 时间 | 修复 | 影响 |")
out.append("| --- | --- | --- |")
out.append("| 13:19 起 | 移除群风格模仿注入、移除二次改写、去分类（统一输出预算） | 回答更自然、不再被改写 |")
out.append("| 13:50 | 短期记忆覆盖**所有**被观察到的群消息（原先只记被 @ 的） | 追问能接上群聊 |")
out.append("| 15:14 | @ 回复的说话者错标修正（原标成提问者昵称） | 记忆里不再混淆群友与塔菲 |")
out.append("| 15:27 | 凝练队列超时放大（原 3 秒/1 秒，与工作量不匹配） | 凝练任务不再被丢弃 |")
out.append("| 16:00 前后 | 长期记忆提取器扩词表 + 单条强触发 | \"我们下周三考试\"能入库 |")
out.append("")
out.append("---")
out.append("")

# A. 会话历史
out.append("## A. 被 @ 触发的问答（完整原文）")
out.append("")
out.append("来源：`bridge_sessions.json`。这是**模型实际生成并发送**的原文。")
out.append("")
out.append("### A1. 最近记录（今天 15:00 之后，含上述修复）")
out.append("")
CUTOFF = time.mktime(time.strptime("2026-10-17 15:00:00", "%Y-%m-%d %H:%M:%S"))
try:
    data = json.load(open(C.SESSIONS_FILE, encoding="utf-8"))
except Exception as e:
    data = {}
    out.append(f"（读取失败：{e}）")
total = 0
recent_lines, old_lines = [], []
for key, store in data.items():
    msgs = (store or {}).get("messages") or []
    if not msgs:
        continue
    total += len(msgs)
    tag = "群" if not str(key).startswith("private_") else "私聊"
    for m in msgs:
        role = "**群友**" if m.get("role") == "user" else "**塔菲**"
        text = str(m.get("content") or "").strip()
        text = re.sub(r"^【QQ群】[^\n]*\n", "", text)
        text = QQ.sub("[QQ号]", text)
        ts_raw = m.get("recorded_at")
        ts = clock(ts_raw) if ts_raw else "—"
        line = f"- [{tag}] {role} `{ts}`：{text}"
        if isinstance(ts_raw, (int, float)) and ts_raw >= CUTOFF:
            recent_lines.append((ts_raw, line))
        else:
            old_lines.append(line)
recent_lines.sort(key=lambda x: x[0])
for _, line in recent_lines:
    out.append(line)
if not recent_lines:
    out.append("（今天 15:00 之后还没有被 @ 的记录）")
out.append("")
out.append("### A2. 更早的历史（修复前，仅作对比参考）")
out.append("")
for line in old_lines[-12:]:
    out.append(line)
out.append("")
out.append(f"（全部会话合计 {total} 条）")
out.append("")

# B. 短期记忆窗口
out.append("## B. 短期记忆窗口（含**非 @** 的群友发言与塔菲主动接话）")
out.append("")
out.append("来源：`recent_group_context.json`（TTL 30 分钟 / 每群 60 条，落盘）。")
out.append("因为 `TRIGGER=at`，这些非 @ 内容只在短期记忆里，日志看不到。")
out.append("")
try:
    ctx = json.load(open(C.RECENT_GROUP_CONTEXT_FILE, encoding="utf-8"))
    if not ctx:
        out.append("（当前为空）")
    for gid, items in ctx.items():
        out.append(f"### 群会话（{len(items)} 条）")
        out.append("")
        for i in items:
            text = QQ.sub("[QQ号]", str(i.get("content") or ""))
            out.append(f"- `{ago(i.get('at'))}` {text}")
        out.append("")
except Exception as e:
    out.append(f"（读取失败：{e}）")

# C. 主动接话
out.append("## C. 主动接话记录（未被 @ 时自己接的话）")
out.append("")
out.append("来源：`logs/bridge.log`。`主动决策` 是打分结果，`主动接话` 是最终发送/放弃。")
out.append("")
lines = []
log_path = os.path.join(C.BASE_DIR, "logs", "bridge.log")
try:
    lines = open(log_path, encoding="utf-8", errors="replace").read().splitlines()
except Exception:
    pass
decisions, sends = [], []
for ln in lines:
    m = re.search(r"(\d\d-\d\d \d\d:\d\d:\d\d) \[主动决策\] 群\d+ (\S+) score=(\S+)(?: 近段=(\d+)条)? 理由=(.*)", ln)
    if m:
        decisions.append(m.groups())
    m = re.search(r"(\d\d-\d\d \d\d:\d\d:\d\d) \[主动接话\] (?:群\d+ )?(?:已发送 (\d+) 字|group=\d+ status=(\S+))", ln)
    if m:
        sends.append(m.groups())
out.append(f"- 决策次数：**{len(decisions)}**，其中「发言」{sum(1 for d in decisions if d[1] == '发言')} 次")
out.append(f"- 最终发送成功：**{sum(1 for s in sends if s[2])}** 次；"
           f"被取消/退避/沉默：{sum(1 for s in sends if not s[2])} 次")
out.append("")
out.append("最近 15 条决策：")
out.append("")
for d in decisions[-15:]:
    extra = f" 近段={d[3]}条" if d[3] else ""
    out.append(f"- `{d[0]}` **{d[1]}** score={d[2]}{extra} — {d[4]}")
out.append("")
out.append("最近 10 条发送结果：")
out.append("")
for s in sends[-10:]:
    out.append(f"- `{s[0]}` " + (f"已发送 {s[1]} 字" if s[2] else f"未发送（{s[2]}）"))
out.append("")

# D. 已知问题
out.append("## D. 已知问题与限制（评审时请一并考虑）")
out.append("")
out.append("| 项 | 状态 | 说明 |")
out.append("| --- | --- | --- |")
out.append("| 塔菲人格 | ✅ 正常 | 单一 `persona/taffy.md`，无模式指令、无字数限制 |")
out.append("| 短期记忆 | ✅ 已验证 | 非 @ 群友发言与塔菲回复都会落盘，重启不丢 |")
out.append("| 长期记忆 | 🟡 刚打通写入 | 提取器原先过窄（\"我们下周三考试\"都存不进），已扩展词表 + 单条强触发；实测能写入且带出处 |")
out.append("| 中文关键词检索 | ❌ 无效 | SQLite FTS5 默认分词器不切分中文，`match '考试'` 命中 0；检索可能依赖语义向量（未验证） |")
out.append("| 上下文干扰 | 🟡 观察中 | 例：问\"塔菲是什么\"时，回答里额外扯到了无关的群赛程信息 |")
out.append("| 回答长度波动 | 正常 | 同一问题 3 次能差 2 倍以上（采样随机性），代码未限制字数 |")
out.append("")
out.append("## E. 请评审的问题")
out.append("")
out.append("1. **人格**：从下面真实对话看，它像不像一个「会正常聊天的群友 + 有塔菲特色」的机器人？")
out.append("   哪里过度表演、哪里不够像？")
out.append("2. **上下文使用**：它接话时是否准确理解了群友在聊什么？有没有答非所问或硬扯无关内容？")
out.append("3. **主动接话**：从 C 段看，它该沉默时沉默、该接话时接话吗？频率合适吗（会不会刷屏或太沉默）？")
out.append("4. **回答质量**：知识类回答是否完整准确？寒暄/调侃类是否自然不油腻？")
out.append("5. **优先级建议**：在「人格 / 短期记忆 / 长期记忆 / 接话频率」四项里，你认为下一步最该改哪一项？")
out.append("")

path = os.path.join(C.BASE_DIR, "docs", "对话记录_供评审.md")
os.makedirs(os.path.dirname(path), exist_ok=True)
open(path, "w", encoding="utf-8", newline="").write("\n".join(out) + "\n")
print("已生成:", path)
print(f"会话 {total} 条 / 短期窗口 {sum(len(v) for v in ctx.values()) if ctx else 0} 条 / "
      f"主动决策 {len(decisions)} 次 / 发送记录 {len(sends)} 条")
