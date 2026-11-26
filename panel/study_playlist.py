"""Persistent multi-course ordering, executed by the existing learning worker."""
import copy

from study_core import CourseLock, Interrupted, StudyError, course_link, redact


ACTIVE=('running','preparing','scanning','pausing','stopping')
FINISHED=('done','skipped')


class CoursePlaylist:
    def __init__(self, manager):
        self.manager=manager
        self.value=manager.store.playlist() or {'status':'idle','items':[],'position':0,'options':{},'message':''}
        if self.value['status'] in ACTIVE:
            self.value.update(status='paused',message='已保留播放队列，点击“继续队列”后恢复')
            for item in self.value['items']:
                if item['status'] in ACTIVE:item['status']='paused'
            self.save()

    def save(self):
        self.manager.store.save_playlist(self.value)

    def snapshot(self):
        with self.manager.lock:
            result=copy.deepcopy(self.value)
            result['completed']=sum(i['status']=='done' for i in result['items'])
            result['skipped']=sum(i['status']=='skipped' for i in result['items'])
            for item in result['items']:
                if item['id']==self.manager.state['course_id']:
                    required=[t for t in self.manager.state['tasks'] if t.get('is_task_point',True)]
                    item['counts']={'discovered':len(required),'verified':sum(bool(t.get('verified')) for t in required)}
            return result

    def interrupt(self, action):
        if self.value['status'] not in ('running','preparing','paused'):return
        self.value.update(status='stopped' if action=='stop' else 'paused',message='播放队列已停止' if action=='stop' else '播放队列已暂停')
        if self.value['position']<len(self.value['items']):
            item=self.value['items'][self.value['position']]
            if item['status'] not in FINISHED:item['status']='paused'
        self.save()

    def _add(self, title, url, source_id=''):
        identity,_=course_link(url)
        if any(i['id']==identity for i in self.value['items']):return
        if len(self.value['items'])>=50:raise StudyError('播放队列最多加入 50 门课程')
        self.manager.store.save_link(identity,url)
        self.value['items'].append({'id':identity,'source_id':source_id,'title':str(title or '当前课程')[:300],'status':'pending','reason':'','counts':{}})

    def command(self, action, course_ids=None, course_id='', options=None):
        manager=self.manager
        with manager.lock:
            from course_activities import RUN_LOCK
            if RUN_LOCK.locked() and action in ('start','resume','skip'):
                raise StudyError('独立任务包正在执行，请先在作业面板停止任务包')
            if action in ('pause','stop'):
                if self.value['status'] not in ('running','preparing','paused'):raise StudyError('没有正在执行的播放队列')
                manager.command(action)
                return manager.snapshot()
            if action not in ('add','current','reorder','remove','clear','start','resume','skip'):
                raise StudyError('未知的播放队列操作')
            if self.value['status'] in ('running','preparing'):
                raise StudyError('请先暂停播放队列，再修改顺序或课程')
            if action=='add':
                ids=list(dict.fromkeys(course_ids or []))
                if not ids:raise StudyError('请先选择要加入队列的课程')
                directory={i['id']:i for i in manager.directory['courses']}
                entries=[]
                for identity in ids:
                    item=directory.get(identity)
                    if not item or not item.get('url'):raise StudyError('所选课程没有有效学习链接，请刷新课程列表')
                    course_link(item['url']);entries.append(item)
                incoming={course_link(i['url'])[0] for i in entries}
                if len(incoming|{i['id'] for i in self.value['items']})>50:raise StudyError('播放队列最多加入 50 门课程')
                for item in entries:self._add(item['title'],item['url'],item['id'])
            elif action=='current':
                if not manager.loaded or not manager.course_url:raise StudyError('请先读取当前课程')
                title=next((i['title'] for i in manager.directory['courses'] if i.get('url') and course_link(i['url'])[0]==manager.state['course_id']),'当前课程')
                self._add(title,manager.course_url)
            elif action=='reorder':
                items={i['id']:i for i in self.value['items']}
                if len(course_ids or [])!=len(items) or set(course_ids or [])!=set(items):raise StudyError('课程顺序已变化，请刷新后重试')
                self.value['items']=[items[key] for key in course_ids]
            elif action=='remove':
                if not any(i['id']==course_id for i in self.value['items']):raise StudyError('课程已不在播放队列中')
                self.value['items']=[i for i in self.value['items'] if i['id']!=course_id]
            elif action=='clear':
                self.value.update(items=[],position=0,status='idle',message='')
            else:
                if manager.state['status'] in ACTIVE:raise StudyError('请先暂停当前学习，再启动多课程播放队列')
                if action=='skip':
                    if self.value['status'] not in ('paused','stopped') or self.value['position']>=len(self.value['items']):raise StudyError('没有可跳过的当前课程')
                    self.value['items'][self.value['position']].update(status='skipped',reason='用户明确跳过，未计为课程完成')
                position=next((n for n,i in enumerate(self.value['items']) if i['status'] not in FINISHED),len(self.value['items']))
                if position==len(self.value['items']):
                    if action!='skip':raise StudyError('播放队列为空或已全部处理，请先加入课程')
                    self.value.update(position=position,status='done',message='队列已结束；跳过的课程未计为完成')
                    self.save();return manager.snapshot()
                merged=dict(self.value.get('options') or manager.state.get('options') or {})
                if options is not None:merged.update(options)
                merged['approved_task_ids']=[]  # A course switch never transfers limited-attempt approval.
                self.value.update(options=manager.execution_options(merged),position=position,status='preparing',message='正在启动播放队列')
                manager.epoch+=1
                manager.state['status']='preparing'
                self.save()
                manager.commands.put((manager.epoch,'playlist',{}))
                return manager.snapshot()
            if action in ('add','current','reorder','remove'):
                self.value['position']=next((n for n,i in enumerate(self.value['items']) if i['status'] not in FINISHED),len(self.value['items']))
                if self.value['status'] in ('idle','done'):self.value['status']='ready' if self.value['items'] else 'idle'
                self.value['message']='课程顺序已保存，点击“开始队列”或“继续队列”执行'
            self.save()
            return manager.snapshot()

    def _activate(self, item):
        manager=self.manager
        url=manager.store.saved_link(item['id'])
        if not url:raise StudyError('队列课程的本机登录链接不可用，请移除后重新加入课程')
        if manager.state['course_id']==item['id'] and manager.loaded:
            if not manager.course_lock:
                lock=CourseLock(manager.folder/'locks',item['id']);lock.acquire()
                manager.course_lock=lock
            manager.driver.url=url
            return
        previous=manager.store.course(item['id'])
        if not previous or not previous.get('chapters'):
            manager.load(url)
            return
        manager.persist()
        lock=CourseLock(manager.folder/'locks',item['id']);lock.acquire()
        if manager.course_lock:manager.course_lock.close()
        manager.course_lock=lock
        with manager.lock:
            manager.checkpoint()
            manager.state=previous
            manager.state.update(status='ready',network_recovering=False,message='')
            manager.course_url=url
            manager.driver.url=url
            manager.loaded=True
            manager.restored_from_cache=True

    def _pause(self, reason):
        self.value.update(status='paused',message=reason)
        if self.value['position']<len(self.value['items']):
            self.value['items'][self.value['position']].update(status='paused',reason=reason)
        self.save()

    def run(self):
        manager=self.manager
        try:
            while self.value['position']<len(self.value['items']):
                manager.checkpoint()
                item=self.value['items'][self.value['position']]
                if item['status'] in FINISHED:
                    self.value['position']+=1;continue
                with manager.lock:
                    manager.checkpoint()
                    self.value.update(status='running',message='正在学习：'+item['title'])
                    item.update(status='running',reason='');self.save()
                self._activate(item)
                manager.checkpoint()
                manager.log('播放队列开始课程：'+item['title'])
                with manager.lock:
                    manager.checkpoint()
                    manager.state.update(selection=[],queue_scope='all',active_chapter='')
                manager.run('resume',dict(self.value['options'],task_ids=[],approved_task_ids=[]))
                manager.checkpoint()
                manager.persist()
                required=[t for t in manager.state['tasks'] if t.get('is_task_point',True)]
                item['counts']={'discovered':len(required),'verified':sum(bool(t.get('verified')) for t in required)}
                if manager.state['status']!='done' or not manager.state.get('catalog_complete') or not required or not all(t.get('verified') and t.get('platform')=='done' for t in required):
                    with manager.lock:self._pause(manager.state.get('message') or '当前课程仍有未确认或需人工处理的任务，播放队列已暂停')
                    return
                with manager.lock:
                    manager.checkpoint()
                    item.update(status='done',reason='课程全部任务已由平台核验')
                    self.value['position']+=1;self.save()
            with manager.lock:
                manager.checkpoint()
                self.value.update(status='done',message='播放队列已结束；完成和跳过的课程分别统计')
                self.save()
        except Interrupted:
            with manager.lock:
                if self.value['status'] in ('running','preparing'):self._pause('播放队列已中断，点击继续后核验当前位置')
            raise
        except Exception as error:
            reason=redact(str(error)) if isinstance(error,StudyError) else '课程切换或连接失败（'+type(error).__name__+'）'
            with manager.lock:self._pause(reason)
            raise
