'use strict';
// One workspace orchestrates the existing audited tools without submitting reviews for the user.
let flowStep=0,bReview=null,bPending=null;
const flowDescriptions=['先完成 A 的复盘与人工复核。','只挑值得试的 1～2 条，写成下一条的行动。','按清单创作 B，在这里登记、复盘并记录采用情况。','核对两边的数据，再决定下一条继续怎么做。'];
const originalRender=render;
render=function(){originalRender();bReview=null;bPending=null;localStorage.setItem('review-active-cycle',current.id);flowStep=current.target?3:2;syncFlow();refreshBReview();restoreWorkMaterials();if(window.ChainOverview)window.ChainOverview.load(current.id);$('single-round').open=query.get('round')==='1'||!current.comparison||current.comparison.result.status!=='ready';};
async function restoreWorkMaterials(){if(!current?.target||current.comparison)return;const id=current.id;try{const bindings=await Promise.all([current.source.sample_id,current.target.sample_id].map(s=>api('/api/samples/materials?sample='+encodeURIComponent(s))));if(current?.id!==id||current.comparison)return;for(const [i,side] of ['a','b'].entries()){const input=$('record-'+side);if(!input.value&&bindings[i].metrics_record_id){input.value=bindings[i].metrics_record_id;showRecord(side);}}}catch(e){message('作品材料未能自动带入：'+e.message);}}
function syncFlow(){
  const editing=!$('inline-work').hidden;
  $('flow-description').textContent=flowDescriptions[flowStep];
  for(const button of document.querySelectorAll('[data-step]')){const n=Number(button.dataset.step);button.classList.toggle('active',n===flowStep);button.setAttribute('aria-current',n===flowStep?'step':'false');button.disabled=(n>1&&!current);}
  $('prepare-a').hidden=flowStep!==0||editing;
  $('create-cycle').hidden=!!current||flowStep!==1||editing;
  $('cycle-detail').hidden=!current||flowStep<1||editing;
  $('exit-cycle').hidden=!current;
  $('exit-cycle').textContent=current&&['decided','closed'].includes(current.status)?'开始新的循环':'结束本轮，重新开始';
  if(!current)return;
  const closed=['decided','closed'].includes(current.status);$('chain-overview').hidden=flowStep!==3;$('single-round').hidden=flowStep!==3;
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
  if(current.status==='closed'){$('decisions-summary').replaceChildren(node('p','本轮已结束，已有记录保留。可以从任意新视频重新开始。'));$('continue-cycle').href='/cycles?fresh=1';$('continue-cycle').textContent='开始新的循环 →';$('review-b').hidden=true;}
  else $('review-b').hidden=false;
}
async function refreshBReview(){
  const target=$('target-sample').value;
  bReview=null;bPending=null;syncFlow();
  if(!current||!target){$('b-review-status').textContent='选择已有作品 B，或登记下一条新作品。';$('open-b-review').textContent='登记作品 B';return;}
  const id=current.id;
  try{const all=await api('/api/runs');const latest=all.runs.find(r=>r.sample_id===target)||null;const detail=latest?await api('/api/review?run='+encodeURIComponent(latest.id)):null;if(current?.id!==id||$('target-sample').value!==target)return;bPending=latest;bReview=detail&&!detail.goal_changed&&['accepted','needs_evidence'].includes(detail.feedback.status)?latest:null;$('b-review-status').textContent=bReview?'B 已完成复核，请确认下面清单的采用情况。':bPending?'已有复盘结果，继续人工复核即可。':'材料会随作品带入；开始复盘后按提示核对结果。';$('open-b-review').textContent=bPending?'继续复核作品 B':'复盘作品 B';
    if(current.target&&bReview&&flowStep===2){flowStep=3;}syncFlow();
  }catch(e){message(e.message);}
}
$('target-sample').onchange=()=>{
  if($('target-sample').value!==current?.target?.sample_id)for(const item of current.items){$('applied-'+item.finding_id).value='';$('note-'+item.finding_id).value=item.planned_change;}
  refreshBReview();
};
function goStep(step){if(!$('inline-work').hidden){message('请先点击“完成，返回本轮”，以免丢失正在填写的内容。');return;}flowStep=step;if(step===0&&current){openWork('/?run='+encodeURIComponent(current.source.run),'查看 A 的人工复核');return;}syncFlow();}
for(const button of document.querySelectorAll('[data-step]'))button.onclick=()=>goStep(Number(button.dataset.step));
function openWork(url,title){const target=new URL(url,location.origin);if(['/start','/metrics'].includes(target.pathname))target.searchParams.set('embed','1');$('inline-title').textContent=title;$('inline-work').hidden=false;$('work-frame').src=target.href;syncFlow();$('inline-work').scrollIntoView({behavior:'smooth',block:'start'});}
$('work-frame').onload=()=>{
  const doc=$('work-frame').contentDocument;if(!doc)return;
  const style=doc.createElement('link');style.rel='stylesheet';style.href='/embedded.css';doc.head.append(style);
  const form=doc.querySelector('#register-form');if(form){const details=doc.createElement('details'),summary=doc.createElement('summary');summary.textContent='更多设置：字幕、受众与编号';details.append(summary);for(const name of ['sample_id','transcript','audience','prior_knowledge']){const label=form.querySelector(`[name="${name}"]`)?.closest('label');if(label)details.append(label);}form.insertBefore(details,form.querySelector('button'));}
  doc.addEventListener('click',e=>{const a=e.target.closest('a');if(!a)return;const u=new URL(a.href,location.origin);if(u.origin!==location.origin)return;
    if(u.pathname==='/cycles'){e.preventDefault();location.assign(u.href);return;}
    if(['/','/start','/metrics','/logs'].includes(u.pathname)){e.preventDefault();$('work-frame').src=u.href;}});
  // Registration is already explicit; return the registered choice without another dropdown selection.
};
$('open-a').onclick=()=>openWork('/start'+(parent?.target?'?sample='+encodeURIComponent(parent.target.sample_id):''),'复盘视频 A');
$('choose-a').onclick=()=>{goStep(1);reloadSources();};
$('open-b-review').onclick=()=>{const reviewed=bReview||bPending,sample=$('target-sample').value||current?.target?.sample_id;openWork(reviewed?'/?run='+encodeURIComponent(reviewed.id):'/start'+(sample?'?sample='+encodeURIComponent(sample):''),'复盘视频 B');};
$('back-b-review').onclick=()=>{goStep(2);$('open-b-review').click();};
async function reloadSources(){const data=await api('/api/cycles/sources'),selected=$('source-run').value;fillSourceChoices(data.runs.filter(r=>!parent||r.sample_id===parent.target.sample_id),selected);if(!current&&$('source-run').value)await loadSource();}
$('return-work').onclick=async()=>{
  const frame=$('work-frame'),doc=frame.contentDocument;
  // Ask the embedded application whether it has unsaved input before leaving its form.
  if(doc){const event=new frame.contentWindow.Event('beforeunload',{cancelable:true});if(!frame.contentWindow.dispatchEvent(event)){message('内页还有未保存内容，请先保存或提交，再返回本轮。');return;}}
  const chosen=doc?.querySelector('#samples')?.value, reviewed=doc?.querySelector('#runs')?.value;
  if(!current&&reviewed){try{await api('/api/cycles/source?run='+encodeURIComponent(reviewed));}catch(e){message('请先在内页提交人工复核，再挑选下一条建议。'+e.message);return;}}
  message('');$('inline-work').hidden=true;if(current&&flowStep===0)flowStep=1;
  try{await reloadSources();if(current){await refreshAvailableRecords();if(chosen&&chosen!==current.source.sample_id)$('target-sample').value=chosen;await refreshBReview();}else{flowStep=1;const runs=(await api('/api/cycles/sources')).runs.filter(r=>!chosen||r.sample_id===chosen);if(runs.length){$('source-run').value=runs[0].id;await loadSource();}}
    if(!current&&reviewed&&Array.from($('source-run').options).some(o=>o.value===reviewed)){$('source-run').value=reviewed;await loadSource();}
  }catch(e){message(e.message);}syncFlow();
};
for(const [id,title] of [['register-b','登记视频 B'],['upload-a','核对 A 的截图'],['upload-b','核对 B 的截图'],['review-b','复盘 B']])$(id).onclick=e=>{e.preventDefault();openWork($(id).href,title);};
// Expand only selected suggestions; retain user edits if unchecked and selected again.
const suggestionObserver=new MutationObserver(()=>{for(const card of $('suggestions').children){const checkbox=card.querySelector('input[type=checkbox]'),field=card.querySelector('.field');if(!checkbox||!field)continue;field.hidden=!checkbox.checked;checkbox.onchange=()=>{if(document.querySelectorAll('#suggestions input:checked').length>2){checkbox.checked=false;message('每轮最多选 2 条，先聚焦最值得试的改动。');return;}field.hidden=!checkbox.checked;if(checkbox.checked&&!field.querySelector('textarea').value)field.querySelector('textarea').value=checkbox.parentElement.textContent.trim();};}});
suggestionObserver.observe($('suggestions'),{childList:true});
$('new-cycle').onclick=()=>{if(existingChoice())location.href='/cycles?fresh=1';};
$('exit-cycle').onclick=async()=>{if(busy)return;if(['decided','closed'].includes(current.status)){localStorage.removeItem('review-active-cycle');location.href='/cycles?fresh=1';return;}try{await mutate('close',base());if(current.status==='closed'){localStorage.removeItem('review-active-cycle');dirty=false;location.href='/cycles?fresh=1';}}catch(e){message(e.message);}};
const oldSubmit=$('comparison-form').onsubmit;
$('comparison-form').onsubmit=async e=>{e.preventDefault();await refreshBReview();if(!bReview){message('请先完成 B 的人工复核，再比较数据。');goStep(2);return;}oldSubmit(e);};
statusNames.closed='已结束';
if(query.get('fresh'))localStorage.removeItem('review-active-cycle');
else if(!query.get('id')&&!query.get('run')&&!query.get('parent')&&localStorage.getItem('review-active-cycle'))location.replace('/cycles?id='+encodeURIComponent(localStorage.getItem('review-active-cycle')));
if(query.get('run')||query.get('parent'))flowStep=1;
syncFlow();
