# QQBot 模块拆分计划

> 状态：计划阶段，未执行  
> 目标：在不改变运行行为的前提下，把 6000+ 行的 `bridge_qq.py` 拆成职责清晰的模块，并让 Git 提交可回滚、可部署、可面试讲解

---

## 一.5 执行记录

### 尝试 1：整文件 facade

做法：

- 把 `bridge_qq.py` 复制成 `qqbot_core/legacy_bridge.py`。
- 让根入口 `bridge_qq.py` 只做兼容导入。

结果：

- 测试大量失败。
- 原因：函数定义在 `legacy_bridge.py`，但测试 monkeypatch `bridge_qq.*`，模块内部读取的是自己的 globals，不共享。

处理：

- 已回滚到备份版本。
- 改为“逐小块抽取模块，测试通过后再继续”。

### 尝试 2：抽取小菲风格模块

已完成：

```text
qqbot_core/style.py
tests/test_style_model.py
```

已从 `bridge_qq.py` 移出：

- `SYSTEM_PROMPT`
- `STYLE_PROMPT`
- `TAFFY_FLAVOR_PROMPT`
- `TAFFY_STYLE_PROMPT`
- `CURRENT_DATE_PROMPT`
- `SEARCH_STRATEGY_SYSTEM`
- `SEARCH_TOOL_FEWSHOT`
- `SEARCH_PLANNER_PERSONA`
- `SEARCH_PLANNING_RULES`
- `_is_taffy_styled`
- `_append_search_source_marker`

当前测试：`306 passed`。

下一步：

- ~~`style.py` 小菲风格~~ ✅
- ~~`prompts.py` 提示词常量~~ ✅
- ~~`text.py` 纯文本工具~~ ✅
- ~~`config.py` 配置~~ ✅
- `memory.py`
- `search.py`
- `model_gateway.py`
- `admin.py`
- `onebot.py`

## 一、为什么拆

当前 `bridge_qq.py` 承担了：

- 配置与环境变量
- 路径、日志、健康状态、统计
- 模型熔断与降级
- 提示词与角色风格
- 搜索、抓取、缓存、引擎降级
- 知识库与长期记忆
- 会话持久化
- 安全护栏
- 管理员命令
- OneBot 桥接与消息处理
- 自我改进学习

文件超过 6000 行，任何修改都容易互相影响。拆模块的目的不是“好看”，而是让每一块可以独立测试、独立演进、独立解释。

---

## 二、旧拆分脚本的问题

`refactor/` 里已经有一版机械拆分脚本：

- `split_agent.py`
- `split_gateway.py`
- `split_memory.py`
- `split_tools.py`
- `attempted_split_modules/`

这些脚本的做法是：

```python
import bridge_qq as _b
from bridge_qq import *
for _name in dir(_b):
    if _name.startswith('_') and not _name.startswith('__'):
        globals()[_name] = getattr(_b, _name)
```

问题：

- 被拆出的模块又反向导入 `bridge_qq`，形成循环依赖。
- 用 `from bridge_qq import *` 把全局状态复制得到处都是。
- 无法保证模块边界，无法独立测试。
- 一旦 `bridge_qq.py` 改名或拆解，这些脚本会失效。

结论：**不采用旧机械拆分方式**。

---

## 三、目标结构

```text
<项目根>\
├── bridge_qq.py                  # 兼容入口 + Bridge 主流程，尽量瘦身
├── requirements.txt              # 运行依赖
├── requirements-dev.txt          # 测试依赖
├── pytest.ini
├── .gitattributes
├── qqbot_core\
│   ├── __init__.py
│   ├── config.py                 # 路径、环境变量、模型、采样参数
│   ├── logging_health.py         # log、health.json、stats.json
│   ├── prompts.py                # 角色、风格、搜索策略、重答提示
│   ├── style.py                  # 小菲风格模型
│   ├── safety.py                 # 安全护栏
│   ├── stats.py                  # 运行统计
│   ├── learning.py               # 群聊进化学习
│   ├── memory.py                 # 长期记忆、会话、历史清洗
│   ├── search.py                 # 搜索、抓取、缓存、引擎降级、凝练
│   ├── model_gateway.py          # API 调用、流解析、熔断、竞速
│   ├── admin.py                  # 管理员命令
│   └── onebot.py                 # OneBot 消息解析、发送、桥接辅助
└── tests\
    ├── test_style_model.py
    ├── test_search.py
    ├── test_memory.py
    ├── test_model_gateway.py
    └── test_integration.py
```

