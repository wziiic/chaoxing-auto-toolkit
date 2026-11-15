"""Browser checks for per-window reads and shared startup preferences (fixtures only)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lxt
from playwright.sync_api import sync_playwright


def main():
    link = 'https://mooc1.chaoxing.com/mycourse/studentstudy?courseId=111&clazzid=222&chapterId=101&cpi=123&enc=fixture'
    state = dict(status='ready',loaded=True,course_id='fixture',url=link,input_url=link,message='',worker_responsive=True,
                 current='',logs=[],options={},selection=[],catalog_complete=True,
                 chapters=[dict(id='101',title='测试章节',parent_id='',locked=False)],tasks=[],
                 counts=dict(discovered=0,verified=0,failed=0,manual=0,waiting_verification=0))
    course = dict(id='one',title='测试课程',url=link,platform='todo',completed=1,total=3,reason='')
    directory = dict(status='idle',courses=[],checked=0,total=0,complete=False,message='',updated_at=1)
    reads, loads, commands, errors = [], [], [], []
    with sync_playwright() as runtime:
        browser = lxt.launch_chromium(runtime,headless=True,standard_automation=True)
        context = browser.new_context(viewport=dict(width=1280,height=900))
        context.route('**/api/study',lambda route:route.fulfill(json=state))
        def courses(route):
            if route.request.method=='POST':
                reads.append(1)
                directory.update(status='ready',courses=[course],checked=1,total=1,complete=True)
            route.fulfill(json=directory)
        context.route('**/api/study/courses',courses)
        def load(route):
            loads.append(route.request.post_data_json);route.fulfill(json=state)
        context.route('**/api/study/load',load)
        def control(route):
            commands.append(route.request.post_data_json);route.fulfill(json=state)
        context.route('**/api/study/control',control)
        context.route('**/api/config',lambda route:route.fulfill(json={'llm':{'base_url':'https://fixture.invalid','model':'fixture','has_key':False}}))
        browser_settings={'preferences':{'choice':'system','custom_path':''},'available':{'chrome':True,'edge':True}}
        def browser_route(route):
            if route.request.method=='POST':browser_settings['preferences']=route.request.post_data_json
            route.fulfill(json=browser_settings)
        context.route('**/api/browser-settings',browser_route)
        context.route('**/api/works?**',lambda route:route.fulfill(json={'works':[]}))
        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto('http://127.0.0.1:8765/study')
        page.locator('#load').click();page.locator('#courseChoices button.primary').wait_for()
        assert len(reads)==1
        page.locator('#closePicker').click();page.locator('#load').click()
        page.locator('#courseChoices button.primary').wait_for()
        assert len(reads)==1, 'Old cached results must not trigger a timed reread'
        with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/api/study/courses')):
            page.locator('#reloadCourses').click()
        page.wait_for_function('document.querySelector("#reloadCourses").disabled===false')
        assert len(reads)==2
        page.locator('#closePicker').click();directory['status']='error'
        page.locator('#load').click();page.locator('#courseChoices button.primary').wait_for()
        assert len(reads)==2, 'A failed read must only retry when explicitly refreshed'
        page.locator('#closePicker').click();directory['status']='ready'
        page.locator('#load').click();page.locator('#courseChoices button.primary').wait_for()
        page.locator('#courseChoices button.primary').click()
        page.wait_for_function('!document.querySelector("#coursePicker").open')
        assert loads==[], 'Selecting the same cached course must not reload it'
        assert '沿用' in page.locator('#setupNote').inner_text()
        page.locator('#refresh').click()
        page.wait_for_timeout(200)
        assert commands[-1]['action']=='refresh'
        page.reload();page.locator('#load').click();page.locator('#courseChoices button.primary').wait_for()
        assert len(reads)==2
        page.locator('#closePicker').click()
        page.locator('#openLlmSettings').click()
        assert page.locator('#openLlmSettings').inner_text()=='设置'
        page.locator('#startupTab').click()
        assert page.locator('#startupPanel').is_visible() and not page.locator('#cfgBase').is_visible()
        page.locator('#prefBrowser').select_option('custom')
        assert page.locator('#prefBrowserPath').is_visible()
        page.locator('#prefBrowser').select_option('edge')
        page.locator('#prefCourseNetwork').select_option('direct')
        assert not page.locator('#prefBrowserPath').is_visible()
        page.locator('#prefAutoRead').check();page.locator('#prefStartPage').select_option('study')
        page.locator('#prefLoadingTips').uncheck();page.locator('#saveStartupSettings').click()
        page.wait_for_function('document.querySelector("#startupNotice").textContent.startsWith("已保存")')
        assert browser_settings['preferences']['choice']=='edge'
        assert browser_settings['preferences']['course_network']=='direct'
        assert page.evaluate('getPanelPreferences().auto_read_courses')
        assert not page.evaluate('getPanelPreferences().loading_tips')
        assert len(reads)==2, 'Saving startup settings must not start a read immediately'
        page.locator('#llmSettings').get_by_role('button',name='关闭',exact=True).click()
        directory.update(status='idle',courses=[])
        second=context.new_page();second.on('pageerror',lambda error:errors.append(str(error)))
        second.goto('http://127.0.0.1:8765/')
        second.wait_for_url('**/study');second.wait_for_timeout(600)
        assert len(reads)==3, 'A new window should automatically read once when enabled'
        second.reload();second.wait_for_timeout(400)
        assert len(reads)==3
        second.locator('#openLlmSettings').click();second.locator('#startupTab').click()
        second.wait_for_function('document.querySelector("#prefBrowser").value==="edge"')
        assert second.locator('#prefCourseNetwork').input_value()=='direct'
        assert second.locator('#prefAutoRead').is_checked()
        assert second.locator('#prefStartPage').input_value()=='study'
        assert not errors, errors
        second.screenshot(path=str(ROOT/'本地资料/启动设置界面核对.png'))
        second.set_viewport_size(dict(width=390,height=844))
        assert second.locator('#llmSettings').evaluate('e=>e.getBoundingClientRect().right<=innerWidth')
        second.locator('#llmSettings').get_by_role('button',name='关闭',exact=True).click()
        state['status']='running';dialogs=[]
        second.once('dialog',lambda dialog:(dialogs.append(dialog.message),dialog.dismiss()))
        second.evaluate('window.fixtureReloadMarker=true')
        second.locator('#reloadPage').click();second.wait_for_timeout(200)
        assert len(dialogs)==1 and second.evaluate('window.fixtureReloadMarker')
        browser.close()
    print('PASS: cached reads, manual refresh, cached course reuse, categorized settings, persistence and startup behavior')


if __name__=='__main__':
    main()
