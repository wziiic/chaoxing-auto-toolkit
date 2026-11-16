"""Reader readiness, safe recovery, and native document loading fixtures."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'panel')]
from study_core import Interrupted, StudyError
from study_service import StudyManager
from study_browser import CourseBrowser, MEDIA_GUARD_SCRIPT
from test_study import FakeBrowser, LINK, until


class ReadingQueueTests(unittest.TestCase):
    def manager(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        manager=StudyManager(folder.name,lambda:{},lambda *_:{},browser_factory=FakeBrowser)
        self.addCleanup(manager.close)
        manager.command('load',{'url':LINK});until(lambda:manager.snapshot()['status']=='ready')
        task=manager.state['tasks'][0];task.update(kind='document',attempts=1)
        manager.worker_epoch=manager.epoch
        return manager,task

    def test_empty_unloaded_reader_never_counts_as_finished(self):
        manager,task=self.manager();manager.driver.complete=True
        def read(*_,**__):
            self.assertFalse(task.get('action_finished',False))
            return False
        with patch.object(manager.driver,'reading_ready',create=True,side_effect=[False,False,True,True]), \
             patch.object(manager.driver,'read_step',create=True,side_effect=read) as scrolling:
            manager.read(task)
        self.assertEqual(scrolling.call_count,2)
        self.assertTrue(task['verified']);self.assertEqual(task['reading_progress']['phase'],'done')

    def test_reader_no_progress_for_sixty_seconds_reconnects_once(self):
        manager,task=self.manager();clock=[0]
        def wait(seconds):clock[0]+=seconds*2;manager.checkpoint()
        with patch('study_service.time.monotonic',side_effect=lambda:clock[0]), \
             patch.object(manager.driver,'reading_ready',create=True,return_value=False), \
             patch.object(manager.driver,'wait',side_effect=wait), \
             patch.object(manager,'reconnect_task_read',return_value='todo') as reconnect, \
             patch.object(manager,'verify') as verify:
            with self.assertRaisesRegex(StudyError,'课件阅读器重连后仍未加载完整'):manager.read(task)
        reconnect.assert_called_once();verify.assert_not_called()
        self.assertFalse(task['action_finished']);self.assertFalse(task['verified'])

    def test_pause_during_reader_loading_does_not_finish_task(self):
        manager,task=self.manager()
        with patch.object(manager.driver,'reading_ready',create=True,return_value=False), \
             patch.object(manager.driver,'wait',side_effect=Interrupted):
            with self.assertRaises(Interrupted):manager.read(task)
        self.assertFalse(task['action_finished'])

    def test_resume_can_reread_unconfirmed_document_without_submitting(self):
        manager,task=self.manager();task['action_finished']=True
        def read(*_,**__):manager.driver.complete=True;return False
        with patch.object(manager.driver,'read_step',create=True,side_effect=read) as scrolling:
            manager.command('resume')
            until(lambda:manager.snapshot()['status']=='needs_attention')
        self.assertEqual(scrolling.call_count,2)
        self.assertEqual(task['attempts'],2);self.assertTrue(task['verified'])
        self.assertEqual(manager.driver.submit_count,0)

    def test_finished_quiz_still_never_reexecutes(self):
        manager,task=self.manager();task.update(kind='quiz',action_finished=True)
        manager.command('resume',{'auto_fill':True,'auto_submit':True})
        until(lambda:manager.snapshot()['status']=='paused')
        self.assertEqual(manager.driver.fill_count,0);self.assertEqual(manager.driver.submit_count,0)


class ReadingBrowserTests(unittest.TestCase):
    def test_native_pdf_waits_for_actual_document_image_then_scrolls(self):
        import lxt
        from playwright.sync_api import sync_playwright
        with sync_playwright() as runtime:
            browser=lxt.launch_chromium(runtime,headless=True,standard_automation=True)
            try:
                context=browser.new_context();context.add_init_script(MEDIA_GUARD_SCRIPT)
                def route(request):
                    if '/ananas/modules/pdf/index.html' in request.request.url:
                        body='<iframe src="/screen/fixture" height="200"></iframe>'
                    elif '/screen/fixture' in request.request.url:body='<html><body></body></html>'
                    else:body='<div data-study-task-id="reader" data-study-kind="document" data-platform-state="todo"><iframe src="/ananas/modules/pdf/index.html" height="250"></iframe></div>'
                    request.fulfill(content_type='text/html',body=body)
                context.route('**/*',route)
                page=context.new_page();page.goto(LINK)
                driver=CourseBrowser(lambda:None,lambda _:None);driver.page=page;driver.context=context;driver.url=LINK
                task={'local_id':'reader','id':'reader','kind':'document'}
                page.wait_for_function("document.querySelector('iframe').contentDocument.querySelector('iframe')?.contentDocument?.readyState==='complete'")
                self.assertFalse(driver.reading_ready(task))
                screen=next(f for f in page.frames if '/screen/fixture' in f.url)
                screen.evaluate("""()=>{const image=new Image();image.src='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="1400"></svg>');document.body.append(image)}""")
                screen.wait_for_function('document.images[0]?.complete&&document.images[0]?.naturalWidth>0')
                self.assertTrue(driver.reading_ready(task))
                self.assertTrue(driver.read_step(task))
                self.assertTrue(screen.evaluate('scrollY>0'))
            finally:browser.close()


if __name__=='__main__':unittest.main()
