"""④ 注入的历史必须经相关性选择：独立问题不附无关群聊。"""
import bridge_qq


def _msg(text, speaker="u"):
    return {"role": "user", "content": text, "speaker_id": speaker}


def test_unrelated_history_is_not_injected():
    """一堆无关群聊 + 一个独立问题 → 只保留最小连贯窗口。"""
    items = [_msg(f"群友在聊完全无关的第{i}件事") for i in range(20)]
    picked = bridge_qq._pick_relevant_group_context(items, "今天吃了没")

    assert len(picked) <= 3, f"独立问题不该带上整段群聊: {len(picked)} 条"
    assert picked == items[-3:]


def test_relevant_older_message_is_pulled_in():
    """与问题共享实体词的旧消息应被挑进来。"""
    items = [_msg("群友甲: 上次说玄武口音那事到底怎么回事"),
             _msg("群友乙: 完全无关的闲聊一"),
             _msg("群友乙: 完全无关的闲聊二"),
             _msg("群友甲: 在吗")]
    picked = bridge_qq._pick_relevant_group_context(items, "玄武口音是什么梗")

    joined = "\n".join(m["content"] for m in picked)
    assert "玄武口音" in joined, "相关旧消息没有被挑进来"
    # 顺序必须保持原样
    assert picked == sorted(picked, key=items.index)


def test_explicit_recall_keeps_broad_window():
    """明确回溯时才放宽到整段窗口。"""
    items = [_msg(f"第{i}条历史消息") for i in range(30)]
    broad = bridge_qq._pick_relevant_group_context(
        items, "之前讨论过什么", explicit_recall=True)
    assert len(broad) == 30

    narrow = bridge_qq._pick_relevant_group_context(
        items, "之前讨论过什么", explicit_recall=False)
    assert len(narrow) <= 3


def test_limit_caps_selected_context():
    items = [_msg("玄武口音相关消息") for _ in range(30)]
    picked = bridge_qq._pick_relevant_group_context(
        items, "玄武口音", limit=5)
    assert len(picked) <= 5


def test_followup_without_topic_uses_continuity_only():
    """省略主语的追问（"你觉得呢"）没有可匹配的词，走最小连贯窗口。"""
    items = [_msg(f"无关第{i}条") for i in range(10)]
    picked = bridge_qq._pick_relevant_group_context(items, "你觉得呢")
    assert picked == items[-3:]
