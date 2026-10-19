"""名片/分享卡片解析测试：B站、新闻、分享、音乐、空数据。"""
import json
import asyncio

from bridge_qq import Bridge


def _bridge():
    return object.__new__(Bridge)


class TestParseCardPayload:
    def test_dict_data_json_string(self):
        payload = Bridge._parse_card_payload({"data": json.dumps({
            "title": "测试视频",
            "url": "https://www.bilibili.com/video/BV1abc",
        })})
        assert payload["title"] == "测试视频"
        assert payload["url"].startswith("https://www.bilibili.com")

    def test_data_field_json_string(self):
        payload = Bridge._parse_card_payload(json.dumps({
            "title": "测试分享",
            "url": "https://example.com/a",
        }))
        assert payload["title"] == "测试分享"

    def test_inner_data_keeps_top_level(self):
        payload = Bridge._parse_card_payload({
            "data": {"title": "内部标题"},
            "app": "com.tencent.structmsg",
        })
        assert payload["title"] == "内部标题"
        assert payload["app"] == "com.tencent.structmsg"

    def test_invalid_json_falls_back_to_raw(self):
        payload = Bridge._parse_card_payload("not-json-card")
        assert payload.get("raw_data") == "not-json-card"

    def test_empty_payload(self):
        assert Bridge._parse_card_payload({}) == {}


class TestCardText:
    def test_bilibili_meta_news(self):
        payload = {
            "app": "com.tencent.structmsg",
            "desc": "分享视频",
            "meta": {
                "news": {
                    "title": "测试B站视频",
                    "url": "https://www.bilibili.com/video/BV1abc",
                }
            },
        }
        text = Bridge._card_text(payload)
        assert "测试B站视频" in text
        assert "bilibili.com/video/BV1abc" in text
        # 2026-10-19：com.tencent.structmsg 是协议标识符（噪音），不再进提示词。
        # 改为断言"标题排在链接前面"——这才是模型可读顺序。
        assert "com.tencent.structmsg" not in text
        assert text.index("测试B站视频") < text.index("bilibili.com/video/BV1abc")

    def test_bilibili_detail_qqdocurl(self):
        payload = {"meta": {"detail_1": {
            "title": "另一个B站视频",
            "qqdocurl": "https://b23.tv/xyz",
        }}}
        text = Bridge._card_text(payload)
        assert "另一个B站视频" in text
        assert "b23.tv/xyz" in text

    def test_news_card(self):
        payload = {"meta": {"news": {
            "title": "新闻标题",
            "desc": "新闻摘要",
            "url": "https://example.com/news",
        }}}
        text = Bridge._card_text(payload)
        assert "新闻标题" in text
        assert "新闻摘要" in text
        assert "example.com/news" in text

    def test_music_card(self):
        payload = {"meta": {"music": {
            "title": "歌曲名",
            "musicUrl": "https://music.example.com/song",
        }}}
        text = Bridge._card_text(payload)
        assert "歌曲名" in text
        assert "music.example.com/song" in text

    def test_share_card(self):
        payload = {"title": "分享标题", "url": "https://example.com/share"}
        text = Bridge._card_text(payload)
        assert "分享标题" in text
        assert "example.com/share" in text

    def test_pages_list_card(self):
        payload = {"meta": {"pages": [
            {"title": "列表页标题", "url": "https://example.com/list"},
        ]}}
        text = Bridge._card_text(payload)
        assert "列表页标题" in text
        assert "example.com/list" in text

    def test_empty_card(self):
        assert Bridge._card_text({}) == ""

    def test_duplicate_values_dedup(self):
        payload = {"title": "同一个", "desc": "同一个"}
        assert Bridge._card_text(payload).count("同一个") == 1


class TestExtractTextCard:
    def test_json_card_enters_context(self):
        bridge = _bridge()
        msg = [{
            "type": "json",
            "data": json.dumps({
                "title": "B站卡片标题",
                "url": "https://www.bilibili.com/video/BV1abc",
            }),
        }]
        text, at_bot = bridge.extract_text(msg, "900000000")
        assert "B站卡片标题" in text
        assert "bilibili.com/video/BV1abc" in text
        assert at_bot is False

    def test_share_card_enters_context(self):
        bridge = _bridge()
        msg = [{
            "type": "share",
            "data": {"title": "分享标题", "url": "https://example.com/share"},
        }]
        text, _ = bridge.extract_text(msg, "900000000")
        assert "分享标题" in text
        assert "example.com/share" in text


