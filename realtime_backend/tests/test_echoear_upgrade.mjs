import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {crc32,selectBoot,makeBootRecord,validateSecurity,validateBackup,encodeBackup,decodeBackup} from '../voicehub/static/echoear-upgrade-core.mjs';

test('OTA CRC matches ESP-IDF seeded CRC32 and new entry selects other slot',()=>{
 assert.equal(crc32(new Uint8Array([1,0,0,0])),0x4743989a);
 const data=new Uint8Array(8192).fill(255);data.set(makeBootRecord(1),0);
 const boot=selectBoot(data);assert.equal(boot.slot,0);assert.equal(boot.sequence,1);
 data.set(makeBootRecord(2),4096);assert.equal(selectBoot(data).slot,1);
 data[4096+28]^=1;assert.equal(selectBoot(data).slot,0);
});
test('unknown OTA metadata is never guessed',()=>{
 const data=new Uint8Array(8192).fill(255);data[0]=17;
 assert.throws(()=>selectBoot(data));
 assert.equal(selectBoot(new Uint8Array(8192).fill(255)).slot,0);
});
test('protected devices fail before any flash operation',()=>{
 for(const info of [{chipId:5,flashCryptCnt:0,parsedFlags:{}},{chipId:9,flashCryptCnt:1,parsedFlags:{}},
  {chipId:9,flashCryptCnt:0,parsedFlags:{SECURE_BOOT_EN:true}}])assert.throws(()=>validateSecurity(info,'16MB'));
 assert.throws(()=>validateSecurity({chipId:9,flashCryptCnt:0,parsedFlags:{}},'8MB'));
 validateSecurity({chipId:9,flashCryptCnt:0,parsedFlags:{}},'16MB');
 validateSecurity({chipId:9,flashCryptCnt:0,parsedFlags:{}},'32MB');
 assert.throws(()=>validateSecurity({chipId:9,flashCryptCnt:0,parsedFlags:{}},undefined));
});
test('backup integrity, identity and permitted address ranges are checked',async()=>{
 const regions=[{name:'nvs',address:0x9000,data:new Uint8Array(0x4000)},
 {name:'otadata',address:0xd000,data:new Uint8Array(0x2000)},
 {name:'target',address:0x410000,data:new Uint8Array(0x3f0000)}];
 const bytes=await encodeBackup({mac:'aa:bb',layout:'known',targetSlot:1},regions);
 const backup=await decodeBackup(bytes);validateBackup(backup,{mac:'aa:bb',layout:'known'});
 assert.throws(()=>validateBackup(backup,{mac:'wrong',layout:'known'}));
 backup.regions[2].address=0;assert.throws(()=>validateBackup(backup,{mac:'aa:bb',layout:'known'}));
 const damaged=bytes.slice();damaged[damaged.length-1]^=1;await assert.rejects(()=>decodeBackup(damaged));
});

