"""群聊长期学习模块：只记录，不主动注入，避免污染当前回答。"""
import os
import re
import threading
from datetime import datetime


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEARNINGS_FILE = os.path.join(BASE_DIR, "data", "learnings.md")
_learning_lock = threading.Lock()

_CORRECTION_RE = re.compile(
    r"不是|应该是|其实是|我说的是|我是说的|你错了|记错|纠正|不对吧|应该叫"
)


def record_group_learning(scope, user, text, reply=""):
    """记录群聊中的纠正、梗和值得长期保留的信息。"""
    if os.environ.get("QQ_TEST_MODE") == "1":
        return
    if not text or not str(text).strip():
        return
    kind = "correction" if _CORRECTION_RE.search(text) else "learning"
    if kind == "learning" and not re.search(r"梗|记住|以后|约定|是(.{1,8})的", text):
        return
    line = (
        f"- {datetime.now().isoformat()} | {scope} | {user} | {kind} | "
        f"{text[:200]} | {reply[:200]}"
    )
    with _learning_lock:
        try:
            os.makedirs(os.path.dirname(LEARNINGS_FILE), exist_ok=True)
            with open(LEARNINGS_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
