# -*- coding: utf-8 -*-
"""从 A/B0/B1/C 实验结果生成对照报告（含 B1 vs B0 的逐指标增减）。"""
import glob
import json
import os
import statistics
import sys

BASE = r"<项目根>"
out_dir = sys.argv[1] if len(sys.argv) > 1 else sorted(
    glob.glob(os.path.join(BASE, ".tmp", "abx-*")))[-1]
rows = json.load(open(os.path.join(out_dir, "samples.json"), encoding="utf-8"))
summary = json.load(open(os.path.join(out_dir, "summary.json"), encoding="utf-8"))
dup = json.load(open(os.path.join(out_dir, "duplication.json"), encoding="utf-8"))

ARMS = ("A", "B0", "B1", "C")
lines = []
def say(s=""):
    lines.append(str(s))
    print(s)

say("# A/B0/B1/C 对照实验报告")
say()
say(f"数据目录: `{os.path.basename(out_dir)}`   样本数: {len(rows)}")
params = {r.get("model") for r in rows if r.get("model")}
temps = {r.get("temp") for r in rows}
tops = {r.get("top_p") for r in rows}
maxt = {r.get("max_tokens") for r in rows}
say(f"模型: {params}   temperature: {temps}   top_p: {tops}   max_tokens: {maxt}")
say(f"同一题同组 3 次完全重复率: {dup}")
say()

say("## 一、汇总统计")
say()
say("| 指标 | A 裸DS | B0 旧人格 | B1 新人格 | C 完整管线 |")
say("| --- | --- | --- | --- | --- |")
keys = ["成功", "失败", "平均字数", "字数中位数", "道具命中率", "平均道具次数",
        "自称率", "语气词率", "冷笑话完整率", "故事够长率", "知识够长率",
        "追问用到历史率", "平均耗时秒"]
for k in keys:
    say(f"| {k} | " + " | ".join(str(summary[a].get(k, "-")) for a in ARMS) + " |")
say()

say("## 二、B1 相对 B0 的变化")
say()
for k in keys:
    try:
        b0 = float(summary["B0"].get(k))
        b1 = float(summary["B1"].get(k))
    except (TypeError, ValueError):
        continue
    delta = b1 - b0
    mark = "↑" if delta > 0 else ("↓" if delta < 0 else "=")
    say(f"- **{k}**: {b0} → {b1}  {mark}{abs(round(delta, 2))}")
say()

say("## 三、逐条对照（每组 3 次的中位字数 / 是否完成）")
say()
say("| # | 类别 | 消息 | A | B0 | B1 | C |")
say("| --- | --- | --- | --- | --- | --- | --- |")
for case_no in sorted({r["case_no"] for r in rows}):
    sub = [r for r in rows if r["case_no"] == case_no]
    cat = sub[0]["category"]
    query = sub[0]["query"]
    cells = []
    for arm in ARMS:
        texts = [r["chars"] for r in sub if r["arm"] == arm and r.get("final")]
        cells.append(str(int(statistics.median(texts))) if texts else "失败")
    say(f"| {case_no} | {cat} | {query[:22]} | " + " | ".join(cells) + " |")
say()

say("## 四、固定道具命中情况（每条消息里出现的道具）")
say()
say("| # | 消息 | A | B0 | B1 | C |")
say("| --- | --- | --- | --- | --- | --- |")
for case_no in sorted({r["case_no"] for r in rows}):
    sub = [r for r in rows if r["case_no"] == case_no]
    cells = []
    for arm in ARMS:
        hits = []
        for r in sub:
            if r["arm"] == arm and r.get("props"):
                hits.extend(r["props"].keys())
        cells.append(",".join(sorted(set(hits))) or "—")
    say(f"| {case_no} | {sub[0]['query'][:22]} | " + " | ".join(cells) + " |")
say()

say("## 五、冷笑话完整度（设问 + 包袱）")
say()
say("| # | 消息 | A | B0 | B1 | C |")
say("| --- | --- | --- | --- | --- | --- |")
for case_no in sorted({r["case_no"] for r in rows}):
    sub = [r for r in rows if r["case_no"] == case_no and r["category"] == "冷笑话"]
    if not sub:
        continue
    cells = []
    for arm in ARMS:
        g = [r for r in sub if r["arm"] == arm]
        cells.append(f"{sum(1 for r in g if r.get('joke_complete'))}/{len(g)}")
    say(f"| {case_no} | {sub[0]['query'][:22]} | " + " | ".join(cells) + " |")
say()

say("## 六、失败样本")
say()
fails = [r for r in rows if not r.get("final") or r.get("state") != "ok"]
if not fails:
    say("无。")
else:
    for r in fails:
        say(f"- case{r['case_no']} {r['arm']} rep{r['rep']} state={r.get('state')}")
say()

say("## 七、典型案例（每组 1 条原文）")
say()
for case_no in (1, 4, 6, 9, 14, 18):
    sub = [r for r in rows if r["case_no"] == case_no and r["rep"] == 0]
    if not sub:
        continue
    say(f"### case{case_no} 【{sub[0]['category']}】{sub[0]['query']}")
    say()
    for r in sub:
        text = (r.get("final") or "（空）").replace("\n", " ")
        say(f"- **{r['arm']}**（{r['chars']}字）: {text[:220]}")
    say()

say("## 八、C 组额外注入的内容")
say()
c_rows = [r for r in rows if r["arm"] == "C"]
say(f"- C 组人格 = B1（{len(rows) and 'persona/taffy.md'}）")
say(f"- C 组注入历史条数: {sorted({r.get('history_len', 0) for r in c_rows})}")
say(f"- C 组 system 额外长度: 人格 1836 字 + 功能性提示（当前日期等）")
say(f"- C 组平均 API 调用次数: "
    f"{round(statistics.mean([r.get('api_calls', 1) for r in c_rows]), 2)}")
raw_eq = sum(1 for r in c_rows if r.get("raw") == r.get("final"))
say(f"- C 组 原始正文 == 最终发送: {raw_eq}/{len(c_rows)}")
say()

path = os.path.join(out_dir, "report.md")
with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print(f"\n报告已写入: {path}")
