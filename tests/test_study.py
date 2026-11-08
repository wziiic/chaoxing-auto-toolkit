import copy
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "panel")]

from study_core import CourseLock, Interrupted, PlaybackPaused, StudyError, StudyStore, course_link, digest, normalize_answers, question_signature, redact
from study_browser import CourseBrowser, DOM_SCRIPT, MEDIA_GUARD_SCRIPT
from study_service import StudyManager


QUESTION = {"qid": "q1", "type": "单选题", "stem": "选择数字二", "options": [{"data": "A", "content": "一"}, {"data": "B", "content": "二"}], "blanks": 0, "images": False}
LINK = "https://mooc1.chaoxing.com/mycourse/studentstudy?courseId=111&clazzid=222&chapterId=101&enc=secret&cpi=hidden"


class CoreTests(unittest.TestCase):
    def test_read_browser_is_headless_and_explicit_show_preserves_session(self):
        driver=CourseBrowser(lambda:None,lambda message:None)
        runtime=MagicMock()
        browsers=[MagicMock(),MagicMock(),MagicMock()]
        stored={'cookies':[],'origins':[{'origin':'https://mooc1.chaoxing.com','localStorage':[]}]}
        for browser in browsers:
            browser.new_context.return_value.storage_state.return_value=stored
            browser.new_context.return_value.new_page.return_value.is_closed.return_value=False
        with patch('playwright.sync_api.sync_playwright') as runtime_factory,patch('lxt.launch_chromium',side_effect=browsers) as launch,patch('lxt.load_cookies_for_browser',return_value=[]):
            runtime_factory.return_value.start.return_value=runtime
            driver.ensure_session()
            driver.ensure_session()
            self.assertEqual(launch.call_count,1)
            self.assertTrue(launch.call_args.kwargs['headless'])
            driver.ensure_session(visible=True)
            self.assertFalse(launch.call_args.kwargs['headless'])
            self.assertEqual(browsers[1].new_context.call_args.kwargs['storage_state'],stored)
            browsers[0].close.assert_called_once()
            driver.background()
            self.assertTrue(launch.call_args.kwargs['headless'])
            self.assertEqual(browsers[2].new_context.call_args.kwargs['storage_state'],stored)
            browsers[1].close.assert_called_once()
            browsers[2].new_context.return_value.new_cdp_session.assert_not_called()
            driver.background()
            self.assertEqual(launch.call_count,3)
            driver.close()

    def test_directory_progress_requires_explicit_valid_counts(self):
        from study_directory import progress_state, platform_url
        self.assertEqual(progress_state('已完成任务点: 21/25')['platform'], 'todo')
        self.assertEqual(progress_state('已完成任务点：25/25')['platform'], 'done')
        for text in ('没有任务', '已完成任务点: 0/0', '已完成任务点: 3/2', '已完成任务点: 1/2 已完成任务点: 2/3'):
            self.assertEqual(progress_state(text)['platform'], 'unknown')
        self.assertFalse(platform_url('https://chaoxing.com.attacker.example/visit/courses/list'))

    def test_link_and_redaction(self):
        key, public = course_link(LINK)
        self.assertNotIn("secret", public)
        self.assertNotIn("cpi", public)
        self.assertEqual(key, course_link(LINK.replace("secret", "new-signature"))[0])
        for link in (LINK.replace("mooc1.chaoxing.com", "localhost"), LINK.replace("https", "http"), LINK.replace("mooc1.chaoxing.com", "chaoxing.com.attacker.example")):
            with self.assertRaises(StudyError):
                course_link(link)
        self.assertNotIn("secret", redact(LINK + " token=secret"))

    def test_answers_are_exact_not_guessed(self):
        self.assertEqual(normalize_answers([QUESTION], {"q1": "B"}), {"q1": ["B"]})
        with self.assertRaises(StudyError):
            normalize_answers([QUESTION], {"q1": "答案是B，置信度99%"})
        changed = copy.deepcopy(QUESTION)
        changed["options"].reverse()
        self.assertEqual(question_signature(QUESTION), question_signature(changed))
        changed["stem"] = "另一题"
        self.assertNotEqual(question_signature(QUESTION), question_signature(changed))

    def test_store_and_cross_instance_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            store = StudyStore(Path(folder) / "state.sqlite3")
            store.submission("course", "task", "question", "intent")
            self.assertEqual(StudyStore(Path(folder) / "state.sqlite3").submission("course", "task", "question"), "intent")
            first, second = CourseLock(folder, "same"), CourseLock(folder, "same")
            first.acquire()
            try:
                with self.assertRaises(StudyError):
                    second.acquire()
            finally:
                first.close()
            second.acquire()
            second.close()


class FakeBrowser:
    def __init__(self, checkpoint, log):
        self.checkpoint = checkpoint
        self.fill_count = 0
        self.submit_count = 0
        self.play_count = 0
        self.verify_count = 0
        self.kind = "quiz"
        self.scan_count = 0
        self.recover_count = 0
        self.page = self
        self.complete = False

    def start(self, url):
        pass

    def show_window(self):
        pass

    def recover_link(self, public_url):
        self.recover_count += 1
        return LINK

    def scan(self, course, report):
        self.scan_count += 1
        return [{"id": "101", "title": "Fixture chapter", "parent_id": ""}], [{"id": "task", "local_id": "local", "chapter_id": "101", "title": "Fixture", "kind": self.kind, "platform": "todo", "status": "pending", "reason": "", "url": "", "progress": {}, "attempts": 0, "verified": False, "submission": "", "is_task_point": True}], False

    def pause_media(self):
        pass

    def activate(self, task):
        self.checkpoint()

    def optional_resource(self, task):
        return False

    def platform(self, task):
        self.verify_count += 1
        return "done" if self.complete else "todo"

    def questions(self, task):
        return self, {"questions": [QUESTION], "formal_exam": False, "restricted": False}

    def fill(self, task, questions, answers):
        self.checkpoint()
        self.fill_count += 1

    def submit(self, task, questions=None, allow_restricted=False):
        self.checkpoint()
        self.submit_count += 1

    def feedback(self, task, frame):
        return ""

    def find(self, task):
        return [], [(self, {"popup": False, "questions": []})], 0

    def popup(self, task):
        return False

    def play(self, task, rate):
        self.checkpoint()
        self.play_count += 1

    def media_state(self, task):
        return {"current": 10, "duration": 10, "rate": 1, "error": 0, "ended": True, "paused": True, "ready": 4}

    def wait(self, seconds):
        self.checkpoint()

    def close(self):
        pass


class LazyFakeBrowser(FakeBrowser):
    def __init__(self, checkpoint, log):
        super().__init__(checkpoint,log)
        self.chapter_calls=[]
        self.completed=set()
        self.events=[]
        self.catalogue_count=0

    def catalogue(self):
        self.catalogue_count+=1
        return [{'id':str(i),'title':'Chapter '+str(i),'parent_id':'','locked':False} for i in (101,102,103)],True

    def chapter_tasks(self, chapter):
        ids=['a','b'] if chapter['id']=='101' else ['a']
        return [{'id':chapter['id']+suffix,'local_id':chapter['id']+suffix,'chapter_id':chapter['id'],'title':'Question '+suffix,'kind':'quiz',
                 'platform':'done' if chapter['id']+suffix in self.completed else 'todo',
                 'status':'done' if chapter['id']+suffix in self.completed else 'pending',
                 'verified':chapter['id']+suffix in self.completed,'reason':'','progress':{},'attempts':0,'submission':'','is_task_point':True,'step':None} for suffix in ids]

    def scan_chapter(self, course, chapter):
        self.checkpoint()
        self.chapter_calls.append(chapter['id']);self.events.append('scan:'+chapter['id'])
        return self.chapter_tasks(chapter),True

    def platform(self, task):
        return 'done' if task['id'] in self.completed else 'todo'

    def submit(self, task, questions=None, allow_restricted=False):
        self.checkpoint();self.submit_count+=1;self.completed.add(task['id']);self.events.append('submit:'+task['id'])

    def feedback(self, task, frame):
        return 'submitted' if task['id'] in self.completed else ''


