from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'panel')]
from study_browser import CourseBrowser
from study_directory import LIST_URL


COURSE='https://mooc1.chaoxing.com/mycourse/studentstudy?courseId=111&clazzid=222&chapterId=1&enc=fixture'


class ShowWindowTests(unittest.TestCase):
    def driver(self,current,course=COURSE):
        driver=CourseBrowser(lambda:None,lambda message:None)
        driver.url=course;driver.page=Mock();driver.page.is_closed.return_value=False;driver.page.url=current
        driver.ensure_session=Mock();driver.goto=Mock();driver.window_state=Mock()
        return driver

    def test_course_list_is_not_reopened_after_course_selection(self):
        driver=self.driver(LIST_URL);driver.show_window()
        driver.goto.assert_called_once_with(COURSE)
        driver.ensure_session.assert_called_once_with(visible=True)

    def test_other_course_does_not_replace_selected_course(self):
        driver=self.driver(COURSE.replace('courseId=111','courseId=333'));driver.show_window()
        driver.goto.assert_called_once_with(COURSE)

    def test_same_course_preserves_current_chapter_without_reload(self):
        driver=self.driver(COURSE.replace('chapterId=1','chapterId=9'));driver.show_window()
        driver.goto.assert_not_called()

    def test_unselected_window_can_show_list(self):
        driver=self.driver('about:blank',None);driver.show_window()
        driver.goto.assert_called_once_with(LIST_URL)


if __name__=='__main__':unittest.main()
