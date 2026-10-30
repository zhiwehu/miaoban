import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readBackupSnapshot} from '../voicehub/static/echoear-incremental-backup.mjs';
test('unchanged Flash is reused only after device checksums; settings are always reread',async()=>{
 const previous=new Uint8Array(3*1024*1024).fill(37),current=previous.slice();current[0x9000]=42;let transferred=0;
 const loader={async flashMd5sum(a,n){return createHash('md5').update(current.subarray(a,a+n)).digest('hex')},async readFlash(a,n){transferred+=n;return current.slice(a,a+n)}};
 const result=await readBackupSnapshot(loader,current.length,previous);assert.deepEqual(result,current);assert.equal(transferred,65536);assert.equal(previous[0x9000],37);
 current[1500000]=7;transferred=0;assert.deepEqual(await readBackupSnapshot(loader,current.length,previous),current);assert.equal(transferred,65536+1024*1024);
});
test('missing cache or checksum support reads a complete backup',async()=>{
 const current=new Uint8Array(128*1024).fill(9);let transferred=0;
 const loader={async readFlash(a,n){transferred+=n;return current.slice(a,a+n)}};
 assert.deepEqual(await readBackupSnapshot(loader,current.length,current),current);assert.equal(transferred,current.length);
});
