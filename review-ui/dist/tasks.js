'use strict';
window.TaskBoard=(()=>{
  const names={pending:'待修改',in_progress:'修改中',ready_for_validation:'待验证',needs_revision:'需要再修改',validated:'验证通过（用户记录）',blocked:'等待条件'};
  const transitions={pending:['pending','in_progress','blocked'],in_progress:['in_progress','ready_for_validation','blocked'],ready_for_validation:['ready_for_validation','validated','needs_revision','in_progress','blocked'],needs_revision:['needs_revision','in_progress','blocked'],validated:['validated','in_progress'],blocked:['blocked','pending','in_progress']};
  let run='',plan=null,index=0,draft=null,changed=false,busy=false;
  const get=id=>document.getElementById(id);
  function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
  function notify(text){get('task-message').textContent=text;}
  async function request(path,body){
    const options=body?{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':(await (await fetch('/api/session')).json()).token},body:JSON.stringify(body)}:{};
    const r=await fetch(path,options),result=await r.json();if(!r.ok)throw new Error(result.error||'保存失败');return result;
  }
  function mark(){changed=true;const s=get('task-saved');if(s)s.textContent='有未保存的任务记录';}
  function formField(title,key,value,onChange,rows=3){
    const label=node('label',undefined,'field');label.append(node('span',title));const input=node('textarea');input.id=key;input.value=value;input.rows=rows;input.oninput=()=>{onChange(input.value);mark();};label.append(input);return label;
  }
  function pick(){draft=structuredClone(plan.tasks[index]);changed=false;}
  function validationVisibility(){get('task-validation').hidden=!['ready_for_validation','validated','needs_revision'].includes(draft.status);}
  function render(){
    const board=get('task-board');board.replaceChildren();if(!plan)return;
    const next=node('section',undefined,'task-source');next.append(node('h2','想把经验用到下一条视频？'),node('p','先挑 1～2 条建议，写成下一条的创作清单。关联视频 B 后记录采用情况，再按相同观察窗口比较数据。'));
    const nextLink=node('a','选择建议，制定下一条计划 →');nextLink.href='/cycles?run='+encodeURIComponent(run);next.append(nextLink);board.append(next);
    const total=plan.tasks.length,done=plan.tasks.filter(t=>t.status==='validated').length;
    const head=node('div',undefined,'section-head');head.append(node('h2','本视频的修改记录'),node('span',`验证通过 ${done} / ${total} · 来源复核第 ${plan.source_feedback_revision} 版`));board.append(head);
    board.append(node('p','下面用于继续修改当前视频。认可的建议不会自动计入下一条计划；验证通过也不等于播放或互动提升。','helper'));
    if(!plan.revision){const create=node('button','生成修改任务','primary');create.onclick=()=>save(true);create.disabled=busy;board.append(create);return;}
    const nav=node('nav',undefined,'task-tabs');nav.setAttribute('aria-label','修改任务');
    plan.tasks.forEach((task,i)=>{const b=node('button',`${i+1} · ${names[task.status]}`,i===index?'selected':'');b.setAttribute('aria-pressed',String(i===index));b.onclick=()=>{if(changed&&!confirm('当前任务有未保存的记录，确定切换吗？'))return;index=i;pick();notify('');render();};nav.append(b);});board.append(nav);
    if(!draft){board.append(node('p','该复核没有需要执行的建议。'));return;}
    const layout=node('div',undefined,'task-layout'),source=node('article',undefined,'task-source'),form=node('article',undefined,'task-editor');
    source.append(node('div',draft.source_finding_id+' / 已提交复核','eyebrow'),node('h3','这项需要修改什么'),node('p',draft.instruction),node('h3','原定验证办法'),node('p',draft.validation_plan),node('p','同一段录屏可以回应多条建议；每条建议仍分别记录验证情况。','helper'));
    const label=node('label',undefined,'field');label.append(node('span','任务状态'));const state=node('select');state.id='task-state';
    transitions[plan.tasks[index].status].forEach(value=>{const o=node('option',names[value]);o.value=value;o.selected=draft.status===value;state.append(o);});
    state.onchange=()=>{draft.status=state.value;if(['pending','in_progress','blocked'].includes(draft.status)){draft.validation={outcome:'not_tested',tested_version:'',method:'',record:'',source:'user_reported'};}mark();render();validationVisibility();};label.append(state);form.append(label);
    form.append(formField('修改说明 / 缺少的条件','task-note',draft.implementation_note,v=>draft.implementation_note=v));
    form.append(formField('作品版本或产物引用','task-artifact',draft.artifact_reference,v=>draft.artifact_reference=v,2));
    form.append(node('p','填写可区分的版本，例如“星际轨道 v2 · 10月19日录屏”。路径仅作为定位文字保存，程序不会打开文件。','helper'));
    const validation=node('section',undefined,'task-validation');validation.id='task-validation';validation.append(node('h3','实际验证记录'));
    const outcomeLabel=node('label',undefined,'field');outcomeLabel.append(node('span','测试结果'));const outcome=node('select');outcome.id='task-outcome';
    Object.entries({not_tested:'尚未测试',passed:'通过',failed:'未通过',inconclusive:'无法判断'}).forEach(([value,text])=>{const o=node('option',text);o.value=value;o.selected=draft.validation.outcome===value;outcome.append(o);});
    outcome.onchange=()=>{draft.validation.outcome=outcome.value;mark();};outcomeLabel.append(outcome);validation.append(outcomeLabel);
    validation.append(formField('被测作品版本（与上方一致）','task-tested-version',draft.validation.tested_version,v=>draft.validation.tested_version=v,2),formField('实际采用的验证方法','task-method',draft.validation.method,v=>draft.validation.method=v,2),formField('实际结果与记录依据','task-record',draft.validation.record,v=>draft.validation.record=v));
    validation.append(node('p','记录测试者的相关基础、是否需要帮助和卡住的位置即可，不需要姓名等个人信息。不能把预期结果当作已测试结果。','helper'));form.append(validation);
    const actions=node('div',undefined,'task-actions');const saveButton=node('button',busy?'正在保存…':'保存任务记录','primary');saveButton.id='save-task';saveButton.disabled=busy;saveButton.onclick=()=>save(false);
    const saved=node('span',changed?'有未保存的任务记录':`已保存第 ${plan.revision} 版`,'helper');saved.id='task-saved';
    const report=node('a','查看任务记录');report.href='/api/tasks/report?run='+encodeURIComponent(run);report.target='_blank';report.rel='noopener';actions.append(saveButton,saved,report);form.append(actions);
    layout.append(source,form);board.append(layout);validationVisibility();
  }
  async function load(identifier,accepted){
    run=identifier;plan=null;draft=null;changed=false;index=0;notify('');get('task-board').replaceChildren();if(!accepted)return;
    try{plan=(await request('/api/tasks?run='+encodeURIComponent(run))).plan;if(plan.tasks.length)pick();render();}catch(error){notify(error.message);}
  }
  async function save(create){
    if(busy)return;busy=true;notify('');render();
    try{
      const body={run};if(!create)body.task={revision:plan.revision,source_feedback_sha256:plan.source_feedback_sha256,task_id:draft.id,status:draft.status,implementation_note:draft.implementation_note,artifact_reference:draft.artifact_reference,validation:draft.validation};
      plan=(await request(create?'/api/tasks/create':'/api/tasks/save',body)).plan;if(plan.tasks.length)pick();
      notify(create?'修改任务已生成，全部从待修改开始。':'任务记录已保存。'+(plan.tasks[index]?.status==='validated'?'已标注为用户记录的验证通过，不外推到其他观众。':''));
    }catch(error){notify(error.message);}finally{busy=false;render();}
  }
  return {load,isDirty:()=>changed};
})();
