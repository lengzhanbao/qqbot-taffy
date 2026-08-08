# QQBot Git 提交计划

> 状态：计划阶段，未提交  
> 目标：让 Git 历史清晰可回滚，并把“永雏塔菲风格模型”从 `bridge_qq.py` 中抽成独立模块

---

## 一、当前状态

### 1.1 已适合进入 Git

- `qqbot_core/`：stats、safety、learning 三个模块。
- `tests/`：296 个测试通过。
- `pytest.ini`、`.gitattributes`、`CHANGELOG.md`。
- `docs/public/`：脱敏后的架构、面试、复盘文档。
- `demo/`：脱敏 demo 说明与构建脚本。
- `skills/group-evolution/`：群聊进化学习 skill。

### 1.2 还不适合一次性提交

- `bridge_qq.py` 仍然超过 6000 行。
- `QQ_ALWAYS_SEARCH=1` 时，模型未调用搜索会忽略直接回复并强制通用联网。
- 小菲风格相关代码还散在：
  - `SYSTEM_PROMPT`
  - `TAFFY_FLAVOR_PROMPT`
  - `_is_taffy_styled`
  - `_restyle_as_taffy`
  - `FACTUAL_TEMPERATURE`
  - `CASUAL_TEMPERATURE`
  - `_append_search_source_marker`
- 如果一次性 `git add .`，会把无关脚本、旧 demo build、本地文件混进同一个 commit。

---

## 二、建议提交顺序

### Commit 0：部署就绪

```text
chore: make project deployable from a clean clone
```

必须新增：

```text
requirements.txt
docs/public/SETUP.md
scripts/check_env.py
scripts/smoke_test.py
.github/workflows/ci.yml
```

`requirements.txt` 至少包含：

```text
websockets>=12.0
requests>=2.31.0
```

原因：

- `requirements-dev.txt` 只有 pytest，不能部署运行。
- `bridge_qq.py` 依赖 `websockets`，网络抓取依赖 `requests`。
- 别人 clone 后必须能直接安装运行依赖。

`docs/public/SETUP.md` 必须覆盖：

1. Python 3.11+。
2. 安装依赖：

```powershell
pip install -r requirements.txt -r requirements-dev.txt
```

3. 复制 `config/.env.example` 为 `.env`，填写 API Key 和管理员 QQ。
4. 启动 NapCat，配置 OneBot v11 WebSocket 和 HTTP。
5. 启动桥接：

```powershell
python bridge_qq.py
```

6. 验证：

```powershell
python -m pytest tests/ -q
python scripts/check_env.py
python scripts/smoke_test.py
```

CI：

```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: python -m pytest tests/ -q
```

### Commit 2：项目元信息

```text
chore: add git baseline metadata
```

文件：

- `.gitignore`
- `.gitattributes`
- `pytest.ini`
- `CHANGELOG.md`

验收：

- `git status` 只显示预期文件。
- `.env`、`data/`、`logs/`、敏感 `docs/` 被忽略。

### Commit 3：模块化核心包

```text
feat: add modular core package
```

文件：

- `qqbot_core/__init__.py`
- `qqbot_core/stats.py`
- `qqbot_core/safety.py`
- `qqbot_core/learning.py`
- `bridge_qq.py`

验收：

- `bridge_qq.py` 仍然能作为主入口运行。
- `qqbot_core.stats` 跨重启累计。
- `qqbot_core.safety` 保持原安全行为。
- `qqbot_core.learning` 记录群聊纠正。

### Commit 4：测试

```text
test: add integration, search stability, stats and learning tests
```

文件：

- `tests/test_integration.py`
- `tests/test_search_stability.py`
- `tests/test_stats.py`
- `tests/test_learning.py`
- 相关已有测试文件

验收：

- `pytest` 全量通过。
- demo 副本也能独立跑通测试。

### Commit 5：文档与 demo

```text
docs: add public architecture, interview, review and demo docs
```

文件：