class LazyQueueTests(unittest.TestCase):
    def test_legacy_scrolled_link_can_execute_its_missing_click_once(self):
        manager=self.manager();chapter=manager.state['chapters'][0]
        old=manager.driver.chapter_tasks(chapter)[0]
        old.update(kind='text',action_finished=True,status='verifying')
        manager.state['tasks']=[old]
        fresh={**old,'kind':'link','action_finished':False}
        manager.worker_epoch=manager.epoch
        with patch.object(manager.driver,'scan_chapter',return_value=([fresh],True)):
            task=manager.discover_chapter(chapter)[0]
        self.assertFalse(task['action_finished'])
        task.update(action_finished=True,link_state='intent')
        with patch.object(manager.driver,'scan_chapter',return_value=([{**fresh,'action_finished':False}],True)):
            again=manager.discover_chapter(chapter)[0]
        self.assertTrue(again['action_finished'])
        self.assertEqual(again['link_state'],'intent')

    def manager(self, model=lambda prompt,config:{'q1':'B'}):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        manager=StudyManager(folder.name,lambda:{},model,browser_factory=LazyFakeBrowser)
        self.addCleanup(manager.close)
        manager.command('load',{'url':LINK})
        until(lambda:manager.snapshot()['status']=='ready')
        return manager

    def test_load_reads_directory_only_and_keeps_start_manual(self):
        manager=self.manager()
        self.assertTrue(manager.snapshot()['loaded'])
        self.assertEqual(len(manager.state['chapters']),3)
        self.assertEqual(manager.state['tasks'],[])
        self.assertEqual(manager.driver.chapter_calls,[])
        self.assertEqual(manager.driver.submit_count,0)
        self.assertFalse(manager.state['catalog_complete'])
        self.assertEqual(manager.snapshot()['chapters_checked'],0)

    def test_tasks_execute_before_later_chapters_are_read(self):
        manager=self.manager()
        manager.command('start',{'auto_fill':True,'auto_submit':True})
        until(lambda:manager.snapshot()['status']=='done',timeout=10)
        self.assertEqual(manager.driver.chapter_calls,['101','102','103'])
        self.assertLess(manager.driver.events.index('submit:101b'),manager.driver.events.index('scan:102'))
        self.assertEqual(manager.driver.submit_count,4)
        self.assertTrue(manager.snapshot()['task_discovery_complete'])

    def test_selected_scope_does_not_execute_new_or_other_chapter_tasks(self):
        manager=self.manager()
        manager.state['tasks']=manager.driver.chapter_tasks(manager.state['chapters'][0])
        manager.command('start',{'task_ids':['101a'],'auto_fill':True,'auto_submit':True})
        until(lambda:manager.snapshot()['status']=='needs_attention')
        self.assertEqual(manager.driver.chapter_calls,['101'])
        self.assertEqual(manager.driver.completed,{'101a'})
        self.assertFalse(manager.state['catalog_complete'])

    def test_legacy_selection_is_not_expanded_to_new_tasks(self):
        manager=self.manager()
        manager.state.update(tasks=manager.driver.chapter_tasks(manager.state['chapters'][0])[:1],selection=['101a'])
        manager.state.pop('queue_scope',None)
        manager.persist();manager.close()
        restored=StudyManager(manager.folder,lambda:{},lambda prompt,config:{'q1':'B'},browser_factory=LazyFakeBrowser)
        self.addCleanup(restored.close)
        self.assertEqual(restored.state['queue_scope'],'selected')
        restored.command('resume',{'auto_fill':True,'auto_submit':True})
        until(lambda:restored.snapshot()['status']=='needs_attention',timeout=10)
        self.assertEqual(restored.driver.completed,{'101a'})
        self.assertEqual(restored.driver.chapter_calls,['101'])

    def test_partial_chapter_cannot_report_entire_course_complete(self):
        manager=self.manager()
        scan=manager.driver.scan_chapter
        manager.driver.scan_chapter=lambda course,chapter:(scan(course,chapter)[0],chapter['id']!='101')
        manager.command('start',{'auto_fill':True,'auto_submit':True})
        until(lambda:manager.snapshot()['status']=='needs_attention',timeout=10)
        self.assertEqual(manager.snapshot()['counts']['verified'],4)
        self.assertFalse(manager.state['catalog_complete'])
        self.assertEqual(manager.state['chapter_states']['101'],'partial')

    def test_restart_resumes_from_active_chapter_without_rereading_directory(self):
        manager=self.manager()
        manager.driver.completed.update({'101a','101b'})
        manager.state.update(tasks=manager.driver.chapter_tasks(manager.state['chapters'][0]),chapter_states={'101':'ready'},active_chapter='102',queue_scope='all',selection=['101a','101b'])
        manager.persist();manager.close()
        restored=StudyManager(manager.folder,lambda:{},lambda prompt,config:{'q1':'B'},browser_factory=LazyFakeBrowser)
        self.addCleanup(restored.close)
        restored.driver.completed.update({'101a','101b'})
        self.assertTrue(restored.snapshot()['loaded'])
        self.assertEqual(restored.driver.catalogue_count,0)
        self.assertEqual(restored.driver.chapter_calls,[])
        restored.command('resume',{'auto_fill':True,'auto_submit':True})
        until(lambda:restored.snapshot()['status']=='done',timeout=10)
        self.assertEqual(restored.driver.chapter_calls,['102','103'])

    def test_resume_all_keeps_discovering_beyond_cached_selection(self):
        entered=threading.Event();release=threading.Event();calls=[]
        def model(prompt,config):
            calls.append(1)
            if len(calls)==1:entered.set();release.wait(5)
            return {'q1':'B'}
        manager=self.manager(model)
        manager.state['tasks']=manager.driver.chapter_tasks(manager.state['chapters'][0])[:1]
        manager.command('start',{'auto_fill':True,'auto_submit':True})
        self.assertTrue(entered.wait(3))
        manager.command('pause');until(lambda:manager.snapshot()['status']=='paused')
        self.assertEqual(manager.driver.submit_count,0)
        release.set()
        manager.command('resume',{'auto_fill':True,'auto_submit':True})
        until(lambda:manager.snapshot()['status']=='done',timeout=10)
        self.assertEqual(manager.driver.completed,{'101a','101b','102a','103a'})


