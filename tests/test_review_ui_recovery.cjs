// No browser or real reports: exercise failure recovery against fake transport/storage.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../review-ui/dist/app.js'),'utf8').split("$('evidence-select').onchange=renderEvidence;")[0];
function setup(fetch){
  const nodes=new Map(),storage=new Map();
  const context=vm.createContext({fetch,AbortController,TypeError,setTimeout,clearTimeout,structuredClone,
    document:{getElementById(id){if(!nodes.has(id))nodes.set(id,{textContent:'',firstChild:{textContent:''},children:[]});return nodes.get(id);}},
    sessionStorage:{setItem(k,v){storage.set(k,v);},getItem(k){return storage.get(k);},removeItem(k){storage.delete(k);}}});
  vm.runInContext(source,context);
  vm.runInContext("data={id:'run-fixture'};feedback={revision:1,candidate_sha256:'c',source_sha256:'s',status:'draft',summary:{decision:'accept',note:'user text'},findings:[]};",context);
  return {context,nodes,storage,run:code=>vm.runInContext(code,context)};
}
test('connection loss keeps draft and shows actionable error beside submit without retry',async()=>{
  let calls=0;const ui=setup(async()=>{calls++;throw new TypeError('Failed to fetch');});
  await ui.run('save(true)');
  assert.equal(calls,1);
  assert.match(ui.nodes.get('save-message').textContent,/无法连接本机服务/);
  assert.equal(JSON.parse(ui.storage.get('review-draft:run-fixture')).summary.note,'user text');
  assert.equal(ui.run('busy'),false);
  assert.equal(ui.nodes.get('submit').disabled,false);
});
test('manual save obtains current token before a single POST after restart',async()=>{
  const calls=[];const ui=setup(async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>url==='/api/session'?{token:'new-session'}:{feedback:JSON.parse(ui.run('JSON.stringify(feedback)'))}};});
  ui.run('loadRun=async()=>{}');
  await ui.run('save(false)');
  assert.deepEqual(calls.map(c=>c.url),['/api/session','/api/draft']);
  assert.equal(calls[1].options.headers['X-Review-Token'],'new-session');
  assert.equal(ui.storage.size,0);
});
test('restore only same revision and source; never silently overwrite newer server feedback',()=>{
  const ui=setup();ui.run("backupDraft();feedback.summary.note='server text'");
  assert.equal(ui.run('restoreBackup()'),true);
  assert.equal(ui.run('feedback.summary.note'),'user text');
  ui.run("feedback.revision=2;feedback.summary.note='newer text'");
  assert.equal(ui.run('restoreBackup()'),false);
  assert.equal(ui.run('feedback.summary.note'),'newer text');
});
test('server rejection remains visible and does not clear backup or retry POST',async()=>{
  let posts=0;const ui=setup(async url=>url==='/api/session'?{ok:true,json:async()=>({token:'t'})}:{ok:false,json:async()=>{posts++;return {error:'修改或暂不确定时，请填写理由'};}});
  await ui.run('save(true)');
  assert.equal(posts,1);assert.match(ui.nodes.get('save-message').textContent,/请填写理由/);assert.equal(ui.storage.size,1);
});
