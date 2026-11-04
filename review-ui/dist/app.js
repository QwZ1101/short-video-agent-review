'use strict';
const $ = id => document.getElementById(id);
const labels = {observation:'观察到的情况', interpretation:'可能的影响', suggestion:'下一条建议怎么做', validation_plan:'下一条如何观察效果'};
const decisions = {pending:'待复核',accept:'保留候选稿',revise:'修改后采用',unsure:'暂不确定'};
const statuses = {awaiting_human_review:'等待人工复核',human_reviewed:'人工复核已提交'};
let data, feedback, selected=0, token='', dirty=false, busy=false;
function showView(view){
  $('review-view').hidden=view!=='review';$('tasks-view').hidden=view!=='tasks';
  $('view-review').setAttribute('aria-pressed',String(view==='review'));
  $('view-tasks').setAttribute('aria-pressed',String(view==='tasks'));
}
function el(tag, text, cls) { const node=document.createElement(tag); if(text!==undefined)node.textContent=text; if(cls)node.className=cls; return node; }
function message(text) {$('message').textContent=text;$('save-message').textContent=text;}
function draftKey(){return 'review-draft:'+data.id;}
function backupDraft(){
  try {sessionStorage.setItem(draftKey(),JSON.stringify(feedback));return true;}
  catch {return false;}
}
function clearBackup(){try{sessionStorage.removeItem(draftKey());}catch{}}
function restoreBackup(){
  try{
    const cached=JSON.parse(sessionStorage.getItem(draftKey())||'null');
    if(!cached||locked())return false;
    if(cached.revision!==feedback.revision||cached.candidate_sha256!==feedback.candidate_sha256||cached.source_sha256!==feedback.source_sha256)return false;
    if(JSON.stringify(cached)===JSON.stringify(feedback))return false;
    feedback=cached;dirty=true;return true;
  }catch{return false;}
}
async function api(path, options={}) {
  const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),20000);
  try{
    const response=await fetch(path,{...options,signal:controller.signal});const result=await response.json();
    if(!response.ok)throw new Error(result.error||'请求失败，请稍后再试');
    return result;
  }catch(error){
    if(error.name==='AbortError'||error instanceof TypeError)throw new Error('无法连接本机服务，或响应超时。请保持本页打开，双击“启动复核工作台.cmd”恢复服务，再点击保存或提交；本次操作未确认成功，不会自动重试。');
    throw error;
  }finally{clearTimeout(timer);}
}
function current(){return selected===0?feedback.summary:feedback.findings[selected-1];}
function source(){return selected===0?{text:data.candidate.summary}:data.candidate.findings[selected-1];}
function locked(){return feedback.status==='accepted';}
function markDirty(){dirty=true; $('saved').textContent=backupDraft()?'未提交 · 已备份到当前标签页':'有未保存的修改 · 浏览器备份不可用，请勿刷新'; progress();}
function progress(){
  const all=[feedback.summary,...feedback.findings];
  const done=all.filter(x=>x.decision!=='pending').length;
  $('progress').textContent=`已复核 ${done} / ${all.length} 项（含总结）`;
  $('save').disabled=locked()||busy;
  $('save').textContent=busy?'正在处理…':'保存草稿';
  $('submit').disabled=locked()||busy||done!==all.length;
  $('submit').firstChild.textContent=busy?'正在处理… ':all.some(x=>x.decision==='unsure')?'提交 · 等待补证 ':'提交本轮复核 ';
  [...$('tabs').children].forEach((button,i)=>{button.textContent=(i===0?'整体总结':`建议 ${i}`)+(all[i].decision==='pending'?'':' · '+(all[i].decision==='unsure'?'?':'✓'));});
}
function tabs(){
  $('tabs').replaceChildren();
  [feedback.summary,...feedback.findings].forEach((entry,i)=>{
    const button=el('button',''); button.className=i===selected?'selected':'';
    button.setAttribute('aria-pressed',String(i===selected));
    button.onclick=()=>{selected=i;tabs();renderCard();renderEvidenceOptions();};
    $('tabs').append(button);
  });progress();
}
function field(key,label){
  const wrapper=el('label',undefined,'field');wrapper.append(el('span',label));
  const input=el('textarea'); input.value=current()[key];input.readOnly=locked()||current().decision!=='revise';
  input.rows=key==='text'?5:key==='suggestion'?4:3;
  input.oninput=()=>{current()[key]=input.value;markDirty();};wrapper.append(input);return wrapper;
}
function renderCard(){
  const card=$('review-card'); card.replaceChildren();
  const entry=current(); const summary=selected===0;
  const old=summary?{text:data.original.summary}:data.original.findings.find(x=>x.id===entry.id);
  const check=summary?data.verification.summary_check:data.verification.checks.find(x=>x.finding_id===entry.id);
  const head=el('div',undefined,'card-title');head.append(el('h3',summary?'先核对整体判断':`建议 ${String(selected).padStart(2,'0')}`),el('span',decisions[entry.decision],'badge'));card.append(head);
  const compare=el('div',undefined,'comparison');
  const original=el('details');original.append(el('summary','展开原始分析','mini-label'));
  for(const key of summary?['text']:Object.keys(labels)){
    if(!summary) original.append(el('div',labels[key],'mini-label'));
    original.append(el('p',old[key],'original-text'));
  }
  compare.append(original);
  const verifier=el('div',undefined,'verifier');
  verifier.append(el('strong','验证模型意见 · '+({supported:'认为证据支持',revise:'建议修改',insufficient_evidence:'证据不足'}[check.status]||check.status)));
  verifier.append(el('p',check.reason),el('div','模型意见也需要你核对。','helper'));compare.append(verifier);card.append(compare);
  card.append(el('div','候选稿 / 你的修改','mini-label'));
  if(summary)card.append(field('text','整体总结'));else Object.entries(labels).forEach(([key,label])=>card.append(field(key,label)));
  const area=el('section',undefined,'decision-area');area.append(el('div','你的决定','mini-label'));
  const choices=el('div',undefined,'choices');
  for(const [value,label] of Object.entries(decisions)){
    const wrapper=el('label',undefined,'choice'); const input=el('input');input.type='radio';input.name='decision';input.value=value;input.checked=entry.decision===value;input.disabled=locked();
    input.onchange=()=>{entry.decision=value;markDirty();tabs();renderCard();};wrapper.append(input,el('span',label));choices.append(wrapper);
  }
  area.append(choices,el('p','选择“修改后采用”即可编辑上方文字；修改或暂不确定时需写明理由。','helper'));
  const noteLabel=el('label',undefined,'field');noteLabel.append(el('span','复核理由'));
  const note=el('textarea');note.rows=2;note.placeholder='例如：没有观众操作数据，不能断言“极高的出错率”。';note.value=entry.note;note.readOnly=locked();note.oninput=()=>{entry.note=note.value;markDirty();};noteLabel.append(note);area.append(noteLabel);card.append(area);
}
function evidenceLabel(id,item){
  const names={'material-profile':'个人简介截图','material-comments':'评论区截图','material-website':'源码目录截图','material-download':'源码包清单与 README','context-delivery':'用户说明的获取路径'};
  if(names[id])return names[id];
  if(item.type==='video_frame')return `视频抽帧 · 请求位置 ${Number(item.requested_time_seconds).toFixed(2)} 秒`;
  if(item.type==='subtitle')return `字幕 ${id.split('-').pop()} · ${item.text}`;
  return id;
}
function renderEvidenceOptions(){
  const entry=current();
  const check=selected===0?data.verification.summary_check:data.verification.checks.find(x=>x.finding_id===entry.id);
  const refs=new Set([...(check.evidence_ids||[]),...(selected===0?[]:source().evidence_ids)]);
  const entries=Object.entries(data.catalog).sort((a,b)=>Number(refs.has(b[0]))-Number(refs.has(a[0])));
  $('evidence-select').replaceChildren();
  for(const [id,item] of entries){const option=el('option',(refs.has(id)?'引用 · ':'')+evidenceLabel(id,item));option.value=id;$('evidence-select').append(option);}
  $('evidence-count').textContent=`${refs.size} 条引用 / ${entries.length} 条材料`;
  renderEvidence();
}
function renderEvidence(){
  const id=$('evidence-select').value; const item=data.catalog[id]; const view=$('evidence-view');view.replaceChildren();if(!item)return;
  if(item.sha256){
    const link=el('a');link.href=`/api/evidence?run=${encodeURIComponent(data.id)}&id=${encodeURIComponent(id)}`;link.target='_blank';link.rel='noopener';
    const img=el('img',undefined,'evidence-image');img.src=link.href;img.alt=evidenceLabel(id,item);img.onerror=()=>{message('证据图片加载失败，请检查文件是否仍存在。');};link.append(img);view.append(link);
    view.append(el('div',id+' · 点击图片查看原尺寸'+(item.type==='video_frame'?'。时间是抽帧请求位置，并非精确帧时间。':''),'evidence-caption'));
  }else{
    let text=item.text||'';
    if(item.readmes)text='文件清单\n'+item.files.join('\n')+'\n\n'+item.readmes.map(x=>x.filename+'\n'+x.text).join('\n\n');
    if(item.content)text=typeof item.content==='string'?item.content:JSON.stringify(item.content,null,2);
    view.append(el('pre',text||JSON.stringify(item,null,2),'evidence-text'),el('div',[id,item.source,item.scope].filter(Boolean).join(' · '),'evidence-caption'));
  }
}
async function loadRun(identifier,selectedIndex=0){
  $('loading').hidden=false;$('workspace').hidden=true;message('');
  try{
    data=await api('/api/review?run='+encodeURIComponent(identifier));feedback=structuredClone(data.feedback);selected=Math.min(selectedIndex,feedback.findings.length);dirty=false;
    const restored=restoreBackup();
    $('runs').value=data.id;$('sample').textContent=data.sample_id+' / '+({verify:'复用分析 · 新验证',live:'完整运行',replay:'历史结果回放',resume:'恢复验证',refine:'受控返修'}[data.mode]||data.mode);
    $('goal').textContent=data.goal_changed?data.current_goal:data.task.tutorial_goal;$('audience').textContent=data.goal_changed?'下方为旧目标历史报告：'+data.task.tutorial_goal:'目标观众：'+(data.task.audience_prior_knowledge||data.task.audience||'未提供');
    $('status').textContent=feedback.status==='needs_evidence'?'等待补充证据':statuses[data.status]||data.status;
    if(data.refinement?.status==='awaiting_evidence'&&!locked())$('status').textContent='等待补证 / 人工复核';
    $('coverage').textContent=`${data.coverage.image_count} 张图片 · ${data.coverage.subtitle_count} 段字幕`;
    const runtime=feedback.supplemental_evidence?.some(x=>x.snapshot?.runtime_result?.status==='passed_user_reported');
    $('boundary').textContent=data.goal_changed?'目标已调整。下方报告和修改任务仅作历史记录，下载源码和本地运行不再是当前待办；新目标尚需重新复盘。':'模型依据抽帧、字幕和配套材料分析。'+(runtime?'已记录创作者反馈：源码在本机正常运行。':'')+'是否达到本轮目标，需要相应的实际记录验证。';
    $('count').textContent=`${feedback.findings.length} 条建议 + 总结`;
    $('limitations').replaceChildren(...[...new Set([...data.candidate.limitations,...data.verification.limitations])].map(x=>el('li',x)));
    $('saved').textContent=feedback.revision?`已保存第 ${feedback.revision} 版 · ${new Date(feedback.saved_at).toLocaleString('zh-CN')}`:'尚未保存人工反馈';
    $('report').hidden=!feedback.revision;$('report').href='/api/report?run='+encodeURIComponent(data.id);
    tabs();renderCard();renderEvidenceOptions();$('workspace').hidden=false;
    $('workspace-views').hidden=!locked();
    $('partial-cycle').hidden=restored||feedback.status!=='needs_evidence'||!feedback.findings.some(f=>['accept','revise'].includes(f.decision));
    $('partial-cycle-link').href='/cycles?run='+encodeURIComponent(data.id);
    showView(locked()?'tasks':'review');
    await window.TaskBoard.load(data.id,locked());
    if(locked())message(feedback.provenance?.actor==='assistant_on_user_request'?'本轮由助手按你的授权整理并提交，历史记录已保留。接下来挑选建议，用于下一条视频。':'本轮复核已提交，接下来挑选建议，用于下一条视频。');
    if(feedback.status==='needs_evidence')message('已记录不确定项，流程仍等待补证。补齐材料后可继续修改并提交。');
    if(data.refinement?.stop_reason&&!locked())message('返修已停止：'+data.refinement.stop_reason);
    if(data.goal_changed)message('当前目标：索要展示网页链接，或者点赞。下面保留旧目标的结果，不代表新目标复盘已完成。');
    if(restored){$('saved').textContent='已恢复当前标签页的未提交草稿';message('已恢复此前填写内容，请核对后保存或提交。');}
  }catch(error){message(error.message);}finally{$('loading').hidden=true;}
}
async function save(submit){
  if(busy)return;busy=true;progress();backupDraft();message(submit?'正在提交，请稍候…':'正在保存草稿，请稍候…');
  try{
    // Reacquire after a server restart without discarding the current form.
    // Only GET is done here; never automatically repeat a mutation.
    token=(await api('/api/session')).token;
    const result=await api(submit?'/api/submit':'/api/draft',{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':token},body:JSON.stringify({run:data.id,feedback})});
    feedback=result.feedback;dirty=false;clearBackup();
    await loadRun(data.id,selected);
    message(submit?(feedback.status==='accepted'?'本轮复核已提交，已生成带来源记录的人工修订结果。':'反馈已提交。不确定项已保留，等待补充证据。'):'草稿已保存，可以稍后回来继续。');
  }catch(error){message(error.message);}finally{busy=false;progress();}
}
$('evidence-select').onchange=renderEvidence;
$('runs').onchange=()=>{const next=$('runs').value;if((dirty||window.TaskBoard.isDirty())&&!confirm('有未保存的修改。确定切换任务并放弃这些修改吗？')){$('runs').value=data.id;return;}loadRun(next);};
$('save').onclick=()=>save(false);$('submit').onclick=()=>save(true);
$('view-review').onclick=()=>showView('review');$('view-tasks').onclick=()=>showView('tasks');
window.addEventListener('beforeunload',event=>{if(dirty||window.TaskBoard.isDirty()){event.preventDefault();event.returnValue='';}});
(async()=>{try{token=(await api('/api/session')).token;const list=await api('/api/runs');$('runs').replaceChildren();
  for(const run of list.runs){const option=el('option',run.sample_id+' · '+run.id.replace(/^(run|resume)-/,''));option.value=run.id;$('runs').append(option);}
  if(!list.runs.length){$('loading').hidden=true;message('还没有可以复核的任务。请先完成内容分析与证据验证。');return;}
  const requested=new URLSearchParams(location.search).get('run');
  await loadRun(list.runs.some(x=>x.id===requested)?requested:list.runs.some(x=>x.id===list.selected)?list.selected:list.runs[0].id);
}catch(error){$('loading').hidden=true;message('工作台连接失败：'+error.message);}})();
