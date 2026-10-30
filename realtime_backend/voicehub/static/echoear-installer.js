import {readBackupSnapshot} from './echoear-incremental-backup.mjs';
import {saveAutomaticBackup,latestAutomaticBackup} from './echoear-backup-storage.mjs';
import {installFlashReader,installSlipReader} from './echoear-flash-reader.mjs';
import {setupResponse} from './echoear-response.mjs';
import {connectionNvs} from './echoear-nvs.mjs';
import {encodeDeviceBackup,decodeDeviceBackup,validateDeviceBackup,applyNetwork,restoreDeviceBackup} from './echoear-device-backup.mjs';
import {PROFILE,sha256,inspectDevice,inspectRecoveryDevice,readFlashBounded,encodeBackup,decodeBackup,validateBackup,upgrade,restore} from './echoear-upgrade-core.mjs';
const $=id=>document.getElementById(id);
const messages={usb_in_use:'USB 正由另一个页面操作。请等该页面完成或断开后再连接。',wifi_credentials_required:'喵伴没有可沿用的 Wi-Fi，请选择“连接新的 Wi-Fi”并填写。',flash_read_checksum_failed:'USB 读取的数据校验未通过，备份已停止，没有开始写入。',flash_recovery_failed:'USB 读取恢复失败，没有开始写入。',invalid_wifi:'请填写 1–32 字节的 Wi-Fi 名称；密码为 8–64 字节，开放网络可留空。',invalid_connection:'设备连接信息不完整，请重新准备连接。',invalid_nvs:'现有设备设置的格式或校验不受支持，已停止，未清空设置。',nvs_space_required:'设备有效设置已占满，整理后仍无足够空间，未修改设备。请先在高级选项保存备份，再处理。',invalid_device_backup:'自动备份格式无效，设备未开始写入。',backup_not_found:'这台喵伴还没有可用的自动备份。',backup_storage_failed:'本机备份保存或读取失败，请检查磁盘空间和权限。',invalid_image:'读取的应用镜像格式不完整，请重新检查设备。',wrong_image:'当前应用镜像的芯片或配置不符合这个升级包。',invalid_image_checksum:'读取的应用校验和未通过，请重新检查设备。',invalid_image_hash:'读取的应用指纹校验未通过，请重新检查设备。',wrong_device:'芯片型号或实际 Flash 容量不在支持范围内，已停止检查。',protected_device:'设备启用了安全保护，本向导不支持升级，请联系管理员。',
 unsupported_partition_table:'设备分区与当前升级包不匹配。本向导不能直接安装到这台新设备，需要先确认对应固件方案。',unrecognized_existing_firmware:'已读到设备，但当前固件不在已验证的升级来源中。请记录设备型号和固件版本，不能直接安装此升级包。',
 flash_read_incomplete:'设备返回的数据不完整，检查已停止，没有开始安装。请重新连接后重试。',
 invalid_ota_data:'无法确认设备的启动分区，已停止操作。',pending_ota_validation:'设备上次升级尚未确认，请先让设备正常启动。',
 backup_wrong_device:'备份与当前设备不匹配，已停止恢复。',backup_checksum_failed:'备份校验未通过，请勿用它恢复。',
 invalid_backup_range:'备份的写入范围不受支持，已停止操作。',flash_verification_failed:'写入后的检查未通过。请保持 USB 连接，使用已保存的备份恢复。',
 device_state_changed:'设备状态已改变，请重新连接检查，再保存一份新的备份。',release_not_hardware_validated:'这一版本还在等待实机验收，暂不能安装。'};
