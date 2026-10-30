// Reuse bytes only after the stub confirms their digest on this device now.
import {md5} from './echoear-flash-reader.mjs';
import {readFlashBounded} from './echoear-upgrade-core.mjs';
const hex=bytes=>Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
export async function readBackupSnapshot(loader,size,previous,progress=()=>{},options={}){
 if(!previous||previous.length!==size||typeof loader.flashMd5sum!=='function')return readFlashBounded(loader,0,size,progress,options);
 const result=previous.slice();let active=loader,recoveries=0;
 async function reconnect(value){
  if(!options.recover||recoveries>=8)throw new Error('flash_recovery_failed');
  active=await options.recover({...value,attempt:++recoveries});return active;
 }
 for(let address=0;address<size;){
  const length=Math.min(address===0?65536:address===65536?1024*1024-65536:1024*1024,size-address);
  let unchanged=false;
  if(address!==0){
   try{unchanged=(await active.flashMd5sum(address,length)).toLowerCase()===hex(md5(previous.subarray(address,address+length)));}
   catch(error){
    // No guessing after a broken checksum command. Reconnect before reading.
    if(!options.recover)throw error;
    active=await reconnect({address,completed:result.subarray(0,address)});
   }
  }
  if(!unchanged){
   const recovery=options.recover?{recover:async value=>{
    const completed=new Uint8Array(value.address);completed.set(result.subarray(0,address));completed.set(value.completed,address);
    return reconnect({...value,completed});
   }}:{};
   const bytes=await readFlashBounded(active,address,length,()=>{},recovery);result.set(bytes,address);
  }
  address+=length;progress(null,address,size);
 }
 return result;
}
