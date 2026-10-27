# -*- coding: utf-8 -*-
"""
学习通（超星）作业自动作答工具
===============================

本工具把「抓取作业页 → 解析题目 → 按题作答 → 暂存/交卷 → 校验」整套流程固化为命令。

用法（在 workspace 下执行）：
  python lxt-auto/lxt.py fetch  "<dowork 链接>" [--cookie-file lxt-auto/cookie.txt]
  python lxt-auto/lxt.py parse  [--html lxt-auto/work.html]
  python lxt-auto/lxt.py save   --answers lxt-auto/answers.json
  python lxt-auto/lxt.py submit --answers lxt-auto/answers.json
  python lxt-auto/lxt.py verify

核心机制（重要，别踩坑）
------------------------
1. 选项在页面上是**乱序**的。每个选项有两个字母：
   - `data` 属性 = 真实答案键（提交时必须用这个）
   - span 里的文字 = 学生屏幕上看到的显示字母（display）
   答案必须按 `data`（真实键）提交，不是按屏幕上看到的字母。
2. 「暂时保存」= pyFlag=1，可反复覆盖，不交卷。
   「交卷」= pyFlag 为空，且需要先过 /work/validate。
3. 提交地址、token、enc 等参数全部从作业页的 <form> 里现抓，所以换作业也能用。
"""
import argparse
import html as html_mod
import json
import os
import re
import sys

import requests

BASE = "https://mooc1-api.chaoxing.com"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
WORK_HTML = os.path.join(HERE, "work.html")
QUESTIONS_JSON = os.path.join(HERE, "questions.json")
COOKIE_FILE = os.path.join(HERE, "cookie.txt")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


# ---------------------------------------------------------------- 基础工具

