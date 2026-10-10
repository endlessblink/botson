"use strict";
(()=>{
const $=id=>document.getElementById(id);
const ddStart=$('dd-start'),ddStatus=$('dd-status'),choices=$('dd-choices');
let run=null,busy=false;
const hintIcon={noise:'🔊',shine:'✨',warm:'🔥',fog:'🌫️',boss:'👹',monster:'👹',trap:'🪤',treasure:'💰',shrine:'⛲'};
const relicIcon={sword:'⚔️',shield:'🛡️',potion:'🧪',coin:'🪙',lantern:'🏮'};
function tab(name){
  $('dungeon').hidden=name!=='dungeon';$('memory').hidden=name!=='memory';
  $('tab-dungeon').classList.toggle('on',name==='dungeon');$('tab-memory').classList.toggle('on',name==='memory');
}
$('tab-dungeon').addEventListener('click',()=>tab('dungeon'));
$('tab-memory').addEventListener('click',()=>tab('memory'));
async function board(){
  const result=await api('/api/dungeon/leaderboard');const list=$('dd-leaderboard');list.replaceChildren();
  $('dd-week').textContent=C.dd_week+' '+result.week;
  for(const p of result.players){
    const row=document.createElement('li');
    for(const [cls,text] of [['rank',p.rank],['player',p.alias],['floors',p.floors+' '+C.dd_floors_short],['points',p.score]]){
      const span=document.createElement('span');span.className=cls;span.textContent=String(text);row.append(span);
    }list.append(row);
  }$('dd-empty').hidden=result.players.length>0;
}
function choice(icon,title,sub,pick){
  const b=document.createElement('button');b.className='dd-choice';
  const i=document.createElement('b');i.textContent=icon;
  const t=document.createElement('span');t.textContent=title;
  if(sub){const s=document.createElement('small');s.textContent=sub;t.append(s);}
  b.append(i,t);b.addEventListener('click',()=>choose(pick));choices.append(b);
}
function render(view,showLog){
  run=view;choices.replaceChildren();
  $('dd-floor').textContent=view.floor+'/'+view.floors;
  $('dd-hp').textContent='❤'.repeat(view.hp)||'0';
  $('dd-power').textContent=String(view.power)+(view.shield?' 🛡️'.repeat(view.shield):'');
  $('dd-gold').textContent=String(view.gold);
  $('dd-relics').textContent=view.relics.map(r=>relicIcon[r]).join(' ');
  const log=(view.log||[]).map(k=>C['log_'+k]).filter(Boolean).join(' ');
  if(view.finished){
    ddStatus.textContent=(log?log+' ':'')+C['dd_done_'+view.outcome]+' '+C.dd_final_score+': '+view.score;
    ddStart.disabled=false;ddStart.firstChild.textContent=C.dd_again+' ';board().catch(()=>{});return;
  }
  ddStatus.textContent=(log?log+' — ':'')+(view.phase==='door'?C.dd_choose_door:C.dd_choose_relic);
  if(view.phase==='door'){
    view.doors.forEach((d,i)=>choice(hintIcon[d.hint]||'🚪',C['hint_'+d.hint]||d.hint,'',i));
  }else{
    view.offers.forEach((r,i)=>choice(relicIcon[r],C['relic_'+r],'',i));
  }
}
async function choose(pick){
  if(busy)return;busy=true;choices.querySelectorAll('button').forEach(b=>b.disabled=true);
  try{render(await api('/api/dungeon/runs/'+run.run_id+'/choose',{pick}));}
  catch(_){ddStatus.textContent=C.unavailable;choices.querySelectorAll('button').forEach(b=>b.disabled=false);}
  busy=false;
}
ddStart.addEventListener('click',async()=>{
  if(ddStart.disabled)return;ddStart.disabled=true;
  try{render(await api('/api/dungeon/runs',{}));}catch(_){ddStatus.textContent=C.unavailable;ddStart.disabled=false;}
});
// game.js admits the player; wait for its session, then load the weekly board.
const wait=setInterval(async()=>{
  if(!admission)return;clearInterval(wait);
  try{await board();ddStart.disabled=false;}catch(_){}
},300);
})();
