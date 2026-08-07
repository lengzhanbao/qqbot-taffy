"""对话模式分类测试：comfort > creative > task > chitchat。"""
from bridge_qq import _classify_dialog_mode


class TestDialogModePriority:
    def test_comfort_beats_creative(self):
        assert _classify_dialog_mode("我很难过，帮我写一个故事") == "comfort"

    def test_comfort_beats_task(self):
        assert _classify_dialog_mode("我不开心，帮我整理计划") == "comfort"

    def test_creative_beats_task(self):
        assert _classify_dialog_mode("帮我写一首歌词") == "creative"

    def test_task_wins_over_chitchat(self):
        assert _classify_dialog_mode("帮我推荐几款手机") == "task"

    def test_plain_chitchat(self):
        assert _classify_dialog_mode("今天天气不错") == "chitchat"


class TestDialogModeMarkers:
    def test_comfort_marker(self):
        assert _classify_dialog_mode("我好焦虑，压力好大") == "comfort"

    def test_comfort_with_punctuation(self):
        assert _classify_dialog_mode("我很难过，怎么办") == "comfort"

    def test_creative_marker(self):
        assert _classify_dialog_mode("给一个文案创意") == "creative"

    def test_task_marker(self):
        assert _classify_dialog_mode("总结一下这篇文章") == "task"

    def test_task_how_to(self):
        assert _classify_dialog_mode("洛克王国草系徽章怎么弄") == "task"

    def test_creative_marker_beats_help_marker(self):
        assert _classify_dialog_mode("帮我设计一个标题") == "creative"

    def test_creative_sad_lyrics_is_not_comfort(self):
        assert _classify_dialog_mode("帮我写一首难过的歌词") == "creative"

    def test_creative_stress_article_is_not_comfort(self):
        assert _classify_dialog_mode("帮我写一篇压力管理的文章") == "creative"


class TestDialogModeEdge:
    def test_empty_text(self):
        assert _classify_dialog_mode("") == "chitchat"

    def test_none_text(self):
        assert _classify_dialog_mode(None) == "chitchat"

    def test_only_whitespace(self):
        assert _classify_dialog_mode("   ") == "chitchat"
