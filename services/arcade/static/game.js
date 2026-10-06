"use strict";
const C=JSON.parse(document.getElementById('arcade-copy').textContent);
const demo=JSON.parse(document.getElementById('arcade-mode').textContent);
const status=document.getElementById('status'),start=document.getElementById('start');
let admission='',challenge=null,moves=[],accepting=false,pendingAnswer=null;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const pads=C.pad_labels.map((label,index)=>{
  const pad=document.createElement('button');pad.className='pad';pad.disabled=true;
  pad.textContent=String(index+1);pad.setAttribute('aria-label',label);
  pad.addEventListener('click',()=>press(index));document.getElementById('pads').append(pad);return pad;
});
async function api(path,body){
  const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json',...(admission?{Authorization:'Bearer '+admission}:{})},...(body!==undefined?{body:JSON.stringify(body)}:{})});
  const result=await response.json();if(!response.ok)throw new Error(result.detail||'unavailable');return result;
}
async function leaderboard(){
  const result=await api('/api/leaderboard');const board=document.getElementById('leaderboard');board.replaceChildren();
  for(const player of result.players){
    const row=document.createElement('li');
    for(const [className,text] of [['rank',player.rank],['player',player.alias],['points',player.score]]){
      const span=document.createElement('span');span.className=className;span.textContent=String(text);row.append(span);
    }board.append(row);
  }document.getElementById('empty').hidden=result.players.length>0;
}
async function show(next){
  challenge=next;moves=[];accepting=false;pads.forEach(p=>p.disabled=true);
  document.getElementById('round').textContent=String(next.round);
  document.getElementById('score').textContent=String(next.score);
  if(next.finished){status.textContent=C.finished;start.disabled=false;start.firstChild.textContent=C.again+' ';await leaderboard();return;}
  status.textContent=C.watch;
  for(const index of next.sequence){pads[index].classList.add('lit');await sleep(next.flash_ms);pads[index].classList.remove('lit');await sleep(next.gap_ms);}
  await sleep(next.ready_ms);accepting=true;pads.forEach(p=>p.disabled=false);status.textContent=C.repeat;
}
async function submitPending(){
  start.disabled=true;status.textContent=C.checking;
  try{
    const next=await api(pendingAnswer.path,pendingAnswer.body);
    pendingAnswer=null;await show(next);
  }catch(error){
    if(pendingAnswer){
      status.textContent=error.message==='watch_sequence'?C.wait:C.retry_answer;
      start.firstChild.textContent=C.retry+' ';start.disabled=false;
    }else{status.textContent=C.unavailable;}
  }
}
async function press(index){
  if(!accepting)return;moves.push(index);pads[index].classList.add('lit');setTimeout(()=>pads[index].classList.remove('lit'),challenge.gap_ms);
  if(moves.length!==challenge.round)return;
  accepting=false;pads.forEach(p=>p.disabled=true);status.textContent=C.checking;
  await sleep(challenge.gap_ms);pads.forEach(p=>p.classList.remove('lit'));
  pendingAnswer={path:'/api/runs/'+challenge.run_id+'/answer',body:{round:challenge.round,sequence:[...moves]}};
  await submitPending();
}
document.addEventListener('keydown',event=>{const index=Number(event.key)-1;if(!event.repeat&&index>=0&&index<pads.length){event.preventDefault();press(index);}});
start.addEventListener('click',async()=>{
  if(start.disabled)return;
  if(pendingAnswer){await submitPending();return;}
  start.disabled=true;try{await show(await api('/api/runs',{}));}catch(_){status.textContent=C.unavailable;start.disabled=false;}
});
(async()=>{
  try{
    const initData=window.Telegram?.WebApp?.initData||'';
    if(!demo&&!initData){status.textContent=C.needs_telegram;return;}
    window.Telegram?.WebApp?.ready();
    const result=await api('/api/session',{init_data:initData});admission=result.session;
    document.getElementById('alias').textContent=C.alias+': '+result.alias;
    await leaderboard();start.disabled=false;
  }catch(_){status.textContent=C.unavailable;}
})();
