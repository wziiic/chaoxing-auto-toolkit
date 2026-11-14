import re
import time
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import lxt

from study_core import Interrupted, StudyError, clean_url, digest, question_signature


QUESTION_SCRIPT = Path(__file__).with_name("study_questions.js").read_text(encoding="utf-8")
DOM_SCRIPT = Path(__file__).with_name("study_dom.js").read_text(encoding="utf-8").replace("__STUDY_QUESTIONS__", QUESTION_SCRIPT)

# Each new document starts with playback blocked, including dynamically inserted media.
# No player functions are replaced; only an explicit task play grants permission.
MEDIA_GUARD_SCRIPT = """(() => {
    // Some courses pause on a top-document mouseout even while hidden=false
    // and hasFocus()=true. Observe the real event without cancelling it.
    if (window===window.top) document.addEventListener('mouseout', event => {
        if (event.isTrusted && !event.relatedTarget && document.documentElement)
            document.documentElement.setAttribute('data-lxt-pointer-left-at',String(Date.now()));
    },true);
    let lastControl = 0;
    const controls = event => {
        if (event.isTrusted && event.target?.closest?.('video,audio,.video-js')) lastControl = Date.now();
    };
    document.addEventListener('pointerdown', controls, true);
    document.addEventListener('keydown', controls, true);
    for (const type of ['pause','waiting','stalled','error','playing']) {
        document.addEventListener(type, event => {
            const media = event.target;
            if (!media?.matches?.('video,audio')) return;
            const reason = document.hidden ? 'hidden' : Date.now()-lastControl<1500 ? 'user_control' :
                media.getAttribute('data-lxt-playback-allowed')!=='true' ? 'not_authorized' : 'unknown';
            // Passive, bounded diagnostics only. No event or visibility is changed.
            if (type==='pause') media.setAttribute('data-lxt-last-pause',JSON.stringify({reason,time:Date.now(),focus:document.hasFocus()}));
        }, true);
    }
    const guard = event => {
        const media = event.target;
        if (media?.matches?.('video,audio') && media.getAttribute('data-lxt-playback-allowed') !== 'true') media.pause();
    };
    document.addEventListener('play', guard, true);
    document.addEventListener('playing', guard, true);
})()"""

TASK_ROOT_SCRIPT = """identity => Array.from(document.querySelectorAll('[data-study-task-id], .ans-attach-ct')).find(element => { const iframe=element.querySelector('iframe'); let data={};try{data=JSON.parse(iframe?.getAttribute('data')||element.getAttribute('data')||'{}')}catch{};return String(element.getAttribute('data-study-task-id')||element.getAttribute('data-jobid')||element.getAttribute('jobid')||data._jobid||data.jobid||data.objectid||data.workid)===identity}) || null"""


class MediaLoading(StudyError):
    pass


