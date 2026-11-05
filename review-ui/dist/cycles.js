'use strict';
const $=id=>document.getElementById(id), query=new URLSearchParams(location.search);
const statusNames={planned:'下一条待创作',implemented:'已记录采用，待数据',waiting_data:'数据未对齐',compared:'已比较，待总结',decided:'本轮已总结'};
const metricNames={likes_per_100:'每百次播放点赞数',plays:'播放量',likes:'点赞量',completion_rate:'完播率',bounce_2s_rate:'2秒跳出率'};
const decisionNames={keep:'继续保留',retry:'再试一条',drop:'暂不采用',inconclusive:'暂不能判断'};
let source=null,current=null,records=[],samples=[],dirty=false,busy=false,sourceVersion=0,parent=null;
let refreshingOptions=null;
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function workTitle(id){return samples.find(s=>s.sample_id===id)?.title||id;}
function chainGoal(record){return record.chain_goal??record.source.goal;}
function eligibleSources(runs){
  return parent?runs.filter(r=>r.sample_id===parent.target.sample_id&&r.goal===chainGoal(parent)):runs;
}
function sourceChoices(runs,preferred=''){
  const chosen=runs.find(r=>r.id===preferred),ordered=chosen?[chosen,...runs.filter(r=>r.id!==preferred)]:runs;
  const seen=new Set();return ordered.filter(r=>{if(seen.has(r.sample_id))return false;seen.add(r.sample_id);return true;});
}
function fillSourceChoices(runs,preferred=''){
  const choices=sourceChoices(runs,preferred);$('source-run').replaceChildren(new Option('选择作品 A',''));
  for(const r of choices)$('source-run').add(new Option(workTitle(r.sample_id),r.id));
  if(choices.some(r=>r.id===preferred))$('source-run').value=preferred;
  else if(choices.length===1)$('source-run').value=choices[0].id;
}
function message(value){$('cycle-message').textContent=value;}
async function api(path,body){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
  try{const headers=body?{'Content-Type':'application/json','X-Review-Token':(await api('/api/session')).token}:{};
    const r=await fetch(path,{method:body?'POST':'GET',headers,body:body?JSON.stringify(body):undefined,signal:controller.signal});
    const result=await r.json();if(!r.ok)throw new Error(result.error||'请求失败');return result;
  }catch(e){if(e.name==='AbortError'||e instanceof TypeError)throw new Error('服务连接失败或超时。请保留填写内容，恢复服务后查看已有记录；不会自动重试提交。');throw e;}finally{clearTimeout(timer);}
}
function field(title,id,value='',kind='textarea'){
  const label=node('label',undefined,'field');label.append(node('span',title));const input=node(kind);input.id=id;input.value=value;input.maxLength=5000;label.append(input);return label;
}
function selectField(title,id,options,value){const label=node('label',undefined,'field');label.append(node('span',title));const input=node('select');input.id=id;for(const [v,t] of Object.entries(options))input.add(new Option(t,v));input.value=value;label.append(input);return label;}
function localDate(value){return value?new Date(new Date(value).getTime()+8*3600000).toISOString().slice(0,16):'';}
function dateValue(id){return $(id).value?$(id).value+':00+08:00':'';}
function base(){return {id:current.id,revision:current.revision};}
function existingChoice(){return !dirty||confirm('还有未保存的内容，确定切换吗？');}
async function listCycles(){const list=await api('/api/cycles');$('cycle-picker').replaceChildren(new Option('新建计划',''));for(const c of list.records)$('cycle-picker').add(new Option(`${c.title} · ${statusNames[c.status]}`,c.id));if(current)$('cycle-picker').value=current.id;if(list.unreadable)message(`${list.unreadable} 条旧记录校验未通过，未混入当前列表。`);}
async function options(){const data=await api('/api/samples');samples=data.samples;records=(await api('/api/metrics')).records.filter(r=>r.status==='confirmed');}
function fillSamples(preferred){
  const previous=preferred??current.target?.sample_id??'';
  const input=$('target-sample');
  input.replaceChildren(new Option('请选择已登记的视频 B',''));
  for(const sample of samples.filter(s=>s.sample_id!==current.source.sample_id)){
    const matches=sample.goal===chainGoal(current);
    const option=new Option(workTitle(sample.sample_id)+(matches?'':' · 目标不同'),sample.sample_id);
    option.disabled=!matches;
    input.add(option);
  }
  input.value=previous;
}
function fillRecords(side,sample,preferred){const input=$('record-'+side),previous=preferred??input.value;input.replaceChildren(new Option('请选择已核对记录',''));for(const r of records){const c=r.confirmation;if(!c.sample_id||c.sample_id===sample)input.add(new Option(`${c.title} · ${c.observed_at}`,r.id));}input.value=previous;showRecord(side);}
function showRecord(side){const r=records.find(r=>r.id===$('record-'+side).value),published=$('published-'+side);if(r&&published.dataset.record!==r.id){published.value=localDate(r.confirmation.published_at);published.dataset.record=r.id;}$('record-'+side+'-info').textContent=r?`统计时间 ${r.confirmation.observed_at}；范围：${{unknown:'不确定',cumulative:'截至截图累计',period:'指定时段'}[r.confirmation.scope]}；播放 ${r.confirmation.normalized.plays??'未知'}，点赞 ${r.confirmation.normalized.likes??'未知'}。`:'';}
async function loadSource(){const version=++sourceVersion;source=null;$('suggestions').replaceChildren();$('source-goal').textContent='';if(!$('source-run').value)return;try{const result=await api('/api/cycles/source?run='+encodeURIComponent($('source-run').value));if(version!==sourceVersion)return;source=result;$('source-goal').textContent=`作品 A：${workTitle(source.sample_id)}；目标：${source.goal}`;for(const f of source.findings){const card=node('div',undefined,'suggestion');const check=node('label',undefined,'check'),input=node('input');input.type='checkbox';input.id='pick-'+f.id;check.append(input,document.createTextNode(f.suggestion));const change=field('在 B 中具体怎么做','change-'+f.id);change.querySelector('textarea').placeholder='写可执行动作，例如：前 2 秒先展示粒子成品，再讲如何生成。';card.append(check,change);$('suggestions').append(card);}if(!source.findings.length)$('suggestions').append(node('p','这份复核没有可选建议。'));}catch(e){message(e.message);}}
function render(){
  dirty=false;$('create-cycle').hidden=true;$('cycle-detail').hidden=false;$('cycle-picker').value=current.id;
  $('detail-title').textContent=current.title;$('cycle-status').textContent=current.detached_from_chain?'已移出创作链':statusNames[current.status];
  $('plan-summary').textContent=`作品 A：${workTitle(current.source.sample_id)}；目标：${current.source.goal}。主指标：${metricNames[current.primary_metric]}，期望${current.expected_direction==='increase'?'升高':'降低'}。统一看发布后 ${current.window_hours} 小时，容差 ${current.tolerance_minutes} 分钟。`;
  $('planned-items').replaceChildren(...current.items.map(i=>node('li',i.planned_change)));
  $('cycle-report').href='/api/cycles/report?id='+current.id;$('lineage').textContent=current.parent_id?'已关联上一轮：'+current.parent_id:'';
  fillSamples();$('execution-items').replaceChildren();
  for(const item of current.items){const e=current.target?.executions.find(e=>e.finding_id===item.finding_id);const group=node('section',undefined,'execution');group.append(node('h3',item.planned_change),selectField('B 中的采用情况','applied-'+item.finding_id,{'':'请确认采用情况',not_applied:'未采用',partial:'部分采用',applied:'已采用'},e?.status||''),field('实际做法 / 未采用原因','note-'+item.finding_id,e?.note??item.planned_change));group.querySelector('select').required=true;$('execution-items').append(group);}
  const target=current.target;$('comparison-section').hidden=!target;
  if(target){$('label-a').textContent='作品 A · '+workTitle(current.source.sample_id);$('label-b').textContent='作品 B · '+workTitle(target.sample_id);$('window-instruction').textContent=`两边都需发布后 ${current.window_hours} 小时（± ${current.tolerance_minutes} 分钟）的累计截图；两边实际时长之差也不能超过该容差。`;
    fillRecords('a',current.source.sample_id,current.comparison?.a?.source.record_id??'');fillRecords('b',target.sample_id,current.comparison?.b?.source.record_id??'');
    $('published-a').value=localDate(current.comparison?.published_a||records.find(r=>r.id===$('record-a').value)?.confirmation.published_at);$('published-b').value=localDate(current.comparison?.published_b||records.find(r=>r.id===$('record-b').value)?.confirmation.published_at);
    const sameGoal=current.source.goal===chainGoal(current)&&target.goal===chainGoal(current);
    $('confounders').value=current.comparison?.confounders||'';$('screenshots-match').checked=!!current.comparison?.screenshots_match;$('same-goal').checked=sameGoal||!!current.comparison?.same_goal_confirmed;
    $('same-goal').closest('label').hidden=sameGoal;
    $('upload-a').href='/metrics?sample='+encodeURIComponent(current.source.sample_id);$('upload-b').href='/metrics?sample='+encodeURIComponent(target.sample_id);}
  renderComparison();
  const locked=current.status==='decided';for(const form of [$('implementation-form'),$('comparison-form')])for(const input of form.elements)input.disabled=locked;$('defer-comparison').disabled=locked;
  $('decision-form').hidden=!current.comparison||locked;$('decision-items').replaceChildren();
  const choices=current.comparison?.result.status==='ready'?decisionNames:{inconclusive:decisionNames.inconclusive};
  for(const item of current.items){const group=node('section',undefined,'execution');group.append(node('h3',item.planned_change),selectField('下一轮决定','decision-'+item.finding_id,choices,'inconclusive'),field('判断理由和下一条行动','reason-'+item.finding_id));$('decision-items').append(group);}
  $('next-round').hidden=!locked;$('decisions-summary').replaceChildren(...current.decisions.map(d=>node('p',`${current.items.find(i=>i.finding_id===d.finding_id).planned_change} → ${decisionNames[d.decision]}：${d.note}`)));
  history.replaceState(null,'','/cycles?id='+current.id);
}
function renderComparison(){$('comparison-inputs').open=!current.comparison||current.comparison.result.status!=='ready';const box=$('comparison-result');box.replaceChildren();box.hidden=!current.comparison;if(box.hidden)return;const c=current.comparison,r=c.result;box.append(node('h2',r.status==='ready'?'对比结果 · 描述变化，留待判断':'暂不能比较'));
  for(const reason of r.reasons)box.append(node('p',reason,'blocked'));
  if(r.retrospective)box.append(node('p','回顾性比较：B 在计划登记前已经发布，本轮不能算作预先设计的试验。','boundary'));
  if(r.actual_hours)box.append(node('p',`实际观察：A ${r.actual_hours.a} 小时；B ${r.actual_hours.b} 小时。`,'helper'));
  if(r.rows.length){const wrapper=node('div',undefined,'table-scroll'),table=node('table',undefined,'comparison-table'),head=node('tr');for(const title of ['指标',workTitle(current.source.sample_id),workTitle(current.target.sample_id),'后者 − 前者','相对变化'])head.append(node('th',title));const thead=node('thead');thead.append(head);table.append(thead);const tbody=node('tbody');for(const row of r.rows){const tr=node('tr',undefined,row.metric===current.primary_metric?'primary-row':'');const valueText=v=>v===null?'未知':String(v)+(row.metric.endsWith('_rate')?'%':'');for(const value of [row.label+(row.metric===current.primary_metric?'（主指标）':''),valueText(row.a),valueText(row.b),row.delta===null?'无法计算':`${row.delta>0?'+':''}${row.delta} ${row.delta_unit}`,row.relative_percent===null?'无法计算':`${row.relative_percent>0?'+':''}${row.relative_percent}%`])tr.append(node('td',String(value)));tbody.append(tr);}table.append(tbody);wrapper.append(table);box.append(wrapper);}
  for(const side of ['a','b'])if(c[side]){const link=node('a',`核查 ${workTitle(side==='a'?current.source.sample_id:current.target.sample_id)} 的原图`);link.href='/metrics?id='+c[side].source.record_id;link.onclick=e=>{e.preventDefault();openWork(link.href,'核对数据截图');};const p=node('p');p.append(link);box.append(p);}
  box.append(node('p','其他差异：'+c.confounders));const details=node('details');details.append(node('summary','如何理解这次比较'));for(const line of r.limitations)details.append(node('p',line,'helper'));box.append(details);
}
async function mutate(action,payload){
  if(busy)return;
  busy=true;
  message('正在保存…');
  const buttons=Array.from(document.querySelectorAll('button'));
  const disabled=buttons.map(b=>b.disabled);
  const restoreButtons=()=>buttons.forEach((b,i)=>{if(b.isConnected)b.disabled=disabled[i];});
  buttons.forEach(b=>b.disabled=true);
  try{
    current=await api('/api/cycles/'+action,payload);
    await options();
    await listCycles();
    restoreButtons();
    render();
    message(action==='compare'
      ?(current.comparison.result.status==='ready'?'已完成数据对比，请逐条记录下一步行动。':'数据未对齐，可补齐后重新比较，也可总结为暂不能判断。')
      :action==='create'?'计划已保存，请准备下一条视频。':'记录已保存。');
  }catch(e){
    restoreButtons();
    message(e.message+' 若结果不确定，可刷新已有记录核查。');
  }finally{
    busy=false;
    if(typeof syncFlow==='function')syncFlow();
  }
}
$('source-run').onchange=()=>{dirty=false;loadSource();};
$('create-cycle').onsubmit=e=>{
  e.preventDefault();
  if(!source){message('请先选择已提交复核。');return;}
  const items=source.findings.filter(f=>$('pick-'+f.id).checked)
    .map(f=>({finding_id:f.id,planned_change:$('change-'+f.id).value}));
  mutate('create',{
    source_run:source.run,
    items,
    window_hours:Number($('window-hours').value),
    tolerance_minutes:Number($('window-tolerance').value),
    primary_metric:$('primary-metric').value,
    expected_direction:$('expected-direction').value,
    parent_id:parent?.id||null,
  });
};
$('implementation-form').onsubmit=e=>{e.preventDefault();mutate('implement',{...base(),sample_id:$('target-sample').value,executions:current.items.map(i=>({finding_id:i.finding_id,status:$('applied-'+i.finding_id).value,note:$('note-'+i.finding_id).value,evidence:current.target?.sample_id===$('target-sample').value?(current.target.executions.find(e=>e.finding_id===i.finding_id)?.evidence||''):''}))});};
$('comparison-form').onsubmit=e=>{e.preventDefault();mutate('compare',{...base(),record_a:$('record-a').value,record_b:$('record-b').value,published_a:dateValue('published-a'),published_b:dateValue('published-b'),screenshots_match:$('screenshots-match').checked,same_goal_confirmed:$('same-goal').checked,confounders:$('confounders').value});};
$('defer-comparison').onclick=()=>mutate('defer',{...base(),reason:$('missing-reason').value});
$('decision-form').onsubmit=e=>{e.preventDefault();mutate('decide',{...base(),decisions:current.items.map(i=>({finding_id:i.finding_id,decision:$('decision-'+i.finding_id).value,note:$('reason-'+i.finding_id).value}))});};
async function openCycle(id,showComparison=false,resume=false){
  if(!id||busy||!existingChoice()){$('cycle-picker').value=current?.id||'';return;}
  busy=true;
  try{
    const requested=await api('/api/cycles/detail?id='+encodeURIComponent(id));
    current=resume&&requested.detached_from_chain
      ?await api('/api/cycles/detail?id='+encodeURIComponent(requested.detached_from_chain))
      :requested;
    parent=null;
    $('workspace-menu').open=false;
    render();
    if(showComparison)$('single-round').open=true;
    message('');
    window.scrollTo({top:0});
  }catch(e){$('cycle-picker').value=current?.id||'';message(e.message);}
  finally{busy=false;}
}
$('cycle-picker').onchange=()=>openCycle($('cycle-picker').value);
$('new-cycle').onclick=()=>{if(existingChoice()){dirty=false;location.href='/cycles';}};
async function refreshAvailableRecords(announce=false){
  if(!current||busy)return;
  if(refreshingOptions)return refreshingOptions;
  const cycleId=current.id,choice=$('target-sample').value;
  refreshingOptions=(async()=>{try{await options();if(current?.id!==cycleId)return;fillSamples(choice);if(current.target){fillRecords('a',current.source.sample_id);fillRecords('b',current.target.sample_id);}if(typeof refreshBReview==='function')await refreshBReview();if(announce)message('作品与数据已更新，可以继续当前步骤。');}catch(e){message('截图列表更新失败：'+e.message);}finally{refreshingOptions=null;}})();
  return refreshingOptions;
}
$('refresh-options').onclick=()=>refreshAvailableRecords(true);
window.addEventListener('focus',()=>refreshAvailableRecords());
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')refreshAvailableRecords();});
$('primary-metric').onchange=()=>{$('expected-direction').value=$('primary-metric').value==='bounce_2s_rate'?'decrease':'increase';};
for(const side of ['a','b'])$('record-'+side).onchange=()=>showRecord(side);
document.addEventListener('input',e=>{if(e.target.closest('form'))dirty=true;});document.addEventListener('change',e=>{if(e.target.closest('form'))dirty=true;});
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
(async()=>{try{await options();await listCycles();if(query.get('parent')){parent=await api('/api/cycles/detail?id='+encodeURIComponent(query.get('parent')));if(parent.status!=='decided')throw new Error('请先完成上一轮总结');$('parent-note').textContent='上一轮行动：'+parent.decisions.map(d=>decisionNames[d.decision]+'：'+d.note).join('；');}
  const runs=eligibleSources((await api('/api/cycles/sources')).runs);fillSourceChoices(runs,query.get('run')||'');
  if(!runs.length)message(parent?'B 还没有已提交复核，请先复盘 B 并完成人工复核。':'请先完成一条视频的人工复核，再制定下一条计划。');
  if($('source-run').value)await loadSource();
  const requested=query.get('id')||(!query.has('fresh')&&!query.has('run')&&!query.has('parent')&&localStorage.getItem('review-active-cycle'));
  if(requested)await openCycle(requested,query.get('round')==='1',!query.has('id'));
}catch(e){message(e.message);}})();
