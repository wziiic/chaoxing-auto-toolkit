from pathlib import Path
import sys
import tempfile
import json
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'panel')]
from study_core import protect_local_link
from study_login import saved_platform_cookies
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
