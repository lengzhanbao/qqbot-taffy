"""点歌模块（qqbot_core.music）的回归测试。

2026-10-20 实测背景：
  NapCat 4.18.14 不接受 music/type=qq（报"消息体无法解析"，第三方签名服务失效），
  但 music/type=custom 可以正常发送，所以默认走 custom 卡片。
"""
import json

import pytest

from qqbot_core import music


def test_cover_url_builds_expected_pattern():
    assert music.cover_url("000MkMni19ClKG") == (
        "https://y.gtimg.cn/music/photo_new/T002R500x500M000000MkMni19ClKG.jpg")
    assert music.cover_url("") == ""
    assert music.cover_url(None) == ""


def test_build_custom_card_uses_custom_type():
    """必须是 custom：type=qq 在 NapCat 4.18 会被拒绝。"""
    card = music.build_custom_card({
        "songmid": "0039MnYb0qxYhV",
        "songname": "晴天",
        "singer": "周杰伦",
        "albummid": "000MkMni19ClKG",
        "cover": "https://example.com/cover.jpg",
    })
    assert card["type"] == "music"
    data = card["data"]
    assert data["type"] == "custom"
    assert data["title"] == "晴天"
    assert data["content"] == "周杰伦"
    assert "0039MnYb0qxYhV" in data["url"]
    assert "0039MnYb0qxYhV" in data["audio"]
    assert data["image"] == "https://example.com/cover.jpg"


def test_build_custom_card_derives_cover_from_albummid():
    card = music.build_custom_card({"songmid": "abc", "songname": "X",
                                    "singer": "Y", "albummid": "MID1"})
    assert card["data"]["image"].endswith("M000MID1.jpg")


def test_build_custom_card_requires_songmid():
    with pytest.raises(music.MusicError):
        music.build_custom_card({"songname": "晴天"})
    with pytest.raises(music.MusicError):
        music.build_custom_card({})
    with pytest.raises(music.MusicError):
        music.build_music_segment("")


def test_build_custom_card_handles_missing_fields():
    card = music.build_custom_card({"songmid": "abc"})
    assert card["data"]["title"] == "未知歌曲"
    assert card["data"]["content"] == "未知歌手"


def test_format_duration():
    assert music.format_duration(269) == "4:29"
    assert music.format_duration(61) == "1:01"
    assert music.format_duration(0) == ""
    assert music.format_duration(None) == ""


def test_describe_includes_song_singer_duration():
    text = music.describe({"songname": "晴天", "singer": "周杰伦", "interval": 269})
    assert "晴天" in text and "周杰伦" in text and "4:29" in text
    numbered = music.describe({"songname": "晴天", "singer": "周杰伦"}, index=1)
    assert numbered.startswith("1. 晴天")


def test_search_rejects_empty_keyword():
    with pytest.raises(music.MusicError):
        music.search_qq_music("")
    with pytest.raises(music.MusicError):
        music.search_qq_music("   ")


def test_music_command_regex_matches_expected_forms():
    """只有 /点歌 才算命令：不带斜杠的"点歌"不能触发，避免误 catch 普通聊天。"""
    import bridge_qq

    rx = bridge_qq._MUSIC_CMD_RE
    assert rx.match("/点歌 晴天").group(1).strip() == "晴天"
    assert rx.match("／点歌 晴天")          # 全角斜杠
    assert rx.match("/点歌").group(1).strip() == ""
    assert not rx.match("点歌 晴天")
    assert not rx.match("今天想点歌")
    assert not rx.match("/点播 晴天")
