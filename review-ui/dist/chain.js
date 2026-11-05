'use strict';
window.ChainOverview=(()=>{
  let data=null,selected='',version=0;
  const get=id=>document.getElementById(id);
  const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
  const labels={comparable:'可比较',unaligned:'暂不可比较',conflict:'记录需核对',missing:'待采集',planned:'待创作'};
  const format=(v,key)=>v===null||v===undefined?'—':String(v)+(key.endsWith('_rate')?'%':'');
  function link(text,href){const a=make('a',text);a.href=href;return a;}
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
    box.append(plot,make('p','缺数据或窗口不一致的作品保留在链中，趋势线在该处断开；悬停数据点可查看采用的做法。','helper'));
  }
  function render(){
    get('chain-scope').textContent=`发布后 ${data.window_hours} 小时（±${data.tolerance_minutes} 分钟） · ${data.goal} · ${data.nodes.filter(n=>n.sample_id).length} 条视频`;
    const windows=get('chain-window');windows.replaceChildren(...data.windows.map(v=>new Option(v+' 小时',v)));windows.value=data.window_hours;
    const metric=get('chain-metric'),before=metric.value;metric.replaceChildren(...Object.entries(data.metrics).map(([k,v])=>new Option(v,k)));if(before in data.metrics)metric.value=before;
    const branch=get('chain-branch');branch.replaceChildren(new Option('选择后续分支',''));for(const b of data.branches)branch.add(new Option(b.title,b.id));get('chain-branches').hidden=!data.branches.length;
    get('chain-root').hidden=data.selected_id===data.root_id;
    get('chain-nodes').replaceChildren(...data.nodes.map((n,i)=>{const item=make('div',undefined,'chain-node');if(n.cover_url){const cover=make('img');cover.src=n.cover_url;cover.alt=n.title+' 的视频抽帧';cover.loading='lazy';item.append(cover);}item.append(make('small','视频 '+(i+1)),link(n.title,'/cycles?id='+encodeURIComponent(n.cycle_id)+'&round=1'),make('span',labels[n.status]));return item;}));
    const table=make('table',undefined,'comparison-table'),head=make('tr');
    for(const name of ['视频名称','播放量','点赞量','每百次播放点赞数','完播率','2秒跳出率','数据状态'])head.append(make('th',name));
    const thead=make('thead');thead.append(head);table.append(thead);const body=make('tbody');
    for(const n of data.nodes){
      const tr=make('tr');const name=make('td');const details=make('details');details.append(make('summary',n.title));
      details.append(make('p',n.reason,'helper'));
      if(n.changes.length)details.append(make('p','采用的做法：'+n.changes.join('；'),'helper'));
      if(n.observed_at)details.append(make('p',`统计时间：${n.observed_at}；实际观察：${n.actual_hours??'未知'} 小时${n.approximate?'；含近似数值':''}`,'helper'));
      details.append(link('查看关联轮次','/cycles?id='+encodeURIComponent(n.cycle_id)+'&round=1'));
      if(n.review_run)details.append(make('br'),link('查看复盘','/?run='+encodeURIComponent(n.review_run)));
      if(n.record_id)details.append(make('br'),link('核对数据截图','/metrics?id='+encodeURIComponent(n.record_id)));
      for(const r of n.record_options||[])if(n.status==='conflict')details.append(make('p'),link('核对候选记录 · '+r.observed_at,'/metrics?id='+encodeURIComponent(r.id)));
      name.append(details);tr.append(name);
      for(const key of ['plays','likes','likes_per_100','completion_rate','bounce_2s_rate'])tr.append(make('td',format(n.values[key],key)));
      tr.append(make('td',labels[n.status]));body.append(tr);
    }
    table.append(body);get('chain-table').replaceChildren(table);
    get('chain-warning').textContent=[...data.warnings,data.limitation].join('\n');chart();
  }
  async function load(id,hours){
    const turn=++version;selected=id;get('chain-loading').textContent='正在读取创作链…';
    try{const r=await fetch('/api/cycles/chain?id='+encodeURIComponent(id)+(hours?'&hours='+encodeURIComponent(hours):''));const value=await r.json();if(turn!==version)return;if(!r.ok)throw new Error(value.error||'读取失败');data=value;render();get('chain-loading').textContent='';}
    catch(e){if(turn===version){get('chain-loading').textContent='创作链读取失败：'+e.message;get('chain-nodes').replaceChildren();get('chain-chart').replaceChildren();get('chain-table').replaceChildren();}}
  }
  get('chain-refresh').onclick=()=>load(selected,get('chain-window').value);
  get('chain-root').onclick=()=>{if(data)load(data.root_id,get('chain-window').value);};
  get('chain-metric').onchange=()=>{if(data)chart();};
  get('chain-window').onchange=()=>load(selected,get('chain-window').value);
  get('chain-branch').onchange=()=>{if(get('chain-branch').value)load(get('chain-branch').value,get('chain-window').value);};
  if(typeof current!=='undefined'&&current)load(current.id);
  return {load};
})();
