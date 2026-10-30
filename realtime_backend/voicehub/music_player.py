"""Bounded local library and an owned afplay process, shared across MCP invocations."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile

import psutil

EXTENSIONS={'.mp3','.wav','.m4a','.aac','.aiff','.aif','.flac'}


class MusicPlayer:
    def __init__(self,root,directory):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.directory=directory
        self.state=self.root/'music-player.json'

    def library(self):
        if not self.directory:raise ValueError('music_directory_required')
        base=Path(self.directory).expanduser().resolve()
        if not base.is_dir():raise ValueError('music_directory_unavailable')
        tracks=[];visited=0;truncated=False;seen=set()
        for folder,dirs,files in os.walk(base,followlinks=False):
            dirs[:]=sorted(d for d in dirs if not d.startswith('.'))
            visited+=1
            for name in sorted(files):
                visited+=1
                if visited>10000 or len(tracks)>=5000:truncated=True;break
                try:path=(Path(folder)/name).resolve()
                except (OSError,RuntimeError):continue
                if (name.startswith('.') or path.suffix.lower() not in EXTENSIONS or
                    not path.is_relative_to(base) or not path.is_file() or path in seen):continue
                seen.add(path)
                relative=str(path.relative_to(base))
                tracks.append({'track_id':hashlib.sha256(relative.encode()).hexdigest()[:20],
                               'title':path.stem,'_path':str(path)})
            if truncated:break
        return tracks,truncated

    def _owned(self):
        try:
            record=json.loads(self.state.read_text())
            process=psutil.Process(record['pid'])
            if (abs(process.create_time()-record['created'])>.01 or
                process.cmdline()!=['/usr/bin/afplay',record['path']] or
                process.status()==psutil.STATUS_ZOMBIE):return None,None
            return process,record
        except (OSError,ValueError,KeyError,psutil.Error):return None,None

    def _save(self,record):
        fd,name=tempfile.mkstemp(dir=self.root,prefix='.music-')
        try:
            with os.fdopen(fd,'w') as file:json.dump(record,file)
            os.replace(name,self.state)
        finally:
            if os.path.exists(name):os.unlink(name)

    def execute(self,action,query='',track_id=''):
        fd=os.open(self.root/'music-player.lock',os.O_CREAT|os.O_RDWR,0o600)
        with os.fdopen(fd,'w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            try:return self._execute(action,query,track_id)
            except psutil.NoSuchProcess:
                self.state.unlink(missing_ok=True)
                return {'status':'stopped','message':'播放已结束。'}

    def _execute(self,action,query,track_id):
        process,record=self._owned()
        if action in ('pause','resume','stop','status'):
            if not process:return {'status':'stopped','message':'当前没有正在播放的本地音乐。'}
            if action=='stop':
                # A paused process must be continued before SIGTERM can complete.
                process.send_signal(signal.SIGCONT);process.terminate()
                try:process.wait(timeout=2)
                except psutil.TimeoutExpired:
                    if process.status()!=psutil.STATUS_ZOMBIE:process.kill()
                self.state.unlink(missing_ok=True)
                return {'status':'stopped'}
            if action in ('pause','resume'):
                process.send_signal(signal.SIGSTOP if action=='pause' else signal.SIGCONT)
                record['paused']=action=='pause';self._save(record)
            return {'status':'paused' if record.get('paused') else 'playing','title':record['title'],
                    'output':'后端电脑默认音频设备'}
        tracks,truncated=self.library()
        matches=[t for t in tracks if (not query or query.casefold() in t['title'].casefold())
                 and (not track_id or track_id==t['track_id'])]
        public=lambda t:{k:v for k,v in t.items() if not k.startswith('_')}
        if action=='search':return {'tracks':[public(t) for t in matches[:20]],'matches':len(matches),'truncated':truncated}
        if not matches:return {'status':'not_found','message':'音乐目录中没有找到匹配的歌曲。'}
        if len(matches)>1 and (query or track_id):
            return {'status':'needs_selection','message':'找到多首歌曲，请补充歌名或选择 track_id。',
                    'tracks':[public(t) for t in matches[:10]]}
        selected=matches[0]
        if not Path('/usr/bin/afplay').is_file():raise ValueError('music_player_unavailable')
        if process:
            process.send_signal(signal.SIGCONT);process.terminate()
            try:process.wait(timeout=2)
            except psutil.TimeoutExpired:
                if process.status()!=psutil.STATUS_ZOMBIE:raise ValueError('music_player_busy')
        child=subprocess.Popen(['/usr/bin/afplay',selected['_path']],stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        try:
            created=psutil.Process(child.pid).create_time()
            self._save({'pid':child.pid,'created':created,'path':selected['_path'],
                        'title':selected['title'],'paused':False})
        except (OSError,psutil.NoSuchProcess):
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=1)
                except subprocess.TimeoutExpired:child.kill();child.wait()
            raise ValueError('music_playback_failed') from None
        try:code=child.wait(timeout=.1)
        except subprocess.TimeoutExpired:code=None
        if code is not None:
            self.state.unlink(missing_ok=True)
            if code:raise ValueError('music_playback_failed')
            return {'status':'stopped','title':selected['title'],'message':'短音频已播放结束。'}
        return {'status':'playing',**public(selected),'output':'后端电脑默认音频设备',
                'message':'已启动本地播放器，播放状态不代表已测量扬声器声音。'}
