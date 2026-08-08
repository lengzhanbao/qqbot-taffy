"""群聊进化学习模块测试。"""
import qqbot_core.learning as learning


def test_record_correction_writes_learning_file(monkeypatch, tmp_path):
    monkeypatch.setenv("QQ_TEST_MODE", "0")
    monkeypatch.setattr(
        learning,
        "LEARNINGS_FILE",
        str(tmp_path / "learnings.md"),
    )
    learning.record_group_learning(
        "group-1",
        "user-a",
        "不是那个，我说的是曼波小马",
        "塔菲记下了喵",
    )
    content = (tmp_path / "learnings.md").read_text(encoding="utf-8")
    assert "correction" in content
    assert "曼波小马" in content
