import test from 'node:test';
import assert from 'node:assert/strict';
import {fixture} from './fixtures/echoear-loader.mjs';
import {encodeDeviceBackup} from '../voicehub/static/echoear-device-backup.mjs';
import {saveAutomaticBackup,latestAutomaticBackup} from '../voicehub/static/echoear-backup-storage.mjs';
const id=n=>String(n).padStart(20,'0')+'-'+'a'.repeat(32);
test('automatic save verifies the exact persisted copy without a file picker',async t=>{
 const f=await fixture(),bytes=await encodeDeviceBackup(f.device,f.flash);let saved=false;
 t.mock.method(globalThis,'fetch',async(url,options)=>{
  if(options?.method==='POST'){assert.deepEqual(options.body,bytes);saved=true;return Response.json({id:id(1)},{status:201});}
  assert.ok(saved);assert.match(url,new RegExp('/'+id(1)+'\\?'));return new Response(bytes);
 });
 const result=await saveAutomaticBackup(bytes,f.device);assert.deepEqual(result.data,f.flash);
});
test('automatic recovery skips a newer checksum-valid backup with invalid firmware',async t=>{
 const f=await fixture(),good=await encodeDeviceBackup(f.device,f.flash);f.flash[0x20000]=0;const bad=await encodeDeviceBackup(f.device,f.flash);let calls=0;
 t.mock.method(globalThis,'fetch',async url=>{
  calls++;const params=new URL(url,'http://localhost').searchParams;assert.equal(params.get('mac'),f.device.mac);assert.equal(params.get('layout'),f.device.layout);
  if(calls===1){assert.equal(params.has('before'),false);return new Response(bad,{headers:{'X-Backup-Id':id(2)}});}
  assert.equal(params.get('before'),id(2));return new Response(good,{headers:{'X-Backup-Id':id(1)}});
 });
 const restored=await latestAutomaticBackup(f.device);assert.equal(restored.data[0x20000],0xe9);assert.equal(calls,2);
});
test('storage failures, missing copies, or failed readback cannot produce a usable backup',async t=>{
 const f=await fixture(),bytes=await encodeDeviceBackup(f.device,f.flash);
 const fetch=t.mock.method(globalThis,'fetch',async()=>Response.json({error:{code:'backup_storage_failed'}},{status:507}));
 await assert.rejects(()=>saveAutomaticBackup(bytes,f.device),/backup_storage_failed/);
 fetch.mock.mockImplementation(async()=>Response.json({error:{code:'backup_not_found'}},{status:404}));
 await assert.rejects(()=>latestAutomaticBackup(f.device),/backup_not_found/);
 fetch.mock.mockImplementation(async(_url,options)=>options?.method==='POST'?Response.json({id:id(1)}):new Response(bytes.subarray(0,bytes.length-1)));
 await assert.rejects(()=>saveAutomaticBackup(bytes,f.device),/backup_checksum_failed/);
});
