# QQBot 架构设计

## 1. 整体架构

```text
用户 QQ
  -> NapCat
  -> OneBot v11 WebSocket
  -> bridge_qq.py
  -> 安全/管理员检查
  -> LLM 第一轮（可调用搜索工具，不强制）
  -> 搜索结果（cn.bing 优先 + 缓存 + 引擎降级）
  -> LLM 最终回答
  -> 格式清理 + 来源标注
  -> NapCat -> 用户 QQ
```

代码入口仍为 `bridge_qq.py`，核心模块逐步拆到 `qqbot_core/`：

- `qqbot_core/stats.py`：运行统计，跨重启累计。
- `qqbot_core/safety.py`：安全护栏。
- `qqbot_core/learning.py`：群聊进化学习。

核心设计原则是“大道至简”：

- 系统提示词只定义永雏塔菲人物特色。
- 搜索工具对明确触发词开放，但不强制。
- 最终回答不再经过答案契约、自检、防复读、来源补全等多次重写。
- 回答末尾明确标注是否来自网络搜索。

## 2. 消息流程

1. NapCat 收到 QQ 消息，通过 WebSocket 推送给 `bridge_qq.py`。
2. `Bridge` 解析消息类型：群聊 / 私聊 / 名片 / 引用。
3. 先执行管理员命令检查。
4. 非管理员发送 `!xxx` 指令会被静默忽略。
5. 执行 `_check_safety` 安全检查。
6. 根据明确触发词决定是否开放搜索工具。
7. LLM 自行决定直接回答或调用 `plan_search` / `search_web`。
8. 搜索结果交给 LLM 最终凝练。
9. 做最小编排清理 `_sanitize_reply`。
10. 追加来源标注 `（联网搜过喵）` 等。
11. 发送回复并更新 `health.json` / `stats.json`。

## 3. 搜索机制

### 3.1 触发

`_SEARCH_TRIGGER` 包含：

- 新闻、比分、比赛、天气、股票等实时词。
- 人物、选手、歌手、演员、主播等实体词。
- `介绍一下`、`介绍`、`什么梗`、`梗`。

只有触发时才开放搜索工具。

### 3.2 工具

- `plan_search`：LLM 拆多个短 query。
- `search_web`：直接搜索一个独立 query。
- `fetch_page`：抓取歌词、文章、攻略等正文。

工具调用通过 function calling 返回；如果模型把 JSON 写进正文，`_extract_tool_calls_from_text` 会自动恢复。

### 3.3 稳定性

- `search_with_resilience`：统一入口。
- `_SEARCH_CACHE`：5 分钟缓存。
- `_ENGINE_FAIL_STATS`：引擎连续失败 3 次后降级 5 分钟。
- `QQ_ALWAYS_SEARCH=1` 时，模型未调用搜索会忽略直接回复并强制通用联网。
- 优先 `cn.bing`，百度百科降权。
- `search_web_async` 并发搜索多个源，整体超时兜底。

## 4. 模型降级机制

- 主模型失败：空回复、超时、过短、截断。
- `finish_reason=length` 会丢弃并切换备用模型。
- 弱回复会标记并跳过。
- 连续失败会熔断，`health.json` 标记 `model_circuit_open`。
- 最终仍无回复时返回诚实兜底文案。

## 5. 提示词体系

当前主 system 只包含：

```text
你是永雏塔菲，群里一个真人朋友，二次元浓度高，活泼可爱，
会整活、接梗、自嘲、一本正经地抽象。怎么说话由你自己判断。
```

另加一行当前日期和一句通用搜索说明：

```text
事实/实时/人物问题需要联网时，由你调用搜索工具；
优先 cn.bing，不要优先百度百科；搜索词由你自己决定，按搜索结果回答。
```

不再注入：

- 抽象控制等级。
- 对话模式模板。
- 群风格硬编码。
- 答案契约。
- 自检重写。
- 防复读重写。

## 6. 记忆管理

- 短期会话：`data/bridge_sessions.json`。
- 长期记忆：`data/bot_memory.md`。
- 群风格画像：`data/group_profiles.json`。
- 管理员命令：
  - `!forget`：清理当前会话。
  - `!clear_memory`：全局清理，需二次确认。
- 历史读取时会剥离旧版误存的 system/style/tone 提示词残留。

当前链路默认不把长期记忆主动注入 system，避免旧记忆污染新回答。

## 7. 安全护栏

`_check_safety` 覆盖 5 类：

- `self_harm`
- `violence`
- `illegal`
- `adult`
- `sensitive`

`self_harm` 优先级最高，回复包含心理援助热线。

## 8. 可观测性

- `data/health.json`：PID、WS 状态、最后消息、最后回复、搜索降级、熔断。
- `data/stats.json`：消息数、回复数、平均耗时、模型成功率、搜索成功率、缓存命中、安全拦截。
- `logs/bridge.log`：`[耗时]`、`[模型]`、`[搜索]`、`[熔断]`、`[错误]` 标签。
- `scripts/status.ps1`：一键查看健康状态。

`stats.json` 启动时会读取已有文件，跨重启累计，不再从 0 开始。

## 9. 群聊进化学习

- 用户纠正、新梗、约定写入 `data/learnings.md`。
- 只记录，不主动注入当前回答，避免旧记忆污染。
- 后续可基于高频纠正提升为 `bot_memory.md` 的可靠记忆。

## 10. 测试

- 单元测试覆盖清洗、分类、卡片解析、模型降级、安全、搜索稳定性、统计。
- 集成测试覆盖群 @、私聊、B站卡片、敏感消息、管理员命令。
- 测试全部 mock，不连真实 NapCat。

## 11. 关键文件

| 文件 | 作用 |
|---|---|
| `bridge_qq.py` | 主桥接和全部核心逻辑 |
| `tests/` | pytest 测试 |
| `scripts/backup.ps1` | 备份 |
| `scripts/restore.ps1` | 恢复 |
| `scripts/status.ps1` | 健康检查 |
| `config/.env.example` | 配置示例 |
| `data/` | 会话、记忆、健康、统计 |
| `docs/` | 计划、架构、面试材料 |
