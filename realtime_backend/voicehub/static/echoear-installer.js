import {PROFILE,sha256,inspectDevice,readFlashBounded,encodeBackup,decodeBackup,validateBackup,upgrade,restore} from './echoear-upgrade-core.mjs';
const $=id=>document.getElementById(id);
const messages={invalid_image:'读取的应用镜像格式不完整，请重新检查设备。',wrong_image:'当前应用镜像的芯片或配置不符合这个升级包。',invalid_image_checksum:'读取的应用校验和未通过，请重新检查设备。',invalid_image_hash:'读取的应用指纹校验未通过，请重新检查设备。',wrong_device:'芯片型号或实际 Flash 容量不在支持范围内，已停止检查。',protected_device:'设备启用了安全保护，本向导不支持升级，请联系管理员。',
 unsupported_partition_table:'设备分区与当前升级包不匹配。本向导不能直接安装到这台新设备，需要先确认对应固件方案。',unrecognized_existing_firmware:'已读到设备，但当前固件不在已验证的升级来源中。请记录设备型号和固件版本，不能直接安装此升级包。',
 flash_read_incomplete:'设备返回的数据不完整，检查已停止，没有开始安装。请重新连接后重试。',
 invalid_ota_data:'无法确认设备的启动分区，已停止操作。',pending_ota_validation:'设备上次升级尚未确认，请先让设备正常启动。',
 backup_wrong_device:'这份备份不属于当前设备，请选择正确备份。',backup_checksum_failed:'备份校验未通过，请勿用它恢复。',
 invalid_backup_range:'备份的写入范围不受支持，已停止操作。',flash_verification_failed:'写入后的检查未通过。请保持 USB 连接，使用已保存的备份恢复。',
 device_state_changed:'设备状态已改变，请重新连接检查，再保存一份新的备份。',release_not_hardware_validated:'这一版本还在等待实机验收，暂不能安装。'};
let releaseKey='',release,loader,transport,device,backup,recovery,busy=false,writing=false,expectedMac='',heard=false;
let lastStage='';
function status(text,progress){lastStage=text;$('echoear-install-status').textContent=text;if(progress!==undefined)$('echoear-install-progress').value=Math.round(Math.max(0,Math.min(1,progress))*100);}
function controls(){
 const supported=!!navigator.serial&&!!window.isSecureContext&&!!window.showSaveFilePicker;
 $('echoear-serial-note').textContent=supported?'使用数据线连接猫头的 USB 接口，选择猫头对应设备。检查会暂时进入下载模式，期间停止联网对话；检查或备份结束后点“断开 USB”让设备重启。新机器也需核对分区和原厂固件，能联网对话不代表适配此升级包。':'请用电脑上的 Chrome 或 Edge，通过 HTTPS 或本机 localhost 打开本页面。';
 $('echoear-connect').disabled=busy||!supported||!release?.install;
 $('echoear-backup').disabled=busy||!device||!window.showSaveFilePicker;
 $('echoear-upgrade').disabled=busy||!backup||release?.install?.validated!==true;
 $('echoear-restore-file').disabled=busy||!device;
 $('echoear-restore').disabled=busy||!device||!recovery;
 $('echoear-disconnect').disabled=busy||!transport;
 $('echoear-heard').disabled=!$('echoear-heard').dataset.ready;
}
async function action(fn){if(busy)return;busy=true;controls();try{await fn();}catch(e){
 const stage=lastStage;console.warn('EchoEar operation failed:',stage,e.name,e.message);
 const cancelled=!writing&&(e.name==='AbortError'||e.name==='NotFoundError');
 const serialTimeout=/No serial data|Serial data stream stopped|timed?\s*out|Timeout/i.test(e.message||'');
 const message=cancelled?'已取消。设备没有开始安装。':messages[e.message]||
  (serialTimeout?'USB 读取中断或超时。请重新连接；若反复发生，请保留下面的阶段和错误信息。':'操作没有完成。请检查数据线、关闭占用串口的软件，再重新连接；已有备份可用于恢复。');
 // A failed inspection must not retain a stale loader or keep the robot in
 // download mode. Never reset automatically after a failed flash write.
 if(!writing)await disconnect();
 status(message+(cancelled?'':'\n停止阶段：'+stage+'\n错误：'+String(e.message||e.name).slice(0,200)));
 }finally{busy=false;writing=false;controls();}}