class CourseBrowser:
    navigation_ready_timeout = 45
    assistant_timeout_ms = 8000

    def __init__(self, checkpoint, log):
        self.checkpoint = checkpoint
        self.log = log
        self.runtime = None
        self.browser = None
        self.context = None
        self.page = None
        self.url = None
        self.chapters = []
        self.locations = {}
        self.dialog = False
        self.cancel_dialog = False
        self.last_diagnostic = 0
        self.action_lock = None
        self.headless = True
        self.embedded_receipts = {}

    def perform(self, action):
        with self.action_lock if self.action_lock is not None else nullcontext():
            self.checkpoint()
            return action()

    def ensure_session(self, visible=False):
        from playwright.sync_api import sync_playwright
        if self.page is None or self.page.is_closed() or self.headless != (not visible):
            try:
                storage = self.context.storage_state() if self.context else None
            except Exception:
                storage = None
            self.close()
            self.runtime = sync_playwright().start()
            self.headless = not visible
            self.browser = lxt.launch_chromium(self.runtime, headless=self.headless, standard_automation=True)
            self.context = self.browser.new_context(viewport={"width": 1400, "height": 900}, **({"storage_state": storage} if storage else {}))
            self.context.add_init_script(MEDIA_GUARD_SCRIPT)
            self.context.route(re.compile(r"^https?://robot-lc\.chaoxing\.com/sdk/CxRobotSdkJs\.js(?:\?.*)?$"), self.assistant_script)
            try:
                cookies = [] if storage else [cookie for cookie in lxt.load_cookies_for_browser()
                           if cookie["domain"].lstrip(".").endswith("chaoxing.com")
                           and (cookie.get("expires", -1) == -1 or cookie["expires"] > time.time())]
                if not storage:
                    self.context.add_cookies(cookies)
                self.log("已保留当前登录会话" if storage else "已复用桌面端会话；若失效，请点击打开课程窗口登录后刷新")
            except (Exception, SystemExit):
                self.log("未能读取桌面登录态；请点击打开课程窗口登录，再刷新课程")
            self.page = self.context.new_page()
            self.page.set_default_timeout(4000)
            self.page.on("dialog", self._dialog)
            self.page.on("pageerror", lambda error: self.diagnostic("课程页面脚本报错；如影响播放，请在课程窗口检查，错误未被屏蔽"))
            self.page.on("requestfailed", lambda request: self.diagnostic("课程窗口出现网络请求失败；若媒体缓冲或目录缺失，请检查连接"))

    def assistant_script(self, route):
        # This optional tutor SDK can block HTML parsing for over a minute.
        # Bound only this exact resource; course, quiz and validation requests
        # continue normally. Preserve the original status, headers and body.
        from playwright.sync_api import Error
        try:
            response = route.fetch(timeout=self.assistant_timeout_ms, max_redirects=0)
        except Error:
            route.abort("timedout")
            self.log("课程智能助教附属脚本加载失败或超时，已停止等待；课程任务照常加载，网页智能助教可能暂不可用")
            return
        route.fulfill(response=response)

    def window_state(self, state):
        if self.headless:
            return
        session = self.context.new_cdp_session(self.page)
        try:
            window_id = session.send("Browser.getWindowForTarget")["windowId"]
            session.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": state}})
        finally:
            session.detach()

    def background(self):
        # Reading must never create pages in a headed browser: Chrome can restore
        # even a minimized window when a temporary course-list tab is opened.
        self.ensure_session(visible=False)

    def show_window(self):
        from study_directory import LIST_URL, platform_url
        current = self.page.url if self.page and not self.page.is_closed() else ""
        self.ensure_session(visible=True)
        if self.page.url == "about:blank":
            self.goto(current if platform_url(current) else self.url or LIST_URL)
        self.window_state("normal")
        self.page.bring_to_front()

    def discover_courses(self, publish):
        from study_directory import discover_courses
        self.background()
        return discover_courses(self, publish)

    def resolve_course(self, entry):
        from study_directory import discover_courses
        self.background()
        result = discover_courses(self, lambda **values: None, selected_entry=entry)
        link = next((item['url'] for item in result['courses'] if item['url']), '')
        if not link:
            raise StudyError(result['courses'][0]['reason'] or '未取得所选课程入口，请重试或手动粘贴链接')
        return link

    def recover_link(self, public_url):
        from study_directory import discover_courses
        target = {key.lower(): values[0] for key, values in parse_qs(urlsplit(public_url).query).items()}
        self.background()
        result = discover_courses(self, lambda **values: None, match_course=target)
        link = next((item["url"] for item in result["courses"] if item["url"]), "")
        if not link:
            raise StudyError("历史课程链接无法恢复，请手动读取一次；已保留原任务记录")
        return link

    def start(self, url):
        self.background()
        self.url = url
        self.goto(url)

    def diagnostic(self, message):
        if time.monotonic() - self.last_diagnostic > 15:
            self.last_diagnostic = time.monotonic()
            self.log(message)

    def _dialog(self, dialog):
        self.dialog = True
        if self.cancel_dialog:
            dialog.dismiss()
        else:
            dialog.dismiss()
            self.log("页面弹出确认对话框，已取消；需人工处理后继续")

    def goto(self, url):
        self.checkpoint()
        parts = urlsplit(url)
        if parts.scheme != "https" or not (parts.hostname or "").endswith(".chaoxing.com"):
            raise StudyError("导航目标不是允许的学习通课程页面")
        from playwright.sync_api import TimeoutError as NavigationTimeout
        course_page = parts.path == "/mycourse/studentstudy"
        try:
            response = self.page.goto(url, wait_until="commit" if course_page else "domcontentloaded", timeout=25000)
        except NavigationTimeout as error:
            raise StudyError("课程页面连接超时，请检查系统代理、网络和平台可用性；已保留课程记录") from error
        if response and response.status >= 400:
            raise StudyError("课程导航返回 HTTP " + str(response.status) + "，可能登录失效、网络失败或访问被限制；请在课程窗口检查")
        if course_page:
            expected = {k.lower(): v[0] for k, v in parse_qs(parts.query).items()}
            until = time.monotonic() + self.navigation_ready_timeout
            while True:
                self.checkpoint()
                samples, _ = self.snapshots()
                current = urlsplit(self.page.url)
                actual = {k.lower(): v[0] for k, v in parse_qs(current.query).items()}
                if (current.hostname != parts.hostname or current.path != parts.path
                        or not expected.get('courseid')
                        or any(expected.get(k) and expected[k] != actual.get(k) for k in ('courseid','clazzid','chapterid','knowledgeid'))):
                    raise StudyError("课程页面跳转到了其他课程或登录页面，请检查课程窗口后重新读取")
                main = next((data for frame,data in samples if frame == self.page.main_frame), {})
                if any(chapter.get('id') for chapter in main.get('chapters', [])) and not main.get('catalog_loading'):
                    break
                if time.monotonic() >= until:
                    raise StudyError("课程目录加载超时，页面尚未就绪；请检查系统代理或平台连接后继续，任务未计为完成")
                self.wait(.25)
        self.checkpoint()

    def wait(self, seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.checkpoint()
            self.page.wait_for_timeout(min(200, max(1, int((until - time.monotonic()) * 1000))))

    def snapshots(self):
        self.checkpoint()
        if not self.page or self.page.is_closed():
            raise StudyError("课程浏览器已关闭，请重新读取课程")
        result, failures = [], 0
        for frame in list(self.page.frames):
            self.checkpoint()
            try:
                data = frame.evaluate(DOM_SCRIPT)
                result.append((frame, data))
            except Exception:
                failures += 1
        if not result:
            raise StudyError("所有 frame 均无法读取，可能页面阻塞或浏览器已关闭")
        for frame, data in result:
            if data["guard"]:
                reasons = {"login": "登录失效，请在课程浏览器登录后继续", "verification": "出现验证码或身份验证，已暂停等待人工处理", "session_conflict": "检测到多设备或会话冲突，请先处理冲突", "risk": "页面提示访问限制或风控拦截，已暂停，请人工处理，不会自动重试"}
                raise StudyError(reasons[data["guard"]])
        by_frame = {frame: data for frame, data in result}
        for frame, data in result:
            for task in data["tasks"]:
                if task["kind"] not in ("quiz", "embedded_quiz") or not task["locator"].get("dom"):
                    continue
                root = None
                try:
                    root = frame.evaluate_handle(TASK_ROOT_SCRIPT, task["locator"]["dom"])
                    element = root.as_element()
                    if not element:
                        continue
                    descendants = []
                    for iframe in element.query_selector_all("iframe"):
                        child = iframe.content_frame()
                        if child:
                            descendants.extend(self.descendants(child))
                    states = {by_frame[child]["quiz_platform"] for child in descendants if child in by_frame and by_frame[child]["quiz_platform"] != "unknown"}
                    if len(states) == 1 and task["platform"] == "unknown":
                        task["platform"] = states.pop()
                except Exception:
                    failures += 1
                finally:
                    if root:
                        root.dispose()
        if self.dialog:
            self.dialog = False
            raise StudyError("页面确认弹窗已取消，请人工确认限制条件后继续")
        return result, failures

    def settle(self, content=False):
        previous = None
        until = time.monotonic() + (self.navigation_ready_timeout if content else 4)
        attempt = 0
        while True:
            samples, failures = self.snapshots()
            pending = content and (any(data.get('content_pending') for _,data in samples) or
                (any(data.get('catalog_present') for _,data in samples) and
                 not any(data.get('tasks') or data.get('steps') or data.get('reference_content') for _,data in samples)))
            signature = digest([[data["tasks"], data["steps"]] for frame, data in samples])
            if not pending and signature == previous and attempt >= 2:
                return samples, failures
            if time.monotonic() >= until:
                if pending:
                    raise StudyError("章节内容仍未加载完成，请检查网络或系统代理后继续；尚未判断任务状态，也未计为完成")
                return samples, failures
            previous = signature
            attempt += 1
            self.wait(0.5)

    def catalogue(self):
        samples, failures = self.settle()
        chapters = {}
        complete = False
        for frame, data in samples:
            for chapter in data["chapters"]:
                if chapter["id"]:
                    chapters.setdefault(chapter["id"], chapter)
            complete = complete or data["catalog_complete"]
        if not chapters:
            raise StudyError("未读到稳定章节编号；请确认已登录且课程目录已加载")
        self.chapters = list(chapters.values())
        complete = complete and not failures and not any(data["missing_chapter_ids"] for frame, data in samples)
        return self.chapters, complete

    @staticmethod
    def reference_only(chapter_id, samples, failures):
        if failures or any(data.get('tasks') or data.get('formal_exam') for frame,data in samples):
            return False
        completed = any(chapter.get('completed') for frame,data in samples
                        for chapter in data.get('chapters',[]) if chapter['id']==chapter_id)
        return completed and any(data.get('reference_content') for frame,data in samples)

    @staticmethod
    def reference_resource(raw, chapter_completed):
        # A uniquely matched, unmarked inline image is presentation content,
        # even in a chapter which still has required videos. Media still needs
        # the completed directory signal before it can be treated as optional.
        return bool(raw.get('non_task_candidate') and (chapter_completed or
                    (raw.get('kind')=='image' and raw.get('locator',{}).get('image'))))

    def optional_resource(self, task):
        matches,samples,failures=self.find(task)
        completed=any(chapter.get('completed') for frame,data in samples
                      for chapter in data.get('chapters',[]) if chapter['id']==task['chapter_id'])
        dom=[raw for frame,data,raw in matches if raw.get('locator',{}).get('dom') or raw.get('locator',{}).get('image')]
        return not failures and not any(data.get('formal_exam') for _,data in samples) and bool(dom) and all(self.reference_resource(raw,completed) for raw in dom)

    def inspect_reference(self, task):
        samples, failures = self.enter_chapter(task['chapter_id'])
        return self.reference_only(task['chapter_id'],samples,failures)

    def chapter_url(self, chapter_id):
        parts = urlsplit(self.url)
        query = parse_qs(parts.query)
        query = {key: values for key, values in query.items() if key.lower() not in ("chapterid", "knowledgeid")}
        query["chapterId"] = [chapter_id]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), ""))

    def enter_chapter(self, chapter_id):
        self.goto(self.chapter_url(chapter_id))
        result = self.settle(content=True)
        if self.reveal_tasks():
            result = self.settle(content=True)
        self.pause_media()
        return result

    def reveal_tasks(self):
        # Trigger the site's own lazy loading, including task cards below knowledge maps.
        found = False
        for frame in list(self.page.frames)[:80]:
            self.checkpoint()
            try:
                nodes = frame.locator('.ans-attach-ct,[data-study-task-id]').all()[:120]
                found = found or bool(nodes)
                for node in nodes:
                    self.checkpoint()
                    if node.is_visible():
                        self.perform(lambda: node.scroll_into_view_if_needed(timeout=2000))
                        self.wait(0.12)
            except (StudyError, Interrupted):
                raise
            except Exception:
                continue
        return found

    def select_step(self, step):
        if not step:
            return self.settle()
        for frame in list(self.page.frames):
            try:
                handle = frame.evaluate_handle("""key => {const matches=Array.from(document.querySelectorAll('.prev_white,[data-study-step]')).filter(element => (element.getAttribute('data-study-step') || element.getAttribute('data-num') || (element.innerText || element.textContent || '').trim()) === key); return matches.length===1?matches[0]:null}""", step)
                element = handle.as_element()
                if element:
                    self.checkpoint()
                    self.perform(lambda: element.click(timeout=4000))
                    handle.dispose()
                    self.reveal_tasks()
                    result = self.settle(content=True)
                    self.pause_media()
                    return result
                if not element:
                    handle.dispose()
            except Exception:
                continue
        raise StudyError("学习步骤已替换或不存在，请刷新任务目录")

    def scan_chapter(self, course_id, chapter):
        _, tasks, complete = self.scan(course_id, lambda progress: None, [chapter])
        return tasks, complete

    def scan(self, course_id, report, chapter_subset=None):
        if chapter_subset is None:
            chapters, complete = self.catalogue()
            self.locations = {}
        else:
            chapters, complete = chapter_subset, True
        tasks = {}
        def progress(completed, chapter):
            report({"phase":"chapters", "completed":completed, "total":len(chapters),
                    "chapter":chapter["title"], "task_points":sum(item.get("is_task_point",True) for item in tasks.values()), "active":True})
        for index, chapter in enumerate(chapters[:300]):
            self.checkpoint()
            progress(index, chapter)
            if chapter["locked"]:
                task_id = digest([course_id, chapter["id"], "locked"])
                tasks[task_id] = self.task(task_id, chapter, "locked", "unknown", chapter["reason"] or "章节锁定，需满足平台解锁条件")
                complete = False
                progress(index + 1, chapter)
                continue
            samples, failures = self.enter_chapter(chapter["id"])
            steps = {}
            for frame, data in samples:
                for step in data["steps"]:
                    if step["key"]:
                        if step["key"] in steps and steps[step["key"]] != step:
                            complete = False
                        steps[step["key"]] = step
            if len(steps) > 30:
                complete = False
            batches = [(None, samples, failures)]
            for step in list(steps.values())[:30]:
                if re.search("考试|限次|不可重做", step["title"]):
                    task_id = digest([course_id, chapter["id"], "restricted-step", step["key"]])
                    tasks[task_id] = self.task(task_id, chapter, "unknown", "quiz", "受限或考试步骤未自动进入，请手动打开确认限制")
                    tasks[task_id]["step"] = step["key"]
                    tasks[task_id]["is_task_point"] = False
                    complete = False
                    continue
                samples, failures = self.select_step(step["key"])
                batches.append((step["key"], samples, failures))
            found = False
            for step, samples, failures in batches:
                complete = complete and not failures
                for frame, data in samples:
                    if data["formal_exam"]:
                        complete = False
                    for raw in data["tasks"]:
                        found = True
                        task_id = digest([course_id, chapter["id"], raw["local_id"]])
                        task = self.task(task_id, chapter, raw["platform"], raw["kind"], raw["reason"])
                        task.update(title=raw["title"], local_id=raw["local_id"], step=step, restricted=data["restricted"], formal_exam=data["formal_exam"],
                                    is_task_point=raw.get("is_task_point",True), requirement=raw.get("requirement",""))
                        completed=any(c.get('completed') for _,d in samples for c in d.get('chapters',[]) if c['id']==chapter['id'])
                        if self.reference_resource(raw,completed) and not failures and not any(d.get('formal_exam') for _,d in samples):
                            task.update(is_task_point=False,reference_only=True,platform='not_applicable',status='reference',verified=False,reason='资料已与页面对应且无必做标记，作为参考资料保留')
                        existing = tasks.get(task_id)
                        if existing and bool(existing.get('reference_only')) != bool(task.get('reference_only')):
                            task.update(reference_only=False,is_task_point=True,platform='unknown',reason='不同学习步骤的任务点标记不一致，请在原页面核验')
                            tasks[task_id]=task
                        if existing and existing["platform"] != task["platform"] and task["platform"] != "unknown":
                            task["platform"] = "unknown"
                            task["reason"] = "不同页面的任务状态不一致，需重新核验"
                        if existing and task["platform"] == "unknown" and not task["reason"]:
                            task["platform"] = existing["platform"]
                            task["verified"] = existing["verified"]
                        task["verified"] = task["platform"] == "done"
                        task["status"] = "reference" if task.get("reference_only") else "done" if task["verified"] else "pending"
                        if not existing or task["platform"] != "unknown" or step:
                            tasks[task_id] = task
                        self.locations[task_id] = (chapter["id"], step)
            if not found:
                task_id = digest([course_id, chapter["id"], "unresolved"])
                tasks[task_id] = self.task(task_id, chapter, "unknown", "unknown", "未发现带稳定任务标识的任务点，可能延迟加载、页面版本未支持或没有任务点；不计为完成")
                tasks[task_id]["is_task_point"] = False
                if all(self.reference_only(chapter['id'],sample,errors) for _,sample,errors in batches):
                    tasks[task_id].update(kind='text',reference_only=True,platform='not_applicable',status='reference',reason='平台目录已通过，此页为无任务点说明或明确空内容页，不计入任务点')
                else:
                    complete = False
                self.locations[task_id] = (chapter["id"], None)
            progress(index + 1, chapter)
        if len(chapters) > 300:
            complete = False
        self.pause_media()
        return chapters, list(tasks.values()), complete

    def task(self, task_id, chapter, platform, kind, reason):
        return {"id": task_id, "chapter_id": chapter["id"], "title": chapter["title"], "kind": kind,
                "platform": platform, "status": "done" if platform == "done" else "pending", "reason": reason,
                "url": clean_url(self.chapter_url(chapter["id"])), "progress": {}, "attempts": 0, "verified": platform == "done",
                "is_task_point": platform != "locked", "submission": "", "step": None}

    def activate(self, task):
        self.enter_chapter(task["chapter_id"])
        if task.get("step"):
            self.select_step(task["step"])
        return self.find(task)

    def find(self, task):
        samples, failures = self.snapshots()
        matches = [(frame, data, raw) for frame, data in samples for raw in data["tasks"]
                   if raw["local_id"] == task.get("local_id")]
        if not matches:
            raise StudyError("任务节点已替换或尚未加载，需刷新后重试")
        known = [entry for entry in matches if entry[2]["platform"] != "unknown"]
        statuses = {entry[2]["platform"] for entry in known}
        if len(statuses) > 1:
            raise StudyError("任务页面状态冲突，暂不能判断完成")
        return matches, samples, failures

    def platform(self, task):
        matches, samples, failures = self.find(task)
        statuses = {raw["platform"] for frame, data, raw in matches}
        if "done" in statuses and not (statuses - {"done", "unknown"}):
            return "done"
        return next((state for state in ("locked", "running", "todo") if state in statuses), "unknown")

    def scoped_frames(self, task):
        matches, samples, failures = self.find(task)
        selected = []
        for frame, data, raw in matches:
            if raw["locator"].get("dom"):
                handle = frame.evaluate_handle(TASK_ROOT_SCRIPT, raw["locator"]["dom"])
                element = handle.as_element()
                if element:
                    selected.append((frame, element))
                    for iframe in element.query_selector_all("iframe"):
                        child = iframe.content_frame()
                        if child:
                            selected.extend((descendant, None) for descendant in self.descendants(child))
                if not element:
                    handle.dispose()
        if not selected:
            raise StudyError("无法把任务标识与播放/答题区域唯一关联，需人工处理")
        return selected

    def descendants(self, frame):
        return [frame] + [descendant for child in frame.child_frames for descendant in self.descendants(child)]

    def media(self, task):
        found = []
        for frame, root in self.scoped_frames(task):
            try:
                elements = (root or frame).query_selector_all("video,audio")
                for element in elements:
                    if element.is_visible():
                        found.append(element)
            except Exception:
                continue
            finally:
                if root:
                    root.dispose()
        if not found:
            raise MediaLoading("当前任务的播放器尚未加载，正在等待")
        if len(found) != 1:
            raise StudyError("当前任务没有唯一可见媒体节点，等待加载或人工处理")
        return found[0]

    def media_state(self, task):
        element = self.media(task)
        try:
            state=element.evaluate("""element => {
                let pause={};try{pause=JSON.parse(element.getAttribute('data-lxt-last-pause')||'{}')}catch{}
                return {current:element.currentTime||0,duration:Number.isFinite(element.duration)?element.duration:0,paused:element.paused,ended:element.ended,ready:element.readyState,error:element.error?.code||0,rate:element.playbackRate,connected:element.isConnected,
                    hidden:element.ownerDocument.hidden,focused:element.ownerDocument.hasFocus(),pause_reason:Date.now()-(pause.time||0)<10000?pause.reason:'unknown',
                    playback_allowed:element.getAttribute('data-lxt-playback-allowed')==='true',speed_rejected:/不支持倍速|倍速.*(?:不计|禁止|无效)/.test(element.ownerDocument.body?.innerText||'')};
            }""")
            context=self.page.evaluate("""()=>({pointer_left_recent:Date.now()-Number(document.documentElement?.getAttribute('data-lxt-pointer-left-at')||0)<5000,job_limit:window.hasJobLimit===true,time_limit:window.videoTimeLimit===true})""")
            state.update(context)
            if state['paused'] and state.get('pause_reason')=='unknown' and context['pointer_left_recent']:
                state['pause_reason']='pointer_left_window'
            return state
        finally:
            element.dispose()

    def normal_rate(self, task):
        element = self.media(task)
        try:
            self.perform(lambda: element.evaluate("element=>{element.playbackRate=1}"))
        finally:
            element.dispose()

    def play(self, task, rate):
        self.checkpoint()
        # Scrolling the owning iframe also loads nested players whose placeholder was offscreen.
        for frame, root in self.scoped_frames(task):
            target = None
            try:
                target = root or (frame.frame_element() if frame.parent_frame else None)
                if target and target.is_visible():
                    self.perform(lambda: target.scroll_into_view_if_needed(timeout=3000))
            finally:
                if root:
                    root.dispose()
                elif target:
                    target.dispose()
        self.wait(0.3)
        until = time.monotonic() + self.navigation_ready_timeout
        while True:
            try:
                element = self.media(task)
                break
            except MediaLoading as error:
                if time.monotonic() >= until:
                    raise StudyError("视频任务已识别，但播放器加载超时；请检查网络或系统代理后继续") from error
                self.wait(.3)
        self.perform(lambda: element.scroll_into_view_if_needed(timeout=3000))
        result = self.perform(lambda: element.evaluate("""async (element, rate) => { element.setAttribute('data-lxt-playback-allowed','true'); const menu=Array.from(element.ownerDocument.querySelectorAll('[data-playback-rate],.vjs-menu-item')).map(item=>Number(item.getAttribute('data-playback-rate')||(item.textContent||'').replace(/[^0-9.]/g,''))).filter(value=>value>=0.5&&value<=2);const supported=rate===1||menu.includes(rate); element.playbackRate=supported?rate:1;let timer;try{const ok=await Promise.race([element.play().then(()=>true,()=>false),new Promise(resolve=>{timer=setTimeout(()=>resolve(false),3000)})]);return {ok,rate:element.playbackRate}}finally{clearTimeout(timer)} }""", rate))
        self.checkpoint()
        if not result["ok"]:
            element.evaluate("element => {element.muted=true}")
            self.checkpoint()
            result = self.perform(lambda: element.evaluate("async element => {let timer;try{const ok=await Promise.race([element.play().then(()=>true,()=>false),new Promise(resolve=>{timer=setTimeout(()=>resolve(false),3000)})]);return {ok,rate:element.playbackRate}}finally{clearTimeout(timer)}}"))
        if not result["ok"]:
            raise StudyError("自动播放被浏览器阻止，请在课程窗口手动点击播放后继续")
        if result["rate"] != rate:
            self.log("平台未提供所选倍速，已回退到正常速度")
        element.dispose()

    def popup(self, task):
        for frame, root in self.scoped_frames(task):
            try:
                data = root.evaluate(DOM_SCRIPT) if root else frame.evaluate(DOM_SCRIPT)
                if data["questions"] or data.get('popup'):
                    return True
            finally:
                if root:
                    root.dispose()
        return False

    def pause_media(self):
        if not self.page or self.page.is_closed():
            return
        for frame in list(self.page.frames):
            try:
                frame.evaluate("() => document.querySelectorAll('video,audio').forEach(element=>{element.removeAttribute('data-lxt-playback-allowed');element.pause()})")
            except Exception:
                continue

    def questions(self, task):
        frames = []
        seen = set()
        for frame, root in self.scoped_frames(task):
            if frame in seen:
                continue
            seen.add(frame)
            data = root.evaluate(DOM_SCRIPT) if root else frame.evaluate(DOM_SCRIPT)
            if data["questions"]:
                frames.append((frame, data))
        if len(frames) != 1:
            raise StudyError("题目区域不唯一或未加载，无法安全填写")
        frame, data = frames[0]
        return frame, data

    def feedback(self, task, expected_frame):
        for frame, root in self.scoped_frames(task):
            if frame == expected_frame:
                data = root.evaluate(DOM_SCRIPT) if root else frame.evaluate(DOM_SCRIPT)
                if data["feedback"]:
                    return data["feedback"]
        return ""

    def question_element(self, frame, question, task):
        candidates = []
        for current_frame, root in self.scoped_frames(task):
            try:
                if current_frame != frame:
                    continue
                handle = frame.evaluate_handle("({root,qid}) => (" + QUESTION_SCRIPT + ")(root).locate(qid)", {"root": root, "qid": question["qid"]})
                element = handle.as_element()
                if element:
                    candidates.append(element)
                else:
                    handle.dispose()
            finally:
                if root:
                    root.dispose()
        if len(candidates) != 1:
            for element in candidates:
                element.dispose()
            raise StudyError("任务内题目节点不唯一或已失效")
        return candidates[0]

    def fill(self, task, questions, answers):
        frame, data = self.questions(task)
        expected = {question["qid"]: question_signature(question) for question in questions}
        if {question["qid"]: question_signature(question) for question in data["questions"]} != expected:
            raise StudyError("模型返回前页面题目已变化，旧答案已丢弃")
        for question in data["questions"]:
            self.checkpoint()
            fresh_frame, fresh = self.questions(task)
            if fresh_frame != frame or {item["qid"]: question_signature(item) for item in fresh["questions"]} != expected:
                raise StudyError("填写过程中页面变化，已停止")
            element = self.question_element(frame, question, task)
            if question["type"] == "填空题":
                controls = element.query_selector_all('textarea,input[type="text"],[contenteditable="true"]')
                for control, answer in zip(controls, answers[question["qid"]]):
                    self.checkpoint()
                    self.perform(lambda: control.fill(answer))
            elif question.get("control_mode") in ("choice",'video_choice'):
                for option in question["options"]:
                    self.checkpoint()
                    _, current = self.questions(task)
                    record = next(item for item in current["questions"] if item["qid"] == question["qid"])
                    wanted = option["data"] in answers[question["qid"]]
                    picked = option["data"] in record["checked"]
                    if wanted == picked or (question["type"] != "多选题" and not wanted):
                        continue
                    handle = frame.evaluate_handle("({root,qid,key}) => (" + QUESTION_SCRIPT + ")(root).choice(qid,key)", {"root": element, "qid": question["qid"], "key": option["data"]})
                    control = handle.as_element()
                    if not control:
                        handle.dispose()
                        raise StudyError("自定义选项节点已变化，未继续填写")
                    try:
                        self.perform(lambda: control.click(timeout=4000))
                    finally:
                        handle.dispose()
            else:
                for control in element.query_selector_all('input[type="radio"],input[type="checkbox"]'):
                    self.checkpoint()
                    picked = control.get_attribute("value") in answers[question["qid"]]
                    if control.get_attribute("type") == "checkbox" or picked:
                        self.perform(lambda: control.set_checked(picked))
            element.dispose()
        fresh_frame, fresh = self.questions(task)
        if fresh_frame != frame or {item["qid"]: question_signature(item) for item in fresh["questions"]} != expected:
            raise StudyError("填写后题目变化，不能确认结果")
        for question in fresh["questions"]:
            actual = question["values"] if question["type"] == "填空题" else sorted(question["checked"])
            wanted = answers[question["qid"]] if question["type"] == "填空题" else sorted(answers[question["qid"]])
            if actual != wanted:
                raise StudyError("控件回读不一致，未执行提交")
            if question.get("has_stored_answer") and sorted(question.get("stored_choices") or []) != sorted(answers[question["qid"]]):
                raise StudyError("页面选项已选中，但平台答案字段未同步；未执行提交")

    def confirm_submission(self, task, questions=None, allow_restricted=False):
        """Only known course confirmation dialogs in the question frame or its ancestors."""
        self.checkpoint()
        frame, data = self.questions(task)
        if data["formal_exam"]:
            raise StudyError("正式考试必须由用户手动进入并处理")
        containers = []
        ancestor = frame
        while ancestor:
            for selector in ("#confirmSubWin", "#workpop"):
                for dialog in ancestor.locator(selector).all():
                    if dialog.is_visible():
                        containers.append((selector, dialog))
            ancestor = ancestor.parent_frame
        if not containers:
            return False
        if len(containers) != 1:
            raise StudyError("发现多个确认弹窗，已暂停，需人工核对")
        selector, confirmation = containers[0]
        prompt_node = confirmation.locator("#popcontent" if selector == "#workpop" else "#tipContent")
        prompt = prompt_node.inner_text() if prompt_node.count() == 1 else confirmation.inner_text()
        if re.search("未(?:全部)?(?:作答|完成)|未答|漏答|验证码|身份验证|考试", prompt):
            raise StudyError("提交确认包含未答题或验证限制，已暂停，需人工处理")
        if re.search("限次|不可重做|不可撤回|剩余.*次数", prompt) and not allow_restricted:
            raise StudyError("提交确认显示重要限制，需要单独授权后人工核验本次提交状态")
        if not re.search(r"(?:确认|确定|是否).*提交|提交.*[?？]", prompt):
            raise StudyError("弹窗内容不是明确的提交确认，已暂停，需人工处理")
        _, current = self.questions(task)
        if questions is not None and sorted(question_signature(item) for item in questions) != sorted(question_signature(item) for item in current["questions"]):
            raise StudyError("确认提交前题目变化，已取消后续提交动作")
        confirm = (confirmation.locator("#popok") if selector == "#workpop" else
                   confirmation.get_by_role("button", name=re.compile(r"^(?:确定|确认提交|提交)$")))
        if (confirm.count() != 1 or not confirm.is_visible() or not confirm.is_enabled()
                or not re.fullmatch(r"确定|确认提交|提交", confirm.inner_text().strip())):
            raise StudyError("未找到唯一确认提交控件")
        self.perform(lambda: confirm.click(timeout=4000))
        return True

    def embedded_key(self, task, questions):
        return task['id'], tuple(sorted(question_signature(q) for q in questions))

    def embedded_feedback(self, task, questions):
        receipt=self.embedded_receipts.get(self.embedded_key(task,questions))
        if receipt:return receipt
        expected=sorted(question_signature(q) for q in questions)
        for frame,root in self.scoped_frames(task):
            try:
                data=root.evaluate(DOM_SCRIPT) if root else frame.evaluate(DOM_SCRIPT)
                if sorted(question_signature(q) for q in data['questions'])==expected and data['feedback'] in ('submitted','graded','incorrect'):
                    return data['feedback']
            finally:
                if root:root.dispose()
        return ''

    def continue_embedded(self, task, questions):
        if self.embedded_feedback(task,questions) not in ('submitted','graded'):
            raise StudyError('弹题尚无明确提交结果，不能点击继续')
        expected=sorted(question_signature(q) for q in questions)
        for frame,root in self.scoped_frames(task):
            try:
                data=root.evaluate(DOM_SCRIPT) if root else frame.evaluate(DOM_SCRIPT)
                if not data['questions']:continue
                if sorted(question_signature(q) for q in data['questions'])!=expected:
                    return  # A different popup will be handled before playback.
                if any(q.get('native_video') for q in questions):
                    buttons=[b for b in (root or frame).query_selector_all('.ans-videoquiz #videoquiz-continue') if b.is_visible()]
                    try:
                        if len(buttons)==1 and buttons[0].is_enabled():
                            self.perform(lambda:buttons[0].click(timeout=4000))
                    finally:
                        for button in buttons:button.dispose()
            finally:
                if root:root.dispose()
        for _ in range(15):
            self.wait(.2)
            found=False
            for frame,root in self.scoped_frames(task):
                try:
                    data=root.evaluate(DOM_SCRIPT) if root else frame.evaluate(DOM_SCRIPT)
                    found=found or any(question_signature(q) in expected for q in data['questions'])
                finally:
                    if root:root.dispose()
            if not found:return
        raise StudyError('弹题已提交，但原弹窗未关闭；请在课程窗口核查，不会强制恢复播放')

    def submit(self, task, questions=None, allow_restricted=False):
        self.checkpoint()
        frame, data = self.questions(task)
        if questions is not None and sorted(question_signature(item) for item in questions) != sorted(question_signature(item) for item in data["questions"]):
            raise StudyError("提交前题目已变化，未点击提交")
        if data["formal_exam"]:
            raise StudyError("正式考试必须由用户手动进入并处理")
        if data['restricted'] and not allow_restricted:
            raise StudyError('提交前发现限次或不可重做限制，需先单独授权')
        if any(q.get('native_video') for q in data['questions']):
            return self.submit_embedded(task,frame,data['questions'])
        if self.confirm_submission(task, questions, allow_restricted):
            return
        candidates = frame.evaluate_handle("""() => Array.from(document.querySelectorAll('button,a,input[type="submit"]')).filter(element=>element.getClientRects().length&&/^(?:提交|提交答案|提交作业|提交测验|确认提交)$/.test((element.innerText||element.textContent||element.value||'').trim()))""")
        properties = candidates.get_properties()
        buttons = [handle.as_element() for key, handle in properties.items() if key.isdigit() and handle.as_element()]
        if len(buttons) != 1:
            candidates.dispose()
            raise StudyError("未找到唯一明确的提交按钮，请人工操作")
        self.checkpoint()
        try:
            self.perform(lambda: buttons[0].click(timeout=4000))
        finally:
            for button in buttons:
                button.dispose()
            candidates.dispose()
        for attempt in range(30):
            self.wait(0.3)
            if frame.is_detached():
                return
            try:
                outcome = frame.evaluate(DOM_SCRIPT)
                if outcome["quiz_platform"] == "done" or outcome["feedback"] in ("submitted", "graded"):
                    return
            except Exception:
                continue
            if self.confirm_submission(task, questions, allow_restricted):
                return
        raise StudyError("提交后未出现可识别的确认窗口或平台反馈；保留提交意图，请人工核对，避免重复提交")

    def submit_embedded(self, task, frame, questions):
        if len(questions)!=1 or not questions[0].get('native_video'):
            raise StudyError('视频弹题区域不唯一，请在原页面核查')
        root=self.question_element(frame,questions[0],task)
        key=self.embedded_key(task,questions)
        self.embedded_receipts.pop(key,None)
        metadata=root.evaluate("""e=>{const cmp=window.Ext?.getCmp?.(e.id);return {url:cmp?.validationUrl2||'',event:cmp?.renderData?.resourceId||''}}""")
        def capture(response):
            try:
                if not metadata['url'] or not metadata['event'] or response.request.frame!=frame:return
                from urllib.parse import urljoin
                wanted=urlsplit(urljoin(frame.url,metadata['url']));actual=urlsplit(response.url)
                if (wanted.scheme,wanted.netloc,wanted.path)!=(actual.scheme,actual.netloc,actual.path):return
                if parse_qs(actual.query).get('eventid',[''])[0]!=str(metadata['event']) or response.status!=200:return
                result=response.json()
                if result.get('status') is True and isinstance(result.get('isRight'),bool):
                    self.embedded_receipts[key]='graded' if result['isRight'] else 'incorrect'
            except Exception:return
        self.page.on('response',capture)
        try:
            buttons=root.query_selector_all('#videoquiz-submit')
            if len(buttons)!=1 or not buttons[0].is_visible() or not buttons[0].is_enabled():
                raise StudyError('未找到唯一的视频弹题提交按钮')
            try:self.perform(lambda:buttons[0].click(timeout=4000))
            finally:
                for button in buttons:button.dispose()
            for _ in range(30):
                self.wait(.3)
                if self.embedded_feedback(task,questions):return
            raise StudyError('弹题提交后缺少明确反馈；保留提交意图，不重复提交')
        finally:
            self.page.remove_listener('response',capture)
            root.dispose()

    def scroll_reading_area(self, task):
        moved = False
        for frame, root in self.scoped_frames(task):
            try:
                # Only inspect the task card and its descendant frames, never the course sidebar.
                target = root or frame.locator('body')
                result = self.perform(lambda: target.evaluate("""r => {
                    const doc=r.ownerDocument;
                    const visible=e=>e.clientHeight>0&&e.getClientRects().length>0;
                    const candidates=Array.from(r.querySelectorAll('*')).filter(e=>
                        visible(e)&&e.clientHeight>=80&&e.scrollHeight>e.clientHeight+2&&
                        /auto|scroll/.test(getComputedStyle(e).overflowY)&&
                        !e.closest('textarea,[contenteditable=true],nav,[role=navigation]'));
                    if(r.tagName==='BODY'&&visible(doc.scrollingElement))candidates.push(doc.scrollingElement);
                    let moved=false;
                    for(const e of new Set(candidates)){
                        if(e.scrollHeight<=e.clientHeight+2)continue;
                        const before=e.scrollTop;
                        e.scrollTo({top:e.scrollHeight,behavior:'instant'});
                        moved=moved||e.scrollTop>before+1;
                    }
                    return {moved};
                }"""))
                moved = moved or result['moved']
            finally:
                if root:
                    root.dispose()
        return moved

    def open_link(self, task, before_open):
        frames=self.scoped_frames(task)
        popup=None
        previous_pages=set(self.context.pages)
        try:
            candidates=[]
            for frame,root in frames:
                if not re.fullmatch(r'/ananas/modules/(?:zt/)?hyperlink/index(?:-pc)?\.html',urlsplit(frame.url).path):continue
                links=frame.locator('a#hyperlink')
                if links.count()==1 and links.is_visible():candidates.append(links)
            if len(candidates)!=1:
                raise StudyError('无法唯一定位本任务的原生链接入口，请在原页面打开链接')
            link=candidates[0]
            href=link.get_attribute('href') or ''
            from urllib.parse import urljoin
            if urlsplit(urljoin(link.evaluate('e=>e.ownerDocument.location.href'),href)).scheme not in ('http','https'):
                raise StudyError('链接入口不是可正常打开的网页地址，请人工检查')
            if link.get_attribute('target')!='_blank':
                raise StudyError('此链接会替换当前课程页面，暂需人工打开以保留队列上下文')
            before_open()
            # Use the actual card click so the site's own reading handler runs.
            with self.context.expect_page(timeout=6000) as opened:
                self.perform(lambda:link.click(timeout=4000))
            popup=opened.value
            self.checkpoint()
            popup.wait_for_load_state('domcontentloaded',timeout=15000)
            self.checkpoint()
            if urlsplit(popup.url).scheme not in ('http','https'):
                raise StudyError('链接未能打开正常网页，请在原页面核查')
            self.log('已通过任务卡片打开链接；继续核验课程任务状态')
        finally:
            for opened_page in set(self.context.pages)-previous_pages:
                if not opened_page.is_closed():opened_page.close()
            if self.page and not self.page.is_closed():self.page.bring_to_front()
            for frame,root in frames:
                if root:root.dispose()

    def read_step(self, task, elapsed=0, remaining=600):
        frames = self.scoped_frames(task)
        try:
            for frame, root in frames:
                data = root.evaluate(DOM_SCRIPT) if root else frame.evaluate(DOM_SCRIPT)
                if data['questions'] or data['media']:
                    raise StudyError('阅读区域出现题目或媒体，不能按纯阅读任务处理，请刷新任务类型')
                if not data['read_requirement']:
                    continue
                requirement = data['read_requirement']
                duration = re.search(r"(\d+)\s*(秒|分钟|分)", requirement)
                seconds = int(duration[1]) * (60 if duration[2] != '秒' else 1) if duration else 0
                if not re.search('每页|每张', requirement):
                    seconds = max(0, seconds - elapsed)
                timer = re.search(r"剩余\s*(\d+)\s*秒", data['read_timer'])
                if timer:
                    seconds = max(seconds, int(timer[1]))
                if seconds > remaining:
                    raise StudyError('页面要求的剩余阅读时间超过本轮观察上限，请手动继续阅读')
                self.wait(seconds)
                if task['kind'] == 'document':
                    area=root or frame
                    button=area.get_by_role('button',name=re.compile(r'^(?:下一页|下一张)$'))
                    if button.count()==1 and button.is_visible() and button.is_enabled():
                        self.perform(lambda:button.click(timeout=4000))
                        return True
        finally:
            for frame, root in frames:
                if root:
                    root.dispose()
        # Native scroll events let the site's own reader track its normal completion rules.
        # Recheck on subsequent calls because reaching the bottom can load more pages.
        return self.scroll_reading_area(task)

    def close(self):
        self.embedded_receipts.clear()
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self.runtime:
                self.runtime.stop()
            self.runtime = self.browser = self.context = self.page = None
