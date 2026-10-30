import test from 'node:test';
import assert from 'node:assert/strict';
import {fixture} from './fixtures/echoear-loader.mjs';
import {PROFILE,sha256,upgrade,inspectDevice,inspectRecoveryDevice} from '../voicehub/static/echoear-upgrade-core.mjs';
import {encodeDeviceBackup,decodeDeviceBackup,applyNetwork,restoreDeviceBackup} from '../voicehub/static/echoear-device-backup.mjs';
async function prepared(){const f=await fixture();const bytes=await encodeDeviceBackup(f.device,f.flash);return{...f,full:await decodeDeviceBackup(bytes)};}
test('complete backup keeps exact original Flash and writes only NVS for connection setup',async()=>{
 const f=await prepared();assert.deepEqual(f.full.data,f.flash);
 const nvs=new Uint8Array(PROFILE.nvsSize).fill(3);await applyNetwork(f.loader,f.device,f.full,nvs);
 assert.deepEqual(f.writes,[PROFILE.nvs]);assert.deepEqual(f.flash.slice(PROFILE.nvs,PROFILE.nvs+PROFILE.nvsSize),nvs);
 assert.deepEqual(f.flash.slice(PROFILE.apps[0],PROFILE.apps[0]+f.old.length),f.old);
});
test('wrong device, changed NVS, or altered backup cannot start a write',async()=>{
 const f=await prepared(),nvs=new Uint8Array(PROFILE.nvsSize);
 await assert.rejects(()=>applyNetwork(f.loader,{...f.device,mac:'other'},f.full,nvs),/backup_wrong_device/);
 f.flash[PROFILE.nvs]^=1;await assert.rejects(()=>applyNetwork(f.loader,f.device,f.full,nvs),/device_state_changed/);f.flash[PROFILE.nvs]^=1;
 f.full.data[0]^=1;await assert.rejects(()=>applyNetwork(f.loader,f.device,f.full,nvs),/backup_checksum_failed/);
 assert.deepEqual(f.writes,[]);
});
test('write readback failure is reported and a complete backup restores original apps and settings',async()=>{
 const f=await prepared(),original=f.loader.writeFlash;
 f.loader.writeFlash=async()=>{};await assert.rejects(()=>applyNetwork(f.loader,f.device,f.full,new Uint8Array(PROFILE.nvsSize)),/flash_verification_failed/);
 f.loader.writeFlash=original;await applyNetwork(f.loader,f.device,f.full,new Uint8Array(PROFILE.nvsSize));
 await restoreDeviceBackup(f.loader,f.device,f.full);
 assert.deepEqual(f.flash,f.full.data);
 assert.deepEqual(f.writes,[PROFILE.nvs,PROFILE.apps[1],PROFILE.apps[0],PROFILE.nvs,PROFILE.ota]);
});
test('optional validated update can be followed by connection setup without touching assets',async()=>{
 const f=await prepared();await upgrade(f.loader,f.device,f.manifest,f.next,f.backup);
 f.device.ota=await f.loader.readFlash(PROFILE.ota,PROFILE.otaSize);
 await applyNetwork(f.loader,f.device,f.full,new Uint8Array(PROFILE.nvsSize));
 assert.deepEqual(f.writes,[PROFILE.apps[1],PROFILE.ota+4096,PROFILE.nvs]);
 assert.deepEqual(f.flash.slice(0x800000),f.full.data.slice(0x800000));
});

test('recovery identity check and full restore work with damaged active app and OTA',async()=>{
 const f=await prepared();f.flash[PROFILE.apps[0]]=0;f.flash.fill(0,PROFILE.ota,PROFILE.ota+PROFILE.otaSize);
 await assert.rejects(()=>inspectDevice(f.loader,f.manifest));
 const identity=await inspectRecoveryDevice(f.loader,f.manifest);
 assert.equal(identity.mac,f.device.mac);assert.equal(identity.current,undefined);
 await restoreDeviceBackup(f.loader,identity,f.full);assert.deepEqual(f.flash,f.full.data);
});