- `README.md`
- `docs/public/ARCHITECTURE.md`
- `docs/public/INTERVIEW.md`
- `docs/public/复盘_15天计划.md`
- `demo/README.md`
- `scripts/build_demo.ps1`
- `skills/group-evolution/SKILL.md`

验收：

- 仓库只包含脱敏文档。
- demo 构建产物不进入 Git。
- demo 必须保持目录结构：

```text
demo/build_*/scripts/backup.ps1
demo/build_*/scripts/restore.ps1
demo/build_*/scripts/status.ps1
demo/build_*/config/.env.example
demo/build_*/docs/public/ARCHITECTURE.md
```

### Commit 6：工具脚本

```text
chore: add utility and test scripts
```

文件：

- `scripts/clean_polluted_data.py`
- `scripts/test_20_questions.py`
- `scripts/test_20_questions_2026.py`
- `scripts/test_random_5.py`
- `scripts/test_trace_10.py`

验收：

- 脚本中不包含真实 API Key。
- 脚本不读取真实 `.env` 作为唯一运行来源。

---

## 三、小菲风格模型：现状

### 3.1 当前实现

当前风格主要靠以下代码：

```python
SYSTEM_PROMPT = (
    "你是永雏塔菲，群里一个真人朋友，二次元浓度高，活泼可爱，"
    "会整活、接梗、自嘲、一本正经地抽象。怎么说话由你自己判断。"
)

TAFFY_FLAVOR_PROMPT = (
    "\n【角色：永雏塔菲】\n"
    "用 taffy / 塔菲 / 小菲自称；喵有节制；语气活泼可爱，"
    "可以用“喵”“呀”“诶”“嘻嘻”这类语气词，但不要每句都加；"
    "可以整活、玩梗、自嘲、抽象。"
)
```

采样参数：

```python
FACTUAL_TEMPERATURE = 0.7
FACTUAL_TOP_P = 0.8
CASUAL_TEMPERATURE = 1.1
CASUAL_TOP_P = 1.0
```

回答来源标注：

```python
def _append_search_source_marker(reply, search_returned, search_attempted=False):
    ...
```

### 3.2 现状问题

1. 风格常量还在 `bridge_qq.py`。
2. `_is_taffy_styled` 和 `_restyle_as_taffy` 与主流程耦合。
3. 温度和 `top_p` 靠环境变量，没有统一配置对象。
4. 没有独立的“风格模型”概念，后续想调活泼度、自称、语气词都只能改主文件。

---

## 四、小菲风格模型：目标设计

新建：

```text
qqbot_core/style.py
```

核心职责：

- 定义小菲人物档案。
- 生成 system 提示词。
- 判断回答是否具有小菲风格。
- 追加来源标注。
- 提供温度参数建议。

示例结构：

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class TaffyProfile:
    name: str = "永雏塔菲"
    self_refs: tuple = ("taffy", "塔菲", "小菲")
    mood: str = "活泼可爱"
    miao_limit: str = "喵有节制"
    allowed_vibes: tuple = ("整活", "玩梗", "自嘲", "抽象")


def build_taffy_system(profile: TaffyProfile) -> str:
    return (
        f"你是{profile.name}，群里一个真人朋友，二次元浓度高，{profile.mood}，"
        "会整活、接梗、自嘲、一本正经地抽象。怎么说话由你自己判断。"
    )


def build_taffy_flavor(profile: TaffyProfile) -> str:
    refs = " / ".join(profile.self_refs)
    vibes = "、".join(profile.allowed_vibes)
    return (
        f"\n【角色：{profile.name}】\n"
        f"用 {refs} 自称；{profile.miao_limit}；语气活泼可爱，"
        "可以用“喵”“呀”“诶”“嘻嘻”这类语气词，但不要每句都加；"
        f"可以{vibes}。"
    )


def is_taffy_styled(reply: str) -> bool:
    ...


def append_search_source_marker(
    reply: str,
    search_returned: bool,
    search_attempted: bool = False,
) -> str:
    ...
