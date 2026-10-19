"""回归：流式响应必须按 UTF-8 逐行解码，不能用 decode_unicode=True。

实测（2026-10-17，同一请求各 3 次）：
  iter_lines(decode_unicode=True) → 1 个内容块、输出 6~7 字（丢内容）
  iter_lines() + 显式 utf-8        → 5~6 个内容块、输出 44 字（逐字正确）
"""
import json

import bridge_qq


def _sse(piece, index=0):
    payload = {
        "id": "x", "object": "chat.completion.chunk", "created": 1,
        "model": "m",
        "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
    }
    return ("data: " + json.dumps(payload, ensure_ascii=False)).encode("utf-8")


def test_stream_line_decodes_utf8_bytes():
    target = "周六晚上八点约好打王者荣耀，实际是否进行待确认。"
    line = _sse(target)
    assert target in bridge_qq._decode_stream_line(line)


def test_all_chunks_survive_utf8_decoding():
    """每一块都要能解出来，不能因为解码方式丢掉中间块。"""
    pieces = ["周六晚上八点", "约好打王者荣耀，", "实际是否进行待确认，",
              "另外群里那位说玄武口音", "的先别急着下结论。"]
    lines = [bridge_qq._decode_stream_line(_sse(p, i)) for i, p in enumerate(pieces)]
    lines.append("data: [DONE]")

    bridge = object.__new__(bridge_qq.Bridge)
    parsed = bridge._parse_stream_response(lines, diagnostics={})
    text = parsed[0] if isinstance(parsed, tuple) else parsed

    assert text == "".join(pieces)


def test_bytes_and_str_inputs_both_supported():
    assert bridge_qq._decode_stream_line(b"data: {\"a\":1}") == 'data: {"a":1}'
    assert bridge_qq._decode_stream_line('data: {"a":1}') == 'data: {"a":1}'
    assert bridge_qq._decode_stream_line(b"") == ""
