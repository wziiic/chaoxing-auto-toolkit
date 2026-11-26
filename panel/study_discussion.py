"""Native discussion navigation/editing. Never calls a platform submission endpoint."""
import re
from urllib.parse import parse_qs, urlsplit

from study_core import StudyError


EDITOR = 'textarea:not([readonly]):not([disabled]),[contenteditable="true"]'
REPLIES = '[data-reply-id],[data-comment-id],.replyContent[id],.reply-content[id],.topicDetail_replyItem[id]'


def frames_for(driver, task, extra_pages, allow_navigation=False):
    from study_core import digest
    frames=[]
    try:
        scoped=driver.scoped_frames(task)
    except StudyError:
        if not allow_navigation:
            raise
        scoped=[]
    key=digest([task['id'],task.get('local_id')])[:20]
    for frame,root in scoped:
        if root:
            try:
                driver.perform(lambda:root.evaluate("(e,key)=>e.setAttribute('data-lxt-discussion-scope',key)",key))
                frames.append((frame,frame.locator('[data-lxt-discussion-scope="'+key+'"]')))
            finally:
                root.dispose()
        elif not any(item[0]==frame for item in frames):
            frames.append((frame,None))
    for page in extra_pages:
        if not page.is_closed():
            frames.extend((frame,None) for frame in page.frames if not any(item[0]==frame for item in frames))
    if not frames and allow_navigation:
        frames=[(frame,None) for frame in driver.page.frames]
    return frames


def area(item):
    return item[1] if item[1] is not None else item[0]


def body(item):
    return item[1].inner_text() if item[1] is not None else item[0].locator('body').inner_text()


def editors_in(frames):
    found = []
    for frame in frames:
        for editor in area(frame).locator(EDITOR).all():
            if editor.is_visible() and editor.is_editable():
                found.append((frame, editor))
    return found


def topic(driver, task):
    """Read only the task's own card/frames, never the surrounding course directory."""
    candidates=[]
    for frame,root in driver.scoped_frames(task):
        try:
            text=(root.inner_text() if root else frame.locator('body').inner_text()).strip()
            lines=[line.strip() for line in text.splitlines() if line.strip() not in ('','讨论','参与讨论','回复','任务点')]
            text='\n'.join(lines).strip()
            if text:candidates.append(text[:3000])
        finally:
            if root:root.dispose()
    if candidates:return candidates[0]
    title=(task.get('title') or '').strip()
    return title if title not in ('','任务点','讨论','discussion','insertbbs') else ''


