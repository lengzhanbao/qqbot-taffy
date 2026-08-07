# QQBot

基于 NapCat + OneBot v11 + Python 的 QQ 群机器人，角色固定为永雏塔菲。

## 目录

- `bridge_qq.py`：主桥接，NapCat WebSocket 到模型 API
- `scripts/backup.ps1`：备份项目关键文件到 `<项目根>\backups`
- `scripts/restore.ps1`：从 `<项目根>\backups` 恢复，恢复前自动备份当前文件
- `scripts/status.ps1`：读取 `<项目根>\data\health.json` 查看机器人健康状态
- `docs/`：计划、学习记录、Prompt 文档、问答对照等文档
- `config/.env.example`：环境变量示例，真实 `.env` 在项目根目录
- `tests/`：pytest 测试
- `backups/`：所有备份，全部在 E 盘
- `AGENTS.md`：项目级约束，包含 E 盘存储和删除确认规则

## 存储约定

所有项目运行数据和备份都放在 E 盘：

- 项目根目录：`<项目根>`
- 会话：`<项目根>\data\bridge_sessions.json`
- 长期记忆：`<项目根>\data\bot_memory.md`
- 群风格画像：`<项目根>\data\group_profiles.json`
- 日志：`<项目根>\logs\bridge.log`
- 健康状态：`<项目根>\data\health.json`
- 临时自检文件：`<项目根>\.tmp`
- 备份：`<项目根>\backups`

## 快速开始

1. 复制 `config\.env.example` 为 `<项目根>\.env`，填写 API Key。
2. 启动 NapCat，确认 `3001/6099` 端口可用；端口可通过 `QQ_WS_URL`、`QQ_ONEBOT_HTTP_URL` 调整。
3. 用隐藏方式启动桥接：

```powershell
Start-Process -FilePath "<用户目录>\AppData\Local\Programs\Python\Python311\pythonw.exe" `
    -ArgumentList "<项目根>\bridge_qq.py" `
    -WorkingDirectory "<项目根>" `
    -WindowStyle Hidden
```

## 测试

```powershell
& "<用户目录>\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/ -q
```

梗/歌词搜索冒烟测试会真实联网并写入 `logs/bridge.log`：

```powershell
& "<用户目录>\AppData\Local\Programs\Python\Python311\python.exe" scripts/test_meme_search.py
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

## 备份与恢复

备份会包含 `shell/config` 下的 NapCat OneBot 配置，恢复后端口配置可一并还原。

```powershell
# 备份
powershell -ExecutionPolicy Bypass -File <项目根>\scripts\backup.ps1 -Tag manual

# 列出并选择备份
powershell -ExecutionPolicy Bypass -File <项目根>\scripts\restore.ps1

# 指定备份目录恢复
powershell -ExecutionPolicy Bypass -File <项目根>\scripts\restore.ps1 -BackupPath <项目根>\backups\20260807_160000_manual

# 查看健康状态
powershell -ExecutionPolicy Bypass -File <项目根>\scripts\status.ps1
```

恢复前脚本会自动停止检查：如果桥接进程还在运行会拒绝执行。恢复前也会先把当前文件备份到 `<项目根>\backups\before_restore_时间戳`。
