"""Native browser preferences shared by the panel and browser workers."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import webbrowser

CONFIG_PATH = Path(__file__).resolve().parent / 'panel' / 'config.json'
DEFAULT = {'choice': 'system', 'custom_path': '', 'course_network': 'system'}


def executable(choice):
    relative, filename = {
        'chrome': ('Google/Chrome/Application/chrome.exe', 'chrome.exe'),
        'edge': ('Microsoft/Edge/Application/msedge.exe', 'msedge.exe'),
    }[choice]
    for name in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        root = os.environ.get(name)
        if root and (Path(root) / relative).is_file():
            return str(Path(root) / relative)
    found = shutil.which(filename)
    if found:
        return found
    if os.name == 'nt':
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths' + '\\' + filename,
                                        0, winreg.KEY_READ | view) as key:
                        path = winreg.QueryValue(key, None).strip('"')
                        if Path(path).is_file():
                            return path
                except OSError:
                    pass
    return None


def normalize(value):
    if not isinstance(value, dict) or value.get('choice') not in ('system', 'chrome', 'edge', 'custom'):
        raise ValueError('请选择系统默认、Chrome、Edge 或自定义浏览器')
    path = str(value.get('custom_path') or '').strip().strip('"')
    network = value.get('course_network', 'system')
    if network not in ('system', 'direct'):
        raise ValueError('课程网络连接请选择跟随系统或直连')
    if value['choice'] == 'custom':
        if not Path(path).is_absolute() or Path(path).suffix.lower() != '.exe' or not Path(path).is_file():
            raise ValueError('请填写已安装浏览器的完整 .exe 路径，不要附加启动参数')
    elif value['choice'] in ('chrome', 'edge') and not executable(value['choice']):
        raise ValueError('未找到所选浏览器，请先安装，或使用自定义浏览器路径')
    return {'choice': value['choice'], 'custom_path': path, 'course_network': network}


def read_preferences():
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding='utf-8')).get('browser', DEFAULT)
        if isinstance(value, dict) and value.get('choice') in ('system', 'chrome', 'edge', 'custom'):
            return {**DEFAULT, **value}
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return dict(DEFAULT)


def open_panel(url, preferences=None):
    prefs = normalize(preferences if preferences is not None else read_preferences())
    if prefs['choice'] == 'system':
        return webbrowser.open(url)
    path = prefs['custom_path'] if prefs['choice'] == 'custom' else executable(prefs['choice'])
    # An argument list preserves spaces and keeps URLs out of the shell.
    subprocess.Popen([path, url], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return True


def course_channels():
    choice = read_preferences()['choice']
    if choice in ('chrome', 'edge'):
        return ('chrome' if choice == 'chrome' else 'msedge',)
    return ('chrome', 'msedge', None)
