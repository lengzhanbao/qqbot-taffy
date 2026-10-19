"""运行统计模块：stats.json 跨重启累计。"""
import json
import os
import threading
from datetime import datetime


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RUNTIME_DIR = (os.environ.get("QQ_TEST_DATA_DIR") or os.path.join(BASE_DIR, ".tmp", "test-runtime")) if os.environ.get("QQ_TEST_MODE") == "1" else BASE_DIR
STATS_FILE = os.path.join(_RUNTIME_DIR, "data", "stats.json")
_stats_lock = threading.Lock()
_stats = {
    "total_messages": 0,
    "total_replies": 0,
    "total_errors": 0,
    "reply_time_sum_ms": 0,
    "model_calls": 0,
    "model_success": 0,
    "model_fail": 0,
    "model_circuit_open_count": 0,
    "search_calls": 0,
    "search_success": 0,
    "search_fail": 0,
    "search_cache_hits": 0,
    "safety_blocks": 0,
    "started_at": datetime.now().isoformat(),
}


def _load_stats():
    if os.environ.get("QQ_TEST_MODE") == "1":
        return
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    if not isinstance(data, dict):
        return
    with _stats_lock:
        for key in ("started_at", "updated_at", "avg_reply_time_ms"):
            if key in data:
                _stats[key] = data[key]
        for key in _stats:
            if key in ("started_at", "updated_at", "avg_reply_time_ms"):
                continue
            if isinstance(data.get(key), (int, float)):
                _stats[key] = int(data[key])
        _stats["updated_at"] = datetime.now().isoformat()


def inc_stat(key, n=1):
    """原子递增统计并落盘。"""
    with _stats_lock:
        _stats[key] = int(_stats.get(key, 0)) + n
        _stats["updated_at"] = datetime.now().isoformat()
        _flush_stats()


def record_reply_time(ms):
    """记录一次回复耗时。"""
    with _stats_lock:
        _stats["total_replies"] = int(_stats.get("total_replies", 0)) + 1
        _stats["reply_time_sum_ms"] = int(
            _stats.get("reply_time_sum_ms", 0)) + max(0, int(ms))
        _stats["updated_at"] = datetime.now().isoformat()
        _flush_stats()


def get_avg_reply_time():
    with _stats_lock:
        count = int(_stats.get("total_replies", 0))
        if count == 0:
            return 0.0
        return round(int(_stats.get("reply_time_sum_ms", 0)) / count, 1)


def _flush_stats():
    if os.environ.get("QQ_TEST_MODE") == "1":
        return
    tmp = dict(_stats)
    tmp["avg_reply_time_ms"] = round(
        int(tmp.get("reply_time_sum_ms", 0))
        / max(int(tmp.get("total_replies", 0)), 1),
        1,
    )
    try:
        os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
        tmp_path = STATS_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(tmp, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, STATS_FILE)
    except Exception:
        pass


_load_stats()