let releaseKey='',release,loader,transport,device,backup,recovery,busy=false,writing=false,expectedMac='',heard=false;
let lastStage='',fullBackup=null,connectingAt=0,releaseUsbLock=null;const thisComputer=['localhost','127.0.0.1','[::1]'].includes(location.hostname);
function friendlyStatus(text){
 if(/重新连接并从|USB 读取中断/.test(text))return '连接暂时中断，正在自动重试，请保持数据线连接…';
 if(/USB：|读取|芯片|Flash|分区|固件指纹|当前固件检查|应用读取/.test(text))return '正在确认喵伴，请稍等…';
 if(/备份原机|已有原机备份|自动保存并校验|设备设置空间/.test(text))return '正在准备，请保持数据线连接。首次准备可能需要几分钟…';
 if(/自动准备 VoiceHub|校验升级包|核对更新包/.test(text))return '正在准备连接…';
 if(/写入连接设置/.test(text))return '正在为喵伴设置网络，请保持数据线连接…';
 if(/设置启动应用|正在安装应用/.test(text))return '正在更新喵伴，请保持数据线连接…';
 if(/配置已保存并校验/.test(text))return '设置完成，即将尝试重启喵伴…';
 if(/原机备份已自动保存并校验/.test(text))return '准备完成。';
 if(/应用已写入并校验/.test(text))return '设置完成，请等待喵伴重新连接。';
 return text;
}
function status(text,progress){lastStage=text;$('echoear-install-status').textContent=friendlyStatus(text);$('echoear-technical-details').textContent=text;if(progress!==undefined)$('echoear-install-progress').value=Math.round(Math.max(0,Math.min(1,progress))*100);}
function wifiMode(){
 const keep=$('echoear-wifi-mode').value==='keep',open=$('echoear-wifi-open').checked;
 $('echoear-wifi-fields').hidden=keep;
 $('echoear-wifi-ssid').required=!keep;$('echoear-wifi-password').required=!keep&&!open;
 $('echoear-wifi-ssid').disabled=busy||keep;$('echoear-wifi-password').disabled=busy||keep||open;
 $('echoear-wifi-mode').disabled=busy;$('echoear-wifi-open').disabled=busy;
 $('echoear-wifi-hint').textContent=keep?'使用喵伴之前连接过的 Wi-Fi，无需再次填写密码。请让电脑也连接到这个网络。':'使用电脑相同的 2.4 GHz Wi-Fi，可修改名称。请填写密码；开放网络请明确勾选。';
}
function stepControls(){
 const step=connectingAt?3:device?.current?2:1;
 document.querySelectorAll('[data-usb-step]').forEach(el=>{const n=Number(el.dataset.usbStep);if(n===step)el.setAttribute('aria-current','step');else el.removeAttribute('aria-current');el.classList.toggle('done',n<step);});
 document.querySelectorAll('[data-usb-panel]').forEach(el=>el.hidden=Number(el.dataset.usbPanel)!==step);
}
$('echoear-open-network').onclick=async()=>{
 $('echoear-network-intro').hidden=true;$('echoear-network-form').hidden=false;
 $('echoear-wifi-mode').value='new';wifiMode();
 try{
  const response=await fetch('/api/clients/echoear-s3/current-wifi');
  if(response.ok){const value=await response.json();if(value.ssid&&!$('echoear-wifi-ssid').value)$('echoear-wifi-ssid').value=value.ssid;}
 }catch{}
 $('echoear-wifi-hint').textContent=($('echoear-wifi-ssid').value?'已填入电脑当前 Wi-Fi 名称，可修改。':'无法自动读取电脑的 Wi-Fi，请填写名称。')+' 请确认支持 2.4 GHz，并填写密码；也可沿用喵伴已保存的 Wi-Fi。';
};
$('echoear-update-with-setup').onchange=controls;
$('echoear-wifi-mode').onchange=wifiMode;$('echoear-wifi-open').onchange=wifiMode;
function controls(){
 const supported=!!navigator.serial&&!!window.isSecureContext;
 $('echoear-serial-note').textContent=supported?'使用数据线连接猫头的 USB 接口，选择猫头对应设备。检查会暂时进入下载模式，期间停止联网对话；检查或备份结束后点“断开 USB”让设备重启。新机器也需核对分区和原厂固件，能联网对话不代表适配此升级包。':'请用电脑上的 Chrome 或 Edge，通过 HTTPS 或本机 localhost 打开本页面。';
 $('echoear-connect').disabled=busy||!supported||!release?.install;
 $('echoear-backup').disabled=busy||!device?.current||!thisComputer;
 $('echoear-upgrade').disabled=busy||!device?.current||!backup||release?.install?.validated!==true;
 $('echoear-restore').disabled=busy||!device||!thisComputer;
 $('echoear-disconnect').disabled=busy||!transport;
 $('echoear-setup').disabled=busy||!device?.current||!supported||!thisComputer;
 $('echoear-update-with-setup').disabled=busy||release?.install?.validated!==true;
 if(release?.install?.validated!==true)$('echoear-update-with-setup').checked=false;
 $('echoear-update-note').textContent=release?.install?.validated===true?'兼容设备可勾选升级；将先保存原机备份。':'已有兼容固件可以直接连接。候选新固件尚未完成实机验收，更新选项暂不可选。';
 $('echoear-setup').textContent=release?.install?.validated===true&&$('echoear-update-with-setup').checked?'开始安装并配网':'开始配置';
 $('echoear-network-host').disabled=busy;wifiMode();stepControls();
 if(!thisComputer)$('echoear-setup-status').textContent='USB 一体接入请在运行 VoiceHub 的这台电脑上，通过 localhost 打开页面。';
 $('echoear-heard').disabled=!$('echoear-heard').dataset.ready;
 $('echoear-heard').hidden=!$('echoear-heard').dataset.ready;
 $('echoear-disconnect').hidden=!transport;
 $('echoear-install-progress').hidden=!busy;
}
async function action(fn){if(busy)return;busy=true;controls();try{await fn();}catch(e){
 const stage=lastStage;console.warn('EchoEar operation failed:',stage,e.name,e.message);
 const cancelled=!writing&&(e.name==='AbortError'||e.name==='NotFoundError');
 const serialTimeout=/No serial data|Serial data stream stopped|timed?\s*out|Timeout/i.test(e.message||'');
 const message=e.keepConnection?(messages[e.message]||e.message):cancelled?'已取消。设备没有开始安装。':messages[e.message]||
  (serialTimeout?'USB 读取中断或超时。请重新连接；若反复发生，请保留下面的阶段和错误信息。':'操作没有完成。请检查数据线、关闭占用串口的软件，再重新连接；已有备份可用于恢复。');
 // A failed inspection must not retain a stale loader or keep the robot in
 // download mode. Never reset automatically after a failed flash write.
 if(!writing&&!e.keepConnection)await disconnect();
 status(/芯片|Flash|分区|校验|NVS|固件|串口/.test(message)?'暂时无法完成，请重新连接喵伴后再试。':message);
 $('echoear-technical-details').textContent=stage+'\n'+String(e.message||e.name).slice(0,200);
 }finally{busy=false;writing=false;controls();}}
