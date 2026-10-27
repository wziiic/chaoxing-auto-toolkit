# -*- coding: utf-8 -*-
"""
用真实 Chrome 打开作业入口，自动完成 SSO 跳转，拿到有效的 dowork 页面。

用法:
  python resolve_work.py --task "<work/task 链接>" [--headed]

流程:
  1. 从桌面端 Cookie 库导出 Cookie（含 domain/path/expires）
  2. 注入 Chrome 新上下文
  3. 打开 task 链接，等待跳转链完成
  4. 输出最终 dowork 地址，并把页面存成 lxt-auto/work.html
"""
import argparse
import base64
import ctypes
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from ctypes import wintypes

from Crypto.Cipher import AES
from playwright.sync_api import sync_playwright

ROOT = os.path.join(os.environ["APPDATA"], "cxstudy")
LS = os.path.join(ROOT, "Local State")
DB = os.path.join(ROOT, "Network", "Cookies")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_HTML = os.path.join(HERE, "work.html")
STATE = os.path.join(HERE, "state.json")


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi(data):
    b = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
    o = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(b), None, None, None, None, 0, ctypes.byref(o)):
        raise OSError("DPAPI 解密失败")
    try:
        return ctypes.string_at(o.pbData, o.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(o.pbData)


def master_key():
    st = json.load(open(LS, encoding="utf-8"))
    return dpapi(base64.b64decode(st["os_crypt"]["encrypted_key"])[5:])


KEY = master_key()


def decrypt(blob):
    if not blob:
        return ""
    if blob[:3] in (b"v10", b"v11"):
        c = AES.new(KEY, AES.MODE_GCM, nonce=blob[3:15])
        return c.decrypt_and_verify(blob[15:-16], blob[-16:]).decode("utf-8", "ignore")
    return blob.decode("utf-8", "ignore")


def load_cookies():
    tmp = os.path.join(tempfile.gettempdir(), "cx_pw.db")
    shutil.copy2(DB, tmp)
    con = sqlite3.connect(tmp)
    rows = con.execute(
        "SELECT host_key,name,encrypted_value,value,path,expires_utc,is_secure,is_httponly,samesite FROM cookies"
    ).fetchall()
    con.close()

    out = []
    for host, name, enc, plain, path, exp, secure, http_only, same_site in rows:
        val = plain if plain else decrypt(enc)
        if not val:
            continue
        # Chromium 时间戳(微秒,1601起) -> epoch 秒
        expires = -1
        if exp:
            expires = exp / 1_000_000 - 11644473600
        out.append({
            "name": name,
            "value": val,
            "domain": host,
            "path": path or "/",
            "expires": expires,
            "httpOnly": bool(http_only),
            "secure": bool(secure),
            "sameSite": {0: "None", 1: "Lax", 2: "Strict"}.get(same_site, "Lax"),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, help="work/task 链接")
    ap.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    args = ap.parse_args()

    cookies = load_cookies()
    print(f"[resolve] 注入 Cookie {len(cookies)} 条")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=not args.headed,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
            viewport={"width": 1440, "height": 900},
        )
        ctx.add_cookies(cookies)
        page = ctx.new_page()

        print("[resolve] 打开作业入口…")
        page.goto(args.task, wait_until="domcontentloaded", timeout=60000)

        # 等跳转链 + 页面脚本跑完
        for _ in range(30):
            url = page.url
            if "dowork" in url and "passport" not in url:
                break
            time.sleep(1)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        final = page.url
        html = page.content()
        has_q = "questionLi" in html
        print(f"[resolve] 最终地址: {final[:190]}")
        print(f"[resolve] 是否题目页: {has_q}  页面长度: {len(html)}")

        if not has_q:
            print("[resolve] 未拿到题目页，可能仍需登录。当前页面标题:", page.title())
            ctx.close()
            browser.close()
            return

        with open(OUT_HTML, "w", encoding="utf-8") as f:
            f.write(html)

        # 浏览器里已建立有效网页会话，导出 Cookie 供后续 requests 复用
        jar = []
        for c in ctx.cookies():
            if "chaoxing" in c["domain"]:
                jar.append(f"{c['name']}={c['value']}")
        if jar:
            with open(os.path.join(HERE, "cookie.txt"), "w", encoding="utf-8") as f:
                f.write("; ".join(jar))
            print(f"[resolve] 已导出浏览器会话 Cookie {len(jar)} 条")

        # 记录状态，供 lxt.py save/verify 使用
        st = {}
        if os.path.exists(STATE):
            try:
                st = json.load(open(STATE, encoding="utf-8"))
            except Exception:
                st = {}
        m = re.search(r"workId=(\d+)", final)
        st.setdefault("works", {})[m.group(1) if m else "unknown"] = {
            "dowork_url": final,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

        # 提取表单参数 + 解析题目
        import subprocess, sys
        py = sys.executable
        subprocess.run([py, os.path.join(HERE, "lxt.py"), "loadhtml", "--url", final], check=False)
        subprocess.run([py, os.path.join(HERE, "lxt.py"), "parse"], check=False)

        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()
