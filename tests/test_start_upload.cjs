const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../review-ui/dist/start.js'),'utf8');
const handler=source.slice(source.indexOf('function openMetricsEditor()'),source.indexOf("$('metrics-editor').ontoggle="));
function setup(){
  const nodes=new Map();
  const get=id=>{if(!nodes.has(id))nodes.set(id,{open:true,hidden:false,textContent:'',src:'',getAttribute(k){return this[k];}});return nodes.get(id);};
  const samples=[{sample_id:'tutorial-013',title:'已登记作品'},{sample_id:'tutorial-014',title:'另一条作品'}];
  const context=vm.createContext({$:get,samples,encodeURIComponent,api:()=>assert.fail('opening upload must not register a video')});
  vm.runInContext(handler,context);
  return {get,open:()=>vm.runInContext('openMetricsEditor()',context)};
}
test('an existing work opens upload even while new registration is expanded',()=>{
  const ui=setup();
  ui.get('register-details').open=true;
  ui.get('samples').value='tutorial-013';
  ui.open();
  assert.equal(ui.get('metrics-editor').open,true);
  assert.equal(ui.get('metrics-editor-status').textContent,'当前作品 · 已登记作品');
  assert.equal(ui.get('metrics-frame').src,'/metrics?embed=1&sample=tutorial-013');
  assert.equal(ui.get('metrics-frame').hidden,false);
});
test('switching or clearing the work updates the upload context',()=>{
  const ui=setup();
  ui.get('samples').value='tutorial-013';ui.open();
  ui.get('samples').value='tutorial-014';ui.open();
  assert.equal(ui.get('metrics-frame').src,'/metrics?embed=1&sample=tutorial-014');
  ui.get('samples').value='';ui.open();
  assert.equal(ui.get('metrics-frame').hidden,true);
  assert.match(ui.get('metrics-editor-status').textContent,/先选择已登记作品/);
});
