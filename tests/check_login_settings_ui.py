"""Fixture-only verification of both panels' credential settings."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import lxt
from playwright.sync_api import sync_playwright


def main():
    errors=[];saved=[]
    status=dict(configured=False,enabled=False,account_hint='',blocked=False)
    with sync_playwright() as runtime:
        browser=lxt.launch_chromium(runtime,headless=True,standard_automation=True)
        context=browser.new_context(viewport=dict(width=1280,height=1000))
        def route(r):
            path=r.request.url.split('fixture.test')[-1].split('?')[0]
            if path in ('/','/study'):
                r.fulfill(content_type='text/html',body=(ROOT/'panel/static'/('study.html' if path=='/study' else 'index.html')).read_text(encoding='utf-8'))
            elif path.startswith('/study-assets/'):
                name=path.rsplit('/',1)[-1]
                r.fulfill(content_type='text/css' if name.endswith('.css') else 'text/javascript',body=(ROOT/'panel/static'/name).read_text(encoding='utf-8'))
            elif path=='/api/study/login-settings':
                if r.request.method=='POST':
                    payload=r.request.post_data_json;saved.append(payload)
                    status.update(configured=not payload.get('clear'),enabled=bool(payload.get('enabled')),account_hint='fi***nt' if not payload.get('clear') else '')
                r.fulfill(json=status)
            elif path=='/api/config':r.fulfill(json={'llm':{'base_url':'https://fixture.invalid','model':'fixture','has_key':False}})
            elif path=='/api/browser-settings':r.fulfill(json={'preferences':{'choice':'system','custom_path':''},'available':{'chrome':True,'edge':True}})
            elif path=='/api/study':r.fulfill(json=dict(status='idle',loaded=False,course_id='',worker_responsive=True,chapters=[],tasks=[],logs=[],options={},counts={},message='',current='',catalog_complete=False))
            elif path=='/api/works':r.fulfill(json={'works':[]})
            elif path=='/api/jobs':r.fulfill(json={'jobs':[]})
            else:r.fulfill(json={})
        context.route('http://fixture.test/**',route)
        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
        for path in ('/study','/'):
            page.goto('http://fixture.test'+path)
            page.locator('#openLlmSettings').click();page.locator('#startupTab').click()
            page.wait_for_function("!document.querySelector('#saveLoginSettings').disabled")
            page.locator('#loginAccount').fill('fixture-account');page.locator('#loginPassword').fill('fixture-password')
            page.locator('#loginEnabled').check();page.locator('#saveLoginSettings').click()
            page.wait_for_function("document.querySelector('#loginNotice').textContent.includes('加密保存')")
            assert saved[-1]['password']=='fixture-password'
            assert page.locator('#loginPassword').input_value()==''
            assert page.locator('#loginAccount').input_value()==''
            assert 'fixture-password' not in page.evaluate('JSON.stringify({...localStorage})')
            page.locator('#loginPassword').fill('unsaved-value');page.locator('#llmSettings .llm-heading button').click()
            assert page.locator('#loginPassword').input_value()==''
            page.locator('#openLlmSettings').click();page.locator('#startupTab').click()
            page.wait_for_function("!document.querySelector('#clearLoginSettings').disabled")
            page.locator('#clearLoginSettings').click()
            page.wait_for_function("document.querySelector('#loginNotice').textContent.includes('已删除')")
        assert not errors,errors
        (ROOT/'本地资料').mkdir(exist_ok=True)
        page.screenshot(path=str(ROOT/'本地资料/login-settings-ui.png'))
        browser.close()
    print('Both panels passed: save, masked status, clear fields, no localStorage credentials, deletion.')


if __name__=='__main__':main()
