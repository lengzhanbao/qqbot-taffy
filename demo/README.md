# QQBot 脱敏 Demo

这个目录只保存可公开演示的构建产物，不包含真实密钥、聊天记录、群记忆和本地日志。

## 构建

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_demo.ps1
```

生成目录：

```text
demo/build_YYYYMMDD_HHMMSS_demo/
├── bridge_qq.py
├── qqbot_core/
├── tests/
├── scripts/
├── config/.env.example
├── docs/public/
├── CHANGELOG.md
├── pytest.ini
├── requirements-dev.txt
├── README.md
└── AGENTS.md
```

## 不包含

- `.env` 和真实 API Key
- `data/` 会话、记忆、群画像、统计
- `logs/` 真实日志
- `docs/` 其他本地计划、聊天记录、Prompt 原始资料
- `backups/`
- `shell/` NapCat 配置

## 使用

1. 复制 `config/.env.example` 为 `.env`。
2. 填入你自己的测试 API Key。
3. 启动 NapCat 或使用 mock 测试。
4. 运行 `pytest` 验证 demo 副本。
