import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from test_study import LINK, LazyFakeBrowser, until
from study_core import StudyError, course_link
from study_service import StudyManager


class PlaylistBrowser(LazyFakeBrowser):
    def start(self, url):
        self.url=url
        self.completed=set()


class PlaylistTests(unittest.TestCase):
    def manager(self, folder=None):
        if folder is None:
            temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);folder=temporary.name
        manager=StudyManager(folder,lambda:{},lambda prompt,config:{'q1':'B'},browser_factory=PlaylistBrowser)
        self.addCleanup(manager.close)
        manager.directory['courses']=[{'id':str(n),'title':'课程'+str(n),'url':LINK.replace('courseId=111','courseId='+str(n))} for n in (1,2,3)]
        return manager

    def add(self, manager):
        manager.playlist.command('add',['1','2'])

    def finish(self, manager, action, payload):
        manager.update(status='done',catalog_complete=True,tasks=[{'id':'t','status':'done','platform':'done','verified':True}])

    def test_real_worker_learns_two_courses_in_order(self):
        manager=self.manager();self.add(manager)
        visited=[]
        original=manager.driver.start
        def start(url):visited.append(course_link(url)[0]);original(url)
        manager.driver.start=start
        manager.playlist.command('start',options={'auto_fill':True,'auto_submit':True,'mute':True})
        until(lambda:manager.playlist.snapshot()['status']=='done',timeout=15)
        queue=manager.playlist.snapshot()
        self.assertEqual(visited,[i['id'] for i in queue['items']])
        self.assertEqual(queue['completed'],2)
        self.assertEqual(manager.driver.submit_count,8)
        self.assertTrue(manager.state['options']['mute'])

    def test_incomplete_course_blocks_next_until_explicit_skip(self):
        manager=self.manager();self.add(manager);visited=[]
        def run(action,payload):
            visited.append(manager.state['course_id'])
            manager.update(status='needs_attention',message='平台尚未核验',catalog_complete=True,tasks=[{'id':'t','status':'verifying','platform':'todo','verified':False}])
        with patch.object(manager,'run',side_effect=run):
            manager.playlist.command('start')
            until(lambda:manager.playlist.snapshot()['status']=='paused')
        self.assertEqual(len(visited),1)
        self.assertEqual(manager.playlist.snapshot()['completed'],0)
        with patch.object(manager,'run',side_effect=lambda a,p:self.finish(manager,a,p)):
            manager.playlist.command('skip')
            until(lambda:manager.playlist.snapshot()['status']=='done')
        self.assertEqual(manager.playlist.snapshot()['skipped'],1)
        self.assertEqual(manager.playlist.snapshot()['completed'],1)

    def test_pause_cancels_course_transition_and_resume_uses_saved_course(self):
        manager=self.manager();self.add(manager);entered=threading.Event();visited=[]
        def run(action,payload):
            visited.append(manager.state['course_id']);entered.set()
            while True:manager.wait(.02)
        with patch.object(manager,'run',side_effect=run):
            manager.playlist.command('start');self.assertTrue(entered.wait(4))
            with self.assertRaises(StudyError):manager.playlist.command('start')
            with self.assertRaises(StudyError):manager.playlist.command('clear')
            with self.assertRaises(StudyError):manager.command('load',{'url':LINK})
            manager.command('pause')
            until(lambda:manager.state['status']=='paused')
        self.assertEqual(len(visited),1)
        self.assertEqual(manager.playlist.snapshot()['status'],'paused')
        scans=manager.driver.catalogue_count
        with patch.object(manager,'run',side_effect=lambda a,p:self.finish(manager,a,p)):
            manager.command('resume')
            until(lambda:manager.playlist.snapshot()['status']=='done')
        self.assertEqual(manager.driver.catalogue_count,scans+1)  # Only the second, uncached course.

    def test_restart_preserves_order_without_automatic_execution(self):
        manager=self.manager();self.add(manager)
        ids=[i['id'] for i in manager.playlist.snapshot()['items']]
        manager.playlist.command('reorder',ids[::-1])
        manager.playlist.value['status']='running';manager.playlist.value['items'][0]['status']='running';manager.playlist.save()
        manager.close()
        restored=self.manager(manager.folder)
        self.assertEqual(restored.playlist.snapshot()['status'],'paused')
        self.assertEqual([i['id'] for i in restored.playlist.snapshot()['items']],ids[::-1])
        self.assertEqual(restored.driver.catalogue_count,0)
        self.assertTrue(restored.commands.empty())

    def test_validation_dedup_and_private_links(self):
        manager=self.manager();self.add(manager);self.add(manager)
        self.assertEqual(len(manager.playlist.snapshot()['items']),2)
        with self.assertRaises(StudyError):manager.playlist.command('add',['3','missing'])
        self.assertEqual(len(manager.playlist.snapshot()['items']),2)
        with self.assertRaises(StudyError):manager.playlist.command('reorder',['bad'])
        text=json.dumps(manager.store.playlist())
        self.assertNotIn('secret',text);self.assertNotIn('https:',text)
        identity=manager.playlist.snapshot()['items'][0]['id']
        self.assertEqual(manager.store.saved_link(identity),manager.directory['courses'][0]['url'])
        manager.playlist.command('remove',course_id=identity)
        manager.playlist.command('clear')
        self.assertEqual(manager.playlist.snapshot()['items'],[])

    def test_no_cross_course_limited_attempt_approval(self):
        manager=self.manager();self.add(manager);observed=[]
        def run(action,payload):
            observed.append(copy.deepcopy(payload));self.finish(manager,action,payload)
        with patch.object(manager,'run',side_effect=run):
            manager.playlist.command('start',options={'auto_fill':True,'approved_task_ids':['t'],'rate':2,'mute':True})
            until(lambda:manager.playlist.snapshot()['status']=='done')
        self.assertEqual(len(observed),2)
        self.assertTrue(all(p['approved_task_ids']==[] and p['rate']==2 and p['mute'] for p in observed))

    def test_stop_is_preserved_on_interruption(self):
        manager=self.manager();self.add(manager);entered=threading.Event()
        def run(action,payload):
            entered.set()
            while True:manager.wait(.02)
        with patch.object(manager,'run',side_effect=run):
            manager.playlist.command('start');self.assertTrue(entered.wait(4))
            manager.playlist.command('stop');until(lambda:manager.state['status']=='stopped')
        self.assertEqual(manager.playlist.snapshot()['status'],'stopped')
        self.assertEqual(manager.playlist.snapshot()['completed'],0)

    def test_playlist_api_requires_auth_and_validates_operations(self):
        from fastapi.testclient import TestClient
        import app
        manager=self.manager();config=copy.deepcopy(app.DEFAULT_CONFIG);config['access_token']='fixture-token'
        with patch.object(app,'load_config',return_value=config),patch.object(app,'STUDY_MANAGER',manager),TestClient(app.app) as client:
            self.assertEqual(client.get('/api/study/playlist').status_code,401)
            self.assertEqual(client.post('/api/study/playlist',json={'action':'clear'}).status_code,401)
            headers={'X-Access-Token':'fixture-token'}
            response=client.post('/api/study/playlist',json={'action':'add','course_ids':['1','2']},headers=headers)
            self.assertEqual(response.status_code,200)
            self.assertEqual(len(response.json()['playlist']['items']),2)
            self.assertEqual(client.get('/api/study/playlist',headers=headers).headers['cache-control'],'no-store')
            self.assertEqual(client.post('/api/study/playlist',json={'action':'arbitrary'},headers=headers).status_code,409)
            self.assertEqual(client.post('/api/study/playlist',json={'action':'add','course_ids':['1']*51},headers=headers).status_code,422)


if __name__=='__main__':unittest.main()
