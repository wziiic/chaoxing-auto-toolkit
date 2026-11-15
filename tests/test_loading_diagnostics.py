import sys,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'panel')]
from study_browser import CourseBrowser, StudyError


class LoadingDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.log=Mock()
        self.driver=CourseBrowser(lambda:None,self.log)

    def fail(self,code,kind='script'):
        self.driver.request_failed(SimpleNamespace(failure=code,resource_type=kind,url='https://static.chaoxing.com/file.js?token=fixture-secret'))

    def test_network_change_explained_without_signed_url(self):
        self.fail('net::ERR_NETWORK_CHANGED')
        message=self.driver.loading_error('读取超时')
        self.assertIn('网络连接发生变化',message)
        self.assertIn('static.chaoxing.com',message)
        self.assertNotIn('fixture-secret',message)
        self.assertNotIn('fixture-secret',str(self.log.call_args))

    def test_proxy_failure_has_targeted_hint(self):
        self.fail('net::ERR_TUNNEL_CONNECTION_FAILED','document')
        self.assertIn('课程窗口直连',self.driver.loading_error('读取超时'))

    def test_cancelled_navigation_is_not_network_failure(self):
        self.fail('net::ERR_ABORTED')
        self.assertIsNone(self.driver.network_issue)
        self.log.assert_not_called()

    def test_image_failure_does_not_blame_course_loading(self):
        self.fail('net::ERR_CONNECTION_RESET','image')
        self.assertEqual(self.driver.loading_error('读取超时'),'读取超时')

    def test_stale_failure_is_not_reused(self):
        self.driver.network_issue=(time.monotonic()-121,'net::ERR_NAME_NOT_RESOLVED','static.chaoxing.com')
        self.assertEqual(self.driver.loading_error('读取超时'),'读取超时')

    def test_login_redirect_is_detected_before_slow_dom_read(self):
        self.driver.page=Mock()
        self.driver.page.url='https://passport2.chaoxing.com/login'
        self.driver.page.goto.return_value=SimpleNamespace(status=200)
        self.driver.snapshots=Mock(side_effect=AssertionError('must not scan a login page'))
        with self.assertRaisesRegex(StudyError,'登录失效'):
            self.driver.goto('https://mooc1.chaoxing.com/mycourse/studentstudy?courseId=1')
        self.driver.snapshots.assert_not_called()

    def test_navigation_network_change_becomes_actionable_study_error(self):
        from playwright.sync_api import Error
        self.driver.page=Mock()
        def fail_navigation(*args,**kwargs):
            self.fail('net::ERR_NETWORK_CHANGED','document')
            raise Error('contains fixture-secret URL')
        self.driver.page.goto.side_effect=fail_navigation
        with self.assertRaisesRegex(StudyError,'网络连接发生变化') as error:
            self.driver.goto('https://mooc1.chaoxing.com/mycourse/studentstudy?courseId=1')
        self.assertNotIn('fixture-secret',str(error.exception))


if __name__=='__main__':unittest.main()
