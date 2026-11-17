import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, Mock

from test_study import LINK, FakeBrowser, until
from study_core import StudyError, StudyStore, LoginRequired, protect_local_link
from study_login import LoginSettings, login_url, sign_in, saved_platform_cookies
from study_service import StudyManager


class LoginTests(unittest.TestCase):
    def settings(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        return LoginSettings(StudyStore(Path(folder.name)/'state.sqlite3'))

    def test_encrypted_storage_masked_response_and_delete(self):
        settings=self.settings();result=settings.save('fixture-account','fixture-password',True)
        self.assertTrue(result['enabled']);self.assertTrue(result['configured'])
        self.assertNotIn('fixture-account',json.dumps(result));self.assertNotIn('fixture-password',json.dumps(result))
        with settings.store.connect() as db:raw=db.execute('SELECT payload FROM login_settings').fetchone()[0]
        self.assertNotIn(b'fixture-password',raw);self.assertNotIn(b'fixture-account',raw)
        self.assertEqual(settings.begin(),('fixture-account','fixture-password'))
        settings.save(clear=True)
        self.assertFalse(settings.snapshot()['configured'])
        with self.assertRaises(StudyError):settings.begin()

    def test_disabled_wrong_login_and_cooldown_do_not_loop(self):
        settings=self.settings();settings.save('fixture','password',False)
        with self.assertRaises(StudyError):settings.begin()
        settings.save(enabled=True);settings.begin()
        with self.assertRaisesRegex(StudyError,'上次'):settings.begin()
        settings.save(enabled=True)
        with self.assertRaisesRegex(StudyError,'上次'):settings.begin()
        settings.success()
        with self.assertRaisesRegex(StudyError,'短时间'):settings.begin()
        settings.save(password='changed',enabled=True)
        self.assertEqual(settings.begin(),('fixture','changed'))

    def test_blank_preserves_password_account_change_requires_password(self):
        settings=self.settings();settings.save('fixture','password',True)
        settings.save(enabled=False)
        self.assertEqual(settings._read()['password'],'password')
        with self.assertRaises(StudyError):settings.save('other','',True)
        self.assertEqual(settings._read()['account'],'fixture')

    def test_official_login_url_only(self):
        self.assertTrue(login_url('https://passport2.chaoxing.com/login?refer=anything'))
        for url in ('http://passport2.chaoxing.com/login','https://passport2.chaoxing.com.attacker.test/login','https://user:pass@passport2.chaoxing.com/login','https://passport2.chaoxing.com:123/login','https://passport2.chaoxing.com/verify'):
            self.assertFalse(login_url(url))

    def manager(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        manager=StudyManager(folder.name,lambda:{},lambda p,c:{},browser_factory=FakeBrowser)
        self.addCleanup(manager.close)
        manager.command('load',{'url':LINK});until(lambda:manager.state['status']=='ready')
        return manager

    def test_worker_recovers_login_then_resumes_without_resetting_options(self):
        manager=self.manager();calls=[]
        def run(action,payload):
            calls.append((action,copy.deepcopy(payload)))
            if len(calls)==1:raise LoginRequired()
            manager.update(status='done')
        with patch.object(manager,'run',side_effect=run),patch('study_service.sign_in') as login:
            manager.command('start',{'mute':True,'rate':2,'auto_submit':True})
            until(lambda:manager.state['status']=='done')
        login.assert_called_once();self.assertEqual([i[0] for i in calls],['start','resume'])
        self.assertTrue(calls[1][1]['mute']);self.assertEqual(calls[1][1]['rate'],2)

    def test_login_failure_pauses_without_repeat(self):
        manager=self.manager()
        with patch.object(manager,'run',side_effect=LoginRequired()),patch('study_service.sign_in',side_effect=StudyError('需要验证')) as login:
            manager.command('start');until(lambda:manager.state['status']=='paused')
        login.assert_called_once();self.assertEqual(manager.state['message'],'需要验证');self.assertTrue(manager.commands.empty())

    def test_playlist_recovers_login_at_same_course(self):
        manager=self.manager();manager.playlist.command('current');calls=[]
        def run(action,payload):
            calls.append(manager.state['course_id'])
            if len(calls)==1:raise LoginRequired()
            for task in manager.state['tasks']:task.update(status='done',platform='done',verified=True)
            manager.update(status='done',catalog_complete=True)
        with patch.object(manager,'run',side_effect=run),patch('study_service.sign_in') as login:
            manager.playlist.command('start')
            until(lambda:manager.playlist.snapshot()['status']=='done')
        login.assert_called_once();self.assertEqual(len(set(calls)),1)
        self.assertEqual(manager.playlist.snapshot()['completed'],1)

    def test_pause_during_login_does_not_enqueue_resume(self):
        manager=self.manager()
        def login(*args):manager.command('pause');manager.checkpoint()
        with patch.object(manager,'run',side_effect=LoginRequired()),patch('study_service.sign_in',side_effect=login):
            manager.command('start');until(lambda:manager.state['status']=='paused')
        self.assertTrue(manager.commands.empty())

    def test_login_settings_api_never_returns_secret_and_rejects_cross_origin(self):
        import app
        from fastapi.testclient import TestClient
        manager=self.manager();config=copy.deepcopy(app.DEFAULT_CONFIG);config['access_token']='fixture-token'
        with patch.object(app,'load_config',return_value=config),patch.object(app,'STUDY_MANAGER',manager),TestClient(app.app) as client:
            self.assertEqual(client.get('/api/study/login-settings').status_code,401)
            headers={'X-Access-Token':'fixture-token'}
            payload={'account':'fixture-account','password':'fixture-password','enabled':True}
            response=client.post('/api/study/login-settings',json=payload,headers=headers)
            self.assertEqual(response.status_code,200)
            for response in (response,client.get('/api/study/login-settings',headers=headers)):
                self.assertNotIn('fixture-password',response.text);self.assertNotIn('fixture-account',response.text)
                self.assertEqual(response.headers['cache-control'],'no-store')
            response=client.post('/api/study/login-settings',json={'clear':True},headers=dict(headers,Origin='https://attacker.test'))
            self.assertEqual(response.status_code,403)
            self.assertTrue(manager.login_settings.snapshot()['configured'])
            response=client.post('/api/study/login-settings',json={'password':'private-value'*100},headers=headers)
            self.assertEqual(response.status_code,409);self.assertNotIn('private-value',response.text)


class NativeLoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import lxt
        from playwright.sync_api import sync_playwright
        cls.runtime=sync_playwright().start()
        cls.browser=lxt.launch_chromium(cls.runtime,headless=True,standard_automation=True)

    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.runtime.stop()

    def test_native_login_success_and_rejection_without_external_requests(self):
        from study_browser import CourseBrowser
        for outcome in ('success','wrong','challenge'):
            with self.subTest(outcome=outcome):
                settings=LoginTests.settings(self)
                settings.save('fixture-account','fixture-password',True)
                context=self.browser.new_context();self.addCleanup(context.close)
                def route(r):
                    if '/login' not in r.request.url:r.fulfill(content_type='text/html',body='<h1>课程目录</h1>');return
                    handler="location.href='https://mooc1.chaoxing.com/mycourse/studentstudy'" if outcome=='success' else "document.querySelector('#err-txt').textContent='账号或密码错误'" if outcome=='wrong' else "document.querySelector('#captcha').style.display='block'"
                    r.fulfill(content_type='text/html',body='<input id="phone"><input id="pwd" type="password"><p id="err-txt"></p><div id="captcha" style="display:none">请验证</div><button id="loginBtn" data-fixture-ready="1" onclick="'+handler+'">登录</button>')
                context.route('**/*',route)
                driver=CourseBrowser(lambda:None,lambda message:None);driver.page=context.new_page();driver.url=LINK
                driver.page.goto('https://passport2.chaoxing.com/login')
                if outcome=='success':
                    sign_in(driver,settings);self.assertFalse(settings.snapshot()['blocked'])
                else:
                    with self.assertRaises(StudyError):sign_in(driver,settings)
                    self.assertTrue(settings.snapshot()['blocked'])
                    self.assertEqual(driver.page.locator('#pwd').input_value(),'')





from study_browser import CourseBrowser

def fixture_cookie():
    return {'name':'fixture','value':'synthetic-login','domain':'.chaoxing.com','path':'/','expires':-1,'secure':True,'httpOnly':True,'sameSite':'Lax'}


class LoginReadTests(unittest.TestCase):
    def test_read_only_backup_filters_domains_and_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'backup.bin'
            cookie=fixture_cookie()
            payload={'version':1,'state':{'cookies':[cookie,dict(cookie,domain='.chaoxing.com.other.test'),dict(cookie,domain='.attacker-chaoxing.com'),dict(cookie,expires=time.time()-1)],'origins':[]}}
            path.write_bytes(protect_local_link(json.dumps(payload).encode('utf-8')))
            before=path.read_bytes()
            self.assertEqual(saved_platform_cookies(path),[cookie])
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(saved_platform_cookies(Path(directory)/'missing.bin'),[])

    def test_permission_failure_uses_backup_without_storage_export(self):
        driver=CourseBrowser(lambda:None,Mock())
        browser=MagicMock()
        with patch('playwright.sync_api.sync_playwright'),patch('lxt.launch_chromium',return_value=browser),patch('lxt.load_cookies_for_browser',side_effect=PermissionError('private-path')),patch('study_login.saved_platform_cookies',return_value=[fixture_cookie()]):
            driver.ensure_session()
            context=browser.new_context.return_value
            context.add_cookies.assert_called_once_with([fixture_cookie()])
            context.storage_state.assert_not_called()
            self.assertEqual(context.new_page.call_count,1)
            self.assertIn('加密登录备份',str(driver.log.call_args))
            self.assertNotIn('private-path',str(driver.log.call_args))
            driver.close()

    def test_unreadable_backup_does_not_block_manual_login_window(self):
        driver=CourseBrowser(lambda:None,Mock());browser=MagicMock()
        with patch('playwright.sync_api.sync_playwright'),patch('lxt.launch_chromium',return_value=browser),patch('lxt.load_cookies_for_browser',side_effect=PermissionError()),patch('study_login.saved_platform_cookies',side_effect=ValueError('private-data')):
            driver.ensure_session(visible=True)
            self.assertIsNotNone(driver.page)
            self.assertIn('手动刷新',str(driver.log.call_args))
            self.assertNotIn('private-data',str(driver.log.call_args))
            driver.close()


if __name__=='__main__':unittest.main()