---

## 四、模块职责

### 4.1 `config.py`

负责：

- 项目根目录、数据目录、日志目录。
- `.env` 加载。
- 模型列表、API 地址、超时、温度、搜索配置。
- 所有 `os.environ.get` 的默认值。

`bridge_qq.py` 只做：

```python
from qqbot_core.config import *
```

不再在 `bridge_qq.py` 内维护大量配置常量。

### 4.2 `logging_health.py`

负责：

- `log()`
- `_log_phase()`
- `write_health()`
- `read_health()`
- 日志清理。

`stats.py` 已独立，可以继续放这里或保持独立。

### 4.3 `prompts.py`

负责：

- 系统提示词。
- 角色提示词。
- 搜索策略提示词。
- 对话模式提示词。
- 抽象控制提示词。
- 重答提示词。

不负责：

- 具体调用模型。
- 判断是否触发搜索。

### 4.4 `style.py`

负责小菲风格模型：

- `TaffyProfile`
- `StyleSampling`
- `build_taffy_system()`
- `build_taffy_flavor()`
- `is_taffy_styled()`
- `append_search_source_marker()`

### 4.5 `memory.py`

负责：

- 长期记忆读取、关键词检索。
- 历史清洗、污染数据修复。
- 会话持久化。
- `!forget` / `!clear_memory` 底层操作。

### 4.6 `search.py`

负责：

- 搜索关键词生成。
- 搜索工具定义。
- 网页抓取。
- 缓存。
- 引擎失败统计与降级。
- 搜索结果过滤、去噪、凝练。
- 歌词深读。

### 4.7 `model_gateway.py`

负责：

- OpenAI 兼容 API 调用。
- 流式响应解析。
- 工具调用解析。
- 模型熔断、降级、竞速。
- `_complete`、`_complete_race`、`_complete_with_tools`。

### 4.8 `admin.py`

负责：

- 管理员权限判断。
- `!status`
- `!style`
- `!taffy`
- `!model`
- `!forget`
- `!clear_memory`

### 4.9 `onebot.py`

负责：

- OneBot 消息数组解析。
- 卡片解析。
- 引用消息提取。
- 发送消息。
- HTTP API 调用。

---

## 五、依赖方向

依赖必须单向：

```text
bridge_qq.py
  -> onebot.py
  -> admin.py
  -> model_gateway.py
  -> search.py
  -> memory.py
  -> prompts.py / style.py
  -> logging_health.py / stats.py
  -> config.py
```

禁止：

- `search.py` 导入 `bridge_qq.py`
- `model_gateway.py` 导入 `bridge_qq.py`
- `memory.py` 导入 `bridge_qq.py`
- 任何模块使用 `from bridge_qq import *`

如果两个模块都需要某个函数，把它放到更底层的公共模块，例如 `utils.py` 或 `text.py`。

---

## 六、兼容策略

拆分期间 `bridge_qq.py` 仍然是主入口，但要变成“兼容门面”：

```python
from qqbot_core.config import *
from qqbot_core.prompts import *
from qqbot_core.style import *
from qqbot_core.search import *
from qqbot_core.memory import *
from qqbot_core.model_gateway import *
from qqbot_core.admin import *
from qqbot_core.onebot import *
```

测试继续 `import bridge_qq`，不要求测试一次性改完。

当某个模块稳定后，再把 `bridge_qq.py` 中对应代码删掉。

---

## 七、执行阶段

每个阶段必须：

- 先备份。
- 再改。
- 跑 `pytest`。
- 跑 `py_compile bridge_qq.py`。
- 可重启验证。
- 每阶段一个 Git commit。

### Phase 0：基线备份

```text
scripts/backup.ps1 -Tag before_module_split
```

验收：

- 当前 `296 passed`。
- `bridge_qq.py` 能正常启动。

