"""Own only fixed VoiceHub workers; remote and user-launched HTTP services stay external."""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import time
import uuid
import aiohttp
import psutil

KINDS=('asr','llm','tts')
PORTS={'asr':8781,'llm':8782,'tts':8767}


class ServiceError(RuntimeError):
    def __init__(self,code):self.code=code;super().__init__(code)


class ServiceManager:
    def __init__(self,root,config,*,ports=None,worker_module='voicehub.model_worker'):
        self.root=Path(root)/'services';self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.config=config;self.ports=dict(ports or PORTS);self.module=worker_module
        self.project=Path(__file__).resolve().parents[1]
        self.locks={k:asyncio.Lock() for k in KINDS};self.children={};self.errors={}

    def _record(self,kind):
        try:return json.loads((self.root/(kind+'.json')).read_text())
        except (OSError,ValueError):return None

    def _save(self,kind,value):
        path=self.root/(kind+'.json');temporary=path.with_suffix('.tmp')
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f:json.dump(value,f)
        temporary.replace(path)

    def _owned(self,kind,record):
        if not record:return None
        try:
            process=psutil.Process(record['pid']);cmd=process.cmdline()
            if (not process.is_running() or process.status()==psutil.STATUS_ZOMBIE or
                abs(process.create_time()-record['created'])>.001 or process.uids().real!=os.getuid() or
                Path(process.cwd()).resolve()!=self.project):return None
            expected='voicehub.qwen_tts' if record.get('legacy') else self.module
            if '-m' not in cmd or cmd[cmd.index('-m')+1]!=expected:return None
            if not record.get('legacy'):
                if cmd[cmd.index('--service-id')+1]!=record['instance'] or cmd[cmd.index('--kind')+1]!=kind:return None
            return process
        except (psutil.Error,KeyError,ValueError,IndexError):return None

    def endpoint(self,kind):return f'http://127.0.0.1:{self.ports[kind]}/v1'

    def headers(self,kind):
        record=self._record(kind)
        if not self._owned(kind,record):raise ServiceError('service_not_running')
        if record['model'] != self.config()[kind]['model']:raise ServiceError('service_needs_restart')
        return {} if record.get('legacy') else {'Authorization':'Bearer '+record['token']}

    async def _health(self,kind,record):
        try:
            headers={} if record.get('legacy') else {'Authorization':'Bearer '+record['token']}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=.6)) as client:
                async with client.get(f'http://127.0.0.1:{record["port"]}/health',headers=headers) as response:
                    if response.status!=200:return None
                    value=await response.json()
                    if not record.get('legacy') and value.get('service_id')!=record['instance']:return None
                    return value
        except (aiohttp.ClientError,TimeoutError,ValueError):return None

    async def status(self,kind):
        if kind not in KINDS:raise ServiceError('invalid_service')
        cfg=self.config()[kind];provider=cfg['provider']
        if provider!='builtin':
            return dict(kind=kind,managed=False,provider=provider,model=cfg['model'],
                state={'http':'external','mlx':'embedded','macos':'system'}.get(provider,'external'))
        record=self._record(kind);process=self._owned(kind,record)
        if not process:
            if record and kind not in self.errors:
                self.errors[kind]=('failed','service_process_exited')
            return dict(kind=kind,managed=True,provider=provider,model=cfg['model'],state=self.errors.get(kind,('stopped',None))[0],
                error=self.errors.get(kind,(None,None))[1])
        health=await self._health(kind,record)
        if health and health.get('error'):state='failed'
        else:state='running' if health and health.get('ready') else 'starting'
        return dict(kind=kind,managed=True,provider=provider,model=record['model'],pid=record['pid'],
            state=state,active=health.get('active',0) if health else 0,
            configured_model=cfg['model'],needs_restart=record['model']!=cfg['model'],
            error=health.get('error') if health else None)

    async def all_status(self):return [await self.status(k) for k in KINDS]

    async def _port_used(self,kind):
        try:
            reader,writer=await asyncio.wait_for(asyncio.open_connection('127.0.0.1',self.ports[kind]),.4)
            writer.close();await writer.wait_closed();return True
        except (OSError,TimeoutError):return False

    async def _adopt_legacy_tts(self):
        if self.module!='voicehub.model_worker':return False
        path=self.root.parent/'qwen-tts.pid'
        try:
            pid=int(path.read_text());process=psutil.Process(pid)
            record=dict(pid=pid,created=process.create_time(),port=8767,model=self.config()['tts']['model'],legacy=True)
            if self.ports['tts']!=8767 or not self._owned('tts',record):return False
            health=await self._health('tts',record)
            if not health or health.get('model')!=record['model']:return False
            self._save('tts',record);return True
        except (OSError,ValueError,psutil.Error):return False

    async def _start(self,kind):
        record=self._record(kind)
        if self._owned(kind,record):return await self.status(kind)
        if kind=='tts' and await self._adopt_legacy_tts():return await self.status(kind)
        if await self._port_used(kind):
            self.errors[kind]=('blocked','service_port_in_use');raise ServiceError('service_port_in_use')
        self.errors.pop(kind,None)
        model=self.config()[kind]['model'];instance=uuid.uuid4().hex;token=secrets.token_urlsafe(32)
        env={key:value for key,value in os.environ.items() if key in
             ('PATH','HOME','LANG','LC_ALL','TMPDIR','HF_HOME','HUGGINGFACE_HUB_CACHE','SSL_CERT_FILE')}
        env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false',VOICEHUB_WORKER_TOKEN=token)
        arguments=[sys.executable,'-u','-m',self.module,'--kind',kind,'--model',model,
            '--port',str(self.ports[kind]),'--service-id',instance]
        with (self.root/(kind+'.log')).open('ab') as log:
            child=await asyncio.create_subprocess_exec(*arguments,cwd=self.project,env=env,
                stdin=asyncio.subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        self.children[kind]=child
        try:created=psutil.Process(child.pid).create_time()
        except psutil.Error:
            self.errors[kind]=('failed','service_start_failed');raise ServiceError('service_start_failed')
        record=dict(pid=child.pid,created=created,port=self.ports[kind],instance=instance,token=token,model=model)
        self._save(kind,record)
        return dict(kind=kind,managed=True,state='starting',model=model,pid=child.pid)

    async def _stop(self,kind):
        record=self._record(kind);process=self._owned(kind,record)
        if process:
            child=self.children.get(kind)
            with contextlib.suppress(psutil.NoSuchProcess):process.send_signal(signal.SIGTERM)
            deadline=time.monotonic()+6
            while self._owned(kind,record) and time.monotonic()<deadline:await asyncio.sleep(.05)
            if self._owned(kind,record):
                with contextlib.suppress(psutil.NoSuchProcess):process.kill()
            if child:
                with contextlib.suppress(TimeoutError):await asyncio.wait_for(child.wait(),2)
        self.children.pop(kind,None);self.errors.pop(kind,None)
        (self.root/(kind+'.json')).unlink(missing_ok=True)
        return dict(kind=kind,managed=True,state='stopped',model=self.config()[kind]['model'])

    async def action(self,kind,action):
        if kind not in KINDS or action not in ('start','stop','restart'):raise ServiceError('invalid_service_action')
        if self.config()[kind]['provider']!='builtin':raise ServiceError('external_service_unmanaged')
        async with self.locks[kind]:
            if action in ('stop','restart'):await self._stop(kind)
            if action in ('start','restart'):return await self._start(kind)
            return await self.status(kind)

    async def shutdown(self,stop_workers=False):
        if stop_workers:await asyncio.gather(*(self._stop(k) for k in KINDS))
        # Owned workers intentionally survive a console restart; identities are revalidated.
