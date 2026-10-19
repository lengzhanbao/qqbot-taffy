"""Low-cost message routing for intent, evidence, and conversation context."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class MessageRoute:
    intent: str
    evidence: str
    context: str
    should_search: bool
    local_query: str = ""
    social_reaction: bool = False


_NO_SEARCH_RE = re.compile(
    r"不要联网|别联网|不用联网|不需要联网|不要搜索|不用搜索|別搜|别搜"
)
_EXPLICIT_SEARCH_RE = re.compile(
    r"(?:帮我|请|麻烦)?(?:搜一下|搜一搜|搜索一下|查一下|联网搜|上网查|找一下)"
    r"|(?:帮我|请|麻烦).{0,6}(?:搜|搜索|查|找)"
)
_LOCAL_MODEL_RE = re.compile(
    r"(?:什么|哪个|当前|现在).{0,8}模型|模型.{0,8}(?:是什么|叫什么|名称|型号)"
)
_LOCAL_STATUS_RE = re.compile(
    r"(?:机器人|qqbot|桥接|NapCat|服务).{0,10}(?:在线|状态|运行|健康|连接)"
    r"|(?:当前|现在).{0,8}(?:在线吗|运行状态|连接状态)", re.IGNORECASE
)
_BOT_SELF_ASSESSMENT_RE = re.compile(
    r"(?:你|塔菲|小菲|机器人|qqbot|bot).{0,14}"
    r"(?:聪明|笨|脑子|智商|开心|高兴|心情|情绪|能力|表现|水平|靠谱|厉害|进步|变好)"
    r"|(?:聪明|笨|脑子|智商|开心|高兴|心情|情绪|能力|表现|水平|靠谱|厉害|进步|变好)"
    r".{0,8}(?:的)?(?:你|塔菲|小菲|机器人|qqbot|bot)", re.IGNORECASE
)
_USER_ASSESSMENT_RE = re.compile(r"你(?:觉得|认为|说|看).{0,8}我|我.{0,8}(?:聪明|怎么样)")
_OPINION_RE = re.compile(
    r"你觉得|你怎么看|你认为|你喜欢|最喜欢|喜欢看|你爱不爱|我聪明吗|我怎么样|"
    r"你同意|你站哪边|你更喜欢|你会选谁|如何评价|怎么看待|怎么样|好笑吗|有意思吗"
)
_ANALOGY_RE = re.compile(
    r"(?:像|类比|相当于|对标|对应|好比|类似于).{0,12}"
    r"(?:谁|什么|哪个|球员|人物|选手|队伍|战队)", re.IGNORECASE
)
_EMOTION_RE = re.compile(
    r"我(?:有点|很|好|最近)?(?:难过|伤心|低落|焦虑|烦|累|压力大|开心|高兴|委屈|无助)"
    r"|(?:安慰我|陪陪我|我需要倾诉)"
)
_TASK_RE = re.compile(
    r"帮我|请你|给我(?:做|写|生成|整理|总结)|生成|写一份|写一个|翻译|总结这篇|"
    r"做一份|制作|计划|步骤|方案"
)
# 明确的"让我做某事"指令。这类消息要真的执行任务，不能被当成短闲聊接梗，
# 否则会收到"45字内、不解释"的社交提示而只回一句俏皮话。
# 这里只收"生成/演算/复述"类动词；"解释/说明/介绍"属于知识查询，不在此列，
# 以免挡住它们本应走的联网检索。
_IMPERATIVE_TASK_RE = re.compile(
    r"^(?:请|麻烦|帮我|给我|你|我(?:叫|让)你|叫你)?\s*(?:给我)?\s*"
    r"(?:背诵|默写|复述|朗读|背出|背一下|背|证明|推导|演算|计算|算一下|算算|算(?!了)|"
    r"翻译|列举|列出|列个|总结|概括|整理|写一段|写一篇|写个|写一首|写|编一个|编|"
    r"画一个|画|生成一个|生成|做个|做一个|分析一下|对比一下|"
    # 内容表演类请求同样是"让你做事"，不是闲聊：
    # 实测"讲个冷笑话"被判成 chitchat，模型只调侃不执行，被催第二次才讲。
    r"讲(?:个|一个|一段|一句)?(?:冷笑话|笑话|段子|故事|谜语|绕口令)|"
    r"说(?:个|一个|一段)?(?:冷笑话|笑话|段子|故事|谜语|绕口令)|"
    r"来(?:个|一个|一段|首|句)?(?:冷笑话|笑话|段子|诗|歌词|谜语|绕口令)|"
    r"整(?:个|一个|一段)?(?:笑话|段子)|唱(?:首|一段|个|一句)?)",
    re.IGNORECASE,
)
_GUIDE_RE = re.compile(
    r"攻略|怎么打|怎么抽卡|怎么抓|怎么过|怎么获得|怎么升级|怎么搭配|怎么配队|"
    r"什么阵容|什么精灵|什么角色|通关|培养|配队|阵容|徽章|关卡|抽卡"
)
_CHAT_RE = re.compile(r"^(?:在吗|早|晚安|哈哈+|嘿嘿+|嗨|hello|hi|你好|谢谢|谢啦)[!！。？?\s]*$", re.IGNORECASE)
_QUESTION_RE = re.compile(r"\?|？|吗|呢|什么|怎么|如何|为什么|谁|哪(?:里|个|天|年)|多少")
_SOCIAL_REACTION_RE = re.compile(
    r"^(?:扣\s*\d{1,4}\s*(?:送|给|抽)\s*.+|"
    r"(?:\d{1,4}\s*)?(?:大哥|老哥|老板).{0,10}(?:真|真的).{0,8}(?:送|给)(?:吗)?[?？]?|"
    r"(?:你|塔菲|小菲).{0,10}(?:是|像|又|还).{0,10}"
    r"(?:牛马|牲口|笨蛋|傻子|傻瓜|摸鱼|懒|菜|怂|小丑|废物|呆子|蠢)"
    r".{0,6}(?:吗|呢|呀|啊|嘛)?[?？!！。]?|"
    r"(?:塔菲|小菲)?你(?:又|是不是).{1,12})$"
)
_REALTIME_RE = re.compile(
    r"天气|气温|温度|比分|赛况|股价|汇率|直播动态|直播了什么|"
    r"(?:最新|最近|近期|目前|当前|现任|现在).{0,15}"
    r"(?:动态|新闻|消息|政策|法规|发布|直播|更新|价格|任职|董事长|CEO|主席|比赛|转会|事件)"
    r"|(?:今年|本届|当前).{0,12}(?:世界杯|冠军|选举|比赛|赛事)"
    r"|(?:现在|今天|今日|实时).{0,15}(?:多少钱|金价|报价|市价)"
)
_FACT_RE = re.compile(
    r"介绍一下|简介|资料|谁是|是谁|什么是|是什么|出生|生日|年龄|几岁|多大|多少岁|"
    r"什么时候|哪天|几月|几号|哪一年|何时|在哪|哪里|多少|作品|作者|导演|"
    r"定义|含义|区别|海拔|高度|多少钱|价格|身价|现状|冠军|比分|汇率|新闻|天气|"
    r"(?:怎么|如何).{0,8}(?:计算|获得|使用|制作|安装|配置)"
)
_MEME_SEARCH_RE = re.compile(
    r"(?:我是说|我说的是|我问的是|说的是).{0,8}梗|"
    r"梗.{0,10}(?:是什么|来源|什么意思)"
)
_TOPIC_RESET_RE = re.compile(r"换个话题|换个问题|说点别的|聊点别的|另一个话题|新问题")
_GROUP_CONTEXT_RE = re.compile(r"群里|群聊|群友|大家刚才|上面那位|其他人刚")
_MEMORY_RECALL_RE = re.compile(
    r"(?:还记得|记不记得|回忆|历史记录)|"
    r"(?:刚才|刚刚|上次|之前|前几天|几天前|昨天|前天|上周|上星期|上个月)"
    r".{0,18}(?:我们|群里|大家|聊|说过|说的|讨论|约过|约好|约的|定过|那个|那个呢)"
)
_SHORT_FOLLOWUP_RE = re.compile(
    r"^(?:笑点解析|解释(?:一下|下)?|展开(?:说说)?|为什么(?:这么说|这样说)|接着(?:说|讲)|继续(?:说|讲)?|然后呢)[?？!！。\s]*$"
)
# 省略主语的追问：句子本身不含任何话题，必须继承上下文，否则一定答非所问。
# 实测：群里讨论考研/就业时问"你觉得呢"，被判成 context=none，模型只看到
# 这四个字，不知道在聊什么，只能回一句"你说了算"这种废话。
_ELIDED_FOLLOWUP_RE = re.compile(
    r"^(?:那|所以|然后|还有|不过|恩|嗯)?\s*(?:你|你们|他|她|大家|群友|老哥)?\s*"
    r"(?:觉得|认为|(?:怎么|咋)?(?:看|说|选|想|办)|倾向|支持|同意)"
    r"\s*(?:呢|呐|怎么样|如何|怎么讲)?[?？!！。~\s]*$"
    r"|^(?:你呢|你们呢|他呢|她呢|大家呢|然后呢|所以呢|接着呢|那呢|怎么说|怎么讲)"
    r"[?？!！。~\s]*$"
)
_FOLLOWUP_RE = re.compile(
    r"^(?:那|然后|再|还|他|她|它|另外|还有|但是|不过)"
    r"|^(?:谁|什么|怎么|为啥|为什么)[呢啊呀吗?？!！\s]*$"
    r"|刚才那个|刚刚那个|上一条|前面那个|继续说|接着说|展开说说|刚才说的|"
    r"我之前问|我上次问|我以前说|我之前说|你还记得|我提过|历史记录|"
    r"你刚才说|刚才提到|刚才说的|前面说的|接着刚才"
)
_LEGACY_SPEAKER_RE = re.compile(r"(?:^|\n)[^()\n]{0,40}\((\d{4,})\):")


# 关于"塔菲自己"的设定类问题：年龄、口味、粉丝称呼、设定等。
# 这类问题问的是角色设定，不该联网——否则会把角色的自述当成现实事实去查，
# 实测出现过"塔菲你多大了"触发搜索并把答案说成"永远的 217 岁"。
_BOT_SELF_FACT_RE = re.compile(
    r"(?:塔菲|小菲|你|机器人|qqbot|bot).{0,10}"
    r"(?:多大|几岁|年龄|生日|喜欢吃什么|爱吃什么|讨厌吃什么|喜欢什么|"
    r"粉丝叫|粉丝是|有什么设定|是什么设定|什么人设)"
)


def is_bot_self_fact_query(query):
    """问的是角色自身设定（年龄/口味/称呼），区别于"评价当前表现"。"""
    return bool(_BOT_SELF_FACT_RE.search(str(query or "")))


def is_bot_self_assessment_query(query):
    text = str(query or "")
    if _BOT_SELF_FACT_RE.search(text):
        return True
    return bool(_BOT_SELF_ASSESSMENT_RE.search(text)) and not bool(
        _USER_ASSESSMENT_RE.search(text)
    )


def classify_message(query, always_search=False):
    text = " ".join(str(query or "").split())
    if not text:
        return MessageRoute("chitchat", "NEVER", "none", False)

    topic_reset = bool(_TOPIC_RESET_RE.search(text))
    local_query = ""
    if _LOCAL_MODEL_RE.search(text) and re.search(
        r"你|塔菲|小菲|机器人|qqbot|当前|现在|正在", text, re.IGNORECASE
    ):
        local_query = "model"
    elif _LOCAL_STATUS_RE.search(text):
        local_query = "status"

    self_assessment = is_bot_self_assessment_query(text)
    emotional = bool(_EMOTION_RE.search(text))
    opinion = bool(_OPINION_RE.search(text))
    explicit_search = bool(_EXPLICIT_SEARCH_RE.search(text))
    realtime = bool(_REALTIME_RE.search(text))
    factual = bool(_FACT_RE.search(text))
    analogy = bool(_ANALOGY_RE.search(text))
    has_url = bool(re.search(r"https?://", text, re.IGNORECASE))
    social_reaction = bool(_SOCIAL_REACTION_RE.search(text)) and not (
        explicit_search
        or has_url
        or realtime
        or factual
        or _GUIDE_RE.search(text)
        or _MEME_SEARCH_RE.search(text)
        or _TASK_RE.search(text)
        or _IMPERATIVE_TASK_RE.search(text)
    )

    if local_query:
        intent, evidence = "local_status", "LOCAL"
    elif _NO_SEARCH_RE.search(text):
        intent, evidence = "opinion" if opinion else "chitchat", "NEVER"
    elif explicit_search or has_url or realtime:
        intent = "command" if explicit_search or has_url else "fact"
        evidence = "REQUIRED"
    elif (_MEMORY_RECALL_RE.search(text) or _SHORT_FOLLOWUP_RE.search(text)) and not topic_reset:
        intent, evidence = "memory", "NEVER"
    elif self_assessment:
        intent, evidence = "opinion", "NEVER"
    elif emotional:
        intent, evidence = "chitchat", "NEVER"
    elif _IMPERATIVE_TASK_RE.search(text):
        # 明确的执行指令：走任务路径，不套社交短回复
        intent, evidence = "command", "NEVER"
    elif social_reaction:
        intent, evidence = "chitchat", "NEVER"
    elif analogy:
        intent, evidence = "analogy", "NEVER"
    elif factual or _GUIDE_RE.search(text) or _MEME_SEARCH_RE.search(text):
        intent, evidence = "fact", "AUTO"
    elif opinion:
        intent, evidence = "opinion", "NEVER"
    elif _TASK_RE.search(text):
        intent, evidence = "command", "NEVER"
    elif _CHAT_RE.search(text):
        intent, evidence = "chitchat", "NEVER"
    elif always_search and _QUESTION_RE.search(text):
        intent, evidence = "fact", "AUTO"
    else:
        intent, evidence = "chitchat", "NEVER"

    should_search = evidence == "REQUIRED" or (
        evidence == "AUTO" and bool(factual or _GUIDE_RE.search(text) or _MEME_SEARCH_RE.search(text) or always_search)
    )
    if topic_reset:
        context = "none"
    elif intent == "memory":
        personal = re.search(r"记得我|我(?:喜欢|之前|上次|以前|偏好)", text)
        context = "group" if not personal and re.search(r"我们|群里|群聊|群友|大家", text) else "same_user"
    elif _ELIDED_FOLLOWUP_RE.search(text):
        # 省略主语的追问（"你觉得呢""大家怎么看"）：本身没有话题，
        # 必须带上近期上下文，否则只能打太极。私聊下该值同样走通用分支。
        context = "group"
    elif _GROUP_CONTEXT_RE.search(text) and _FOLLOWUP_RE.search(text):
        context = "group"
    elif _FOLLOWUP_RE.search(text):
        context = "same_user"
    else:
        context = "none"
    return MessageRoute(
        intent, evidence, context, should_search, local_query, social_reaction
    )


def select_context_messages(messages, route, speaker_id=None, is_group=False, limit=100):
    """Select only explicitly requested context, isolating group turns by sender."""
    if route.context == "none":
        return []
    items = list(messages or [])
    if route.context == "group" and is_group:
        return items[-max(1, int(limit)):]
    if is_group:
        wanted = str(speaker_id or "")
        if not wanted:
            return []
        selected = []
        current_speaker = ""
        for item in items:
            role = item.get("role")
            if role == "user":
                current_speaker = str(item.get("speaker_id") or "")
                if not current_speaker:
                    match = _LEGACY_SPEAKER_RE.search(str(item.get("content") or ""))
                    current_speaker = match.group(1) if match else ""
            item_speaker = str(item.get("speaker_id") or current_speaker)
            if item_speaker == wanted:
                selected.append(item)
        return selected[-max(1, int(limit)):]
    return items[-max(1, int(limit)):]


_GROUP_UNWELCOME_RE = re.compile(
    r"别插话|闭嘴|别说话|不要机器人|吵架|去世|住院|隐私|身份证|密码|银行卡|转账|骗子"
)
# 有人在群里向"大家"发问，是较好的接话机会
_GROUP_QUESTION_RE = re.compile(
    r"有没有人|谁(?:知道|来|在)|大家(?:觉得|说|怎么看)|有人吗|求(?:推荐|解答)|"
    r"怎么(?:办|搞|弄)|要不要|该不该|[?？]\s*$"
)
# 话题已经收尾，不该再插话
_TOPIC_CLOSED_RE = re.compile(r"算了|不聊了|先这样|睡了|晚安|撤了|走了|下次再|散了吧")
# 吐槽/游戏/情绪类：更适合自然接话。
# 注意排除"哈哈/笑死"这类纯笑声——一个笑声不是邀请，插话反而突兀。
_RANT_RE = re.compile(
    r"连败|连跪|掉分|掉段|输麻|卡了|裂开|离谱|太菜|上分|排位|"
    r"好烦|累死|麻了|无语|服了|绷不住",
    re.IGNORECASE,
)


# 纯粹的笑声/附和/单字：这类消息不构成"邀请"，即使很久没说话也不主动接。
_FILLER_RE = re.compile(
    r"^(?:(?:哈+|嘿+|呵+|嘻+|笑死(?:了)?|草|6+|9+|哦+|噢+|嗯+|啊+|呃+|额+|"
    r"好(?:的)?|行|中|收到|确实|可以|牛(?:逼|批)?|顶|赞|同上|加一|"
    r"[+＋]1|[?？!！。.、,，…~～\s]+))+$"
)


def _is_filler(text):
    return bool(_FILLER_RE.match(" ".join(str(text or "").split())))


def proactive_opportunity_score(
    query,
    messages,
    learned_terms=(),
    has_directed_reference=False,
    bot_recent_texts=(),
    recent_message_count=None,
    seconds_since_bot_spoke=None,
    mentions_bot=False,
    min_recent_messages=2,
    self_quiet_seconds=120,
    idle_bonus_seconds=300,
):
    """规则化评估"这次适不适合自己接话"，不调用任何模型。

    返回 (score, reasons)：score 越高越"该接"；0 表示明确不该接。
    理由用于决策日志——只记原因与信号，不落敏感原文。

    与"能不能接"不同，这里判断的是"合不合适"：冷场里突然插话、刚说完又插、
    话题已收尾都属于不合适，即使这句话本身可以接。
    """
    reasons = []
    text = " ".join(str(query or "").split())
    if has_directed_reference:
        return 0, ["已被@或引用他人"]
    if not text:
        return 0, ["空消息"]
    if len(text) > 100:
        return 0, ["消息过长"]
    if re.search(r"https?://|www\.", text, re.IGNORECASE):
        return 0, ["含链接"]

    recent_text = "\n".join(
        str(m.get("content", "")) for m in list(messages or [])[-12:]
    )
    if _GROUP_UNWELCOME_RE.search(recent_text + "\n" + text):
        return 0, ["群内不欢迎机器人或话题敏感"]

    route = classify_message(text, always_search=False)
    # REQUIRED = 需要联网核实的实时/事实问题，不抢答，避免不查就编。
    if route.evidence == "REQUIRED":
        return 0, ["需要联网核实，不抢答"]
    if route.intent not in ("chitchat", "opinion", "analogy"):
        return 0, [f"意图={route.intent}，不抢答"]

    users = [item for item in list(messages or []) if item.get("role") == "user"]
    speakers = {str(item.get("speaker_id") or "") for item in users}
    speakers.discard("")
    # 2026-10-17：放宽参与门槛。
    # 原条件要求"≥2 条群友消息 且 ≥2 个不同发言人"，在安静的小群里几乎永远
    # 不满足（实测今天 115 次决策里 23 次死在这一条），而且它在 mentions_bot
    # 判断之前返回 0——有人直接说"塔菲在干嘛"也会因为群里只有一个人说话而沉默。
    # 现在：被提到时不看人数；否则只要有 ≥2 条群友消息（允许同一人连说）即可。
    if not mentions_bot and (len(users) < 2 or not speakers):
        return 0, ["参与人数不足两人"]
    # 对话不活跃时插话很突兀（刚开一个话题、或大家早散了）
    if (
        recent_message_count is not None
        and recent_message_count < int(min_recent_messages)
        and not mentions_bot
    ):
        return 0, [f"对话不活跃(近段仅{recent_message_count}条)，插话突兀"]

    score = 1
    # 2026-10-17：区分"内容类"与"环境类"信号。
    # 内容类（有接话点/有人发问/吐槽话题/群梗/提到塔菲）说明这次发言**有价值**；
    # 环境类（对话正热、很久没参与）只说明**有机会**。
    # 只有环境类而没有内容类时，不足以构成主动接话的理由——避免"为了参与而参与"。
    substantive = 0
    if route.social_reaction:
        score += 1
        substantive += 1
        reasons.append("有明确接话点")
    if _GROUP_QUESTION_RE.search(text):
        score += 1
        substantive += 1
        reasons.append("有人在群里发问")
    if _RANT_RE.search(text):
        score += 1
        substantive += 1
        reasons.append("吐槽/游戏/情绪话题")
    if any(
        isinstance(term, str) and 2 <= len(term) <= 12 and term in text
        for term in (learned_terms or ())
    ):
        score += 1
        substantive += 1
        reasons.append("踩到已审核群梗")
    if mentions_bot:
        # 没被 @，但话里提到它：这是最自然该接的时机
        score += 2
        substantive += 2
        reasons.append("消息里提到塔菲")
    if recent_message_count is not None and recent_message_count >= 4:
        score += 1
        reasons.append("对话正热")
    if _TOPIC_CLOSED_RE.search(text):
        score -= 2
        reasons.append("话题已收尾")
    if (
        seconds_since_bot_spoke is not None
        and seconds_since_bot_spoke < int(self_quiet_seconds)
    ):
        # 刚说完又接，容易被当成刷屏
        score -= 2
        reasons.append(f"机器人{int(seconds_since_bot_spoke)}秒前刚说过")
    elif (
        not _is_filler(text)
        and (seconds_since_bot_spoke is None
             or seconds_since_bot_spoke >= int(idle_bonus_seconds))
    ):
        # 很久没参与（或从没说过）+ 这句话有点内容 -> 可以自然接一句。
        # 纯笑声/单字不算"有内容"，避免对"笑死"也插嘴。
        score += 1
        reasons.append("很久没参与，可以接一句")
    if substantive == 0:
        # 只有"热闹/久未参与"这类环境信号：这次发言没有内容切入点，
        # 不因为热闹本身或沉默时间长而强行插话（被 @ 的路径不受此限）。
        return 0, reasons + ["缺少内容切入点"]
    # 与机器人刚说过的话高度重复时，避免刷屏式复读
    for previous in (bot_recent_texts or ()):
        previous = str(previous or "").strip()
        if previous and (previous == text or (len(text) > 4 and text in previous)):
            score -= 2
            reasons.append("与机器人刚说的重复")
            break
    return max(0, score), reasons


def should_proactively_join_group(
    query,
    messages,
    learned_terms=(),
    has_directed_reference=False,
    bot_recent_texts=(),
    threshold=2,
):
    """Gate unsolicited group reactions without model calls. 见 proactive_opportunity_score。"""
    score, _ = proactive_opportunity_score(
        query,
        messages,
        learned_terms=learned_terms,
        has_directed_reference=has_directed_reference,
        bot_recent_texts=bot_recent_texts,
    )
    return score >= max(1, int(threshold))
