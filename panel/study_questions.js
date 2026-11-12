(scope => {
  const root = scope || document;
  const visible = element => !!element && element.getClientRects().length > 0;
  const text = element => (element?.innerText || element?.textContent || '').trim();
  const selector = '.singleQuesId, [data-question-id], .questionLi, .TiMu, .question-item, .ans-videoquiz';
  const candidates = Array.from(root.querySelectorAll(selector));
  if (root.matches?.(selector)) candidates.unshift(root);
  const roots = candidates.filter(visible).filter((element, index, elements) => !elements.some(parent => parent !== element && parent.contains(element)));
  const rows = root.matches?.('.readCompreHensionItem') ? [{element:root,parent:root.closest('.singleQuesId')||root,reading:true}] : roots.flatMap(parent => {
    const children = Array.from(parent.querySelectorAll('.readCompreHensionItem')).filter(visible);
    return children.length ? children.map(element => ({element, parent, reading: true})) : [{element: parent, parent, reading: false}];
  });
  const videoChoices = element => Array.from(element.querySelectorAll('.ans-videoquiz-opt label')).filter(visible).map(label=>{
    const match=text(label).match(/^\s*([A-Z]|\d+|正确|错误|对|错)\s*[、.．:：]\s*([\s\S]+)$/);
    return {element:label,data:match?.[1]||'',content:match?.[2]||'',picked:!!label.querySelector('input:checked')};
  });
  const videoEvent = element => {
    try { const data=window.Ext?.getCmp?.(element.id)?.renderData;return String(data?.resourceId||''); } catch { return ''; }
  };
  const contentId = element => {
    const value=text(element.querySelector('.tkItem_title'))+'|'+videoChoices(element).map(c=>c.data+':'+c.content).sort().join('|');
    let hash=2166136261;for(const ch of value)hash=Math.imul(hash^ch.charCodeAt(0),16777619);
    return 'video-content-'+(hash>>>0).toString(16)+'-'+value.length;
  };
  const idOf = ({element, reading}) => {
    if(element.matches('.ans-videoquiz'))return 'video-event-'+(videoEvent(element)||element.getAttribute('data-question-id')||contentId(element));
    if (reading) {
      const field = element.querySelector('input[name^="answer"]');
      return field?.name.replace(/^answer/, '') || '';
    }
    const direct = element.getAttribute('data-question-id') || element.getAttribute('data-qid');
    const fromId = (element.id || '').match(/^(?:question|questionLi|TiMu)[_-]?(.+)$/);
    const hidden = element.querySelector('input[name="questionId"],input[name="questionid"],input[name^="answertype"],input[id^="answertype"]');
    return direct || fromId?.[1] || (hidden?.name?.toLowerCase() === 'questionid' ? hidden.value : (hidden?.name || hidden?.id || '').replace(/^answertype/, '')) || '';
  };
  const native = element => Array.from(element.querySelectorAll('input[type="radio"],input[type="checkbox"]'));
  const custom = element => Array.from(element.querySelectorAll('li:has(.num_option),li:has(.num_option_dx),div.answerBg:has([data])')).filter(visible);
  const selected = element => element.getAttribute('aria-checked') === 'true' || !!element.querySelector('.check_answer,.check_answer_dx');
  const entries = rows.map(row => {
    const {element, parent, reading} = row;
    const qid = idOf(row);
    const nativeVideo=element.matches('.ans-videoquiz');
    const videoControls=nativeVideo?videoChoices(element):[];
    const nativeControls = nativeVideo?[]:native(element);
    const customControls = nativeControls.length ? [] : custom(element);
    const fields = Array.from(element.querySelectorAll('textarea,input[type="text"],[contenteditable="true"]'));
    const typeField = element.querySelector('input[name="readCompreHension-childType"],input[name^="answertype"],input[id^="answertype"],input[name="type"]');
    const code = element.getAttribute('data-question-type') || typeField?.value || element.querySelector('.TiMu[data],ul[qtype]')?.getAttribute('data') || element.querySelector('ul[qtype]')?.getAttribute('qtype');
    const types = {'0':'单选题','1':'多选题','2':'填空题','3':'判断题',single:'单选题',multiple:'多选题',judge:'判断题',blank:'填空题'};
    const type = types[code] || (/判断题/.test(text(element)) ? '判断题' : (nativeVideo&&/多选/.test(text(element.querySelector('.tkTopic_title'))))||nativeControls.some(control=>control.type==='checkbox') ? '多选题' : nativeControls.length || customControls.length || videoControls.length ? '单选题' : fields.length ? '填空题' : '未知');
    const stemNode = element.querySelector('.newZy_TItle,.mark_name,.question-title,.stem,[data-stem],.Zy_TItle,.tkItem_title');
    let stem = text(stemNode);
    if (reading) {
      const clone = element.cloneNode(true);
      clone.querySelectorAll('ul,input,textarea,label,i.index').forEach(node=>node.remove());
      const passage = text(parent.querySelector('.readcomprehension_content'));
      stem = '阅读材料：\n'+passage+'\n子题：\n'+text(clone);
      if (!passage || !text(clone)) stem = '';
    }
    const options = nativeVideo?videoControls.map(({data,content})=>({data,content})):nativeControls.length ? nativeControls.map(control=>({data:control.value,content:text(control.closest('label,li,.option')||control.parentElement)})) : customControls.map(control=>({data:control.querySelector('.num_option,.num_option_dx,[data]')?.getAttribute('data')||'',content:text(control.querySelector('a.after,.answer_p')||control.querySelector('a')||control)}));
    const checked = nativeVideo?videoControls.filter(c=>c.picked).map(c=>c.data):nativeControls.length ? nativeControls.filter(control=>control.checked).map(control=>control.value) : customControls.filter(selected).map(control=>control.querySelector('.num_option,.num_option_dx,[data]')?.getAttribute('data')||'');
    const answerField = Array.from(element.querySelectorAll('input[type="hidden"][name^="answer"]')).find(field=>field.name==='answer'+qid);
    let storedChoices = null;
    if (answerField && customControls.length) {
      const value = answerField.value;
      const keys = options.map(option=>option.data);
      if (!value) storedChoices = [];
      else if (keys.includes(value)) storedChoices = [value];
      else if (keys.every(key=>key.length===1) && Array.from(value).every(key=>keys.includes(key))) storedChoices = Array.from(value);
    }
    return {element,qid,type,stem,options,checked,blanks:fields.length,values:fields.map(field=>field.value??text(field)),images:!!element.querySelector('img,canvas,svg,math')||(reading&&!!parent.querySelector('.readcomprehension_content img,.readcomprehension_content canvas,.readcomprehension_content svg,.readcomprehension_content math')),control_mode:nativeVideo?'video_choice':nativeControls.length?'native':customControls.length?'choice':'text',native_video:nativeVideo,stored_choices:storedChoices,has_stored_answer:!!answerField&&customControls.length>0};
  });
  return {
    snapshot: () => entries.map(({element,...data})=>data),
    locate: qid => {const matches=entries.filter(entry=>entry.qid===qid);return matches.length===1?matches[0].element:null},
    choice: (qid,key) => {const matches=entries.filter(entry=>entry.qid===qid);if(matches.length!==1)return null;if(matches[0].native_video){const choices=videoChoices(matches[0].element).filter(c=>c.data===key);return choices.length===1?choices[0].element:null;}const choices=custom(matches[0].element).filter(element=>element.querySelector('.num_option,.num_option_dx,[data]')?.getAttribute('data')===key);return choices.length===1?choices[0]:null}
  };
})