import {PROFILE,sha256,inspectDevice,inspectImage,upgrade,restore} from '../voicehub/static/echoear-upgrade-core.mjs';
import {fixture} from './fixtures/echoear-loader.mjs';
test('upgrade only writes inactive app and one boot-selection sector, then recovery restores prior boot',async()=>{
 const f=await fixture();await upgrade(f.loader,f.device,f.manifest,f.next,f.backup);
 assert.deepEqual(f.writes,[PROFILE.apps[1],PROFILE.ota+4096]);
 assert.equal(selectBoot(f.flash.slice(PROFILE.ota,PROFILE.ota+8192)).slot,1);
 assert.deepEqual(f.flash.slice(PROFILE.apps[0],PROFILE.apps[0]+f.old.length),f.old);
 assert.deepEqual([...f.flash.slice(0x800000,0x800003)],[7,8,9]);
 await restore(f.loader,f.device,f.backup);
 assert.equal(selectBoot(f.flash.slice(PROFILE.ota,PROFILE.ota+8192)).slot,0);
 assert.deepEqual(f.writes.slice(2),[PROFILE.apps[1],PROFILE.nvs,PROFILE.ota]);
});
test('failed image verification cannot switch the boot slot',async()=>{
 const f=await fixture();f.loader.writeFlash=async options=>{f.writes.push(options.fileArray[0].address);};
 await assert.rejects(()=>upgrade(f.loader,f.device,f.manifest,f.next,f.backup),/flash_verification_failed/);
 assert.deepEqual(f.writes,[PROFILE.apps[1]]);
 assert.equal(selectBoot(f.flash.slice(PROFILE.ota,PROFILE.ota+8192)).slot,0);
});
test('unvalidated release, wrong identity, altered image and altered device state never start writing',async()=>{
 const f=await fixture();f.manifest.install.validated=false;
 await assert.rejects(()=>upgrade(f.loader,f.device,f.manifest,f.next,f.backup),/release_not_hardware_validated/);
 f.manifest.install.validated=true;
 const bad=f.next.slice();bad[50]^=1;await assert.rejects(()=>upgrade(f.loader,f.device,f.manifest,bad,f.backup));
 f.flash[PROFILE.ota]^=1;await assert.rejects(()=>upgrade(f.loader,f.device,f.manifest,f.next,f.backup),/device_state_changed/);
 assert.deepEqual(f.writes,[]);
});
test('unknown firmware fingerprint is not accepted just because chip and partition match',async()=>{
 const f=await fixture();f.manifest.install.upgrade_from_sha256=['f'.repeat(64)];
 await assert.rejects(()=>inspectDevice(f.loader,f.manifest),/unrecognized_existing_firmware/);
 assert.deepEqual(f.writes,[]);
});

test('32MB physical Flash still requires exact layout and known application',async()=>{
 const f=await fixture();f.loader.detectFlashSize=async()=> '32MB';
 assert.equal((await inspectDevice(f.loader,f.manifest)).current.sha256,await sha256(f.old));
 f.manifest.install.partition_sha256='0'.repeat(64);
 await assert.rejects(()=>inspectDevice(f.loader,f.manifest),/unsupported_partition_table/);
 assert.deepEqual(f.writes,[]);
});

test('inspection bounds serial reads and reports application read progress',async()=>{
 const f=await fixture(),read=f.loader.readFlash.bind(f.loader),sizes=[],progress=[];
 f.loader.readFlash=async(address,size)=>{
  sizes.push(size);assert.ok(size<=64*1024,'large burst risks overflowing the browser serial buffer');
  return read(address,size);
 };
 const device=await inspectDevice(f.loader,f.manifest,(text,value)=>progress.push([text,value]));
 assert.equal(device.current.sha256,await sha256(f.old));
 assert.ok(sizes.length>2);assert.ok(progress.some(([text])=>text.includes('读取现有应用')));
 assert.deepEqual(f.writes,[]);
});

test('bounded reads reject truncated serial data before accepting an image',async()=>{
 const f=await fixture(),read=f.loader.readFlash.bind(f.loader);
 f.loader.readFlash=async(address,size)=>{
  const bytes=await read(address,size);return address>=PROFILE.apps[0]?bytes.slice(0,-1):bytes;
 };
 await assert.rejects(()=>inspectDevice(f.loader,f.manifest),/flash_read_incomplete/);
 assert.deepEqual(f.writes,[]);
});

test('device inspection reads only the actual image rather than unused application space',async()=>{
 const f=await fixture(),read=f.loader.readFlash.bind(f.loader);let bytes=0;
 f.loader.readFlash=async(a,n)=>{bytes+=n;return read(a,n)};
 const device=await inspectDevice(f.loader,f.manifest);
 assert.equal(device.current.sha256,await sha256(f.old));
 assert.ok(bytes<20000,'small firmware must not cause a 4MB partition transfer');
});

test('image length discovery rejects segment ranges outside the application partition',async()=>{
 const f=await fixture();new DataView(f.flash.buffer).setUint32(PROFILE.apps[0]+28,0xffffffff,true);
 await assert.rejects(()=>inspectDevice(f.loader,f.manifest),/invalid_image/);assert.deepEqual(f.writes,[]);
});
