"""安全护栏模块。"""
import re


SAFETY_PATTERNS = {
    "sensitive": [
        r"(?:聊聊|谈|讨论|评价|怎么看|怎么看|关注|讲).{0,8}(?:政治|宗教|种族)",
        r"(?:政治|宗教|种族)(?:问题|话题|敏感|事件|冲突|歧视|领袖|组织)",
        r"台湾问题|香港问题|新疆问题|西藏问题|法轮|六四|天安门事件",
    ],
    "self_harm": [
        r"自杀|想死|自残|不想活|不想活了|结束生命|活不下去|想结束这一切|撑不下去",
    ],
    "violence": [
        r"杀人|打人|砍人|弄死|伤害别人|暴力伤害|杀人犯",
    ],
    "adult": [
        r"色情|裸体|未成年色情|未成年人色情|援交|卖淫|大尺度",
    ],
    "illegal": [
        r"毒品|买毒|制毒|诈骗|洗钱|走私|黑客攻击|教你黑客|黑客技术",
    ],
}

REFUSAL_TEMPLATES = {
    "sensitive": "这个话题塔菲可能聊不好喵，咱们换个轻松点的吧。",
    "self_harm": (
        "听到你这么说，塔菲很担心你喵。如果你正在经历痛苦，"
        "请考虑联系心理援助热线 400-161-9995。塔菲愿意陪你说说别的，"
        "或只是听你说。"
    ),
    "violence": "塔菲理解你可能很生气，但伤害不是解决办法喵。要不要说说发生了什么？",
    "adult": "这个塔菲没法聊喵，咱们聊点别的吧。",
    "illegal": "这个涉及违法，塔菲不能帮忙喵。如果有困难可以聊聊合法的解决方式。",
}


def _check_safety(text):
    """检查输入是否安全；self_harm 优先级最高。"""
    if not text:
        return True, ""
    for category in ("self_harm", "violence", "illegal", "adult", "sensitive"):
        for pattern in SAFETY_PATTERNS.get(category, []):
            if re.search(pattern, text or "", re.IGNORECASE):
                return False, REFUSAL_TEMPLATES[category]
    return True, ""
