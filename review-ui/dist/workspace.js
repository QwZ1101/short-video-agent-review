'use strict';
// One workspace orchestrates the existing audited tools without submitting reviews for the user.
let flowStep=0,bReview=null,bPending=null,bReviewRequest=0;
let missingSide=null;
const flowTitles=['先复盘一条视频','挑选下一条要试的建议','复盘下一条视频','比较数据，完成总结'];
const flowDescriptions=['选择一条视频，完成复盘。','选 1～2 条建议，用在下一条视频。','选择下一条视频，复盘后确认采用了哪些建议。','核对两条视频的数据，记录下一轮怎么做。'];
function resumeStep(){
  if(!current)return flowStep;
  if(current.comparison||['decided','closed'].includes(current.status))return 3;
  return current.target&&bReview&&$('target-sample').value===current.target.sample_id?3:2;
}
const originalRender=render;
render=function(){
  originalRender();
  bReview=null;
  bPending=null;
  missingSide=undefined;
  localStorage.setItem('review-active-cycle',current.detached_from_chain||current.id);
  flowStep=resumeStep();
  syncFlow();
  refreshBReview().then(()=>{
    if(!dirty&&$('inline-work').hidden){flowStep=resumeStep();syncFlow();}
  });
  restoreWorkMaterials();
  if(!current.detached_from_chain)window.ChainOverview?.load(current.id);
  $('single-round').open=!current.comparison||current.comparison.result.status!=='ready';
  $('round-plan').open=false;
};
async function restoreWorkMaterials(){if(!current?.target||current.comparison)return;const id=current.id;try{const bindings=await Promise.all([current.source.sample_id,current.target.sample_id].map(s=>api('/api/samples/materials?sample='+encodeURIComponent(s))));if(current?.id!==id||current.comparison)return;for(const [i,side] of ['a','b'].entries()){const input=$('record-'+side);if(!input.value&&bindings[i].metrics_record_id){input.value=bindings[i].metrics_record_id;showRecord(side);}}updateComparisonAction();}catch(e){message('作品材料未能自动带入：'+e.message);}}
function syncFlow(){
  const editing=!$('inline-work').hidden;
  const closed=['decided','closed'].includes(current?.status);
  const archived=!!current?.detached_from_chain;
  $('flow-progress').textContent=archived?'已移出创作链':closed?'已结束':`第 ${flowStep+1} 步 / 共 4 步`;
  $('flow-title').textContent=archived?'历史轮次':closed?'本轮总结':flowTitles[flowStep];
  $('flow-steps').hidden=archived;
  $('flow-description').textContent=closed?'':flowDescriptions[flowStep];
  for(const marker of document.querySelectorAll('[data-step]')){
    const step=Number(marker.dataset.step);
    marker.classList.toggle('active',step===flowStep);
    marker.classList.toggle('done',step<flowStep||closed);
    if(step===flowStep)marker.setAttribute('aria-current','step');
    else marker.removeAttribute('aria-current');
  }
  $('workspace-menu').hidden=editing;
  $('prepare-a').hidden=flowStep!==0||editing;
  $('create-cycle').hidden=!!current||flowStep!==1||editing;
  $('back-to-source').hidden=!!parent;
  $('cycle-detail').hidden=!current||flowStep<1||editing;
  $('exit-cycle').hidden=!current||closed;
  if(!current)return;
  $('locked-chain-goal').textContent='创作链目标 · '+chainGoal(current);
  updateComparisonAction();
  $('chain-overview').hidden=archived||(!current.target&&!closed);
  $('single-round').hidden=flowStep!==3||(closed&&!current.comparison);
  $('b-review-panel').hidden=flowStep!==2||closed||!!bReview;
  $('implementation-form').hidden=flowStep!==2||closed;
  $('execution-items').hidden=!bReview;
  $('confirm-b').hidden=!bReview;
  $('adoption-help').hidden=!bReview;
  $('compare-gate').hidden=flowStep!==3||!!bReview||closed;
  $('comparison-section').hidden=flowStep!==3||!current.target||(!bReview&&!closed)||closed;
  $('comparison-result').hidden=flowStep!==3||!current.comparison;
  $('decision-form').hidden=flowStep!==3||!current.comparison||closed||!bReview;
  $('next-round').hidden=flowStep!==3||!closed;
  if(current.status==='closed')$('decisions-summary').textContent='可以选择新视频继续复盘。';
  $('continue-cycle').textContent=archived?'返回原创作链':current.status==='closed'?'从新视频开始':'继续下一轮';
}
function updateComparisonAction(){
  if(!current.target)return;
  const side=['a','b'].find(side=>!$('record-'+side).value)||null;
  const ready=current.comparison?.result.status==='ready';
  $('comparison-next').hidden=!side||ready;
  if(side){
    const sample=side==='a'?current.source.sample_id:current.target.sample_id;
    $('comparison-next-text').textContent=workTitle(sample)+' · 缺少发布后 '+current.window_hours+' 小时的累计截图';
    $('add-missing-data').onclick=()=>$('upload-'+side).click();
  }
  if(side!==missingSide){
    $('comparison-inputs').open=!side&&!ready;
    missingSide=side;
  }
}
for(const side of ['a','b']){
  const input=$('record-'+side);
  input.addEventListener('change',updateComparisonAction);
}
async function refreshBReview(){
  const target=$('target-sample').value;
  const request=++bReviewRequest;
  bReview=null;bPending=null;syncFlow();
  if(!current||!target){$('b-review-status').textContent='选择已有作品 B，或登记下一条新作品。';$('open-b-review').textContent='登记作品 B';return;}
  if(samples.find(s=>s.sample_id===target)?.goal!==chainGoal(current)){
    $('b-review-status').textContent='这条视频原先的目标不同。请换一条，或在本轮登记视频以沿用链路目标。';
    $('open-b-review').textContent='按链路目标登记视频';
    return;
  }
  const id=current.id;
  try{const all=await api('/api/runs');const latest=all.runs.find(r=>r.sample_id===target)||null;const detail=latest?await api('/api/review?run='+encodeURIComponent(latest.id)):null;if(request!==bReviewRequest||current?.id!==id||$('target-sample').value!==target)return;bPending=latest;bReview=detail&&!detail.goal_changed&&['accepted','needs_evidence'].includes(detail.feedback.status)?latest:null;$('b-review-status').textContent=bPending?'已有复盘，打开后完成人工复核。':'复盘完成后，回来确认采用情况。';$('open-b-review').textContent=bPending?'继续人工复核':'开始复盘';
    syncFlow();
  }catch(e){message(e.message);}
}
$('target-sample').onchange=()=>{
  if($('target-sample').value!==current?.target?.sample_id)for(const item of current.items){$('applied-'+item.finding_id).value='';$('note-'+item.finding_id).value=item.planned_change;}
  refreshBReview();
};
function goStep(step){flowStep=step;syncFlow();}
let workPurpose='',confirmedScreenshot=null;
function openWork(url,title,purpose='inspect'){
  const target=new URL(url,location.origin);
  if(['/start','/metrics'].includes(target.pathname))target.searchParams.set('embed','1');
  if(purpose==='target'&&target.pathname==='/start')target.searchParams.set('cycle',current.id);
  workPurpose=purpose;
  confirmedScreenshot=null;
  $('inline-title').textContent=title;
  $('inline-work').hidden=false;
  $('workspace-menu').open=false;
  $('work-frame').src=target.href;
  syncFlow();
  $('inline-work').scrollIntoView({block:'start'});
}
window.addEventListener('message',event=>{
  if(event.origin===location.origin&&event.source===$('work-frame').contentWindow&&event.data?.type==='metrics-confirmed'){
    confirmedScreenshot=event.data.id;
  }
});
$('work-frame').onload=()=>{
  const doc=$('work-frame').contentDocument;if(!doc)return;
  const style=doc.createElement('link');style.rel='stylesheet';style.href='/embedded.css';doc.head.append(style);
  const form=doc.querySelector('#register-form');if(form){const details=doc.createElement('details'),summary=doc.createElement('summary');summary.textContent='字幕、受众与编号';details.append(summary);for(const name of ['sample_id','transcript','audience','prior_knowledge']){const label=form.querySelector(`[name="${name}"]`)?.closest('label');if(label)details.append(label);}form.insertBefore(details,form.querySelector('button'));}
  doc.addEventListener('click',e=>{const a=e.target.closest('a');if(!a)return;const u=new URL(a.href,location.origin);if(u.origin!==location.origin)return;
    if(u.pathname==='/cycles'){e.preventDefault();$('return-work').click();return;}
    if(['/','/start','/metrics','/logs'].includes(u.pathname)){
      e.preventDefault();
      if(!canLeaveWork())return;
      if(['/start','/metrics'].includes(u.pathname))u.searchParams.set('embed','1');
      if(workPurpose==='target'&&u.pathname==='/start'&&current)u.searchParams.set('cycle',current.id);
      $('work-frame').src=u.href;
    }});
  // Registration is already explicit; return the registered choice without another dropdown selection.
};
$('open-a').onclick=()=>openWork('/start','复盘视频 A','source');
$('choose-a').onclick=()=>{goStep(1);reloadSources();};
$('back-to-source').onclick=()=>{if(existingChoice())goStep(0);};
$('open-b-review').onclick=()=>{
  const reviewed=bReview||bPending;
  const sample=$('target-sample').value;
  const useExisting=sample&&samples.find(s=>s.sample_id===sample)?.goal===chainGoal(current);
  const url=reviewed?'/?run='+encodeURIComponent(reviewed.id):useExisting?'/start?sample='+encodeURIComponent(sample):'/start?register=1';
  openWork(url,'复盘视频 B','target');
};
$('back-b-review').onclick=()=>{goStep(2);$('open-b-review').click();};
async function reloadSources(preferred=$('source-run').value){const data=await api('/api/cycles/sources');fillSourceChoices(eligibleSources(data.runs),preferred);if(!current)await loadSource();}
function canLeaveWork(){
  const win=$('work-frame').contentWindow;
  if(win&&!win.dispatchEvent(new win.Event('beforeunload',{cancelable:true}))){
    message('请先保存内页的修改，再返回。');
    return false;
  }
  return true;
}
$('return-work').onclick=async()=>{
  const frame=$('work-frame'),doc=frame.contentDocument;
  if(!canLeaveWork())return;
  const purpose=workPurpose;
  const screenshot=confirmedScreenshot;
  const chosen=doc?.querySelector('#samples')?.value, reviewed=doc?.querySelector('#runs')?.value;
  message('');
  $('inline-work').hidden=true;
  frame.removeAttribute('src');
  try{
    if(current){
      await refreshAvailableRecords();
      if(screenshot&&['metrics-a','metrics-b'].includes(purpose)&&current.target){
        const side=purpose.slice(-1);
        const sample=side==='a'?current.source.sample_id:current.target.sample_id;
        const record=records.find(r=>r.id===screenshot&&r.confirmation.sample_id===sample);
        if(record){
          $('record-'+side).value=record.id;
          showRecord(side);
          dirty=true;
        }
      }
      if(purpose==='target'){
        const sample=reviewed?(await api('/api/runs')).runs.find(r=>r.id===reviewed)?.sample_id:chosen;
        if(sample&&sample!==current.source.sample_id&&sample!==$('target-sample').value){
          $('target-sample').value=sample;
          $('target-sample').onchange();
        }
        if(!dirty&&bReview&&current.target?.sample_id===$('target-sample').value)flowStep=3;
      }
      if(!current.detached_from_chain)window.ChainOverview?.load(current.id);
    }else if(purpose==='source'){
      const {runs}=await api('/api/cycles/sources');
      const run=runs.find(r=>r.id===reviewed)||runs.find(r=>chosen&&r.sample_id===chosen);
      if(run){flowStep=1;await reloadSources(run.id);}
    }
  }catch(e){message(e.message);}
  syncFlow();
  window.scrollTo({top:0});
};
for(const [id,title,purpose] of [['register-b','登记视频 B','target'],['upload-a','核对 A 的截图','metrics-a'],['upload-b','核对 B 的截图','metrics-b']])$(id).onclick=e=>{e.preventDefault();openWork($(id).href,title,purpose);};
// Expand only selected suggestions; retain user edits if unchecked and selected again.
const suggestionObserver=new MutationObserver(()=>{for(const card of $('suggestions').children){const checkbox=card.querySelector('input[type=checkbox]'),field=card.querySelector('.field');if(!checkbox||!field)continue;field.hidden=!checkbox.checked;checkbox.onchange=()=>{if(document.querySelectorAll('#suggestions input:checked').length>2){checkbox.checked=false;message('每轮最多选 2 条，先聚焦最值得试的改动。');return;}field.hidden=!checkbox.checked;if(checkbox.checked&&!field.querySelector('textarea').value)field.querySelector('textarea').value=checkbox.parentElement.textContent.trim();};}});
suggestionObserver.observe($('suggestions'),{childList:true});
async function startPlan(previous=null){
  if(busy||!existingChoice())return;
  if(previous){
    try{
      const {runs}=await api('/api/cycles/sources');
      if(!runs.some(r=>r.sample_id===previous.target.sample_id&&r.goal===chainGoal(previous))){
        message(previous.target.goal!==chainGoal(previous)
          ?'这条历史视频的目标与创作链不同，不能用这份复核接续。可从「更多」开启新链。'
          :'请先提交下一条视频的复核，并认可至少一条建议。');
        return;
      }
    }catch(e){message(e.message);return;}
  }
  current=null;
  parent=previous;
  source=null;
  dirty=false;
  localStorage.removeItem('review-active-cycle');
  $('workspace-menu').open=false;
  $('cycle-picker').value='';
  $('source-run').value='';
  $('create-cycle').reset();
  $('parent-note').textContent=parent?'接续视频 · '+workTitle(parent.target.sample_id):'';
  message('');
  flowStep=parent?1:0;
  history.replaceState(null,'',parent?'/cycles?parent='+encodeURIComponent(parent.id):'/cycles?fresh=1');
  syncFlow();
  try{
    await reloadSources('');
    if(parent&&!$('source-run').value)message('请先完成人工复核，再选择建议。');
  }catch(e){message(e.message);}
  window.scrollTo({top:0});
}
$('new-cycle').onclick=()=>startPlan();
$('exit-cycle').onclick=async()=>{
  if(!current||busy||!confirm('结束本轮？你仍可以从历史轮次查看已保存的内容。'))return;
  await mutate('close',base());
  if(current.status==='closed')await startPlan();
};
$('continue-cycle').onclick=()=>{
  if(current.detached_from_chain)return openCycle(current.detached_from_chain);
  return startPlan(current.status==='decided'?current:null);
};
$('open-logs').onclick=()=>openWork('/logs','运行记录');
const oldSubmit=$('comparison-form').onsubmit;
$('comparison-form').onsubmit=async e=>{e.preventDefault();await refreshBReview();if(!bReview){message('请先完成 B 的人工复核，再比较数据。');goStep(2);return;}oldSubmit(e);};
statusNames.closed='已结束';
if(query.get('fresh'))localStorage.removeItem('review-active-cycle');
if(query.get('run')||query.get('parent'))flowStep=1;
syncFlow();
