(scope => {
  const root = scope || document;
  const visible = element => !!element && element.getClientRects().length > 0;
  const text = element => (element?.innerText || element?.textContent || '').trim();
  const number = value => /^\d+$/.test(String(value || '')) ? String(value) : '';
  const parse = value => { try { return JSON.parse(value || '{}'); } catch { return {}; } };
  const chapterId = element => {
    const direct = element.getAttribute('data-chapter-id') || element.getAttribute('knowledgeid') || element.getAttribute('data-knowledgeid');
    if (number(direct)) return String(direct);
    const href = element.getAttribute('href') || element.querySelector('a')?.getAttribute('href') || '';
    const fromLink = href.match(/[?&](?:chapterId|knowledgeid)=(\d+)/i);
    const fromId = (element.id || '').match(/^(?:cur|chapter|knowledge)[_-]?(\d+)$/i);
    const click = (element.getAttribute('onclick') || element.querySelector('[onclick]')?.getAttribute('onclick') || '').match(/getTeacherAjax\(\s*['"]?\d+['"]?\s*,\s*['"]?\d+['"]?\s*,\s*['"]?(\d+)/);
    return fromLink?.[1] || fromId?.[1] || click?.[1] || '';
  };
  const chapterNodes = () => Array.from(document.querySelectorAll('#coursetree .posCatalog_select:not(.firstLayer), #coursetree [data-chapter-id], #coursetree [knowledgeid]'));
  const stepKey = element => element.getAttribute('data-study-step') || element.getAttribute('data-num') || text(element);
  const getSteps = () => Array.from(document.querySelectorAll('.prev_white, [data-study-step]')).filter(visible).map(element => ({
    key: stepKey(element),
    title: text(element), active: /active|current|on/.test(element.className) || element.getAttribute('aria-selected') === 'true'
  }));
  const stateOf = (element, attachment) => {
    if (attachment?.isPassed === true || attachment?.isPassed === 'true' || element?.matches('.ans-job-finished, [data-platform-state="done"]')) return 'done';
    if (element?.matches('[data-platform-state="locked"], [data-locked="true"]')) return 'locked';
    if (attachment?.isPassed === false || attachment?.isPassed === 'false' || element?.matches('[data-platform-state="todo"]')) return 'todo';
    if (element?.matches('[data-platform-state="running"]')) return 'running';
    if (element && visible(element.querySelector('.ans-job-icon'))) return 'todo';
    return 'unknown';
  };
  const kindOf = value => {
    const valueText = String(value || '').toLowerCase();
    if (/hyperlink|超链接/.test(valueText)) return 'link';
    if (/videoquiz|video-question|弹题|内嵌题/.test(valueText)) return 'embedded_quiz';
    if (/video|视频/.test(valueText)) return 'video';
    if (/audio|音频/.test(valueText)) return 'audio';
    if (/work|quiz|test|测验|练习/.test(valueText)) return 'quiz';
    if (/document|ppt|pdf|课件|文档/.test(valueText)) return 'document';
    if (/^(?:insertimage|image)$/.test(valueText)) return 'image';
    if (/read|text|阅读/.test(valueText)) return 'text';
    if (/insertbbs|discuss|discussion|讨论/.test(valueText)) return 'discussion';
    return 'unknown';
  };
  const taskData = [];
  const aliases = new Map();
  const attachments = (typeof window.mArg === 'object' && Array.isArray(window.mArg?.attachments)) ? window.mArg.attachments : [];
  for (const attachment of attachments) {
    const property = attachment.property || {};
    const identity = attachment.jobid || attachment._jobid || property._jobid || property.jobid || property.objectid || property.workid;
    if (!identity) continue;
    for (const alias of [identity, property.objectid, property.workid, attachment.jobid, attachment._jobid]) if (alias) aliases.set(String(alias), String(identity));
    const flag=attachment.job??property.isJob;
    taskData.push({local_id: String(identity), kind: kindOf(attachment.type || property.type || property.module), title: property.name || property.title || attachment.type || '任务点', platform: stateOf(null, attachment), declared_task_point:[true,'true',1,'1'].includes(attachment.job)||[true,'true',1,'1'].includes(property.isJob), is_task_point:![false,'false',0,'0'].includes(flag), requirement:'', reason: '', locator: {attachment: String(identity)}});
  }
  for (const element of document.querySelectorAll('[data-study-task-id], .ans-attach-ct')) {
    const iframe = element.querySelector('iframe');
    const data = parse(iframe?.getAttribute('data') || element.getAttribute('data'));
    const identity = element.getAttribute('data-study-task-id') || element.getAttribute('data-jobid') || element.getAttribute('jobid') || data._jobid || data.jobid || data.objectid || data.workid;
    if (!identity) continue;
    const kind = kindOf(element.getAttribute('data-study-kind') || data.type || data.module || iframe?.getAttribute('module') || iframe?.className || (element.querySelector('video') ? 'video' : element.querySelector('audio') ? 'audio' : '') || iframe?.getAttribute('src'));
    const canonical = aliases.get(String(identity)) || String(identity);
    const existing = taskData.find(task => task.local_id === canonical);
    const platform = stateOf(element);
    const requirement=(text(element).match(/完成条件[\s\S]{0,260}/)||[''])[0].trim();
    const task = {local_id: canonical, kind, title: element.getAttribute('data-title') || data.name || data.title || iframe?.title || element.getAttribute('title') || kind, platform, is_task_point:![false,'false',0,'0'].includes(data.isJob), requirement, reason: element.getAttribute('data-reason') || '', locator: {dom: String(identity)}};
    if (!existing) taskData.push(task);
    else { if (platform !== 'unknown') existing.platform = platform; existing.locator = task.locator; existing.non_task_candidate=['video','audio','document'].includes(existing.kind)&&!existing.declared_task_point&&![true,'true',1,'1'].includes(data.isJob)&&!element.matches('.ans-job-finished')&&!element.querySelector('.ans-job-icon'); existing.requirement=requirement; if(data.isJob!==undefined)existing.is_task_point=task.is_task_point; if (existing.kind === 'unknown') existing.kind = kind; }
  }
  // Inline images are attachments too, but have no iframe/card. Match their
  // stable resource IDs before considering them optional reference material.
  for (const attachment of attachments) {
    const property=attachment.property||{};
    if (String(property.module||'').toLowerCase()!=='insertimage'||!property.objectid) continue;
    const identity=attachment.jobid||attachment._jobid||property._jobid||property.jobid||property.objectid;
    const task=taskData.find(item=>item.local_id===String(identity));
    const images=Array.from(document.querySelectorAll('img[_jobid]')).filter(image=>image.getAttribute('_jobid')===String(property._jobid||identity)&&[image.getAttribute('src'),image.getAttribute('data-original')].some(value=>value?.includes(String(property.objectid))));
    if (!task||images.length!==1) continue;
    const image=images[0], container=image.closest('.ans-attach-ct,[data-study-task-id]');
    task.kind='image';
    task.locator={image:String(identity)};
    const containerData=parse(container?.querySelector('iframe')?.getAttribute('data')||container?.getAttribute('data'));
    task.non_task_candidate=!task.declared_task_point&&![false,'false'].includes(attachment.isPassed)&&!attachment.jobid&&!attachment._jobid&&!property.jobid&&!image.matches('[jobid],[data-jobid],[data-study-task-id],[data-platform-state],[data-locked],[isjob="true"],[data-is-job="true"]')&&![true,'true',1,'1'].includes(containerData.isJob)&&!containerData.jobid&&!container?.matches('.ans-job-finished,[data-platform-state],[data-locked],[jobid],[data-jobid]')&&!container?.querySelector('.ans-job-icon');
  }
  const questions = (__STUDY_QUESTIONS__)(root).snapshot();
  const body = document.body?.innerText || '';
  const guard = /验证码|人机验证|身份验证|安全验证/.test(body) ? 'verification' : /操作过于频繁|访问被拦截|异常访问|风控拦截|请求被拒绝/.test(body) ? 'risk' : /另一(?:个|台)设备|其他设备|其它设备|多设备|异地登录|账号.*(?:冲突|占用)/.test(body) || (location.hostname==='detect.chaoxing.com' && /^\/api\/monitor/.test(location.pathname)) ? 'session_conflict' : /账号密码登录|扫码登录/.test(body) || /passport.*chaoxing/.test(location.host) ? 'login' : '';
  const tree = document.querySelector('#coursetree');
  const nodes = chapterNodes();
  const chapters = nodes.map(element => ({id: chapterId(element), title: element.querySelector('.posCatalog_name')?.getAttribute('title') || text(element.querySelector('.posCatalog_name')) || element.getAttribute('title') || text(element).slice(0,120), parent_id: chapterId(element.parentElement?.closest('.posCatalog_select, [data-chapter-id], [knowledgeid]') || document.createElement('div')), locked: element.getAttribute('data-locked') === 'true' || /locked|lockCatalog/.test(element.className), reason: element.getAttribute('data-reason') || element.getAttribute('title') || ''}));
  for(const chapter of chapters){const node=nodes.find(element=>chapterId(element)===chapter.id);chapter.completed=!!node?.querySelector('.icon_Completed');}
  const graphAttachments=attachments.length>0&&attachments.every(a=>String(a.type||a.property?.module||'').toLowerCase()==='knowledgegraph'&&![true,'true',1,'1'].includes(a.job??a.property?.isJob)&&!(a.jobid||a._jobid||a.property?._jobid||a.property?.jobid)&&a.property?.knowledgeGraphId);
  const graphIds=new Set(attachments.map(a=>String(a.property?.knowledgeGraphId||'')));
  const cards=Array.from(document.querySelectorAll('.ans-attach-ct'));
  const graphCards=graphAttachments&&cards.length===attachments.length&&cards.every(e=>{
    const data=parse(e.querySelector('iframe')?.getAttribute('data'));
    return graphIds.has(String(data.knowledgeGraphId||''))&&!data._jobid&&!data.jobid&&![true,'true',1,'1'].includes(data.isJob)&&!e.querySelector('.ans-job-icon')&&!e.getAttribute('jobid')&&!e.getAttribute('data-jobid');
  });
  const emptyAudition=e=>e.id==='auditionAudio'&&!e.getAttribute('src')&&!e.currentSrc&&!e.querySelector('source')&&getComputedStyle(e).display==='none';
  const referenceContent=/\/knowledge\/cards$/.test(location.pathname)&&Array.isArray(window.mArg?.attachments)&&taskData.length===0&&questions.length===0&&!document.querySelector('video')&&!Array.from(document.querySelectorAll('audio')).some(e=>!emptyAudition(e))&&body.trim().length>=10&&document.readyState!=='loading'&&
    ((attachments.length===0&&cards.length===0&&!document.querySelector('iframe'))||(graphCards&&Array.from(document.querySelectorAll('iframe')).every(e=>e.closest('.ans-attach-ct'))));

  const total = tree?.getAttribute('data-total-chapters');
  const incomplete = !!document.querySelector('#coursetree [aria-busy="true"], #coursetree .loading, #coursetree [data-lazy="true"]');
  const catalogComplete = !!tree && !!total && Number(total) === new Set(chapters.map(chapter => chapter.id).filter(Boolean)).size && !incomplete;
  const media = Array.from(root.querySelectorAll('video,audio')).filter(visible).map(element => ({kind: element.tagName.toLowerCase(), current: Number(element.currentTime) || 0, duration: Number.isFinite(element.duration) ? element.duration : 0, paused: element.paused, ended: element.ended, ready: element.readyState, error: element.error?.code || 0, rate: element.playbackRate}));
  const feedbackText = root === document ? body : text(root);
  const feedback = /已判分|批阅完成/.test(feedbackText) ? 'graded' : /提交成功|已提交/.test(feedbackText) ? 'submitted' : /保存成功|已保存|暂存成功/.test(feedbackText) ? 'saved' : '';
  const explicitSubmit = Array.from(root.querySelectorAll('button,a,input[type="submit"]')).filter(visible).filter(element=>/^(?:提交|提交答案|提交作业|提交测验|确认提交)$/.test(text(element)||element.value||'')).length === 1;
  const inlineSubmit = Array.from(root.querySelectorAll('input,textarea,label')).some(element=>/submit|saveAnswer|checkAnswer/i.test((element.getAttribute('onclick')||'')+' '+(element.getAttribute('onchange')||'')));
  const quizStatus = text(root.querySelector('.newTestTitle .testTit_status'));
  const quizPlatform = /^(?:待完成|未完成|未提交)$/.test(quizStatus) ? 'todo' : /^(?:已完成|已通过)$/.test(quizStatus) ? 'done' : 'unknown';
  return {chapters, reference_content:referenceContent, catalog_complete: catalogComplete, catalog_present: !!tree, catalog_loading: incomplete, missing_chapter_ids: chapters.some(chapter => !chapter.id), tasks: taskData, steps: getSteps(), questions, media, guard, feedback, quiz_platform:quizPlatform, explicit_submit: explicitSubmit, inline_submit: inlineSubmit, formal_exam: /正式考试|考试剩余时间|考试截止|考试须知/.test(body) || /\/exam(?:\/|\?)/i.test(location.href), restricted: /限次|不可重做|不可撤回|剩余提交次数|仅.*[一1]次/.test(body), popup: !!document.querySelector('.ans-videoquiz:has(input), .quiz-dialog:has(input), [data-video-question]'), read_requirement: text(root.querySelector('[data-read-requirement], .read-requirement')), read_timer: text(root.querySelector('[data-read-timer], .read-timer')), page_finished: document.readyState === 'complete'};
})
