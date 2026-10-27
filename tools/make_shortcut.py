# -*- coding: utf-8 -*-
"""
创建/更新桌面快捷方式（无需 pywin32，用 PowerShell COM 实现）。

单独用法:
    python tools/make_shortcut.py
    python tools/make_shortcut.py --name 我的面板 --target 启动面板.bat
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def desktop_dir() -> str:
    """取当前用户桌面（兼容 OneDrive 重定向）。"""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[Environment]::GetFolderPath('Desktop')"],
        capture_output=True, text=True, encoding="utf-8", errors="ignore")
    d = (out.stdout or "").strip()
    return d or os.path.join(os.path.expanduser("~"), "Desktop")


def create_shortcut(name="学习通面板", target=None, icon=None, overwrite=True) -> str:
    target = target or os.path.join(ROOT, "启动面板.bat")
    if icon is None:
        icon = os.path.join(ROOT, "assets", "chaoxing.ico")

    lnk = os.path.join(desktop_dir(), f"{name}.lnk")
    if os.path.exists(lnk) and not overwrite:
        return lnk

    env = os.environ.copy()
    env.update({
        "LNK_PATH": lnk,
        "LNK_TARGET": target,
        "LNK_WORKDIR": ROOT,
        "LNK_ICON": icon if os.path.exists(icon) else "",
        "LNK_DESC": "启动学习通作业面板并自动打开网页",
    })

    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "$s = $ws.CreateShortcut($env:LNK_PATH); "
        "$s.TargetPath = $env:LNK_TARGET; "
        "$s.WorkingDirectory = $env:LNK_WORKDIR; "
        "if ($env:LNK_ICON) { $s.IconLocation = $env:LNK_ICON + ',0' }; "
        "$s.Description = $env:LNK_DESC; "
        "$s.Save()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", env=env)
    if r.returncode != 0 or not os.path.exists(lnk):
        raise RuntimeError(f"创建快捷方式失败: {(r.stderr or r.stdout or '')[:200]}")
    return lnk


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="学习通面板")
    ap.add_argument("--target", default="")
    ap.add_argument("--icon", default="")
    ap.add_argument("--no-overwrite", action="store_true")
    a = ap.parse_args()

    lnk = create_shortcut(
        name=a.name,
        target=os.path.join(ROOT, a.target) if a.target else None,
        icon=os.path.join(ROOT, a.icon) if a.icon else None,
        overwrite=not a.no_overwrite,
    )
    print(f"✅ 快捷方式已创建: {lnk}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
