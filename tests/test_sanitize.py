"""_sanitize_reply 的清洗规则测试。"""
from bridge_qq import _sanitize_reply


class TestMarkdown:
    def test_remove_bold(self):
        assert "**" not in _sanitize_reply("**你好**")

    def test_remove_unmatched_bold(self):
        assert "**" not in _sanitize_reply("就是指**南京玄武湖附近")

    def test_remove_italic(self):
        assert "*" not in _sanitize_reply("*斜体*")

    def test_remove_backtick(self):
        assert "`" not in _sanitize_reply("`代码`")

    def test_remove_unmatched_backtick(self):
        assert "`" not in _sanitize_reply("玄武口音就是`南京附近")

    def test_remove_underscore(self):
        assert "__" not in _sanitize_reply("__重点__")

    def test_remove_header(self):
        assert "#" not in _sanitize_reply("### 标题")


class TestEmoji:
    def test_dedup_same_emoji(self):
        assert _sanitize_reply("好🔥🔥🔥") == "好🔥"

    def test_keep_different_emoji(self):
        result = _sanitize_reply("好🔥🐢")
        assert "🔥" in result and "🐢" in result

    def test_dedup_mixed(self):
        result = _sanitize_reply("🔥🔥🐢🐢")
        assert result.count("🔥") == 1
        assert result.count("🐢") == 1

    def test_remove_turtle_fire_combo(self):
        assert "🐢" not in _sanitize_reply("🐢🔥稳住")


class TestPhrase:
    def test_erci_to_erciyuan(self):
        assert "二次元" in _sanitize_reply("这很二次")

    def test_second_not_changed(self):
        result = _sanitize_reply("这是第二次")
        assert "第二次" in result
        assert "第二次元" not in result

    def test_uncertain_kept(self):
        result = _sanitize_reply("我不确定答案")
        assert "不确定" in result

    def test_not_found_kept(self):
        assert "没查到" in _sanitize_reply("没查到相关信息")


class TestEdge:
    def test_empty_string(self):
        assert _sanitize_reply("") == ""

    def test_only_whitespace(self):
        assert _sanitize_reply("   ") == ""

    def test_idempotent(self):
        text = "**你好** 🔥🔥"
        once = _sanitize_reply(text)
        twice = _sanitize_reply(once)
        assert once == twice

    def test_no_change_for_clean_text(self):
        assert _sanitize_reply("今天天气不错") == "今天天气不错"

    def test_chinese_punctuation_kept(self):
        result = _sanitize_reply("你好，世界！")
        assert "，" in result
        assert "！" in result


class TestSanitizeMathContext:
    def test_secondary_function_not_changed(self):
        assert _sanitize_reply("这是二次函数") == "这是二次函数"

    def test_secondary_power_not_changed(self):
        assert _sanitize_reply("这是二次方") == "这是二次方"

    def test_erciyuan_not_doubled(self):
        assert _sanitize_reply("这是二次元浓度") == "这是二次元浓度"


class TestSanitizeContextualReplace:
    def test_huitou_rate_not_replaced(self):
        result = _sanitize_reply("这衣服回头率很高")
        assert "回头率" in result
        assert "我觉得率" not in result

    def test_huitou_stalling_kept(self):
        result = _sanitize_reply("回头再说吧")
        assert "回头再说吧" in result

    def test_not_yet_kept(self):
        result = _sanitize_reply("这个比赛还没开始")
        assert "还没开始" in result

    def test_markdown_link_keeps_text(self):
        result = _sanitize_reply("[塔菲](https://example.com)")
        assert "塔菲" in result
        assert "https://example.com" not in result
