import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {inflateSync} from 'node:zlib';
import {connectionNvs,inspectNvs,compactNvs} from '../voicehub/static/echoear-nvs.mjs';
const fixture=()=>new Uint8Array(inflateSync(Buffer.from(JSON.parse(fs.readFileSync(new URL('./fixtures/nvs-v2-synthetic.json',import.meta.url))).deflate_base64,'base64')));
const config={ssid:'测试网络',password:'sample-password',ota_url:'http://192.168.1.50:8766/xiaozhi/ota/abcdefghijklmnop/',websocket:{url:'ws://192.168.1.50:8766/xiaozhi/v1/',token:'abcdefghijklmnop',version:1}};
function get(data,ns,key){const nvs=inspectNvs(data);const r=nvs.records.findLast(r=>r.ns===nvs.spaces.get(ns)&&r.key===key);return r?.type===0x21?new TextDecoder().decode(r.value).slice(0,-1):r;}
test('official NVS fixture remains readable; writes exact connection fields and preserves opaque settings',()=>{
 const raw=fixture(),original=raw.slice(),result=connectionNvs(raw,config);
 assert.deepEqual(raw,original);assert.equal(get(result,'wifi','ssid'),config.ssid);assert.equal(get(result,'wifi','password'),config.password);
 assert.equal(get(result,'wifi','ota_url'),config.ota_url);assert.equal(get(result,'websocket','url'),config.websocket.url);assert.equal(get(result,'websocket','token'),config.websocket.token);
 assert.equal(get(result,'mqtt','endpoint'),undefined);assert.equal(get(result,'wifi','ssid1'),undefined);
 for(const key of ['volume','opaque'])assert.deepEqual(get(result,'custom',key).entry,get(raw,'custom',key).entry);
 assert.ok(inspectNvs(result).pages.some(p=>p.free));
});
test('corrupt or unsupported NVS and invalid credentials are rejected before writing',()=>{
 const raw=fixture();raw[28]^=1;assert.throws(()=>connectionNvs(raw,config),/invalid_nvs/);
 for(const values of [{ssid:''},{ssid:'猫'.repeat(11)},{password:'short'},{password:'bad\0password'},{websocket:{...config.websocket,url:'ws://foreign.test/xiaozhi/v1/'}}])assert.throws(()=>connectionNvs(fixture(),{...config,...values}));
 const open=connectionNvs(fixture(),{...config,password:''});assert.equal(get(open,'wifi','password'),'');
});
test('repeated changes reclaim erased entries while retaining opaque records and a GC page',()=>{
 let raw=fixture();const original=raw.slice();
 for(let i=0;i<100;i++){
  raw=connectionNvs(raw,{...config,ssid:'Network'+i});assert.ok(inspectNvs(raw).pages.some(p=>p.free));
 }
 assert.equal(get(raw,'wifi','ssid'),'Network99');
 assert.deepEqual(get(raw,'custom','opaque').value,get(original,'custom','opaque').value);
 const packed=compactNvs(raw);assert.deepEqual(get(packed,'custom','opaque').value,get(original,'custom','opaque').value);
});
// Explicit local artifact for independent ESP-IDF checker, not a user partition.
if(process.env.MIAOBAN_NVS_REVIEW_OUTPUT)fs.writeFileSync(process.env.MIAOBAN_NVS_REVIEW_OUTPUT,connectionNvs(fixture(),config));

test('missing websocket namespace is created without changing existing namespace records',()=>{
 const raw=fixture(),nvs=inspectNvs(raw),id=nvs.spaces.get('websocket');
 for(const r of nvs.records)if(r.ns===id||r.ns===0&&r.key==='websocket')for(let j=0;j<r.span;j++){
  const index=r.index+j,offset=r.page.offset+32+(index>>2);raw[offset]&=~(3<<((index%4)*2));
 }
 assert.equal(inspectNvs(raw).spaces.has('websocket'),false);
 const result=connectionNvs(raw,config);assert.equal(get(result,'websocket','url'),config.websocket.url);
 assert.deepEqual(get(result,'custom','opaque').value,get(raw,'custom','opaque').value);
 const r=inspectNvs(result).records.findLast(r=>r.key==='url');
 assert.ok(r.entry.subarray(12,24).every(v=>v===0),'NVS keys must be zero padded');
});

test('reuse mode preserves saved Wi-Fi credentials and alternate networks without requiring input',()=>{
 const raw=fixture(),result=connectionNvs(raw,{...config,ssid:undefined,password:undefined,keepWifi:true});
 for(const key of ['ssid','password','ssid1','password1'])assert.equal(get(result,'wifi',key),get(raw,'wifi',key));
 assert.equal(get(result,'wifi','ota_url'),config.ota_url);
});

test('reuse mode does not invent a network when the device has no saved Wi-Fi',()=>{
 const raw=fixture(),nvs=inspectNvs(raw);
 for(const r of nvs.records)if(r.ns===nvs.spaces.get('wifi')&&/^ssid[0-9]?$/.test(r.key))for(let j=0;j<r.span;j++){
  const index=r.index+j;raw[r.page.offset+32+(index>>2)]&=~(3<<((index%4)*2));
 }
 assert.throws(()=>connectionNvs(raw,{...config,keepWifi:true}),/wifi_credentials_required/);
});

test('reuse mode accepts a saved alternate Wi-Fi slot when slot zero is missing',()=>{
 const raw=fixture(),nvs=inspectNvs(raw);
 for(const r of nvs.records)if(r.ns===nvs.spaces.get('wifi')&&['ssid','password'].includes(r.key))for(let j=0;j<r.span;j++){
  const index=r.index+j;raw[r.page.offset+32+(index>>2)]&=~(3<<((index%4)*2));
 }
 const result=connectionNvs(raw,{...config,keepWifi:true});assert.equal(get(result,'wifi','ssid1'),'OtherNetwork');assert.equal(get(result,'wifi','password1'),get(raw,'wifi','password1'));
});

test('genuinely full live settings fail without deleting unrelated records or mutating input',()=>{
 const raw=new Uint8Array(inflateSync(Buffer.from(JSON.parse(fs.readFileSync(new URL('./fixtures/nvs-v2-dense.json',import.meta.url))).deflate_base64,'base64'))),before=raw.slice();
 assert.throws(()=>connectionNvs(raw,{...config,keepWifi:true}),/nvs_space_required/);assert.deepEqual(raw,before);
});

test('compaction preserves multi-page blob chunks, indexes, and bytes through repeated updates',()=>{
 let raw=new Uint8Array(inflateSync(Buffer.from(JSON.parse(fs.readFileSync(new URL('./fixtures/nvs-v2-blob.json',import.meta.url))).deflate_base64,'base64')));
 const original=inspectNvs(raw).records.filter(r=>r.key==='large').map(r=>r.page.bytes.slice(64+r.index*32,64+(r.index+r.span)*32));
 for(let i=0;i<20;i++)raw=connectionNvs(raw,{...config,keepWifi:true});
 const compacted=compactNvs(raw),actual=inspectNvs(compacted).records.filter(r=>r.key==='large').map(r=>r.page.bytes.slice(64+r.index*32,64+(r.index+r.span)*32));
 assert.deepEqual(actual,original);assert.ok(inspectNvs(compacted).pages.some(p=>p.free));
 if(process.env.MIAOBAN_NVS_BLOB_OUTPUT)fs.writeFileSync(process.env.MIAOBAN_NVS_BLOB_OUTPUT,compacted);
});
