import copy
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from study_browser import CourseBrowser
from study_core import CourseLock, Interrupted, StudyError, StudyStore, course_link, digest, normalize_answers, question_signature, redact, study_prompt


class StudyManager:
    def __init__(self, folder, load_config, call_model, job_callbacks=None, browser_factory=CourseBrowser):
        self.folder = Path(folder)
        self.store = StudyStore(self.folder / "courses.sqlite3")
        self.load_config = load_config
        self.call_model = call_model
        self.jobs = job_callbacks
        self.state = self.store.latest() or {"course_id": "", "url": "", "chapters": [], "tasks": [], "logs": [], "status": "idle", "catalog_complete": False, "current": "", "options": {}}
        self.course_url = ""
        try:
            self.course_url = self.store.saved_link(self.state["course_id"])
        except (StudyError, UnicodeError):
            pass
        self.restored_from_cache = bool(self.state["course_id"])
        if self.state.get("read_progress", {}).get("active"):
            self.state["read_progress"].update(active=False, phase="cancelled")
        if self.state["course_id"]:
            if self.state["status"] in ("running", "preparing", "scanning", "pausing", "stopping"):
                self.state["status"] = "paused"
            self.state["message"] = "已保留上次课程、状态和设置，可直接开始或继续；执行前只核验当前任务"
            for task in self.state["tasks"]:
                if task["status"] == "done" and task["platform"] == "done":
                    task["verified"] = True
                if task["status"] in ("preparing", "running", "waiting_model"):
                    task["status"] = "paused"
        self.lock = threading.RLock()
        self.commands = queue.Queue()
        self.epoch = 0
        self.worker_epoch = 0
        self.closed = False
        self.driver = browser_factory(self.checkpoint, self.log)
        self.driver.action_lock = self.lock
        self.course_lock = None
        self.loaded = bool((self.course_url or self.state["url"]) and self.state["chapters"])
        # Directory links stay in memory; the selected link is saved with user-bound encryption.
        self.directory = {"status": "idle", "courses": [], "checked": 0, "total": 0, "complete": False, "message": "尚未读取账号课程"}
        self.last_checkpoint = time.monotonic()
        self.job_id = None
        self.models = ThreadPoolExecutor(max_workers=1, thread_name_prefix="study-model")
        self.model_future = None
        self.thread = threading.Thread(target=self.worker, daemon=True, name="course-queue")
        self.thread.start()

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
        tasks = [task for task in result["tasks"] if task.get("is_task_point", True)]
        result["counts"] = {"discovered": len(tasks), "verified": sum(task.get("verified", False) for task in tasks),
                            "failed": sum(task["status"] == "failed" for task in tasks),
                            "manual": sum(task["status"] == "manual" for task in tasks),
                            "waiting_verification": sum(task["status"] == "verifying" for task in tasks)}
        result["loaded"] = self.loaded
        result["input_url"] = self.course_url or self.state["url"]
        result["restored_from_cache"] = self.restored_from_cache
        result["directory_loading"] = self.directory_snapshot()["status"] == "loading"
        active = result["status"] in ("preparing", "scanning", "running", "pausing", "stopping")
        result["worker_responsive"] = (not self.closed and self.thread.is_alive()
                                        and (not active or time.monotonic() - self.last_checkpoint < 35))
        return result

    def directory_snapshot(self):
        with self.lock:
            return copy.deepcopy(self.directory)

    def directory_update(self, **values):
        self.checkpoint()
        with self.lock:
            if values.get("status") == "ready":
                values["updated_at"] = time.time()
            self.directory.update(copy.deepcopy(values))

    def persist(self):
        with self.lock:
            self.store.save(self.state)

    def log(self, message):
        message = redact(message)
        with self.lock:
            self.state["logs"].append({"time": time.strftime("%H:%M:%S"), "message": message})
            self.state["logs"] = self.state["logs"][-400:]
        if self.jobs and self.job_id:
            self.jobs[1](self.job_id, message)

    def update(self, task=None, **values):
        self.checkpoint()
        with self.lock:
            if task is None:
                self.state.update(values)
            else:
                task.update(values)
            self.store.save(self.state)

    def checkpoint(self):
        self.last_checkpoint = time.monotonic()
        if self.closed or self.worker_epoch != self.epoch:
            raise Interrupted()

    def wait(self, seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.checkpoint()
            time.sleep(min(0.15, max(0, until - time.monotonic())))

    def command(self, action, payload=None):
        payload = payload or {}
        if action not in ("load", "refresh", "start", "pause", "resume", "stop", "retry", "open", "discover", "reset_submission", "settings", "discussion", "discussion_settings"):
            raise StudyError("未知的课程队列操作")
        with self.lock:
            if action in ("discussion", "discussion_settings"):
                task=next((item for item in self.state['tasks'] if item['id']==payload.get('task_id')),None)
                if not self.loaded or not task or task['kind']!='discussion':
                    raise StudyError('请先读取并选择有效的讨论任务')
                if self.state['status'] in ('running','preparing','scanning','pausing','stopping'):
                    raise StudyError('请先暂停当前队列，再编辑或发布讨论')
                text=payload.get('discussion_text','').strip()
                if not text or len(text)>6000:
                    raise StudyError('请填写 1–6000 字讨论正文')
                task.update(discussion_draft=text,auto_discussion=bool(payload.get('auto_discussion',False)))
                if action=='discussion_settings':
                    if task['auto_discussion'] and task.get('discussion_state')!='published':
                        task.update(status='pending',verified=False)
                    self.persist()
                    return self.snapshot()
            if action == "settings":
                options = self.execution_options(payload)
                self.state["options"] = options
                if "task_ids" in payload:
                    existing = {task["id"] for task in self.state["tasks"]}
                    self.state["selection"] = [key for key in payload["task_ids"] if key in existing]
                self.persist()
                return self.snapshot()
            if action == "load":
                course_link(payload["url"])
            elif action not in ("pause", "stop", "discover", "open") and not self.loaded:
                raise StudyError("尚无可恢复的课程链接，请先手动读取一次课程")
            if action == "open" and payload.get("task_id") and not self.loaded:
                raise StudyError("请先读取课程，再打开具体任务")
            if action == "reset_submission":
                if self.state["status"] in ("running", "scanning", "preparing", "pausing", "stopping"):
                    raise StudyError("请先暂停任务，再核实并重置提交记录")
                if payload.get("acknowledge_unsubmitted") is not True:
                    raise StudyError("必须先在原页面核实尚未提交，明确确认后才能重试")
                task = next((item for item in self.state["tasks"] if item["id"] == payload.get("task_id")), None)
                if not task or task["kind"] != "quiz" or task.get("submission") != "intent":
                    raise StudyError("只能处理有待确认提交意图的章节练习")
            if action in ("start", "resume", "retry") and self.state["status"] in ("running", "scanning", "preparing"):
                raise StudyError("当前队列仍在运行，请先暂停或停止")
            if (action in ("load", "refresh") and self.state["status"] in ("scanning", "preparing")
                    and not (action == "load" and self.directory["status"] == "loading")):
                raise StudyError("正在读取课程，请等待，或先停止")
            if action == "discover":
                if self.state["status"] in ("running", "scanning", "preparing", "pausing", "stopping"):
                    raise StudyError("请先暂停当前任务，再读取账号课程列表")
                payload = {"previous_status": self.state["status"]}
                self.directory.update(status="loading", courses=[], checked=0, total=0, complete=False, message="正在读取当前登录账号的课程…")
            self.epoch += 1
            self.state["status"] = {"pause": "pausing", "stop": "stopping", "load": "preparing", "refresh": "scanning"}.get(action, "preparing")
            self.commands.put((self.epoch, action, payload))
        return self.snapshot()

    def worker(self):
        try:
            while not self.closed:
                try:
                    epoch, action, payload = self.commands.get(timeout=0.3)
                except queue.Empty:
                    continue
                if epoch != self.epoch:
                    continue
                self.worker_epoch = epoch
                try:
                    self.driver.pause_media()
                    if action == "discover":
                        result = self.driver.discover_courses(self.directory_update)
                        self.directory_update(status="ready", **result)
                        continue
                    if action in ("pause", "stop"):
                        for task in self.state["tasks"]:
                            if task["status"] in ("preparing", "running", "waiting_model"):
                                task["status"] = "paused"
                        self.update(status="paused" if action == "pause" else "stopped")
                        if action == "stop" and self.course_lock:
                            self.course_lock.close()
                            self.course_lock = None
                        self.log("队列已暂停；只有点击继续才会恢复" if action == "pause" else "队列已停止，旧响应和延迟动作已失效")
                        continue
                    if action != "load" and not self.course_lock and (action != "open" or self.loaded):
                        self.course_lock = CourseLock(self.folder / "locks", self.state["course_id"])
                        try:
                            self.course_lock.acquire()
                        except Exception:
                            self.course_lock = None
                            raise
                    if self.jobs:
                        self.job_id = self.jobs[0]("study", "课程学习")
                    if action != "load":
                        if self.loaded and not self.course_url and (action != "open" or payload.get("task_id")):
                            self.recover_course_link()
                        self.driver.url = self.course_url or getattr(self.driver, "url", None)
                    if action == "load":
                        self.load(payload["url"])
                    elif action == "refresh":
                        self.driver.start(self.course_url)
                        self.refresh()
                    elif action == "reset_submission":
                        self.ensure_course_page()
                        self.reset_submission(payload)
                    elif action == "discussion":
                        task=next(item for item in self.state['tasks'] if item['id']==payload['task_id'])
                        self.driver.show_window()
                        self.driver.activate(task)
                        self.handle_discussion(task,payload['discussion_text'],payload.get('publish_discussion',False))
                        self.update(status='paused',message=task['reason'])
                    elif action == "open":
                        self.driver.show_window()
                        task = next((task for task in self.state["tasks"] if task["id"] == payload.get("task_id")), None)
                        if task:
                            self.driver.enter_chapter(task["chapter_id"])
                            if task.get("step"):
                                self.driver.select_step(task["step"])
                        self.driver.page.bring_to_front()
                        self.driver.pause_media()
                        self.update(status="paused")
                    else:
                        self.run(action, payload)
                    if self.jobs and self.job_id:
                        self.jobs[2](self.job_id, result={"course_id": self.state["course_id"], "status": self.state["status"]})
                except Interrupted:
                    self.driver.pause_media()
                    if self.jobs and self.job_id:
                        self.jobs[2](self.job_id, result={"status": "cancelled"})
                except Exception as error:
                    self.driver.pause_media()
                    message = redact(str(error)) if isinstance(error, StudyError) else "浏览器或网络操作失败（" + type(error).__name__ + "）；请检查课程窗口后刷新"
                    if action == "discover":
                        with self.lock:
                            self.directory.update(status="error", message=message)
                    self.log(message)
                    with self.lock:
                        if epoch == self.epoch:
                            self.state.update(status="paused", message=message)
                    if self.jobs and self.job_id:
                        self.jobs[2](self.job_id, error=message)
                finally:
                    if action in ("load", "refresh") and self.state.get("read_progress",{}).get("active"):
                        with self.lock:
                            self.state["read_progress"].update(active=False, phase="cancelled" if epoch!=self.epoch else "failed")
                    if action == "discover":
                        with self.lock:
                            if self.directory["status"] == "loading":
                                self.directory.update(status="cancelled", message="课程列表读取已取消，可重新读取")
                            if epoch == self.epoch:
                                self.state["status"] = payload["previous_status"]
                    self.job_id = None
                    self.persist()
        finally:
            self.driver.close()
            if self.course_lock:
                self.course_lock.close()

    def load(self, url):
        course_id, public_url = course_link(url)
        self.store.save_link(course_id, url)
        if not self.course_lock or self.state["course_id"] != course_id:
            new_lock = CourseLock(self.folder / "locks", course_id)
            new_lock.acquire()
            if self.course_lock:
                self.course_lock.close()
            self.course_lock = new_lock
        previous = self.store.course(course_id) or {}
        with self.lock:
            self.course_url = url
            self.state.update(course_id=course_id, url=public_url, status="scanning", chapters=[], tasks=previous.get("tasks", []),
                              selection=previous.get("selection", []), options=previous.get("options", {}), catalog_complete=False,
                              current="", message="正在逐章节读取任务点")
        self.loaded = False
        self.driver.start(url)
        self.loaded = True
        self.refresh()

    def ensure_course_page(self):
        page = self.driver.page
        if page is None or (hasattr(page, "is_closed") and page.is_closed()):
            self.driver.start(self.course_url)

    def recover_course_link(self):
        # One-time migration of legacy redacted links, without scanning task pages.
        self.log("正在恢复当前课程入口链接，沿用历史目录，不重新扫描任务")
        url = self.driver.recover_link(self.state["url"])
        if course_link(url)[0] != self.state["course_id"]:
            raise StudyError("当前会话的课程身份与缓存不符，请手动读取课程；原任务记录已保留")
        self.store.save_link(self.state["course_id"], url)
        self.course_url = url

    def refresh(self):
        self.update(status="scanning", message="目录中的每个学习步骤都会单独读取；不会仅按章节统计完成",
                    read_progress={"phase":"catalogue","completed":0,"total":None,"active":True,"chapter":"正在定位目录"})
        previous = {task["id"]: task for task in self.state["tasks"]}
        chapters, tasks, complete = self.driver.scan(self.state["course_id"], lambda progress: self.update(current="读取：" + progress["chapter"],read_progress=progress))
        for task in tasks:
            old = previous.get(task["id"], {})
            task["attempts"] = old.get("attempts", 0)
            task["submission"] = old.get("submission", "")
            task["action_finished"] = old.get("action_finished", False)
            for key in ('discussion_draft','auto_discussion','discussion_state'):
                if key in old:task[key]=old[key]
            if task["platform"] == "unknown" and not task["reason"]:
                task["reason"] = "平台没有明确暴露任务完成状态，不能推定为未完成或已完成"
        progress=self.state["read_progress"]
        if progress["total"] is None:
            progress={"completed":len(chapters),"total":len(chapters)}
        progress={**progress,"phase":"done","active":False,"task_points":sum(task.get("is_task_point",True) for task in tasks)}
        self.update(tasks=tasks, chapters=chapters, catalog_complete=complete, status="ready", current="",read_progress=progress,
                    message="已读取任务点" if complete else "目录完整性尚未得到平台明确确认；仅展示已发现任务，不会报告课程全部完成")
        self.log("课程读取完成；发现 " + str(sum(task.get("is_task_point", True) for task in tasks)) + " 个任务点")
        self.restored_from_cache = False

    def reset_submission(self, payload):
        task = next(item for item in self.state["tasks"] if item["id"] == payload["task_id"])
        self.driver.activate(task)
        platform = self.driver.platform(task)
        if platform == "done":
            self.complete(task)
            self.update(status="ready", message="平台已确认完成，保留完成记录，不会重新提交")
            return
        if platform != "todo":
            raise StudyError("平台未明确显示待完成，不能解除提交保护；请在原页面核实")
        _, data = self.driver.questions(task)
        if data["formal_exam"] or (data["restricted"] and task["id"] not in payload.get("approved_task_ids", [])):
            raise StudyError("正式考试或未经单独授权的限次练习不能重置提交记录")
        with self.lock:
            self.checkpoint()
            self.store.authorize_submission_retry(self.state["course_id"], task["id"])
            self.update(task, platform="todo", status="pending", submission="", action_finished=False, attempts=0,
                        reason="已由用户核实尚未提交；等待点击开始选中任务")
            self.update(status="ready", selection=[task["id"]], message="旧提交意图已解除；请确认答题设置，再点击开始选中任务")
        self.log("用户明确核实未提交后，已解除该任务旧提交意图；未自动执行或提交")

    def execution_options(self, payload):
        options = {"auto_fill": bool(payload.get("auto_fill", False)), "auto_submit": bool(payload.get("auto_submit", False)),
                   "manual_policy": payload.get("manual_policy", "pause"), "rate": float(payload.get("rate", 1)),
                   "approved_task_ids": payload.get("approved_task_ids", [])}
        if options["manual_policy"] not in ("pause", "continue") or options["rate"] not in (0.5, 1, 1.25, 1.5, 2):
            raise StudyError("执行选项无效")
        return options

    def run(self, action, payload):
        options = self.execution_options(payload)
        selected = payload.get("task_ids") or (self.state.get("selection") if action == "resume" else None)
        if selected and set(selected) - {task["id"] for task in self.state["tasks"]}:
            raise StudyError("选择中含已失效的任务，请刷新目录后重新选择")
        if action == "retry":
            selected = [task["id"] for task in self.state["tasks"] if task["status"] in ("failed", "verifying")]
        tasks = [task for task in self.state["tasks"] if not selected or task["id"] in selected]
        if action == "retry" and not selected:
            raise StudyError("没有可重试的失败或待核验任务")
        tasks = [task for task in tasks if not (task.get("verified") and task["platform"] == "done" and task["status"] == "done") or (task['kind']=='discussion' and task.get('auto_discussion') and task.get('discussion_state')!='published')]
        tasks = [task for task in tasks if not (task['kind']=='discussion' and not task.get('is_task_point',True) and task.get('discussion_state')=='published')]
        self.driver.show_window()
        self.update(status="running", options=options, selection=[task["id"] for task in tasks], message="")
        for task in tasks:
            self.checkpoint()
            if task.get('reference_only'):
                self.update(task,status='reference')
                continue
            if not task.get('is_task_point',True) and task['kind']=='unknown' and not task.get('local_id') and not task.get('step') and task['platform']!='locked':
                self.update(current=task['id'])
                if self.driver.inspect_reference(task):
                    self.update(task,kind='text',reference_only=True,platform='not_applicable',status='reference',reason='平台目录已通过，此页为无附件任务的文字说明，不计入任务点')
                    self.log('已确认当前为无任务点说明页，继续后续任务')
                    continue
            optional_discussion=task['kind']=='discussion' and not task.get('is_task_point',True) and task.get('auto_discussion')
            if (not task.get("is_task_point", True) and not optional_discussion) or task["platform"] == "locked":
                self.update(task, status="manual", reason=task["reason"] or "任务锁定或尚未识别")
            else:
                self.update(current=task["id"])
                self.update(task, status="preparing", verified=False)
                try:
                    self.driver.activate(task)
                    platform = self.driver.platform(task)
                    self.update(task, platform=platform)
                    if platform == "done" and not optional_discussion:
                        if task['kind'] in ('text','document'):
                            self.read(task)
                        else:
                            self.complete(task)
                        continue
                    if platform == "locked":
                        raise StudyError("任务锁定，需满足平台解锁要求")
                    if task.get("action_finished"):
                        self.verify(task)
                        if task["status"] == "done":
                            continue
                        raise StudyError("先前动作已结束，平台仍未确认；只核验，不重复执行")
                    if platform == "unknown" and task['kind'] not in ('text','document'):
                        raise StudyError("平台任务状态未知；请手动检查并刷新，不能直接当作未完成执行")
                    if task["attempts"] >= 3:
                        raise StudyError("已达到本任务 3 次执行上限；请人工检查后重新读取课程")
                    if task["attempts"]:
                        self.driver.wait(min(2 ** task["attempts"], 8))
                    self.update(task, attempts=task["attempts"] + 1, status="running", reason="")
                    if task["kind"] in ("video", "audio"):
                        self.media(task, options)
                    elif task["kind"] in ("quiz", "embedded_quiz"):
                        self.answer(task, options)
                    elif task["kind"] in ("text", "document"):
                        self.read(task)
                    elif task['kind']=='discussion':
                        if not task.get('auto_discussion'):
                            raise StudyError('请在本行编辑讨论正文，并明确开启队列自动发布，或手动填写发布')
                        if task.get('discussion_state')=='published':
                            self.verify(task)
                        else:
                            self.handle_discussion(task,task.get('discussion_draft',''),True)
                    else:
                        raise StudyError("此任务类型尚未支持自动执行，请打开原页面处理")
                except StudyError as error:
                    self.update(task, status="manual", reason=redact(error))
                    self.log("任务需要人工处理：" + redact(error))
                except Interrupted:
                    with self.lock:
                        task["attempts"] = max(0, task["attempts"] - 1)
                        task["status"] = "paused"
                    raise
                except Exception as error:
                    self.update(task, status="failed", reason="浏览器或网络失败：" + type(error).__name__)
                    self.log("任务执行失败，已停止本次动作；不会重复提交")
                finally:
                    self.driver.pause_media()
            if task["status"] in ("manual", "failed", "verifying") and options["manual_policy"] == "pause":
                self.update(status="paused", message=task["reason"])
                return
        required=[task for task in self.state['tasks'] if task.get('is_task_point',True)]
        complete = self.state["catalog_complete"] and bool(required) and all(task.get("verified") for task in required)
        self.update(status="done" if complete else "needs_attention", current="", message="课程全部任务已由平台核验" if complete else "本轮队列结束；仍可能有未核验、需人工处理或尚未发现的任务")

    def handle_discussion(self, task, text, publish):
        from study_discussion import discussion
        fingerprint=digest({'kind':'discussion','task':task['id'],'text':text.strip()})
        previous=self.store.submission(self.state['course_id'],task['id'],fingerprint)
        if publish and (previous in ('discussion_intent','discussion_published') or self.store.pending_discussion(self.state['course_id'],task['id'])):
            raise StudyError('已有讨论发布意图或成功记录，请在原页面核实，不会重复发布')
        def before_publish():
            self.checkpoint()
            self.store.submission(self.state['course_id'],task['id'],fingerprint,'discussion_intent')
            self.update(task,discussion_state='intent')
        result=discussion(self.driver,task,text,publish,before_publish)
        self.update(task,discussion_state=result['state'],reason=result['reason'])
        if result['state']=='published':
            self.store.submission(self.state['course_id'],task['id'],fingerprint,'discussion_published')
            if task.get('is_task_point',True):
                self.driver.activate(task)
                self.verify(task)
            else:
                self.update(task,status='done',verified=False)
        else:
            self.update(task,status='verifying' if result['state']=='intent' else 'paused')

    def complete(self, task):
        if task.get("submission") == "intent":
            self.store.resolve_intents(self.state["course_id"], task["id"])
            self.update(task, submission="verified")
        self.update(task, status="done", platform="done", verified=True, reason="")
        self.log("已核验一个平台任务点完成")

    def verify(self, task):
        self.update(task, status="verifying", reason="正在等待平台任务状态确认")
        for delay in (1, 2, 4):
            self.driver.wait(delay)
            if self.driver.platform(task) == "done":
                self.complete(task)
                return True
        self.driver.activate(task)
        if self.driver.platform(task) == "done":
            self.complete(task)
            return True
        self.update(task, status="verifying", reason="动作已结束，但平台尚未确认任务完成；不会重复播放或重复提交，请刷新核验")
        return False

    def media(self, task, options):
        if self.driver.popup(task):
            if not self.answer(task, options, embedded=True):
                return
        self.driver.play(task, options["rate"])
        deadline = time.monotonic() + 6 * 60 * 60
        last_progress = time.monotonic()
        last_time = -1
        buffering = None
        while time.monotonic() < deadline:
            self.driver.wait(1)
            if self.driver.popup(task):
                self.driver.pause_media()
                if not self.answer(task, options, embedded=True):
                    return
                self.driver.play(task, 1)
                continue
            state = self.driver.media_state(task)
            if state.get("speed_rejected") and state["rate"] != 1:
                self.driver.normal_rate(task)
                self.log("页面提示倍速不被认可，已回退正常速度；完成状态仍以平台核验为准")
            self.update(task, progress={"current": round(state["current"], 1), "duration": round(state["duration"], 1), "rate": state["rate"]})
            if state["error"]:
                raise StudyError("播放器报告媒体错误，请检查网络或在原页面重新加载")
            if state["ended"]:
                self.update(task, action_finished=True)
                self.verify(task)
                return
            if state["paused"]:
                raise StudyError("播放器被暂停，可能是用户操作或页面限制；不会强制恢复，请确认后点击继续")
            if state["ready"] < 3:
                buffering = buffering or time.monotonic()
                if time.monotonic() - buffering > 45:
                    raise StudyError("媒体缓冲超过 45 秒，请检查网络后重试")
            else:
                buffering = None
            if abs(state["current"] - last_time) > 0.05:
                last_progress, last_time = time.monotonic(), state["current"]
            elif time.monotonic() - last_progress > 60:
                raise StudyError("媒体长时间没有真实进度，可能页面脚本阻塞或播放失败")
        raise StudyError("当前媒体超过单次 6 小时执行上限，已暂停")

    def model(self, questions):
        prompt = study_prompt(questions)
        if self.model_future and not self.model_future.done():
            self.log("上次模型请求仍在结束中；旧响应已失效，等待后再请求")
            while not self.model_future.done():
                self.wait(0.2)
        self.checkpoint()
        config = self.load_config()
        self.model_future = self.models.submit(self.call_model, prompt, config)
        try:
            while not self.model_future.done():
                self.wait(0.2)
            self.checkpoint()
            try:
                raw = self.model_future.result()
            except Exception as error:
                raise StudyError("模型调用失败（" + type(error).__name__ + "）；请检查第一页的模型设置，未填写或提交") from None
            return normalize_answers(questions, raw)
        finally:
            self.model_future.cancel()

    def answer(self, task, options, embedded=False):
        if embedded:
            self.update(task, has_popup=True)
        frame, data = self.driver.questions(task)
        if data["formal_exam"]:
            raise StudyError("已识别为正式考试；请打开原页面，由用户手动决定和处理")
        if data["restricted"] and task["id"] not in options["approved_task_ids"]:
            raise StudyError("此练习存在限次或不可撤回限制；选中任务并明确授权受限练习后才可执行")
        if data.get("inline_submit") or not data.get("explicit_submit", True):
            raise StudyError("检测到即时提交型控件或没有独立提交按钮；为避免填写即提交，需在原页面人工处理")
        fingerprint = digest(sorted(question_signature(question) for question in data["questions"]))
        previous = self.store.submission(self.state["course_id"], task["id"], fingerprint)
        if previous in ("intent", "submitted", "graded", "saved", "verified") or self.store.pending_intent(self.state["course_id"], task["id"]):
            self.log("已有提交意图或反馈记录，仅核验，不重复提交")
            self.verify(task)
            if task["status"] != "done" and self.store.pending_intent(self.state["course_id"], task["id"]):
                self.update(task, reason="旧提交意图阻止重复答题。请先打开原页面核实；确认尚未提交后，点击本行“核实未提交后重试”，再开始选中任务")
            return False
        if not options["auto_fill"]:
            raise StudyError("自动填写未开启，请在原页面答题或开启开关后继续")
        self.update(task, status="waiting_model")
        answers = self.model(data["questions"])
        self.checkpoint()
        self.driver.fill(task, data["questions"], answers)
        self.update(task, status="running", submission="filled")
        self.store.submission(self.state["course_id"], task["id"], fingerprint, "filled")
        if not options["auto_submit"]:
            raise StudyError("已填写并回读确认；自动提交未开启，请手动提交或开启后继续")
        self.checkpoint()
        self.store.submission(self.state["course_id"], task["id"], fingerprint, "intent")
        self.update(task, status="verifying", submission="intent")
        self.driver.submit(task, data["questions"], allow_restricted=task["id"] in options["approved_task_ids"])
        feedback = ""
        for delay in (1, 2, 4):
            self.driver.wait(delay)
            feedback = self.driver.feedback(task, frame)
            if feedback:
                break
        if feedback:
            self.store.submission(self.state["course_id"], task["id"], fingerprint, feedback)
            self.update(task, submission=feedback)
        if embedded and feedback in ("submitted", "graded"):
            self.update(task, status="running")
            return True
        if embedded:
            raise StudyError("弹题已尝试提交但缺少明确反馈；保留提交记录，需人工核验后继续播放")
        self.verify(task)
        return False

    def read(self, task):
        started = time.monotonic()
        stable = 0
        self.log('正在滚动课件内部阅读区域，随后核验平台完成状态')
        for step in range(120):
            self.checkpoint()
            elapsed = time.monotonic() - started
            if elapsed >= 600:
                break
            moved = self.driver.read_step(task, elapsed=elapsed, remaining=600-elapsed)
            stable = 0 if moved else stable + 1
            self.driver.wait(1)
            if stable >= 2:
                self.update(task, action_finished=True)
                self.verify(task)
                return
        raise StudyError('课件滚动或翻页仍未结束，已达本轮阅读观察上限；请在原页面检查')

    def close(self):
        self.closed = True
        self.epoch += 1
        self.models.shutdown(wait=False, cancel_futures=True)
        self.thread.join(timeout=5)
