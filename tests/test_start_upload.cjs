const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../review-ui/dist/start.js'),'utf8');
const handler=source.slice(source.indexOf('let openingMetrics=false;'),source.indexOf("$('metrics-editor').ontoggle="));
function setup(ensureVideo){
  const nodes=new Map();
  const get=id=>{if(!nodes.has(id))nodes.set(id,{open:true,hidden:false,textContent:'',src:'',getAttribute(k){return this[k];}});return nodes.get(id);};
  const context=vm.createContext({$:get,ensureVideo,encodeURIComponent});
  vm.runInContext(handler,context);
  return {get,open:()=>vm.runInContext('openMetricsEditor()',context)};
}
test('failed registration keeps upload expanded with a local error and hides stale screenshot form',async()=>{
  const ui=setup(async()=>{throw new Error('missing video');});
  ui.get('metrics-frame').src='/metrics?embed=1&sample=old';
  await ui.open();
  assert.equal(ui.get('metrics-editor').open,true);
  assert.equal(ui.get('metrics-editor-status').textContent,'missing video');
  assert.equal(ui.get('metrics-editor-recovery').hidden,false);
  assert.equal(ui.get('metrics-frame').hidden,true);
});
test('retry opens the upload for the corrected work without collapsing the panel',async()=>{
  let ready=false;
  const ui=setup(async()=>{if(!ready)throw new Error('network');return 'tutorial-005';});
  await ui.open();ready=true;await ui.open();
  assert.equal(ui.get('metrics-frame').src,'/metrics?embed=1&sample=tutorial-005');
  assert.equal(ui.get('metrics-frame').hidden,false);
  assert.equal(ui.get('metrics-editor-recovery').hidden,true);
});
test('rapid repeated clicks do not register a video twice',async()=>{
  let resolve,calls=0;const pending=new Promise(r=>resolve=r);
  const ui=setup(()=>{calls++;return pending;});
  const first=ui.open();await ui.open();assert.equal(calls,1);
  resolve('tutorial-006');await first;assert.equal(ui.get('metrics-frame').hidden,false);
});
test('no selected work shows recovery instead of an unbound upload',async()=>{
  const ui=setup(async()=> '');await ui.open();
  assert.equal(ui.get('metrics-frame').hidden,true);
  assert.equal(ui.get('metrics-editor-recovery').hidden,false);
  assert.equal(ui.get('metrics-editor').open,true);
});