def discussion(driver, task, text, publish, before_publish):
    text = text.strip()
    if not text or len(text) > 6000:
        raise StudyError("讨论正文须为 1–6000 字，请先编辑正文")
    extras = []
    frames = frames_for(driver, task, extras)
    editors = editors_in(frames)
    if not editors:
        links = []
        for frame in frames:
            for link in area(frame).get_by_text(re.compile(r"^(?:参与讨论|去讨论|进入讨论|发表讨论|我要回复|回复)$")).all():
                if link.is_visible():
                    links.append(link)
        if not links:
            for frame in frames:
                links.extend(card for card in area(frame).locator('.PublicCardBox[onclick]').all() if card.is_visible())
        if len(links) != 1:
            raise StudyError("未找到唯一讨论入口；请打开原页面检查讨论卡片是否已加载或允许回复")
        href = links[0].get_attribute('href') or ''
        if href.startswith(('http:', 'https:')):
            parts = urlsplit(href)
            if parts.scheme != 'https' or not (parts.hostname or '').endswith('.chaoxing.com'):
                raise StudyError("讨论入口超出当前学习通接入范围，需手动处理")
        pages = list(driver.context.pages)
        driver.perform(lambda: links[0].click(timeout=4000))
        opened_reply=False
        for attempt in range(20):
            driver.wait(.2)
            extras = [page for page in driver.context.pages if page not in pages]
            for page in extras:
                parts=urlsplit(page.url)
                if parts.scheme not in ('https','about') or (parts.scheme=='https' and not (parts.hostname or '').endswith('.chaoxing.com')):
                    raise StudyError("讨论跳转目标不在允许范围，已暂停")
            frames = frames_for(driver, task, extras, allow_navigation=True)
            editors = editors_in(frames)
            if editors:
                break
            if not opened_reply:
                reply_entries=[button for current in frames for button in area(current).locator('.replyBtn').all() if button.is_visible()]
                if len(reply_entries)==1:
                    driver.perform(lambda:reply_entries[0].click(timeout=4000))
                    opened_reply=True
    if len(editors) != 1:
        raise StudyError("讨论编辑器未加载或不唯一，需人工处理；未发布正文")
    frame, editor = editors[0]
    if re.search(r'验证码|身份验证|扫码登录|账号密码登录|正式考试', body(frame)):
        raise StudyError("讨论页面需要登录、验证或受限流程，已暂停，未发布")
    native_reply=area(frame).locator('.addReply')
    if native_reply.count()==1:
        # Native topic HTML can appear before its click/input handlers are attached.
        for attempt in range(225):
            if native_reply.evaluate("e=>!!(e.onclick||e.ownerDocument.defaultView.jQuery?._data?.(e,'events')?.click?.length)"):
                break
            driver.wait(.2)
        else:
            raise StudyError('讨论编辑器仍在初始化，尚未填写或提交，请稍后继续')
    driver.perform(lambda: editor.fill(text, timeout=4000))
    # Also notify native character-count handlers through a normal keyboard event.
    if editor.get_attribute('placeholder')=='回复话题':
        driver.perform(lambda:editor.press('End',timeout=4000))
    actual = editor.evaluate("e=>e.value??e.innerText").strip()
    if actual != text:
        raise StudyError("讨论正文回读不一致，未发布")
    if not publish:
        return {'state':'draft','reason':'已跳转并填写正文，尚未发布'}
    native_buttons=[button for button in area(frame).locator('.addReply').all() if button.is_visible() and button.is_enabled()]
    buttons, replies_buttons = [], []
    for candidate in ([] if native_buttons else frames):
        for button in area(candidate).locator('button,a,input[type=submit],[role=button]').all():
            if button.is_visible() and button.is_enabled():
                label=re.sub(r'\s+','',button.inner_text() or button.get_attribute('value') or '')
                if re.fullmatch(r'发布|发表|发表回复|提交',label):buttons.append(button)
                elif label=='回复':replies_buttons.append(button)
    buttons=native_buttons or buttons or replies_buttons
    if len(buttons) != 1:
        raise StudyError("未找到唯一讨论发布按钮，未发布")
    def replies():
        result={}
        for index,current in enumerate(frames):
            for item in area(current).locator(REPLIES).evaluate_all("""items=>items.map(e=>({id:e.getAttribute('data-reply-id')||e.getAttribute('data-comment-id')||e.id,text:(e.querySelector('.replyContent[content]')?.getAttribute('content')??e.innerText).trim()}))"""):
                result[index,item['id']]=item['text']
        return result
    before = replies()
    success = re.compile(r'发布成功|发表成功|回复成功')
    feedback_before = [success.search(body(current)) is not None for current in frames]
    receipts=[]
    def response_received(response):
        if not re.fullmatch(r'/pc/invitation/[^/]+/addReplys',urlsplit(response.url).path):return
        try:
            request=response.request
            if request.method!='POST' or parse_qs(request.post_data or '').get('topic_content')!=[text]:return
            data=response.json()
            if isinstance(data,dict):receipts.append(data)
        except Exception:
            return
    def click():
        before_publish()
        buttons[0].click(timeout=4000)
    def published(reason):
        # Only close windows opened by this operation, after positive confirmation.
        for page in extras:
            if not page.is_closed():page.close()
        return {'state':'published','reason':reason}
    driver.context.on('response',response_received)
    try:
        driver.perform(click)
        for attempt in range(40):
            driver.wait(.3)
            if receipts:
                receipt=receipts[-1]
                if receipt.get('status') is False:
                    return {'state':'rejected','reason':'平台拒绝讨论回复：'+str(receipt.get('msg') or '未提供原因')[:200]}
                if receipt.get('status') is True:
                    reply=(receipt.get('datas') or {}).get('reply')
                    if isinstance(reply,dict) and (reply.get('uuid') or reply.get('id')):
                        return published('平台返回新讨论回复编号，发布已核验')
                    if not receipt.get('datas'):
                        return {'state':'intent','reason':'平台已接收讨论，等待审核；不重复发布'}
            if any(key not in before and content==text for key,content in replies().items()):
                return published('页面出现带新编号的讨论正文，发布已核验')
            if any(not old and success.search(body(current)) for old,current in zip(feedback_before,frames)):
                return published('平台页面明确提示讨论发布成功')
    finally:
        driver.context.remove_listener('response',response_received)
    return {'state':'intent','reason':'已点击发布，平台尚未明确确认；请人工核实，不会自动重复发布'}
