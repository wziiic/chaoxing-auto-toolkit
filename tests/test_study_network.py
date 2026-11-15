"""Offline media recovery without touching a real course or network adapter."""
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'panel')]
from study_core import Interrupted, NetworkInterrupted, PlaybackPaused, StudyError
from study_service import StudyManager
from study_browser import CourseBrowser, MEDIA_GUARD_SCRIPT
from test_study import FakeBrowser, LINK, until

OFFLINE={'current':42,'duration':100,'rate':1,'error':2,'ended':False,'paused':True,'ready':0,
         'online':False,'hidden':False,'pause_reason':'unknown','playback_allowed':True}
ENDED={**OFFLINE,'current':100,'error':0,'ready':4,'online':True,'ended':True}


class NetworkQueueTests(unittest.TestCase):
    def manager(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        manager=StudyManager(folder.name,lambda:{},lambda *_:{},browser_factory=FakeBrowser)
        self.addCleanup(manager.close)
        manager.command('load',{'url':LINK});until(lambda:manager.snapshot()['status']=='ready')
        manager.state['tasks'][0].update(kind='video',attempts=1)
        manager.worker_epoch=manager.epoch
        manager.driver.network_available=lambda:True
        manager.driver.reload_media_task=lambda task:None
        return manager,manager.state['tasks'][0]

    def test_offline_waits_and_resumes_same_task_with_saved_playback_options(self):
        manager,task=self.manager()
        with patch.object(manager.driver,'media_state',side_effect=[OFFLINE,OFFLINE,OFFLINE,ENDED]), \
             patch.object(manager.driver,'network_available',side_effect=[False,True,True]) as probe, \
             patch.object(manager.driver,'reload_media_task') as reload, \
             patch.object(manager.driver,'play') as play,patch.object(manager,'verify') as verify:
            manager.media(task,{'rate':1.5,'mute':True})
        reload.assert_called_once_with(task);verify.assert_called_once_with(task)
        self.assertEqual(play.call_count,2)
        self.assertTrue(all(call.kwargs['mute'] for call in play.call_args_list))
        self.assertTrue(all(call.args[1]==1.5 for call in play.call_args_list))
        self.assertEqual(task['attempts'],1)
        self.assertEqual(manager.driver.scan_count,1)
        self.assertEqual(manager.driver.submit_count,0)
        self.assertFalse(manager.snapshot()['network_recovering'])

    def test_network_flap_during_reload_stays_in_recovery_without_consuming_attempts(self):
        manager,task=self.manager()
        with patch.object(manager.driver,'media_state',side_effect=[OFFLINE,OFFLINE,StudyError('任务节点已替换或尚未加载'),ENDED]), \
             patch.object(manager.driver,'reload_media_task',side_effect=[NetworkInterrupted('flap'),None]) as reload, \
             patch.object(manager,'verify'):
            manager.media(task,{'rate':1})
        self.assertEqual(reload.call_count,2);self.assertEqual(task['attempts'],1)

    def test_initial_play_network_failure_also_recovers(self):
        manager,task=self.manager()
        with patch.object(manager.driver,'play',side_effect=[NetworkInterrupted('offline'),None]) as play, \
             patch.object(manager.driver,'media_state',side_effect=[OFFLINE,ENDED]), \
             patch.object(manager,'verify'):
            manager.media(task,{'rate':1})
        self.assertEqual(play.call_count,2)

    def test_recovered_connection_with_stale_offline_pause_is_recognized(self):
        manager,task=self.manager()
        stale={**OFFLINE,'error':0,'online':True,'offline_since_play':True}
        with patch.object(manager.driver,'media_state',side_effect=[stale,stale,ENDED]), \
             patch.object(manager.driver,'reload_media_task') as reload,patch.object(manager,'verify'):
            manager.media(task,{'rate':1})
        reload.assert_called_once_with(task)

    def test_user_control_and_platform_restrictions_prevent_auto_recovery(self):
        for extra in ({'pause_reason':'user_control'},{'pause_reason_since_play':'user_control'},
                      {'hidden':True},{'job_limit':True},{'time_limit':True},{'pause_reason':'pointer_left_window'}):
            with self.subTest(extra=extra):
                manager,task=self.manager()
                with patch.object(manager.driver,'media_state',return_value={**OFFLINE,**extra}), \
                     patch.object(manager.driver,'reload_media_task') as reload:
                    with self.assertRaises(PlaybackPaused):manager.media(task,{'rate':1})
                    reload.assert_not_called()

    def test_native_user_pause_while_waiting_cancels_auto_recovery(self):
        manager,task=self.manager()
        with patch.object(manager.driver,'media_state',side_effect=[OFFLINE,{**OFFLINE,'pause_reason_since_play':'user_control'}]), \
             patch.object(manager.driver,'reload_media_task') as reload:
            with self.assertRaises(PlaybackPaused):manager.media(task,{'rate':1})
            reload.assert_not_called()

    def test_pause_and_stop_cancel_reconnect_even_when_network_returns(self):
        for action in ('pause','stop'):
            with self.subTest(action=action):
                manager,task=self.manager();waiting=threading.Event()
                def probe():waiting.set();return False
                with patch.object(manager.driver,'media_state',return_value=OFFLINE), \
                     patch.object(manager.driver,'network_available',side_effect=probe), \
                     patch.object(manager.driver,'reload_media_task') as reload:
                    manager.command('start')
                    self.assertTrue(waiting.wait(3))
                    self.assertTrue(manager.snapshot()['network_recovering'])
                    manager.command(action)
                    until(lambda:manager.snapshot()['status']==('paused' if action=='pause' else 'stopped'))
                    self.assertFalse(manager.snapshot()['network_recovering']);reload.assert_not_called()

    def test_decode_error_is_not_treated_as_network_interruption(self):
        manager,task=self.manager()
        with patch.object(manager.driver,'media_state',return_value={**OFFLINE,'online':True,'error':3}), \
             patch.object(manager.driver,'reload_media_task') as reload:
            with self.assertRaisesRegex(StudyError,'媒体错误'):manager.media(task,{'rate':1})
            reload.assert_not_called()


class NetworkBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import lxt
        from playwright.sync_api import sync_playwright
        cls.runtime=sync_playwright().start()
        cls.browser=lxt.launch_chromium(cls.runtime,headless=True,standard_automation=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop()

    def test_browser_offline_events_probe_and_current_task_reload(self):
        context=self.browser.new_context();self.addCleanup(context.close)
        context.add_init_script(MEDIA_GUARD_SCRIPT)
        html='''<html><body><div id="coursetree"><div data-chapter-id="101">Fixture chapter</div></div>
        <div data-study-task-id="network-video" data-study-kind="video" data-platform-state="todo"><video controls width="320" height="180"></video></div>
        <script>document.querySelector('video').play=()=>Promise.resolve()</script></body></html>'''
        context.route('**/*',lambda route:route.fulfill(content_type='text/html',body=html))
        page=context.new_page();page.goto(LINK)
        driver=CourseBrowser(lambda:None,lambda _:None);driver.page=page;driver.context=context;driver.url=LINK
        task={'local_id':'network-video','id':'network-video','chapter_id':'101'}
        driver.play(task,1,mute=True)
        self.assertTrue(driver.network_available())
        context.set_offline(True);page.wait_for_function('!navigator.onLine')
        self.assertFalse(driver.network_available())
        state=driver.media_state(task)
        self.assertFalse(state['online']);self.assertTrue(state['offline_since_play'])
        context.set_offline(False);page.wait_for_function('navigator.onLine')
        self.assertTrue(driver.network_available())
        driver.reload_media_task(task)
        self.assertEqual(driver.platform(task),'todo')
        self.assertFalse(driver.media_state(task)['offline_since_play'])


if __name__=='__main__':unittest.main()
