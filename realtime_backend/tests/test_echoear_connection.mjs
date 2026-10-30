import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

// Exercise the actual browser connection helper with serial hardware replaced.
const source=await readFile(new URL('../voicehub/static/echoear-installer.js',import.meta.url),'utf8');
const helper=source.slice(source.indexOf('async function connectUsbLoader('),source.indexOf("$('echoear-connect').onclick"));
function fixture(fail){
 const rates=[],closed=[],reset=[],probed=[];
 const context={PROFILE:{table:0x8000},status(){},loader:null,transport:null,
  async openUsbLoader(port,rate){
   assert.equal(port,'usb');rates.push(rate);
   context.loader={async main(){if(fail?.(rate,'connect'))throw new Error('connect failed')},async after(){reset.push(rate)}};
   context.transport={async disconnect(){closed.push(rate)}};
  },async readFlashBounded(loader,address,size){
   assert.equal(address,0x8000);assert.equal(size,4096);probed.push(rates.at(-1));
   if(fail?.(rates.at(-1),'read'))throw new Error('read failed');return new Uint8Array(size);
  }};
 vm.createContext(context);vm.runInContext(helper,context);
 return {context,rates,closed,reset,probed};
}
test('connection uses fast stub speed and verifies a flash reply',async()=>{
 const f=fixture();await f.context.connectUsbLoader('usb');
 assert.deepEqual(f.rates,[460800]);assert.deepEqual(f.probed,[460800]);assert.deepEqual(f.closed,[]);
});
for(const phase of ['connect','read'])test(`fast ${phase} failure reconnects at compatible speed`,async()=>{
 const f=fixture((rate,stage)=>rate===460800&&stage===phase);await f.context.connectUsbLoader('usb');
 assert.deepEqual(f.rates,[460800,115200]);assert.deepEqual(f.closed,[460800]);assert.deepEqual(f.reset,[460800]);
 assert.equal(f.probed.at(-1),115200);
});
test('failure at both speeds is bounded and closes the failed connection',async()=>{
 const f=fixture(()=>true);await assert.rejects(()=>f.context.connectUsbLoader('usb'),/connect failed/);
 assert.deepEqual(f.rates,[460800,115200]);assert.deepEqual(f.closed,[460800,115200]);assert.equal(f.context.loader,null);
});
