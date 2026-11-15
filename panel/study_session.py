"""Windows-user encrypted, local-only browser session storage."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit

from study_core import protect_local_link


def platform_host(host):
    return isinstance(host, str) and (host.lstrip('.').lower() == 'chaoxing.com' or host.lstrip('.').lower().endswith('.chaoxing.com'))


def platform_storage(state):
    """Keep only platform state; never keep a browser's unrelated accounts."""
    if not isinstance(state, dict) or not isinstance(state.get('cookies'), list) or not isinstance(state.get('origins'), list):
        raise ValueError('Invalid browser session')
    cookies = []
    for cookie in state['cookies']:
        if not isinstance(cookie, dict) or not platform_host(cookie.get('domain')):
            continue
        expires = cookie.get('expires', -1)
        if not isinstance(expires, (int, float)) or (expires != -1 and expires <= time.time()):
            continue
        cookies.append(cookie)
    origins = []
    for origin in state['origins']:
        if not isinstance(origin, dict):
            continue
        parts = urlsplit(origin.get('origin', ''))
        if parts.scheme == 'https' and platform_host(parts.hostname) and not parts.username and not parts.password:
            origins.append(origin)
    return {'cookies': cookies, 'origins': origins}


class BrowserSessionStore:
    def __init__(self, path):
        self.path = Path(path)
        self.fingerprint = None

    def load(self):
        if not self.path.exists():
            return None
        envelope = json.loads(protect_local_link(self.path.read_bytes(), decrypt=True).decode('utf-8'))
        if envelope.get('version') != 1:
            raise ValueError('Unsupported browser session')
        return platform_storage(envelope['state'])

    def save(self, state):
        state = platform_storage(state)
        raw = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        fingerprint = hashlib.sha256(raw).digest()
        if self.fingerprint == fingerprint and self.path.exists():
            return
        payload = json.dumps({'version': 1, 'saved_at': time.time(), 'state': state}, ensure_ascii=False).encode('utf-8')
        encrypted = protect_local_link(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The temporary file contains ciphertext only, and replacement is atomic.
        descriptor, temporary = tempfile.mkstemp(prefix='session-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.fingerprint = fingerprint
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