async function acquireUsbLock(){
 if(releaseUsbLock||!navigator.locks)return;
 let decide;const acquired=new Promise(resolve=>{decide=resolve});
 navigator.locks.request('voicehub-echoear-usb',{ifAvailable:true},lock=>{
  if(!lock){decide(false);return;}
  return new Promise(resolve=>{releaseUsbLock=resolve;decide(true);});
 }).catch(()=>decide(false));
 if(!await acquired)throw new Error('usb_in_use');
}
async function disconnect(){
 let resetRequested=false;
 if(loader){try{await loader.after('hard_reset');resetRequested=true;}catch{}}
 if(transport){try{await transport.disconnect();}catch{}}
 loader=transport=device=backup=recovery=fullBackup=null;$('echoear-device-info').textContent='';
 if(releaseUsbLock){releaseUsbLock();releaseUsbLock=null;}
 return resetRequested;
}
window.addEventListener('voicehub:firmware',event=>{
 const next=event.detail;
 if(busy)return;
 const key=JSON.stringify([next?.sha256,next?.install]);
 if(releaseKey!==key){device=backup=recovery=null;expectedMac='';heard=false;$('echoear-heard').dataset.ready='';}
 releaseKey=key;release=next;
 $('echoear-update-with-setup').checked=release?.install?.validated===true;
 $('echoear-release-status').textContent=!release?.install?'安装包还在准备中。已安装适配固件的设备可跳过升级，继续下面的联网设置。':
 release.install.validated?'已提供通过实机验收的版本 '+release.version:'版本 '+release.version+' 已构建，正在等待实机验收；可先检查设备和保存备份。';
 controls();
});
async function openUsbLoader(port){
 const {ESPLoader,Transport,ESPError}=await import('./vendor/esptool-js-0.7.0/bundle.js');
 transport=installSlipReader(new Transport(port,false),ESPError);
 loader=new ESPLoader({transport,baudrate:115200,romBaudrate:115200,debugLogging:false,serialOptions:{bufferSize:1024*1024},
  terminal:{clean(){},writeLine(text){if(text?.trim())status('USB：'+text.trim());},write(){}}});
 loader.FLASH_READ_TIMEOUT=10000;installFlashReader(loader);
}
$('echoear-connect').onclick=()=>action(async()=>{
 await disconnect();$('echoear-network-intro').hidden=false;$('echoear-network-form').hidden=true;heard=false;connectingAt=0;expectedMac='';$('echoear-verify-status').textContent='';$('echoear-heard').dataset.ready='';status('请选择 USB 设备…',0);
 await acquireUsbLock();
 const port=await navigator.serial.requestPort();
 await openUsbLoader(port);
 status('正在连接设备，请稍等…',0);await loader.main();
 try{device=await inspectDevice(loader,release,status);}
 catch(e){
  if(!['invalid_image','wrong_image','invalid_image_checksum','invalid_image_hash','unrecognized_existing_firmware','invalid_ota_data','pending_ota_validation'].includes(e.message))throw e;
  device=await inspectRecoveryDevice(loader,release,status);
  $('echoear-device-info').textContent='喵伴暂时无法完成连接，请在高级选项中尝试恢复。';
  status((messages[e.message]||'当前固件检查未通过。')+' 可展开高级选项，恢复最近的可用备份。',0);return;
 }
 expectedMac=device.mac;
 $('echoear-device-info').textContent='已连接喵伴';
 status('喵伴已连接。点击“开始配网”继续。',1);
});
async function saveOriginalBackup(){
 if(!thisComputer)throw new Error('请在 VoiceHub 本机保存备份。');
  const size=device.flashSize==='32MB'?32*1024*1024:16*1024*1024;
  let previous;
  status('正在检查已有原机备份…',0);
  try{previous=(await latestAutomaticBackup(device)).data;}catch(error){if(error.message!=='backup_not_found')throw error;}
  const flash=await readBackupSnapshot(loader,size,previous,(_chunk,read,total)=>status('正在备份原机固件与设置（'+(read/1024/1024).toFixed(1)+' / '+(total/1024/1024)+' MB）…',read/total),{recover:async({attempt,completed})=>{
  status('USB 读取中断，正在重新连接并从 '+(completed.length/1024/1024).toFixed(1)+' MB 继续（第 '+attempt+' 次）…',completed.length/size);
  const port=transport.device;await transport.disconnect();
  for(let attempt=0;;attempt++){
   await new Promise(resolve=>setTimeout(resolve,200*(attempt+1)));
   try{await openUsbLoader(port);await loader.main();break;}
   catch(error){if(attempt>=2||!/(?:Failed to open|already open)/i.test(error.message||''))throw error;try{await transport.disconnect();}catch{}}
  }
  const identity=await inspectRecoveryDevice(loader,release);
  if(identity.mac!==device.mac||identity.layout!==device.layout||identity.flashSize!==device.flashSize)throw new Error('backup_wrong_device');
  // The first block includes table, NVS and OTA. A reboot or device swap may
  // invalidate a partial backup; never combine it with changed settings.
  const prefix=completed.subarray(0,Math.min(completed.length,64*1024));
  if(prefix.length&&await sha256(await loader.readFlash(0,prefix.length))!==await sha256(prefix))throw new Error('device_state_changed');
  return loader;
 }});
  status('正在自动保存并校验原机备份…',1);
  const encoded=await encodeDeviceBackup(device,flash);
  fullBackup=await saveAutomaticBackup(encoded,device);recovery=fullBackup;
}
async function upgradeBackup(snapshot){
 const regions=[{name:'nvs',address:PROFILE.nvs,data:snapshot.data.slice(PROFILE.nvs,PROFILE.nvs+PROFILE.nvsSize)},{name:'otadata',address:PROFILE.ota,data:device.ota},{name:'target',address:PROFILE.apps[device.targetSlot],data:snapshot.data.slice(PROFILE.apps[device.targetSlot],PROFILE.apps[device.targetSlot]+PROFILE.appSize)}];
 return decodeBackup(await encodeBackup({mac:device.mac,layout:device.layout,targetSlot:device.targetSlot},regions));
}
$('echoear-backup').onclick=()=>action(async()=>{
 await saveOriginalBackup();backup=await upgradeBackup(fullBackup);
 status(release.install.validated?'原机备份已自动保存并校验，可以开始安装。':'原机备份已自动保存并校验；新版本仍需管理员完成实机验收后才能安装。',1);
});
$('echoear-upgrade').onclick=()=>action(async()=>{
 status('正在校验升级包…',0);
 const response=await fetch(release.download);if(!response.ok)throw new Error('firmware_download_failed');
 const bytes=new Uint8Array(await response.arrayBuffer());
 writing=true;
 await upgrade(loader,device,release,bytes,backup,status);
 await disconnect();status('应用已写入并校验。请等待设备重新启动，然后完成下面的联网设置。',1);
});
$('echoear-restore').onclick=()=>action(async()=>{
 status('正在查找并校验这台喵伴最近的可用备份…',0);
 recovery=await latestAutomaticBackup(device);
 status('正在恢复最近的可用备份，请保持 USB 连接。',0);
 writing=true;
 await restoreDeviceBackup(loader,device,recovery,status);
 await disconnect();status('已恢复最近的可用备份。请等待设备重新启动。',1);
});
$('echoear-disconnect').onclick=()=>action(async()=>{await disconnect();status('USB 已断开，设备已尝试重启。');});
async function prepareConnection(){
 try{
 const host=$('echoear-network-host').value;
 const response=await fetch('/api/clients/echoear-s3/prepare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(host?{host}:{})});
 if(response.status===401)throw new Error('请先使用管理员账号登录。');
 const value=await setupResponse(response);
 if(!response.ok){
  if(value.interfaces?.length){
   const select=$('echoear-network-host');select.replaceChildren();
   const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='请选择电脑连接喵伴的网络';select.append(placeholder);
   for(const item of value.interfaces){const option=document.createElement('option');option.value=item.address;option.textContent=(item.label||item.interface)+'（'+item.address+'）';select.append(option);}
   $('echoear-network-choice').hidden=false;
  }
  throw new Error(value.error?.message||'设备连接服务准备失败。');
 }
 return value;
 }catch(error){error.keepConnection=true;throw error;}
}
$('echoear-network-form').onsubmit=event=>{
 event.preventDefault();
 return action(async()=>{
  if(!device||!thisComputer)throw new Error('请在 VoiceHub 本机连接并检查 USB 设备。');
  const keepWifi=$('echoear-wifi-mode').value==='keep',ssid=$('echoear-wifi-ssid').value,password=$('echoear-wifi-open').checked?'':$('echoear-wifi-password').value,bytes=new TextEncoder();
  if(!keepWifi&&(!ssid||bytes.encode(ssid).length>32||ssid.includes('\0')||bytes.encode(password).length>64||password.includes('\0')||!$('echoear-wifi-open').checked&&bytes.encode(password).length<8))throw new Error('invalid_wifi');
  status('正在自动准备 VoiceHub 设备连接服务…',0);
  const connection=await prepareConnection();
  status('正在检查设备设置空间…',0);
  try{connectionNvs(await readFlashBounded(loader,PROFILE.nvs,PROFILE.nvsSize),{ssid,password,keepWifi,...connection});}
  catch(error){if(error.message==='wifi_credentials_required'){$('echoear-wifi-mode').value='new';error.keepConnection=true;}throw error;}
  await saveOriginalBackup();
  // Validate capacity and all settings before the first device write.
  const nvs=connectionNvs(fullBackup.data.subarray(PROFILE.nvs,PROFILE.nvs+PROFILE.nvsSize),{ssid,password,keepWifi,...connection});
  if($('echoear-update-with-setup').checked){
   status('原机备份已保存并校验，正在核对更新包…',0);
   const response=await fetch(release.download);if(!response.ok)throw new Error('firmware_download_failed');
   const image=new Uint8Array(await response.arrayBuffer());
   backup=await upgradeBackup(fullBackup);
   writing=true;const result=await upgrade(loader,device,release,image,backup,status);
   const ota=await readFlashBounded(loader,PROFILE.ota,PROFILE.otaSize);
   device.ota=ota;
   if(result.slot===undefined)throw new Error('flash_verification_failed');
  }
  writing=true;await applyNetwork(loader,device,fullBackup,nvs,status);
  $('echoear-wifi-password').value='';connectingAt=Date.now();heard=false;$('echoear-heard').dataset.ready='';
  status('配置已保存并校验，正在请求喵伴重启…',1);
  const resetRequested=await disconnect();
  status(resetRequested?'设置完成，正在等待喵伴重新连接。':'配置已完成，但自动重启请求失败。请将喵伴完全断电 5 秒后重新开机。',1);
  $('echoear-verify-status').textContent='正在等待喵伴上线，首次连接可能需要一些时间。';
  window.dispatchEvent(new CustomEvent('voicehub:connection-prepared'));
 });
};
$('echoear-copy-setup').onclick=async()=>{
 try{
  const response=await fetch('/api/clients/echoear-s3/prepare',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  if(response.status===401)throw new Error('请先使用管理员账号登录。');
  const value=await setupResponse(response);if(!response.ok)throw new Error(value.error?.message||'请先让管理员开启设备连接服务。');
  await navigator.clipboard.writeText(value.ota_url);$('echoear-setup-status').textContent='连接入口已复制。连接设备热点后，在设备配网页的“高级设置 → 自定义 OTA 地址”粘贴它并保存，再设置 Wi-Fi。';
 }catch(e){$('echoear-setup-status').textContent=e.message||'复制失败，请使用下方的连接设置下载。';}
};
$('echoear-heard').onclick=()=>{heard=true;$('echoear-verify-status').textContent='你已确认听到设备回复，语音接入完成。之后可以在对话记录和日志里查看。';};
setInterval(async()=>{
 if(!expectedMac||!connectingAt||document.hidden||$('echoear-installer').hidden||heard)return;
 try{
  const response=await fetch('/api/clients/echoear-s3/verify?device='+encodeURIComponent(expectedMac));
  if(!response.ok)return;const value=await setupResponse(response);
  $('echoear-heard').dataset.ready=value.recognized&&value.replied?'yes':'';
  $('echoear-verify-status').textContent=!value.online?(connectingAt&&Date.now()-connectingAt>120000?'尚未看到设备上线。请确认 Wi-Fi 名称和密码、电脑与喵伴网络可互通；同名 Wi-Fi 也可能存在客户端隔离。可以在高级选项中尝试恢复。':'正在等待设备联网。'):!value.recognized?'喵伴已联网！请用设备当前唤醒词唤醒（若为“小宝同学”，请说“小宝同学”），再说一句“你好”。':
   !value.replied?'喵伴听到了，正在准备回复…':value.playback_reported?'喵伴正在回复，你听到声音了吗？':'回复已准备好，请听听喵伴有没有回应。';controls();
 }catch{}
},3000);
window.addEventListener('beforeunload',event=>{if(busy){event.preventDefault();event.returnValue='';}});
window.addEventListener('pagehide',()=>{disconnect().catch(()=>{});});
controls();
