"""Offline browser fixture for course playlist controls and responsive layout."""
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import lxt
from playwright.sync_api import sync_playwright


def main():
    queue=dict(items=[],status='idle',position=0,completed=0,skipped=0,message='')
    state=dict(playlist=queue,status='ready',loaded=False,course_id='fixture',input_url='',worker_responsive=True,
               chapters=[],tasks=[],logs=[],options={},counts={},message='',current='',catalog_complete=False)
    courses=[dict(id=str(n),title='测试课程 '+str(n),url='https://fixture.invalid',platform='todo',completed=0,total=3) for n in (1,2,3)]
    calls=[];errors=[]
    with sync_playwright() as runtime:
        browser=lxt.launch_chromium(runtime,headless=True,standard_automation=True)
        context=browser.new_context(viewport=dict(width=1440,height=1050))
        def route(request):
            path=request.request.url.split('fixture.test')[-1].split('?')[0]
            if path=='/study':request.fulfill(content_type='text/html',body=(ROOT/'panel/static/study.html').read_text(encoding='utf-8'))
            elif path.startswith('/study-assets/'):
                name=path.rsplit('/',1)[-1]
                request.fulfill(content_type='text/css' if name.endswith('.css') else 'text/javascript',body=(ROOT/'panel/static'/name).read_text(encoding='utf-8'))
            elif path=='/api/study':request.fulfill(json=state)
            elif path=='/api/study/courses':request.fulfill(json=dict(status='ready',courses=courses,checked=3,total=3,complete=True,message='模拟课程'))
            elif path=='/api/config':request.fulfill(json={'llm':{'base_url':'https://fixture.invalid','model':'fixture','has_key':False}})
            elif path=='/api/browser-settings':request.fulfill(json={'preferences':{'choice':'system','custom_path':''},'available':{'chrome':True,'edge':True}})
            elif path=='/api/study/control':
                payload=request.request.post_data_json;calls.append(payload)
                if payload['action']=='pause':queue['status']='paused';state['status']='paused';queue['items'][0]['status']='paused'
                elif payload['action']=='stop':queue['status']='stopped';state['status']='stopped'
                request.fulfill(json=state)
            elif path=='/api/study/playlist':
                payload=request.request.post_data_json;calls.append(payload);action=payload['action']
                if action=='add':
                    for identity in payload['course_ids']:
                        queue['items'].append(dict(id=identity,source_id=identity,title='测试课程 '+identity,status='pending',counts={},reason=''))
                    queue['status']='ready'
                elif action=='reorder':queue['items'].sort(key=lambda i:payload['course_ids'].index(i['id']))
                elif action in ('start','resume'):
                    queue['status']='running';queue['items'][0]['status']='running';state['status']='running'
                elif action=='pause':queue['status']='paused';state['status']='paused';queue['items'][0]['status']='paused'
                elif action=='skip':
                    queue['items'][0]['status']='skipped';queue['skipped']=1;queue['status']='running';state['status']='running'
                request.fulfill(json=state)
            else:request.fulfill(json={})
        context.route('http://fixture.test/**',route)
        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto('http://fixture.test/study')
        page.locator('#playlistChoose').click()
        page.get_by_role('checkbox',name='加入队列 测试课程 2',exact=True).check()
        page.locator('#courseSearch').fill('1')
        page.get_by_role('checkbox',name='加入队列 测试课程 1',exact=True).check()
        assert page.locator('#playlistAdd').inner_text()=='加入队列（2）'
        page.locator('#playlistAdd').click()
        page.wait_for_function("document.querySelectorAll('#playlistItems li').length===2")
        assert calls[0]['course_ids']==['2','1']
        assert not page.locator('#all').is_disabled(), 'A new multi-course queue must start without first loading a single course'
        page.get_by_role('button',name='上移 测试课程 1',exact=True).click()
        page.wait_for_function("document.querySelector('#playlistItems strong').textContent==='测试课程 1'")
        assert page.locator('#studySettings #playlistItems').count()==1
        assert page.locator('#playlistStart,#playlistPause,#playlistResume,#playlistStop').count()==0
        page.locator('#all').click()
        page.wait_for_function("document.querySelector('#pause').disabled===false")
        assert page.locator('#all').is_disabled()
        page.locator('#pause').click()
        page.locator('#playlistSkip').wait_for(state='visible')
        page.locator('#resume').click()
        page.wait_for_function("document.querySelector('#pause').disabled===false")
        assert calls[-1]['action']=='resume'
        page.locator('#pause').click()
        page.locator('#playlistSkip').click()
        page.wait_for_function("document.querySelector('#playlistItems').textContent.includes('已跳过（未完成）')")
        assert '完成 0/2' in page.locator('#playlistState').inner_text()
        output=ROOT/'本地资料';output.mkdir(exist_ok=True)
        page.screenshot(path=str(output/'playlist-desktop.png'),full_page=True)
        page.set_viewport_size(dict(width=390,height=844))
        assert page.locator('body').evaluate('e=>e.scrollWidth<=innerWidth')
        page.screenshot(path=str(output/'playlist-mobile.png'),full_page=True)
        assert not errors,errors
        browser.close()
    print('Playlist UI passed: multi-select, selection order, reorder, pause, explicit skip, desktop and mobile layout.')


if __name__=='__main__':main()
