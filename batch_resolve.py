# -*- coding: utf-8 -*-
"""
批量解析：一次浏览器会话内，依次打开多份作业，各自存到 works/<workId>/。

产物:
  works/<workId>/page.html        作业页（浏览器渲染后）
  works/<workId>/state.json       表单参数 + dowork 地址（save 用）
  works/<workId>/questions.json   题目结构

用法:
  python batch_resolve.py --ids 55366359,55366922
  python batch_resolve.py --all-undone
"""
import argparse
import json
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import lxt  # 复用 Cookie 解密 / 表单提取 / 题目解析

WORKLIST = os.path.join(HERE, "worklist.json")
WORKS_DIR = os.path.join(HERE, "works")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def resolve_all(targets, headed=False):
    os.makedirs(WORKS_DIR, exist_ok=True)
    cookies = lxt.load_cookies_for_browser()

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=not headed,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1440, "height": 900})
        ctx.add_cookies(cookies)
        page = ctx.new_page()

        for i, it in enumerate(targets, 1):
            wid = it["workId"]
            print(f"\n[{i}/{len(targets)}] {wid} 《{it['course']}》{it['title']}")
            outdir = os.path.join(WORKS_DIR, wid)
            os.makedirs(outdir, exist_ok=True)

            try:
                page.goto(it["url"], wait_until="domcontentloaded", timeout=60000)
                for _ in range(40):
                    if "dowork" in page.url and "passport" not in page.url:
                        break
                    time.sleep(1)
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                final = page.url
                html = page.content()
            except Exception as e:
                print(f"    ✗ 打开失败: {e}")
                results.append({"workId": wid, "ok": False, "err": str(e)})
                continue

            if "questionLi" not in html:
                print(f"    ✗ 未进入题目页（{final[:80]}）")
                results.append({"workId": wid, "ok": False, "err": "not-dowork"})
                continue

            open(os.path.join(outdir, "page.html"), "w", encoding="utf-8").write(html)

            form = lxt.extract_form(html)
            state = {"work": {"dowork_url": final, "form_action": form["action"],
                              "fields": form["fields"], "question_ids": form["question_ids"]}}
            json.dump(state, open(os.path.join(outdir, "state.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)

            questions = lxt.parse_page(html)
            json.dump(questions, open(os.path.join(outdir, "questions.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)

            from collections import Counter
            dist = dict(Counter(q["type"] for q in questions))
            print(f"    ✓ {len(questions)} 题 {dist}")
            results.append({"workId": wid, "ok": True, "n": len(questions), "dist": dist,
                            "url": final})

        # 导出最新会话 Cookie，供 save 复用
        jar = [f"{c['name']}={c['value']}" for c in ctx.cookies() if "chaoxing" in c["domain"]]
        if jar:
            open(os.path.join(HERE, "cookie.txt"), "w", encoding="utf-8").write("; ".join(jar))
            print(f"\n已更新会话 Cookie {len(jar)} 条")

        ctx.close()
        browser.close()

    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    ap.add_argument("--all-undone", action="store_true")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    wl = json.load(open(WORKLIST, encoding="utf-8"))
    if args.ids:
        want = {x.strip() for x in args.ids.split(",") if x.strip()}
        targets = [w for w in wl if w["workId"] in want]
    elif args.all_undone:
        targets = [w for w in wl if "未提交" in w["status"]]
    else:
        sys.exit("请指定 --ids 或 --all-undone")

    print(f"目标 {len(targets)} 份作业")
    res = resolve_all(targets, headed=args.headed)

    ok = [r for r in res if r["ok"]]
    print(f"\n完成 {len(ok)}/{len(res)}；明细写入 works/ 目录")
    json.dump(res, open(os.path.join(HERE, "resolve_result.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
