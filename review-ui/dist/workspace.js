'use strict';
// One workspace orchestrates the existing audited tools without submitting reviews for the user.
let flowStep=0,bReview=null,bPending=null;
const flowDescriptions=['先完成 A 的复盘与人工复核。','只挑值得试的 1～2 条，写成下一条的行动。','按清单创作 B，在这里登记、复盘并记录采用情况。','核对两边的数据，再决定下一条继续怎么做。'];
const originalRender=render;
render=function(){originalRender();bReview=null;bPending=null;localStorage.setItem('review-active-cycle',current.id);flowStep=current.target?3:2;syncFlow();refreshBReview();};
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
  const closed=['decided','closed'].includes(current.status);
  $('b-review-panel').hidden=flowStep!==2||closed;
  $('implementation-form').hidden=flowStep!==2||closed;
  $('compare-gate').hidden=flowStep!==3||!!bReview||closed;
  $('comparison-section').hidden=flowStep!==3||!current.target||(!bReview&&!closed)||closed;
  $('comparison-result').hidden=flowStep!==3||!current.comparison;
  $('decision-form').hidden=flowStep!==3||!current.comparison||closed||!bReview;
  $('next-round').hidden=flowStep!==3||!closed;
  if(current.status==='closed'){$('decisions-summary').replaceChildren(node('p','本轮已结束，已有记录保留。可以从任意新视频重新开始。'));$('continue-cycle').href='/cycles?fresh=1';$('continue-cycle').textContent='开始新的循环 →';$('review-b').hidden=true;}
  else $('review-b').hidden=false;
}
async function refreshBReview(){
  if(!current?.target){bReview=null;syncFlow();return;}
  const target=current.target.sample_id,id=current.id;
  try{const [sources,all]=await Promise.all([api('/api/cycles/sources'),api('/api/runs')]);if(current?.id!==id||current.target?.sample_id!==target)return;bReview=sources.runs.find(r=>r.sample_id===target)||null;bPending=all.runs.find(r=>r.sample_id===target)||null;$('b-review-status').textContent=bReview?'B 已提交人工复核，可以进入数据比较。':bPending?'B 的分析已完成，直接打开人工复核即可。':'B 还没有复盘记录，在这里启动分析后完成核对。';syncFlow();}catch(e){message(e.message);}
}
function goStep(step){if(!$('inline-work').hidden){message('请先点击“完成，返回本轮”，以免丢失正在填写的内容。');return;}flowStep=step;if(step===0&&current){openWork('/?run='+encodeURIComponent(current.source.run),'查看 A 的人工复核');return;}syncFlow();}
for(const button of document.querySelectorAll('[data-step]'))button.onclick=()=>goStep(Number(button.dataset.step));
function openWork(url,title){const target=new URL(url,location.origin);if(['/start','/metrics'].includes(target.pathname))target.searchParams.set('embed','1');$('inline-title').textContent=title;$('inline-work').hidden=false;$('work-frame').src=target.href;syncFlow();$('inline-work').scrollIntoView({behavior:'smooth',block:'start'});}
$('work-frame').onload=()=>{
  const doc=$('work-frame').contentDocument;if(!doc)return;
  const style=doc.createElement('link');style.rel='stylesheet';style.href='/embedded.css';doc.head.append(style);
  const form=doc.querySelector('#register-form');if(form){const details=doc.createElement('details'),summary=doc.createElement('summary');summary.textContent='更多设置：字幕、受众与编号';details.append(summary);for(const name of ['sample_id','transcript','audience','prior_knowledge']){const label=form.querySelector(`[name="${name}"]`)?.closest('label');if(label)details.append(label);}form.insertBefore(details,form.querySelector('button'));}
  doc.addEventListener('click',e=>{const a=e.target.closest('a');if(!a)return;const u=new URL(a.href,location.origin);if(u.origin!==location.origin)return;if(['/','/start','/metrics'].includes(u.pathname)){e.preventDefault();$('work-frame').src=u.href;}});
  // Registration is already explicit; return the registered choice without another dropdown selection.
};
$('open-a').onclick=()=>openWork('/start'+(parent?.target?'?sample='+encodeURIComponent(parent.target.sample_id):''),'复盘视频 A');
$('choose-a').onclick=()=>{goStep(1);reloadSources();};
$('open-b-review').onclick=()=>{const reviewed=bReview||bPending,sample=$('target-sample').value||current?.target?.sample_id;openWork(reviewed?'/?run='+encodeURIComponent(reviewed.id):'/start'+(sample?'?sample='+encodeURIComponent(sample):''),'复盘视频 B');};
$('back-b-review').onclick=()=>{goStep(2);$('open-b-review').click();};
async function reloadSources(){const data=await api('/api/cycles/sources'),selected=$('source-run').value;$('source-run').replaceChildren(new Option('选择 A 的已提交复核',''));for(const r of data.runs.filter(r=>!parent||r.sample_id===parent.target.sample_id))$('source-run').add(new Option(`${r.sample_id} · ${r.id}`,r.id));if(selected)$('source-run').value=selected;}
$('return-work').onclick=async()=>{
  const frame=$('work-frame'),doc=frame.contentDocument;
  // Ask the embedded application whether it has unsaved input before leaving its form.
  if(doc){const event=new frame.contentWindow.Event('beforeunload',{cancelable:true});if(!frame.contentWindow.dispatchEvent(event)){message('内页还有未保存内容，请先保存或提交，再返回本轮。');return;}}
  const chosen=doc?.querySelector('#samples')?.value, reviewed=doc?.querySelector('#runs')?.value;
  $('inline-work').hidden=true;if(current&&flowStep===0)flowStep=1;
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
