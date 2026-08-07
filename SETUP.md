# QQ 群 AI 机器人部署文档（NapCat + Hermes 桥接）

> 机器人账号：<BOT_QQ>（<BOT_NAME>）
> 部署日期：2026-08-04
> 项目目录：`<项目根>`（全部文件都在此，C 盘无残留）

## 架构

```
QQ 群/私聊消息
   │
   ▼
NapCat (<项目根>\shell) ── 基于 QQNT 9.9.32-51246 (D:\ 安装)
   │  OneBot 11 WebSocket 服务: 127.0.0.1:3001
   │  OneBot 11 HTTP API: 127.0.0.1:6099
   ▼
桥接脚本 (<项目根>\bridge_qq.py) ── Python 3.11 + websockets
   │  每群独立对话历史（群记忆隔离，见 bridge_sessions.json）
   │  直连模型 API，支持多 Provider 降级与异常熔断
   ▼
模型 API ── GLM-4.7-Flash / GLM-4-Flash-250414（智谱免费） → OpenCode Go 兜底
```

## 目录结构

```
<项目根>\
├── shell\                  NapCat 本体（4.18.14）
│   ├── launch_napcat.bat   启动脚本（管理员，快速登录参数 -q <BOT_QQ>）
│   ├── config\             NapCat 配置（onebot11_<BOT_QQ>.json 含 WS 3001）
│   ├── cache\              qrcode.png / qr_highres.png（登录二维码）
│   └── boot.log            启动日志
├── bridge_qq.py            AI 桥接脚本（核心）
├── data\                   运行数据（记忆、会话、画像、健康状态）
├── logs\                   桥接日志
├── docs\                   计划、学习记录、Prompt 文档
├── scripts\                备份/恢复/状态/测试脚本
├── tests\                  pytest 测试
├── restart_napcat.ps1      重启 NapCat（提权执行，带日志）
├── block_qq_update.ps1     hosts 屏蔽 QQ 更新域名
├── tools\                  QQ/7z/NapCat 安装包备份（Git 忽略）
└── SETUP.md                本文档
```

## 日常操作

### 启动 / 重启（手动）
- **NapCat**：双击 `shell\launch_napcat.bat`（管理员）或运行
  `powershell -Command "Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','<项目根>\restart_napcat.ps1' -Verb runAs"`
- **桥接**：`python -u <项目根>\bridge_qq.py`（Python 3.11: `<用户目录>\AppData\Local\Programs\Python\Python311\python.exe`）

### 开机自启（已配置）
- NapCat：计划任务 `QQBotNapCat`（登录时，最高权限，无 UAC）
- 桥接：启动文件夹 `QQBotBridge.bat`
- 重启电脑后两个服务自动上线

### 排障
- 桥接日志：`<项目根>\logs\bridge.log`（消息收发全记录）
- NapCat 日志：`<项目根>\shell\boot.log`
- 机器人不在线：先看 NapCat 进程（NapCatWinBootMain.exe + QQ.exe），再看桥接进程
- 登录会话：成功登录后运行 `powershell -File <项目根>\backup_qq_session.ps1` 备份
  `E:\Tencent Files\Tencent Files\<BOT_QQ>\nt_qq`；下次要扫码时，关闭 QQ/NapCat 后
  运行 `powershell -File <项目根>\restore_qq_session.ps1 -BackupPath "备份目录"` 恢复
- **重复回复**：说明有多个 bridge_qq.py 实例在跑，只留一个
  `powershell -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object {$_.CommandLine -like '*bridge_qq*'} | Select ProcessId,CommandLine"`
- 需要重新扫码登录：删掉 `E:\Tencent Files\Tencent Files\NapCat\data` 里的会话或等二维码过期，启动后从 boot.log 提取二维码 URL 生成图片

## 关键坑位（血泪教训）

1. **QQ 版本必须匹配 NapCat**：NapCat 4.18.x 只支持 QQ 9.9.26-44343 附近版本（≥40768）。
   9.9.33-51802 会报 `PacketBackend 不支持当前QQ版本架构`（登录后阶段才报，消息收发不可用）。
   当前使用 9.9.32-51246（有警告但 hook 模式可正常工作）。QQ 安装包备份在项目目录。
2. **QQ 自动更新会废掉机器人**：已通过 hosts 屏蔽 `dldir1.qq.com`、`dldir1v6.qq.com`、
   `dldir1v4.qq.com`、`dldir2.qq.com`、`qqdl.gtimg.cn`（备份在 hosts.bak）。要更新 QQ 时删掉这些行。
3. **PowerShell 脚本编码**：.ps1 必须 UTF-8 **带 BOM**（否则中文乱码、语法解析失败，静默不执行）。
   用 `open(path, 'w', encoding='utf-8-sig')` 写。
4. **模型 API 直连**：桥接直接调用 OpenAI 兼容 API，不再 spawn Hermes 进程。
   多 Provider 按 `MODELS` 顺序降级，异常后进入熔断，避免 429/5xx 反复重试。
