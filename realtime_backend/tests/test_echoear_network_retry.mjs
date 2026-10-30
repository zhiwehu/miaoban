import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {setupResponse} from '../voicehub/static/echoear-response.mjs';
function installer({locks}={}){
 const nodes=new Map();const node=()=>({value:'',dataset:{},children:[],disabled:false,hidden:false,textContent:'',append(child){this.children.push(child)},replaceChildren(){this.children=[]},setAttribute(k,v){this[k]=v},removeAttribute(k){delete this[k]},classList:{toggle(){}}});
 const steps=[1,2,3].map(n=>({...node(),dataset:{usbStep:String(n)}})),panels=[1,2,3].map(n=>({...node(),dataset:{usbPanel:String(n)}}));
 const document={getElementById(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)},createElement:node,querySelectorAll(selector){return selector==='[data-usb-step]'?steps:panels}};
 const source=fs.readFileSync('voicehub/static/echoear-installer.js','utf8').replace(/^import .*;\n/gm,'');
 return vm.runInNewContext(source+'\n;({action,prepareConnection,controls,acquireUsbLock,disconnect,nodes,steps,panels,ready(){connectingAt=1;controls()},setDevice(value,link){device=value;transport=link},connected(){return !!device}})',{
  document,nodes,steps,panels,location:{hostname:'127.0.0.1'},navigator:{serial:{},locks},window:{isSecureContext:true,addEventListener(){}},
  console:{warn(){}},setInterval(){},setupResponse,
  fetch:async()=>new Response(JSON.stringify({error:{code:'choose_network',message:'请选择电脑连接的网络'},interfaces:[{interface:'en0',address:'10.159.134.213',label:'Wi-Fi'}]}),{status:409})
 });
}
test('network selection keeps USB connected and asks for a deliberate network choice',async()=>{
 const app=installer();let disconnected=false;app.setDevice({current:{}},{async disconnect(){disconnected=true}});
 await app.action(app.prepareConnection);
 assert.equal(disconnected,false);assert.equal(app.connected(),true);
 assert.equal(app.nodes.get('echoear-setup').disabled,false);
 const select=app.nodes.get('echoear-network-host');assert.equal(select.children[0].value,'');
 assert.match(select.children[1].textContent,/Wi-Fi/);
 assert.equal(app.nodes.get('echoear-network-choice').hidden,false);
 assert.match(app.nodes.get('echoear-install-status').textContent,/请选择电脑连接的网络/);
 assert.doesNotMatch(app.nodes.get('echoear-install-status').textContent,/检查数据线/);
});

test('reuse Wi-Fi mode hides and disables credential fields; new mode requires explicit credentials',()=>{
 const app=installer();app.nodes.get('echoear-wifi-mode').value='keep';app.controls();
 assert.equal(app.nodes.get('echoear-wifi-fields').hidden,true);assert.equal(app.nodes.get('echoear-wifi-password').disabled,true);
 app.nodes.get('echoear-wifi-mode').value='new';app.controls();
 assert.equal(app.nodes.get('echoear-wifi-fields').hidden,false);assert.equal(app.nodes.get('echoear-wifi-password').required,true);
 app.nodes.get('echoear-wifi-open').checked=true;app.controls();
 assert.equal(app.nodes.get('echoear-wifi-password').required,false);assert.equal(app.nodes.get('echoear-wifi-password').disabled,true);
});

test('wizard shows only the current step as USB connects and device restarts',()=>{
 const app=installer();assert.equal(app.steps[0]['aria-current'],'step');assert.deepEqual(app.panels.map(p=>p.hidden),[false,true,true]);
 app.setDevice({current:{}},{});app.controls();assert.equal(app.steps[1]['aria-current'],'step');assert.deepEqual(app.panels.map(p=>p.hidden),[true,false,true]);
 app.ready();assert.equal(app.steps[2]['aria-current'],'step');assert.deepEqual(app.panels.map(p=>p.hidden),[true,true,false]);
});

test('USB ownership spans pages until disconnect and blocks a second page from opening it',async()=>{
 let held=false;const locks={request(_name,_options,callback){if(held)return Promise.resolve(callback(null));held=true;return Promise.resolve(callback({})).finally(()=>{held=false});}};
 const a=installer({locks}),b=installer({locks});
 await a.acquireUsbLock();await assert.rejects(()=>b.acquireUsbLock(),/usb_in_use/);
 await a.disconnect();await b.acquireUsbLock();await b.disconnect();assert.equal(held,false);
});

 test('USB setup works without file picker support and automatic restore needs only a connected device',()=>{
  const app=installer();app.setDevice({current:{}},{});app.controls();
  assert.equal(app.nodes.get('echoear-setup').disabled,false);
  assert.equal(app.nodes.get('echoear-backup').disabled,false);
  assert.equal(app.nodes.get('echoear-restore').disabled,false);
 });

test('connected device asks to start Wi-Fi setup before displaying the form',async()=>{
 const app=installer();app.setDevice({current:{}},{});app.controls();
 await app.nodes.get('echoear-open-network').onclick();
 assert.equal(app.nodes.get('echoear-network-intro').hidden,true);
 assert.equal(app.nodes.get('echoear-network-form').hidden,false);
 assert.equal(app.nodes.get('echoear-wifi-mode').value,'new');
 assert.match(app.nodes.get('echoear-wifi-hint').textContent,/2.4 GHz/);
});
test('disconnect reports failed reboot rather than claiming success',async()=>{
 const app=installer();app.setDevice({current:{}},{async disconnect(){}});
 assert.equal(await app.disconnect(),false);
});
