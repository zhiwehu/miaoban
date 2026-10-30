import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {md5,installFlashReader,installSlipReader} from '../voicehub/static/echoear-flash-reader.mjs';
import {readFlashBounded} from '../voicehub/static/echoear-upgrade-core.mjs';
const digest=data=>new Uint8Array(createHash('md5').update(data).digest());
test('transport MD5 agrees with independent Node crypto for boundaries and full read blocks',()=>{
 for(const length of [0,1,3,55,56,63,64,65,127,128,4096,65536]){
  const data=Uint8Array.from({length},(_,i)=>(i*37+length)%256);assert.deepEqual(md5(data),digest(data),'length '+length);
 }
 assert.equal(Buffer.from(md5(new TextEncoder().encode('abc'))).toString('hex'),'900150983cd24fb0d6963f7d28e17f72');
});
function stub(){
 let frames=[],ack=0;const requests=[],acks=[];
 const loader={IS_STUB:true,ESP_READ_FLASH:0xd2,FLASH_READ_TIMEOUT:100,
  async checkCommand(_name,op,payload){assert.equal(frames.length,0,'previous digest must be consumed');assert.equal(op,0xd2);const v=new DataView(payload.buffer);requests.push([v.getUint32(0,true),v.getUint32(4,true)]);assert.equal(v.getUint32(12,true),4);const data=Uint8Array.from({length:v.getUint32(4,true)},(_,i)=>(v.getUint32(0,true)+i)%251);ack=0;const packetSize=v.getUint32(8,true);assert.equal(packetSize,1024);for(let i=0;i<data.length;i+=packetSize)frames.push(data.slice(i,i+packetSize));frames.push(digest(data));return 0;},
  transport:{async read(){return frames.shift()},async write(bytes){const value=new DataView(bytes.buffer).getUint32(0,true);assert.ok(value>ack);ack=value;acks.push(value);}}
 };return{loader:installFlashReader(loader),requests,acks};
}
test('consecutive reads consume their digest and acknowledge exact packet offsets',async()=>{
 const f=stub(),data=await readFlashBounded(f.loader,0,2*65536+123);
 assert.equal(data.length,2*65536+123);assert.deepEqual(f.requests,[[0,65536],[65536,65536],[131072,123]]);
 assert.deepEqual(data,Uint8Array.from({length:data.length},(_,i)=>i%251));assert.equal(f.acks.at(-1),123);
});
test('digest mismatch and truncated packets stop the read',async()=>{
 const f=stub(),read=f.loader.transport.read;f.loader.transport.read=async()=>{const packet=await read();if(packet.length===16)packet[0]^=1;return packet};
 await assert.rejects(()=>f.loader.readFlash(0,4096),/flash_read_checksum_failed/);
 const g=stub();g.loader.transport.read=async()=>new Uint8Array(3);await assert.rejects(()=>g.loader.readFlash(0,4096),/flash_read_incomplete/);
});

test('transient failures resume at the failed block, retaining verified progress',async()=>{
 const read=[],progress=[];let recovery=0;
 const replacement={async readFlash(address,size){read.push(address);return new Uint8Array(size).fill(address/65536)}};
 const initial={async readFlash(address,size){read.push(address);if(address===65536)throw new Error('Serial data stream stopped: Possible serial noise or corruption.');return new Uint8Array(size)}};
 const data=await readFlashBounded(initial,0,3*65536,(_chunk,count)=>progress.push(count),{recover:async({completed,address})=>{
  recovery++;assert.equal(completed.length,65536);assert.equal(address,65536);return replacement;
 }});
 assert.equal(recovery,1);assert.deepEqual(read,[0,65536,65536,131072]);assert.deepEqual(progress,[65536,131072,196608]);
 assert.equal(data[65536],1);assert.equal(data[131072],2);
});
test('recovery is bounded and a failed identity/snapshot check cannot continue',async()=>{
 let attempts=0;const broken={async readFlash(){throw new Error('No serial data received.')}};
 await assert.rejects(()=>readFlashBounded(broken,0,65536,()=>{},{recover:async()=>{attempts++;return broken}}),/No serial data/);assert.equal(attempts,2);
 await assert.rejects(()=>readFlashBounded(broken,0,65536,()=>{},{recover:async()=>{throw new Error('backup_wrong_device')}}),/backup_wrong_device/);
});

test('linear SLIP reader decodes escaped fragmented frames and preserves following frames',async()=>{
 const transport=installSlipReader({buffer:new Uint8Array([0xc0,0xc0,1,0xdb])});
 const pending=transport.read(100);setTimeout(()=>{transport.buffer=new Uint8Array([0xdc,0xdb,0xdd,2,0xc0,0xc0,3,0xc0])},1);
 assert.deepEqual(await pending,new Uint8Array([1,0xc0,0xdb,2]));assert.deepEqual(await transport.read(100),new Uint8Array([3]));
});
test('linear reader rejects malformed frames and bounds waiting',async()=>{
 await assert.rejects(()=>installSlipReader({buffer:new Uint8Array([0xc0,0xdb,1])}).read(20),/Invalid SLIP/);
 await assert.rejects(()=>installSlipReader({buffer:new Uint8Array(0)}).read(2),/Serial data stream stopped/);
});