```

### 4.1 如何实现

#### 第 1 步：抽取风格模块

把 `SYSTEM_PROMPT`、`TAFFY_FLAVOR_PROMPT`、`_is_taffy_styled`、`_append_search_source_marker` 移到 `qqbot_core/style.py`。

`bridge_qq.py` 保留兼容导入：

```python
from qqbot_core.style import (
    SYSTEM_PROMPT,
    TAFFY_FLAVOR_PROMPT,
    _is_taffy_styled,
    _append_search_source_marker,
)
```

#### 第 2 步：加入风格配置对象

在 `style.py` 中新增：

```python
@dataclass(frozen=True)
class StyleSampling:
    factual_temperature: float = 0.7
    factual_top_p: float = 0.8
    casual_temperature: float = 1.1
    casual_top_p: float = 1.0
```

由环境变量加载：

```python
import os

def load_sampling_from_env() -> StyleSampling:
    return StyleSampling(
        factual_temperature=float(os.environ.get("QQ_FACTUAL_TEMPERATURE", "0.7")),
        factual_top_p=float(os.environ.get("QQ_FACTUAL_TOP_P", "0.8")),
        casual_temperature=float(os.environ.get("QQ_CASUAL_TEMPERATURE", "1.1")),
        casual_top_p=float(os.environ.get("QQ_CASUAL_TOP_P", "1.0")),
    )
```

#### 第 3 步：加入风格校验

```python
def is_taffy_styled(reply: str) -> bool:
    text = (reply or "").lower()
    has_identity = any(m in text for m in ("塔菲", "taffy", "小菲"))
    has_mood = any(m in text for m in ("喵", "嘻嘻", "哈哈", "！"))
    return has_identity and has_mood
```

#### 第 4 步：来源标注也进风格模块

```python
def append_search_source_marker(reply, search_returned, search_attempted=False):
    ...
```

#### 第 5 步：测试

新建：

```text
tests/test_style_model.py
```

测试：

- `build_taffy_system` 包含人物特色。
- `build_taffy_flavor` 包含自称和“喵有节制”。
- `is_taffy_styled` 对小菲回答返回 True。
- `is_taffy_styled` 对客服腔返回 False。
- `append_search_source_marker` 正确追加三种来源标注。
- 温度和 `top_p` 能从环境变量读取。

---

## 五、小菲风格模型提交顺序

### Commit 7：抽取风格模块

```text
refactor: extract taffy style model to qqbot_core.style
```

只做移动，不改变行为。

### Commit 8：加入风格配置对象

```text
feat: add taffy style profile and sampling config
```

新增 `TaffyProfile`、`StyleSampling`。

### Commit 9：加入风格校验和来源标注测试

```text
test: add taffy style model tests
```

新增 `tests/test_style_model.py`。

---

## 六、每个 commit 的验收标准

每个 commit 前必须：

- `pytest` 通过。
- `py_compile bridge_qq.py` 通过。
- `git diff --cached` 不含 `.env`、真实数据、真实日志。
- commit message 遵循：

```text
feat: ...
fix: ...
refactor: ...
test: ...
docs: ...
chore: ...
```

---

## 七、最终目标

完成之后，Git 历史会是：

```text
chore: make project deployable from a clean clone
chore: add git baseline metadata
feat: add modular core package
test: add integration and stability tests
docs: add public docs and demo
chore: add utility scripts
refactor: extract taffy style model
feat: add taffy style profile and sampling config
test: add taffy style model tests
```

这样的历史：

- 每个 commit 只做一件事。
- 每个 commit 可回滚。
- 小菲风格模型有独立模块和测试。
- 之后想调风格，不用再改 6000 行主文件。

### 最终部署验收

从空白目录 clone 后必须能完成：

```powershell
git clone <repo-url>
cd qqbot
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
copy config\.env.example .env
python -m pytest tests/ -q
python scripts\check_env.py
python bridge_qq.py
```

只有满足这个流程，Git 提交才算“部署可复现”。
