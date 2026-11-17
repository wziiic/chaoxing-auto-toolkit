"""User-bound credential storage and bounded native Chaoxing sign-in."""
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit, urlencode

from study_core import StudyError, protect_local_link


def login_url(value):
    try:
        url=urlsplit(value)
        return (url.scheme=='https' and url.hostname in ('passport2.chaoxing.com','passport.chaoxing.com')
                and url.port in (None,443) and not url.username and not url.password and url.path.rstrip('/')=='/login')
    except ValueError:
        return False


class LoginSettings:
    def __init__(self, store):
        self.store=store
        self.lock=threading.RLock()
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS login_settings (id INTEGER PRIMARY KEY CHECK(id=1), payload BLOB NOT NULL, enabled INTEGER NOT NULL, attempted REAL NOT NULL DEFAULT 0, blocked INTEGER NOT NULL DEFAULT 0)')

    def _read(self):
        with self.store.connect() as db:
            row=db.execute('SELECT payload,enabled,attempted,blocked FROM login_settings WHERE id=1').fetchone()
        if not row:return None
        try:
            value=json.loads(protect_local_link(row[0],decrypt=True).decode('utf-8'))
        except Exception:
            raise StudyError('本机登录凭据无法解密，请重新保存账号密码') from None
        return dict(value,enabled=bool(row[1]),attempted=row[2],blocked=bool(row[3]))

    def snapshot(self):
        with self.lock:
            value=self._read()
            account=value['account'] if value else ''
            return dict(configured=bool(value),enabled=bool(value and value['enabled']),
                        account_hint=(account[:2]+'***'+account[-2:]) if len(account)>4 else ('***' if account else ''),
                        blocked=bool(value and value['blocked']))

    def save(self, account='', password='', enabled=False, clear=False):
        with self.lock:
            if clear:
                with self.store.connect() as db:db.execute('DELETE FROM login_settings')
                return self.snapshot()
            try:old=self._read()
            except StudyError:
                if not account or not password:raise
                old=None
            account=account.strip()
            if account and old and account!=old['account'] and not password:
                raise StudyError('更换账号时请同时填写密码')
            chosen=dict(account=account or (old['account'] if old else ''),password=password or (old['password'] if old else ''))
            if not chosen['account'] or not chosen['password']:raise StudyError('请先填写学习通账号和密码')
            changed=not old or any(chosen[k]!=old[k] for k in chosen)
            protected=protect_local_link(json.dumps(chosen,ensure_ascii=False).encode('utf-8'))
            with self.store.connect() as db:
                db.execute('INSERT OR REPLACE INTO login_settings VALUES(1,?,?,?,?)',
                           (protected,int(enabled),0 if changed else old['attempted'],0 if changed else int(old['blocked'])))
            return self.snapshot()

    def begin(self):
        with self.lock:
            value=self._read()
            if not value or not value['enabled']:raise StudyError('登录失效，请在设置中保存账号密码并开启自动重新登录，或在课程窗口手动登录')
            if value['blocked']:raise StudyError('上次自动登录未成功，已停止尝试；请核对账号密码或在课程窗口完成验证后继续')
            if time.time()-value['attempted']<300:raise StudyError('短时间内再次登录失效，已暂停自动登录；请检查账号和平台会话')
            with self.store.connect() as db:db.execute('UPDATE login_settings SET attempted=?,blocked=1 WHERE id=1',(time.time(),))
            return value['account'],value['password']

    def success(self):
        with self.lock,self.store.connect() as db:db.execute('UPDATE login_settings SET blocked=0 WHERE id=1')


def sign_in(driver, settings):
    account,password=settings.begin()
    page=driver.page
    if not login_url(page.url):
        page.goto('https://passport2.chaoxing.com/login?'+urlencode({'refer':driver.url or 'https://mooc2-ans.chaoxing.com/visit/courses/list'}),wait_until='domcontentloaded',timeout=25000)
    if not login_url(page.url):raise StudyError('未进入受信任的学习通登录页，已停止自动登录')
    driver.log('登录会话失效，正在使用本机加密保存的账号重新登录')
    def blocked():
        return page.locator('#captcha:visible, #verifyCode:visible, .yidun_panel:visible, .geetest_panel:visible').count()>0
    try:
        if blocked():raise StudyError('登录需要验证码或身份验证，请在课程窗口完成后继续')
        phone=page.locator('#phone');pwd=page.locator('#pwd')
        phone.wait_for(state='visible',timeout=10000);pwd.wait_for(state='visible',timeout=10000)
        if not login_url(page.url):raise StudyError('登录页地址发生变化，已停止自动登录')
        driver.perform(lambda:phone.fill(account));driver.perform(lambda:pwd.fill(password))
        if phone.input_value()!=account or pwd.input_value()!=password:
            raise StudyError('登录页未能完整填写账号密码，请检查所保存的账号格式和密码长度')
        # Native checkbox state only; never dismiss a verification challenge.
        for selector in ('input[type=checkbox]#select','input[type=checkbox]#zreadAgreement'):
            box=page.locator(selector)
            if box.count() and box.is_visible() and not box.is_checked():driver.perform(lambda:box.check())
        # Wait for the official inline handler's dependencies to initialize.
        page.wait_for_function("typeof loginByPhoneAndPwd==='function' || !!document.querySelector('#loginBtn')?.getAttribute('data-fixture-ready')",timeout=10000)
        if blocked():raise StudyError('登录需要验证码或身份验证，请在课程窗口完成后继续')
        driver.perform(lambda:page.locator('#loginBtn').click(timeout=10000))
        until=time.monotonic()+25
        while time.monotonic()<until:
            driver.checkpoint()
            current=urlsplit(page.url)
            if current.scheme=='https' and (current.hostname or '').endswith('.chaoxing.com') and current.hostname not in ('passport2.chaoxing.com','passport.chaoxing.com','detect.chaoxing.com'):
                settings.success();driver.log('自动登录成功，正在重新核验并恢复当前学习任务');return
            if blocked():raise StudyError('登录需要验证码或身份验证，请在课程窗口完成后继续')
            if page.locator('#show_error:visible, #error:visible, #phoneMsg:visible, #pwdMsg:visible, #err-txt:visible').filter(has_text=re.compile(r'\S')).count():
                raise StudyError('平台未接受自动登录，请核对账号密码或在课程窗口完成验证')
            driver.wait(.25)
        raise StudyError('自动登录未获平台确认，已停止重试，请检查课程窗口')
    finally:
        # Never return credentials or include platform exception details in logs.
        account=password=None
        try:
            if login_url(page.url):page.locator('#pwd').fill('',timeout=1000)
        except Exception:pass


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
