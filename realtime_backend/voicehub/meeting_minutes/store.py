"""Persistent meeting-minute jobs and custom templates.

All state lives under the VoiceHub data root.  Job records contain the
generated minute, evidence quotes and status; audio/transcripts are not copied
into this store.
"""
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path

from .templates import validate_custom

RECORDING_ID = re.compile(r'[a-f0-9]{32}')
JOB_ID = re.compile(r'[a-f0-9]{32}')


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


class MinutesStore:
    def __init__(self, root):
        self.root = Path(root)/'meeting_minutes'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root/'jobs').mkdir(parents=True, exist_ok=True, mode=0o700)
        self.templates_file = self.root/'templates.json'

    def recover(self):
        """A job cannot survive a process restart; mark it interrupted."""
        for path in (self.root/'jobs').glob('*/*.json'):
            try:
                job = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if job.get('status') in ('queued', 'running'):
                job['status'] = 'interrupted'
                job['stage'] = 'interrupted'
                job['message'] = '服务重启，生成任务已中断；可以重新生成。'
                job['error'] = {'code': 'minutes_interrupted',
                                'message': '服务重启，生成任务已中断；可以重新生成。'}
                job['updated_at'] = time.time()
                _atomic_json(path, job)

    def recording_dir(self, recording_id):
        if not isinstance(recording_id, str) or not RECORDING_ID.fullmatch(recording_id):
            raise ValueError('recording_not_found')
        return self.root/'jobs'/recording_id

    def job_path(self, recording_id, job_id):
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            raise ValueError('minutes_not_found')
        return self.recording_dir(recording_id)/f'{job_id}.json'

    def create_job(self, recording_id, template, model_id, transcript_sha256,
                   transcript_lines):
        job = {
            'id': secrets.token_hex(16),
            'recording_id': recording_id,
            'template_id': template['template_id'],
            'template_version': template['version'],
            'template_name': template['name'],
            'status': 'queued',
            'stage': 'queued',
            'progress': 0,
            'message': '排队等待本地模型',
            'created_at': time.time(),
            'updated_at': time.time(),
            'started_at': None,
            'finished_at': None,
            'model_id': model_id,
            'transcript_sha256': transcript_sha256,
            'transcript_lines': transcript_lines,
            'result': None,
            'edited_markdown': None,
            'edited_at': None,
            'revision': 0,
            'error': None,
            'chunks': 0,
            'chunks_failed': 0,
        }
        _atomic_json(self.job_path(recording_id, job['id']), job)
        return job

    def get_job(self, recording_id, job_id):
        path = self.job_path(recording_id, job_id)
        if not path.is_file():
            return None
        try:
            job = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        return job if isinstance(job, dict) and job.get('recording_id') == recording_id else None

    def list_jobs(self, recording_id):
        directory = self.recording_dir(recording_id)
        jobs = []
        if not directory.is_dir():
            return jobs
        for path in directory.glob('*.json'):
            try:
                job = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(job, dict) and job.get('recording_id') == recording_id:
                jobs.append(job)
        return sorted(jobs, key=lambda job: job.get('created_at') or 0, reverse=True)

    def save_job(self, job):
        job['updated_at'] = time.time()
        _atomic_json(self.job_path(job['recording_id'], job['id']), job)

    def delete_job(self, recording_id, job_id):
        path = self.job_path(recording_id, job_id)
        if not path.is_file():
            raise ValueError('minutes_not_found')
        path.unlink()

    def delete_recording(self, recording_id):
        directory = self.recording_dir(recording_id)
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)

    def active_jobs(self):
        jobs = []
        for path in (self.root/'jobs').glob('*/*.json'):
            try:
                job = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if job.get('status') in ('queued', 'running'):
                jobs.append(job)
        return jobs

    def custom_templates(self):
        if not self.templates_file.is_file():
            return []
        try:
            data = json.loads(self.templates_file.read_text())
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def save_custom_templates(self, templates):
        _atomic_json(self.templates_file, templates)

    def create_template(self, data):
        template = validate_custom(data)
        templates = self.custom_templates()
        if any(item.get('template_id') == template['template_id'] for item in templates):
            raise ValueError('template_exists')
        templates.append(template)
        self.save_custom_templates(templates)
        return template

    def update_template(self, template_id, data):
        templates = self.custom_templates()
        for index, current in enumerate(templates):
            if current.get('template_id') != template_id:
                continue
            version = int(current.get('version', 1))+1
            updated = validate_custom(data, template_id=template_id, version=version)
            templates[index] = updated
            self.save_custom_templates(templates)
            return updated
        raise ValueError('template_not_found')

    def delete_template(self, template_id):
        templates = self.custom_templates()
        remaining = [item for item in templates if item.get('template_id') != template_id]
        if len(remaining) == len(templates):
            raise ValueError('template_not_found')
        self.save_custom_templates(remaining)
