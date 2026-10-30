// Complete original Flash is kept only in the user's local backup file.
import {PROFILE,sha256,readFlashBounded,writeAndVerify,inspectImage,selectBoot} from './echoear-upgrade-core.mjs';
const magic=new TextEncoder().encode('VOICEHUB-DEVICE/1\n');
const fail=code=>{throw new Error(code);};
export async function encodeDeviceBackup(device,data){
 const size=device.flashSize==='16MB'?16*1024*1024:device.flashSize==='32MB'?32*1024*1024:0;
 if(!(data instanceof Uint8Array)||data.length!==size||!size)fail('invalid_device_backup');
 const metadata={mac:device.mac,layout:device.layout,flashSize:device.flashSize,sha256:await sha256(data),current_sha256:device.current.sha256};
 const json=new TextEncoder().encode(JSON.stringify(metadata)),bytes=new Uint8Array(magic.length+4+json.length+data.length);
 bytes.set(magic);new DataView(bytes.buffer).setUint32(magic.length,json.length,true);bytes.set(json,magic.length+4);bytes.set(data,magic.length+4+json.length);return bytes;
}
export async function decodeDeviceBackup(bytes){
 if(!(bytes instanceof Uint8Array)||bytes.length>33*1024*1024||bytes.length<magic.length+4||!magic.every((v,i)=>bytes[i]===v))fail('invalid_device_backup');
 const length=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength).getUint32(magic.length,true);
 if(length<1||length>4096||magic.length+4+length>=bytes.length)fail('invalid_device_backup');
 const metadata=JSON.parse(new TextDecoder().decode(bytes.subarray(magic.length+4,magic.length+4+length))),data=bytes.slice(magic.length+4+length);
 if(data.length!==(metadata.flashSize==='16MB'?16*1024*1024:metadata.flashSize==='32MB'?32*1024*1024:0)||metadata.sha256!==await sha256(data))fail('backup_checksum_failed');
 return{...metadata,data};
}
export async function validateDeviceBackup(backup,device){
 if(!(backup.data instanceof Uint8Array)||backup.sha256!==await sha256(backup.data))fail('backup_checksum_failed');
 if(backup.mac!==device.mac||backup.layout!==device.layout||backup.flashSize!==device.flashSize)fail('backup_wrong_device');
 if(await sha256(backup.data.subarray(PROFILE.table,PROFILE.table+3072))!==device.layout)fail('backup_wrong_device');
 const ota=backup.data.subarray(PROFILE.ota,PROFILE.ota+PROFILE.otaSize),boot=selectBoot(ota);
 const image=await inspectImage(backup.data.subarray(PROFILE.apps[boot.slot],PROFILE.apps[boot.slot]+PROFILE.appSize));
 if(image.sha256!==backup.current_sha256)fail('backup_checksum_failed');
}
export async function applyNetwork(loader,device,backup,nvs,progress=()=>{}){
 await validateDeviceBackup(backup,device);
 if(!(nvs instanceof Uint8Array)||nvs.length!==PROFILE.nvsSize)fail('invalid_nvs');
 if(await loader.chip.readMac(loader)!==device.mac)fail('backup_wrong_device');
 for(const [address,size] of [[PROFILE.table,4096],[PROFILE.ota,PROFILE.otaSize],[PROFILE.nvs,PROFILE.nvsSize]]){
  const current=await readFlashBounded(loader,address,size);
  const expected=address===PROFILE.ota?device.ota:backup.data.subarray(address,address+size);
  if(await sha256(current)!==await sha256(expected))fail('device_state_changed');
 }
 await writeAndVerify(loader,PROFILE.nvs,nvs,p=>progress('正在写入连接设置，请保持 USB 连接…',p));
}
export async function restoreDeviceBackup(loader,device,backup,progress=()=>{}){
 await validateDeviceBackup(backup,device);
 if(await loader.chip.readMac(loader)!==device.mac||await sha256(await readFlashBounded(loader,PROFILE.table,4096))!==await sha256(backup.data.subarray(PROFILE.table,PROFILE.table+4096)))fail('backup_wrong_device');
 // Only application slots and settings; never rewrite bootloader/table/assets.
 let active;try{active=selectBoot(await readFlashBounded(loader,PROFILE.ota,PROFILE.otaSize)).slot;}catch{active=undefined;}
 const slots=active===0?[1,0]:[0,1];
 for(const [address,size] of [[PROFILE.apps[slots[0]],PROFILE.appSize],[PROFILE.apps[slots[1]],PROFILE.appSize],[PROFILE.nvs,PROFILE.nvsSize],[PROFILE.ota,PROFILE.otaSize]])
  await writeAndVerify(loader,address,backup.data.slice(address,address+size),p=>progress('正在恢复原固件与连接设置…',p));
}
