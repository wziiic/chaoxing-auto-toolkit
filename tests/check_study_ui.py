import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lxt
from playwright.sync_api import sync_playwright


def main():
    state = {"course_id": "fixture-only", "url": "", "status": "ready", "message": "页面夹具核验：没有连接真实课程", "worker_responsive": True, "loaded": True,
             "catalog_complete": False, "current": "", "logs": [{"time": "12:00:00", "message": "夹具任务已加载"}], "options": {},
             "counts": {"discovered": 2, "verified": 0, "failed": 0, "manual": 1, "waiting_verification": 0},
             "chapters": [{"id": "101", "title": "第一章 · 页面夹具", "parent_id": ""}],
             "tasks": [{"id": "fixture-video", "chapter_id": "101", "title": "视频任务 · 不连接真实课程", "kind": "video", "platform": "todo", "status": "pending", "reason": "", "submission": "", "progress": {}, "is_task_point": True},
                       {"id": "fixture-quiz", "chapter_id": "101", "title": "章节练习 · 模拟数据", "kind": "quiz", "platform": "unknown", "status": "manual", "reason": "平台状态尚未确认", "submission": "", "progress": {}, "is_task_point": True},
                       {"id":"fixture-discussion","chapter_id":"101","title":"讨论 · 模拟数据","kind":"discussion","platform":"todo","status":"pending","reason":"","submission":"","is_task_point":False}]}
    config = {"llm": {"base_url": "https://fixture.invalid", "model": "fixture-model", "has_key": False, "api_key_masked": ""}}
    commands = []
    loads = []
    settings = []
    discussions = []
    errors = []
    with sync_playwright() as runtime:
        browser = lxt.launch_chromium(runtime, headless=True, standard_automation=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1050})
        context.route('**/study-assets/header.css',lambda route:route.fulfill(content_type='text/css',body=(ROOT/'panel/static/header.css').read_text(encoding='utf-8')))
        context.route('**/api/study', lambda route: route.fulfill(json=state))
        course_link='https://mooc1.chaoxing.com/mycourse/studentstudy?courseId=111&clazzid=222&chapterId=333&enc=fixture-secret'
        directory={'status':'ready','courses':[{'id':'c1','title':'英语 · 未完成','platform':'todo','completed':1,'total':3,'url':course_link,'reason':''},
                    {'id':'c2','title':'<img src=x onerror=alert(1)>','platform':'unknown','completed':None,'total':None,'url':'','reason':'状态未知'},
                    {'id':'c3','title':'已完成的课程','platform':'done','completed':3,'total':3,'url':course_link,'reason':''}],
                   'checked':3,'total':3,'complete':True,'message':'夹具课程列表；没有连接真实账号'}
        context.route('**/api/study/courses',lambda route:route.fulfill(json=directory))
        def load(route):
            loads.append(route.request.post_data_json)
            state['input_url']=loads[-1]['url']
            state['status']='scanning'
            state['read_progress']={'completed':1,'total':4,'active':True,'phase':'chapters','chapter':'夹具章节','task_points':2}
            route.fulfill(json=state)
        context.route('**/api/study/load',load)
        config_saves=[]
        def config_route(route):
            if route.request.method=='POST':
                payload=route.request.post_data_json;config_saves.append(payload)
                config['llm'].update(base_url=payload['llm_base_url'],model=payload['llm_model'])
                route.fulfill(json={'ok':True})
            else:route.fulfill(json=config)
        context.route('**/api/config',config_route)
        context.route('**/api/testllm',lambda route:route.fulfill(json={'ok':True,'model':config['llm']['model'],'latency':0.1}))
        context.route('**/study-assets/llm-settings.js',lambda route:route.fulfill(content_type='text/javascript',body=(ROOT/'panel/static/llm-settings.js').read_text(encoding='utf-8')))
        context.route('**/api/works?**', lambda route: route.fulfill(json={"works": []}))
        def control(route):
            payload = route.request.post_data_json
            if payload['action'] in ('discussion','discussion_settings'):
                discussions.append(payload)
                state['tasks'][2]['discussion_draft']=payload['discussion_text']
                if payload['action']=='discussion':state['tasks'][2]['discussion_state']='published' if payload['publish_discussion'] else 'draft'
                route.fulfill(json=state)
                return
            if payload['action']=='settings':
                settings.append(payload)
                state['options']={k:v for k,v in payload.items() if k not in ('action','task_ids')}
                state['selection']=payload['task_ids']
                route.fulfill(json=state)
                return
            commands.append(payload)
            state['status'] = 'paused' if payload['action'] == 'pause' else 'running'
            if payload['action']=='reset_submission':
                state['status']='ready'
                state['tasks'][1].update(submission='',status='pending')
                state['selection']=['fixture-quiz']
            route.fulfill(json=state)
        context.route('**/api/study/control', control)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:8765/')
        page.wait_for_function("document.querySelector('#cfgBase').value==='https://fixture.invalid'")
        assert page.get_by_role('navigation', name='页面导航').count() == 1
        assert page.get_by_role('button', name='课程学习', exact=True).count() == 0
        assert page.locator('nav a[aria-current="page"]').inner_text() == '作业面板'
        nav_boxes="() => Array.from(document.querySelectorAll('header nav a')).map(e=>{const r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height]})"
        desktop_nav=page.evaluate(nav_boxes)
        header_controls=page.locator('#reloadPage').bounding_box(),page.locator('#openLlmSettings').bounding_box()
        page.get_by_role('link', name='课程学习', exact=True).click()
        page.wait_for_function("document.querySelector('#discovered').textContent==='2'")
        assert page.evaluate(nav_boxes)==desktop_nav, 'Desktop navigation moved between pages'
        assert (page.locator('#reloadPage').bounding_box(),page.locator('#openLlmSettings').bounding_box())==header_controls
        page.locator('#openLlmSettings').click()
        page.wait_for_function("!document.querySelector('#saveLlmSettings').disabled")
        page.locator('#cfgModel').fill('fixture-updated-model')
        page.locator('#cfgKey').fill('fixture-only-key')
        page.locator('#saveLlmSettings').click()
        page.wait_for_function("document.querySelector('#llmNotice').textContent.includes('设置已保存')")
        assert config_saves[-1]['llm_model']=='fixture-updated-model'
        assert page.locator('#cfgKey').input_value()==''
        assert 'fixture-updated-model' in page.locator('#modelInfo').inner_text()
        page.locator('#testLlmSettings').click()
        page.wait_for_function("document.querySelector('#llmNotice').textContent.includes('连接成功')")
        page.locator('#llmSettings').get_by_role('button',name='关闭',exact=True).click()
        page.locator('header h1 small').evaluate("e=>e.textContent='很长的副标题变化也不会推动导航按钮'")
        assert page.evaluate(nav_boxes)==desktop_nav
        page.locator('#load').click()
        page.locator('#courseChoices .course-choice').first.wait_for()
        assert page.locator('#courseChoices .course-choice').count()==1
        assert page.locator('#courseFilter').input_value()=='todo'
        picker_box=page.locator('#coursePicker').bounding_box()
        page.screenshot(path=str(ROOT/'本地资料/课程选择弹窗核对.png'))
        page.locator('#courseFilter').select_option('all')
        assert page.locator('#courseChoices .course-choice').count()==3
        assert page.locator('#coursePicker').bounding_box()==picker_box, 'Filtering must not resize or move the modal'
        page.locator('#courseChoices').evaluate("e=>{for(let i=0;i<25;i++)e.append(e.firstElementChild.cloneNode(true));e.scrollTop=150}")
        assert page.locator('#courseChoices').evaluate('e=>e.scrollTop>0&&e.scrollHeight>e.clientHeight')
        assert page.locator('#coursePicker').bounding_box()==picker_box
        assert page.locator('#courseChoices img').count()==0
        page.locator('#courseSearch').fill('英语')
        assert page.locator('#courseChoices .course-choice').count()==1
        page.get_by_role('button',name='选择并读取',exact=True).click()
        page.wait_for_function("!document.querySelector('#coursePicker').open")
        assert page.locator('#courseUrl').input_value()==course_link
        assert loads==[{'url':course_link}]
        assert not commands
        page.wait_for_function("Number.parseInt(document.querySelector('#settingsPercent').textContent)>0&&Number.parseInt(document.querySelector('#settingsPercent').textContent)<25")
        page.wait_for_function("document.querySelector('#settingsPercent').textContent==='25%'")
        assert page.locator('#settingsBar').get_attribute('aria-valuenow')=='25'
        assert page.locator('#readLoader').count()==0
        assert page.locator('#settingsTip').inner_text()
        assert page.locator('#settingsBar').get_attribute('aria-valuenow')=='25'
        page.screenshot(path=str(ROOT/'本地资料/课程读取液体进度核对.png'))
        page.emulate_media(reduced_motion='reduce')
        assert page.locator('.liquid-orb').first.evaluate("e=>getComputedStyle(e,'::before').animationName")=='none'
        page.emulate_media(reduced_motion='no-preference')
        page.wait_for_function("Math.abs(document.querySelector('#studySettings').getBoundingClientRect().top)<80")
        assert page.locator('#all').is_disabled()
        page.locator('#autoFill').check()
        page.locator('#autoSubmit').check()
        page.locator('#rate').select_option('1.5')
        assert not commands, 'Changing settings while reading must not start or cancel the scan'
        state['status']='ready'
        state['read_progress'].update(completed=4,active=False,phase='done')
        page.wait_for_function("!document.querySelector('#all').disabled")
        assert '再点击' in page.locator('#setupNote').inner_text()
        assert page.locator('#autoSubmit').is_checked()
        page.locator('input[aria-label="选择 视频任务 · 不连接真实课程"]').check()
        page.get_by_role('button', name='开始选中任务', exact=True).click()
        page.wait_for_function("document.querySelector('#queueState').textContent==='执行中'")
        page.get_by_role('button', name='暂停', exact=True).click()
        page.wait_for_function("document.querySelector('#queueState').textContent==='已暂停'")
        assert commands[0]['task_ids'] == ['fixture-video']
        assert commands[0]['auto_submit'] and commands[0]['auto_fill']
        assert commands[0]['rate']==1.5
        assert commands[-1]['action'] == 'pause'
        state['tasks'][1]['submission']='intent'
        page.wait_for_function("Array.from(document.querySelectorAll('#tasks button')).some(e=>e.textContent==='核实未提交后重试')")
        page.once('dialog',lambda dialog:dialog.dismiss())
        page.get_by_role('button',name='核实未提交后重试',exact=True).click()
        assert commands[-1]['action']=='pause'
        page.once('dialog',lambda dialog:dialog.accept())
        page.get_by_role('button',name='核实未提交后重试',exact=True).click()
        page.wait_for_function("document.querySelector('#queueState').textContent==='待执行'")
        assert commands[-1]['action']=='reset_submission'
        assert commands[-1]['acknowledge_unsubmitted']
        assert commands[-1]['task_id']=='fixture-quiz'
        previous_loads=len(loads)
        page.reload()
        page.wait_for_function("document.querySelector('#discovered').textContent==='2'")
        assert page.locator('#courseUrl').input_value()==course_link
        assert page.locator('#autoSubmit').is_checked()
        assert page.locator('#autoFill').is_checked()
        assert page.locator('#rate').input_value()=='1.5'
        assert page.locator('input[aria-label="选择 章节练习 · 模拟数据"]').is_checked()
        assert page.locator('#all').is_enabled()
        assert len(loads)==previous_loads, 'Reload must not reread the course'
        assert settings
        page.get_by_role('button',name='编辑讨论',exact=True).click()
        page.locator('#discussionText').fill('Fixture discussion body')
        page.locator('#saveDiscussion').click()
        page.wait_for_function("document.querySelector('#discussionNotice').textContent.includes('已保存')")
        assert discussions[-1]['action']=='discussion_settings'
        assert not discussions[-1]['publish_discussion']
        page.locator('#fillDiscussion').click()
        page.wait_for_function("!document.querySelector('#fillDiscussion').disabled")
        assert not discussions[-1]['publish_discussion']
        page.locator('#publishDiscussion').click()
        page.wait_for_function("!document.querySelector('#publishDiscussion').disabled")
        assert discussions[-1]['publish_discussion']
        assert discussions[-1]['discussion_text']=='Fixture discussion body'
        page.locator('#closeDiscussion').click()
        output = ROOT / '本地资料' / '课程学习页面核对.png'
        page.screenshot(path=str(output), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.locator('#load').is_visible()
        page.screenshot(path=str(output.with_name('课程学习移动布局核对.png')), full_page=True)
        page.locator('#load').click()
        page.locator('#coursePicker').wait_for(state='visible')
        page.screenshot(path=str(ROOT/'本地资料/课程选择移动布局核对.png'))
        assert page.locator('#coursePicker').evaluate('e=>e.getBoundingClientRect().right<=innerWidth')
        page.locator('#closePicker').click()
        assert page.locator('nav a[aria-current="page"]').inner_text() == '课程学习'
        mobile_nav=page.evaluate(nav_boxes)
        page.get_by_role('link', name='作业面板', exact=True).click()
        page.wait_for_function("document.querySelector('#cfgBase').value==='https://fixture.invalid'")
        assert page.locator('nav a[aria-current="page"]').inner_text() == '作业面板'
        assert page.evaluate(nav_boxes)==mobile_nav, 'Mobile navigation moved between pages'
        assert not page.locator('#cfgBase').is_visible()
        page.locator('#openLlmSettings').click()
        assert page.locator('#cfgBase').is_visible()
        page.locator('#llmSettings').get_by_role('button',name='关闭',exact=True).click()
        assert not page.locator('#cfgBase').is_visible()
        # Directory-only readiness: start is available before task discovery.
        fixture_video=copy.deepcopy(state['tasks'][0])
        state.update(lazy_loading=True,chapter_states={'101':'unseen'},chapters_checked=0,chapters_total=1,directory_complete=True,
                     tasks=[],counts={'discovered':0,'verified':0,'failed':0,'manual':0,'waiting_verification':0},status='ready',
                     read_progress={'scope':'catalogue','phase':'done','completed':1,'total':1,'active':False})
        page.get_by_role('link',name='课程学习',exact=True).click()
        page.wait_for_function("document.querySelector('#catalogNote').textContent.includes('已检查 0 / 1')")
        assert page.locator('#all').is_enabled()
        assert '尚未检查' in page.locator('#tasks').inner_text()
        previous_loads=len(loads)
        page.locator('#all').click()
        page.wait_for_function("document.querySelector('#queueState').textContent==='执行中'")
        assert len(loads)==previous_loads
        state.update(tasks=[fixture_video],chapter_states={'101':'ready'},chapters_checked=1)
        state['counts']['discovered']=1
        page.wait_for_function("document.querySelector('#discovered').textContent==='1'")
        assert '已检查 1 / 1' in page.locator('#catalogNote').inner_text()
        assert fixture_video['title'] in page.locator('#tasks').inner_text()
        assert not errors, errors
        context.close()
        browser.close()
    print('PASS: navigation, course picker/filter/search/autofill, no XSS or auto-execution, UI controls and desktop/mobile layout (fixture only)')


if __name__ == '__main__':
    main()