### Phase 1：抽取 `config.py`

动作：

- 把路径常量、环境变量、模型列表、温度、搜索配置移入 `qqbot_core/config.py`。
- `bridge_qq.py` 改为从 `config.py` 导入。

验收：

- 所有测试通过。
- 启动行为不变。

### Phase 2：抽取 `prompts.py` 和 `style.py`

动作：

- 移动角色、风格、搜索策略、重答提示词。
- 新增 `TaffyProfile`、`StyleSampling`。
- 新增 `tests/test_style_model.py`。

验收：

- 小菲风格可独立测试。
- 不改变最终 prompt 内容。

### Phase 3：抽取 `memory.py`

动作：

- 移动知识库检索。
- 移动历史清洗。
- 移动 `SessionStore`。

验收：

- `bot_memory.md`、`bridge_sessions.json` 行为不变。
- 记忆相关测试通过。

### Phase 4：抽取 `search.py`

动作：

- 移动搜索关键词、抓取、缓存、降级、凝练、歌词深读。

验收：

- `search_with_resilience` 可独立测试。
- `search_web_async` 可独立测试。
- 搜索行为不变。

### Phase 5：抽取 `model_gateway.py`

动作：

- 移动 API 调用、流解析、工具调用恢复、熔断、竞速。

验收：

- 模型降级测试通过。
- 工具调用恢复测试通过。
- 流式解析测试通过。

### Phase 6：抽取 `admin.py`

动作：

- 移动管理员命令。

验收：

- 管理员命令测试通过。
- 非管理员命令仍静默忽略。

### Phase 7：抽取 `onebot.py`

动作：

- 移动卡片解析、文本提取、引用消息、发送 API。

验收：

- 集成测试通过。
- `bridge_qq.py` 只保留 Bridge 主流程。

### Phase 8：收尾

- 删除 `bridge_qq.py` 中已迁移的重复代码。
- 确认 `bridge_qq.py` 只做编排。
- 跑全量测试。
- 生成脱敏 demo。
- 按 Git 提交计划提交。

---

## 八、Git 提交计划

```text
chore: backup before module split
refactor: extract config module
refactor: extract prompts and style modules
test: add taffy style model tests
refactor: extract memory module
refactor: extract search module
refactor: extract model gateway module
refactor: extract admin module
refactor: extract onebot module
docs: update architecture and interview
chore: rebuild sanitized demo
```

每个 commit 只做一件事。

---

## 九、风险与对策

### 9.1 循环依赖

风险：

- `search.py` 需要 `_current_date_text`。
- `model_gateway.py` 需要 `log`、`write_health`、`inc_stat`。

对策：

- 公共底层函数放 `config.py`、`logging_health.py`、`utils.py`。
- 禁止模块反向导入 `bridge_qq.py`。

### 9.2 行为变化

风险：

- 移动代码时遗漏全局变量。
- 导入顺序变化导致 `.env` 未加载。

对策：

- 每阶段只移动一个职责。
- 每阶段跑测试。
- 每阶段重启验证。

### 9.3 测试依赖 `bridge_qq` 命名

风险：

- 测试直接访问 `bridge_qq._search_cache`。

对策：

- `bridge_qq.py` 保留兼容导入。
- 后续逐步把测试改为导入 `qqbot_core.*`。

### 9.4 旧 `refactor/` 脚本误用

风险：

- 有人继续执行 `refactor/split_*.py`。

对策：

- 将 `refactor/` 标记为“已废弃，不执行”。
- 计划执行期间不调用这些脚本。

---

## 十、完成标准

拆分完成的标准：

- `bridge_qq.py` 只剩 Bridge 编排和兼容导入。
- `qqbot_core/` 每个模块职责单一。
- 模块之间无反向依赖。
- 全量测试通过。
- `py_compile` 通过。
- 机器人能启动并回复。
- demo 副本能独立安装运行。
- Git 历史按模块拆分可回滚。

---

## 十一、最终建议

不要一次拆完，按 Phase 0-8 执行。

每一步都是“备份 -> 抽取 -> 测试 -> 提交”。

这样即使某一步出错，也不会破坏当前能跑的 QQBot。
