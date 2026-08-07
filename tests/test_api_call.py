"""模型 API 调用测试：HTTP 状态、超时、流式解析全部 mock。"""
import requests

import bridge_qq as bridge_module
from bridge_qq import Bridge


class FakeResponse:
    def __init__(self, status_code=200, text="", lines=None, exc=None):
        self.status_code = status_code
        self.text = text
        self.lines = lines or []
        self.exc = exc

    def iter_lines(self, decode_unicode=True):
        if self.exc:
            raise self.exc
        return iter(self.lines)

    def close(self):
        pass


def _bridge():
    return object.__new__(Bridge)


class TestApiCallStatus:
    def test_rate_limit_state(self, monkeypatch):
        def fake_post(*args, **kwargs):
            return FakeResponse(status_code=429, text="rate limited")

        monkeypatch.setattr(requests, "post", fake_post)
        result = _bridge()._api_call({}, "http://example", "key")
        assert result == ("", "rate_limit", [], "", [])

    def test_server_error_state(self, monkeypatch):
        def fake_post(*args, **kwargs):
            return FakeResponse(status_code=500, text="server error")

        monkeypatch.setattr(requests, "post", fake_post)
        result = _bridge()._api_call({}, "http://example", "key")
        assert result == ("", "server_error", [], "", [])

    def test_timeout_state(self, monkeypatch):
        def fake_post(*args, **kwargs):
            raise requests.exceptions.Timeout("timeout")

        monkeypatch.setattr(requests, "post", fake_post)
        result = _bridge()._api_call({}, "http://example", "key")
        assert result == ("", "network", [], "", [])

    def test_generic_exception_state(self, monkeypatch):
        def fake_post(*args, **kwargs):
            raise ValueError("boom")

        monkeypatch.setattr(requests, "post", fake_post)
        result = _bridge()._api_call({}, "http://example", "key")
        assert result == ("", "network", [], "", [])


class TestApiCallStream:
    def test_streaming_content_parsed(self, monkeypatch):
        def fake_post(*args, **kwargs):
            return FakeResponse(lines=[
                'data: {"choices":[{"delta":{"content":"你好"}}]}',
                "data: [DONE]",
            ])

        monkeypatch.setattr(requests, "post", fake_post)
        content, state, tool_calls, finish_reason, web_search = (
            _bridge()._api_call({}, "http://example", "key"))
        assert content == "你好"
        assert state == "ok"
        assert tool_calls == []
        assert finish_reason == ""
        assert web_search == []

    def test_empty_stream_is_network(self, monkeypatch):
        def fake_post(*args, **kwargs):
            return FakeResponse(lines=[])

        monkeypatch.setattr(requests, "post", fake_post)
        result = _bridge()._api_call({}, "http://example", "key")
        assert result == ("", "network", [], "", [])

    def test_stream_read_timeout_is_network(self, monkeypatch):
        def fake_post(*args, **kwargs):
            return FakeResponse(lines=[], exc=requests.exceptions.Timeout("read timeout"))

        monkeypatch.setattr(requests, "post", fake_post)
        result = _bridge()._api_call({}, "http://example", "key")
        assert result == ("", "network", [], "", [])


class TestOneBotHttpProbe:
    def test_skipped_in_test_mode(self):
        assert bridge_module._onebot_http_probe() is True

    def test_returns_false_on_error(self, monkeypatch):
        monkeypatch.delenv("QQ_TEST_MODE", raising=False)

        def boom(*args, **kwargs):
            raise OSError("onebot down")

        monkeypatch.setattr(bridge_module.urllib.request, "urlopen", boom)
        assert bridge_module._onebot_http_probe() is False
