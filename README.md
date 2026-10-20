# QQBot · 永雏塔菲

基于 **NapCat + OneBot v11 + Python** 的 QQ 群机器人，角色固定为**永雏塔菲**（中文虚拟主播）的同人 AI 群友。

**它不是本人，也不是官方账号。**

设计原则：**多保留人物特征，少干预模型的表达自由。**
人格只描述"她是谁、怎么说话"，不规定"每句话必须怎么写"——不设字数上限、不做口癖配额、不写必须加表情。

---

## 目录

- [能力](#能力)
- [架构](#架构)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [配置](#配置)
- [测试](#测试)
- [隐私边界](#隐私边界)
- [已知限制](#已知限制)
- [开源协议](#开源协议)

---

## 能力

| 能力 | 说明 |
| --- | --- |
| **塔菲人格** | 单一来源 `persona/taffy.md`，直接作为 system prompt。融合公开语料特征：优先 `taffy` 自称（不用"我"）、`喵` 的量感、`一只` 量词、事故播报感、表情习惯 |
| **短期记忆** | 近期群聊窗口（默认 30 分钟 / 每群 60 条），落盘、重启不丢；**按授权版本隔离**，撤销授权后旧窗口不复用 |
| **长期记忆** | 凝练入库（episodes）+ 相关性检索注入。内容由"触发消息 + 相邻证据"构成，并按覆盖率去重 |
| **识图** | 群消息带图即可识图（含未被 @ 的图、被引用消息里的图）；仅接受 QQ 图片域名的 https 地址，先缩到 768px 再传 |
| **@ 回复** | `TRIGGER=at`：被 @ 或私聊时正常回复 |
| **主动接话** | 由**模型自己判断**是否开口，输出 `__QUIET__` 则沉默；代码只负责"是否允许说、是否太频繁" |
| **群授权** | 逐群授权：`observe` / `knowledge_learning` / `knowledge_retrieval` / `style_learning` / `proactive_chat` |
| **语音回复** | `taffy_speak` 工具：**DeepSeek 自己决定**发文字还是发语音；本地 Bert-VITS2（塔菲 V200 音色）合成，失败自动降级文字 |
| **点歌** | `/点歌 <歌名>`：搜 QQ 音乐并发送音乐卡片（无需 @，与对话流程解耦） |

### 分工原则

```
"是否有自然切入点"  →  交给 DeepSeek
"是否允许说话、是否太频繁、是否打断其他处理"  →  留给代码
```

代码侧只保留工程边界：**群授权 · 夜间静默 · 冷却/防刷屏 · 安全 · 需联网核实不抢答 · 发送前对回复本身的安全检查**。

---

## 架构

```
QQ 客户端
   │
NapCat（OneBot v11）
   ├── WebSocket :3001  → 群/私聊消息事件
   └── HTTP API  :6099  ← 发送消息 / get_msg / get_group_msg_history
   │
bridge_qq.py（主桥接）
   ├── qqbot_core.observer      观察器：群消息窗口、话题、去重
   ├── qqbot_core.routing       路由：意图、上下文需求、机会评分（仅观测）
   ├── qqbot_core.persona       人格加载（核心人格 + 背景资料）
   ├── qqbot_core.agent_memory  长期记忆：凝练、索引、召回
   ├── qqbot_core.group_policy  群授权与版本
   ├── qqbot_core.text          文本清洗与截断
   ├── qqbot_core.voice         语音工具（taffy_speak）与投递
   ├── qqbot_core.music         点歌（QQ 音乐搜索 + 卡片）
   └── qqbot_core.safety        安全检查
   │
模型 API（按顺序回退）
   opencode/deepseek-v4.1-flash → opencode/longcat-2.5-preview-free → deepseek/deepseek-flash
```

### 两条回复路径

```
被 @（TRIGGER=at）
  system  = 人格 + 当前日期（+ 需要联网时的搜索策略）
  messages= 会话历史（按授权与相关性筛选）+ 本轮问题（带图则附多模态块）
  后处理  = 格式清理 + 超长按句截断

没被 @（主动接话）
  system  = 人格 + 自主群聊接话原则
  messages= 「群聊最近内容（按时间从早到晚）：昵称: 原话 … 最新消息：X」（带图则附多模态块）
  决定    = 模型输出 __QUIET__ 则沉默，否则发送
```

---

## 目录结构

```
bridge_qq.py                 主桥接（消息接收、请求组装、识图、主动接话、记忆调度）
qqbot_core/                  核心包
  ├── persona.py             人格加载与缓存
  ├── observer.py            群消息观察器
  ├── routing.py             路由与意图
  ├── agent_memory.py        长期记忆（凝练 / 索引 / 召回）
  ├── group_policy.py        群授权与版本
  ├── knowledge.py           知识检索
  ├── topics.py              话题维护
  ├── text.py                文本清洗
  ├── safety.py              安全检查
  ├── voice.py               语音工具与投递（SQLite 投递日志）
  ├── music.py               点歌（搜索 + 卡片）
  ├── config.py              配置常量
  └── ...
persona/
  ├── taffy.md               当前人格（唯一来源）
  ├── taffy_background.md    扩展背景（按话题检索注入）
  └── taffy_b0/b1/b2/b3.md   历史版本存档
tests/                       pytest（866 项）
services/taffy_tts.py        常驻语音服务（复用本地 Bert-VITS2，仅 127.0.0.1）
scripts/                     备份 / 恢复 / 状态 / 评估 / 审查包生成 / 启动语音服务
config/.env.example          环境变量示例（真实 .env 在项目根，不进 Git）
docs/public/                 可进入 Git 的脱敏文档
```

---

## 快速开始

1. 复制配置并填写 API Key：

```powershell
Copy-Item config\.env.example .env
# 编辑 .env，填写 DEEPSEEK_API_KEY 等
```

2. 启动 NapCat，确认端口可用（默认 `3001` WebSocket、`6099` HTTP）。

3. 启动桥接（隐藏窗口）：

```powershell
python restart_bridge_hidden.py
```

4. 查看状态：

```powershell
.\scripts\status.ps1
```

### 逐群授权

主动接话、记忆、识图都要求对应群已授权。授权前先确认群号，再写入 `data/group_policy.sqlite3`。

---

## 配置

配置来自 `.env`（键名见 `config/.env.example`）。常用项：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `QQ_TRIGGER` | `at` | 触发方式：仅被 @ 时回复 |
| `QQ_CASUAL_TEMPERATURE` / `QQ_FACTUAL_TEMPERATURE` | `0.85` / `0.7` | 闲聊与事实类温度 |
| `QQ_CASUAL_TOP_P` / `QQ_FACTUAL_TOP_P` | `1.0` / `0.8` | top_p |
| `QQ_MAX_OUTPUT_TOKENS` | `10000` | 生成预算 |
| `QQ_PROACTIVE_GROUP_CHAT` | `0` | 主动接话总开关（需逐群授权） |
| `QQ_PROACTIVE_DEBOUNCE` | `2.5` | 防抖：等群友把一段话说完（期间有新消息则重新计时） |
| `QQ_PROACTIVE_GROUP_COOLDOWN` | `900` | 同群两次主动发言最小间隔（秒） |
| `QQ_PROACTIVE_SELF_QUIET` | `300` | 自己说完后多久能再接（秒） |
| `QQ_AGENT_MEMORY` | `0` | 长期记忆开关 |
| `QQ_LEGACY_STYLE_LAYERS` | `0` | 旧风格层（L0–L3）回滚开关 |
| `QQ_REJECTION_GATES` | `0` | 旧风格闸门回滚开关 |

> 发送字符上限由 `MAX_REPLY_LEN` 控制（默认 4500，即 QQ 单条消息上限）。

---

## 语音（可选，默认关闭）

语音是**可选项**：TTS 服务没启动时，机器人照常文字聊天，不会报错。

### 组成

```
../Bert-VITS2-master/        本地 Bert-VITS2（与仓库同级，模型 + 独立 venv，不进仓库）
  G_*.pth                     塔菲 V200 权重
  config.json                 version=2.0, spk2id={永雏塔菲:0}, 44100Hz
services/taffy_tts.py         常驻服务，仅监听 127.0.0.1:17861
qqbot_core/voice.py           taffy_speak 工具 + 至多一次投递
```

### 启动

```powershell
python scripts/start_taffy_tts.py     # 约 10-15 秒加载模型
```

### 工作方式

```
@塔菲 → DeepSeek 拿到 taffy_speak 工具 → 自己决定文字还是语音
       → 语音：TTS 合成 → record 段发送
       → 失败：自动降级发文字（不会没反应、不会重复发）
```

语音的文本内容**照常进入会话历史**，所以下一轮它知道自己说过什么。

### 说明

合成音色为社区模型的**同人二创**，非官方；机器人明确不是本人也不是官方账号。
语音依赖本地 GPU（实测 RTX 5070 8GB 可用，torch cu128）。

---

## 测试

```powershell
python -m pytest tests -q
```

> **注意必须带 `tests` 参数**：不带路径时 pytest 会从项目根目录收集，在本项目会产生大量收集错误。

当前：**866 项通过**。

---

## 隐私边界

- `data/`、`logs/`、`backups/`、`.tmp/`、`.env`、`*.sqlite3` **都不进入 Git**（见 `.gitignore`）。
- 代码与测试中**不使用真实 QQ 号、群号或成员名**，统一使用占位号。
- 仓库只包含**功能代码、人格文本与测试**，不含聊天记录、数据库、日志或密钥。
- `.env` 中的密钥仅存在于本地。

---

## 已知限制

1. **长期记忆命中率低** —— 凝练只认"明确事件/约定"（考试、报名、说定了…），日常闲聊很少命中。
2. **凝练频率偏高** —— 空闲约 180 秒即尝试一次，日志噪音较大（多数产出 0 条）。
3. **偶发空回复** —— 模型返回空且备选模型预算耗尽（实测约 2/24 次）。
4. **夜间静默硬编码** —— `8:00–23:00` 之外不主动接话（只影响主动接话，@ 与私聊照常回复），目前不是配置项。
5. **NapCat 可能自行掉线** —— 桥接启动自检含 OneBot HTTP 探测，探测失败会拒绝启动。
6. **语音音频不自动清理** —— 保留到 512 MB 上限后降级文字（按项目规则，删除需人工确认）。
7. **群风格学习已停用** —— 学习链路已移除，`group_profiles.json` 中的统计为历史快照。

---

## 开源协议

MIT，见 [LICENSE](LICENSE)
