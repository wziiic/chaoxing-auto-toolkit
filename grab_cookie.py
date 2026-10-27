# -*- coding: utf-8 -*-
"""
从学习通桌面端（Electron）的 Chromium Cookie 库里取出登录态。

流程：
  Local State -> os_crypt.encrypted_key -> DPAPI 解出 AES 主密钥
  Network/Cookies -> 每行 encrypted_value -> AES-256-GCM 解密
输出：cookie 字符串，写入 lxt-auto/cookie.txt
"""
import base64
import ctypes
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from ctypes import wintypes

from Crypto.Cipher import AES

ROOT = os.path.join(os.environ["APPDATA"], "cxstudy")
LOCAL_STATE = os.path.join(ROOT, "Local State")
COOKIES_DB = os.path.join(ROOT, "Network", "Cookies")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookie.txt")


# ---------------------------------------------------------------- DPAPI

class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_decrypt(data: bytes) -> bytes:
    blob_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError("CryptUnprotectData 失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def get_master_key() -> bytes:
    with open(LOCAL_STATE, encoding="utf-8") as f:
        state = json.load(f)
    enc = base64.b64decode(state["os_crypt"]["encrypted_key"])
    if not enc.startswith(b"DPAPI"):
        raise ValueError("encrypted_key 不是 DPAPI 格式")
    return dpapi_decrypt(enc[5:])


# ---------------------------------------------------------------- 解密

def decrypt_value(blob: bytes, key: bytes) -> str:
    if not blob:
        return ""
    prefix = blob[:3]
    if prefix in (b"v10", b"v11"):
        nonce = blob[3:15]
        payload = blob[15:]
        ct, tag = payload[:-16], payload[-16:]
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ct, tag).decode("utf-8", "ignore")
    if prefix == b"v20":
        # Chrome 127+ 应用绑定加密，需要额外密钥；Electron 一般不使用
        return ""
    return blob.decode("utf-8", "ignore")


def read_cookies(key: bytes, domain_filter=("chaoxing",)):
    tmp = os.path.join(tempfile.gettempdir(), "cx_cookies.db")
    shutil.copy2(COOKIES_DB, tmp)
    con = sqlite3.connect(tmp)
    rows = con.execute(
        "SELECT host_key, name, encrypted_value, value, path, is_secure FROM cookies").fetchall()
    con.close()
    out = []
    for host, name, enc, plain, path, secure in rows:
        if not any(d in host for d in domain_filter):
            continue
        val = plain if plain else decrypt_value(enc, key)
        if val:
            out.append((host, name, val))
    return out


def main():
    key = get_master_key()
    print(f"AES 主密钥长度: {len(key)}")

    cookies = read_cookies(key)
    print(f"命中 chaoxing 域 Cookie: {len(cookies)} 条")

    # 合并成 Cookie 串（同名取一个）
    merged = {}
    for host, name, val in cookies:
        merged.setdefault(name, val)

    print("字段:", ", ".join(sorted(merged)))
    cookie_str = "; ".join(f"{k}={v}" for k, v in merged.items())

    if not merged:
        sys.exit("没取到 Cookie —— 桌面端可能没登录，或用了 v20 加密。")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(cookie_str)
    print(f"已写入 {OUT}（{len(cookie_str)} 字符）")


if __name__ == "__main__":
    main()
