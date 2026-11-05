'use strict';
window.ChainOverview=(()=>{
  let data=null,selected='',version=0;
  const get=id=>document.getElementById(id);
  const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
  const labels={comparable:'可比较',unaligned:'暂不可比较',conflict:'记录需核对',missing:'待采集',planned:'待创作'};
  const format=(v,key)=>v===null||v===undefined?'—':String(v)+(key.endsWith('_rate')?'%':'');
  function action(text,handler){
    const button=make('button',text,'secondary');
    button.type='button';
    button.onclick=handler;
    return button;
  }
  function svg(tag,attributes,text){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attributes))n.setAttribute(k,String(v));if(text!==undefined)n.textContent=text;return n;}
  function chart(){
    const key=get('chain-metric').value,box=get('chain-chart');box.replaceChildren();
    const points=data.nodes.map((n,i)=>({n,i,v:n.status==='comparable'?n.values[key]:null}));
    const usable=points.filter(p=>p.v!==null&&p.v!==undefined);
    if(!usable.length){box.append(make('p','还没有满足当前观察窗口的已确认数据。','helper'));return;}
    const width=Math.max(680,points.length*155),height=240,pad=50,max=Math.max(1,...usable.map(p=>p.v));
    const x=i=>pad+(width-pad*2)*i/Math.max(1,points.length-1),y=v=>height-pad-(height-pad*2)*v/max;
    const plot=svg('svg',{viewBox:`0 0 ${width} ${height}`,width,height,role:'img','aria-label':data.metrics[key]+'，按视频名称排列的趋势'});
    for(let i=0;i<=4;i++){const value=max*i/4;plot.append(svg('line',{x1:pad,y1:y(value),x2:width-pad,y2:y(value),stroke:'#e2e8e3'}),svg('text',{x:pad-8,y:y(value)+4,'text-anchor':'end',fill:'#62736e','font-size':11},String(Math.round(value*100)/100)));}
    for(const p of points){
      plot.append(svg('text',{x:x(p.i),y:height-15,'text-anchor':'middle',fill:'#334e43','font-size':12},p.n.title.length>11?p.n.title.slice(0,10)+'…':p.n.title));
      if(p.v===null||p.v===undefined)continue;
      const prev=points[p.i-1];
      if(prev&&prev.v!==null&&prev.v!==undefined&&p.n.previous_compatible)plot.append(svg('line',{x1:x(prev.i),y1:y(prev.v),x2:x(p.i),y2:y(p.v),stroke:'#2c7156','stroke-width':2}));
      const dot=svg('circle',{cx:x(p.i),cy:y(p.v),r:5,fill:'#235c49'});dot.append(svg('title',{},p.n.title+'：'+format(p.v,key)+(p.n.changes.length?'；采用：'+p.n.changes.join('；'):'')));
      const edge=p.i===0?'start':p.i===points.length-1?'end':'middle';
      const labelX=x(p.i)+(edge==='start'?8:edge==='end'?-8:0);
      plot.append(dot,svg('text',{x:labelX,y:y(p.v)-12,'text-anchor':edge,fill:'#235c49','font-size':12},format(p.v,key)));
    }
    box.append(plot);
  }
  function render(){
    const videos=data.nodes.filter(n=>n.sample_id);
    const comparable=videos.filter(n=>n.status==='comparable');
    get('chain-scope').textContent=`发布后 ${data.window_hours} 小时 · ${videos.length} 条视频 · ${comparable.length} 条数据可比`;
    const windows=get('chain-window');windows.replaceChildren(...data.windows.map(v=>new Option(v+' 小时',v)));windows.value=data.window_hours;
    get('chain-window-field').hidden=data.windows.length<2;
    const metric=get('chain-metric'),before=metric.value;metric.replaceChildren(...Object.entries(data.metrics).map(([k,v])=>new Option(v,k)));if(before in data.metrics)metric.value=before;else if(current?.primary_metric in data.metrics)metric.value=current.primary_metric;
    const branch=get('chain-branch');branch.replaceChildren(new Option('选择后续分支',''));for(const b of data.branches)branch.add(new Option(b.title,b.id));get('chain-branches').hidden=!data.branches.length;
    get('chain-root').hidden=data.selected_id===data.root_id;
    renderTable();
    get('chain-warning').textContent=[...data.warnings,data.limitation].join('\n');
    chart();
  }
  function renderTable(){
    const metric=get('chain-metric').value;
    const table=make('table',undefined,'comparison-table'),head=make('tr');
    for(const name of ['视频',data.metrics[metric]])head.append(make('th',name));
    const thead=make('thead');thead.append(head);table.append(thead);const body=make('tbody');
    for(const n of data.nodes){
      const tr=make('tr');const name=make('td');const details=make('details');details.append(make('summary',n.title));
      details.append(make('p',n.reason,'helper'));
      if(n.changes.length)details.append(make('p',n.changes.join(' · '),'helper'));
      if(n.observed_at)details.append(make('p',`统计时间：${n.observed_at}；实际观察：${n.actual_hours??'未知'} 小时${n.approximate?'；含近似数值':''}`,'helper'));
      const actions=make('div',undefined,'chain-row-actions');
      if(n.cycle_id!==current?.id)actions.append(action('查看关联轮次',()=>openCycle(n.cycle_id,true)));
      if(n.review_run)actions.append(action('查看复盘',()=>openWork('/?run='+encodeURIComponent(n.review_run),'查看复盘')));
      if(n.record_id)actions.append(action('查看数据截图',()=>openWork('/metrics?id='+encodeURIComponent(n.record_id),'查看数据截图')));
      for(const r of n.record_options||[])if(n.status==='conflict')actions.append(action('核对 '+r.observed_at,()=>openWork('/metrics?id='+encodeURIComponent(r.id),'核对候选记录')));
      details.append(actions);
      name.append(details);tr.append(name);
      const comparable=n.status==='comparable';
      const value=comparable?(n.values[metric]==null?'缺少此指标':format(n.values[metric],metric)):labels[n.status];
      tr.append(make('td',value,comparable?'chain-value':'chain-muted'));body.append(tr);
    }
    table.append(body);get('chain-table').replaceChildren(table);
  }
  async function load(id,hours){
    const turn=++version;selected=id;get('chain-loading').textContent='正在读取创作链…';
    try{const r=await fetch('/api/cycles/chain?id='+encodeURIComponent(id)+(hours?'&hours='+encodeURIComponent(hours):''));const value=await r.json();if(turn!==version)return;if(!r.ok)throw new Error(value.error||'读取失败');data=value;render();get('chain-loading').textContent='';}
    catch(e){if(turn===version){data=null;get('chain-loading').textContent='创作链读取失败：'+e.message;get('chain-chart').replaceChildren();get('chain-table').replaceChildren();}}
  }
  get('chain-root').onclick=()=>{if(data)load(data.root_id,get('chain-window').value);};
  get('chain-metric').onchange=()=>{if(data){renderTable();chart();}};
  get('chain-window').onchange=()=>load(selected,get('chain-window').value);
  get('chain-branch').onchange=()=>{if(get('chain-branch').value)load(get('chain-branch').value,get('chain-window').value);};
  if(typeof current!=='undefined'&&current)load(current.id);
  return {load};
})();
