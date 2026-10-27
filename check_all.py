# -*- coding: utf-8 -*-
"""批量核对：逐份回读页面，统计已保存答案数。"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

wl = json.load(open(os.path.join(HERE, "worklist.json"), encoding="utf-8"))
ids = ["55366359", "55366922", "56511284", "56511822", "56511932", "56512038",
       "56512103", "56512174", "56512231", "56512291", "56512377",
       "57159873", "56241637", "57260630"]

title = {w["workId"]: (w["course"], w["title"]) for w in wl}
print(f"{'workId':<10} {'课程':<14} {'已保存':<6} {'题量':<5} 作业")
print("-" * 78)
total = 0
for wid in ids:
    st = os.path.join(HERE, "works", wid, "state.json")
    qf = os.path.join(HERE, "works", wid, "questions.json")
    n_q = len(json.load(open(qf, encoding="utf-8")))
    r = subprocess.run([PY, os.path.join(HERE, "lxt.py"), "verify", "--state", st],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore")
    m = re.search(r"非空答案 (\d+) 题", r.stdout)
    n_saved = int(m.group(1)) if m else -1
    total += n_saved if n_saved > 0 else 0
    c, t = title.get(wid, ("?", "?"))
    flag = "✅" if n_saved == n_q else "⚠️"
    print(f"{wid:<10} {c[:12]:<14} {flag}{n_saved:<4} {n_q:<5} {t[:26]}")
print("-" * 78)
print(f"合计已保存 {total} 题")
