const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const read=name=>fs.readFileSync(path.join(__dirname,'../review-ui/dist',name),'utf8');
test('embedded next-step links return to the workspace without navigating the page',()=>{
  const source=read('workspace.js'),start=source.indexOf("doc.addEventListener('click'");
  const code=source.slice(start,source.indexOf('// Registration',start));
  let handler,returned=0,prevented=false;const frame={};
  const context=vm.createContext({URL,doc:{addEventListener:(type,fn)=>handler=fn},
    location:{origin:'http://127.0.0.1:19433'},canLeaveWork:()=>true,workPurpose:'inspect',
    $:id=>id==='return-work'?{click:()=>returned++}:frame});
  vm.runInContext(code,context);
  const click=href=>{prevented=false;handler({target:{closest:()=>({href})},preventDefault:()=>prevented=true});};
  click('http://127.0.0.1:19433/cycles?run=run-test');
  assert.equal(returned,1);assert.equal(frame.src,undefined);assert.ok(prevented);
  click('http://127.0.0.1:19433/metrics?sample=test');assert.equal(frame.src,'http://127.0.0.1:19433/metrics?sample=test&embed=1');
  click('http://127.0.0.1:19433/logs?run=run-test');assert.equal(frame.src,'http://127.0.0.1:19433/logs?run=run-test');
  click('https://external.test/');assert.equal(prevented,false);
});
test('saving a first plan refreshes step availability after releasing busy controls',async()=>{
  const source=read('cycles.js'),start=source.indexOf('async function mutate(');
  const button={disabled:true,isConnected:true};let synced=false;
  const context=vm.createContext({document:{querySelectorAll:()=>[button]},api:async()=>({status:'planned'}),
    options:async()=>{},listCycles:async()=>{},render:()=>{button.disabled=false;},message:()=>{},
    syncFlow:()=>{synced=true;}});
  vm.runInContext('let busy=false,current=null;',context);
  vm.runInContext(source.slice(start,source.indexOf("$('source-run').onchange",start)),context);
  await vm.runInContext("mutate('create',{})",context);
  assert.equal(synced,true);assert.equal(button.disabled,false);
});
test('submitted review offers next-video plan without loading current-video tasks',async()=>{
  function element(tag){return {tag,children:[],append(...children){this.children.push(...children);},replaceChildren(){this.children=[];}};}
  const nodes={'task-board':element('div'),'task-message':element('p')};
  const context=vm.createContext({window:{},encodeURIComponent,document:{getElementById:id=>nodes[id],createElement:element},
    fetch:()=>assert.fail('must not load or create old modification tasks')});
  vm.runInContext(read('tasks.js'),context);
  await context.window.TaskBoard.load('run-test',true);
  const card=nodes['task-board'].children[0],link=card.children[2];
  assert.equal(link.href,'/cycles?run=run-test');assert.equal(link.target,'_top');
  assert.equal(card.children.some(c=>['form','select','textarea'].includes(c.tag)),false);
  assert.equal(context.window.TaskBoard.isDirty(),false);
});