def load_cookie(path=COOKIE_FILE):
    if not os.path.exists(path):
        sys.exit(f"找不到 cookie 文件：{path}\n请先把浏览器里的完整 Cookie 字符串存进去。")
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def headers(cookie, referer=None):
    h = {
        "User-Agent": UA,
        "Cookie": cookie,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if referer:
        h["Referer"] = referer
    return h


def load_cookies_for_browser():
    """从桌面端 cxstudy 的 Chromium 存储里解出 Cookie，转成 Playwright 需要的格式。"""
    import base64
    import ctypes
    import shutil
    import sqlite3
    import tempfile
    from ctypes import wintypes

    from Crypto.Cipher import AES

    root = os.path.join(os.environ["APPDATA"], "cxstudy")
    local_state = os.path.join(root, "Local State")
    cookies_db = os.path.join(root, "Network", "Cookies")
    if not os.path.exists(local_state) or not os.path.exists(cookies_db):
        sys.exit("找不到桌面端 Cookie 存储，请确认学习通已安装并登录。")

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def dpapi(data):
        b = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data),
                                             ctypes.POINTER(ctypes.c_char)))
        o = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(b), None, None, None, None, 0, ctypes.byref(o)):
            raise OSError("DPAPI 解密失败")
        try:
            return ctypes.string_at(o.pbData, o.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(o.pbData)

    with open(local_state, encoding="utf-8") as f:
        st = json.load(f)
    key = dpapi(base64.b64decode(st["os_crypt"]["encrypted_key"])[5:])

    def decrypt(blob):
        if not blob:
            return ""
        if blob[:3] in (b"v10", b"v11"):
            c = AES.new(key, AES.MODE_GCM, nonce=blob[3:15])
            return c.decrypt_and_verify(blob[15:-16], blob[-16:]).decode("utf-8", "ignore")
        return blob.decode("utf-8", "ignore")

    tmp = os.path.join(tempfile.gettempdir(), "cx_batch.db")
    shutil.copy2(cookies_db, tmp)
    con = sqlite3.connect(tmp)
    rows = con.execute(
        "SELECT host_key,name,encrypted_value,value,path,expires_utc,is_secure,is_httponly,samesite"
        " FROM cookies").fetchall()
    con.close()

    out = []
    for host, name, enc, plain, path, exp, secure, http_only, same_site in rows:
        val = plain if plain else decrypt(enc)
        if not val:
            continue
        expires = -1
        if exp:
            expires = exp / 1_000_000 - 11644473600
        out.append({
            "name": name, "value": val, "domain": host, "path": path or "/",
            "expires": expires, "httpOnly": bool(http_only), "secure": bool(secure),
            "sameSite": {0: "None", 1: "Lax", 2: "Strict"}.get(same_site, "Lax"),
        })
    return out


def load_state(path=None):
    path = path or STATE
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(st, path=None):
    path = path or STATE
    with open(path, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def clean(s):
    s = html_mod.unescape(s)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- 抓取

def cmd_fetch(args):
    cookie = load_cookie(args.cookie_file)
    r = requests.get(args.url, headers=headers(cookie), timeout=40, allow_redirects=True)
    if "passport2.chaoxing.com" in r.url or "登录" in r.text[:2000]:
        sys.exit("抓取被重定向到登录页 —— Cookie 已失效，请重新从浏览器复制。")
    if r.status_code != 200:
        sys.exit(f"抓取失败，HTTP {r.status_code}")

    with open(WORK_HTML, "w", encoding="utf-8") as f:
        f.write(r.text)

    # 提取 form 基本信息
    form = extract_form(r.text)
    st = load_state()
    st["work"] = {
        "dowork_url": args.url,
        "form_action": form["action"],
        "fields": form["fields"],
        "question_ids": form["question_ids"],
    }
    save_state(st)

    print(f"[fetch] OK  页面 {len(r.text)} 字符，表单字段 {len(form['fields'])} 个")
    print(f"[fetch] 表单 action = {form['action'][:110]}...")
    print(f"[fetch] 题目数量 = {len(form['question_ids'])}")
    print(f"[fetch] 已存 {WORK_HTML}")


def cmd_loadhtml(args):
    """从已保存的 work.html（浏览器渲染结果）里提取表单参数并写入 state。"""
    path = args.html
    if not os.path.exists(path):
        sys.exit(f"找不到 {path}")
    with open(path, encoding="utf-8") as f:
        page = f.read()

    form = extract_form(page)
    if not form["action"]:
        sys.exit("页面里找不到 form action，可能不是作业作答页。")

    st = load_state(args.state or None)
    prev = st.get("work", {})
    st["work"] = {
        "dowork_url": args.url or prev.get("dowork_url", ""),
        "form_action": form["action"],
        "fields": form["fields"],
        "question_ids": form["question_ids"],
    }
    save_state(st, args.state or None)
    print(f"[loadhtml] 表单字段 {len(form['fields'])} 个，题目 {len(form['question_ids'])} 个")
    print(f"[loadhtml] action = {form['action'][:110]}...")


def extract_form(page):
    """从作业页里抽出 <form> 的 action 与所有带 name 的隐藏字段。"""
    m = re.search(r'<form[^>]*action="([^"]*)"', page)
    if not m:
        sys.exit("页面里找不到 <form action=...>，可能不是作业作答页。")
    action = html_mod.unescape(m.group(1))

    # 只取 submitForm 这一段
    start = page.find("<form")
    end = page.find("</form>", start)
    form_html = page[start:end if end > 0 else len(page)]

    fields = {}
    for im in re.finditer(r"<input\b[^>]*>", form_html):
        tag = im.group(0)
        name = re.search(r'\bname="([^"]*)"', tag)
        if not name:
            continue
        value = re.search(r'\bvalue="([^"]*)"', tag)
        fields[name.group(1)] = html_mod.unescape(value.group(1)) if value else ""

    # 题目 id 顺序（从 answerNNN 字段推）
    qids = [re.match(r"answer(\d+)$", k).group(1)
            for k in fields if re.match(r"answer(\d+)$", k)]
    return {"action": action, "fields": fields, "question_ids": qids}


# ---------------------------------------------------------------- 解析题目

def parse_page(page):
    """把整页 HTML 解析成题目列表（供 cmd_parse 和批量脚本复用）。"""
    blocks = re.split(r'(?=<div class="padBom50 questionLi)', page)
    questions = []
    for blk in blocks:
        if "questionLi" not in blk[:80]:
            continue
        q = parse_question(blk, len(questions) + 1)
        if q:
            questions.append(q)
    return questions


def cmd_parse(args):
    path = args.html
    if not os.path.exists(path):
        sys.exit(f"找不到 {path}，请先执行 fetch。")
    with open(path, encoding="utf-8") as f:
        page = f.read()

    questions = parse_page(page)
    out_path = args.out or QUESTIONS_JSON

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(questions, f, ensure_ascii=False, indent=1)

    from collections import Counter
    print(f"[parse] 共 {len(questions)} 题：{dict(Counter(q['type'] for q in questions))}")
    print(f"[parse] 已存 {out_path}")
    for q in questions:
        print(f"\n[{q['num']}] {q['type']}  qid={q['qid']}")
        print(f"   Q: {q['stem'][:90]}")
        for o in q["options"]:
            print(f"     data={o['data']}  屏幕={o['display']}  {o['content'][:60]}")


def parse_question(blk, num):
    # 属性名可能被 DOM 序列化成小写（typename），两种都认
    m_type = re.search(r'type[Nn]ame="([^"]+)"', blk)
    m_qid = re.search(r'id="question(\d+)"', blk)
    if not m_qid:
        return None

    # 没有 typeName 时，用选项的 qtype 推断
    m_qtype = re.search(r'qtype="(\d+)"', blk)
    type_map = {"0": "单选题", "1": "多选题", "3": "判断题", "2": "填空题"}
    qtype_name = type_map.get(m_qtype.group(1), "") if m_qtype else ""
    type_name = m_type.group(1) if m_type else qtype_name
    if not type_name:
        return None

    stem = ""
    mh3 = re.search(r"<h3[^>]*>(.*?)</h3>", blk, re.S)
    if mh3:
        stem = clean(mh3.group(1))
        stem = re.sub(r"^\d+\.\s*", "", stem)
        stem = re.sub(r"^\([^)]*\)\s*", "", stem)

    options = []
    # aria-label 可能被 DOM 序列化成双引号，也可能保持单引号
    opt_re = re.compile(r'<div class="clearfix answerBg[^>]*aria-label=(["\'])(.*?)\1[^>]*>', re.S)
    for om in opt_re.finditer(blk):
        label = om.group(2)
        seg = blk[om.end(): om.end() + 600]

        span_m = re.search(r"<span[^>]*num_option[^>]*>(.*?)</span>", seg, re.S)
        if span_m:
            tag = span_m.group(0)
            md = re.search(r'\bdata="([^"]*)"', tag)
            data_val = md.group(1) if md else ""
            display = clean(span_m.group(1))
        else:
            data_val, display = "", ""

        m_text = re.search(r"answer_p[^>]*>(.*?)</div>", seg, re.S)
        content = clean(m_text.group(1)) if m_text else ""
        if not content:  # 兜底：用 aria-label
            content = re.sub(r"^[A-Z]\s*", "", label)
            content = re.sub(r"选择$", "", content).strip()

        # aria-label 里的前缀字母是真实键
        m_key = re.match(r"([A-Z])\s", label)
        options.append({
            "data": data_val or (m_key.group(1) if m_key else ""),
            "display": display,
            "content": content,
        })

    return {
        "num": num,
        "qid": m_qid.group(1),
        "type": type_name,
        "stem": stem,
        "options": options,
    }


# ---------------------------------------------------------------- 作答提交

def build_payload(st, answers):
    """answers: {qid: "A" 或 "ABCE"}，值为真实键。"""
    fields = dict(st["work"]["fields"])
    qids = st["work"]["question_ids"]

    for qid in qids:
        fields[f"answer{qid}"] = answers.get(qid, "")
    fields["answerwqbid"] = ",".join(qids) + ","
    fields["randomOptions"] = "true"
    return fields


def do_post(st, answers, submit=False):
    cookie = load_cookie()
    action = st["work"]["form_action"]

    # 页面脚本里 action 是相对路径，拼成绝对地址
    if action.startswith("/"):
        url = BASE + action
    else:
        url = action

    fields = build_payload(st, answers)
    fields["pyFlag"] = "" if submit else "1"

    params = url + ("&" if "?" in url else "?") + \
        ("ua=pc&formType=post&saveStatus=1&version=1")

    h = headers(cookie, referer=st["work"]["dowork_url"])
    h["X-Requested-With"] = "XMLHttpRequest"
    h["Origin"] = BASE
    h["Accept"] = "application/json, text/javascript, */*; q=0.01"

    r = requests.post(params, data=fields, headers=h, timeout=40, allow_redirects=False)
    try:
        body = r.json()
    except ValueError:
        sys.exit(f"返回不是 JSON，HTTP {r.status_code}：{r.text[:400]}")
    return r.status_code, body


def cmd_save(args):
    st = load_state(getattr(args, "state", None))
    if "work" not in st:
        sys.exit("还没有抓取作业，请先执行 fetch / resolve。")
    answers = load_answers(args.answers)

    code, body = do_post(st, answers, submit=False)
    filled = sum(1 for v in answers.values() if v)
    print(f"[save] HTTP {code}  提交答案 {filled} 题")
    print(f"[save] 返回：{json.dumps(body, ensure_ascii=False)}")
    if not body.get("status"):
        sys.exit("[save] 保存失败")


def cmd_submit(args):
    st = load_state(getattr(args, "state", None))
    if "work" not in st:
        sys.exit("还没有抓取作业，请先执行 fetch / resolve。")
    answers = load_answers(args.answers)

    # 交卷前先过 validate
    cookie = load_cookie()
    f = st["work"]["fields"]
    try:
        v = requests.get(
            f"{BASE}/mooc-ans/work/validate",
            params={"courseId": f.get("courseId", ""),
                    "classId": f.get("classId", ""),
                    "cpi": f.get("cpi", "")},
            headers=headers(cookie), timeout=30)
        print(f"[submit] validate = {v.text[:200]}")
    except Exception as e:
        print(f"[submit] validate 跳过：{e}")

    code, body = do_post(st, answers, submit=True)
    print(f"[submit] HTTP {code}")
    print(f"[submit] 返回：{json.dumps(body, ensure_ascii=False)}")


def load_answers(path):
    if not os.path.exists(path):
        sys.exit(f"找不到答案文件 {path}")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    # 允许 {"qid": "A"} 或 {"215411695": {"answer": "A"}} 两种写法
    out = {}
    for k, v in raw.items():
        if k.startswith("_"):     # 忽略 _comment 之类的键
            continue
        out[str(k)] = v["answer"] if isinstance(v, dict) else str(v)
    return out


# ---------------------------------------------------------------- 校验

def cmd_verify(args):
    st = load_state(getattr(args, "state", None))
    if "dowork_url" not in st.get("work", {}):
        sys.exit("还没有抓取作业，请先执行 fetch / resolve。")
    cookie = load_cookie()
    r = requests.get(st["work"]["dowork_url"], headers=headers(cookie), timeout=40)

    saved = {}
    for m in re.finditer(
            r'<input type="hidden" id="answer(\d+)" name="answer\d+" value="([^"]*)"',
            r.text):
        saved[m.group(1)] = m.group(2)

    nonempty = {k: v for k, v in saved.items() if v}
    print(f"[verify] 页面 {len(r.text)} 字符，非空答案 {len(nonempty)} 题")
    for qid, val in saved.items():
        mark = "✅" if val else "⬜"
        print(f"  {mark} {qid}: {val or '(空)'}")


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description="学习通作业自动作答工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch", help="抓取作业页并解析表单")
    p.add_argument("url")
    p.add_argument("--cookie-file", default=COOKIE_FILE)
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("parse", help="解析题目到 questions.json")
    p.add_argument("--html", default=WORK_HTML)
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("loadhtml", help="从已保存的页面提取表单参数")
    p.add_argument("--html", default=WORK_HTML)
    p.add_argument("--url", default="")
    p.add_argument("--state", default="")
    p.set_defaults(func=cmd_loadhtml)

    p = sub.add_parser("save", help="暂时保存答案（不交卷）")
    p.add_argument("--answers", default=os.path.join(HERE, "answers.json"))
    p.add_argument("--state", default="")
    p.set_defaults(func=cmd_save)

    p = sub.add_parser("submit", help="正式交卷（谨慎）")
    p.add_argument("--answers", default=os.path.join(HERE, "answers.json"))
    p.add_argument("--state", default="")
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("verify", help="重新抓页面确认已保存的答案")
    p.add_argument("--state", default="")
    p.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
