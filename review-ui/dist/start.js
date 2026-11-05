'use strict';
const $=id=>document.getElementById(id);
const statuses={planned:'计划已准备',queued:'正在启动',running:'运行中',awaiting_human_review:'等待人工复核',failed:'失败',interrupted:'进程异常退出',completed:'完成',skipped:'跳过',pending:'待执行'};
let token='',samples=[],planned=null,selectedJob='',pollTimer=null,requestVersion=0,planning=false,launching=false;
const cycleId=new URLSearchParams(location.search).get('cycle');
let cycleContext=null;
function el(tag,text,cls){const node=document.createElement(tag);if(text!==undefined)node.textContent=String(text);if(cls)node.className=cls;return node;}
async function api(path,payload){if(payload!==undefined)token=(await api('/api/session')).token;const options=payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':token},body:JSON.stringify(payload)};const response=await fetch(path,options);const value=await response.json();if(!response.ok)throw new Error(value.error||'请求失败');return value;}
function message(text){$('message').textContent=text;$('quick-status').textContent=text;}
async function reuseExistingReview(sample){
  const list=await api('/api/runs');
  for(const run of list.runs.filter(r=>r.sample_id===sample).slice(0,3)){
    const detail=await api('/api/review?run='+encodeURIComponent(run.id));
    if(sample!==$('samples').value)return;
    if(detail.goal_changed||detail.task.tutorial_goal!==samples.find(s=>s.sample_id===sample)?.goal)continue;
    const metric=$('metrics-record').value;
    if(metric&&detail.task.platform_metrics?.source?.record_id!==metric)continue;
    $('work-next-link').href='/?run='+encodeURIComponent(run.id);$('work-next-link').hidden=false;$('quick-start').hidden=true;
    $('quick-status').textContent='已找到这条作品的有效复盘，直接继续人工复核或挑选建议。';return;
  }
}
function invalidate(){$('quick-start').hidden=false;$('work-next-link').hidden=true;planned=null;++requestVersion;$('start-actions').hidden=true;$('plan-view').replaceChildren(el('p','选择已变化，请重新预览计划。','empty'));$('sample-goal').textContent=samples.find(s=>s.sample_id===$('samples').value)?.goal||'';}
async function loadSamples(preferred){
  const data=await api('/api/samples');
  samples=cycleContext?data.samples.filter(s=>s.goal===cycleContext.goal&&s.sample_id!==cycleContext.source_sample):data.samples;
  const chosen=preferred||$('samples').value;
  const select=$('samples');
  select.replaceChildren();
  if(cycleContext||!samples.length)select.add(new Option(samples.length?'选择同目标的视频':'暂无可选视频，请登记新作品',''));
  for(const sample of samples)select.add(new Option(sample.title||sample.sample_id,sample.sample_id));
  if(samples.some(s=>s.sample_id===chosen))select.value=chosen;
  $('plan').disabled=false;
  invalidate();
  openMetricsEditor();
  await refreshMetrics();
  if(data.unreadable)message(`${data.unreadable} 份样本配置无法读取，请检查本机文件。`);
}
function showPlan(data){const view=$('plan-view');view.replaceChildren(el('h3',samples.find(s=>s.sample_id===data.sample_id)?.title||data.sample_id),el('p',data.plan.goal),el('p',`本轮最多 ${data.model_stage_budget} 次模型调用，最多 ${data.max_repair_rounds} 轮返修。`));const list=el('ol',undefined,'plan-list');for(const task of data.plan.tasks){const item=el('li',`${task.name} · ${task.action==='skip'?'跳过':'执行'}`);item.append(el('small',task.reason));list.append(item);}view.append(list);const metric=data.plan.platform_data;if(metric?.status==='completed'){const source=metric.source;view.append(el('p',source.schema_version===2?`已接入截图：${source.source.title} · ${source.observed_at}`:'使用样本中登记的平台数据','data-summary'));for(const c of metric.calculations)view.append(el('p',`每百次播放${c.label}数：${c.per_100_plays??'无法计算'}`,'helper'));if(source.schema_version===2){const link=el('a','查看本轮数据原图与核对记录 ↗');link.href='/metrics?id='+encodeURIComponent(source.source.record_id);link.target='_blank';link.rel='noopener';view.append(link);for(const limitation of metric.limitations)view.append(el('p',limitation,'helper'));}}for(const reason of data.blockers)view.append(el('p',reason,'blocker'));for(const warning of data.warnings)view.append(el('p',warning,'helper'));$('start-actions').hidden=false;$('start').disabled=!data.ready||launching;}
async function refreshJobs(preferred){const data=await api('/api/jobs');if(new URLSearchParams(location.search).get('embed')==='1')data.jobs=data.jobs.filter(j=>j.sample_id===$('samples').value);const wanted=preferred||selectedJob||data.active_job;const chosen=data.jobs.some(j=>j.id===wanted)?wanted:data.jobs[0]?.id||'';$('jobs').replaceChildren();for(const job of data.jobs){const option=el('option',`${statuses[job.status]||job.status} · ${new Date(job.created_at).toLocaleString('zh-CN')}`);option.value=job.id;$('jobs').append(option);}if(!data.jobs.length){const option=el('option','暂无记录');option.value='';$('jobs').append(option);}if(chosen){$('jobs').value=chosen;await chooseJob(chosen);}else{selectedJob='';clearTimeout(pollTimer);$('job-view').replaceChildren(el('p','这条作品暂无网页启动记录。','empty'));await reuseExistingReview($('samples').value);}}
async function chooseJob(id){clearTimeout(pollTimer);selectedJob=id;if(!id)return;try{const data=await api('/api/jobs/detail?job='+encodeURIComponent(id));if(id!==selectedJob)return;const view=$('job-view');view.replaceChildren(el('p',`${samples.find(s=>s.sample_id===data.sample_id)?.title||data.sample_id} · ${statuses[data.status]||data.status}`,'job-status'),el('details',undefined,'run-details'));const trace=view.querySelector('.run-details');trace.append(el('summary','运行详情'),el('p',data.id,'helper'));const option=Array.from($('jobs').options).find(item=>item.value===id);if(option)option.textContent=data.sample_id+' · '+(statuses[data.status]||data.status)+' · '+new Date(data.created_at).toLocaleString('zh-CN');const live=data.workflow;view.append(el('p',`已尝试模型阶段 ${live?.model_stage_attempts??0} / ${data.model_stage_budget}`));if(live){const stages=el('div',undefined,'stage-list');for(const stage of live.stages){const item=el('div',stage.name,'stage-item '+stage.status);item.append(el('small',statuses[stage.status]||stage.status));stages.append(item);}view.append(stages);if(live.retry_count)view.append(el('p',`已执行故障重试 ${live.retry_count} 次；每次都计入上方预算。`,'helper'));if(live.error_detail||live.stop_reason)view.append(el('p',live.error_detail||live.stop_reason,data.status==='failed'?'blocker':''));}if(data.error&&!live?.error_detail&&data.error!==live?.stop_reason)view.append(el('p',data.error,'blocker'));const links=el('div',undefined,'result-links');if(live){const log=el('a','查看本轮日志 ↗');log.href='/logs?run='+encodeURIComponent(data.run_id);links.append(log);}if(data.status==='awaiting_human_review'){const review=el('a','进入本轮人工复核 →');review.href='/?run='+encodeURIComponent(data.run_id);links.append(review);}view.append(links);$('quick-start').hidden=['queued','running','awaiting_human_review'].includes(data.status);$('work-next-link').hidden=true;if(data.status==='awaiting_human_review'){$('quick-start').hidden=false;await reuseExistingReview($('samples').value);}if(['queued','running'].includes(data.status))pollTimer=setTimeout(()=>chooseJob(id),2000);}catch(error){message(error.message);}}
$('samples').onchange=()=>{
  $('register-details').open=false;
  invalidate();
  openMetricsEditor();
  refreshJobs().catch(e=>message(e.message));
  refreshMetrics().catch(e=>message(e.message));
};
$('budget').onchange=invalidate;$('model-retry').onchange=invalidate;
let materialsBinding=null,registerPending=null;
async function registerVideo(){
  if(cycleId&&!cycleContext)throw new Error('创作链目标未读取成功，请刷新后再继续。');
  if(registerPending)return registerPending;
  const form=$('register-form');if(!form.checkValidity())throw new Error(cycleContext?'请补全视频路径和受众。':'请补全视频路径、目标和受众。');
  registerPending=(async()=>{
    const payload=Object.fromEntries(new FormData(form));
    if(!payload.sample_id)delete payload.sample_id;
    if(!payload.transcript)delete payload.transcript;
    if(cycleId)payload.cycle_id=cycleId;
    const result=await api('/api/samples/register',payload);
    form.reset();
    $('register-details').open=false;
    await loadSamples(result.sample_id);
    await refreshJobs();
    message('视频已登记，可以上传截图。');
    return result.sample_id;
  })();
  try{return await registerPending;}finally{registerPending=null;}
}
async function saveMaterials(sample=$('samples').value,metric=$('metrics-record').value,match=$('metrics-match').checked){
  if(!arguments.length&&$('register-details').open&&$('register-form').elements.namedItem('video').value.trim()){
    throw new Error('新视频尚未登记，请点击「登记视频」。');
  }
  if(!sample)throw new Error('请先选择或登记作品。');
  if(metric&&!match)throw new Error('请先核对截图属于这条作品。');
  const previous=await api('/api/samples/materials?sample='+encodeURIComponent(sample));
  const saved=await api('/api/samples/materials',{sample_id:sample,revision:previous.revision,metrics_record_id:metric,match_confirmed:match});
  if(sample!==$('samples').value)return saved;
  materialsBinding=saved;$('materials-saved').textContent='作品材料已保存'+(metric?' · 视频与数据截图已绑定，后续自动带入。':' · 暂无数据截图，可在发布后补充。');return saved;
}
$('register-form').onsubmit=async e=>{
  e.preventDefault();
  $('register').disabled=true;
  try{await registerVideo();}catch(error){message(error.message);}
  finally{$('register').disabled=false;}
};
$('save-materials').onclick=async()=>{$('save-materials').disabled=true;try{await saveMaterials();message('作品材料已保存，可以预览复盘计划。');}catch(e){message(e.message);}finally{$('save-materials').disabled=false;}};
$('plan').onclick=async()=>{
  if(planning||launching)return;
  planning=true;$('plan').disabled=true;$('plan').textContent='正在检查材料…';
  $('plan-status').textContent='正在保存并核对作品材料…';message('');planned=null;$('start-actions').hidden=true;
  try{
    const saved=await saveMaterials();
    if(saved.sample_id!==$('samples').value||saved.metrics_record_id!==$('metrics-record').value)throw new Error('作品选择已变化，请重新点击预览。');
    const version=++requestVersion,rounds=Number($('budget').value),retry=$('model-retry').checked?1:0;
    $('plan-status').textContent='材料已保存，正在生成复盘计划…';
    const payload={sample_id:saved.sample_id,max_model_calls:2+2*rounds+2*retry,max_repair_rounds:rounds,model_retry_limit:retry,metrics_record_id:saved.metrics_record_id,metrics_match_confirmed:$('metrics-match').checked};
    if(cycleId)payload.cycle_id=cycleId;
    const data=await api('/api/jobs/plan',payload);
    if(version!==requestVersion){$('plan-status').textContent='选择已变化，请重新点击预览。';return;}
    planned=data;showPlan(data);$('plan-status').textContent=data.ready?'计划已准备好，可点击“启动本轮复盘”。':'材料检查已完成，请按右侧提示补全后重新预览。';
    try{await refreshJobs(data.id);}catch(error){$('plan-status').textContent+=' 运行记录刷新失败：'+error.message;}
  }catch(error){$('plan-status').textContent='预览未完成：'+error.message;message(error.message);}
  finally{planning=false;$('plan').disabled=false;$('plan').textContent='检查材料并预览计划';}
};
$('start').onclick=async()=>{if(!planned||launching)return;launching=true;$('start').disabled=true;$('plan').disabled=true;message('');const job=planned;try{const data=await api('/api/jobs/start',{job_id:job.id});$('start-actions').hidden=true;planned=null;await refreshJobs(data.id);message('启动请求已处理。刷新或重复提交同一请求不会增加运行次数。');}catch(error){message(error.message+' 可以刷新记录检查状态；不会自动重试。');}finally{launching=false;$('plan').disabled=!samples.length;if(planned)$('start').disabled=!planned.ready;}};
$('jobs').onchange=()=>chooseJob($('jobs').value);
$('refresh').onclick=async()=>{try{await refreshJobs();}catch(error){message(error.message);}};
let metricRecords=[],metricRefresh=0;
async function refreshMetrics(preferred){const version=++metricRefresh,sample=$('samples').value;if(!sample){$('metrics-record').replaceChildren(new Option('暂无数据截图（可后补）',''));materialsBinding=null;metricsChanged();return;}
  const [result,binding]=await Promise.all([api('/api/metrics'),api('/api/samples/materials?sample='+encodeURIComponent(sample))]);if(version!==metricRefresh||sample!==$('samples').value)return;
  metricRecords=result.records;materialsBinding=binding;const previous=preferred||binding.metrics_record_id||'';
  $('metrics-record').replaceChildren(new Option('暂无数据截图（可后补）',''));
  for(const r of metricRecords){const c=r.confirmation;if(r.status==='confirmed'&&c&&(!c.sample_id||c.sample_id===sample))$('metrics-record').add(new Option(`${c.title} · ${c.observed_at}`,r.id));}
  if(Array.from($('metrics-record').options).some(o=>o.value===previous))$('metrics-record').value=previous;metricsChanged();
  $('materials-saved').textContent=binding.revision?'已恢复这条作品保存的材料。':'';
}
function metricsChanged(){invalidate();const r=metricRecords.find(r=>r.id===$('metrics-record').value);$('metrics-match').checked=!!r&&(r.confirmation.sample_id===$('samples').value||materialsBinding?.metrics_record_id===r.id);$('metrics-match').closest('label').hidden=!r||$('metrics-match').checked;if(!r){$('metrics-summary').textContent='暂无截图，可稍后补充。';return;}const c=r.confirmation;const labels={plays:'播放',likes:'点赞',comments:'评论',shares:'分享',favorites:'收藏',danmaku:'弹幕',completion_rate:'完播率',bounce_2s_rate:'2秒跳出率'};$('metrics-summary').textContent=Object.entries(c.normalized).filter(([,v])=>v!==null).map(([k,v])=>`${labels[k]} ${v}${k.endsWith('_rate')?'%':''}`).join(' · ')+`。统计范围：${{unknown:'尚未确认，只展示指标',cumulative:'截至截图累计',period:'指定时段'}[c.scope]}。`;
}
$('metrics-record').onchange=metricsChanged;$('metrics-match').onchange=invalidate;
$('refresh-metrics').onclick=()=>refreshMetrics().catch(e=>message(e.message));
function openMetricsEditor(){
  if(!$('metrics-editor').open)return;
  const sample=samples.find(s=>s.sample_id===$('samples').value);
  const frame=$('metrics-frame');
  frame.hidden=!sample;
  $('metrics-editor-status').textContent=sample
    ?'当前作品 · '+sample.title
    :'先选择已登记作品，或点击「登记视频」。';
  if(!sample)return;
  const url='/metrics?embed=1&sample='+encodeURIComponent(sample.sample_id);
  if(frame.getAttribute('src')!==url)frame.src=url;
}
$('metrics-editor').ontoggle=openMetricsEditor;
window.addEventListener('message',async event=>{
  if(event.origin!==location.origin||event.source!==$('metrics-frame').contentWindow)return;
  if(event.data?.type==='metrics-height'&&Number.isFinite(event.data.height)){
    $('metrics-frame').style.height=Math.max(150,Math.min(5000,event.data.height))+'px';
  }
  if(event.data?.type!=='metrics-confirmed')return;
  const {id,sample_id:sample}=event.data;
  if(!sample||sample!==$('samples').value)return;
  try{
    await saveMaterials(sample,id,true);
    if(sample!==$('samples').value)return;
    await refreshMetrics(id);
    if(sample!==$('samples').value)return;
    $('metrics-editor-status').textContent='数据已确认并绑定到当前作品。';
  }catch(error){
    if(sample===$('samples').value)$('metrics-editor-status').textContent='截图已保存，绑定失败：'+error.message;
  }
});
async function init(){
  try{
    const params=new URLSearchParams(location.search);
    token=(await api('/api/session')).token;
    if(cycleId){
      cycleContext=await api('/api/cycles/goal?id='+encodeURIComponent(cycleId));
      const goal=$('register-form').elements.namedItem('goal');
      goal.disabled=true;
      goal.closest('label').hidden=true;
      $('start-chain-goal').textContent='沿用创作链目标 · '+cycleContext.goal;
      $('start-chain-goal').hidden=false;
    }
    await loadSamples(params.get('sample'));
    if(cycleContext&&(params.has('register')||!samples.length))$('register-details').open=true;
    await refreshJobs(params.get('job'));
  }catch(error){message(error.message);}
}
init();

let quickBusy=false;
$('quick-start').onclick=async()=>{
  if(quickBusy||planning||launching)return;quickBusy=true;$('quick-start').disabled=true;$('quick-start').textContent='正在准备复盘…';
  try{await $('plan').onclick();$('quick-status').textContent=$('plan-status').textContent;if(planned?.ready)await $('start').onclick();}
  finally{quickBusy=false;$('quick-start').disabled=false;$('quick-start').textContent='保存材料并开始复盘';}
};
