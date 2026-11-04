const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../review-ui/dist/start.js'),'utf8');
const handler=source.slice(source.indexOf("$('plan').onclick="),source.indexOf("$('start').onclick="));
const saved={sample_id:'video-test',metrics_record_id:'metric-test'};
function setup(overrides={}){
  const nodes=new Map();
  const get=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',checked:true});return nodes.get(id);};
  get('samples').value=saved.sample_id;get('metrics-record').value=saved.metrics_record_id;get('budget').value='0';
  const context=vm.createContext({$:get,saveMaterials:async()=>saved,api:async()=>({id:'planned-job',ready:true}),
    message:()=>{},showPlan:()=>{},refreshJobs:async()=>{},...overrides});
  vm.runInContext('let planning=false,launching=false,planned=null,requestVersion=0;',context);
  vm.runInContext(handler,context);
  return {get,context,run:()=>get('plan').onclick()};
}
test('backend rejection is shown beside preview and button is released for retry',async()=>{
  const ui=setup({api:async()=>{throw new Error('screenshot validation failed');}});
  await ui.run();assert.match(ui.get('plan-status').textContent,/screenshot validation failed/);
  assert.equal(ui.get('plan').disabled,false);assert.equal(vm.runInContext('planned',ui.context),null);
});
test('material save failure is visible before sending a plan request',async()=>{
  const ui=setup({saveMaterials:async()=>{throw new Error('missing material');},api:async()=>assert.fail('must not plan')});
  await ui.run();assert.match(ui.get('plan-status').textContent,/missing material/);assert.equal(ui.get('plan').disabled,false);
});
test('button locks before saving and duplicate clicks do not create extra plans',async()=>{
  let resolve,saves=0,plans=0;const pending=new Promise(r=>resolve=r);
  const ui=setup({saveMaterials:()=>{saves++;return pending;},api:async()=>{plans++;return {id:'j',ready:true};}});
  const first=ui.run();assert.equal(ui.get('plan').disabled,true);await ui.run();
  resolve(saved);await first;assert.equal(saves,1);assert.equal(plans,1);
  assert.match(ui.get('plan-status').textContent,/启动本轮复盘/);
});
test('selection change while planning discards stale response and explains retry',async()=>{
  let resolve,entered;const pending=new Promise(r=>resolve=r),started=new Promise(r=>entered=r);
  const ui=setup({api:()=>{entered();return pending;},showPlan:()=>assert.fail('must not show stale plan')});
  const first=ui.run();await started;vm.runInContext('requestVersion++',ui.context);
  resolve({id:'old',ready:true});await first;
  assert.match(ui.get('plan-status').textContent,/选择已变化/);assert.equal(vm.runInContext('planned',ui.context),null);
});
test('retry checkbox explicitly reserves two extra calls in the frozen request',async()=>{
  for(const enabled of [true,false]){
    let request;
    const ui=setup({api:async(path,body)=>{request=body;return {id:'j',ready:true};}});
    ui.get('model-retry').checked=enabled;await ui.run();
    assert.equal(request.model_retry_limit,enabled?1:0);
    assert.equal(request.max_model_calls,enabled?4:2);
  }
});
