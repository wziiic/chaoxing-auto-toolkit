import copy
import tempfile
import unittest
from unittest.mock import patch

from test_study import FakeBrowser, LazyFakeBrowser, LINK, QUESTION, until
from study_core import AnswerFormatError, StudyError, normalize_answers
from study_service import StudyManager


class AnswerFormatTests(unittest.TestCase):
    def manager(self, model=lambda *_:{'q1':'B'}, browser=FakeBrowser):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        manager=StudyManager(temporary.name,lambda:{},model,browser_factory=browser)
        self.addCleanup(manager.close)
        manager.command('load',{'url':LINK});until(lambda:manager.state['status']=='ready')
        return manager

    def test_booleans_match_only_exact_judgement_string_controls(self):
        q={**QUESTION,'type':'判断题','options':[{'data':'true','content':'正确'},{'data':'false','content':'错误'}]}
        self.assertEqual(normalize_answers([q],{'q1':True}),{'q1':['true']})
        self.assertEqual(normalize_answers([q],{'q1':[False]}),{'q1':['false']})
        for raw in (True,'答案是B','正确',None):
            with self.assertRaises(AnswerFormatError):normalize_answers([QUESTION],{'q1':raw})
        with self.assertRaises(AnswerFormatError):normalize_answers([{**q,'options':QUESTION['options']}],{'q1':True})

    def test_format_repair_is_bounded_and_null_is_not_guessed(self):
        manager=self.manager()
        with patch.object(manager,'request_model',side_effect=[{'q1':'答案是B'},{'q1':'B'}]) as model:
            self.assertEqual(manager.model([QUESTION]),{'q1':['B']});self.assertEqual(model.call_count,2)
        with patch.object(manager,'request_model',return_value={'q1':'invalid'}) as model:
            with self.assertRaises(AnswerFormatError):manager.model([QUESTION])
            self.assertEqual(model.call_count,2)
        with patch.object(manager,'request_model',return_value={'q1':None}) as model:
            with self.assertRaises(AnswerFormatError):manager.model([QUESTION])
            self.assertEqual(model.call_count,1)

    def test_bad_format_never_fills_or_consumes_execution_attempt(self):
        manager=self.manager(lambda *_:{'q1':'invalid'})
        manager.command('start',{'auto_fill':True,'auto_submit':True})
        until(lambda:manager.state['status']=='paused')
        self.assertEqual(manager.state['tasks'][0]['attempts'],0)
        self.assertEqual(manager.driver.fill_count,0);self.assertEqual(manager.driver.submit_count,0)

    def test_legacy_generation_attempts_can_recover_without_form_activity(self):
        manager=self.manager();task=manager.state['tasks'][0]
        task.update(attempts=3,status='manual',reason='模型答案无法精确匹配题目选项')
        with patch.object(manager,'verify',return_value=True):
            manager.command('start',{'auto_fill':True,'auto_submit':True})
            until(lambda:manager.state['status'] in ('needs_attention','paused'))
        self.assertEqual(manager.driver.submit_count,1,task.get('reason'))

    def test_legacy_activity_keeps_attempt_limit(self):
        manager=self.manager();task=manager.state['tasks'][0]
        task.update(attempts=3,status='manual',reason='模型答案无法精确匹配题目选项')
        manager.store.submission(manager.state['course_id'],task['id'],'fingerprint','filled')
        manager.command('start',{'auto_fill':True,'auto_submit':True})
        until(lambda:manager.state['status']=='paused')
        self.assertEqual(manager.driver.fill_count,0);self.assertEqual(manager.driver.submit_count,0)
        self.assertIn('执行上限',task['reason'])

    def test_lazy_reread_preserves_safe_legacy_model_failure_recovery(self):
        manager=self.manager(browser=LazyFakeBrowser)
        task=manager.driver.chapter_tasks(manager.state['chapters'][2])[0]
        task.update(attempts=3,status='manual',reason='模型答案无法精确匹配题目选项')
        manager.state['tasks']=[task]
        manager.command('start',{'task_ids':[task['id']],'auto_fill':True,'auto_submit':True})
        until(lambda:manager.state['status'] in ('needs_attention','paused'))
        self.assertEqual(manager.driver.submit_count,1)

    def test_login_resume_explicit_task_ids_override_old_full_course_scope(self):
        manager=self.manager(browser=LazyFakeBrowser)
        task=manager.driver.chapter_tasks(manager.state['chapters'][2])[0]
        manager.state.update(tasks=[task],queue_scope='all')
        manager.command('resume',{'task_ids':[task['id']],'auto_fill':True,'auto_submit':True})
        until(lambda:manager.state['status'] in ('needs_attention','paused'))
        self.assertEqual(manager.driver.chapter_calls,['103'])
        self.assertEqual(manager.driver.submit_count,1)

    def test_course_scope_allows_only_current_restricted_practice(self):
        manager=self.manager();task=manager.state['tasks'][0]
        options=manager.execution_options({'auto_fill':True,'auto_submit':True,'approved_course_ids':[manager.state['course_id']]})
        manager.worker_epoch=manager.epoch
        data={'questions':[QUESTION],'formal_exam':False,'restricted':True}
        with patch.object(manager.driver,'questions',return_value=(manager.driver,data)),patch.object(manager,'verify',return_value=True),patch.object(manager.driver,'submit',wraps=manager.driver.submit) as submit:
            manager.answer(task,options)
            self.assertTrue(submit.call_args.kwargs['allow_restricted'])
        self.assertFalse(manager.restricted_approved(task,dict(options,approved_course_ids=['a'*24])))
        with patch.object(manager.driver,'questions',return_value=(manager.driver,dict(data,formal_exam=True))):
            with self.assertRaisesRegex(StudyError,'正式考试'):manager.answer(task,options)

    def test_legacy_options_preserve_explicit_course_scope_and_allow_revocation(self):
        manager=self.manager();course=manager.state['course_id']
        manager.state['options']={'approved_course_ids':[course]}
        self.assertEqual(manager.execution_options({})['approved_course_ids'],[course])
        self.assertEqual(manager.execution_options({'approved_course_ids':None})['approved_course_ids'],[course])
        self.assertEqual(manager.execution_options({'approved_course_ids':[]})['approved_course_ids'],[])
        with self.assertRaises(StudyError):manager.execution_options({'approved_course_ids':['invalid']})


if __name__=='__main__':unittest.main()
