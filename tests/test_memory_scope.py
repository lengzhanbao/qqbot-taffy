import bridge_qq
import qqbot_core.config as config


def test_scoped_private_memory_does_not_leak_group_memory():
    memory = """## 私聊 测试用户
- 私聊专属事实
## 群 111
- 私聊专属事实（群内也出现过）
## 常识补充
- 私聊专属事实（共享知识）
"""
    result, score = bridge_qq.retrieve_knowledge_scored(
        "私聊专属事实",
        memory,
        scope="private_900000001",
        scope_alias="测试用户",
    )

    assert "私聊专属事实" in result
    assert "群 111" not in result
    assert "常识补充" in result


def test_memory_blocks_support_crlf_line_endings():
    memory = "## 私聊 900000001\r\n- 私聊专属事实\r\n## 常识补充\r\n- 加油 林俊杰 热狗\r\n"
    result, score = bridge_qq.retrieve_knowledge_scored(
        "加油的歌词是什么",
        memory,
        scope="private_900000001",
    )
    assert score >= 1
    assert "林俊杰" in result


def test_scoped_group_memory_does_not_leak_private_memory():
    memory = """## 群 111
- 群专属事实
## 私聊 测试用户
- 群专属事实（私聊也出现过）
## 常识补充
- 群专属事实（共享知识）
"""
    result, score = bridge_qq.retrieve_knowledge_scored(
        "群专属事实",
        memory,
        scope="111",
    )

    assert "群 111" in result
    assert "私聊" not in result
    assert "常识补充" in result


def test_question_or_quote_is_not_written_as_memory():
    assert bridge_qq._is_question_or_quote("[引用]问的是今天拜仁香港喵")
    assert bridge_qq._is_question_or_quote("今天有什么比赛吗")
    assert not bridge_qq._is_question_or_quote("拜仁今天有活动")


def test_group_style_is_memory_only_not_long_term_prompt():
    assert not bridge_qq._should_inject_group_style("900000002", "今晚有什么比赛吗")


def test_memory_file_cache_reads_and_refreshes(monkeypatch, tmp_path):
    memory_file = tmp_path / "bot_memory.md"
    memory_file.write_text("# Bot Memory\n\n- 塔菲\n", encoding="utf-8")
    monkeypatch.setattr(config, "MEMORY_FILE", str(memory_file))
    config._MEMORY_CACHE.update({"content": "", "mtime": 0.0})

    assert config._read_memory_file_cached() == "# Bot Memory\n\n- 塔菲\n"
    memory_file.write_text("# Bot Memory\n\n- 塔菲更新\n", encoding="utf-8")
    assert "塔菲更新" in config._read_memory_file_cached()
