"""搜索拆词测试：游戏/攻略整句保护、多实体拆分子查询、标点分隔。"""
from bridge_qq import _split_sub_queries


class TestGameGuideProtection:
    def test_rock_kingdom_not_split(self):
        query = "洛克王国世界草系徽章怎么弄"
        assert _split_sub_queries(query) == [query]

    def test_rock_kingdom_with_entities_not_split(self):
        query = "洛克王国世界草系徽章怎么弄"
        assert _split_sub_queries(query, entities=["洛克王国"]) == [query]

    def test_game_boss_query_not_split(self):
        query = "王者荣耀BOSS怎么打"
        assert _split_sub_queries(query) == [query]


class TestMultiEntitySplit:
    def test_two_entities_get_own_queries(self):
        result = _split_sub_queries("梅西和C罗谁更强", entities=["梅西", "C罗"])
        assert len(result) == 2
        assert any("梅西" in q and "C罗" not in q for q in result)
        assert any("C罗" in q and "梅西" not in q for q in result)

    def test_doubao_and_kimi_not_sliced(self):
        result = _split_sub_queries("豆包和Kimi有什么区别", entities=["豆包", "Kimi"])
        assert any("豆包" in q for q in result)
        assert any("Kimi" in q for q in result)

    def test_entity_attributes_split(self):
        result = _split_sub_queries("C罗和梅西年龄", entities=["C罗", "梅西"])
        assert "C罗" in result[0] and "年龄" in result[0]
        assert "梅西" in result[1] and "年龄" in result[1]

    def test_absent_entity_is_not_invented(self):
        result = _split_sub_queries("梅西年龄", entities=["梅西", "C罗"])
        assert "C罗" not in " ".join(result)
        assert any("梅西" in q for q in result)


class TestProtectedTerms:
    def test_doubao_and_kimi_keep_whole_query_without_entities(self):
        query = "豆包和Kimi有什么区别"
        assert _split_sub_queries(query) == [query]


class TestConjunctionSplit:
    def test_split_huanyou(self):
        result = _split_sub_queries("今天天气怎么样还有明天呢")
        assert "今天天气怎么样" in result
        assert "明天呢" in result

    def test_split_tongshi(self):
        result = _split_sub_queries("A事件同时B事件")
        assert len(result) >= 2

    def test_split_lingwai(self):
        result = _split_sub_queries("查一下价格另外看看评价")
        assert len(result) >= 2

    def test_filters_short_parts(self):
        assert _split_sub_queries("你好 还有") == ["你好"]

    def test_max_three_subqueries(self):
        assert len(_split_sub_queries("a还有b还有c还有d")) <= 3

    def test_no_split_for_plain_text(self):
        query = "今天天气不错"
        assert _split_sub_queries(query) == [query]
