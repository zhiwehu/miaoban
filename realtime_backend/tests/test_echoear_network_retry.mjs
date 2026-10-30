import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {setupResponse} from '../voicehub/static/echoear-response.mjs';
function installer(){
 const nodes=new Map();const node=()=>({value:'',dataset:{},children:[],disabled:false,hidden:false,textContent:'',append(child){this.children.push(child)},replaceChildren(){this.children=[]}});
 const document={getElementById(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)},createElement:node};
 const source=fs.readFileSync('voicehub/static/echoear-installer.js','utf8').replace(/^import .*;\n/gm,'');
 return vm.runInNewContext(source+'\n;({action,prepareConnection,nodes,setDevice(value,link){device=value;transport=link},connected(){return !!device}})',{
  document,nodes,location:{hostname:'127.0.0.1'},navigator:{serial:{}},window:{isSecureContext:true,showSaveFilePicker(){},addEventListener(){}},
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
