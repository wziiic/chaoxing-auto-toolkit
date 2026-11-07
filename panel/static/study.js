(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const queryToken = new URLSearchParams(location.search).get('token');
  if (queryToken) localStorage.setItem('lxt_token', queryToken);
  const token = queryToken || localStorage.getItem('lxt_token') || '';
  const controller = new AbortController();
  let timer = null, directoryTimer = null, state = null, directory = null, initialized = false, busy = false, choosing = false, awaitingSetup = false;
  const selected = new Set();
  let settingsSave = Promise.resolve();
  let displayedCourseUrl = '';
  let discussionTask = null;
  const animations=new Map();
  const catLines=['(=^･ω･^=) 快了喵快了喵','(ฅ•̀ω•́ฅ) 很努力的加载了别催喵','(つ﹏⊂) 哇...被作业吓哭了喵','(￢ω￢) 我没有在偷懒喵（目移）','(｡•̀ᴗ-)✧ 老大我们这样不会被发现喵','(〃ω〃) 不学习的杂鱼❤......没有不喜欢你喵！','(－ω－) Zzz','Ciallo～(∠・ω< )⌒☆','(ฅ´ω`ฅ) 正在把任务点一颗颗叼回来喵','(｡•́︿•̀｡) 作业好多，尾巴都打结了喵','(๑•̀ㅂ•́)و✧ 再等一下就给你看成果喵'];
  let tipPair=[],tipIndex=0,tipTimer=null;
  function resetTips(){clearTimeout(tipTimer);tipTimer=null;const pool=[...catLines];tipPair=[];for(let i=0;i<2;i++)tipPair.push(pool.splice(Math.floor(Math.random()*pool.length),1)[0]);tipIndex=0;}
  function tips(){
    if(!tipPair.length)resetTips();
    const active=['settings','directory'].some(prefix=>!byId(prefix+'Loader').hidden&&byId(prefix+'Loader').classList.contains('loading')&&(prefix!=='directory'||byId('coursePicker').open));
    for(const prefix of ['settings','directory']){const node=byId(prefix+'Tip');node.textContent=tipPair[tipIndex];node.hidden=!byId(prefix+'Loader').classList.contains('loading');}
    if(active&&!tipTimer&&tipIndex===0)tipTimer=setTimeout(()=>{tipTimer=null;tipIndex=1;tips()},9000);
    if(!active){clearTimeout(tipTimer);tipTimer=null;}
  }
  const statuses = {reference:'说明内容',not_applicable:'非任务点',idle:'未读取',ready:'待执行',preparing:'准备中',scanning:'读取目录中',pending:'待执行',running:'执行中',waiting_model:'等待模型',verifying:'等待平台核验',paused:'已暂停',pausing:'正在暂停',stopping:'正在停止',stopped:'已停止',done:'已完成',failed:'失败',manual:'需人工处理',needs_attention:'本轮结束 / 待处理',todo:'未完成',locked:'锁定',unknown:'未知'};
  const kinds = {video:'视频',audio:'音频',text:'文本阅读',document:'文档 / 课件',quiz:'章节练习',embedded_quiz:'视频内嵌题',discussion:'讨论',unknown:'待识别'};
  const submissions = {'':'—',filled:'已填写',intent:'已记录提交意图',saved:'已保存',submitted:'已提交',graded:'已判分',verified:'平台已核验完成'};
  async function api(path, body) {
    const headers = token ? {'X-Access-Token':token} : {};
    if (body) headers['Content-Type'] = 'application/json';
    const response = await fetch(path,{headers,method:body?'POST':'GET',body:body?JSON.stringify(body):undefined,signal:controller.signal,cache:'no-store'});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '请求失败，请检查登录和访问口令');
    return result;
  }
  function message(text, error=false) { byId('message').textContent=text; byId('message').classList.toggle('error',error); }
  function element(tag, text, className) { const node=document.createElement(tag); if(text!==undefined)node.textContent=text; if(className)node.className=className; return node; }
  function options() { return {auto_fill:byId('autoFill').checked,auto_submit:byId('autoSubmit').checked,rate:Number(byId('rate').value),manual_policy:byId('manualPolicy').value,approved_task_ids:byId('allowRestricted').checked?Array.from(selected):[]}; }
  function saveSettings() {
    const payload={action:'settings',...options(),task_ids:Array.from(selected)};
    settingsSave=settingsSave.catch(()=>{}).then(()=>api('/api/study/control',payload)).catch(error=>{if(error.name!=='AbortError')message(error.message,true)});
    return settingsSave;
  }
  async function command(action, extra={}) {
    if (busy && !['pause','stop'].includes(action)) return;
    busy=true;
    try { render(await api('/api/study/control',{action,...options(),...extra})); }
    catch(error) { if(error.name!=='AbortError')message(error.message,true); }
    finally { busy=false; }
  }
  function depth(chapter, chapters) { let count=0, parent=chapter.parent_id; const seen=new Set([chapter.id]); while(parent&&count<12&&!seen.has(parent)){seen.add(parent);count++;parent=chapters.find(item=>item.id===parent)?.parent_id;} return count; }
  function liquid(prefix, progress) {
    const holder=byId(prefix+'Loader');holder.hidden=!progress;if(!progress){const old=animations.get(prefix);if(old)cancelAnimationFrame(old.frame);animations.delete(prefix);tips();return;}
    const reading=prefix!=='directory';
    const total=Number(progress.total),done=Math.max(0,Number(progress.completed)||0),known=total>0;
    const percent=known?Math.min(100,Math.floor(done/total*100)):0,active=!!progress.active;
    holder.classList.toggle('loading',active);holder.classList.toggle('indeterminate',active&&!known);
    const bar=byId(prefix+'Bar');
    let animation=animations.get(prefix);
    const paint=value=>{animation.shown=value;bar.querySelector('.liquid-fill').style.width=known?value+'%':(active?'35%':'0%');byId(prefix+'Percent').textContent=known?Math.floor(value)+'%':active?'加载中':'—';};
    if(!animation){animation={shown:0,target:-1,frame:0};animations.set(prefix,animation);}
    if(animation.target!==percent||animation.known!==known){
      cancelAnimationFrame(animation.frame);animation.target=percent;animation.known=known;
      const start=percent<animation.shown?0:animation.shown,begin=performance.now();
      if(!known||matchMedia('(prefers-reduced-motion: reduce)').matches)paint(percent);
      else {const tick=now=>{const t=Math.min(1,(now-begin)/1100);paint(start+(percent-start)*(1-Math.pow(1-t,3)));if(t<1)animation.frame=requestAnimationFrame(tick);};animation.frame=requestAnimationFrame(tick);}
    }
    if(known)bar.setAttribute('aria-valuenow',String(percent));else bar.removeAttribute('aria-valuenow');
    bar.setAttribute('aria-valuetext',known?`${done} / ${total}`:'正在取得总数');
    byId(prefix+'Label').textContent=active?(reading?'正在读取任务点':'正在核对课程'):progress.phase==='cancelled'?'读取已停止':progress.phase==='failed'?'读取中断':(reading?'上次课程读取':'课程列表读取');
    byId(prefix+'Detail').textContent=known?`${done} / ${total} ${reading?'章节':'课程'}${progress.task_points!==undefined?' · 已发现 '+progress.task_points+' 个任务点':''}${progress.chapter?' · '+progress.chapter:''}`:(progress.chapter||'连接课程页面，等待平台目录…');
    tips();
  }
  function render(next) {
    state=next;
    liquid('settings',next.read_progress);
    byId('connection').textContent=next.worker_responsive?'服务已连接':'队列长时间无响应；请关闭课程浏览器后重新读取';
    byId('queueState').textContent=statuses[next.status]||next.status;
    if(awaitingSetup)byId('setupNote').textContent=['preparing','scanning'].includes(next.status)?'正在读取课程。可以先调整设置；读取完成后点击“开始学习”。':next.loaded&&next.status==='ready'?'课程已读取，请确认下方设置，再点击“开始学习”。':'读取尚未完成，请查看提示；完成读取后再开始学习。';
    if(next.message)message(next.message);
    for(const [key,value] of Object.entries(next.counts||{})) if(byId(key))byId(key).textContent=String(value);
    byId('catalogNote').textContent=next.catalog_complete?'平台目录数量已核对；完成数仍只按具体任务点计算。':'目录完整性未确认：以下为已发现的章节及任务，不代表全部课程。待解析项不计入任务点数量。';
    if(next.restored_from_cache)byId('catalogNote').textContent='沿用上次目录和平台状态，未自动刷新。'+byId('catalogNote').textContent;
    if(!initialized){
      if(next.input_url&&!byId('courseUrl').value){byId('courseUrl').value=next.input_url;displayedCourseUrl=next.input_url;}
      for(const id of next.selection||[])selected.add(id);
      const saved=next.options||{};
      byId('autoFill').checked=!!saved.auto_fill;byId('autoSubmit').checked=!!saved.auto_submit;
      byId('rate').value=String(saved.rate||1);byId('manualPolicy').value=saved.manual_policy||'pause';
      byId('allowRestricted').checked=!!saved.approved_task_ids?.length&&saved.approved_task_ids.every(id=>selected.has(id));
      if(next.loaded)byId('setupNote').textContent='已保留上次课程和设置，可直接开始或继续。仅手动读取或刷新时重新扫描目录。';
      initialized=true;
    }
    if(!choosing&&next.input_url&&byId('courseUrl').value===displayedCourseUrl){byId('courseUrl').value=next.input_url;displayedCourseUrl=next.input_url;}
    const active=['running','scanning','preparing','pausing','stopping'].includes(next.status);
    for(const id of ['all','selected','retry','resume'])byId(id).disabled=!next.loaded||active;
    byId('refresh').disabled=!next.loaded||active;byId('load').disabled=active&&!next.directory_loading;byId('open').disabled=active;
    byId('pause').disabled=!active;byId('stop').disabled=!next.loaded&&!active;
    const task=next.tasks.find(item=>item.id===next.current);
    byId('currentTask').textContent=task?task.title:(next.current||'当前没有执行任务');
    const progress=task?.progress||{};
    byId('progress').max=progress.duration||1;byId('progress').value=progress.current||0;
    byId('progressText').textContent=progress.duration?`${Math.floor(progress.current)} / ${Math.floor(progress.duration)} 秒 · ${progress.rate||1}×`:'—';
    const oldScroll=byId('log').scrollTop, atEnd=byId('log').scrollHeight-byId('log').clientHeight-oldScroll<30;
    byId('log').textContent=next.logs.map(item=>`${item.time}  ${item.message}`).join('\n')||'等待任务…';
    if(atEnd)byId('log').scrollTop=byId('log').scrollHeight;else byId('log').scrollTop=oldScroll;
    const existing=new Set(next.tasks.map(item=>item.id));for(const id of selected)if(!existing.has(id))selected.delete(id);
    const body=byId('tasks');body.replaceChildren();
    for(const chapter of next.chapters){
      const row=element('tr',undefined,'chapter');const cell=element('td',chapter.title||chapter.id);cell.colSpan=7;cell.style.paddingLeft=`${12+depth(chapter,next.chapters)*18}px`;row.append(cell);body.append(row);
      for(const item of next.tasks.filter(item=>item.chapter_id===chapter.id)){
        const row=element('tr'); const selection=element('td');const checkbox=element('input');checkbox.type='checkbox';checkbox.checked=selected.has(item.id);checkbox.setAttribute('aria-label','选择 '+item.title);checkbox.addEventListener('change',()=>{checkbox.checked?selected.add(item.id):selected.delete(item.id);saveSettings()});selection.append(checkbox);row.append(selection);
        const title=element('td',item.title+(item.is_task_point===false?(item.reference_only?'（说明内容，非任务点）':item.kind==='discussion'?'（非必做讨论）':'（参考内容 / 待解析项）'):''),'task-name');
        if(item.requirement)title.append(element('small',item.requirement,'task-requirement'));row.append(title);
        row.append(element('td',(kinds[item.kind]||item.kind)+(item.has_popup?' · 含播放中弹题':'')));
        for(const value of [item.platform,item.status]){const cell=element('td');cell.append(element('span',statuses[value]||value,'pill '+value));row.append(cell);}
        row.append(element('td',submissions[item.submission]||item.submission));
        const reason=element('td',item.reason||'—','reason');const open=element('button','打开原页面');open.addEventListener('click',()=>command('open',{task_id:item.id}));reason.append(open);
        if(item.kind==='discussion'){
          const edit=element('button','编辑讨论');edit.disabled=!next.loaded||active;edit.addEventListener('click',()=>{
            discussionTask=item.id;byId('discussionPrompt').textContent=item.title+(item.is_task_point===false?'（非必做讨论，不计入任务点数量）':'');
            byId('discussionText').value=item.discussion_draft||'';byId('discussionAuto').checked=!!item.auto_discussion;byId('discussionNotice').textContent='';byId('discussionEditor').showModal();
          });reason.append(edit);
          if(item.discussion_state)reason.append(element('small',({draft:'正文已填写，未发布',published:'讨论已发布',intent:'发布待核验'})[item.discussion_state]||item.discussion_state));
        }
        if(item.kind==='quiz'&&item.submission==='intent'&&item.platform!=='done'){
          const reset=element('button','核实未提交后重试');reset.disabled=!next.loaded||active;
          reset.addEventListener('click',()=>{if(window.confirm('请先在原页面核实这份测验尚未提交。仅显示“待完成”不能证明没有交卷。\n\n我已核实尚未提交，允许解除旧记录并重新答题。解除后仍需点击“开始选中任务”。')){selected.clear();selected.add(item.id);command('reset_submission',{task_id:item.id,acknowledge_unsubmitted:true});}});
          reason.append(reset);
        }
        row.append(reason);body.append(row);
      }
    }
    if(!next.chapters.length){const row=element('tr');const cell=element('td','尚未读取课程，或登录 / 页面结构需要处理');cell.colSpan=7;row.append(cell);body.append(row);}
  }
  async function loadUrl(url) {
    if(busy||choosing)return;
    if(!url){byId('courseDirectoryNote').textContent='请先选择课程，或在主页面输入有效链接。';return;}
    choosing=true;busy=true;
    try {
      byId('courseUrl').value=url;
      displayedCourseUrl=url;
      awaitingSetup=true;
      render(await api('/api/study/load',{url}));
      byId('coursePicker').close();
      byId('studySettings').focus({preventScroll:true});
      byId('studySettings').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});
    } catch(error) {byId('courseDirectoryNote').textContent=error.message;message(error.message,true);}
    finally {busy=false;choosing=false;renderDirectory();}
  }
  function renderDirectory() {
    if(!directory)return;
    const courses=directory.courses||[],filter=byId('courseFilter').value,search=byId('courseSearch').value.trim().toLocaleLowerCase();
    liquid('directory',{completed:directory.checked||0,total:directory.total,active:directory.status==='loading',phase:directory.status==='error'?'failed':'done'});
    const counts={todo:courses.filter(item=>item.platform==='todo').length,unknown:courses.filter(item=>item.platform==='unknown').length};
    byId('courseDirectoryNote').textContent=`${directory.message||''} · 未完成 ${counts.todo} 门，状态未知 ${counts.unknown} 门`;
    byId('reloadCourses').disabled=directory.status==='loading'||choosing;
    const list=byId('courseChoices'),scroll=list.scrollTop;list.replaceChildren();
    for(const course of courses.filter(item=>(filter==='all'||item.platform===filter)&&item.title.toLocaleLowerCase().includes(search))){
      const row=element('div',undefined,'course-choice'),info=element('div');
      info.append(element('strong',course.title));
      const progress=course.total!==null&&course.completed!==null?` · 任务点 ${course.completed} / ${course.total}`:'';
      info.append(element('p',`${statuses[course.platform]||'未知'}${progress}${course.reason?' · '+course.reason:''}`));
      const button=element('button',course.url?'选择并读取':'暂无法读取',course.url?'primary':'');
      button.disabled=!course.url||choosing;
      button.addEventListener('click',()=>loadUrl(course.url));row.append(info,button);list.append(row);
    }
    if(!list.children.length)list.append(element('p',directory.status==='loading'?'正在核对课程进度，请稍候…':search?'没有匹配的课程。':filter==='todo'?'没有确认未完成的课程；可切换“状态未知”或“全部课程”查看。':'当前分类没有课程。','hint'));
    list.scrollTop=scroll;
  }
  async function readDirectory(refresh=false) {
    clearTimeout(directoryTimer);
    try {directory=await api('/api/study/courses',refresh?{}:undefined);renderDirectory();}
    catch(error){directory={status:'error',courses:[],message:error.message};renderDirectory();}
    if(byId('coursePicker').open&&directory?.status==='loading')directoryTimer=setTimeout(()=>readDirectory(),1000);
  }
  byId('load').addEventListener('click',async()=>{
    if(busy)return;
    resetTips();liquid('directory',{active:true});
    byId('coursePicker').showModal();byId('courseDirectoryNote').textContent='正在读取当前账号的课程…';
    await readDirectory();
    if(byId('coursePicker').open&&directory?.status!=='loading'&&
       (directory?.status!=='ready'||Date.now()/1000-(directory.updated_at||0)>120))readDirectory(true);
  });
  byId('closePicker').addEventListener('click',()=>byId('coursePicker').close());
  byId('coursePicker').addEventListener('close',()=>{clearTimeout(directoryTimer);tips()});
  byId('reloadCourses').addEventListener('click',()=>{resetTips();readDirectory(true)});
  byId('courseFilter').addEventListener('change',renderDirectory);
  byId('courseSearch').addEventListener('input',renderDirectory);
  byId('loadTypedUrl').addEventListener('click',()=>loadUrl(byId('courseUrl').value.trim()));
  byId('closeDiscussion').addEventListener('click',()=>byId('discussionEditor').close());
  async function editDiscussion(action,publish=false){
    const text=byId('discussionText').value.trim();if(!text){byId('discussionNotice').textContent='请先填写讨论正文';return;}
    for(const id of ['saveDiscussion','fillDiscussion','publishDiscussion'])byId(id).disabled=true;
    try{render(await api('/api/study/control',{action,task_id:discussionTask,discussion_text:text,publish_discussion:publish,auto_discussion:byId('discussionAuto').checked}));byId('discussionNotice').textContent=action==='discussion_settings'?'正文与队列设置已保存，尚未发布':'操作已加入队列，请在任务行查看填写和发布结果。';}
    catch(error){byId('discussionNotice').textContent=error.message;}
    finally{for(const id of ['saveDiscussion','fillDiscussion','publishDiscussion'])byId(id).disabled=false;}
  }
  byId('saveDiscussion').addEventListener('click',()=>editDiscussion('discussion_settings'));
  byId('fillDiscussion').addEventListener('click',()=>editDiscussion('discussion'));
  byId('publishDiscussion').addEventListener('click',()=>editDiscussion('discussion',true));
  byId('refresh').addEventListener('click',()=>{resetTips();command('refresh')});
  byId('open').addEventListener('click',()=>command('open'));
  async function startLearning(taskIds) {await settingsSave;awaitingSetup=false;byId('setupNote').textContent='已按当前设置开始学习；修改设置会暂停执行。';return command('start',{task_ids:taskIds});}
  byId('all').addEventListener('click',()=>startLearning([]));
  byId('selected').addEventListener('click',()=>selected.size?startLearning(Array.from(selected)):message('请先选择任务',true));
  for(const action of ['pause','resume','stop','retry'])byId(action).addEventListener('click',()=>command(action));
  for(const id of ['autoFill','autoSubmit','rate','manualPolicy','allowRestricted'])byId(id).addEventListener('change',async()=>{if(state?.status==='running')await command('pause');await saveSettings()});
  byId('selectAll').addEventListener('change',event=>{selected.clear();if(event.target.checked)for(const task of state?.tasks||[])selected.add(task.id);if(state)render(state);saveSettings()});
  byId('export').addEventListener('click',async()=>{try{const result=await api('/api/study/export');const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));const link=element('a');link.href=url;link.download='课程执行记录.json';link.click();URL.revokeObjectURL(url);}catch(error){message(error.message,true)}});
  async function poll(){try{render(await api('/api/study'));}catch(error){if(error.name!=='AbortError'){byId('connection').textContent='连接失败';message(error.message,true);}}finally{if(!controller.signal.aborted)timer=setTimeout(poll,1500);}}
  window.addEventListener('pagehide',()=>{clearTimeout(timer);clearTimeout(directoryTimer);clearTimeout(tipTimer);for(const animation of animations.values())cancelAnimationFrame(animation.frame);controller.abort()},{once:true});
  window.loadCfg().catch(()=>{});
  poll();
})();
