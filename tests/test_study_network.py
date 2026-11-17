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

    def test_visible_pointer_pause_tries_native_play_once(self):
        manager,task=self.manager()
        paused={**OFFLINE,'online':True,'error':0,'ready':4,'focused':True,'pause_reason':'pointer_left_window'}
        with patch.object(manager.driver,'media_state',side_effect=[paused,paused,ENDED]), \
             patch.object(manager.driver,'play') as play,patch.object(manager,'verify'):
            manager.watch_media(task,{'rate':2,'mute':True})
        self.assertEqual(play.call_count,2)
        self.assertTrue(all(call.args[1]==2 and call.kwargs['mute'] for call in play.call_args_list))
        self.assertEqual(manager.driver.submit_count,0)

    def test_repeated_pointer_pause_does_not_loop(self):
        manager,task=self.manager()
        paused={**OFFLINE,'online':True,'error':0,'ready':4,'focused':True,'pause_reason':'pointer_left_window'}
        with patch.object(manager.driver,'media_state',return_value=paused),patch.object(manager.driver,'play') as play:
            with self.assertRaises(PlaybackPaused):manager.watch_media(task,{'rate':1})
        self.assertEqual(play.call_count,2)

    def test_pointer_recovery_requires_real_focus_and_authorized_player(self):
        for extra in ({'focused':False},{'hidden':True},{'job_limit':True},{'time_limit':True},
                      {'playback_allowed':False},{'pause_reason':'user_control'}):
            manager,task=self.manager()
            paused={**OFFLINE,'online':True,'error':0,'ready':4,'focused':True,'pause_reason':'pointer_left_window',**extra}
            with patch.object(manager.driver,'media_state',return_value=paused),patch.object(manager.driver,'play') as play:
                with self.assertRaises(PlaybackPaused):manager.watch_media(task,{'rate':1})
            self.assertEqual(play.call_count,1)

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

    def test_suspended_media_reconnects_even_without_browser_offline_event(self):
        manager,task=self.manager()
        paused={**OFFLINE,'error':0,'online':True}
        with patch('study_service.wall_time',side_effect=[0,200]), \
             patch.object(manager.driver,'media_state',return_value=paused):
            with self.assertRaises(NetworkInterrupted):manager.watch_media(task,{'rate':1})

    def test_suspend_does_not_override_native_user_pause(self):
        manager,task=self.manager()
        with patch('study_service.wall_time',side_effect=[0,200]), \
             patch.object(manager.driver,'media_state',return_value={**OFFLINE,'pause_reason_since_play':'user_control'}):
            with self.assertRaises(PlaybackPaused):manager.watch_media(task,{'rate':1})

    def test_transient_model_tls_failure_retries_generation_only(self):
        from requests.exceptions import SSLError
        manager,task=self.manager()
        with patch.object(manager,'call_model',side_effect=[SSLError('EOF occurred in violation of protocol'),{'text':'回答'}]) as model, \
             patch.object(manager,'wait'):
            self.assertEqual(manager.request_model('题目'),{'text':'回答'})
        self.assertEqual(model.call_count,2)
        self.assertEqual(manager.driver.submit_count,0)
        self.assertEqual(task['attempts'],1)

    def test_model_certificate_and_config_errors_do_not_retry(self):
        from requests.exceptions import SSLError
        for error in (SSLError('CERTIFICATE_VERIFY_FAILED'),RuntimeError('missing API key')):
            with self.subTest(error=type(error).__name__):
                manager,task=self.manager()
                with patch.object(manager,'call_model',side_effect=error) as model:
                    with self.assertRaises(StudyError):manager.request_model('题目')
                    self.assertEqual(model.call_count,1)

    def test_model_network_retries_are_bounded(self):
        from requests.exceptions import ConnectionError
        manager,task=self.manager()
        with patch.object(manager,'call_model',side_effect=ConnectionError('offline')) as model, \
             patch.object(manager,'wait'):
            with self.assertRaises(StudyError):manager.request_model('题目')
        self.assertEqual(model.call_count,6)
        self.assertEqual(manager.driver.submit_count,0)

    def test_pause_during_model_retry_cancels_generation(self):
        from requests.exceptions import ConnectionError
        manager,task=self.manager()
        def cancel_wait(seconds):
            if seconds>=2:
                manager.epoch+=1
            manager.checkpoint()
        with patch.object(manager,'call_model',side_effect=ConnectionError('offline')) as model, \
             patch.object(manager,'wait',side_effect=cancel_wait):
            with self.assertRaises(Interrupted):manager.request_model('题目')
        self.assertEqual(model.call_count,1)

    def test_discussion_late_topic_waits_without_reloading(self):
        manager,task=self.manager()
        with patch.object(manager.driver,'discussion_topic',create=True,side_effect=['','真实讨论题目']), \
             patch.object(manager.driver,'reload_current_task',create=True) as reload:
            self.assertEqual(manager.await_discussion_topic(task),'真实讨论题目')
        reload.assert_not_called()

    def test_discussion_offline_empty_topic_reloads_only_current_task(self):
        manager,task=self.manager()
        task['kind']='discussion'
        with patch.object(manager.driver,'discussion_topic',create=True,side_effect=['']*20+['真实讨论题目']), \
             patch.object(manager.driver,'network_available',side_effect=[False,True,True]), \
             patch.object(manager.driver,'reload_current_task',create=True,side_effect=[NetworkInterrupted('flap'),None]) as reload:
            self.assertEqual(manager.await_discussion_topic(task),'真实讨论题目')
        self.assertEqual(reload.call_count,2)
        self.assertEqual(task['attempts'],1)
        self.assertEqual(manager.driver.scan_count,1)
        self.assertEqual(manager.driver.submit_count,0)
        self.assertFalse(manager.snapshot()['network_recovering'])

    def test_discussion_still_empty_after_reload_never_generates(self):
        manager,task=self.manager()
        with patch.object(manager.driver,'discussion_topic',create=True,return_value=''), \
             patch.object(manager.driver,'reload_current_task',create=True) as reload, \
             patch.object(manager,'request_model') as model:
            with self.assertRaisesRegex(StudyError,'未读取到讨论题目'):manager.generate_discussion(task)
        reload.assert_called_once_with(task);model.assert_not_called()

    def test_discussion_already_done_after_reload_skips_generation(self):
        manager,task=self.manager()
        with patch.object(manager.driver,'discussion_topic',create=True,return_value=''), \
             patch.object(manager.driver,'reload_current_task',create=True), \
             patch.object(manager.driver,'platform',return_value='done'), \
             patch.object(manager,'request_model') as model:
            self.assertEqual(manager.generate_discussion(task),'')
        self.assertEqual(task['status'],'done');model.assert_not_called()

    def test_discussion_reconnect_respects_pause_and_clears_marker(self):
        manager,task=self.manager()
        def cancel_wait(seconds):
            if seconds>=5:manager.epoch+=1
            manager.checkpoint()
        with patch.object(manager.driver,'discussion_topic',create=True,return_value=''), \
             patch.object(manager.driver,'reload_current_task',create=True) as reload, \
             patch.object(manager.driver,'network_available',return_value=False), \
             patch.object(manager.driver,'wait',side_effect=cancel_wait):
            with self.assertRaises(Interrupted):manager.await_discussion_topic(task)
        reload.assert_not_called();self.assertFalse(manager.snapshot()['network_recovering'])

    def test_discussion_missing_frame_waits_but_login_error_is_not_retried(self):
        manager,task=self.manager()
        with patch.object(manager.driver,'discussion_topic',create=True,side_effect=[StudyError('任务节点已替换或尚未加载'),'题目']):
            self.assertEqual(manager.await_discussion_topic(task),'题目')
        with patch.object(manager.driver,'discussion_topic',create=True,side_effect=StudyError('需要登录')) as topic:
            with self.assertRaisesRegex(StudyError,'需要登录'):manager.await_discussion_topic(task)
            self.assertEqual(topic.call_count,1)

    def test_delayed_media_platform_waits_before_declaring_unknown(self):
        manager,task=self.manager()
        with patch.object(manager.driver,'platform',side_effect=['unknown','todo']), \
             patch.object(manager.driver,'reload_current_task',create=True) as reload:
            self.assertEqual(manager.await_media_platform(task),'todo')
        reload.assert_not_called()

    def test_media_unknown_state_reloads_once_and_never_guesses(self):
        manager,task=self.manager()
        with patch.object(manager.driver,'platform',return_value='unknown'), \
             patch.object(manager.driver,'reload_current_task',create=True) as reload:
            self.assertEqual(manager.await_media_platform(task),'unknown')
        reload.assert_called_once_with(task)
        self.assertEqual(manager.driver.play_count,0)
        self.assertFalse(manager.snapshot()['network_recovering'])


