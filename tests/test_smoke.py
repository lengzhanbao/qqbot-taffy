"""冒烟测试：确认 bridge_qq 可 import，主入口和启动自检存在。"""
import bridge_qq


def test_import_success():
    assert bridge_qq is not None


def test_main_callable():
    assert callable(getattr(bridge_qq, "main", None))


def test_startup_checks_callable():
    assert callable(getattr(bridge_qq, "run_startup_checks", None))


def test_startup_checks_returns_bool():
    result = bridge_qq.run_startup_checks()
    assert isinstance(result, bool)
