"""Start/resume must choose the earliest unresolved point, not a stale cursor."""
import tempfile
import unittest
from unittest.mock import patch

from test_study import LINK, FakeBrowser, LazyFakeBrowser, until
from study_service import StudyManager


class StartPositionTests(unittest.TestCase):
    def manager(self, browser=LazyFakeBrowser):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        manager=StudyManager(folder.name,lambda:{},lambda *_:{'q1':'B'},browser_factory=browser)
        self.addCleanup(manager.close)
        manager.command('load',{'url':LINK});until(lambda:manager.state['status']=='ready')
        return manager

    def test_resume_returns_to_earlier_incomplete_chapter(self):
        manager=self.manager();manager.state.update(active_chapter='103',queue_scope='all')
        manager.command('resume',{'auto_fill':True,'auto_submit':True})
        until(lambda:manager.state['status']=='done')
        self.assertEqual(manager.driver.chapter_calls,['101','102','103'])
        self.assertEqual(manager.driver.events[1],'submit:101a')

    def test_start_skips_finished_chapter_and_finished_point_in_next_chapter(self):
        manager=self.manager();manager.driver.completed.update({'101a','101b','102a'})
        manager.state.update(tasks=manager.driver.chapter_tasks(manager.state['chapters'][0]),chapter_states={'101':'ready'},active_chapter='101')
        manager.command('start',{'auto_fill':True,'auto_submit':True})
        until(lambda:manager.state['status']=='done')
        self.assertEqual(manager.driver.chapter_calls,['102','103'])
        self.assertEqual(manager.driver.submit_count,1)
        self.assertIn('submit:103a',manager.driver.events)

    def test_stale_verified_flag_does_not_hide_unfinished_chapter(self):
        manager=self.manager()
        tasks=manager.driver.chapter_tasks(manager.state['chapters'][0])
        for task in tasks:task.update(verified=True,platform='todo',status='pending')
        manager.state.update(tasks=tasks,chapter_states={'101':'ready'},active_chapter='103',queue_scope='all')
        manager.command('resume',{'auto_fill':True,'auto_submit':True})
        until(lambda:manager.state['status']=='done')
        self.assertEqual(manager.driver.chapter_calls[0],'101')
        self.assertEqual(manager.driver.submit_count,4)

    def test_first_unseen_chapter_is_not_skipped_by_resume_cursor(self):
        manager=self.manager();manager.state.update(active_chapter='103',queue_scope='all')
        manager.driver.completed.update({'102a'})
        manager.state.update(tasks=manager.driver.chapter_tasks(manager.state['chapters'][1]),chapter_states={'102':'ready'})
        manager.command('resume',{'auto_fill':True,'auto_submit':True})
        until(lambda:manager.state['status']=='done')
        self.assertEqual(manager.driver.chapter_calls,['101','103'])

    def test_explicit_selection_still_only_executes_selected_task(self):
        manager=self.manager()
        manager.state['tasks']=manager.driver.chapter_tasks(manager.state['chapters'][2])
        manager.command('start',{'task_ids':['103a'],'auto_fill':True,'auto_submit':True})
        until(lambda:manager.state['status']=='needs_attention')
        self.assertEqual(manager.driver.chapter_calls,['103'])
        self.assertEqual(manager.driver.submit_count,1)

    def test_completed_document_detected_at_execution_is_not_scrolled(self):
        manager=self.manager(FakeBrowser);manager.state['tasks'][0]['kind']='document';manager.driver.complete=True
        with patch.object(manager,'read') as read:
            manager.command('start');until(lambda:manager.state['status']=='needs_attention')
        read.assert_not_called();self.assertTrue(manager.state['tasks'][0]['verified'])

    def test_resume_revalidates_stale_incomplete_directory(self):
        manager=self.manager();manager.state['directory_complete']=False
        manager.command('resume',{'auto_fill':True,'auto_submit':True})
        until(lambda:manager.state['status']=='done')
        self.assertTrue(manager.state['directory_complete'])
        self.assertEqual(manager.driver.catalogue_count,2)

    def test_incomplete_or_missing_directory_does_not_claim_completion(self):
        for missing in (False,True):
            with self.subTest(missing=missing):
                manager=self.manager();manager.state['directory_complete']=False
                chapters=list(manager.state['chapters'])
                manager.driver.catalogue=lambda:(chapters[:-1],True) if missing else (chapters,False)
                manager.command('resume',{'auto_fill':True,'auto_submit':True})
                until(lambda:manager.state['status']=='needs_attention')
                self.assertFalse(manager.state['catalog_complete'])

    def test_revalidated_new_chapter_is_processed_before_completion(self):
        manager=self.manager();manager.state['directory_complete']=False
        chapters=list(manager.state['chapters'])+[{'id':'104','title':'New','parent_id':'','locked':False}]
        manager.driver.catalogue=lambda:(chapters,True)
        manager.command('resume',{'auto_fill':True,'auto_submit':True})
        until(lambda:manager.state['status']=='done')
        self.assertIn('104',manager.driver.chapter_calls)
        self.assertIn('submit:104a',manager.driver.events)

    def test_selected_scope_does_not_revalidate_or_expand_directory(self):
        manager=self.manager();manager.state['directory_complete']=False
        manager.state['tasks']=manager.driver.chapter_tasks(manager.state['chapters'][2])
        manager.command('start',{'task_ids':['103a'],'auto_fill':True,'auto_submit':True})
        until(lambda:manager.state['status']=='needs_attention')
        self.assertEqual(manager.driver.catalogue_count,1)
        self.assertEqual(manager.driver.chapter_calls,['103'])


if __name__=='__main__':unittest.main()