5. **记忆隔离**：每个群/私聊独立对话历史，持久化在 `data/bridge_sessions.json`；
   `data/bot_memory.md` 按 `## 群 <gid>` 分块，仅注入与当前问题相关的记忆。
6. **提速关键**：只有明显需要实时/事实检索的问题才触发 360 搜索，
   普通闲聊不再每条消息等待搜索。
7. **同群消息串行**：每群一个 asyncio 队列 worker，防止并发调用弄乱会话。
8. **二维码登录**：二维码约 2 分钟过期，NapCat 连续过期会自己退出（正常）。
   提取最新 URL 用 segno 生成高清 PNG 给用户扫：
   `python -c "import re,segno; urls=re.findall(r'二维码解码URL:\s*(\S+)', open(r'<项目根>\shell\boot.log',encoding='utf-8',errors='ignore').read()); segno.make_qr(urls[-1]).save(r'<项目根>\shell\cache\qr_highres.png', scale=24, border=4)"`

## 配置项

- `TRIGGER=at`（默认）：仅 @机器人 时回复；`TRIGGER=all`：回复全部群消息（环境变量 QQ_TRIGGER）
- 回复风格：很神人、像串子、会整活接梗，但知识严格按搜索，其次群内消息
- API Key：统一放 `<项目根>\.env`，不要在源码硬编码
- 模型：事实/复杂问题优先 `glm-4.5-flash` / `glm-4.5-air`；闲聊优先 `glm-4-flash-250414`；OpenCode Go 兜底
- Agent 模式：事实/实时问题按用户原话关键词触发搜索，再生成回答；搜索无结果时自动反思防编造，`QQ_REFLECTION=1` 可全局开启
- 事实核查门控：回答生成后检查是否被搜索结果/记忆支撑，不达标就重写或直接说不确定
- 记忆门控：`KNOWLEDGE_MIN_SCORE=2`，低分记忆不注入，避免无关内容误导模型
- 群风格自学习：自动统计群内常用口头禅/表情，写入 `group_styles.json` 并注入回复风格
- 搜索降级：DuckDuckGo Lite / 搜狗 / 必应中国 / 360 并发请求，谁先出结果用谁
- 搜索源：新增中文维基 JSON、DuckDuckGo Instant，减少网页反爬影响
- 搜索内容：每次最多 8 条结果、2000 字注入，并强制基于搜索结果回答
- 搜索凝练：自动生成更准的搜索词，并去重、去噪、按相关度只保留高价值片段
- 搜索词优化：事实问题先用快模型压缩成搜索关键词，再并发搜索，失败自动退回规则版
- 标准工具调用：第一轮模型返回 `tool_calls`，宿主执行搜索后以 `role:tool` 回填，第二轮生成最终回答
- 默认所有消息都先搜索：事实类走模型压缩搜索词，闲聊用规则词；`QQ_ALWAYS_SEARCH=0` 可关闭
- 实体识别：事实类先提取人名/地名/赛事/作品等实体，再单独搜“实体+百科”并合并结果
- 拆分搜索：压缩后的关键词会再拆成独立词分别搜索并合并，提升查不到的概率
- 搜索问题清洗：自动去掉 `[引用]`、`@` 等标记，并把“罗哥/老罗”归一成“C罗”
- 搜索缓存：相同问题 5 分钟内复用结果，降低反爬和重复搜索
- 响应提示：默认关闭“正在想...”，需要时设 `QQ_SEND_THINKING=1` 开启
- 模型并发限制：同时最多 3 个模型调用，减少 GLM 免费额度 429
- 启动自检：每次启动先离线跑核心规则检查，失败拒绝启动
- 发送重试：QQ 消息发送失败自动重试一次
- 防空承诺：不会回复“等下告诉你/帮你查查”；禁止“不确定/没查到/无法回答”，没查到也给出“我印象中/我觉得”的合理答案
- API 超时：25 秒；Provider 熔断：429 暂停 30 秒，5xx/网络错误暂停 60 秒
- 采样参数：事实/搜索类 0.0/0.5；日常闲聊/整活 1.0/0.95，避免千篇一律
- 上下文：完整保存群/私聊历史，不截断删除；早期历史超过阈值后由模型提炼成摘要，上下文按 128K 模型能力动态分配
- 对话存储：上限 50GB；只有超过 50GB 才把最旧会话归档到 `data/bridge_sessions_archive.json`，不主动删除
- 群历史学习：每约 20 条群消息自动提取一次成员/偏好/约定/事件/梗，写入 `data/bot_memory.md`
- 上下文压缩：未压缩对话达到 11 万字自动压缩一次，保留最近 8.5 万字原文；对话文件达到 45GB 时后台深度压缩保留重要内容
- 短追问记忆：会记录上一句问题/回答，像“亚军呢”这种短句会自动接着上一轮话题回答
- 说话人区分：群消息带昵称+UID，短追问会注明上一句是谁问的、当前是谁在问
- 幽默回复：回答尽量带乐子，可用比喻/调侃/夸张，事实信息保持清楚
- 回复风格：emoji 自然穿插在相关词句后，不集中堆在结尾
