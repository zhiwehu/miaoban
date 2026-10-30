import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {inflateSync} from 'node:zlib';
import {connectionNvs,inspectNvs} from '../voicehub/static/echoear-nvs.mjs';
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
test('repeated changes preserve a free GC page or fail without changing the input',()=>{
 let raw=fixture(),failed=false;
 for(let i=0;i<50;i++){
  const before=raw.slice();try{raw=connectionNvs(raw,{...config,ssid:'Network'+i});assert.ok(inspectNvs(raw).pages.some(p=>p.free));}
  catch(e){assert.equal(e.message,'nvs_space_required');assert.deepEqual(raw,before);failed=true;break;}
 }
 assert.ok(failed);
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
