// Offline client acceptance: a committed answer loses its response, then is
// retried exactly once by the player without creating another run or score.
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const basePath=process.argv[2]||'';
class Element {
  constructor(){this.textContent='';this.disabled=false;this.hidden=false;this.children=[];this.listeners={};this.firstChild={textContent:''};this.classList={add(){},remove(){}};}
  setAttribute(){}
  addEventListener(name,fn){this.listeners[name]=fn;}
  append(child){this.children.push(child);}
  replaceChildren(){this.children=[];}
}
const ids=Object.fromEntries(['arcade-copy','arcade-mode','arcade-base-path','status','start','pads','round','score','leaderboard','empty','alias'].map(id=>[id,new Element()]));
ids.start.disabled=true;
ids['arcade-copy'].textContent=JSON.stringify({pad_labels:['a','b','c','d'],checking:'checking',retry:'retry',retry_answer:'retry answer',watch:'watch',repeat:'repeat',again:'again',finished:'finished',alias:'alias',unavailable:'unavailable'});
ids['arcade-mode'].textContent='true';
ids['arcade-base-path'].textContent=JSON.stringify(basePath);
const calls=[];
let answers=0;
const response=value=>({ok:true,json:async()=>value});
const scope={document:{getElementById:id=>ids[id],createElement:()=>new Element(),addEventListener(){}},window:{},setTimeout:fn=>{fn();return 1;},fetch:async(path,options)=>{
  assert.equal(options.credentials,'omit');
  const body=options.body?JSON.parse(options.body):null;calls.push({path,body});
  if(path===basePath+'/api/session')return response({session:'synthetic-session',alias:'Synthetic'});
  if(path===basePath+'/api/leaderboard')return response({players:[]});
  if(path===basePath+'/api/runs')return response({run_id:'run-one',round:1,sequence:[2],score:0,flash_ms:1,gap_ms:1,ready_ms:1,finished:false});
  assert.equal(path,basePath+'/api/runs/run-one/answer');
  answers++;
  if(answers===1)throw new Error('response lost after commit');
  return response({run_id:'run-one',round:2,sequence:[2,1],score:100,flash_ms:1,gap_ms:1,ready_ms:1,finished:false});
}};
vm.createContext(scope);
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../services/arcade/static/game.js'),'utf8'),scope);
const settle=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
(async()=>{
  await settle();assert.equal(ids.start.disabled,false);
  await ids.start.listeners.click();assert.equal(ids.pads.children[2].disabled,false);
  await ids.pads.children[2].listeners.click();await settle();
  assert.equal(answers,1);assert.equal(ids.start.disabled,false);
  assert.equal(ids.start.firstChild.textContent,'retry ');
  assert.ok(ids.pads.children.every(p=>p.disabled));
  await ids.pads.children[0].listeners.click();assert.equal(answers,1);
  await ids.start.listeners.click();
  assert.equal(answers,2);
  assert.deepEqual(calls.filter(c=>c.path.includes('/answer')).map(c=>c.body),[
    {round:1,sequence:[2]},{round:1,sequence:[2]}]);
  assert.equal(calls.filter(c=>c.path===basePath+'/api/runs').length,1);
  assert.ok(calls.every(c=>c.path.startsWith(basePath+'/api/')));
  assert.equal(ids.score.textContent,'100');assert.equal(ids.round.textContent,'2');
  assert.ok(ids.pads.children.every(p=>!p.disabled));
  process.stdout.write('PASS '+(basePath||'/')+': lost answer response retries exact payload; one run; recovered score 100.\n');
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