class PowerTests(unittest.TestCase):
    def test_navigation_retries_only_after_observed_network_change(self):
        import time
        driver=CourseBrowser(lambda:None,lambda _:None)
        def incomplete(_):
            driver.last_network_change=time.monotonic()
            raise StudyError('课程目录加载超时，页面尚未就绪')
        with patch.object(driver,'_goto_once',side_effect=[None]):
            driver.goto(LINK)
        with patch.object(driver,'_goto_once',side_effect=[StudyError('课程目录加载超时'),None]) as navigate,patch.object(driver,'wait'):
            with self.assertRaises(StudyError):driver.goto(LINK)
            self.assertEqual(navigate.call_count,1)
        calls=[]
        def navigation(url):
            calls.append(url)
            if len(calls)==1:incomplete(url)
        with patch.object(driver,'_goto_once',side_effect=navigation),patch.object(driver,'wait'):
            driver.goto(LINK)
        self.assertEqual(calls,[LINK,LINK])

    def test_navigation_change_retry_is_bounded_and_login_redirect_is_not_retried(self):
        driver=CourseBrowser(lambda:None,lambda _:None)
        for message,count in [('net::ERR_NETWORK_CHANGED',2),('课程页面跳转到了其他课程或登录页面',1)]:
            with patch.object(driver,'_goto_once',side_effect=StudyError(message)) as navigate,patch.object(driver,'wait'):
                with self.assertRaises(StudyError):driver.goto(LINK)
                self.assertEqual(navigate.call_count,count)

    def test_transport_diagnostic_keeps_error_but_hides_signed_url(self):
        from types import SimpleNamespace
        driver=CourseBrowser(lambda:None,lambda _:None)
        with patch.object(driver,'log') as log:
            driver.request_failed(SimpleNamespace(url='https://mooc1.chaoxing.com/private?enc=secret',resource_type='script',failure='net::ERR_NETWORK_CHANGED'))
            message=log.call_args.args[0]
            self.assertIn('ERR_NETWORK_CHANGED',message)
            self.assertIn('script',message)
            self.assertNotIn('secret',message)
            self.assertNotIn('/private',message)
            driver.request_failed(SimpleNamespace(url='https://mooc1.chaoxing.com/',resource_type='script',failure='net::ERR_NETWORK_CHANGED'))
            self.assertEqual(log.call_count,1)
            log.reset_mock()
            driver.request_failed(SimpleNamespace(url='https://mooc1.chaoxing.com/',resource_type='document',failure='net::ERR_ABORTED'))
            log.assert_not_called()

    def test_awake_request_restores_previous_flags_on_failure(self):
        from study_power import learning_awake
        with patch('study_power.os.name','nt'), \
             patch('study_power.ctypes.windll.kernel32.SetThreadExecutionState',return_value=0x80000000) as setter:
            with self.assertRaises(ValueError):
                with learning_awake(lambda _:None):raise ValueError('cancel')
            self.assertEqual([c.args[0] for c in setter.call_args_list],[0x80000001,0x80000000])


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

    def test_startup_failures_preserve_native_reason(self):
        for name,expected,exception in (
            ('NotAllowedError','浏览器阻止',StudyError),
            ('StartupTimeout','媒体资源尚未就绪',StudyError),
            ('NetworkError','网络请求失败',NetworkInterrupted),
            ('NotSupportedError','NotSupportedError',StudyError),
        ):
            with self.subTest(name=name):
                context=self.browser.new_context()
                try:
                    context.route('**/*',lambda route:route.fulfill(content_type='text/html',body='<div data-study-task-id="fixture"><video controls width="320" height="180"></video></div>'))
                    page=context.new_page();page.goto(LINK)
                    page.evaluate('(name)=>document.querySelector("video").play=()=>Promise.reject(new DOMException("fixture",name))',name)
                    driver=CourseBrowser(lambda:None,lambda _:None);driver.page=page;driver.context=context;driver.url=LINK
                    with self.assertRaisesRegex(exception,expected):driver.play({'local_id':'fixture'},1,mute=True)
                finally:context.close()


if __name__=='__main__':unittest.main()
