# 群聊进化学习 Skill

## 目标

让 QQBot 在群聊中长期学习，但不过度主动复述旧梗、旧记忆。

## 设计原则

- 只记录，不主动注入。
- 用户纠正时写入 `data/learnings.md`。
- 新梗、约定、人物修正也写入。
- 当前回答链路不自动读取这些内容，避免旧记忆污染。
- 后续可增加“明确提到某关键词时再检索对应记忆块”。

## 使用流程

1. 用户消息包含“不是/应该是/我说的是/我是说的/你错了”等纠正词。
2. `qqbot_core/learning.py::record_group_learning` 记录：
   - 时间
   - 群/私聊 scope
   - 用户
   - 类型：`correction` / `learning`
   - 用户原文
   - 机器人回复
3. 写入 `data/learnings.md`。
4. 测试模式不落盘。

## 文件

- `qqbot_core/learning.py`：学习模块。
- `data/learnings.md`：长期学习文件。
- `tests/test_learning.py`：测试。

## 后续进化

当某个纠正出现 3 次以上时，可提升为可靠记忆：

```text
明确纠正 3 次
  -> 写入 bot_memory.md 的常识补充
  -> 只有用户问到时才注入
```
