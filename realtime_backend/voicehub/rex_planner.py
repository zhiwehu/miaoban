"""Bounded, cancellable requests to one persistent local Rex worker."""
import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path


class PlannerError(RuntimeError):pass


def build_request(serial,users,defaults,previous=None):
    """Build one classifier request; context only rides along for follow-ups."""
    from .extension_routing import is_followup
    latest=users[-1]
    return {'id':serial,'text':latest,
            'previous_user':users[-2][-80:] if len(users)>1 and is_followup(latest) else '',
            'defaults':{k:defaults[k] for k in ('default_city','news_source','news_limit')},
            'previous':previous}


class RexPlanner:
    def __init__(self):
        self.process=None;self.lock=asyncio.Lock();self.serial=0

    async def _stop(self):
        if self.process:
            if self.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):self.process.terminate()
                try:await asyncio.wait_for(self.process.wait(),2)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):self.process.kill()
                    await self.process.wait()
            self.process=None

    async def _start(self):
        if self.process and self.process.returncode is None:return
        env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','LANG','LC_ALL','TMPDIR')}
        env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
        self.process=await asyncio.create_subprocess_exec(sys.executable,'-m','voicehub.rex_planner_worker',
            cwd=Path(__file__).resolve().parents[1],env=env,stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,limit=32768)
        raw=await asyncio.wait_for(self.process.stdout.readline(),20)
        if not raw or json.loads(raw).get('type')!='ready':raise PlannerError('planner_unavailable')

    async def plan(self,history,defaults,previous=None):
        users=[m['content'] for m in history if m.get('role')=='user' and isinstance(m.get('content'),str)]
        if not users:raise PlannerError('planner_input_required')
        async with asyncio.timeout(25):
            async with self.lock:
                try:
                    await self._start()
                    self.serial+=1
                    request=build_request(self.serial,users,defaults,previous)
                    raw=json.dumps(request,ensure_ascii=False).encode()+b'\n'
                    if len(raw)>16384:raise PlannerError('planner_input_limit')
                    self.process.stdin.write(raw);await self.process.stdin.drain()
                    raw=await asyncio.wait_for(self.process.stdout.readline(),5)
                    if not raw:raise PlannerError('planner_disconnected')
                    response=json.loads(raw)
                    if response.get('id')!=self.serial or not isinstance(response.get('result'),dict):
                        raise PlannerError('planner_invalid_response')
                    return response['result']
                except asyncio.CancelledError:
                    await self._stop();raise
                except (OSError,ValueError,TimeoutError,PlannerError) as exc:
                    await self._stop();raise PlannerError('planner_unavailable') from exc

    async def close(self):
        async with self.lock:await self._stop()