def until(check, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for fixture state")


class QueueTests(unittest.TestCase):
    def test_pointer_leave_pause_and_platform_limits_are_specific_and_do_not_replay(self):
        for extra,expected in [({'pause_reason':'pointer_left_window'},'鼠标离开'),({'job_limit':True},'任务数量'),({'time_limit':True},'学习时长')]:
            with self.subTest(extra=extra):
                manager=self.make_manager(lambda prompt,config:{})
                task=manager.state['tasks'][0];task.update(kind='video');manager.worker_epoch=manager.epoch
                state={'current':12,'duration':100,'rate':1,'error':0,'ended':False,'paused':True,'ready':4,'playback_allowed':True,'hidden':False,'focused':True,**extra}
                with patch.object(manager.driver,'media_state',return_value=state):
                    with self.assertRaisesRegex(PlaybackPaused,expected):manager.media(task,{'rate':1})
                self.assertEqual(manager.driver.play_count,1)

    def test_link_click_then_verify_without_reading_or_duplicate_click(self):
        manager=self.make_manager(lambda prompt,config:{})
        task=manager.state['tasks'][0];task.update(kind='link')
        def open_link(task,before_open):before_open();manager.driver.complete=True
        with patch.object(manager.driver,'open_link',create=True,side_effect=open_link) as click:
            manager.command('start')
            until(lambda:manager.snapshot()['status']=='needs_attention')
            self.assertEqual(click.call_count,1)
            manager.command('resume')
            until(lambda:manager.snapshot()['status']=='needs_attention')
            self.assertEqual(click.call_count,1)
        self.assertTrue(task['verified'])
        self.assertEqual(task['link_state'],'opened')
        self.assertEqual(manager.driver.submit_count,0)

    def test_visibility_pause_does_not_consume_retry_limit_or_restart(self):
        manager=self.make_manager(lambda prompt,config:{})
        task=manager.state['tasks'][0];task.update(kind='video',attempts=2)
        paused={'current':12,'duration':100,'rate':1,'error':0,'ended':False,'paused':True,'ready':4,'hidden':True,'focused':False,'pause_reason':'hidden','playback_allowed':True}
        with patch.object(manager.driver,'media_state',return_value=paused):
            manager.command('start',{'manual_policy':'continue'})
            until(lambda:manager.snapshot()['status']=='paused')
        self.assertEqual(task['attempts'],2)
        self.assertEqual(task['status'],'paused')
        self.assertIn('隐藏',task['reason'])
        self.assertEqual(manager.driver.play_count,1)
        self.assertTrue(task['playback_diagnostic']['hidden'])

    def test_transient_pause_rechecks_without_forcing_play(self):
        manager=self.make_manager(lambda prompt,config:{})
        task=manager.state['tasks'][0];task.update(kind='video');manager.worker_epoch=manager.epoch
        state={'current':12,'duration':100,'rate':1,'error':0,'ended':False,'paused':True,'ready':4}
        with patch.object(manager.driver,'media_state',side_effect=[state,{**state,'paused':False},{**state,'paused':False,'ended':True}]),patch.object(manager,'verify',return_value=True):
            manager.media(task,{'rate':1})
        self.assertEqual(manager.driver.play_count,1)

    def test_unknown_script_pause_stays_paused_without_automatic_replay(self):
        manager=self.make_manager(lambda prompt,config:{})
        task=manager.state['tasks'][0];task.update(kind='video');manager.worker_epoch=manager.epoch
        state={'current':12,'duration':100,'rate':1,'error':0,'ended':False,'paused':True,'ready':4,'playback_allowed':True,'hidden':False,'focused':True}
        with patch.object(manager.driver,'media_state',return_value=state):
            with self.assertRaisesRegex(PlaybackPaused,'触发原因'):manager.media(task,{'rate':1})
        self.assertEqual(manager.driver.play_count,1)

    def test_completion_sync_after_reentry_is_observed_without_repeating_action(self):
        manager=self.make_manager(lambda prompt,config:{})
        task=manager.state['tasks'][0];task.update(kind='text',action_finished=True)
        manager.worker_epoch=manager.epoch
        samples=iter(['todo']*6+['done'])
        with patch.object(manager.driver,'platform',side_effect=lambda task:next(samples)) as platform,patch.object(manager.driver,'activate') as activate,patch.object(manager.driver,'wait') as wait:
            self.assertTrue(manager.verify(task))
        activate.assert_called_once_with(task)
        self.assertEqual(platform.call_count,7)
        self.assertEqual([call.args[0] for call in wait.call_args_list],[1,2,4,8,1])
        self.assertTrue(task['verified'])
        self.assertEqual(manager.driver.submit_count,0)
        self.assertEqual(manager.driver.play_count,0)

    def test_unconfirmed_completion_has_bounded_wait_and_only_one_reentry(self):
        manager=self.make_manager(lambda prompt,config:{})
        task=manager.state['tasks'][0];manager.worker_epoch=manager.epoch
        with patch.object(manager.driver,'platform',return_value='todo'),patch.object(manager.driver,'activate') as activate,patch.object(manager.driver,'wait') as wait:
            self.assertFalse(manager.verify(task))
        activate.assert_called_once_with(task)
        self.assertEqual(sum(call.args[0] for call in wait.call_args_list),30)
        self.assertEqual(task['status'],'verifying')
        self.assertFalse(task['verified'])

    def test_pause_cancels_post_reentry_completion_polling(self):
        manager=self.make_manager(lambda prompt,config:{})
        task=manager.state['tasks'][0];manager.worker_epoch=manager.epoch
        def reenter(task):manager.epoch+=1
        with patch.object(manager.driver,'platform',return_value='todo'),patch.object(manager.driver,'activate',side_effect=reenter),patch.object(manager.driver,'wait'):
            with self.assertRaises(Interrupted):manager.verify(task)
        self.assertFalse(task['verified'])

    def test_cached_unknown_image_reference_continues_to_next_task(self):
        manager=self.make_manager(lambda prompt,config:{})
        first=manager.state['tasks'][0];first.update(kind='unknown',platform='unknown')
        second=copy.deepcopy(first);second.update(id='following-quiz',kind='quiz')
        manager.state['tasks'].append(second)
        with patch.object(manager.driver,'optional_resource',side_effect=lambda task:task['id']==first['id']),patch.object(manager.driver,'platform',return_value='unknown'):
            manager.command('start',{'auto_fill':False})
            until(lambda:manager.snapshot()['status']=='paused')
        self.assertEqual(first['status'],'reference')
        self.assertFalse(first['verified'])
        self.assertFalse(first['is_task_point'])
        self.assertEqual(manager.state['current'],'following-quiz')

    def test_optional_news_video_does_not_block_or_play(self):
        manager=self.make_manager(lambda prompt,config:{})
        first=manager.state['tasks'][0];first.update(kind='video',platform='unknown')
        second=copy.deepcopy(first);second.update(id='following-quiz',kind='quiz')
        manager.state['tasks'].append(second)
        with patch.object(manager.driver,'optional_resource',return_value=True),patch.object(manager.driver,'platform',return_value='unknown'):
            manager.command('start',{'auto_fill':False})
            until(lambda:manager.snapshot()['status']=='paused')
        self.assertEqual(first['status'],'reference')
        self.assertFalse(first['is_task_point'])
        self.assertFalse(first['verified'])
        self.assertEqual(manager.driver.play_count,0)
        self.assertEqual(manager.state['current'],'following-quiz')

    def test_interrupted_load_recovers_chapters_from_retained_tasks(self):
        manager=self.make_manager(lambda prompt,config:{})
        manager.state['chapters']=[]
        manager.store.save(manager.state)
        manager.close()
        restored=StudyManager(manager.folder,lambda:{},lambda prompt,config:{},browser_factory=FakeBrowser)
        self.addCleanup(restored.close)
        self.assertTrue(restored.snapshot()['loaded'])
        self.assertEqual(restored.state['chapters'][0]['id'],'101')
        self.assertFalse(restored.state['catalog_complete'])
        self.assertEqual(restored.driver.scan_count,0)
        restored.command('start',{'auto_fill':False})
        until(lambda:restored.snapshot()['status']=='paused')
        self.assertIn('自动填写未开启',restored.state['tasks'][0]['reason'])

    def test_cached_plain_text_reference_does_not_pause_before_next_task(self):
        manager=self.make_manager(lambda prompt,config:{})
        first=manager.state['tasks'][0]
        second=copy.deepcopy(first);second.update(id='next-quiz')
        first.update(kind='unknown',local_id=None,is_task_point=False,platform='unknown')
        manager.state['tasks'].append(second)
        with patch.object(manager.driver,'inspect_reference',create=True,return_value=True) as inspect:
            manager.command('start',{'auto_fill':False})
            until(lambda:manager.snapshot()['status']=='paused')
        inspect.assert_called_once()
        self.assertEqual(first['status'],'reference')
        self.assertFalse(first['verified'])
        self.assertEqual(manager.snapshot()['current'],'next-quiz')

    def test_unresolved_chapter_still_pauses_when_reference_not_confirmed(self):
        manager=self.make_manager(lambda prompt,config:{})
        first=manager.state['tasks'][0]
        first.update(kind='unknown',local_id=None,is_task_point=False,platform='unknown')
        with patch.object(manager.driver,'inspect_reference',create=True,return_value=False):
            manager.command('start')
            until(lambda:manager.snapshot()['status']=='paused')
        self.assertEqual(first['status'],'manual')

    def test_document_scrolls_before_completion_and_advances_once(self):
        manager=self.make_manager(lambda prompt,config:{})
        first=manager.state['tasks'][0]
        first.update(kind='document')
        second=copy.deepcopy(first);second.update(id='second-document',local_id='second-document')
        manager.state['tasks'].append(second)
        calls=[]
        with patch.object(manager.driver,'read_step',create=True,side_effect=lambda task,**kw:calls.append(task['id']) or False), patch.object(manager.driver,'platform',return_value='done'):
            manager.command('start')
            until(lambda:manager.snapshot()['status']=='needs_attention')
        self.assertEqual(calls,[first['id'],first['id'],'second-document','second-document'])
        self.assertTrue(all(t['verified'] for t in manager.state['tasks']))

    def test_document_bottom_without_platform_confirmation_is_not_done(self):
        manager=self.make_manager(lambda prompt,config:{})
        task=manager.state['tasks'][0];task.update(kind='document')
        with patch.object(manager.driver,'read_step',create=True,return_value=False):
            manager.command('start')
            until(lambda:manager.snapshot()['status']=='paused')
        self.assertFalse(task['verified'])
        self.assertEqual(task['status'],'verifying')

    def test_discussion_publish_is_explicit_and_prevents_repeat(self):
        manager=self.make_manager(lambda prompt,config:{})
        task=manager.state['tasks'][0]
        task.update(kind='discussion',is_task_point=False,auto_discussion=True,discussion_draft='Synthetic discussion text')
        def publish(driver,task,text,enabled,before):
            self.assertTrue(enabled)
            before()
            return {'state':'published','reason':'Fixture publication verified'}
        with patch('study_discussion.discussion',side_effect=publish) as handler:
            manager.command('start')
            until(lambda:manager.snapshot()['status']=='needs_attention')
            self.assertEqual(task['discussion_state'],'published')
            self.assertEqual(manager.snapshot()['counts']['discovered'],0)
            manager.command('start')
            until(lambda:manager.snapshot()['status']=='needs_attention')
            self.assertEqual(handler.call_count,1)
        self.assertEqual(manager.driver.submit_count,0)

    def test_legacy_cache_recovers_only_course_link_without_full_scan(self):
        manager=self.make_manager(lambda prompt,config:{'q1':'B'})
        with manager.store.connect() as connection:
            connection.execute('DELETE FROM course_links')
        folder=manager.folder
        manager.close()
        restored=StudyManager(folder,lambda:{},lambda prompt,config:{'q1':'B'},browser_factory=FakeBrowser)
        self.addCleanup(restored.close)
        self.assertTrue(restored.snapshot()['loaded'])
        self.assertEqual(restored.driver.recover_count,0)
        self.assertEqual(restored.driver.scan_count,0)
        restored.command('start',{'auto_fill':False})
        until(lambda:restored.snapshot()['status']=='paused')
        self.assertEqual(restored.driver.recover_count,1)
        self.assertEqual(restored.driver.scan_count,0)
        self.assertEqual(restored.store.saved_link(restored.state['course_id']),LINK)

    def test_restart_preserves_link_settings_selection_and_counts_without_scan(self):
        manager=self.make_manager(lambda prompt,config:{'q1':'B'})
        task=manager.state['tasks'][0]
        task.update(platform='done',status='done',verified=False)
        remaining=copy.deepcopy(task)
        remaining.update(id='remaining-task',platform='todo',status='pending',verified=False)
        manager.state['tasks'].append(remaining)
        manager.command('settings',{'auto_fill':True,'auto_submit':True,'rate':1.5,'task_ids':[task['id'],remaining['id']]})
        folder=manager.folder
        course=manager.state['course_id']
        self.assertNotIn('secret',json.dumps(manager.store.latest()))
        with manager.store.connect() as connection:
            encrypted=connection.execute('SELECT protected FROM course_links WHERE course=?',(course,)).fetchone()[0]
        self.assertNotIn(b'enc=secret',encrypted)
        manager.close()
        restored=StudyManager(folder,lambda:{},lambda prompt,config:{'q1':'B'},browser_factory=FakeBrowser)
        self.addCleanup(restored.close)
        state=restored.snapshot()
        self.assertTrue(state['loaded'])
        self.assertEqual(state['input_url'],LINK)
        self.assertEqual(state['counts']['verified'],1)
        self.assertEqual(state['selection'],[task['id'],remaining['id']])
        self.assertEqual(state['options']['rate'],1.5)
        self.assertEqual(restored.driver.scan_count,0)
        self.assertEqual(restored.driver.submit_count,0)
        restored.driver.complete=True
        restored.command('resume',state['options'])
        until(lambda:restored.snapshot()['status']=='needs_attention')
        self.assertEqual(restored.driver.verify_count,1)
        self.assertEqual(restored.driver.scan_count,0)
        self.assertEqual(restored.driver.submit_count,0)
        restored.command('refresh')
        until(lambda:restored.snapshot()['status']=='ready')
        self.assertEqual(restored.driver.scan_count,1)

    def seed_intent(self, manager):
        task=manager.state['tasks'][0]
        task.update(submission='intent',status='verifying',attempts=3)
        manager.store.submission(manager.state['course_id'],task['id'],'old-fingerprint','intent')
        manager.persist()
        return task

    def test_explicit_unsubmitted_recovery_rearms_only_after_platform_check(self):
        manager=self.make_manager(lambda prompt,config:{'q1':'B'})
        task=self.seed_intent(manager)
        payload={'task_id':task['id'],'acknowledge_unsubmitted':True}
        with self.assertRaisesRegex(StudyError,'核实'):
            manager.command('reset_submission',{'task_id':task['id']})
        manager.driver.platform=lambda task:'unknown'
        manager.command('reset_submission',payload)
        until(lambda:manager.snapshot()['status']=='paused')
        self.assertTrue(manager.store.pending_intent(manager.state['course_id'],task['id']))
        manager.driver.platform=lambda task:'todo'
        manager.command('reset_submission',payload)
        until(lambda:manager.snapshot()['status']=='ready')
        self.assertFalse(manager.store.pending_intent(manager.state['course_id'],task['id']))
        self.assertEqual(task['attempts'],0)
        self.assertEqual(task['status'],'pending')
        self.assertEqual(manager.driver.submit_count,0)
        manager.command('start',{'task_ids':[task['id']],'auto_fill':True,'auto_submit':True})
        until(lambda:manager.snapshot()['status']=='paused')
        self.assertEqual(manager.driver.fill_count,1)
        self.assertEqual(manager.driver.submit_count,1)

    def test_unsubmitted_recovery_preserves_platform_completed_task(self):
        manager=self.make_manager(lambda prompt,config:{'q1':'B'})
        task=self.seed_intent(manager)
        manager.driver.complete=True
        manager.command('reset_submission',{'task_id':task['id'],'acknowledge_unsubmitted':True})
        until(lambda:manager.snapshot()['status']=='ready')
        self.assertEqual(task['status'],'done')
        self.assertEqual(task['submission'],'verified')
        self.assertEqual(manager.driver.submit_count,0)

    def test_loading_with_saved_auto_settings_does_not_execute(self):
        def forbidden_model(prompt, config):
            raise AssertionError('Reading must not request model answers')
        manager=self.make_manager(forbidden_model)
        manager.state['options']={'auto_fill':True,'auto_submit':True}
        manager.persist()
        manager.command('load',{'url':LINK})
        until(lambda:manager.snapshot()['status']=='ready')
        self.assertTrue(manager.snapshot()['options']['auto_submit'])
        self.assertEqual(manager.driver.play_count,0)
        self.assertEqual(manager.driver.fill_count,0)
        self.assertEqual(manager.driver.submit_count,0)

    def test_directory_is_transient_and_stop_cancels_scan(self):
        manager = self.make_manager(lambda prompt, config: {})
        def discover(publish):
            publish(courses=[{'id':'fixture','url':LINK}], checked=1)
            return {'courses':[{'id':'fixture','url':LINK}]}
        manager.driver.discover_courses = discover
        manager.command('discover')
        until(lambda: manager.directory_snapshot()['status']=='ready')
        self.assertEqual(manager.snapshot()['status'], 'ready')
        self.assertEqual(manager.directory_snapshot()['courses'][0]['url'], LINK)
        self.assertNotIn('secret', json.dumps(manager.store.latest()))
        started=threading.Event()
        def slow(publish):
            started.set()
            while True:
                manager.driver.checkpoint()
                time.sleep(0.01)
        manager.driver.discover_courses=slow
        manager.command('discover')
        self.assertTrue(started.wait(2))
        manager.command('stop')
        until(lambda: manager.snapshot()['status']=='stopped')
        self.assertEqual(manager.directory_snapshot()['status'], 'cancelled')
        self.assertEqual(manager.driver.submit_count, 0)

    def test_selecting_course_cancels_in_progress_directory(self):
        manager = self.make_manager(lambda prompt, config: {})
        started=threading.Event()
        def slow(publish):
            started.set()
            while True:
                manager.driver.checkpoint()
                time.sleep(0.01)
        manager.driver.discover_courses=slow
        manager.command('discover')
        self.assertTrue(started.wait(2))
        manager.command('load', {'url':LINK})
        until(lambda: manager.snapshot()['status']=='ready')
        self.assertEqual(manager.directory_snapshot()['status'], 'cancelled')
        self.assertTrue(manager.snapshot()['loaded'])

    def make_manager(self, model):
        folder = tempfile.TemporaryDirectory()
        manager = StudyManager(folder.name, lambda: {}, model, browser_factory=FakeBrowser)
        self.addCleanup(folder.cleanup)
        self.addCleanup(manager.close)
        manager.command("load", {"url": LINK})
        until(lambda: manager.snapshot()["status"] == "ready")
        return manager

    def test_pause_and_stop_discard_late_model_result(self):
        for action in ("pause", "stop"):
            with self.subTest(action=action):
                started, release = threading.Event(), threading.Event()
                def model(prompt, config):
                    started.set()
                    release.wait(4)
                    return {"q1": "B"}
                manager = self.make_manager(model)
                manager.command("start", {"auto_fill": True, "auto_submit": True})
                self.assertTrue(started.wait(2))
                manager.command(action)
                release.set()
                until(lambda: manager.snapshot()["status"] in ("paused", "stopped"))
                self.assertEqual(manager.driver.fill_count, 0)
                self.assertEqual(manager.driver.submit_count, 0)

    def test_ended_does_not_mean_platform_complete_or_replay(self):
        manager = self.make_manager(lambda prompt, config: {})
        manager.state["tasks"][0]["kind"] = "video"
        manager.command("start")
        until(lambda: manager.snapshot()["status"] == "paused")
        self.assertEqual(manager.driver.play_count, 1)
        self.assertEqual(manager.snapshot()["counts"]["verified"], 0)
        manager.command("resume")
        until(lambda: manager.snapshot()["status"] == "paused")
        self.assertEqual(manager.driver.play_count, 1)

    def test_submission_intent_survives_retry_and_restart(self):
        manager = self.make_manager(lambda prompt, config: {"q1": "B"})
        manager.command("start", {"auto_fill": True, "auto_submit": True})
        until(lambda: manager.snapshot()["status"] == "paused")
        self.assertEqual(manager.driver.submit_count, 1)
        manager.command("resume", {"auto_fill": True, "auto_submit": True})
        until(lambda: manager.snapshot()["status"] == "paused")
        self.assertEqual(manager.driver.submit_count, 1)
        changed = copy.deepcopy(QUESTION)
        changed['stem'] = '页面重排后稍有变化的题干'
        manager.driver.questions = lambda task: (manager.driver, {"questions":[changed], "formal_exam":False,"restricted":False})
        manager.command('resume', {"auto_fill": True, "auto_submit": True})
        until(lambda: manager.snapshot()['status']=='paused')
        self.assertEqual(manager.driver.submit_count, 1)
        latest = manager.store.latest()
        self.assertNotIn("secret", json.dumps(latest))
        self.assertEqual(latest["tasks"][0]["submission"], "intent")

    def test_model_error_does_not_fill_or_submit(self):
        def broken(prompt, config):
            raise TimeoutError("fixture only")
        manager = self.make_manager(broken)
        manager.command("start", {"auto_fill": True, "auto_submit": True})
        until(lambda: manager.snapshot()["status"] == "paused")
        self.assertEqual(manager.driver.fill_count, 0)
        self.assertEqual(manager.driver.submit_count, 0)
        self.assertIn("模型调用失败", manager.snapshot()["tasks"][0]["reason"])

    def test_embedded_question_blocks_playback_until_resolved(self):
        manager = self.make_manager(lambda prompt, config: {})
        manager.state["tasks"][0]["kind"] = "video"
        manager.driver.popup = lambda task: True
        manager.command("start", {"auto_fill": False})
        until(lambda: manager.snapshot()["status"] == "paused")
        self.assertEqual(manager.driver.play_count, 0)


class BrowserFixtureTests(unittest.TestCase):
    def empty_card(self, body='<dl><dd>暂无内容</dd></dl>'):
        self.context.route('**/knowledge/cards',lambda route:route.fulfill(content_type='text/html',body='<script>window.mArg={attachments:[]}</script>'+body))
        self.page.locator('[data-study-task-id]').evaluate_all('nodes=>nodes.forEach(n=>n.remove())')
        self.page.evaluate('''()=>{document.querySelector('[data-chapter-id="101"]').insertAdjacentHTML('beforeend','<span class="icon_Completed"></span>');const f=document.createElement('iframe');f.src='https://mooc1.chaoxing.com/knowledge/cards';document.body.append(f)}''')
        self.page.frame_locator('iframe').locator('dd').wait_for()
        return next(f for f in self.page.frames if f.url.endswith('/knowledge/cards'))

    def test_explicit_empty_card_requires_completed_directory_and_empty_attachments(self):
        frame=self.empty_card()
        samples,failures=self.driver.snapshots()
        self.assertTrue(self.driver.reference_only('101',samples,failures))
        self.assertFalse(self.driver.reference_only('101',samples,1))
        self.page.locator('.icon_Completed').evaluate('e=>e.remove()')
        samples,failures=self.driver.snapshots()
        self.assertFalse(self.driver.reference_only('101',samples,failures))
        self.page.evaluate('''document.querySelector('[data-chapter-id="101"]').insertAdjacentHTML('beforeend','<span class="icon_Completed"></span>')''')
        frame.evaluate("window.mArg.attachments=[{type:'unknown',property:{objectid:'unresolved'}}]")
        samples,failures=self.driver.snapshots()
        self.assertFalse(self.driver.reference_only('101',samples,failures))

    def test_empty_marker_does_not_hide_loading_errors_or_unresolved_frames(self):
        frame=self.empty_card()
        for content in ['<dl><dd>暂无内容</dd></dl><span aria-busy="true"></span>','<dl><dd>暂无内容</dd></dl><p>加载失败，请重试</p>','<dl><dd>暂无内容</dd></dl><iframe src="about:blank"></iframe>','<dl><dd>暂无内容</dd></dl><video></video>','<dl><dd>短文字</dd></dl>']:
            frame.evaluate('html=>document.body.innerHTML=html',content)
            samples,failures=self.driver.snapshots()
            self.assertFalse(self.driver.reference_only('101',samples,failures),content)

    def test_real_pointer_leave_is_observed_without_faking_focus_or_events(self):
        task=self.task('video-one')
        self.page.evaluate("document.documentElement.removeAttribute('data-lxt-pointer-left-at')")
        self.page.evaluate("document.dispatchEvent(new MouseEvent('mouseout',{bubbles:true,relatedTarget:null}))")
        self.assertFalse(self.driver.media_state(task)['pointer_left_recent'])
        self.page.mouse.move(300,150)
        self.page.mouse.move(-20,-20)
        state=self.driver.media_state(task)
        self.assertTrue(state['pointer_left_recent'])
        self.assertEqual(state['pause_reason'],'pointer_left_window')

    def prepare_link(self):
        self.context.route('**/ananas/modules/zt/hyperlink/index-pc.html',lambda r:r.fulfill(content_type='text/html',body='''<a id="hyperlink" href="https://example.test/article" target="_blank">Read article</a><script>document.querySelector('a').onclick=()=>parent.document.querySelector('[data-link-card]').classList.add('ans-job-finished')</script>'''))
        self.context.route('https://example.test/article',lambda r:r.fulfill(content_type='text/html',body='<p>Article fixture</p>'))
        self.page.evaluate('''()=>{window.mArg={attachments:[{type:'hyperlink',job:true,jobid:'link-task',property:{type:'text',_jobid:'link-task'}}]};const card=document.createElement('div');card.className='ans-attach-ct';card.setAttribute('data-link-card','true');const f=document.createElement('iframe');f.setAttribute('data',JSON.stringify({_jobid:'link-task',type:'text'}));f.src='/ananas/modules/zt/hyperlink/index-pc.html';card.append(f);document.body.append(card)}''')
        self.page.frame_locator('iframe[src*="hyperlink"]').locator('#hyperlink').wait_for()
        return self.task('link-task')

    def test_native_link_click_opens_page_and_platform_confirms(self):
        task=self.prepare_link();intent=[]
        raw=next(t for t in self.page.evaluate(DOM_SCRIPT)['tasks'] if t['local_id']=='link-task')
        self.assertEqual(raw['kind'],'link')
        self.driver.open_link(task,lambda:intent.append(True))
        self.assertEqual(intent,[True])
        self.assertEqual(self.driver.platform(task),'done')
        self.assertEqual(len(self.context.pages),1)

    def test_link_cancel_or_invalid_destination_does_not_click(self):
        task=self.prepare_link()
        with self.assertRaises(Interrupted):self.driver.open_link(task,lambda:(_ for _ in ()).throw(Interrupted()))
        self.assertNotEqual(self.driver.platform(task),'done')
        self.page.frame_locator('iframe[src*="hyperlink"]').locator('#hyperlink').evaluate('e=>e.href="javascript:void(0)"')
        with self.assertRaisesRegex(StudyError,'网页地址'):self.driver.open_link(task,lambda:None)
        self.assertEqual(len(self.context.pages),1)

    def test_single_chapter_scan_does_not_read_other_chapters(self):
        chapter={'id':'101','title':'Fixture chapter','parent_id':'','locked':False}
        with patch.object(self.driver,'catalogue',side_effect=AssertionError('must not reload full directory')),patch.object(self.driver,'enter_chapter',wraps=self.driver.enter_chapter) as enter:
            tasks,complete=self.driver.scan_chapter('fixture',chapter)
        self.assertEqual([call.args[0] for call in enter.call_args_list],['101'])
        self.assertEqual(len([task for task in tasks if task['kind']=='video']),2)
        self.assertTrue(complete)

    def test_optional_media_requires_completed_catalog_and_no_job_evidence(self):
        self.page.evaluate("""()=>{window.mArg={attachments:[{type:'video',property:{_jobid:'news-video'}}]};document.querySelector('[data-chapter-id="101"]').insertAdjacentHTML('beforeend','<span class="icon_Completed"></span>');const e=document.createElement('div');e.className='ans-attach-ct videoContainer';e.innerHTML='<iframe></iframe>';e.querySelector('iframe').setAttribute('data',JSON.stringify({_jobid:'news-video',type:'.mp4'}));document.body.append(e)}""")
        task={**self.task('news-video'),'kind':'video'}
        self.assertTrue(self.driver.optional_resource(task))
        self.page.locator('.icon_Completed').evaluate('e=>e.remove()')
        self.assertFalse(self.driver.optional_resource(task))
        self.page.evaluate("document.querySelector('[data-chapter-id=\"101\"]').insertAdjacentHTML('beforeend','<span class=icon_Completed></span>');document.querySelector('.ans-attach-ct').insertAdjacentHTML('beforeend','<span class=ans-job-icon>Task</span>')")
        self.assertFalse(self.driver.optional_resource(task))
        self.page.locator('.ans-job-icon').evaluate('e=>e.remove()')
        self.page.evaluate('window.mArg.attachments[0].job=true')
        self.assertFalse(self.driver.optional_resource(task))

    def test_inline_image_reference_requires_matching_resource_and_no_task_evidence(self):
        self.page.evaluate('''()=>{window.mArg={attachments:[{property:{module:'insertimage',type:'.png',objectid:'qr-resource',_jobid:'qr-image',name:'Scan news'}}]};document.querySelector('[data-chapter-id="101"]').insertAdjacentHTML('beforeend','<span class="icon_Completed"></span>');document.body.insertAdjacentHTML('beforeend','<img _jobid="qr-image" src="/qr-resource.png">')}''')
        task={**self.task('qr-image'),'kind':'unknown'}
        self.assertTrue(self.driver.optional_resource(task))
        raw=next(t for t in self.page.evaluate(DOM_SCRIPT)['tasks'] if t['local_id']=='qr-image')
        self.assertEqual(raw['kind'],'image')
        self.page.locator('.icon_Completed').evaluate('e=>e.remove()')
        self.assertTrue(self.driver.optional_resource(task))
        samples,failures=self.driver.snapshots()
        chapter={'id':'101','title':'Mixed chapter','locked':False,'parent_id':''}
        with patch.object(self.driver,'enter_chapter',return_value=(samples,failures)):
            tasks,complete=self.driver.scan_chapter('fixture',chapter)
        image=next(t for t in tasks if t.get('local_id')=='qr-image')
        self.assertTrue(image['reference_only'])
        self.assertFalse(image['is_task_point'])
        self.assertFalse(image['verified'])
        videos=[t for t in tasks if t['kind']=='video']
        self.assertEqual(len(videos),2)
        self.assertTrue(all(t['is_task_point'] and not t['verified'] for t in videos))
        self.page.evaluate('''()=>document.querySelector('[data-chapter-id="101"]').insertAdjacentHTML('beforeend','<span class="icon_Completed"></span>')''')
        for field,value in [('job',True),('isPassed',False),('jobid','required-job')]:
            self.page.evaluate('([key,value])=>window.mArg.attachments[0][key]=value',[field,value])
            if field=='jobid':
                with self.assertRaises(StudyError):self.driver.optional_resource(task)
            else:self.assertFalse(self.driver.optional_resource(task))
            self.page.evaluate('key=>delete window.mArg.attachments[0][key]',field)
        self.page.locator('img[_jobid]').evaluate('e=>{const c=document.createElement("div");c.className="ans-attach-ct";e.replaceWith(c);c.append(e);c.insertAdjacentHTML("beforeend","<span class=ans-job-icon>Task</span>")}')
        self.assertFalse(self.driver.optional_resource(task))
        self.page.locator('.ans-job-icon').evaluate('e=>e.remove()')
        self.page.locator('img[_jobid]').evaluate('e=>e.src="/different-resource.png"')
        self.assertFalse(self.driver.optional_resource(task))

    def test_non_job_knowledge_graph_is_reference_but_job_marker_is_not(self):
        html='''<p>Fixture chapter learning overview.</p><script>window.mArg={attachments:[{type:'knowledgeGraph',job:null,property:{module:'knowledgeGraph',knowledgeGraphId:'fixture-graph'}}]}</script><div class="ans-attach-ct"><iframe data='{"knowledgeGraphId":"fixture-graph"}' src="https://mooc1.chaoxing.com/graph-fixture"></iframe></div>'''
        self.context.route('**/knowledge/cards',lambda route:route.fulfill(content_type='text/html',body=html))
        self.context.route('**/graph-fixture',lambda route:route.fulfill(content_type='text/html',body='<p>Fixture graph</p>'))
        self.page.locator('[data-study-task-id]').evaluate_all('nodes=>nodes.forEach(n=>n.remove())')
        self.page.evaluate("""()=>{document.querySelector('[data-chapter-id="101"]').insertAdjacentHTML('beforeend','<span class="icon_Completed"></span>');const f=document.createElement('iframe');f.src='https://mooc1.chaoxing.com/knowledge/cards';document.body.append(f)}""")
        self.page.frame_locator('iframe').locator('.ans-attach-ct').wait_for()
        samples,failures=self.driver.snapshots()
        self.assertTrue(self.driver.reference_only('101',samples,failures))
        frame=next(f for f in self.page.frames if f.url.endswith('/knowledge/cards'))
        frame.locator('.ans-attach-ct').evaluate("e=>e.insertAdjacentHTML('afterbegin','<span class=ans-job-icon>Task</span>')")
        samples,failures=self.driver.snapshots()
        self.assertFalse(self.driver.reference_only('101',samples,failures))
        frame.locator('.ans-job-icon').evaluate('e=>e.remove()')
        frame.evaluate('window.mArg.attachments[0].job=true')
        samples,failures=self.driver.snapshots()
        self.assertFalse(self.driver.reference_only('101',samples,failures))

    def test_plain_text_reference_requires_empty_attachments_and_completed_catalog(self):
        self.context.route('**/knowledge/cards',lambda route:route.fulfill(content_type='text/html',body='<p>Fixture course teaching objectives, without task attachments.</p><script>window.mArg={attachments:[]}</script>'))
        self.page.locator('[data-study-task-id]').evaluate_all('nodes=>nodes.forEach(n=>n.remove())')
        self.page.evaluate("""()=>{document.querySelector('[data-chapter-id="101"]').insertAdjacentHTML('beforeend','<span class="icon_Completed"></span>');const f=document.createElement('iframe');f.src='https://mooc1.chaoxing.com/knowledge/cards';document.body.append(f)}""")
        self.page.frame_locator('iframe').locator('p').wait_for()
        samples,failures=self.driver.snapshots()
        self.assertTrue(self.driver.reference_only('101',samples,failures))
        self.assertFalse(self.driver.reference_only('101',samples,1))
        frame=next(f for f in self.page.frames if f.url.endswith('/knowledge/cards'))
        frame.evaluate("()=>{const e=document.createElement('audio');e.id='auditionAudio';e.style.display='none';document.body.append(e)}")
        samples,failures=self.driver.snapshots()
        self.assertTrue(self.driver.reference_only('101',samples,failures))
        frame.locator('#auditionAudio').evaluate("e=>e.src='https://mooc1.chaoxing.com/real-audio.mp3'")
        samples,failures=self.driver.snapshots()
        self.assertFalse(self.driver.reference_only('101',samples,failures))
        frame.locator('#auditionAudio').evaluate("e=>{e.removeAttribute('src');e.id='other-audio'}")
        samples,failures=self.driver.snapshots()
        self.assertFalse(self.driver.reference_only('101',samples,failures))
        frame.locator('#other-audio').evaluate('e=>e.remove()')
        frame.evaluate('window.mArg.attachments=[{jobid:"pending-job",type:"video"}]')
        samples,failures=self.driver.snapshots()
        self.assertFalse(self.driver.reference_only('101',samples,failures))

    @classmethod
    def setUpClass(cls):
        import lxt
        from playwright.sync_api import sync_playwright
        cls.runtime = sync_playwright().start()
        cls.browser = lxt.launch_chromium(cls.runtime, headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.context.add_init_script(MEDIA_GUARD_SCRIPT)
        self.addCleanup(self.context.close)
        html = (ROOT / "tests/fixtures/course.html").read_text(encoding="utf-8")
        def route(request):
            if "fixture-frame" in request.request.url:
                request.fulfill(content_type="text/html", body='<html><body><iframe src="https://mooc1.chaoxing.com/nested"></iframe></body></html>')
            elif "nested" in request.request.url:
                request.fulfill(content_type="text/html", body='<html><body><video controls width="320" height="180"></video></body></html>')
            else:
                request.fulfill(content_type="text/html", body=html)
        self.context.route("**/*", route)
        self.page = self.context.new_page()
        self.page.goto(LINK)
        self.page.wait_for_load_state()
        self.driver = CourseBrowser(lambda: None, lambda message: None)
        self.driver.page = self.page
        self.driver.context = self.context
        self.driver.url = LINK

    def task(self, identity):
        return {"local_id": identity, "chapter_id": "101", "id": identity}

    def test_required_markers_rules_and_optional_discussion_count(self):
        self.page.evaluate("""() => {
          window.mArg={attachments:[{job:true,jobid:'required-video',type:'video',property:{objectid:'video-object'}},{property:{_jobid:'optional-discussion',module:'insertbbs',isJob:false,title:'Discuss this topic'}}]};
          const root=document.createElement('div');root.className='ans-attach-ct';root.innerHTML='<span class="ans-job-icon">任务点</span><p>完成条件 观看时长需 ≥ 总时长的 90%</p><iframe></iframe>';root.querySelector('iframe').setAttribute('data',JSON.stringify({objectid:'video-object',type:'video'}));document.body.append(root);
          const optional=document.createElement('div');optional.className='ans-attach-ct ans-job-finished';optional.innerHTML='<iframe module="insertbbs"></iframe>';optional.querySelector('iframe').setAttribute('data',JSON.stringify({_jobid:'optional-discussion',isJob:false}));document.body.append(optional);
        }""")
        data=self.page.evaluate(DOM_SCRIPT)
        video=next(t for t in data['tasks'] if t['local_id']=='required-video')
        optional=next(t for t in data['tasks'] if t['local_id']=='optional-discussion')
        self.assertEqual(video['platform'],'todo')
        self.assertIn('90%',video['requirement'])
        self.assertEqual(optional['kind'],'discussion')
        self.assertFalse(optional['is_task_point'])

    def test_offscreen_lazy_video_is_scrolled_into_view_and_played(self):
        import base64,io,wave
        buffer=io.BytesIO()
        with wave.open(buffer,'wb') as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(8000);audio.writeframes(b'\0\0'*16000)
        source='data:audio/wav;base64,'+base64.b64encode(buffer.getvalue()).decode()
        self.page.evaluate("""source=>{const spacer=document.createElement('div');spacer.style.height='2200px';document.body.append(spacer);
          const root=document.createElement('div');root.dataset.studyTaskId='lazy-video';root.dataset.studyKind='video';root.dataset.platformState='todo';
          root.innerHTML='<video muted controls width="480" height="270"></video>';document.body.append(root);
          const video=root.querySelector('video');new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting))video.src=source}).observe(video);
        }""",source)
        self.driver.play(self.task('lazy-video'),1)
        self.assertGreater(self.page.evaluate('scrollY'),1000)
        self.assertFalse(self.page.locator('[data-study-task-id="lazy-video"] video').evaluate('e=>e.paused'))
        self.driver.pause_media()

    def test_discussion_navigation_edit_only_and_verified_publication(self):
        from study_discussion import discussion
        html='''<!doctype html><meta charset="utf-8"><button id="enter" onclick="this.hidden=true;document.querySelector('#editor').hidden=false">参与讨论</button><section id="editor" hidden><div contenteditable="true" role="textbox"></div><button onclick="const text=document.querySelector('[contenteditable]').innerText;const reply=document.createElement('div');reply.dataset.replyId='reply-'+Date.now();reply.textContent=text;document.body.append(reply);window.posts=(window.posts||0)+1">发表</button></section>'''
        self.context.route('**/discussion-fixture',lambda route:route.fulfill(content_type='text/html',body=html))
        self.page.evaluate("""() => {const root=document.createElement('div');root.dataset.studyTaskId='discussion-one';root.dataset.studyKind='discussion';root.dataset.platformState='todo';root.innerHTML='<iframe src="https://mooc1.chaoxing.com/discussion-fixture" height="300"></iframe>';document.body.append(root)}""")
        self.page.wait_for_timeout(200)
        task=self.task('discussion-one');intent=[]
        result=discussion(self.driver,task,'Fixture discussion body',False,lambda:intent.append(1))
        self.assertEqual(result['state'],'draft')
        self.assertFalse(intent)
        result=discussion(self.driver,task,'Fixture discussion body',True,lambda:intent.append(1))
        self.assertEqual(result['state'],'published')
        self.assertEqual(intent,[1])
        frame=next(f for f in self.page.frames if f.url.endswith('discussion-fixture'))
        self.assertEqual(frame.evaluate('window.posts'),1)

    def test_scan_progress_reports_real_counts_monotonically(self):
        reports=[]
        chapters,tasks,complete=self.driver.scan('fixture',reports.append)
        self.assertTrue(reports)
        self.assertEqual(reports[0]['completed'],0)
        self.assertEqual(reports[-1]['completed'],len(chapters))
        self.assertEqual([p['completed'] for p in reports],sorted(p['completed'] for p in reports))

    def test_media_stays_paused_until_explicit_play_and_reblocks_after_pause(self):
        import base64, io, wave
        buffer=io.BytesIO()
        with wave.open(buffer,'wb') as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(8000)
            audio.writeframes(b'\0\0'*16000)
        source='data:audio/wav;base64,'+base64.b64encode(buffer.getvalue()).decode()
        self.page.evaluate("""source=>{const root=document.createElement('div');root.dataset.studyTaskId='guard-audio';root.dataset.studyKind='audio';root.dataset.platformState='todo';document.body.append(root);const node=document.createElement('audio');node.id='guard-audio';node.controls=true;node.muted=true;node.src=source;root.append(node)}""",source)
        media=self.page.locator('#guard-audio')
        async_play="async node=>{await node.play().catch(()=>{});return node.paused}"
        self.assertTrue(media.evaluate(async_play))
        task=self.task('guard-audio')
        with patch.object(self.driver,'media',return_value=media.element_handle()):
            self.driver.play(task,1)
        self.assertFalse(media.evaluate('node=>node.paused'))
        self.driver.pause_media()
        self.assertIsNone(media.get_attribute('data-lxt-playback-allowed'))
        self.assertTrue(media.evaluate(async_play))

    def test_directory_reads_progress_and_captures_signed_link_without_player(self):
        from urllib.parse import parse_qs, urlsplit
        player_requests=[]
        def fixture(route):
            parts=urlsplit(route.request.url)
            query=parse_qs(parts.query)
            if parts.path=='/visit/courses/list':
                html=''.join(f'<li class="course"><a href="/visit/stucoursemiddle?courseid={identity}&clazzid=222&cpi=hidden">Course {identity}</a></li>' for identity in ('111','111','333','444'))
            elif parts.path=='/visit/stucoursemiddle':
                identity=query['courseid'][0]
                html=f'<button onclick="document.querySelector(\'iframe\').src=\'/mooc2-ans/mycourse/studentcourse?courseid={identity}\'">章节</button><iframe></iframe>'
            elif parts.path.endswith('/mycourse/studentcourse'):
                identity=query['courseid'][0]
                progress={'111':'已完成任务点: 1/3','333':'已完成任务点: 3/3','444':'目录状态未知'}[identity]
                link=LINK.replace('courseId=111','courseId='+identity)
                html=f'''<p>{progress}</p><div class="chapter_item" onclick="toOld()">1.1</div><script>function toOld(){{setTimeout(()=>{{top.location.href='https://mooc1.chaoxing.com/mycourse/transfer?refer='+encodeURIComponent({json.dumps(link)})}},500)}}</script>'''
                if identity=='333':
                    # Native navigation was suppressed; the page still exposes its own URL parameters.
                    html=f'''<p>{progress}</p><div class="chapter_item" onclick="toOld('333','101','222',0)">1.1</div><script>
                    const ServerHost={{mooc1Domain:'https://mooc1.chaoxing.com'}};
                    function toOld(){{var enc='secret';var cpi=123;var fanyaVersion=4;
                    const path='/mycourse/studentstudy?chapterId=';return;}}</script>'''
            else:
                player_requests.append(parts.path)
                html='unexpected navigation'
            route.fulfill(content_type='text/html; charset=utf-8',body=html)
        self.context.route('**/*',fixture)
        self.driver.context=self.context
        before=self.page.url
        result=self.driver.discover_courses(lambda **values:None)
        self.assertEqual([item['platform'] for item in result['courses']],['todo','done','unknown'])
        self.assertTrue(all('enc=secret' in item['url'] for item in result['courses']))
        self.assertEqual(self.page.url,before)
        self.assertEqual(player_requests,[])

    def test_directory_login_failure_is_explicit(self):
        self.context.route('**/*', lambda route:route.fulfill(content_type='text/html',body='<input type="password">请登录'))
        self.driver.context=self.context
        with self.assertRaisesRegex(StudyError,'登录'):
            self.driver.discover_courses(lambda **values:None)

    def test_multi_video_nested_frames_and_node_replacement(self):
        snapshot = self.page.evaluate(DOM_SCRIPT)
        self.assertEqual(len(snapshot["tasks"]), 3)
        self.assertEqual(snapshot["chapters"][1]["parent_id"], "101")
        self.assertTrue(snapshot["catalog_complete"])
        self.assertEqual(self.driver.media_state(self.task("video-two"))["current"], 0)
        self.page.locator('[data-study-task-id="video-one"] video').evaluate("element=>element.replaceWith(element.cloneNode(true))")
        self.assertEqual(self.driver.media_state(self.task("video-one"))["current"], 0)
        self.page.locator('#coursetree').evaluate("element=>element.removeAttribute('data-total-chapters')")
        self.assertFalse(self.page.evaluate(DOM_SCRIPT)["catalog_complete"])

    def test_scan_counts_task_points_not_chapters(self):
        chapters, tasks, complete = self.driver.scan('fixture-course', lambda chapter: None)
        self.assertEqual(len(chapters), 2)
        self.assertEqual(len(tasks), 6)
        self.assertEqual(sum(task['kind']=='video' for task in tasks), 4)
        self.assertFalse(any(task['verified'] for task in tasks))

    def test_attachment_object_id_alias_does_not_duplicate_jobs(self):
        self.page.evaluate("""() => {window.mArg={attachments:[{jobid:'job-stable',isPassed:false,type:'video',property:{objectid:'object-stable',name:'Alias fixture'}}]};const element=document.createElement('div');element.className='ans-attach-ct';element.setAttribute('data','{"objectid":"object-stable","type":"video"}');element.innerHTML='<video controls width="320" height="180"></video>';document.body.append(element)}""")
        tasks = self.page.evaluate(DOM_SCRIPT)['tasks']
        self.assertEqual(sum(task['local_id']=='job-stable' for task in tasks), 1)
        self.assertFalse(any(task['local_id']=='object-stable' for task in tasks))
        self.assertEqual(self.driver.media_state(self.task('job-stable'))['current'], 0)

    def test_fill_reordering_changed_questions_and_submit(self):
        task = self.task("quiz-one")
        frame, data = self.driver.questions(task)
        questions = data["questions"]
        answers = {"q-single": ["B"], "q-multi": ["A", "C"], "q-blank": ["甲", "乙"]}
        self.driver.fill(task, questions, answers)
        self.driver.submit(task)
        self.assertEqual(self.page.evaluate('window.fixtureSubmits'), 1)
        self.assertEqual(self.driver.platform(task), "done")
        self.page.locator('[data-question-id="q-single"] .stem').evaluate("element=>element.textContent='已经换成另一道题'")
        with self.assertRaises(StudyError):
            self.driver.fill(task, questions, answers)
        with self.assertRaises(StudyError):
            self.driver.submit(task, questions)
        self.assertEqual(self.page.evaluate('window.fixtureSubmits'), 1)

    def test_verification_and_login_are_explicit(self):
        self.page.locator('body').evaluate("element=>element.append('请完成验证码')")
        with self.assertRaisesRegex(StudyError, "验证"):
            self.driver.snapshots()

    def test_image_question_and_unknown_status_are_not_assumed_complete(self):
        from study_core import validate_questions
        self.page.locator('[data-study-task-id="quiz-one"] .stem').first.evaluate("element=>element.append(document.createElement('img'))")
        frame, data = self.driver.questions(self.task('quiz-one'))
        with self.assertRaisesRegex(StudyError, '图片'):
            validate_questions(data['questions'])
        self.page.locator('[data-study-task-id="video-one"]').evaluate("element=>element.removeAttribute('data-platform-state')")
        self.assertEqual(self.driver.platform(self.task('video-one')), 'unknown')

    def test_one_unreadable_frame_does_not_hide_other_frames(self):
        class BrokenFrame:
            def evaluate(self, script):
                raise RuntimeError('fixture inaccessible frame')
        replacement = SimpleNamespace(is_closed=lambda: False, frames=[BrokenFrame()]+list(self.page.frames))
        with patch.object(self.driver, 'page', replacement):
            snapshots, failures = self.driver.snapshots()
        self.assertEqual(failures, 1)
        self.assertEqual(len(snapshots[0][1]['tasks']), 3)

    def test_actual_read_requirement_waits_before_scroll(self):
        self.page.evaluate("""() => {const element=document.createElement('div');element.setAttribute('data-study-task-id','read-one');element.setAttribute('data-study-kind','text');element.setAttribute('data-platform-state','todo');element.innerHTML='<div data-read-requirement>每页至少阅读3秒</div>';document.body.append(element)}""")
        waits = []
        with patch.object(self.driver, 'wait', side_effect=lambda seconds: waits.append(seconds)):
            task = self.task('read-one')
            task['kind'] = 'text'
            self.driver.read_step(task, elapsed=0, remaining=30)
        self.assertEqual(waits, [3])

    def test_read_scrolls_nested_document_and_new_lazy_pages_not_sidebar(self):
        html='''<!doctype html><style>body{margin:0}#reader{height:180px;overflow-y:auto}</style><div id="reader"><div style="height:1600px">Fixture slides</div></div><script>window.more=false;reader.onscroll=()=>{if(!window.more&&reader.scrollTop>1000){window.more=true;const e=document.createElement('div');e.style.height='900px';reader.append(e)}}</script>'''
        self.context.route('**/reader-fixture',lambda route:route.fulfill(content_type='text/html',body=html))
        self.page.evaluate("""() => {const nav=document.createElement('nav');nav.id='sidebar';nav.style.cssText='height:100px;overflow:auto';nav.innerHTML='<div style="height:1000px">Catalog</div>';document.body.prepend(nav);const root=document.createElement('div');root.dataset.studyTaskId='read-nested';root.dataset.studyKind='document';root.dataset.platformState='todo';root.innerHTML='<iframe src="https://mooc1.chaoxing.com/reader-fixture" height="240"></iframe>';document.body.append(root)}""")
        self.page.frame_locator('iframe[src$="reader-fixture"]').locator('#reader').wait_for()
        frame=next(f for f in self.page.frames if f.url.endswith('reader-fixture'))
        frame.locator('#reader').wait_for()
        task={**self.task('read-nested'),'kind':'document'}
        self.assertTrue(self.driver.read_step(task))
        self.page.wait_for_timeout(100)
        self.assertTrue(self.driver.read_step(task))
        self.assertFalse(self.driver.read_step(task))
        self.assertTrue(frame.locator('#reader').evaluate('e=>e.scrollTop+e.clientHeight>=e.scrollHeight-2'))
        self.assertEqual(self.page.locator('#sidebar').evaluate('e=>e.scrollTop'),0)

    def test_read_scrolls_iframe_document_scrolling_element(self):
        self.context.route('**/long-reader',lambda route:route.fulfill(content_type='text/html',body='<html><body><div style="height:3000px">Fixture document</div></body></html>'))
        self.page.evaluate("""() => {const root=document.createElement('div');root.dataset.studyTaskId='read-body';root.innerHTML='<iframe src="https://mooc1.chaoxing.com/long-reader" height="240"></iframe>';document.body.append(root)}""")
        self.page.frame_locator('iframe[src$="long-reader"]').locator('body div').wait_for()
        frame=next(f for f in self.page.frames if f.url.endswith('long-reader'))
        frame.locator('body div').wait_for()
        self.assertTrue(self.driver.read_step({**self.task('read-body'),'kind':'document'}))
        self.assertTrue(frame.evaluate('document.scrollingElement.scrollTop+document.scrollingElement.clientHeight>=document.scrollingElement.scrollHeight-2'))

    def test_inline_submit_controls_are_detected(self):
        self.page.locator('[data-question-id="q-single"] input').first.evaluate("element=>element.setAttribute('onclick','submitAnswer()')")
        frame, data = self.driver.questions(self.task('quiz-one'))
        self.assertTrue(data['inline_submit'])

    def test_login_conflict_and_network_failure_are_visible(self):
        for text, reason in [('请扫码登录', '登录'), ('另一台设备学习中', '冲突'), ('访问被拦截', '风控')]:
            self.page.evaluate("text=>{const element=document.createElement('div');element.id='guard-fixture';element.textContent=text;document.body.append(element)}", text)
            with self.assertRaisesRegex(StudyError, reason):
                self.driver.snapshots()
            self.page.locator('#guard-fixture').evaluate('element=>element.remove()')
        self.page.goto('https://detect.chaoxing.com/api/monitor_temp')
        with self.assertRaisesRegex(StudyError,'冲突'):
            self.driver.snapshots()
        self.page.goto(LINK)
        self.context.route('**/mycourse/studentstudy?**', lambda route: route.abort())
        with self.assertRaises(Exception):
            self.driver.goto(LINK)

    def chapter_quiz(self):
        html = (ROOT / 'tests/fixtures/chaoxing_quiz.html').read_text(encoding='utf-8')
        self.context.route('**/work-fixture', lambda route: route.fulfill(content_type='text/html', body=html))
        self.page.set_content('<div class="ans-attach-ct"><iframe data=\'{"_jobid":"job-quiz","type":"work"}\' src="https://mooc1.chaoxing.com/work-fixture"></iframe></div>')
        self.page.wait_for_timeout(200)
        until(lambda: len(self.page.frames)==2)
        task = self.task('job-quiz')
        return task

    def test_real_layout_status_custom_controls_and_reading_subquestions(self):
        from study_core import validate_questions
        task = self.chapter_quiz()
        self.assertEqual(self.driver.platform(task), 'todo')
        frame, data = self.driver.questions(task)
        self.assertEqual(len(data['questions']), 3)
        validate_questions(data['questions'])
        self.assertEqual([question['qid'] for question in data['questions']], ['501','502child-one','502child-two'])
        self.assertIn('A red apple', data['questions'][1]['stem'])
        self.driver.fill(task,data['questions'],{'501':['A'],'502child-one':['A'],'502child-two':['B']})
        self.assertEqual(frame.locator('#answer501').input_value(), 'A')
        self.assertEqual(frame.locator('#answer502child-two').input_value(), 'B')
        self.assertIsNone(frame.evaluate('window.fixtureSubmits'))
        self.driver.submit(task,data['questions'])
        self.assertEqual(frame.evaluate('window.fixtureSubmits'),1)
        self.assertEqual(self.driver.platform(task),'done')

    def top_level_confirmation(self):
        task=self.chapter_quiz()
        self.page.evaluate("""() => {
          const pop=document.createElement('div');pop.id='workpop';pop.style.display='none';
          pop.innerHTML='<h2>提示</h2><p id="popcontent">确认提交？</p><a id="popok">提交</a><a id="popno">取消</a>';document.body.append(pop);
          window.workPop=callback=>{pop.style.display='block';document.querySelector('#popok').onclick=()=>{callback();pop.style.display='none'}};
        }""")
        frame,data=self.driver.questions(task)
        frame.locator('form > a.btnBlue').evaluate("""e=>{e.onclick=()=>{window.fixtureOpenCount=(window.fixtureOpenCount||0)+1;top.workPop(()=>fixtureConfirm())}}""")
        return task,frame,data

    def test_top_level_workpop_submit_is_confirmed_once(self):
        task,frame,data=self.top_level_confirmation()
        self.driver.fill(task,data['questions'],{'501':['A'],'502child-one':['A'],'502child-two':['B']})
        self.driver.submit(task,data['questions'])
        self.assertEqual(frame.evaluate('window.fixtureOpenCount'),1)
        self.assertEqual(frame.evaluate('window.fixtureSubmits'),1)
        self.assertEqual(self.driver.platform(task),'done')

    def test_top_level_confirmation_refuses_unknown_prompt_and_changed_questions(self):
        task,frame,data=self.top_level_confirmation()
        self.page.locator('#workpop').evaluate("e=>{e.style.display='block';e.querySelector('#popcontent').textContent='删除这份作业？'}")
        with self.assertRaisesRegex(StudyError,'不是明确'):
            self.driver.confirm_submission(task,data['questions'])
        self.page.locator('#popcontent').evaluate("e=>e.textContent='确认提交？'")
        frame.locator('.newZy_TItle').first.evaluate("e=>e.textContent='Changed question'")
        with self.assertRaisesRegex(StudyError,'题目变化'):
            self.driver.confirm_submission(task,data['questions'])
        self.assertIsNone(frame.evaluate('window.fixtureSubmits'))

    def test_confirmation_with_unanswered_or_restricted_warning_is_not_accepted(self):
        for warning in ['还有题目未作答，确认提交？','本次提交不可撤回，确认提交？']:
            task=self.chapter_quiz()
            frame,data=self.driver.questions(task)
            frame.locator('#tipContent').evaluate('element=>element.textContent='+json.dumps(warning))
            with self.assertRaises(StudyError):
                self.driver.submit(task,data['questions'])
            self.assertIsNone(frame.evaluate('window.fixtureSubmits'))

    def test_custom_selected_style_without_platform_value_fails_readback(self):
        task=self.chapter_quiz()
        frame,data=self.driver.questions(task)
        frame.evaluate("() => {window.addChoice=element=>{element.querySelector('.num_option').classList.add('check_answer');element.setAttribute('aria-checked','true')}}")
        with self.assertRaisesRegex(StudyError,'答案字段'):
            self.driver.fill(task,data['questions'],{'501':['A'],'502child-one':['A'],'502child-two':['B']})


class ApiTests(unittest.TestCase):
    def test_first_page_and_second_page_auth_and_validation(self):
        from fastapi.testclient import TestClient
        import app
        config = copy.deepcopy(app.DEFAULT_CONFIG)
        config["access_token"] = "fixture-token"
        with patch.object(app, "load_config", return_value=config), tempfile.TemporaryDirectory() as folder:
            manager = StudyManager(folder, lambda: {}, lambda prompt, config: {}, browser_factory=FakeBrowser)
            with patch.object(app, "STUDY_MANAGER", manager), TestClient(app.app) as client:
                self.assertEqual(client.get('/').status_code, 200)
                self.assertIn('batch(', client.get('/').text)
                self.assertEqual(client.get('/study').status_code, 200)
                self.assertEqual(client.get('/study-assets/study.js').status_code, 200)
                settings_asset=client.get('/study-assets/llm-settings.js')
                self.assertEqual(settings_asset.status_code,200)
                self.assertEqual(settings_asset.headers['cache-control'],'no-store')
                self.assertEqual(client.get('/api/study').status_code, 401)
                self.assertEqual(client.get('/api/study/courses').status_code, 401)
                self.assertEqual(client.post('/api/study/courses',json={}).status_code, 401)
                headers = {'X-Access-Token': 'fixture-token'}
                self.assertEqual(client.get('/api/study/courses',headers=headers).headers['cache-control'],'no-store')
                self.assertEqual(client.get('/api/study', headers=headers).status_code, 200)
                self.assertEqual(client.post('/api/study/load', json={'url':'https://example.com/'}, headers=headers).status_code, 400)
                self.assertEqual(client.post('/api/study/control', json={'action':'start'}, headers=headers).status_code, 409)


if __name__ == '__main__':
    unittest.main()
