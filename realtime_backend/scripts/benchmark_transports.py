#!/usr/bin/env python3
"""Software-client comparison. Measures transport RTT / text-to-audio, not physical playback."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import aiohttp
import aiomqtt
from voicehub.audio import AudioCodec
from voicehub.mqtt_udp import UdpAudio
from voicehub.mqtt_service import load_config

HELLO={'type':'hello','version':3,'transport':'udp','features':{'playback_ready':True},
    'audio_params':{'format':'opus','sample_rate':16000,'channels':1,'frame_duration':60}}


class DatagramReceiver(asyncio.DatagramProtocol):
    def __init__(self,events,audio):self.events,self.audio=events,audio
    def datagram_received(self,data,addr):
        try:pcm,_=self.audio.decode(data)
        except ValueError:return
        if not self.events.full():self.events.put_nowait(('audio',pcm,time.perf_counter()))


class Link:
    def __init__(self,send,events,hello_ms):
        self.send,self.events,self.hello_ms=send,events,hello_ms

    async def next(self):
        return await asyncio.wait_for(self.events.get(),90)

    async def ping(self):
        started=time.perf_counter()
        await self.send({'type':'ping'})
        while True:
            kind,event,arrived=await self.next()
            if kind=='json' and event['type']=='pong':return (arrived-started)*1000

    async def reply(self,prompt):
        started=time.perf_counter();first=None;packets=0
        await self.send({'type':'input.text','text':prompt})
        while True:
            kind,event,arrived=await self.next()
            if kind=='audio':
                packets+=1
                if first is None:first=(arrived-started)*1000
            elif event['type']=='error':raise RuntimeError(event.get('code','protocol_error'))
            elif event['type']=='tts':
                if event['state']=='start':
                    await self.send({'type':'tts','state':'ready','turn_id':event['turn_id'],
                        'device_state':6,'output_enabled':True,'rx_audio_packets':0,'dropped_audio_packets':0,
                        'decoded_audio_packets':0,'played_audio_packets':0})
                elif event['state']=='stop':
                    if first is None:raise RuntimeError('No audio received')
                    return dict(text_to_first_audio_ms=first,completion_ms=(arrived-started)*1000,packets=packets)


@asynccontextmanager
async def websocket(url,token):
    events=asyncio.Queue(maxsize=256)
    async with aiohttp.ClientSession() as http:
        started=time.perf_counter()
        async with http.ws_connect(url,headers={'Authorization':'Bearer '+token}) as ws:
            await ws.send_json(dict(HELLO,device_id='benchmark-ws'))
            hello=await ws.receive_json()
            if hello.get('type')!='hello':raise RuntimeError('WebSocket hello failed')
            hello_ms=(time.perf_counter()-started)*1000
            async def reader():
                async for message in ws:
                    if message.type==aiohttp.WSMsgType.TEXT:
                        await events.put(('json',message.json(),time.perf_counter()))
                    elif message.type==aiohttp.WSMsgType.BINARY:
                        await events.put(('audio',message.data,time.perf_counter()))
            task=asyncio.create_task(reader())
            try:yield Link(ws.send_json,events,hello_ms)
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True)


@asynccontextmanager
async def mqtt(config,device):
    item=config['devices'][device];events=asyncio.Queue(maxsize=256)
    async with aiomqtt.Client(config['broker_host'],config['broker_port'],username=item['username'],
        password=item['password'],identifier='benchmark_'+item['username'],timeout=5) as client:
        await client.subscribe('voicehub/'+device+'/down')
        async def send(value):await client.publish('voicehub/'+device+'/up',json.dumps(value),qos=0)
        started=time.perf_counter();await send(HELLO)
        async with asyncio.timeout(5):
            while True:
                hello=json.loads((await anext(client.messages)).payload)
                if hello.get('type')=='hello':break
                if hello.get('type')=='error':raise RuntimeError('MQTT hello rejected')
        hello_ms=(time.perf_counter()-started)*1000
        if hello.get('type')!='hello':raise RuntimeError('MQTT hello failed')
        audio=UdpAudio(bytes.fromhex(hello['udp']['key']),int.from_bytes(bytes.fromhex(hello['udp']['nonce'])[4:8],'big'),server=False)
        udp,_=await asyncio.get_running_loop().create_datagram_endpoint(lambda:DatagramReceiver(events,audio),
            local_addr=('0.0.0.0',0),remote_addr=(hello['udp']['server'],hello['udp']['port']))
        # Prime the UDP source address while the session is still in standby.
        opus=AudioCodec('opus').encode(b'\0\0'*960)[0]
        udp.sendto(audio.encode(opus))
        async def reader():
            async for message in client.messages:
                await events.put(('json',json.loads(message.payload),time.perf_counter()))
        task=asyncio.create_task(reader())
        try:yield Link(send,events,hello_ms)
        finally:
            await send({'type':'goodbye','session_id':hello['session_id']})
            task.cancel();await asyncio.gather(task,return_exceptions=True);udp.close()


def summary(values):
    values=sorted(values)
    return dict(p50_ms=round(statistics.median(values),2),p95_ms=round(values[min(len(values)-1,int(len(values)*.95))],2),samples=len(values))


async def run(args):
    config=load_config(args.mqtt_config)
    token=json.loads(args.auth_file.read_text())['device_token']
    result={'scope':'software_clients_on_same_mac; text input excludes ASR and physical playback',
        'ws_hello_includes_tcp':True,'mqtt_hello_uses_existing_broker_connection':True,'rounds':args.rounds,
        'websocket':{'hello_ms':[],'ping_ms':[],'replies':[]},'mqtt_udp':{'hello_ms':[],'ping_ms':[],'replies':[]}}
    # Warm each path once; alternate order to reduce model warm-up/order bias.
    for i in range(args.rounds+1):
        order=['websocket','mqtt_udp'] if i%2==0 else ['mqtt_udp','websocket']
        for kind in order:
            context=websocket(args.ws_url,token) if kind=='websocket' else mqtt(config,args.device)
            async with context as link:
                pings=[]
                for _ in range(10):
                    async with asyncio.timeout(3):pings.append(await link.ping())
                    await asyncio.sleep(.03)  # Stay below the shared per-device control limit.
                reply=await link.reply(args.prompt) if args.prompt else None
                if i:
                    result[kind]['hello_ms'].append(link.hello_ms)
                    result[kind]['ping_ms'].extend(pings)
                    if reply:result[kind]['replies'].append(reply)
    for kind in ['websocket','mqtt_udp']:
        record=result[kind]
        record['hello_summary']=summary(record['hello_ms']);record['ping_summary']=summary(record['ping_ms'])
        if record['replies']:record['first_audio_summary']=summary([r['text_to_first_audio_ms'] for r in record['replies']])
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({kind:{k:v for k,v in result[kind].items() if k.endswith('_summary')} for kind in ['websocket','mqtt_udp']},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mqtt-config',type=Path,required=True)
    parser.add_argument('--auth-file',type=Path,default=ROOT/'data/auth.json')
    parser.add_argument('--device',default='benchmark')
    parser.add_argument('--ws-url',default='ws://127.0.0.1:8765/xiaozhi/v1/')
    parser.add_argument('--rounds',type=int,default=5)
    parser.add_argument('--prompt',default='',help='Optional fixed text; empty measures only transport')
    parser.add_argument('--out',type=Path,default=ROOT/'data/transport-benchmark.json')
    args=parser.parse_args()
    if not 1<=args.rounds<=30:parser.error('rounds must be 1..30')
    asyncio.run(run(args))
