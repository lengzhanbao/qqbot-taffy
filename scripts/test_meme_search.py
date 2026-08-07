"""Meme/lyric search smoke test for the QQ bot pipeline.

Run from <项目根>:
    python scripts/test_meme_search.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bridge_qq


CASES = [
    ("玄武口音是什么", ["余承东", "竹知了"]),
    ("竹知了是什么梗", ["余承东", "竹知了"]),
    ("哈基米是什么梗", ["哈基米"]),
    ("牢大是什么梗", ["科比"]),
    ("冰红茶是什么梗", ["冰红茶"]),
    ("何意味是什么梗", ["何意味"]),
    ("给你两窝窝是什么梗", ["两窝窝"]),
    ("周杰伦简单爱歌词是什么", ["简单爱", "说不上为什么"]),
    ("周杰伦那天下雨了歌词是什么", ["那天下雨了", "车子缓缓的开"]),
]


async def check_case(query, keywords):
    plans = bridge_qq._rule_plan_for_query(query)
    text_parts = []
    if bridge_qq._is_lyrics_query(query):
        bridge = object.__new__(bridge_qq.Bridge)
        body = await bridge._enrich_lyrics_search(
            "",
            query,
            [plan["query"] for plan in plans[:2]],
        )
        text_parts.append(body or "")
    else:
        for plan in plans[:2]:
            raw = await bridge_qq.search_web_async(
                plan["query"],
                max_results=8,
                exact=True,
            )
            text_parts.append(raw or "")

    try:
        with open(bridge_qq.MEMORY_FILE, encoding="utf-8") as f:
            memory_text = f.read()
        memory_hit, memory_score = bridge_qq.retrieve_knowledge_scored(
            query,
            memory_text,
            scope="private_900000001",
            scope_alias="测试用户",
        )
    except Exception:
        memory_hit, memory_score = "", 0

    combined = "\n".join(text_parts + [memory_hit])
    missing = [kw for kw in keywords if kw not in combined]
    return {
        "query": query,
        "plans": [plan["query"] for plan in plans],
        "text_len": len(combined),
        "memory_score": memory_score,
        "missing": missing,
        "status": "PASS" if not missing else "MISS",
    }


async def main():
    results = []
    for query, keywords in CASES:
        result = await check_case(query, keywords)
        results.append(result)
        bridge_qq.log(
            f"[梗测试] {result['status']} {query} "
            f"plans={result['plans']} len={result['text_len']} "
            f"missing={result['missing']}"
        )
        print(
            f"{result['status']} | {query} | len={result['text_len']} "
            f"| memory={result['memory_score']} | missing={result['missing']}"
        )

    failed = [r for r in results if r["status"] == "MISS"]
    print(f"\nSUMMARY: {len(results) - len(failed)}/{len(results)} PASS")
    if failed:
        for result in failed:
            print(f"MISS: {result['query']} -> {result['missing']}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
