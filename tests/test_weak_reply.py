"""弱回复判定测试：空回复、短回复、漏实体、弱话术。"""
from bridge_qq import _is_weak_reply


class TestWeakReply:
    def test_empty_is_weak(self):
        assert _is_weak_reply("", "洛克王国怎么玩")

    def test_whitespace_is_weak(self):
        assert _is_weak_reply("   ", "洛克王国怎么玩")

    def test_short_guide_reply_is_weak(self):
        assert _is_weak_reply("很好玩", "洛克王国怎么玩")

    def test_short_factual_reply_is_weak(self):
        assert _is_weak_reply("梅西", "梅西是谁")

    def test_short_social_reply_is_accepted(self):
        assert not _is_weak_reply("111大哥真送吗", "扣1送地狱火")

    def test_short_chitchat_reply_is_accepted(self):
        assert not _is_weak_reply("小菲在呢", "你怎么不回答了喵")

    def test_guide_reply_without_exact_entity_still_passes(self):
        assert not _is_weak_reply(
            "前往草系道馆完成挑战，再去徽章密室击败守护精灵，成功拿到草系徽章才算解锁，之后继续推进下一关。",
            "洛克王国世界草系徽章怎么弄",
        )

    def test_complete_guide_reply_passes(self):
        assert not _is_weak_reply(
            "洛克王国世界草系徽章要先完成草系道馆挑战，再去徽章密室击败守护精灵，成功拿到草系徽章才算解锁。",
            "洛克王国世界草系徽章怎么弄",
        )

    def test_weak_phrase_is_weak(self):
        assert _is_weak_reply("我不太清楚这个答案", "随便问问")

    def test_substantive_factual_opinion_passes(self):
        assert not _is_weak_reply(
            "评价，塔菲知道！在塔菲看来，评价就是给事物或人物一个公正的判断。",
            "如何评价梅西香港行不出站不尊重球迷",
        )

    def test_honest_not_found_is_not_weak(self):
        assert not _is_weak_reply(
            "没查到具体链接，暂时无法确认直播间号，建议看原消息或官方页面。",
            "你看得见这个哔哩哔哩链接吗",
        )
