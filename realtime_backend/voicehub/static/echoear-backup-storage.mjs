import {decodeDeviceBackup,validateDeviceBackup} from './echoear-device-backup.mjs';
import {sha256} from './echoear-upgrade-core.mjs';
const endpoint='/api/clients/echoear-s3/backups';
const identity=/^[0-9]{20}-[a-f0-9]{32}$/;
function query(device){return new URLSearchParams({mac:device.mac,layout:device.layout,flashSize:device.flashSize});}
async function checked(response){
 if(response.ok)return response;
 let value;try{value=await response.json()}catch{}
 throw new Error(value?.error?.code||'backup_storage_failed');
}
export async function saveAutomaticBackup(bytes,device){
 await validateDeviceBackup(await decodeDeviceBackup(bytes),device);
 const response=await checked(await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:bytes}));
 const {id}=await response.json();if(!identity.test(id))throw new Error('backup_storage_failed');
 const saved=new Uint8Array(await(await checked(await fetch(endpoint+'/'+id+'?'+query(device)))).arrayBuffer());
 if(await sha256(saved)!==await sha256(bytes))throw new Error('backup_checksum_failed');
 const parsed=await decodeDeviceBackup(saved);await validateDeviceBackup(parsed,device);return parsed;
}
export async function latestAutomaticBackup(device){
 let before='';
 for(;;){
  const params=query(device);if(before)params.set('before',before);
  const response=await checked(await fetch(endpoint+'/latest?'+params)),id=response.headers.get('X-Backup-Id');
  if(!identity.test(id)||before&&id>=before)throw new Error('backup_storage_failed');
  const bytes=new Uint8Array(await response.arrayBuffer());
  try{const parsed=await decodeDeviceBackup(bytes);await validateDeviceBackup(parsed,device);return parsed;}
  catch(error){
   if(!/^(?:invalid_device_backup|backup_checksum_failed|backup_wrong_device|invalid_image|wrong_image|invalid_image_checksum|invalid_image_hash|invalid_ota_data|pending_ota_validation)$/.test(error.message))throw error;
   before=id;
  }
 }
}