class TestFetchReplyContext:
    def test_reply_card_context_is_fetched(self):
        bridge = _bridge()
        bridge.self_id = "900000000"

        async def fake_call_api(action, params, retries=5):
            assert action == "get_msg"
            assert params == {"message_id": 123}
            assert retries == 1
            return {
                "status": "ok",
                "retcode": 0,
                "data": {
                    "sender": {"nickname": "测试机器人"},
                    "message": [{
                        "type": "json",
                        "data": {
                            "title": "B站视频",
                            "url": "https://www.bilibili.com/video/BV1abc",
                        },
                    }],
                },
            }

        bridge.call_api = fake_call_api
        ev = {"message": [{"type": "reply", "data": {"id": "123"}}]}
        # 2026-10-18：改为返回 (文本, 图片地址列表)——被引用的图也要能看到
        result, images = asyncio.run(bridge._fetch_reply_context(ev))
        assert "测试机器人" in result
        assert "bilibili.com/video/BV1abc" in result
        assert images == []

    def test_no_reply_returns_empty(self):
        bridge = _bridge()
        result = asyncio.run(bridge._fetch_reply_context({"message": [
            {"type": "text", "data": {"text": "你好"}},
        ]}))
        assert result == ("", [])


# ---------------- 卡片/分享文本清洗（2026-10-19）----------------

_QQ_MINIAPP_CARD = {
    "ver": "1.0.0.19",
    "prompt": "[QQ小程序]示例视频标题",
    "app": "com.tencent.miniapp_01",
    "meta": {
        "detail_1": {
            "appid": "1109937557",
            "appType": 0,
            "title": "哔哩哔哩",
            "desc": "示例视频标题",
            "icon": "http://miniapp.gtimg.cn/public/appicon/abc_200.jpg",
            "preview": "https://qq.ugcimg.cn/v1/9f8e7d6c5b4a",
            "url": "m.q.qq.com/a/s/00000000000000000000000000000000",
            "scene": 1036,
            "host": {"uin": 3000000001, "nick": "某人"},
            "shareTemplateId": "8C8E89B49BE609866298ADDFF2DBABA4",
            "qqdocurl": "https://b23.tv/yiGwKcm?share_medium=android&share_source=qq"
                         "&bbid=XXE8207EA19C039E987A3C1770728FCB973BF&ts=1792331022574",
        }
    },
}


def test_card_text_keeps_title_and_source():
    import bridge_qq

    text = bridge_qq.Bridge._card_text(_QQ_MINIAPP_CARD)
    assert "示例视频标题" in text, text
    assert "哔哩哔哩" in text, text
    # 标题不应重复出现两次（desc 与 [QQ小程序]prompt 是同一件事）
    assert text.count("示例视频标题") == 1, text


def test_card_text_drops_noise():
    import bridge_qq

    text = bridge_qq.Bridge._card_text(_QQ_MINIAPP_CARD)
    assert "1109937557" not in text          # appid
    assert "appicon" not in text             # 图标
    assert "ugcimg" not in text              # 预览追踪链接
    assert "m.q.qq.com/a/s/" not in text     # QQ 内部短链
    assert "share_medium" not in text        # 追踪参数
    assert "bbid" not in text


def test_card_text_keeps_share_link_without_tracking():
    import bridge_qq

    text = bridge_qq.Bridge._card_text(_QQ_MINIAPP_CARD)
    assert "https://b23.tv/yiGwKcm" in text, text


def test_share_segment_is_readable_by_extract_text():
    """端到端：json 卡片消息 → extract_text 出来是人话，不是协议名。"""
    import json

    import bridge_qq

    bridge = object.__new__(bridge_qq.Bridge)
    bridge.self_id = "900000000"
    segments = [{"type": "json", "data": {"data": json.dumps(_QQ_MINIAPP_CARD,
                                                             ensure_ascii=False)}}]
    text, at_bot = bridge.extract_text(segments, bridge.self_id)
    assert not at_bot
    assert "示例视频标题" in text
    assert "json分享" not in text
    assert "分享" in text


def test_card_text_handles_missing_and_broken_payloads():
    import bridge_qq

    assert bridge_qq.Bridge._card_text({}) == ""
    assert bridge_qq.Bridge._card_text({"raw_data": "http://x/appicon/a.png"}) == ""
