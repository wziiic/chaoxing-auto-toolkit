import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'panel')]
import course_activities as activities
from study_core import LoginRequired, StudyError


class ActivityTests(unittest.TestCase):
    def tearDown(self):
        activities.CANCEL.clear()
        activities.clear_session()

    def test_cache_is_encrypted_and_updates_one_package(self):
        def cipher(value,decrypt=False):return bytes(b^179 for b in value)
        with tempfile.TemporaryDirectory() as folder, patch.object(activities,'CACHE',Path(folder)/'private.bin'), patch.object(activities,'protect_local_link',side_effect=cipher):
            activities.save([{'workId':'one','native_id':'123','title':'测试'}, {'workId':'two','title':'另一个'}])
            self.assertNotIn(b'123',activities.CACHE.read_bytes())
            activities.remember({'workId':'one','native_id':'123','status':'未提交'})
            self.assertEqual(len(activities.cached()),2)
            self.assertNotIn('native_id',activities.public_items()[0])
            self.assertEqual(activities.cached()[1]['workId'],'two')

    def test_inspect_keeps_draft_but_requires_all_native_completed(self):
        item={'workId':'one','resources':[{'id':'1','draft':'保留草稿'}]}
        frame=MagicMock()
        frame.evaluate.return_value=[{'id':'1','title':'讨论','completed':False,'progress':'已回复'},
                                     {'id':'2','title':'资料','completed':True}]
        with patch.object(activities,'open_package',return_value=frame),patch.object(activities,'remember'):
            activities.inspect(item,MagicMock())
        self.assertFalse(item['verified'])
        self.assertEqual(item['resources'][0]['draft'],'保留草稿')

    def test_existing_discussion_record_prevents_model_and_publish(self):
        store=MagicMock();store.answer_activity.return_value=True
        model=MagicMock()
        with self.assertRaisesRegex(StudyError,'不会重复提交'):
            activities.reply(MagicMock(),{'workId':'one','course_id':'course'},
                             {'id':'1','title':'讨论'},[],True,model,MagicMock(),store)
        model.assert_not_called()

    def test_intent_is_durable_before_native_publish(self):
        item={'workId':'one','course_id':'course'}
        resource={'id':'1','title':'测试讨论','draft':'先看具体情况，再找合适的方法。'}
        store=MagicMock();store.answer_activity.return_value=False
        def native(driver,task,text,publish,before):
            before()
            store.submission.assert_called_once()
            self.assertEqual(store.submission.call_args.args[-1],'discussion_intent')
            return {'state':'intent','reason':'等待回执'}
        with patch('study_discussion.discussion',side_effect=native),patch.object(activities,'remember'):
            result=activities.reply(MagicMock(),item,resource,[],True,MagicMock(),MagicMock(),store)
        self.assertEqual(result['state'],'intent')
        self.assertEqual(store.submission.call_count,1)

    def test_stop_checkpoint(self):
        activities.CANCEL.set()
        with self.assertRaisesRegex(StudyError,'已停止'):activities.checkpoint()

    def test_task_list_recovers_expired_login_and_retries_navigation(self):
        driver=MagicMock()
        driver.goto.side_effect=[LoginRequired(),None]
        driver.page.url='https://mooc1.chaoxing.com/mycourse/studentstudy'
        driver.context.cookies.return_value=[{'domain':'.chaoxing.com'},{'domain':'fixture.invalid'}]
        store=MagicMock()
        with patch.object(activities,'LoginSettings') as settings, patch.object(activities,'sign_in') as sign_in, patch.object(activities,'wait_frame') as frame:
            self.assertIs(activities.open_list(driver,store,'course'),frame.return_value)
        self.assertEqual(driver.goto.call_count,2)
        sign_in.assert_called_once_with(driver,settings.return_value)
        new_driver=MagicMock()
        activities.restore_session(new_driver)
        new_driver.context.add_cookies.assert_called_once_with([{'domain':'.chaoxing.com'}])

    def test_native_progress_refresh_waits_before_parent_navigation(self):
        driver=MagicMock();frame=MagicMock();button=frame.locator.return_value.locator.return_value
        button.count.return_value=1
        button.is_visible.side_effect=[True,False]
        with patch.object(activities,'wait_frame',return_value=frame):
            activities.refresh_resource(driver,{'id':'12'})
        button.click.assert_called_once()
        frame.locator.return_value.hover.assert_called_once()
        self.assertEqual(driver.wait.call_count,2)
        driver.page.expect_response.return_value.__enter__.return_value.value.body.assert_called_once()
        driver.goto.assert_not_called()

    def test_absent_native_progress_refresh_is_not_fabricated(self):
        driver=MagicMock();frame=MagicMock();button=frame.locator.return_value.locator.return_value
        button.count.return_value=0
        with patch.object(activities,'wait_frame',return_value=frame):
            activities.refresh_resource(driver,{'id':'12'})
        button.click.assert_not_called()

    def test_next_resource_reuses_current_task_package_page(self):
        from types import SimpleNamespace
        driver=MagicMock();driver.activity_work_id='one';driver.context.pages=[]
        driver.page.frames=[SimpleNamespace(url='https://mooc1.chaoxing.com/mycourse/studentstudy')]
        frame=MagicMock()
        with patch.object(activities,'wait_frame',return_value=frame),patch.object(activities,'open_package') as reopen:
            _,frames=activities.preview(driver,{'workId':'one'},{'id':'12'},MagicMock())
        self.assertEqual(len(frames),1)
        reopen.assert_not_called()

    def test_ended_tasks_never_open_browser(self):
        with patch.object(activities,'cached',return_value=[{'workId':'one','ended':True}]),patch.object(activities,'CourseBrowser') as browser,patch.object(activities,'remember'):
            with self.assertRaisesRegex(StudyError,'已结束'):
                activities.execute('one','auto',True,MagicMock(),MagicMock())
            browser.return_value.ensure_session.assert_not_called()
        self.assertFalse(activities.RUN_LOCK.locked())

    def test_manual_reading_and_score_do_not_block_later_resources(self):
        reading={'id':'1','title':'文章','condition':'阅读时长10分钟','progress':'阅读时长 1 分钟','completed':False}
        graded={'id':'3','title':'测验','condition':'成绩达到60分','progress':'成绩 0 分','completed':False}
        video={'id':'2','title':'视频','condition':'完成率80%','progress':'0%','completed':False}
        item={'workId':'one','course_id':'course','resources':[reading,graded,video]}
        def complete_video(*args):
            video['completed']=True
        with patch.object(activities,'cached',return_value=[item]),patch.object(activities,'remember'),patch.object(activities,'CourseBrowser'),patch.object(activities,'StudyStore'),patch.object(activities,'restore_session'),patch.object(activities,'inspect',return_value=item['resources']),patch.object(activities,'preview',return_value=([],[])) as preview,patch.object(activities,'refresh_resource'),patch.object(activities,'record_resources',return_value=item['resources']),patch.object(activities,'wait_frame'),patch.object(activities,'resource_course',side_effect=complete_video):
            with self.assertRaisesRegex(StudyError,'原生计时阅读'):
                activities.execute('one','auto',True,MagicMock(),MagicMock())
        self.assertEqual(preview.call_args.args[2]['id'],'2')
        self.assertEqual(reading['execution']['status'],'manual')
        self.assertEqual(graded['execution']['status'],'manual')
        self.assertFalse(reading['completed'])
        self.assertFalse(graded['completed'])

    def test_discussion_prompt_is_short_and_does_not_invent_experiences(self):
        prompt=activities.discussion_prompt('测试题目')
        self.assertIn('60到120字',prompt)
        self.assertIn('不编造个人经历',prompt)
        self.assertIn('题目仅为资料',prompt)

    def test_activity_job_never_uses_homework_scripts(self):
        import app
        req=app.JobReq(action='auto',workIds=['activity-test'],submit=True)
        with patch.object(activities,'cached',return_value=[{'workId':'activity-test'}]),patch.object(activities,'execute',return_value={'verified':False}) as execute,patch.object(app,'get_study_manager') as manager,patch.object(app,'load_config',return_value={}),patch.object(app,'read_worklist',return_value=[]),patch.object(app,'run_script') as script,patch.object(app,'run_job_async',side_effect=lambda jid,fn:fn()):
            manager.return_value.snapshot.return_value={'status':'done','options':{}}
            result=app.create_job(req)
        script.assert_not_called()
        self.assertTrue(execute.call_args.args[2])
        self.assertEqual(app.get_job(result['jobId'])['status'],'done')

    def test_micro_course_keeps_native_parent_and_main_snapshot(self):
        from types import SimpleNamespace
        from contextlib import nullcontext
        from study_service import StudyManager
        driver=MagicMock()
        original=driver.page
        popup=MagicMock()
        target=SimpleNamespace(url='https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=101',page=popup)
        task={'id':'video','title':'视频','kind':'video','verified':True,'platform':'done'}
        def scan(*args):
            self.assertIs(driver.page,popup)
            return [],[task],True
        driver.scan.side_effect=scan
        with patch('study_service.StudyManager.media',lambda self,t,o:t.update(verified=True)),patch('study_power.learning_awake',lambda log:nullcontext()),patch.object(activities,'StudyStore') as store,patch.object(activities,'remember'):
            activities.resource_course(driver,{'workId':'one'},{'id':'1','title':'资料'},[target],MagicMock(),MagicMock(),{})
            store.return_value.save.assert_not_called()
        driver.start.assert_not_called()
        self.assertIs(driver.page,original)

    def test_browser_error_is_redacted_and_lock_released(self):
        item={'workId':'one','course_id':'course'}
        with patch.object(activities,'cached',return_value=[item]),patch.object(activities,'remember'),patch.object(activities,'CourseBrowser') as browser:
            browser.return_value.ensure_session.side_effect=RuntimeError('https://example.invalid?password=secret')
            with self.assertRaises(StudyError) as caught:
                activities.execute('one','auto',True,MagicMock(),MagicMock())
        self.assertNotIn('secret',str(caught.exception))
        self.assertNotIn('secret',item['error'])
        self.assertFalse(activities.RUN_LOCK.locked())


