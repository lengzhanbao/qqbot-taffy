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
        assert text.index("测试B站视频") < text.index("com.tencent.structmsg")

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
        result = asyncio.run(bridge._fetch_reply_context(ev))
        assert "测试机器人" in result
        assert "bilibili.com/video/BV1abc" in result

    def test_no_reply_returns_empty(self):
        bridge = _bridge()
        result = asyncio.run(bridge._fetch_reply_context({"message": [
            {"type": "text", "data": {"text": "你好"}},
        ]}))
        assert result == ""
