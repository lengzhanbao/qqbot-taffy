# -*- coding: utf-8 -*-
"""点歌：QQ 音乐搜索 + 音乐卡片消息段。

与 DeepSeek 对话完全解耦：本模块只负责"搜歌"和"拼消息段"，不认识人格、记忆或路由。

三个状态要分清（只有最后一个算真正打通）：
  1. 搜索成功      —— QQ 音乐接口返回了 songmid
  2. 卡片被接受    —— NapCat 的 send_group_msg 返回 status=ok
  3. 群里真的发出  —— message_id 非空（用户能看到卡片）

注意：NapCat 的 music 段内部可能调用第三方卡片签名服务，
因此"搜索成功"不等于"能发出去"，必须看第 2/3 步。

2026-10-20 实测（NapCat 4.18.14）：
  music/type=qq      → failed "消息体无法解析"（原生 QQ 音乐卡不可用）
  music/type=custom  → ok + message_id（可用，走 build_custom_card）
"""
import json
import re
import urllib.parse
import urllib.request

SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 搜索时长上限（秒）
TIMEOUT = 8


class MusicError(Exception):
    """点歌过程中可预期的失败（网络/接口/无结果）。"""


def _clean(text, limit=80):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:limit]


def search_qq_music(keyword, limit=5):
    """按关键词搜 QQ 音乐。

    返回 [{"songmid","songname","singer","album","interval"}, ...]
    搜不到返回空列表；网络/解析失败抛 MusicError。
    """
    keyword = _clean(keyword, 60)
    if not keyword:
        raise MusicError("歌名是空的")
    query = urllib.parse.urlencode({
        "w": keyword,
        "format": "json",
        "n": max(1, min(int(limit or 5), 10)),
        "p": 1,
        "cr": 1,
        "new_json": 1,
    })
    request = urllib.request.Request(
        f"{SEARCH_URL}?{query}",
        headers={"User-Agent": _UA, "Referer": "https://y.qq.com/"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read()
    except Exception as exc:  # noqa: BLE001
        raise MusicError(f"搜索请求失败（{type(exc).__name__}）") from exc

    text = raw.decode("utf-8", "replace")
    # 接口可能返回 jsonp 包裹，取最外层对象
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise MusicError("搜索返回不是 JSON")
    try:
        payload = json.loads(text[start:end + 1])
    except Exception as exc:  # noqa: BLE001
        raise MusicError(f"搜索返回解析失败（{type(exc).__name__}）") from exc

    songs = (((payload.get("data") or {}).get("song") or {}).get("list")) or []
    results = []
    for song in songs:
        songmid = str(song.get("songmid") or song.get("mid") or "").strip()
        if not songmid:
            continue
        singers = song.get("singer") or []
        if isinstance(singers, list):
            singer = "、".join(_clean(s.get("name"), 24) for s in singers if s.get("name"))
        else:
            singer = _clean(singers, 24)
        album = song.get("album") or {}
        albummid = str(album.get("mid") or song.get("albummid") or "").strip()
        results.append({
            "songmid": songmid,
            "songname": _clean(song.get("songname") or song.get("name")
                               or song.get("title"), 60),
            "singer": singer or "未知歌手",
            "album": _clean(album.get("name") or song.get("albumname"), 40),
            "albummid": albummid,
            "cover": cover_url(albummid),
            "interval": song.get("interval") or 0,
            "songid": song.get("id"),
        })
    return results


def cover_url(albummid, size=500):
    """专辑封面地址（实测可用，image/jpeg）。"""
    albummid = str(albummid or "").strip()
    if not albummid:
        return ""
    return f"https://y.gtimg.cn/music/photo_new/T002R{size}x{size}M000{albummid}.jpg"


def build_custom_card(result):
    """拼"自定义音乐卡片"段。

    2026-10-20 实测：NapCat 4.18.14 **不接受** `music/type=qq`
    （返回"消息体无法解析, 请检查是否发送了不支持的消息类型"，
    这正是第三方卡片签名服务失效的表现）。
    而 `music/type=custom` 可以正常发送（返回 ok + message_id），
    所以默认走 custom：卡片上显示封面/歌名/歌手，点击打开 QQ 音乐页面。
    """
    songmid = str((result or {}).get("songmid") or "").strip()
    if not songmid:
        raise MusicError("songmid 为空")
    songname = _clean(result.get("songname"), 60) or "未知歌曲"
    singer = _clean(result.get("singer"), 40) or "未知歌手"
    return {
        "type": "music",
        "data": {
            "type": "custom",
            # 点开卡片打开的页面（QQ 音乐歌曲详情）
            "url": f"https://y.qq.com/n/ryqq/songDetail/{songmid}",
            # 让 QQ 尝试直接播放
            "audio": f"https://i.y.qq.com/v8/playsong.html?songmid={songmid}",
            "title": songname,
            "content": singer,
            "image": result.get("cover") or cover_url(result.get("albummid")),
        },
    }


def build_music_segment(songmid):
    """拼 OneBot 的 QQ 音乐卡片段。"""
    songmid = str(songmid or "").strip()
    if not songmid:
        raise MusicError("songmid 为空")
    return {"type": "music", "data": {"type": "qq", "id": songmid}}


def format_duration(seconds):
    """秒数 → mm:ss。"""
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    return f"{total // 60}:{total % 60:02d}"


def describe(result, index=None):
    """一条搜索结果 → 可读文本。"""
    prefix = f"{index}. " if index else ""
    parts = [f"{prefix}{result.get('songname') or '?'}"]
    if result.get("singer"):
        parts.append(f"- {result['singer']}")
    duration = format_duration(result.get("interval"))
    if duration:
        parts.append(f"[{duration}]")
    return " ".join(parts)