async function disconnect(){
 if(loader){try{await loader.after('hard_reset');}catch{}}
 if(transport){try{await transport.disconnect();}catch{}}
 loader=transport=device=backup=recovery=null;
}
window.addEventListener('voicehub:firmware',event=>{
 const next=event.detail;
 if(busy)return;
 const key=JSON.stringify([next?.sha256,next?.install]);
 if(releaseKey!==key){device=backup=recovery=null;expectedMac='';heard=false;$('echoear-heard').dataset.ready='';}
 releaseKey=key;release=next;
 $('echoear-release-status').textContent=!release?.install?'安装包还在准备中。已安装适配固件的设备可跳过升级，继续下面的联网设置。':
 release.install.validated?'已提供通过实机验收的版本 '+release.version:'版本 '+release.version+' 已构建，正在等待实机验收；可先检查设备和保存备份。';
 controls();
});
$('echoear-connect').onclick=()=>action(async()=>{
 await disconnect();heard=false;expectedMac='';$('echoear-heard').dataset.ready='';status('请选择 USB 设备…',0);
 const port=await navigator.serial.requestPort();
 const {ESPLoader,Transport}=await import('./vendor/esptool-js-0.7.0/bundle.js');
 transport=new Transport(port,false);
 loader=new ESPLoader({transport,baudrate:115200,romBaudrate:115200,debugLogging:false,serialOptions:{bufferSize:1024*1024},
  terminal:{clean(){},writeLine(text){if(text?.trim())status('USB：'+text.trim());},write(){}}});
 loader.FLASH_READ_TIMEOUT=10000;
 status('正在连接设备，请稍等…',0);await loader.main();
 device=await inspectDevice(loader,release,status);expectedMac=device.mac;
 $('echoear-device-info').textContent='已识别兼容的 EchoEar（ESP32-S3，实际 Flash '+device.flashSize+'），当前应用版本 '+device.current.version+'。设备型号、容量、分区和现有固件检查通过。';
 status('设备检查通过。下一步保存备份。',1);
});
$('echoear-backup').onclick=()=>action(async()=>{
 backup=null;
 const handle=await window.showSaveFilePicker({suggestedName:'EchoEar-'+device.mac.replaceAll(':','')+'.vhbackup',
  types:[{description:'VoiceHub 设备备份',accept:{'application/octet-stream':['.vhbackup']}}]});
 status('正在备份设备。备份含设备设置，只保存在你的电脑。',0);
 const target=await readFlashBounded(loader,PROFILE.apps[device.targetSlot],PROFILE.appSize,(_p,w,t)=>status('正在备份应用，请保持 USB 连接。',w/t));
 const nvs=await loader.readFlash(PROFILE.nvs,PROFILE.nvsSize);
 const regions=[{name:'nvs',address:PROFILE.nvs,data:nvs},{name:'otadata',address:PROFILE.ota,data:device.ota},
  {name:'target',address:PROFILE.apps[device.targetSlot],data:target}];
 const bytes=await encodeBackup({mac:device.mac,layout:device.layout,targetSlot:device.targetSlot},regions);
 const writable=await handle.createWritable();try{await writable.write(bytes);await writable.close();}catch(e){try{await writable.abort();}catch{}throw e;}
 const saved=new Uint8Array(await (await handle.getFile()).arrayBuffer());
 if(await sha256(saved)!==await sha256(bytes))throw new Error('backup_checksum_failed');
 backup=await decodeBackup(saved);validateBackup(backup,device);
 status(release.install.validated?'备份已保存并校验。可以开始安装。':'备份已保存并校验；新版本仍需管理员完成实机验收后才能安装。',1);
});
$('echoear-upgrade').onclick=()=>action(async()=>{
 status('正在校验升级包…',0);
 const response=await fetch(release.download);if(!response.ok)throw new Error('firmware_download_failed');
 const bytes=new Uint8Array(await response.arrayBuffer());
 writing=true;
 await upgrade(loader,device,release,bytes,backup,status);
 await disconnect();status('应用已写入并校验。请等待设备重新启动，然后完成下面的联网设置。',1);
});
$('echoear-restore-file').onchange=event=>action(async()=>{
 const file=event.target.files[0];if(!file)return;
 if(file.size>16*1024*1024)throw new Error('invalid_backup');
 recovery=null;
 const parsed=await decodeBackup(new Uint8Array(await file.arrayBuffer()));validateBackup(parsed,device);recovery=parsed;
 status('备份校验通过，属于这台设备。点击“确认恢复”后，会还原升级前的应用与设备设置。');
});
$('echoear-restore').onclick=()=>action(async()=>{
 status('正在恢复升级前的应用与设备设置，请保持 USB 连接。',0);
 writing=true;
 await restore(loader,device,recovery,status);await disconnect();status('已恢复升级前的备份。请等待设备重新启动。',1);
});
$('echoear-disconnect').onclick=()=>action(async()=>{await disconnect();status('USB 已断开，设备已尝试重启。');});
$('echoear-copy-setup').onclick=async()=>{
 try{
  const response=await fetch('/api/clients/echoear-s3/connection?protocol=xiaozhi_websocket');
  if(response.status===401)throw new Error('请先使用管理员账号登录。');
  const value=await response.json();if(!response.ok)throw new Error(value.error?.message||'请先让管理员开启设备连接服务。');
  await navigator.clipboard.writeText(value.ota_url);$('echoear-setup-status').textContent='连接入口已复制。连接设备热点后，在设备配网页的“高级设置 → 自定义 OTA 地址”粘贴它并保存，再设置 Wi-Fi。';
 }catch(e){$('echoear-setup-status').textContent=e.message||'复制失败，请使用下方的连接设置下载。';}
};
$('echoear-heard').onclick=()=>{heard=true;$('echoear-verify-status').textContent='你已确认听到设备回复，语音接入完成。之后可以在对话记录和日志里查看。';};
setInterval(async()=>{
 if(!expectedMac||document.hidden||$('echoear-installer').hidden||heard)return;
 try{
  const response=await fetch('/api/clients/echoear-s3/verify?device='+encodeURIComponent(expectedMac));
  if(!response.ok)return;const value=await response.json();
  $('echoear-heard').dataset.ready=value.recognized&&value.replied?'yes':'';
  $('echoear-verify-status').textContent=!value.online?'正在等待设备联网。':!value.recognized?'设备已上线。请唤醒它并说一句“你好”。':
   !value.replied?'已收到语音识别，正在等待回复。':value.playback_reported?'设备已上报播放。请确认你是否听到了声音。':'已生成回复。请确认设备是否播放出声音。';controls();
 }catch{}
},3000);
window.addEventListener('beforeunload',event=>{if(busy){event.preventDefault();event.returnValue='';}});
window.addEventListener('pagehide',()=>{disconnect().catch(()=>{});});
controls();
