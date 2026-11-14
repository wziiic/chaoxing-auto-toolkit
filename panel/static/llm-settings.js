/* Both panels edit the same server configuration and use the same connection test. */
(() => {
  'use strict';
  const node=id=>document.getElementById(id);
  let working=false;
  const preferenceKey='lxt-panel-preferences',attemptKey='lxt-course-read-attempted';
  const defaults={auto_read_courses:false,start_page:'current',remember_filters:true,loading_tips:true,confirm_reload:true};
  window.getPanelPreferences=()=>{
    let saved={};try{saved=JSON.parse(localStorage.getItem(preferenceKey)||'{}')||{}}catch{}
    const prefs={...defaults};
    for(const key of ['auto_read_courses','remember_filters','loading_tips','confirm_reload'])if(typeof saved[key]==='boolean')prefs[key]=saved[key];
    if(['current','last','works','study'].includes(saved.start_page))prefs.start_page=saved.start_page;
    return prefs;
  };
  async function request(path, body) {
    const token=localStorage.getItem('lxt_token')||'';
    const headers=token?{'X-Access-Token':token}:{};
    if(body)headers['Content-Type']='application/json';
    const response=await fetch(path,{method:body?'POST':'GET',headers,body:body?JSON.stringify(body):undefined,cache:'no-store'});
    const data=await response.json();
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:data.msg||'请求失败，请检查服务和访问口令');
    return data;
  }
  function notice(text,error=false){node('llmNotice').textContent=text;node('llmNotice').classList.toggle('error',error);}
  function lock(value){working=value;for(const id of ['saveLlmSettings','testLlmSettings'])node(id).disabled=value;}
  let directoryRequest=null;
  window.readCourseDirectoryOnce=(refresh=false)=>{
    if(directoryRequest)return directoryRequest;
    directoryRequest=(async()=>{
      const current=await request('/api/study/courses');
      if(!refresh&&['ready','loading'].includes(current.status)){sessionStorage.setItem(attemptKey,'1');return current;}
      if(!refresh&&sessionStorage.getItem(attemptKey))return current;
      sessionStorage.setItem(attemptKey,'1');
      return request('/api/study/courses',{});
    })().finally(()=>{directoryRequest=null});
    return directoryRequest;
  };
  function selectCategory(name){
    for(const category of ['llm','startup']){
      const selected=category===name;
      node(category+'Panel').hidden=!selected;
      node(category+'Tab').setAttribute('aria-selected',String(selected));
      node(category+'Tab').tabIndex=selected?0:-1;
    }
  }
  for(const name of ['llm','startup']){
    node(name+'Tab').addEventListener('click',()=>selectCategory(name));
    node(name+'Tab').addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight'].includes(event.key))return;
      event.preventDefault();const other=name==='llm'?'startup':'llm';selectCategory(other);node(other+'Tab').focus();
    });
  }
  function loadStartupPreferences(){
    const prefs=window.getPanelPreferences();
    node('prefAutoRead').checked=prefs.auto_read_courses;node('prefStartPage').value=prefs.start_page;
    node('prefRememberFilters').checked=prefs.remember_filters;node('prefLoadingTips').checked=prefs.loading_tips;
    node('prefConfirmReload').checked=prefs.confirm_reload;
  }
  node('saveStartupSettings').addEventListener('click',()=>{
    try{
      localStorage.setItem(preferenceKey,JSON.stringify({auto_read_courses:node('prefAutoRead').checked,start_page:node('prefStartPage').value,
        remember_filters:node('prefRememberFilters').checked,loading_tips:node('prefLoadingTips').checked,confirm_reload:node('prefConfirmReload').checked}));
      window.dispatchEvent(new Event('panelpreferenceschange'));
      node('startupNotice').textContent='已保存。启动选项在下次打开新窗口时生效，界面偏好立即生效。';
    }catch{node('startupNotice').textContent='保存失败，请检查浏览器是否允许本地存储。';}
  });
  window.reloadPanelPage=async()=>{
    if(window.getPanelPreferences().confirm_reload){
      try{
        const state=await request('/api/study');
        if(['running','preparing','scanning','waiting_model','pausing','stopping'].includes(state.status)&&
           !window.confirm('后台任务正在进行。重载网页不会停止任务，确定重载吗？'))return;
      }catch{}
    }
    location.reload();
  };
  window.loadCfg=async()=>{
    const config=await request('/api/config');
    node('cfgBase').value=config.llm.base_url||'';
    node('cfgModel').value=config.llm.model||'';
    node('keyState').textContent=config.llm.has_key?`已配置 ${config.llm.api_key_masked||''}`:'未配置';
    if(node('modelInfo'))node('modelInfo').textContent=`共用模型：${config.llm.model} · ${config.llm.has_key?'已配置':'请配置模型密钥'}`;
  };
  window.saveCfg=async()=>{
    if(working)return;lock(true);
    try{
      await request('/api/config',{llm_base_url:node('cfgBase').value,llm_model:node('cfgModel').value,llm_api_key:node('cfgKey').value});
      node('cfgKey').value='';await window.loadCfg();notice('设置已保存，作业面板与课程学习共用此配置。');
    }catch(error){notice('保存失败：'+error.message,true);}finally{lock(false);}
  };
  window.testLLM=async()=>{
    if(working)return;lock(true);notice('正在测试已保存的模型配置…');
    try{
      const result=await request('/api/testllm',{});
      notice(result.ok?`连接成功 · 模型 ${result.model} · 耗时 ${result.latency}s`:result.msg||'连接失败',!result.ok);
    }catch(error){notice('连接失败：'+error.message,true);}finally{lock(false);}
  };
  window.openLlmSettings=async()=>{
    loadStartupPreferences();node('llmSettings').showModal();notice('正在读取模型配置…');lock(true);
    try{await window.loadCfg();notice('两页共用同一配置；修改后请先保存，再测试连接。');}
    catch(error){notice('读取失败：'+error.message,true);}finally{lock(false);}
  };
  node('llmSettings').addEventListener('close',()=>{node('cfgKey').value=''});
  window.addEventListener('DOMContentLoaded',()=>{
    const prefs=window.getPanelPreferences(),current=location.pathname==='/study'?'study':'works';
    const first=!sessionStorage.getItem('lxt-window-started');
    if(first){
      sessionStorage.setItem('lxt-window-started','1');
      sessionStorage.setItem('lxt-startup-auto-read',prefs.auto_read_courses?'1':'0');
      const target=prefs.start_page==='last'?localStorage.getItem('lxt-last-page'):prefs.start_page;
      if(['works','study'].includes(target)&&target!==current){location.replace(target==='study'?'/study':'/');return;}
    }
    localStorage.setItem('lxt-last-page',current);
    if(sessionStorage.getItem('lxt-startup-auto-read')==='1'&&!sessionStorage.getItem(attemptKey))window.readCourseDirectoryOnce().catch(()=>{});
  },{once:true});
})();
