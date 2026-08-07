"""实体完整性测试：回答不能把关键名称切碎或漏掉。"""
from bridge_qq import _reply_missing_entities, _required_entity_terms


class TestRequiredTerms:
    def test_messi_not_broken_into_xi(self):
        assert _required_entity_terms("梅西是谁") == {"梅西"}

    def test_doubao_not_broken_into_danzi(self):
        terms = _required_entity_terms("豆包是什么")
        assert "豆包" in terms
        assert "豆" not in terms
        assert "包" not in terms

    def test_taffy_kept(self):
        assert "塔菲" in _required_entity_terms("关注塔菲谢谢喵")


class TestMissingTerms:
    def test_partial_xi_missing_is_detected(self):
        assert "梅西" in _reply_missing_entities("西是阿根廷球员", "梅西是谁")

    def test_full_messi_passes(self):
        assert _reply_missing_entities("梅西是阿根廷球员", "梅西是谁") == []

    def test_rock_kingdom_full_name_passes(self):
        reply = "洛克王国世界草系徽章要先完成草系道馆挑战，再去徽章密室击败守护精灵，成功拿到草系徽章才算解锁。"
        assert _reply_missing_entities(reply, "洛克王国世界草系徽章怎么弄") == []
