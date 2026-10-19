"""群图片识图（2026-10-17）。

群里发的图原先只传占位符 [图片]，模型看不到内容。
实测 deepseek-v4.1-flash 支持多模态，所以把图取出来送进请求。
"""
import bridge_qq


def test_extract_image_urls_picks_only_safe_qq_urls():
    """2026-10-18：只接受 https + QQ 图片域名；陌生域名/裸 IP/非 https 一律拒绝。"""
    msg = [
        {"type": "text", "data": {"text": "看这个"}},
        {"type": "image", "data": {"url": "https://multimedia.nt.qq.com.cn/a.png"}},
        {"type": "image", "data": {"url": ""}},
        {"type": "face", "data": {"id": 1}},
        {"type": "image", "data": {"url": "https://evil.example.com/c.jpg"}},
        {"type": "image", "data": {"url": "http://multimedia.nt.qq.com.cn/d.png"}},
        {"type": "image", "data": {"url": "https://gchat.qpic.cn/e.jpg"}},
    ]
    assert bridge_qq._extract_image_urls(msg) == [
        "https://multimedia.nt.qq.com.cn/a.png", "https://gchat.qpic.cn/e.jpg"]


def test_safe_image_url_rejects_unsafe():
    assert bridge_qq._safe_image_url("https://multimedia.nt.qq.com.cn/x")
    assert bridge_qq._safe_image_url("https://gchat.qpic.cn/x")
    for bad in ("http://multimedia.nt.qq.com.cn/x", "https://evil.com/x",
                "https://127.0.0.1/x", "https://192.168.1.1/x", "https://localhost/x",
                "file:///etc/passwd", "", None):
        assert not bridge_qq._safe_image_url(bad), bad


def test_extract_image_urls_respects_limit():
    msg = [{"type": "image", "data": {"url": f"https://multimedia.nt.qq.com.cn/{i}.png"}} for i in range(9)]
    assert len(bridge_qq._extract_image_urls(msg, limit=3)) == 3


def test_attach_image_blocks_converts_last_user_message():
    blocks = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}}]
    msgs = [{"role": "system", "content": "s"},
            {"role": "user", "content": "前面的话"},
            {"role": "assistant", "content": "回"},
            {"role": "user", "content": "这张图里有什么"}]
    bridge_qq._attach_image_blocks(msgs, blocks)
    content = msgs[-1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "这张图里有什么"}
    assert content[1]["type"] == "image_url"
    # 只动最后一条 user，前面的不受影响
    assert msgs[1]["content"] == "前面的话"


def test_attach_image_blocks_is_noop_without_blocks():
    msgs = [{"role": "user", "content": "普通消息"}]
    bridge_qq._attach_image_blocks(msgs, [])
    assert msgs[0]["content"] == "普通消息"


def test_fetch_image_blocks_skips_bad_url_without_raising():
    """取图失败必须静默跳过，不能影响正常回复。"""
    assert bridge_qq._fetch_image_blocks(["not-a-url"]) == []
    assert bridge_qq._fetch_image_blocks([]) == []


def test_image_only_message_is_not_treated_as_empty(monkeypatch):
    """2026-10-18：群里发图（没 @ 它）原先被"空消息"硬挡，什么都不会发生。
    现在图片本身就是内容，应当进入正常判断并把图送进请求。"""
    import asyncio
    import time

    import pytest

    import test_proactive
    from qqbot_core.observer import GroupConversationObserver

    url = "https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=abc"
    with pytest.MonkeyPatch.context() as mp:
        bridge = test_proactive._ready(mp)
        mp.setattr(bridge_qq, "PROACTIVE_DEBOUNCE_SECONDS", 0)
        # 测试不联网：把取图换成固定块，只验证"图有没有进请求"
        mp.setattr(bridge_qq, "_fetch_image_blocks",
                   lambda urls, max_side=None: [{"type": "image_url",
                                                 "image_url": {"url": "data:image/jpeg;base64,AA"}}])
        observer = GroupConversationObserver(bridge.group_policy)
        now = time.time()
        observer.ingest({"group_id": "42", "user_id": "u1", "message_id": "i1",
                         "time": now, "sender": {"card": "甲"},
                         "message": [{"type": "text", "data": {"text": "在忙"}}]})
        observer.ingest({"group_id": "42", "user_id": "u2", "message_id": "i2",
                         "time": now + 1, "sender": {"card": "乙"},
                         "message": [{"type": "image", "data": {"url": url}}]})
        bridge.observer = observer
        captured = {}

        async def fake(system, messages, **kwargs):
            captured["messages"] = messages
            return "__QUIET__", bridge_qq.DEEPSEEK_MODEL_REF

        bridge._complete = fake
        event = {"message": [{"type": "image", "data": {"url": url}}]}
        asyncio.run(bridge._maybe_proactive_group_reply(event, "42", ""))

    messages = captured.get("messages") or []
    assert messages, "纯图片消息必须进入模型判断，不能被当空消息挡掉"
    blocks = messages[-1]["content"]
    assert isinstance(blocks, list)
    assert any(b.get("type") == "image_url" for b in blocks)
