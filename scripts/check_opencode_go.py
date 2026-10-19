# -*- coding: utf-8 -*-
"""OpenCode Go 联通自检：确认 .env 里的 key 是否已有 Go 套餐订阅。

用法（项目实际使用的解释器）：
    <用户目录>\\AppData\\Local\\Programs\\Python\\Python311\\python.exe scripts\\check_opencode_go.py

退出码：0=Go 可用，1=未订阅或不可用。
"""
import json
import os
import sys
import uuid

import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

KEY = os.environ.get("OPENCODE_GO_API_KEY", "")
BASE_URL = os.environ.get(
    "OPENCODE_GO_BASE_URL", "https://opencode.ai/inference/go/openai/v1"
).rstrip("/")
MODEL = (
    os.environ.get("QQ_FINAL_MODEL", "opencode/deepseek-v4.1-flash").split("/", 1)[-1]
)
# 与 bridge_qq 一致：OpenCode 要求自有 UA + 稳定会话头
USER_AGENT = os.environ.get(
    "QQ_API_USER_AGENT", "QQBot-Taffy/1.0 (+https://github.com/lengzhanbao/qqbot-taffy)"
)


def main():
    if not KEY:
        print("[×] .env 里没有 OPENCODE_GO_API_KEY")
        return 1
    print(f"端点   : {BASE_URL}/chat/completions")
    print(f"模型   : {MODEL}")
    print(f"key    : {KEY[:6]}...（长度 {len(KEY)}）")
    try:
        resp = requests.post(
            f"{BASE_URL}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {KEY}",
                "User-Agent": USER_AGENT,
                "x-opencode-session": str(uuid.uuid4()),
            },
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": "你是测试助手。"},
                    {"role": "user", "content": "只回答两个字：收到"},
                ],
                "max_tokens": 16,
                "stream": True,
            },
            stream=True,
            timeout=(10, 40),
        )
    except Exception as exc:
        print(f"[×] 连接失败：{type(exc).__name__}: {exc}")
        return 1

    if resp.status_code != 200:
        body = resp.text[:300]
        print(f"[×] HTTP {resp.status_code}\n    {body}")
        if resp.status_code == 403:
            print("\n→ 这把 key 还没有 OpenCode Go 订阅。")
            print("  请到 OpenCode Console 订阅 Go / Go Plus，然后复制新的 API key 填回 .env。")
        elif "MissingSessionID" in body:
            print("\n→ 缺少 x-opencode-session 头（本脚本已带；若仍报此错请检查代理是否剥离了该头）。")
        return 1

    chunks = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except Exception:
            continue
        for ch in obj.get("choices") or []:
            piece = (ch.get("delta") or {}).get("content")
            if piece:
                chunks.append(piece)
    text = "".join(chunks).strip()
    if text:
        print(f"[√] Go 套餐可用，模型输出：{text!r}")
    else:
        print("[√] Go 套餐可用（本次没返回正文，属模型偶发空回复，重跑一次即可）")
    print("\n现在重启桥接即可让机器人在线使用它：")
    print(r"   Start-Process -FilePath '<用户目录>\AppData\Local\Programs\Python\Python311\pythonw.exe' "
          r"-ArgumentList 'restart_bridge_hidden.py' -WorkingDirectory '<项目根>' -WindowStyle Hidden")
    return 0


if __name__ == "__main__":
    sys.exit(main())
