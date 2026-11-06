/* Both panels edit the same server configuration and use the same connection test. */
(() => {
  'use strict';
  const node=id=>document.getElementById(id);
  let working=false;
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
    node('llmSettings').showModal();notice('正在读取模型配置…');lock(true);
    try{await window.loadCfg();notice('两页共用同一配置；修改后请先保存，再测试连接。');}
    catch(error){notice('读取失败：'+error.message,true);}finally{lock(false);}
  };
  node('llmSettings').addEventListener('close',()=>{node('cfgKey').value=''});
})();
