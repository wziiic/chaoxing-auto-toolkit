"""Real browser restart test with synthetic credentials, never a live account."""
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'panel')]
import browser_preferences
from study_browser import CourseBrowser
from study_session import BrowserSessionStore


def main():
    url='https://mooc1.chaoxing.com/session-fixture'
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'session.bin'
        first=CourseBrowser(lambda:None,lambda message:None)
        first.session_store=BrowserSessionStore(path)
        with patch('lxt.load_cookies_for_browser',return_value=[]), patch('browser_preferences.read_preferences',return_value={'choice':'chrome'}):
            first.background()
        try:
            first.context.route('**/*',lambda route:route.fulfill(content_type='text/html',body='<p>Local login fixture</p>'))
            first.page.goto(url)
            first.context.add_cookies([dict(name='fixture-login',value='synthetic-session',domain='.chaoxing.com',path='/',secure=True,httpOnly=True,sameSite='Lax')])
            first.page.evaluate("localStorage.setItem('fixture-login','synthetic-storage')")
            first.page.evaluate('''()=>new Promise((resolve,reject)=>{const r=indexedDB.open('fixture-login',1);r.onupgradeneeded=()=>r.result.createObjectStore('tokens');r.onerror=()=>reject(r.error);r.onsuccess=()=>{const db=r.result;const t=db.transaction('tokens','readwrite');t.objectStore('tokens').put('synthetic-indexeddb','fixture');t.oncomplete=()=>{db.close();resolve()}}})''')
            first.last_session_save=0
            first.maintain_session()
            assert path.exists(), 'A paused browser must persist a manual login'
            assert b'synthetic-session' not in path.read_bytes()
        finally:first.close()
        second=CourseBrowser(lambda:None,lambda message:None)
        second.session_store=BrowserSessionStore(path)
        choice='edge' if browser_preferences.executable('edge') else 'chrome'
        try:
            with patch('browser_preferences.read_preferences',return_value={'choice':choice}), patch('lxt.load_cookies_for_browser',side_effect=AssertionError('Old desktop login must not override saved login')):
                second.background()
            second.context.route('**/*',lambda route:route.fulfill(content_type='text/html',body='<p>Local login fixture</p>'))
            second.page.goto(url)
            assert any(c['name']=='fixture-login' and c['value']=='synthetic-session' for c in second.context.cookies(url))
            assert second.page.evaluate("localStorage.getItem('fixture-login')")=='synthetic-storage'
            assert second.page.evaluate('''()=>new Promise((resolve,reject)=>{const r=indexedDB.open('fixture-login',1);r.onerror=()=>reject(r.error);r.onsuccess=()=>{const db=r.result;const t=db.transaction('tokens');const q=t.objectStore('tokens').get('fixture');q.onsuccess=()=>{resolve(q.result);db.close()}}})''')=='synthetic-indexeddb'
            print('PASS: encrypted login restored after browser restart, including cookies, local storage and IndexedDB; browser='+choice)
        finally:second.close()


if __name__=='__main__':main()