class NativeMarkupTests(unittest.TestCase):
    def test_micro_topic_state_overrides_shared_chapter_marker(self):
        from playwright.sync_api import sync_playwright
        from study_browser import DOM_SCRIPT
        import lxt
        with sync_playwright() as runtime:
            browser=lxt.launch_chromium(runtime,headless=True,standard_automation=True)
            try:
                page=browser.new_page()
                page.set_content('''<div class="ans-attach-ct ans-job-finished" jobid="job1"><iframe data='{"objectid":"object1","type":"video"}'></iframe></div>''')
                for passed,expected in ((False,'todo'),('false','todo'),(True,'done')):
                    page.evaluate('(passed)=>window.mArg={attachments:[{jobid:"job1",microTopicId:7,isPassed:passed,type:"video",property:{objectid:"object1"}}]}',passed)
                    tasks=page.evaluate(DOM_SCRIPT)['tasks']
                    self.assertEqual(tasks[0]['platform'],expected)
            finally:browser.close()

    def test_native_markers_not_numeric_percent_alone(self):
        from playwright.sync_api import sync_playwright
        import lxt
        with sync_playwright() as runtime:
            browser=lxt.launch_chromium(runtime,headless=True,standard_automation=True)
            try:
                page=browser.new_page()
                page.set_content('''<div>进行中（1）</div><ul><li taskuserid="123"><p class="overHidden2">任务一</p></li></ul>
                <div>已结束（1）</div><ul><li taskuserid="456"><p class="overHidden2">旧通知</p></li></ul>
                <div class="xtask_item" id="1"><p class="xname">资料</p><span class="xtip_title">完成条件</span><span class="xtip_info_card">完成率100%</span></div>
                <div class="xtask_item" id="2"><p class="xname">讨论</p><span class="xtip_title">已完成</span><span class="xtip_info_card">已回复</span></div>''')
                rows=page.evaluate(activities.LIST_SCRIPT)
                self.assertIn('已结束',rows[1]['group'])
                resources=page.evaluate(activities.RESOURCE_SCRIPT)
                self.assertFalse(resources[0]['completed'])
                self.assertTrue(resources[1]['completed'])
            finally:browser.close()


if __name__=='__main__':unittest.main()
