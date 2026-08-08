# QQBot 部署文档

## 环境要求

- Windows 10/11
- Python 3.11+
- NapCat + OneBot v11
- 可用的模型 API Key

## 1. 获取代码

```powershell
git clone <repo-url>
cd qqbot
```

## 2. 创建虚拟环境

```powershell
python -m venv .venv
.\.venv\Scripts\activate
```

## 3. 安装依赖

```powershell
pip install -r requirements.txt -r requirements-dev.txt
```

## 4. 配置环境变量

```powershell
copy config\.env.example .env
```

至少填写：

- `GLM_API_KEY`
- `OPENCODE_GO_API_KEY` 或 `OPENCODE_FREE_API_KEY`
- `QQ_ADMIN_UIDS`

## 5. 启动 NapCat

配置 OneBot v11：

- WebSocket：`ws://127.0.0.1:3001`
- HTTP：`http://127.0.0.1:6099`

## 6. 启动桥接

```powershell
python bridge_qq.py
```

## 7. 验证

```powershell
python -m pytest tests/ -q
python scripts/check_env.py
python scripts/smoke_test.py
```

## 8. 健康检查

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\status.ps1
```

预期输出：

```text
[OK] 机器人运行正常
```
