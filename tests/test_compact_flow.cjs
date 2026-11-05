const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const read=n=>fs.readFileSync(path.join(__dirname,'../review-ui/dist',n),'utf8');
test('one work has one source choice; explicit historical link is respected',()=>{
  const s=read('cycles.js'),c=vm.createContext({Set});
  vm.runInContext(s.slice(s.indexOf('function sourceChoices('),s.indexOf('function fillSourceChoices(')),c);
  c.rows=[{id:'new-a',sample_id:'a'},{id:'old-a',sample_id:'a'},{id:'new-b',sample_id:'b'}];
  assert.equal(vm.runInContext("sourceChoices(rows).map(r=>r.id).join(',')",c),'new-a,new-b');
  assert.equal(vm.runInContext("sourceChoices(rows,'old-a').map(r=>r.id).join(',')",c),'old-a,new-b');
});
function quickSetup(ready){
  const nodes=new Map();const $=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',disabled:false});return nodes.get(id);};
  let plans=0,starts=0,release;const pending=new Promise(r=>release=r);
  const c=vm.createContext({$});vm.runInContext('let planning=false,launching=false,planned=null;',c);
  $('plan').onclick=async()=>{plans++;await pending;c.ready=ready;vm.runInContext('planned=ready?{ready:true}:null',c);};
  $('start').onclick=async()=>starts++;
  const s=read('start.js');vm.runInContext(s.slice(s.indexOf('let quickBusy=false;')),c);
  return {$,release,click:()=>$('quick-start').onclick(),count:()=>({plans,starts})};
}
test('single main action prepares and launches once despite double click',async()=>{
  const u=quickSetup(true),first=u.click();await u.click();u.release();await first;
  assert.deepEqual(u.count(),{plans:1,starts:1});assert.equal(u.$('quick-start').disabled,false);
});
test('failed material/plan check never proceeds to a paid model run',async()=>{
  const u=quickSetup(false),first=u.click();u.release();await first;
  assert.deepEqual(u.count(),{plans:1,starts:0});assert.equal(u.$('quick-start').disabled,false);
});
test('existing review reuse checks the goal and the selected screenshot',async()=>{
  const source=read('start.js'),code=source.slice(source.indexOf('async function reuseExistingReview('),source.indexOf('function invalidate('));
  for(const [changed,metric,expectReuse] of [[false,'metric-a',true],[true,'metric-a',false],[false,'metric-new',false]]){
    const nodes={samples:{value:'work-a'},'metrics-record':{value:metric},'work-next-link':{hidden:true},'quick-start':{hidden:false},'quick-status':{}};
    const context=vm.createContext({$:id=>nodes[id],samples:[{sample_id:'work-a',goal:'like'}],encodeURIComponent,
      api:async p=>p==='/api/runs'?{runs:[{id:'run-a',sample_id:'work-a'}]}:{goal_changed:changed,task:{tutorial_goal:'like',platform_metrics:{source:{record_id:'metric-a'}}}}});
    vm.runInContext(code,context);await vm.runInContext("reuseExistingReview('work-a')",context);
    assert.equal(nodes['quick-start'].hidden,expectReuse);assert.equal(nodes['work-next-link'].hidden,!expectReuse);
  }
});
test('B can compare after submitted review even if no recommendation was adopted for the next cycle',async()=>{
  const s=read('workspace.js'),code=s.slice(s.indexOf('async function refreshBReview()'),s.indexOf("$('target-sample').onchange="));
  for(const status of ['accepted','draft']){
    const nodes={'target-sample':{value:'b'},'b-review-status':{},'open-b-review':{}};
    const context=vm.createContext({$:id=>nodes[id],syncFlow:()=>{},message:()=>{},encodeURIComponent,
      api:async p=>p==='/api/runs'?{runs:[{id:'latest-b',sample_id:'b'}]}:{goal_changed:false,feedback:{status,findings:[]}}});
    vm.runInContext("let current={id:'cycle'},bReview=null,bPending=null,flowStep=2;",context);
    vm.runInContext(code,context);await vm.runInContext('refreshBReview()',context);
    assert.equal(vm.runInContext('!!bReview',context),status==='accepted');
  }
});
