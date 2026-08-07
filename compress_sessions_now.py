# -*- coding: utf-8 -*-
"""一次性压缩所有群/私聊会话历史，保留摘要与原文位置标记。"""
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, r"<项目根>")
spec = importlib.util.spec_from_file_location("bridge_qq", r"<项目根>\bridge_qq.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


async def main():
    bridge = mod.Bridge()
    keys = list(bridge.sessions.data.keys())
    print("开始压缩", len(keys), "个会话")
    for key in keys:
        await bridge._compress_history(key)
    print("压缩完成")


if __name__ == "__main__":
    asyncio.run(main())
