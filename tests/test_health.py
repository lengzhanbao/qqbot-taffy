"""health.json 读写与 E 盘路径测试。"""
import json
import os
import uuid

import bridge_qq
import pytest


@pytest.fixture
def e_tmp_file():
    path = os.path.join(bridge_qq.TMP_DIR, "health_test_" + uuid.uuid4().hex + ".json")
    os.makedirs(bridge_qq.TMP_DIR, exist_ok=True)
    yield path
    for p in (path, path + ".tmp"):
        if os.path.exists(p):
            os.remove(p)


def test_health_file_is_on_e_drive():
    assert bridge_qq.HEALTH_FILE.startswith("E:\\")


def test_write_health_creates_file(monkeypatch, e_tmp_file):
    monkeypatch.setattr(bridge_qq, "HEALTH_FILE", e_tmp_file)
    monkeypatch.delenv("QQ_TEST_MODE", raising=False)

    bridge_qq.write_health(pid=123, ws_connected=True)

    assert os.path.exists(bridge_qq.HEALTH_FILE)
    with open(bridge_qq.HEALTH_FILE, encoding="utf-8") as f:
        data = json.load(f)
    assert data["pid"] == 123
    assert data["ws_connected"] is True
    assert data["updated_at"]


def test_write_health_is_incremental(monkeypatch, e_tmp_file):
    monkeypatch.setattr(bridge_qq, "HEALTH_FILE", e_tmp_file)
    monkeypatch.delenv("QQ_TEST_MODE", raising=False)

    bridge_qq.write_health(pid=1)
    bridge_qq.write_health(ws_connected=True)

    data = bridge_qq.read_health()
    assert data["pid"] == 1
    assert data["ws_connected"] is True


def test_health_defaults_include_message_timestamps(monkeypatch, e_tmp_file):
    monkeypatch.setattr(bridge_qq, "HEALTH_FILE", e_tmp_file)
    monkeypatch.delenv("QQ_TEST_MODE", raising=False)

    bridge_qq.write_health(pid=1)

    data = bridge_qq.read_health()
    assert data["last_message_received_at"] == ""
    assert data["last_reply_sent_at"] == ""


def test_write_health_leaves_no_tmp_file(monkeypatch, e_tmp_file):
    monkeypatch.setattr(bridge_qq, "HEALTH_FILE", e_tmp_file)
    monkeypatch.delenv("QQ_TEST_MODE", raising=False)

    bridge_qq.write_health(pid=1)

    assert not os.path.exists(e_tmp_file + ".tmp")


def test_read_health_missing_returns_none(monkeypatch, e_tmp_file):
    monkeypatch.setattr(bridge_qq, "HEALTH_FILE", e_tmp_file)
    assert bridge_qq.read_health() is None


def test_read_health_invalid_json_returns_none(monkeypatch, e_tmp_file):
    with open(e_tmp_file, "w", encoding="utf-8") as f:
        f.write("{bad")
    monkeypatch.setattr(bridge_qq, "HEALTH_FILE", e_tmp_file)
    assert bridge_qq.read_health() is None
