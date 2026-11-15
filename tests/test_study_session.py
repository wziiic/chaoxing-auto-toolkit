import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'panel')]
from study_session import BrowserSessionStore, platform_storage
from study_browser import CourseBrowser


def fixture():
    return {'cookies': [{'name': 'fixture', 'value': 'fixture-secret', 'domain': '.chaoxing.com', 'path': '/', 'expires': -1, 'httpOnly': True, 'secure': True, 'sameSite': 'Lax'}],
            'origins': [{'origin': 'https://mooc1.chaoxing.com', 'localStorage': [{'name': 'fixture', 'value': 'fixture-value'}]}]}


class SessionStoreTests(unittest.TestCase):
    def test_encrypted_roundtrip_across_store_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'browser-session.bin'
            store = BrowserSessionStore(path)
            self.assertIsNone(store.load())
            store.save(fixture())
            self.assertNotIn(b'fixture-secret', path.read_bytes())
            self.assertNotIn(b'fixture-value', path.read_bytes())
            self.assertEqual(BrowserSessionStore(path).load(), fixture())
            self.assertEqual(list(path.parent.glob('session-*.tmp')), [])

    def test_only_platform_storage_and_unexpired_cookies_are_kept(self):
        state = fixture()
        state['cookies'] += [dict(state['cookies'][0], domain='.attacker-chaoxing.com'), dict(state['cookies'][0], domain='.chaoxing.com.evil.test'), dict(state['cookies'][0], expires=time.time()-1)]
        state['origins'] += [{'origin':'https://other.test','localStorage':[]}, {'origin':'http://mooc1.chaoxing.com','localStorage':[]}]
        self.assertEqual(platform_storage(state), fixture())

    def test_failed_atomic_replace_preserves_previous_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'browser-session.bin'
            store=BrowserSessionStore(path);store.save(fixture());original=path.read_bytes()
            changed=fixture();changed['cookies'][0]['value']='updated-login'
            with patch('study_session.os.replace', side_effect=OSError('fixture')):
                with self.assertRaises(OSError):store.save(changed)
            self.assertEqual(path.read_bytes(),original)
            self.assertEqual(list(path.parent.glob('session-*.tmp')),[])

    def test_logout_replaces_saved_credentials_with_empty_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store=BrowserSessionStore(Path(directory)/'session.bin');store.save(fixture())
            store.save({'cookies':[],'origins':[]})
            self.assertEqual(store.load(),{'cookies':[],'origins':[]})

    def test_unchanged_capture_does_not_rewrite_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store=BrowserSessionStore(Path(directory)/'session.bin');store.save(fixture())
            with patch('study_session.os.replace') as replace:
                store.save(fixture());replace.assert_not_called()


class BrowserSessionTests(unittest.TestCase):
    def driver(self):
        driver=CourseBrowser(lambda:None,Mock());driver.session_store=Mock()
        driver.context=Mock();driver.context.storage_state.return_value=fixture()
        return driver

    def test_close_saves_before_browser_closes(self):
        driver=self.driver();driver.browser=Mock();sequence=[]
        driver.session_store.save.side_effect=lambda state:sequence.append('save')
        driver.browser.close.side_effect=lambda:sequence.append('close')
        driver.close();self.assertEqual(sequence,['save','close'])

    def test_save_failure_keeps_in_memory_state_and_warns_once(self):
        driver=self.driver();driver.last_storage=fixture()
        driver.context.storage_state.side_effect=RuntimeError('contains-private-data')
        driver.capture_session(force=True);driver.capture_session(force=True)
        driver.session_store.save.assert_not_called();driver.log.assert_called_once()
        self.assertNotIn('contains-private-data',str(driver.log.call_args))
        self.assertEqual(driver.last_storage,fixture())

    def test_periodic_capture_is_throttled_but_dirty_navigation_saves_soon(self):
        driver=self.driver();driver.last_session_save=time.monotonic()
        driver.capture_session();driver.context.storage_state.assert_not_called()
        driver.last_session_save-=3;driver.mark_session_dirty();driver.capture_session()
        driver.session_store.save.assert_called_once()

    def test_paused_browser_pumps_events_and_saves(self):
        driver=self.driver();driver.page=Mock();driver.page.is_closed.return_value=False
        driver.maintain_session()
        driver.page.wait_for_timeout.assert_called_once_with(40)
        driver.session_store.save.assert_called_once()

    def test_restart_prefers_saved_session_over_desktop_import(self):
        driver=CourseBrowser(lambda:None,Mock());driver.session_store=Mock();driver.session_store.load.return_value=fixture()
        browser=MagicMock();browser.new_context.return_value.storage_state.return_value=fixture()
        browser.new_context.return_value.new_page.return_value.is_closed.return_value=False
        with patch('playwright.sync_api.sync_playwright'), patch('lxt.launch_chromium',return_value=browser), patch('lxt.load_cookies_for_browser') as desktop:
            driver.ensure_session()
            desktop.assert_not_called()
            self.assertEqual(browser.new_context.call_args.kwargs['storage_state'],fixture())
            driver.close()

    def test_corrupt_saved_state_falls_back_without_leaking_error(self):
        driver=CourseBrowser(lambda:None,Mock());driver.session_store=Mock();driver.session_store.load.side_effect=ValueError('private-state')
        browser=MagicMock();browser.new_context.return_value.storage_state.return_value=fixture()
        with patch('playwright.sync_api.sync_playwright'), patch('lxt.launch_chromium',return_value=browser), patch('lxt.load_cookies_for_browser',return_value=[]) as desktop:
            driver.ensure_session();desktop.assert_called_once()
            self.assertNotIn('private-state',str(driver.log.call_args_list));driver.close()


if __name__=='__main__':unittest.main()
