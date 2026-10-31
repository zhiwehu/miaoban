"""Meeting-minute job orchestration: snapshot, extract, merge, persist."""
import asyncio
import hashlib
import re
import time

from .extractor import extract_all
from .inference import MinutesEngine, MinutesError
from .render import build_result, plain_text
from .store import MinutesStore
from .templates import (builtin_templates, clean_template_id, find_builtin)

MAX_QUEUED = 4
_CONTROL = re.compile(r'^(开始录音模式|开始录音|进入录音模式|停止录音模式|确认停止)[。！!，,、\s]*$')


class MinutesService:
    def __init__(self, root, recordings, engine=None, publish=None):
        self.recordings = recordings
        self.store = MinutesStore(root)
        self.engine = engine or MinutesEngine()
        self.publish = publish
        self.tasks = {}
        self.cancel_events = {}
        self.deleted_jobs = set()
        self.started = False

    async def start(self):
        if not self.started:
            self.store.recover()
            self.started = True

    def _templates(self):
        return builtin_templates() + self.store.custom_templates()

    def templates(self):
        return self._templates()

    def template(self, template_id):
        template_id = clean_template_id(template_id)
        for template in self._templates():
            if template.get('template_id') == template_id:
                return template
        raise MinutesError('minutes_template_not_found', '找不到这个纪要模板。')

    def create_template(self, data):
        return self.store.create_template(data)

    def update_template(self, template_id, data):
        if find_builtin(template_id):
            raise MinutesError('minutes_builtin_template', '内置模板不能修改。')
        return self.store.update_template(template_id, data)

    def delete_template(self, template_id):
        if find_builtin(template_id):
            raise MinutesError('minutes_builtin_template', '内置模板不能删除。')
        self.store.delete_template(template_id)

    def _job(self, recording_id, job_id):
        job = self.store.get_job(recording_id, job_id)
        if job is None:
            raise MinutesError('minutes_not_found', '找不到这份会议纪要。')
        return job

    async def start_job(self, recording_id, template_id):
        await self.start()
        recording = self.recordings.get(recording_id)
        if recording is None:
            raise MinutesError('recording_not_found', '找不到这条录音。')
        if recording.get('status') == 'active':
            raise MinutesError('minutes_recording_active', '录音还没有结束，停止并保存后再生成纪要。')
        transcript = self.recordings.transcript(recording_id)
        if (transcript is None or not transcript.get('available')
                or not transcript.get('text') or not transcript.get('lines')):
            raise MinutesError('minutes_transcript_empty',
                               '这条录音还没有可用的转写，无法生成会议纪要。')
        template = self.template(template_id)
        for job in self.store.list_jobs(recording_id):
            if job.get('status') in ('queued', 'running'):
                raise MinutesError('minutes_job_active', '这条录音已经有一个纪要任务在生成。')
        if len(self.store.active_jobs()) >= MAX_QUEUED:
            raise MinutesError('minutes_queue_full', '纪要生成队列已满，请稍后再试。')
        if hasattr(self.engine, 'check_available'):
            await self.engine.check_available()
        lines = [line for line in transcript['lines']
                 if not _CONTROL.match((line.get('text') or '').strip())]
        if not lines:
            raise MinutesError('minutes_transcript_empty',
                               '这条录音只有控制指令，没有可总结的会议内容。')
        text = '\n'.join(line.get('text') or '' for line in lines)
        job = self.store.create_job(
            recording_id, template, self.engine.model_id,
            hashlib.sha256(text.encode('utf-8')).hexdigest(), len(lines))
        self.cancel_events[job['id']] = asyncio.Event()
        self.tasks[job['id']] = asyncio.create_task(self._run(job, lines))
        return job

    async def _run(self, job, lines):
        job_id = job['id']
        cancel = self.cancel_events.get(job_id) or asyncio.Event()
        try:
            if job_id in self.deleted_jobs:
                return
            job['status'] = 'running'
            job['stage'] = 'snapshot'
            job['started_at'] = time.time()
            job['progress'] = 2
            job['message'] = '已锁定最终转写，准备分段提取'
            self.store.save_job(job)

            async def progress(stage, percent, message):
                if cancel.is_set() or job_id in self.deleted_jobs:
                    raise MinutesError('minutes_cancelled', '生成任务已取消。')
                job['stage'] = stage
                job['progress'] = min(99, max(0, int(percent)))
                job['message'] = message
                self.store.save_job(job)

            template = self.template(job['template_id'])
            merged = await extract_all(self.engine, lines, template, progress, cancel)
            if cancel.is_set() or job_id in self.deleted_jobs:
                raise MinutesError('minutes_cancelled', '生成任务已取消。')
            await progress('rendering', 90, '正在生成 Markdown 和证据索引')
            recording = self.recordings.get(job['recording_id']) or {}
            result = build_result(merged, template, recording, self.engine.model_id,
                                  job['transcript_sha256'])
            job['chunks'] = (result.get('coverage') or {}).get('chunks', 0)
            job['chunks_failed'] = (result.get('coverage') or {}).get('chunks_failed', 0)
            job['result'] = result
            job['status'] = 'completed'
            job['stage'] = 'completed'
            job['progress'] = 100
            job['message'] = '会议纪要已生成'
            job['finished_at'] = time.time()
            job['error'] = None
            if job_id not in self.deleted_jobs:
                self.store.save_job(job)
        except MinutesError as exc:
            if job_id in self.deleted_jobs:
                return
            job['error'] = {'code': exc.code, 'message': exc.message}
            job['status'] = 'cancelled' if exc.code == 'minutes_cancelled' else 'failed'
            job['stage'] = job['status']
            job['message'] = exc.message
            job['finished_at'] = time.time()
            self.store.save_job(job)
        except asyncio.CancelledError:
            if job_id not in self.deleted_jobs:
                job['status'] = 'interrupted'
                job['stage'] = 'interrupted'
                job['message'] = '生成任务被服务中断。'
                job['error'] = {'code': 'minutes_interrupted',
                                'message': job['message']}
                job['finished_at'] = time.time()
                self.store.save_job(job)
            raise
        except Exception:
            if job_id not in self.deleted_jobs:
                job['status'] = 'failed'
                job['stage'] = 'failed'
                job['message'] = '生成失败，请稍后重试。'
                job['error'] = {'code': 'minutes_failed',
                                'message': '生成失败，请稍后重试。'}
                job['finished_at'] = time.time()
                self.store.save_job(job)
        finally:
            self.tasks.pop(job_id, None)
            self.cancel_events.pop(job_id, None)

    def list_jobs(self, recording_id):
        return self.store.list_jobs(recording_id)

    def get_job(self, recording_id, job_id):
        return self._job(recording_id, job_id)

    async def cancel_job(self, recording_id, job_id):
        job = self._job(recording_id, job_id)
        event = self.cancel_events.get(job_id)
        if event:
            event.set()
        if job.get('status') in ('queued', 'running'):
            job['status'] = 'cancelled'
            job['stage'] = 'cancelled'
            job['message'] = '生成任务已取消。'
            job['error'] = {'code': 'minutes_cancelled', 'message': '生成任务已取消。'}
            job['finished_at'] = time.time()
            self.store.save_job(job)
        return job

    async def delete_job(self, recording_id, job_id):
        job = self._job(recording_id, job_id)
        self.deleted_jobs.add(job_id)
        event = self.cancel_events.get(job_id)
        if event:
            event.set()
        self.store.delete_job(recording_id, job_id)

    def update_markdown(self, recording_id, job_id, markdown):
        job = self._job(recording_id, job_id)
        if job.get('status') != 'completed':
            raise MinutesError('minutes_not_completed', '纪要还没有生成完成，不能编辑。')
        if not isinstance(markdown, str) or len(markdown) > 200000:
            raise MinutesError('minutes_invalid_markdown', '纪要内容无效或过长。')
        job['edited_markdown'] = markdown
        job['edited_at'] = time.time()
        job['revision'] = int(job.get('revision') or 0)+1
        self.store.save_job(job)
        return job

    def export(self, recording_id, job_id, fmt):
        job = self._job(recording_id, job_id)
        if job.get('status') != 'completed' or not job.get('result'):
            raise MinutesError('minutes_not_completed', '纪要还没有生成完成，不能导出。')
        markdown = job.get('edited_markdown') or job['result'].get('markdown') or ''
        content = markdown if fmt == 'md' else plain_text(markdown)
        return content, fmt

    async def delete_recording(self, recording_id):
        jobs = self.store.list_jobs(recording_id)
        for job in jobs:
            self.deleted_jobs.add(job['id'])
            event = self.cancel_events.get(job['id'])
            if event:
                event.set()
            task = self.tasks.get(job['id'])
            if task:
                task.cancel()
        tasks = [task for job in jobs if (task := self.tasks.get(job['id']))]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.store.delete_recording(recording_id)

    async def close(self):
        for event in self.cancel_events.values():
            event.set()
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.cancel_events.clear()
        if hasattr(self.engine, 'close'):
            self.engine.close()
