"use strict";
(()=>{
const $=id=>document.getElementById(id);
const canvas=$('bt-canvas'),ctx=canvas.getContext('2d');
const startBtn=$('bt-start'),jumpBtn=$('bt-jump'),slideBtn=$('bt-slide'),statusEl=$('bt-status');
const W=canvas.width,H=canvas.height,GROUND=290,RUNNER_X=170,PX_PER_MS=0.34,MOVE_MS=480;
let run=null,taps=[],t0=0,raf=0,state='idle',judged=0,used=[],lives=0,combo=0,bestCombo=0,score=0,flash='',flashAt=-1e9;
let pendingReplay=null,sound=true,audio=null,lastMove={kind:'',at:-1e9};
const beatAt=i=>run.lead_ms+Math.floor(i*60000/run.bpm);
$('tab-beat').addEventListener('click',()=>window.arcadeTab('beat'));
$('bt-sound').addEventListener('click',()=>{sound=!sound;$('bt-sound').textContent=sound?C.bt_sound_on:C.bt_sound_off;});
async function board(){
  const result=await api('/api/beat/leaderboard');const list=$('bt-leaderboard');list.replaceChildren();
  $('bt-week').textContent=C.dd_week+' '+result.week;
  for(const p of result.players){
    const row=document.createElement('li');
    for(const [cls,text] of [['rank',p.rank],['player',p.alias],['floors',C.bt_combo+' '+p.combo],['points',p.score]]){
      const span=document.createElement('span');span.className=cls;span.textContent=String(text);row.append(span);
    }list.append(row);
  }$('bt-empty').hidden=result.players.length>0;
}
function setControls(on){jumpBtn.disabled=slideBtn.disabled=!on;}
function tick(at){
  if(!audio)return;const osc=audio.createOscillator(),gain=audio.createGain();
  osc.frequency.value=at.accent?880:440;gain.gain.setValueAtTime(.18,at.when);gain.gain.exponentialRampToValueAtTime(.001,at.when+.08);
  osc.connect(gain).connect(audio.destination);osc.start(at.when);osc.stop(at.when+.09);
}
function scheduleAudio(){
  if(!sound)return;
  try{audio=audio||new (window.AudioContext||window.webkitAudioContext)();audio.resume();}catch(_){audio=null;return;}
  const base=audio.currentTime+.05,count=Math.floor((beatAt(run.track.length))/(run.beat_ms))+1;
  for(let k=0;k<count;k++)tick({when:base+k*run.beat_ms/1000,accent:k%4===0});
}
function input(action){
  if(state!=='play')return;const t=Math.round(performance.now()-t0);
  const prev=taps.length?taps[taps.length-1].t:-1e9;if(t-prev<run.min_gap_ms||t<0)return;
  taps.push({t,a:action});lastMove={kind:action,at:t};
}
function judge(i){
  const item=run.track[i];if(item==='rest')return;const at=beatAt(i);let near=-1;
  for(let n=0;n<taps.length;n++){
    if(used[n]||Math.abs(taps[n].t-at)>run.good_ms)continue;
    if(near<0||Math.abs(taps[n].t-at)<Math.abs(taps[near].t-at))near=n;
  }
  if(item==='coin'){if(near>=0){used[near]=true;score+=30;flash=C.bt_good;flashAt=at;}return;}
  if(near>=0)used[near]=true;
  if(near>=0&&taps[near].a===item){
    const exact=Math.abs(taps[near].t-at)<=run.perfect_ms;
    score+=(exact?100:60)*Math.min(1+Math.floor(combo/8),4);combo++;bestCombo=Math.max(bestCombo,combo);
    flash=exact?C.bt_perfect:C.bt_good;
  }else{combo=0;lives--;flash=C.bt_miss;}
  flashAt=at;
}
function hud(){
  $('bt-score').textContent=String(score);$('bt-combo').textContent=String(combo);
  $('bt-lives').textContent='❤'.repeat(Math.max(lives,0))||'0';
}
function draw(now){
  ctx.clearRect(0,0,W,H);ctx.fillStyle='#10150f';ctx.fillRect(0,0,W,H);
  const phase=((now-run.lead_ms)%run.beat_ms+run.beat_ms)%run.beat_ms,pulse=Math.max(0,1-phase/180);
  ctx.fillStyle='rgba(232,206,141,'+(0.08+pulse*0.25)+')';ctx.fillRect(0,GROUND,W,H-GROUND);
  ctx.strokeStyle='#354038';ctx.lineWidth=3;ctx.beginPath();ctx.moveTo(0,GROUND);ctx.lineTo(W,GROUND);ctx.stroke();
  // beat marker where the runner stands
  ctx.strokeStyle='rgba(232,206,141,'+(0.25+pulse*0.6)+')';ctx.beginPath();ctx.moveTo(RUNNER_X,GROUND-150);ctx.lineTo(RUNNER_X,GROUND+30);ctx.stroke();
  for(let i=0;i<run.track.length;i++){
    const item=run.track[i];if(item==='rest')continue;
    const x=RUNNER_X+(beatAt(i)-now)*PX_PER_MS;if(x<-60||x>W+60)continue;
    if(item==='jump'){ctx.fillStyle='#e78b79';ctx.fillRect(x-22,GROUND-44,44,44);}
    else if(item==='slide'){ctx.fillStyle='#85aec8';ctx.fillRect(x-30,GROUND-150,60,92);ctx.fillRect(x-4,GROUND-60,8,16);}
    else{ctx.fillStyle='#ebc570';ctx.beginPath();ctx.arc(x,GROUND-70,16,0,7);ctx.fill();}
  }
  // runner
  const age=now-lastMove.at,moving=age>=0&&age<MOVE_MS;let y=GROUND,h=70;
  if(moving&&lastMove.kind==='jump')y-=Math.sin(age/MOVE_MS*Math.PI)*110;
  if(moving&&lastMove.kind==='slide')h=36;
  ctx.fillStyle='#8dbba4';ctx.fillRect(RUNNER_X-18,y-h,36,h);ctx.fillStyle='#10150f';ctx.fillRect(RUNNER_X+4,y-h+10,8,8);
  if(now-flashAt<500){ctx.fillStyle='#fff';ctx.font='bold 34px Arial';ctx.textAlign='center';ctx.fillText(flash,RUNNER_X+90,GROUND-170);}
}
function frame(){
  const now=performance.now()-t0;
  if(now<0){statusEl.textContent=C.bt_get_ready;}else if(statusEl.textContent===C.bt_get_ready)statusEl.textContent='';
  while(judged<run.track.length&&beatAt(judged)+run.good_ms<now&&lives>0){judge(judged);judged++;}
  hud();draw(now);
  if(lives<=0||judged>=run.track.length){finish();return;}
  raf=requestAnimationFrame(frame);
}
async function finish(){
  state='submit';setControls(false);statusEl.textContent=C.bt_checking;
  pendingReplay={path:'/api/beat/runs/'+run.run_id+'/finish',body:{inputs:taps}};await submit();
}
async function submit(){
  startBtn.disabled=true;
  try{
    const result=await api(pendingReplay.path,pendingReplay.body);pendingReplay=null;state='idle';
    $('bt-score').textContent=String(result.score);$('bt-combo').textContent=String(result.best_combo);
    statusEl.textContent=(result.completed?C.bt_done:C.bt_died)+' · '+C.bt_result+': '+result.score+' · '+C.bt_perfects+': '+result.perfect;
    startBtn.disabled=false;startBtn.firstChild.textContent=C.bt_again+' ';await board();
  }catch(error){
    if(error.message==='too_early'){await new Promise(r=>setTimeout(r,3000));return submit();}
    statusEl.textContent=C.bt_retry;startBtn.firstChild.textContent=C.bt_again+' ';startBtn.disabled=false;
  }
}
startBtn.addEventListener('click',async()=>{
  if(startBtn.disabled)return;
  if(pendingReplay){await submit();return;}
  startBtn.disabled=true;
  try{run=await api('/api/beat/runs',{});}catch(_){statusEl.textContent=C.unavailable;startBtn.disabled=false;return;}
  taps=[];used=[];judged=0;lives=run.lives;combo=0;bestCombo=0;score=0;flash='';lastMove={kind:'',at:-1e9};
  state='play';setControls(true);t0=performance.now();scheduleAudio();raf=requestAnimationFrame(frame);
});
const press=action=>event=>{event.preventDefault();input(action);};
jumpBtn.addEventListener('pointerdown',press('jump'));slideBtn.addEventListener('pointerdown',press('slide'));
let swipe=null;
canvas.addEventListener('pointerdown',event=>{swipe=event.clientY;});
canvas.addEventListener('pointerup',event=>{if(swipe===null)return;input(event.clientY-swipe>30?'slide':'jump');swipe=null;});
document.addEventListener('keydown',event=>{
  if(event.repeat)return;
  if(event.key===' '||event.key==='ArrowUp'){event.preventDefault();input('jump');}
  else if(event.key==='ArrowDown'){event.preventDefault();input('slide');}
});
const wait=setInterval(async()=>{
  if(!admission)return;clearInterval(wait);
  try{await board();startBtn.disabled=false;}catch(_){}
},300);
})();
