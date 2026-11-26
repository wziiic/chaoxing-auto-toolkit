"""Offline UI check: task packages use their own actions and cached refresh."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import lxt
from playwright.sync_api import sync_playwright


def main():
    calls=[];errors=[]
    works=[dict(workId='activity-test',kind='course_task',title='测试讨论任务包',course='心理健康',
                status='未提交',parsed=True,answered=False,ended=False),
           dict(workId='123',title='普通作业',course='课程',status='未提交',parsed=True,answered=False)]
    with sync_playwright() as runtime:
        browser=lxt.launch_chromium(runtime,headless=True,standard_automation=True)
        context=browser.new_context(viewport={'width':1600,'height':1000})
        def route(r):
            path=r.request.url.split('fixture.test')[-1]
            if path=='/':r.fulfill(content_type='text/html',body=(ROOT/'panel/static/index.html').read_text(encoding='utf8'))
            elif path.startswith('/study-assets/'):
                name=path.split('/')[-1]
                r.fulfill(content_type='text/javascript',body=(ROOT/'panel/static'/name).read_text(encoding='utf8'))
            elif path.startswith('/api/works'):
                calls.append(path);r.fulfill(json={'works':works,'count':2})
            elif path=='/api/config':r.fulfill(json={'llm':{'model':'fixture','base_url':'https://fixture.invalid','has_key':False}})
            elif path=='/api/jobs':
                calls.append(r.request.post_data_json);r.fulfill(json={'jobId':'fixture'})
            elif path=='/api/jobs/fixture':r.fulfill(json={'status':'done','log':[]})
            elif path=='/api/course-activities/activity-test':r.fulfill(json={**works[0],'resources':[{'title':'测试资源','completed':False,'progress':'未回复'}]})
            elif path=='/api/course-activities/stop':calls.append('stop');r.fulfill(json={'message':'已请求停止'})
            else:r.fulfill(json={})
        context.route('http://fixture.test/**',route)
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://fixture.test/')
        row=page.locator('#worksBody tr').filter(has_text='测试讨论任务包')
        row.get_by_role('button',name='自动完成并提交讨论',exact=True).click()
        page.wait_for_function("document.getElementById('statusBar').textContent.includes('共 2')")
        page.wait_for_timeout(1200)
        assert next(c for c in calls if isinstance(c,dict))=={'action':'auto','workIds':['activity-test'],'submit':True}
        assert '/api/works?refresh=false' in calls
        assert calls.count('/api/works?refresh=true')==1
        assert row.get_by_role('button',name='交卷',exact=True).count()==0
        row.get_by_role('button',name='查看任务',exact=True).click()
        page.wait_for_function("document.getElementById('log').textContent.includes('测试资源')")
        row.get_by_role('button',name='停止任务包',exact=True).click()
        page.wait_for_function("document.getElementById('log').textContent.includes('已请求停止')")
        assert 'stop' in calls
        assert not errors,errors
        browser.close()
    print('task package UI passed')


if __name__=='__main__':main()
