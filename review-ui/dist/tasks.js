"use strict";
// Published-video reviews feed the next creation, with no current-video revision form.
window.TaskBoard=(()=>{
  async function load(run,accepted){
    const board=document.getElementById('task-board');board.replaceChildren();
    document.getElementById('task-message').textContent='';
    if(!accepted)return;
    const section=document.createElement('section');section.className='task-source';
    const heading=document.createElement('h2');heading.textContent='挑选建议，用到下一条视频';
    const text=document.createElement('p');text.textContent='这条视频的复盘已完成。从认可的建议中挑 1～2 条，写成下一条 B 的创作清单；B 发布后再比较数据。';
    const link=document.createElement('a');link.className='primary';link.textContent='选择 1～2 条建议，准备下一条 →';link.href='/cycles?run='+encodeURIComponent(run);link.target='_top';
    section.append(heading,text,link);board.append(section);
  }
  return {load,isDirty:()=>false};
})();
