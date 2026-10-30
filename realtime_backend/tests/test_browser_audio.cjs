const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
// Audio tests isolate the MCP UI; its event handling has separate module tests.
const source = fs.readFileSync('voicehub/static/app.js', 'utf8')
  .replace("import {createMcpUi} from './mcp-ui.js';", '')
  .replace(/await boot\(\);\s*$/, '');
async function client({failSource=false}={}) {
  const order=[], nodes=new Map();
  const node=()=>({disabled:false,hidden:false,value:'',textContent:'',style:{},classList:{toggle(){},add(){},remove(){}},append(){},replaceChildren(){},setAttribute(){},querySelectorAll(){return []}});
  const track={label:'AirPods',readyState:'live',enabled:true,muted:false,stopped:false,
    getSettings(){return {sampleRate:48000,channelCount:1}},stop(){this.stopped=true}};
  const stream={getAudioTracks(){return [track]},getTracks(){return [track]}};
  class AudioContext {
    constructor(options){order.push(['context',options]);this.sampleRate=48000;this.state='suspended';this.audioWorklet={addModule:async()=>{}};this.destination={};}
    async resume(){this.state='running'}
    createMediaStreamSource(){if(failSource)throw new Error('source failed');return {connect(n){return n},disconnect(){}}}
    createGain(){return {gain:{value:1},connect(n){return n},disconnect(){}}}
    async close(){this.state='closed'}
  }
  class Worklet {constructor(){this.port={};}connect(n){return n}disconnect(){}}
  const sandbox={console,createMcpUi:()=>({}),performance:{now(){return 1}},AudioContext,AudioWorkletNode:Worklet,
    navigator:{mediaDevices:{async getUserMedia(){order.push(['microphone']);return stream},async enumerateDevices(){return []},addEventListener(){}}},
    document:{hidden:false,getElementById(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)},querySelectorAll(){return []},createElement:node},
    location:{hash:'#lab'},history:{replaceState(){}},sessionStorage:{getItem(){return null}},
    CustomEvent:class {constructor(type,options){this.type=type;this.detail=options?.detail}},window:{addEventListener(){},dispatchEvent(){}},setInterval(){},clearInterval(){},setTimeout(){},clearTimeout(){}};
  const result=await vm.runInNewContext('(async()=>{'+source+'\nreturn {microphone,stopMicrophone};})()',sandbox);
  return {...result,order,track,nodes};
}
test('selects the input before opening a context at the native device rate',async()=>{
  const c=await client();await c.microphone();
  assert.equal(c.order[0][0],'microphone');
  const options=c.order.find(v=>v[0]==='context')[1];
  assert.equal(options?.sampleRate,undefined);
  c.stopMicrophone();assert.equal(c.track.stopped,true);
});
test('failed graph construction releases the acquired microphone',async()=>{
  const c=await client({failSource:true});
  await assert.rejects(c.microphone(),/source failed/);
  assert.equal(c.track.stopped,true);
});

test('capture packs correct 16 kHz PCM and reports levels at native headset rates',()=>{
 const code=fs.readFileSync('voicehub/static/capture.js','utf8');
 for(const rate of [8000,16000,24000,44100,48000]){
  let Capture;const messages=[];
  vm.runInNewContext(code,{sampleRate:rate,AudioWorkletProcessor:class{constructor(){this.port={postMessage(v){messages.push(v)}}}},
    registerProcessor(name,klass){Capture=klass}});
  const capture=new Capture();
  for(let offset=0;offset<rate;offset+=128){
    const input=new Float32Array(Math.min(128,rate-offset)).fill(.25);
    capture.process([[input]]);
  }
  const audio=messages.filter(v=>!v.type), levels=messages.filter(v=>v.type==='level');
  assert.equal(audio.length,25,'40ms frames at '+rate+' Hz');
  assert.equal(audio.reduce((sum,b)=>sum+b.byteLength,0),32000);
  assert.equal(new Int16Array(audio[0])[0],8192);
  assert.ok(levels.length>=3);
  assert.equal(levels[0].peak,.25);
 }
});
