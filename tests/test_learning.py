"""群聊进化学习模块测试。"""
import qqbot_core.learning as learning


def test_legacy_correction_cannot_write_trusted_memory(monkeypatch, tmp_path):
    import bridge_qq
    path = tmp_path / "bot_memory.md"
    path.write_text("原有知识，不得覆盖", encoding="utf-8")
    monkeypatch.setattr(bridge_qq, "MEMORY_FILE", str(path))
    monkeypatch.setattr(bridge_qq, "ENABLE_GENERATED_MEMORY", True)
    result = learning.record_group_learning(
        "group-1",
        "user-a",
        "不是那个，我说的是曼波小马",
        "塔菲记下了喵",
    )
    bridge_qq._try_self_improve("group-1", "user-a", "其实是曼波小马", "错误回复")
    assert result is None
    assert path.read_text(encoding="utf-8") == "原有知识，不得覆盖"
    assert not (tmp_path / "learnings.md").exists()
