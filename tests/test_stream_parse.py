"""模型流式/JSON 响应解析测试：截断、工具调用、联网结果、坏数据。"""
from bridge_qq import Bridge


def _bridge():
    return object.__new__(Bridge)


class TestStreamParse:
    def test_json_non_stream_returns_content_and_length(self):
        lines = ['{"choices":[{"message":{"content":"半截回复"},"finish_reason":"length"}]}']
        content, state, tool_calls, finish_reason, web_search = (
            _bridge()._parse_stream_response(lines))
        assert content == "半截回复"
        assert state == "ok"
        assert tool_calls == []
        assert finish_reason == "length"
        assert web_search == []

    def test_json_diagnostics_keep_pre_clean_chars_reason_and_usage(self):
        diagnostics = {}
        lines = [
            '{"choices":[{"message":{"content":"  原始正文\\n"},'
            '"finish_reason":"length"}],"usage":{"completion_tokens":17}}'
        ]
        content, *_ = _bridge()._parse_stream_response(lines, diagnostics=diagnostics)
        assert content == "原始正文"
        assert diagnostics == {
            "raw_chars": len("  原始正文\n"),
            "finish_reason": "length",
            "completion_tokens": 17,
        }

    def test_stream_diagnostics_include_usage_only_chunk(self):
        diagnostics = {}
        lines = [
            'data: {"choices":[{"delta":{"content":"正文"}}]}',
            'data: {"choices":[{"delta":{"content":"续写"},"finish_reason":"length"}]}',
            'data: {"choices":[],"usage":{"completion_tokens":23}}',
            "data: [DONE]",
        ]
        content, *_ = _bridge()._parse_stream_response(lines, diagnostics=diagnostics)
        assert content == "正文续写"
        assert diagnostics == {
            "raw_chars": len("正文续写"),
            "finish_reason": "length",
            "completion_tokens": 23,
        }

    def test_json_content_tool_call_text_is_recovered(self):
        lines = [
            '{"choices":[{"message":{"content":"{\\"queries\\":[{\\"query\\":\\"2026世界杯 冠军\\",\\"purpose\\":\\"冠军\\"}]}"},"finish_reason":"stop"}]}'
        ]
        content, state, tool_calls, finish_reason, _ = (
            _bridge()._parse_stream_response(lines))
        assert content == ""
        assert state == "ok"
        assert len(tool_calls) == 1
        assert tool_calls[0]["function"]["name"] == "plan_search"
        assert "2026世界杯 冠军" in tool_calls[0]["function"]["arguments"]
        assert finish_reason == "stop"

    def test_streaming_content_chunks_are_joined(self):
        lines = [
            'data: {"choices":[{"delta":{"content":"你"}}]}',
            'data: {"choices":[{"delta":{"content":"好"}}]}',
            "data: [DONE]",
        ]
        content, state, _, finish_reason, _ = _bridge()._parse_stream_response(lines)
        assert content == "你好"
        assert state == "ok"
        assert finish_reason == ""

    def test_streaming_tool_calls_accumulate(self):
        lines = [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"t1","type":"function","function":{"name":"search_web","arguments":"{\\"query\\":"}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"梅西\\"}"}}]}}]}',
            "data: [DONE]",
        ]
        _, _, tool_calls, _, _ = _bridge()._parse_stream_response(lines)
        assert len(tool_calls) == 1
        fn = tool_calls[0]["function"]
        assert fn["name"] == "search_web"
        assert "梅西" in fn["arguments"]

    def test_streaming_web_search_result(self):
        lines = ['data: {"web_search":[{"title":"结果"}],"choices":[]}']
        _, _, _, _, web_search = _bridge()._parse_stream_response(lines)
        assert web_search == [{"title": "结果"}]

    def test_finish_reason_length_kept_in_stream(self):
        lines = ['data: {"choices":[{"delta":{"content":"内容"},"finish_reason":"length"}]}']
        _, _, _, finish_reason, _ = _bridge()._parse_stream_response(lines)
        assert finish_reason == "length"

    def test_empty_lines_returns_ok_empty(self):
        content, state, tool_calls, finish_reason, web_search = (
            _bridge()._parse_stream_response([]))
        assert content == ""
        assert state == "ok"
        assert tool_calls == []
        assert finish_reason == ""
        assert web_search == []

    def test_invalid_stream_line_does_not_crash(self):
        lines = ["data: not-json", "data: [DONE]"]
        content, state, tool_calls, finish_reason, _ = (
            _bridge()._parse_stream_response(lines))
        assert content == ""
        assert state == "ok"
        assert tool_calls == []
        assert finish_reason == ""

    def test_malformed_json_returns_network(self):
        lines = ['{"choices":[{}]}']
        content, state, _, _, _ = _bridge()._parse_stream_response(lines)
        assert content == ""
        assert state == "network"
