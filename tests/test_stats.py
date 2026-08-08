"""Day12 统计模块测试。"""
import json

import bridge_qq
import qqbot_core.stats as stats_module


def test_inc_stat_updates_in_memory():
    key = "total_messages"
    old = int(bridge_qq._stats.get(key, 0))
    try:
        bridge_qq.inc_stat(key)
        assert bridge_qq._stats[key] == old + 1
    finally:
        bridge_qq._stats[key] = old


def test_record_reply_time_updates_avg():
    old_replies = int(bridge_qq._stats.get("total_replies", 0))
    old_sum = int(bridge_qq._stats.get("reply_time_sum_ms", 0))
    try:
        bridge_qq.record_reply_time(2500)
        assert bridge_qq._stats["total_replies"] == old_replies + 1
        assert bridge_qq._stats["reply_time_sum_ms"] == old_sum + 2500
        assert bridge_qq.get_avg_reply_time() > 0
    finally:
        bridge_qq._stats["total_replies"] = old_replies
        bridge_qq._stats["reply_time_sum_ms"] = old_sum


def test_flush_stats_writes_file_while_lock_held(monkeypatch, tmp_path):
    monkeypatch.setenv("QQ_TEST_MODE", "0")
    monkeypatch.setattr(
        stats_module,
        "STATS_FILE",
        str(tmp_path / "stats.json"),
    )
    bridge_qq._stats["total_replies"] = 2
    bridge_qq._stats["reply_time_sum_ms"] = 4000
    with bridge_qq._stats_lock:
        bridge_qq._flush_stats()
    data = bridge_qq.json.loads((tmp_path / "stats.json").read_text(
        encoding="utf-8"))
    assert data["avg_reply_time_ms"] == 2000.0


def test_load_stats_keeps_counts_across_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("QQ_TEST_MODE", "0")
    stats_file = tmp_path / "stats.json"
    stats_file.write_text(json.dumps({
        "total_messages": 10,
        "total_replies": 5,
        "model_calls": 7,
    }), encoding="utf-8")
    monkeypatch.setattr(stats_module, "STATS_FILE", str(stats_file))
    old = dict(stats_module._stats)
    try:
        stats_module._stats.update({
            "total_messages": 0,
            "total_replies": 0,
            "model_calls": 0,
        })
        stats_module._load_stats()
        assert stats_module._stats["total_messages"] == 10
        assert stats_module._stats["total_replies"] == 5
        assert stats_module._stats["model_calls"] == 7
    finally:
        stats_module._stats.clear()
        stats_module._stats.update(old)
