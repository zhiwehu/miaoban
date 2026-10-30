// Preserve the existing ESP-IDF NVS pages and append only connection settings.
// Layout/CRC follows ESP-IDF nvs_partition_gen Page and nvs_page.cpp.
import {crc32} from './echoear-upgrade-core.mjs';
const PAGE=4096,COUNT=126,encoder=new TextEncoder(),decoder=new TextDecoder('utf-8',{fatal:true});
const fail=code=>{throw new Error(code);};
const view=bytes=>new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
const allFF=bytes=>bytes.every(v=>v===255);
function entryCRC(entry){const bytes=new Uint8Array(28);bytes.set(entry.subarray(0,4));bytes.set(entry.subarray(8,32),4);return crc32(bytes);}
function bitmap(page,index){return(page.bytes[32+(index>>2)]>>((index%4)*2))&3;}
function mark(page,index,state){const offset=32+(index>>2),shift=(index%4)*2;page.bytes[offset]=(page.bytes[offset]&~(3<<shift))|(state<<shift);}
function keyOf(entry){const bytes=entry.subarray(8,24),end=bytes.indexOf(0);if(end<1)fail('invalid_nvs');return decoder.decode(bytes.subarray(0,end));}
export function inspectNvs(raw){
 if(!(raw instanceof Uint8Array)||raw.length!==0x4000)fail('invalid_nvs');
 const pages=[],records=[],spaces=new Map();
 for(let offset=0;offset<raw.length;offset+=PAGE){
  const bytes=raw.subarray(offset,offset+PAGE),v=view(bytes);
  if(allFF(bytes)){pages.push({bytes,offset,free:true});continue;}
  const state=v.getUint32(0,true),sequence=v.getUint32(4,true);
  if(![0xfffffffe,0xfffffffc].includes(state)||bytes[8]!==0xfe||v.getUint32(28,true)!==crc32(bytes.subarray(4,28)))fail('invalid_nvs');
  const page={bytes,offset,state,sequence,tail:0};pages.push(page);
  for(let i=0;i<COUNT;){
   const state=bitmap(page,i);if(state===1)fail('invalid_nvs');
   if(state!==3)page.tail=i+1;
   if(state!==2){i++;continue;}
   const entry=bytes.subarray(64+i*32,96+i*32),span=entry[2],type=entry[1],ns=entry[0];
   if(span<1||i+span>COUNT||view(entry).getUint32(4,true)!==entryCRC(entry))fail('invalid_nvs');
   for(let j=0;j<span;j++)if(bitmap(page,i+j)!==2)fail('invalid_nvs');
   const key=keyOf(entry),record={page,index:i,span,type,ns,key,entry};
   if([0x21,0x41,0x42].includes(type)){
    const length=view(entry).getUint16(24,true);record.value=bytes.subarray(96+i*32,96+i*32+length);
    if(length<1||length>(span-1)*32||view(entry).getUint32(28,true)!==crc32(record.value))fail('invalid_nvs');
    if(type===0x21&&record.value.at(-1)!==0)fail('invalid_nvs');
   }
   records.push(record);page.tail=i+span;i+=span;
  }
 }
 const used=pages.filter(p=>!p.free).sort((a,b)=>a.sequence-b.sequence);
 if(!used.length||new Set(used.map(p=>p.sequence)).size!==used.length)fail('invalid_nvs');
 records.sort((a,b)=>a.page.sequence-b.page.sequence||a.index-b.index);
 for(const r of records)if(r.ns===0){
  if(r.type!==1||r.span!==1||r.entry[24]<1||r.entry[24]>254)fail('invalid_nvs');
  if(spaces.has(r.key)&&spaces.get(r.key)!==r.entry[24])fail('invalid_nvs');spaces.set(r.key,r.entry[24]);
 }
 for(const r of records)if(r.ns!==0&&![...spaces.values()].includes(r.ns))fail('invalid_nvs');
 return{pages,records,spaces};
}
function entry(ns,key,type,value){
 const bytes=new Uint8Array(32).fill(255),name=encoder.encode(key);if(name.length>15)fail('invalid_nvs_key');
 bytes[0]=ns;bytes[1]=type;bytes[2]=1;bytes.fill(0,8,24);bytes.set(name,8);bytes[8+name.length]=0;
 if(type===1)bytes[24]=value;
 else if(type===0x14)view(bytes).setInt32(24,value,true);
 else{
  const payload=encoder.encode(value+'\0');bytes[2]=1+Math.ceil(payload.length/32);
  view(bytes).setUint16(24,payload.length,true);view(bytes).setUint32(28,crc32(payload),true);
  const result=new Uint8Array(bytes[2]*32).fill(255);view(bytes).setUint32(4,entryCRC(bytes),true);result.set(bytes);result.set(payload,32);return result;
 }
 view(bytes).setUint32(4,entryCRC(bytes),true);return bytes;
}
export function connectionNvs(raw,{ssid,password,ota_url,websocket}){
 if(typeof ssid!=='string'||encoder.encode(ssid).length<1||encoder.encode(ssid).length>32||ssid.includes('\0'))fail('invalid_wifi');
 if(typeof password!=='string'||encoder.encode(password).length>64||password.includes('\0')||password.length>0&&encoder.encode(password).length<8)fail('invalid_wifi');
 if(typeof ota_url!=='string'||!/^http:\/\/[^/]+\/xiaozhi\/ota\/[A-Za-z0-9_-]+\/$/.test(ota_url)||ota_url.length>512||
  !websocket||typeof websocket.url!=='string'||!/^ws:\/\/[^/]+\/xiaozhi\/v1\/$/.test(websocket.url)||typeof websocket.token!=='string'||! /^[A-Za-z0-9_-]{16,128}$/.test(websocket.token)||websocket.version!==1)fail('invalid_connection');
 if(new URL(ota_url).host!==new URL(websocket.url).host)fail('invalid_connection');
 const data=raw.slice(),nvs=inspectNvs(data),changes=[['wifi','ssid',0x21,ssid],['wifi','password',0x21,password],['wifi','ota_url',0x21,ota_url],['wifi','force_ap',0x14,0],['websocket','url',0x21,websocket.url],['websocket','token',0x21,websocket.token],['websocket','version',0x14,1]];
 let sequence=Math.max(...nvs.pages.filter(p=>!p.free).map(p=>p.sequence));
 if(sequence>0xfffffffc)fail('invalid_nvs');
 let active=nvs.pages.filter(p=>!p.free&&p.state===0xfffffffe).sort((a,b)=>b.sequence-a.sequence)[0];
 function append(bytes){
  const span=bytes.length/32;
  if(!active||COUNT-active.tail<span){
   const free=nvs.pages.filter(p=>p.free);if(free.length<2)fail('nvs_space_required');
   if(active){view(active.bytes).setUint32(0,0xfffffffc,true);active.state=0xfffffffc;}
   active=free[0];active.free=false;active.sequence=++sequence;active.tail=0;active.state=0xfffffffe;
   const v=view(active.bytes);v.setUint32(0,active.state,true);v.setUint32(4,sequence,true);active.bytes[8]=0xfe;v.setUint32(28,crc32(active.bytes.subarray(4,28)),true);
  }
  for(let j=0;j<span;j++)if(bitmap(active,active.tail+j)!==3)fail('invalid_nvs');
  active.bytes.set(bytes,64+active.tail*32);for(let j=0;j<span;j++)mark(active,active.tail+j,2);active.tail+=span;
 }
 for(const name of ['wifi','websocket'])if(!nvs.spaces.has(name)){
  const used=new Set(nvs.spaces.values());let index=1;while(used.has(index))index++;if(index>254)fail('invalid_nvs');
  append(entry(0,name,1,index));nvs.spaces.set(name,index);
 }
 for(const r of nvs.records){
  const wifi=r.ns===nvs.spaces.get('wifi')&&/^(?:ssid[0-9]?|password[0-9]?|ota_url|force_ap|remember_bssid)$/.test(r.key);
  const ws=r.ns===nvs.spaces.get('websocket')&&['url','token','version'].includes(r.key);
  const mqtt=r.ns===nvs.spaces.get('mqtt')&&r.key==='endpoint';
  if(wifi||ws||mqtt)for(let j=0;j<r.span;j++)mark(r.page,r.index+j,0);
 }
 for(const [ns,key,type,value] of changes)append(entry(nvs.spaces.get(ns),key,type,value));
 inspectNvs(data);return data;
}
