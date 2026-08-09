# QQBot

基于 NapCat + OneBot v11 + Python 的 QQ 群机器人，角色固定为永雏塔菲。

## 目录

- `bridge_qq.py`：主桥接，NapCat WebSocket 到模型 API
- `qqbot_core/`：模块化核心包，目前包含 stats、safety、learning
- `scripts/backup.ps1`：备份项目关键文件到 `backups/`
- `scripts/restore.ps1`：从 `backups/` 恢复，恢复前自动备份当前文件
- `scripts/status.ps1`：读取 `data/health.json` 查看机器人健康状态
- `docs/public/`：可进入 Git 的脱敏文档
- `docs/`：本地计划、学习记录、Prompt 文档、问答对照等资料，不进入 Git
- `config/.env.example`：环境变量示例，真实 `.env` 在项目根目录
- `tests/`：pytest 测试
- `demo/`：脱敏 demo 说明与构建产物
- `backups/`：所有备份
- `AGENTS.md`：项目级约束，包含存储和删除确认规则

详细文档：

- [架构设计](docs/public/ARCHITECTURE.md)
- [面试准备](docs/public/INTERVIEW.md)
- [模块职责导览](docs/public/MODULE_GUIDE.md)
- [完整函数清单](docs/public/MODULE_FUNCTIONS.md)
- [复盘](docs/public/复盘_15天计划.md)
- [贡献指南](CONTRIBUTING.md)

开源协议：MIT，见 [LICENSE](LICENSE)

## 存储约定

所有项目运行数据和备份都放在项目根目录：

- 会话：`data/bridge_sessions.json`
- 长期记忆：`data/bot_memory.md`
- 群风格画像：`data/group_profiles.json`
- 日志：`logs/bridge.log`
- 健康状态：`data/health.json`
- 运行统计：`data/stats.json`
- 临时自检文件：`.tmp`
- 备份：`backups/`

## 隐私边界

- `docs/` 含真实群聊、计划、日志标记等本地资料，只保留本机，不进入 Git。
- `docs/public/` 只放脱敏后的架构、面试、复盘文档，可进入 Git。
- `data/`、`logs/`、`.env`、`backup_qq_session.ps1`、`restore_qq_session.ps1` 都不进入 Git。
- 代码和测试中不使用真实 QQ 号、群号或成员名，统一使用测试占位号。

## 快速开始

1. 复制 `config\.env.example` 为 `.env`，填写 API Key。
2. 启动 NapCat，确认 `3001/6099` 端口可用；端口可通过 `QQ_WS_URL`、`QQ_ONEBOT_HTTP_URL` 调整。
3. 用隐藏方式启动桥接：

```powershell
Start-Process -FilePath "pythonw.exe" `
    -ArgumentList "bridge_qq.py" `
    -WorkingDirectory (Get-Location) `
    -WindowStyle Hidden
```

## 测试

```powershell
python -m pytest tests/ -q
```

梗/歌词搜索冒烟测试会真实联网并写入 `logs/bridge.log`：

```powershell
python scripts/test_meme_search.py
```

## Git

代码、测试和脚本纳入 Git；`.env`、会话、长期记忆、群画像、日志、健康状态和备份不提交，避免泄露隐私和运行时数据。

## 管理员命令

`QQ_ADMIN_UIDS` 中的用户可使用：

- `!status`：查看 PID、WebSocket、会话和最近错误
- `!style 0|1|2|3`：设置抽象等级上限
- `!taffy on|off`：开启或关闭塔菲风格
- `!model`：查看当前模型顺序
- `!forget`：清理当前群/私聊的会话记忆、长期记忆和群风格，清理前自动备份
- `!clear_memory`：清空所有记忆，需二次确认，执行前自动备份

非管理员发送这些指令会被静默忽略。

## 运行统计

`data/stats.json` 会记录：

- 消息数、回复数、平均耗时
- 模型调用、成功、失败、熔断次数
- 搜索调用、成功、失败、缓存命中
- 安全拦截次数

查看：

```powershell
Get-Content -Raw -Encoding UTF8 data\stats.json
```

## 回答来源标注

最终回答会追加：

- `（联网搜过喵）`
- `（联网搜了但没查到，塔菲凭现有知识答的喵）`
- `（没联网，塔菲凭现有知识答的喵）`

这样用户能直接判断答案是否经过网络搜索验证。

## 搜索行为

`QQ_ALWAYS_SEARCH=1` 时：

- 搜索工具对所有消息开放。
- 模型第一轮即使没调用搜索，也会忽略直接回复并强制走通用联网兜底。
- 只有 `QQ_ALWAYS_SEARCH=0` 时，才可能保留模型未联网的直接回答。

## 备份与恢复

备份会包含 `shell/config` 下的 NapCat OneBot 配置，恢复后端口配置可一并还原。

```powershell
# 备份
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1 -Tag manual

# 列出并选择备份
powershell -ExecutionPolicy Bypass -File scripts\restore.ps1

# 指定备份目录恢复
powershell -ExecutionPolicy Bypass -File scripts\restore.ps1 -BackupPath backups\20260807_160000_manual

# 查看健康状态
powershell -ExecutionPolicy Bypass -File scripts\status.ps1
```

恢复前脚本会自动停止检查：如果桥接进程还在运行会拒绝执行。恢复前也会先把当前文件备份到 `backups/before_restore_时间戳`。
