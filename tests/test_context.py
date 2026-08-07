"""追问判定与上下文切断相关测试。"""
from bridge_qq import _is_followup_query


class TestFollowupDetection:
    def test_connective_is_followup(self):
        assert _is_followup_query("然后呢")
        assert _is_followup_query("那梅西呢")
        assert _is_followup_query("还有呢")
        assert _is_followup_query("为什么")

    def test_short_new_topic_is_not_followup(self):
        assert not _is_followup_query("今天好累")
        assert not _is_followup_query("C罗今年多大了")
        assert not _is_followup_query("帮我看一下链接")

    def test_empty_is_not_followup(self):
        assert not _is_followup_query("")
