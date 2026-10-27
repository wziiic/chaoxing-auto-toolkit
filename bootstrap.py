# -*- coding: utf-8 -*-
"""
一键自举：首次运行自动补齐依赖、建桌面快捷方式，然后启动面板。

流程:
    1. 检查必需依赖 → 缺了就自动 pip install（失败再换清华源重试）
    2. 检查桌面快捷方式 → 没有就创建
    3. 检查学习通桌面端 → 没有就提示（不阻塞，只是提醒）
    4. 启动面板（panel/app.py 会自己打开浏览器）

单独用法:
    python bootstrap.py            # 完整流程
    python bootstrap.py --check    # 只体检不启动
    python bootstrap.py --no-install  # 不自动装依赖
"""
import argparse
import importlib.util
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PKG_MAP = {
    "requests": "requests",
    "Crypto": "pycryptodome",
    "playwright": "playwright",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "pydantic": "pydantic",
}
REQ_FILE = os.path.join(HERE, "requirements.txt")
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


def _ok(msg):
    print(f"✅ {msg}", flush=True)


def _warn(msg):
    print(f"⚠️  {msg}", flush=True)


def _bad(msg):
    print(f"❌ {msg}", flush=True)


def _step(msg):
    print(f"\n▶ {msg}", flush=True)


def missing_packages():
    return [mod for mod in PKG_MAP if importlib.util.find_spec(mod) is None]


def pip_install(extra_args=None):
    cmd = [sys.executable, "-m", "pip", "install", "-r", REQ_FILE,
           "--disable-pip-version-check"]
    if extra_args:
        cmd += extra_args
    print("   " + " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")


def ensure_deps(auto_install=True):
    _step("检查依赖")
    miss = missing_packages()
    if not miss:
        _ok("依赖齐全")
        return True

    names = ", ".join(PKG_MAP[m] for m in miss)
    _warn(f"缺少依赖: {names}")

    if not auto_install:
        _bad("请手动执行: pip install -r requirements.txt")
        return False

    _step("正在自动安装依赖（首次约 1-3 分钟，请保持网络）")
    ok, out = pip_install()
    if not ok:
        _warn("默认源安装失败，换清华镜像重试…")
        ok, out = pip_install(["-i", MIRROR])
    if not ok:
        _bad("依赖安装失败，请手动执行后重试:")
        print("   pip install -r requirements.txt -i " + MIRROR, flush=True)
        print("---- 最后 800 字输出 ----", flush=True)
        print(out[-800:], flush=True)
        return False

    # 装完重新检查一次（有些包需要重启进程才可见，这里只做提示）
    miss = missing_packages()
    if miss:
        _warn("依赖已安装，但当前进程未加载，继续启动（通常无影响）")
    else:
        _ok("依赖安装完成")
    return True


def ensure_icon():
    """确保有可用图标；仓库没带图标时，现场画一个通用图标（不涉及第三方商标）。"""
    ico = os.path.join(HERE, "assets", "chaoxing.ico")
    if os.path.exists(ico):
        return ico
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return ""

    os.makedirs(os.path.dirname(ico), exist_ok=True)
    S = 256
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=46, fill=(214, 40, 40, 255))
    # 白色播放三角，寓意"启动"
    d.polygon([(98, 74), (98, 182), (186, 128)], fill=(255, 255, 255, 255))
    img.save(ico, format="ICO",
             sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    _ok(f"已生成默认图标: {ico}")
    return ico


def ensure_shortcut():
    _step("检查桌面快捷方式")
    try:
        from tools.make_shortcut import create_shortcut, desktop_dir
    except Exception as e:                      # noqa: BLE001
        _warn(f"跳过快捷方式（{e}）")
        return

    icon = ensure_icon()
    lnk = os.path.join(desktop_dir(), "学习通面板.lnk")
    if os.path.exists(lnk):
        _ok(f"已存在: {lnk}")
        return
    try:
        lnk = create_shortcut(icon=icon or None)
        _ok(f"已创建: {lnk}（以后双击它即可）")
    except Exception as e:                      # noqa: BLE001
        _warn(f"创建失败（不影响使用）: {e}")


def check_desktop_client():
    _step("检查学习通桌面端")
    root = os.path.join(os.environ.get("APPDATA", ""), "cxstudy")
    db = os.path.join(root, "Network", "Cookies")
    if os.path.isdir(root) and os.path.exists(db):
        _ok("桌面端已安装并登录过")
        return True
    if os.path.isdir(root):
        _warn("桌面端已安装，但似乎没登录过 —— 请打开学习通登录一次")
    else:
        _bad("没检测到学习通桌面端（%APPDATA%\\cxstudy）")
        print("   请先安装学习通 Windows 版并登录一次，否则拿不到登录态。", flush=True)
    return False


def launch_panel():
    _step("启动面板")
    app = os.path.join(HERE, "panel", "app.py")
    if not os.path.exists(app):
        _bad(f"找不到 {app}")
        return 1
    print("   启动后会自动打开浏览器，黑窗口请勿关闭\n", flush=True)
    return subprocess.call([sys.executable, app], cwd=HERE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只体检，不启动面板")
    ap.add_argument("--no-install", action="store_true", help="不自动安装依赖")
    args = ap.parse_args()

    print("=" * 58)
    print("  学习通作业面板 · 一键启动")
    print("=" * 58)

    if not ensure_deps(auto_install=not args.no_install):
        print("\n启动中止。", flush=True)
        return 1

    ensure_shortcut()
    client_ok = check_desktop_client()

    if args.check:
        print("\n✅ 体检结束（未启动面板）", flush=True)
        return 0 if client_ok else 0

    if not client_ok:
        print("\n提示：仍可启动面板，但抓题可能失败。", flush=True)

    return launch_panel()


if __name__ == "__main__":
    sys.exit(main())
