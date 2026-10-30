import assert from 'node:assert/strict';
import {PROFILE,sha256,inspectDevice,encodeBackup,decodeBackup,makeBootRecord} from '../../voicehub/static/echoear-upgrade-core.mjs';
async function image(version){
 const raw=new Uint8Array(336),v=new DataView(raw.buffer);
 raw[0]=0xe9;raw[1]=1;raw[3]=0x40;v.setUint16(12,9,true);raw[23]=1;
 v.setUint32(24,0x3c190020,true);v.setUint32(28,256,true);v.setUint32(32,0xabcd5432,true);
 raw.set(new TextEncoder().encode(version),48);raw.set(new TextEncoder().encode('xiaozhi'),80);
 let sum=0xef;for(const b of raw.subarray(32,288))sum^=b;raw[303]=sum;
 raw.set(new Uint8Array(await crypto.subtle.digest('SHA-256',raw.subarray(0,304))),304);return raw;
}
export async function fixture(){
 const old=await image('old'),next=await image('new'),flash=new Uint8Array(0x1000000).fill(255);
 const table=new Uint8Array(4096).fill(255),v=new DataView(table.buffer);
 const entries=[['nvs',1,2,0x9000,0x4000],['otadata',1,0,0xd000,0x2000],['phy_init',1,1,0xf000,0x1000],
 ['ota_0',0,16,0x20000,0x3f0000],['ota_1',0,17,0x410000,0x3f0000],['assets',1,130,0x800000,0x800000]];
 entries.forEach(([name,type,sub,address,size],i)=>{const p=i*32;v.setUint16(p,0x50aa,true);table[p+2]=type;table[p+3]=sub;
  v.setUint32(p+4,address,true);v.setUint32(p+8,size,true);table.fill(0,p+12,p+28);table.set(new TextEncoder().encode(name),p+12);v.setUint32(p+28,0,true);});
 flash.set(table,PROFILE.table);flash.set(makeBootRecord(1),PROFILE.ota);flash.set(old,PROFILE.apps[0]);flash.set([7,8,9],0x800000);
 const writes=[];
 const loader={chip:{async readMac(){return '90:e5:b1:ae:80:a0'}},async getSecurityInfo(){return{chipId:9,flashCryptCnt:0,parsedFlags:{}}},
  async detectFlashSize(){return'16MB'},async readFlash(address,size){return flash.slice(address,address+size)},
  async writeFlash(options){assert.equal(options.eraseAll,false);for(const file of options.fileArray){writes.push(file.address);flash.set(file.data,file.address);}}};
 const manifest={sha256:await sha256(next),install:{board:'echoear-s3',partition_bytes:3072,partition_sha256:await sha256(table.subarray(0,3072)),
 upgrade_from_sha256:[await sha256(old)],validated:true}};
 const device=await inspectDevice(loader,manifest);
 const backup=await decodeBackup(await encodeBackup({mac:device.mac,layout:device.layout,targetSlot:device.targetSlot},[
 {name:'nvs',address:PROFILE.nvs,data:flash.slice(PROFILE.nvs,PROFILE.nvs+PROFILE.nvsSize)},
 {name:'otadata',address:PROFILE.ota,data:device.ota},{name:'target',address:PROFILE.apps[1],data:flash.slice(PROFILE.apps[1],PROFILE.apps[1]+PROFILE.appSize)}]));
 return{loader,manifest,device,backup,flash,writes,next,old};
}
