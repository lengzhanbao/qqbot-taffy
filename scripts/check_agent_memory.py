# -*- coding: utf-8 -*-
"""持久记忆部署冒烟：等价于在群里执行 `!memory status`。

按 bridge 相同的方式加载授权库与记忆库，只做只读诊断：
不启动第二个桥接、不发送 QQ 消息、不写入合成记忆。

用法：
    python scripts/check_agent_memory.py
    python scripts/check_agent_memory.py --group 123456 --user 10001
"""
import argparse
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(BASE, ".env"))

from qqbot_core import config as C  # noqa: E402
from qqbot_core.agent_memory import AgentMemory  # noqa: E402
from qqbot_core.group_policy import GroupPolicy  # noqa: E402

SCOPES = ("observe", "style_learning", "knowledge_learning",
          "knowledge_retrieval", "proactive_chat")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", default=os.environ.get("QQ_SMOKE_GROUP", ""))
    parser.add_argument("--user", default=os.environ.get("QQ_SMOKE_USER", ""))
    args = parser.parse_args()

    if os.environ.get("QQ_TEST_MODE") == "1":
        print("!! 当前 QQ_TEST_MODE=1：本脚本检查的是测试运行时目录，"
              "不是生产数据。")
        print("!! 检查生产请在不设置该变量的终端里运行。\n")

    print("KNOWLEDGE_SYSTEM     =", C.KNOWLEDGE_SYSTEM)
    print("AGENT_MEMORY_ENABLED =", C.AGENT_MEMORY_ENABLED)
    print("AGENT_MEMORY_DIR     =", C.AGENT_MEMORY_DIR)
    print("LIMIT_GB             =", round(C.AGENT_MEMORY_LIMIT_BYTES / 1024 ** 3, 1))
    print("EMBEDDING_PATH       =", C.AGENT_MEMORY_EMBEDDING_PATH,
          "(存在)" if os.path.isdir(C.AGENT_MEMORY_EMBEDDING_PATH) else "(不存在)")
    if not C.AGENT_MEMORY_ENABLED:
        print("\n记忆未启用：把 .env 的 QQ_AGENT_MEMORY 设为 1 并重启桥接后再跑本脚本。")
        return 1
    if not args.group:
        print("\n未提供群号：用 --group 指定要检查的群（授权库按群存储）。")
        return 1

    # 注意：GroupPolicy 的第一个参数是管理员列表，不是数据库路径。
    policy = GroupPolicy(C.ADMIN_UIDS, persistence_path=C.GROUP_POLICY_FILE)
    for scope in SCOPES:
        print(f"  群授权 {scope:22} = {policy.allows(args.group, scope)}")

    memory_root = os.path.realpath(C.AGENT_MEMORY_DIR)
    db_path = os.path.join(memory_root, "memory.sqlite3")
    print("记忆库此前存在 =", os.path.exists(db_path))

    memory = AgentMemory(
        memory_root, policy,
        legacy_retriever=None,
        limit_bytes=C.AGENT_MEMORY_LIMIT_BYTES,
        warning_ratio=C.AGENT_MEMORY_WARNING_RATIO,
        legacy_paths=(C.SESSIONS_FILE, C.GROUP_STYLES_FILE, C.KNOWLEDGE_FILE),
        embedding_path=C.AGENT_MEMORY_EMBEDDING_PATH,
    )
    for command in ("status", "stats"):
        try:
            print(f"!memory {command} ->", memory.command(command, args.group, args.user))
        except Exception as exc:  # noqa: BLE001
            print(f"!memory {command} -> <异常 {type(exc).__name__}: {exc}>")
    print("记忆库现已存在 =", os.path.exists(db_path))
    if os.path.exists(db_path):
        print("memory.sqlite3 大小 =", os.path.getsize(db_path), "字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
