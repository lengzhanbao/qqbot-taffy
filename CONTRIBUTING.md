# Contributing

## Before you start

- Read `docs/public/ARCHITECTURE.md`.
- Read `docs/public/MODULE_GUIDE.md`.
- Read `docs/public/MODULE_FUNCTIONS.md`.

## Do not leak secrets

- Do not commit `.env`.
- Do not commit real QQ IDs, group IDs, nicknames, chat logs, or memory files.
- Do not commit local machine paths or Tencent account session paths.
- Keep all public docs in `docs/public/`.

## Development flow

1. Create a feature branch:

```powershell
git checkout -b feature/your-change
```

2. Make a small change.
3. Run tests:

```powershell
python -m pytest tests/ -q
```

4. Run checks:

```powershell
python scripts/check_env.py
python scripts/smoke_test.py
python -m py_compile bridge_qq.py
```

5. Commit with a clear message:

```text
feat: ...
fix: ...
refactor: ...
test: ...
docs: ...
chore: ...
```

6. Do not use `git add .` unless you verified the file list.

## Before opening a PR

- Confirm `git status` only shows intended files.
- Confirm sensitive files are ignored.
- Run the demo build:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_demo.ps1
```

- Confirm the demo tests pass.
