// ESP stub READ_FLASH protocol, matched against Espressif's esptool loader.py.
// The digest is a transport integrity check; device/image trust uses SHA256.
const shifts=[7,12,17,22,5,9,14,20,4,11,16,23,6,10,15,21];
const constants=Array.from({length:64},(_,i)=>Math.floor(Math.abs(Math.sin(i+1))*2**32)>>>0);
export function md5(bytes){
 const length=Math.ceil((bytes.length+9)/64)*64,padded=new Uint8Array(length);padded.set(bytes);padded[bytes.length]=0x80;
 const view=new DataView(padded.buffer);view.setBigUint64(length-8,BigInt(bytes.length)*8n,true);
 let h0=0x67452301,h1=0xefcdab89,h2=0x98badcfe,h3=0x10325476;
 for(let offset=0;offset<length;offset+=64){
  let a=h0,b=h1,c=h2,d=h3;
  for(let i=0;i<64;i++){
   const round=i>>4,word=round===0?i:round===1?(5*i+1)%16:round===2?(3*i+5)%16:(7*i)%16;
   const f=round===0?(b&c)|(~b&d):round===1?(d&b)|(~d&c):round===2?b^c^d:c^(b|~d);
   const x=(a+f+constants[i]+view.getUint32(offset+word*4,true))|0,shift=shifts[round*4+i%4],previous=d;
   d=c;c=b;b=(b+((x<<shift)|(x>>>(32-shift))))|0;a=previous;
  }
  h0=(h0+a)|0;h1=(h1+b)|0;h2=(h2+c)|0;h3=(h3+d)|0;
 }
 const digest=new Uint8Array(16),out=new DataView(digest.buffer);[h0,h1,h2,h3].forEach((n,i)=>out.setInt32(i*4,n,true));return digest;
}
function words(...values){const bytes=new Uint8Array(values.length*4),view=new DataView(bytes.buffer);values.forEach((v,i)=>view.setUint32(i*4,v,true));return bytes;}
function fail(code){throw new Error(code);}
export function installFlashReader(loader){
 loader.readFlash=async function(address,size,progress){
  if(!this.IS_STUB)fail('flash_stub_required');
  if(!Number.isInteger(address)||address<0||!Number.isInteger(size)||size<1||size>64*1024||address+size>32*1024*1024)fail('flash_read_range');
  // At most four 1KB packets in flight; keep USB bursts bounded.
  if(await this.checkCommand('read flash',this.ESP_READ_FLASH,words(address,size,1024,4))!==0)fail('flash_read_rejected');
  const data=new Uint8Array(size),timeout=this.FLASH_READ_TIMEOUT;
  for(let offset=0;offset<size;){
   const packet=await this.transport.read(timeout),expected=Math.min(1024,size-offset);
   if(!(packet instanceof Uint8Array)||packet.length!==expected){const error=new Error('flash_read_incomplete');error.readAddress=address+offset;error.expectedBytes=expected;error.receivedBytes=packet?.length??-1;throw error;}
   data.set(packet,offset);offset+=packet.length;
   await this.transport.write(words(offset));if(progress)progress(packet,offset,size);
  }
  // Consume the digest before any subsequent command can flush or misread it.
  const digest=await this.transport.read(timeout),computed=md5(data);
  if(!(digest instanceof Uint8Array)||digest.length!==16||!computed.every((v,i)=>v===digest[i]))fail('flash_read_checksum_failed');
  return data;
 };
 return loader;
}
export function retryableFlashRead(error){
 return ['flash_read_incomplete','flash_read_checksum_failed'].includes(error.message)||/No serial data|Serial data stream stopped|Invalid head of packet|Invalid SLIP escape/i.test(error.message||'');
}

// The vendored SLIP reader reallocates a growing Uint8Array for every byte.
// Reuse one scratch buffer instead, so long Flash reads do not churn gigabytes
// of temporary arrays and stall the USB receive loop.
export function installSlipReader(transport,ErrorType=Error){
 const scratch=new Uint8Array(65536);let reading=false;
 transport.read=async function(timeout=10000){
  if(reading)throw new ErrorType('Concurrent serial read');reading=true;
  let count=0,started=false,escaped=false;const deadline=Date.now()+timeout;
  try{
   for(;;){
    if(!this.buffer.length){
     if(Date.now()>=deadline)throw new ErrorType(started?'No serial data received.':'Serial data stream stopped: Possible serial noise or corruption.');
     await new Promise(resolve=>setTimeout(resolve,1));continue;
    }
    const bytes=this.buffer;this.buffer=new Uint8Array(0);
    for(let i=0;i<bytes.length;i++){
     let byte=bytes[i];
     if(!started){if(byte!==0xc0){this.detectPanicHandler?.(bytes);throw new ErrorType('Invalid head of packet');}started=true;continue;}
     if(escaped){escaped=false;if(byte===0xdc)byte=0xc0;else if(byte===0xdd)byte=0xdb;else throw new ErrorType('Invalid SLIP escape');}
     else if(byte===0xdb){escaped=true;continue;}
     else if(byte===0xc0){
      if(!count)continue;
      const remaining=bytes.subarray(i+1),queued=this.buffer,next=new Uint8Array(remaining.length+queued.length);next.set(remaining);next.set(queued,remaining.length);this.buffer=next;
      return scratch.slice(0,count);
     }
     if(count>=scratch.length)throw new ErrorType('Serial frame too large');scratch[count++]=byte;
    }
   }
  }finally{reading=false;}
 };
 return transport;
}
