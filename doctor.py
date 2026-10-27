# -*- coding: utf-8 -*-
"""
环境自检：新同学拿到这个仓库后，先跑这个。

    python doctor.py

逐项检查运行前提，标出哪一步缺什么、怎么修。
"""
import importlib
import json
import os
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OK, BAD, WARN = "✅", "❌", "⚠️"

results = []


def check(name, fn, hint=""):
    try:
        ok, detail = fn()
        results.append((ok, name, detail, hint if not ok else ""))
    except Exception as e:                       # noqa: BLE001
        results.append((False, name, f"异常: {e}", hint))


# ---------------------------------------------------------------- 检查项

def c_python():
    v = sys.version_info
    ok = v >= (3, 9)
    return ok, f"{v.major}.{v.minor}.{v.micro}"


def c_packages():
    need = ["requests", "Crypto", "playwright", "fastapi", "uvicorn"]
    missing = []
    for m in need:
        if importlib.util.find_spec(m) is None:
            missing.append(m)
    return (not missing), ("缺少: " + ", ".join(missing)) if missing else "requests/pycryptodome/playwright/fastapi/uvicorn 齐全"


def c_chrome():
    cands = [
        os.path.join(os.environ.get("ProgramFiles", ""), r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
    ]
    for p in cands:
        if p and os.path.exists(p):
            return True, p
    return False, "未找到 chrome.exe"


def c_desktop():
    root = os.path.join(os.environ.get("APPDATA", ""), "cxstudy")
    db = os.path.join(root, "Network", "Cookies")
    if not os.path.isdir(root):
        return False, "未检测到学习通桌面端数据目录 (%APPDATA%\\cxstudy)"
    if not os.path.exists(db):
        return False, "桌面端已安装但未登录过（缺少 Network\\Cookies）"
    return True, root


def c_cookie():
    """尝试真的解一次 Cookie，确认能拿到登录态。"""
    if not os.path.isdir(HERE):
        return False, "路径异常"
    sys.path.insert(0, HERE)
    import lxt
    cookies = lxt.load_cookies_for_browser()
    if not cookies:
        return False, "解出 0 条 Cookie"
    names = {c["name"] for c in cookies}
    if "_uid" not in names:
        return False, f"解出 {len(cookies)} 条但缺 _uid（登录态不完整）"
    uid = next((c["value"] for c in cookies if c["name"] == "_uid"), "?")
    return True, f"{len(cookies)} 条 Cookie，uid={uid}"


def c_llm():
    cfg_path = os.path.join(HERE, "panel", "config.json")
    if not os.path.exists(cfg_path):
        return False, "没有 panel/config.json（可复制 config.example.json）"
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    key = (cfg.get("llm") or {}).get("api_key", "")
    if not key:
        return False, "config.json 里 api_key 为空"
    model = (cfg.get("llm") or {}).get("model", "?")
    return True, f"已配置 key（{len(key)} 字符），模型={model}"


def c_port():
    cfg_path = os.path.join(HERE, "panel", "config.json")
    port = 8765
    if os.path.exists(cfg_path):
        try:
            port = json.load(open(cfg_path, encoding="utf-8")).get("port", 8765)
        except Exception:
            pass
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True, f"{port} 可用"
    except OSError:
        # 端口被占：先判断是不是本面板已经在跑
        try:
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=3) as r:
                data = json.loads(r.read().decode("utf-8"))
            if "llm" in data:
                return True, f"{port} 上已有面板在运行（直接打开 http://127.0.0.1:{port}/ 即可）"
        except Exception:
            pass
        return False, f"{port} 被其他程序占用（改 config.json 的 port）"
    finally:
        s.close()


CHECKS = [
    ("Python 版本 ≥ 3.9", c_python, "装 Python 3.9+ 并加入 PATH"),
    ("依赖包", c_packages, "pip install -r requirements.txt"),
    ("Chrome 浏览器", c_chrome, "装 Chrome；或改 resolve 脚本用 playwright 自带 chromium"),
    ("学习通桌面端 + 已登录", c_desktop, "装学习通桌面端并登录一次"),
    ("Cookie 解密", c_cookie, "确认桌面端处于登录状态后重试"),
    ("LLM 配置", c_llm, "复制 panel/config.example.json 为 panel/config.json 并填 api_key"),
    ("面板端口", c_port, "改 config.json 里的 port"),
]


def main():
    print("=" * 64)
    print("学习通作业自动化 · 环境自检")
    print("=" * 64)
    for name, fn, hint in CHECKS:
        check(name, fn, hint)

    for ok, name, detail, hint in results:
        print(f"{OK if ok else BAD} {name:<22} {detail}")
        if hint:
            print(f"   └─ 修复: {hint}")

    bad = [r for r in results if not r[0]]
    print("=" * 64)
    if not bad:
        print(f"{OK} 全部通过，可以直接 双击 启动面板.bat")
    else:
        print(f"{BAD} 有 {len(bad)} 项未通过，按上面提示处理后重跑本脚本")
    print("=" * 64)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
