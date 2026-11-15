"""Read an existing local encrypted login backup without creating browser pages."""
import json
from pathlib import Path
import time

from study_core import protect_local_link


def saved_platform_cookies(path=None):
    path = Path(path) if path else Path(__file__).with_name('study-data') / 'browser-session.bin'
    if not path.exists():
        return []
    envelope = json.loads(protect_local_link(path.read_bytes(), decrypt=True).decode('utf-8'))
    if envelope.get('version') != 1:
        return []
    cookies = envelope.get('state', {}).get('cookies', [])
    result = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get('domain') or '').lstrip('.').lower()
        expires = cookie.get('expires', -1)
        if (domain == 'chaoxing.com' or domain.endswith('.chaoxing.com')) and isinstance(expires, (int, float)) and (expires == -1 or expires > time.time()):
            if all(isinstance(cookie.get(key), str) for key in ('name', 'value', 'domain', 'path')):
                result.append(cookie)
    return result
