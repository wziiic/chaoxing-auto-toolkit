# -*- coding: utf-8 -*-
"""紧凑打印某份作业的题目，便于作答。用法: python show_work.py <workId>"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
wid = sys.argv[1]
p = os.path.join(HERE, "works", wid, "questions.json")
qs = json.load(open(p, encoding="utf-8"))
print(f"=== workId={wid}  共 {len(qs)} 题 ===")
for q in qs:
    print(f"[{q['num']}] {q['type']} {q['qid']}")
    print(f"  Q: {q['stem'][:150]}")
    for o in q["options"]:
        print(f"    {o['data']:<6} {o['content'][:80]}")
