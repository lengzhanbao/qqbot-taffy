"""A refused quota-accounted snapshot must never be reported as successful."""
import json

import pytest

from scripts import backup_agent_memory as helper


@pytest.mark.parametrize("refused", [True, False])
def test_snapshot_helper_requires_an_existing_snapshot(monkeypatch, tmp_path, capsys, refused):
    import qqbot_core.config as config
    import qqbot_core.agent_memory as memory_module
    import qqbot_core.group_policy as policy_module

    root = tmp_path / "data" / "agent_memory"
    root.mkdir(parents=True)
    (root / "memory.sqlite3").touch()
    monkeypatch.setattr(helper, "ROOT", tmp_path)
    monkeypatch.setattr(config, "AGENT_MEMORY_DIR", str(root))

    class Store:
        def snapshot(self, target):
            if refused:
                return None
            target.parent.mkdir(parents=True)
            target.touch()
            return target

    class Memory:
        def __init__(self, *args, **kwargs):
            self.store = Store()

    class Policy:
        persistence_ok = True

        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(memory_module, "AgentMemory", Memory)
    monkeypatch.setattr(policy_module, "GroupPolicy", Policy)
    if refused:
        with pytest.raises(RuntimeError, match="refused or incomplete"):
            helper.main()
        assert not capsys.readouterr().out
    else:
        helper.main()
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "ok"
        assert str(root) in report["snapshot"]
