"""Validated configuration snapshots; credentials are environment references only."""
import copy
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT = {
    'system_prompt': '你是小宝，一个温暖、自然的语音伙伴。用简短中文回答。只输出可直接朗读的正文，不使用emoji、颜文字、动作或表情旁白。',
    'asr': {'provider': 'builtin', 'model': 'mlx-community/Qwen3-ASR-0.6B-8bit',
            'url': 'http://127.0.0.1:8000/v1', 'key_env': 'VOICE_ASR_KEY', 'language': 'Chinese'},
    'llm': {'provider': 'builtin', 'model': 'mlx-community/Qwen3-4B-Instruct-2507-4bit',
            'url': 'http://127.0.0.1:1234/v1', 'key_env': 'VOICE_LLM_KEY', 'max_tokens': 240},
    'tts': {'provider': 'macos', 'model': 'tts-1', 'voice': 'Tingting',
            'url': 'http://127.0.0.1:8000/v1', 'key_env': 'VOICE_TTS_KEY', 'sample_rate': 24000},
    'wake': {'mode': 'device', 'phrase': '小宝同学', 'idle_seconds': 30},
    'audio': {'barge_in': True, 'vad_model': '', 'speech_threshold': .65,
              'onset_ms': 160, 'silence_ms': 640, 'max_utterance_seconds': 15},
    'retention_days': 30,
    'tools': {'weather': True, 'news': True, 'system_info': True,
              'default_city': '', 'news_source': 'bbc', 'news_limit': 3,
              'timeout_seconds': 20, 'planner': 'llm'},
}


def validate(config):
    if isinstance(config, dict) and 'tools' not in config:
        config = {**config, 'tools': copy.deepcopy(DEFAULT['tools'])}
    elif isinstance(config,dict) and isinstance(config.get('tools'),dict) and 'planner' not in config['tools']:
        config={**config,'tools':{**config['tools'],'planner':'llm'}}
    if not isinstance(config, dict) or set(config) != set(DEFAULT):
        raise ValueError('配置字段不完整或包含未知字段')
    if not isinstance(config['system_prompt'], str) or len(config['system_prompt']) > 8000:
        raise ValueError('system_prompt 最多 8000 字符')
    for section in ('asr', 'llm', 'tts', 'wake', 'audio'):
        if not isinstance(config[section], dict) or set(config[section]) != set(DEFAULT[section]):
            raise ValueError(f'{section} 配置字段不完整或包含未知字段')
    # Accept old files and clients, but only expose the canonical service provider.
    config = copy.deepcopy(config)
    for kind in ('asr', 'llm'):
        if config[kind]['provider'] == 'mlx':
            config[kind]['provider'] = 'builtin'
    for kind in ('asr', 'llm', 'tts'):
        c = config[kind]
        allowed = ('builtin', 'macos', 'http') if kind == 'tts' else ('builtin', 'http')
        if c['provider'] not in allowed:
            raise ValueError(f'{kind} 不支持该提供商')
        for key in ('url', 'key_env', 'model'):
            if not isinstance(c[key], str) or not c[key] or len(c[key]) > 2048:
                raise ValueError(f'{kind}.{key} 无效')
        url = urlsplit(c['url'])
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError(f'{kind}.url 必须是无凭证和查询参数的 HTTP 地址')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', c['key_env']):
            raise ValueError('密钥必须填写环境变量名称')
    if config['wake']['mode'] not in ('device', 'backend', 'manual'):
        raise ValueError('无效唤醒模式')
    if not isinstance(config['wake']['phrase'], str) or not 1 <= len(config['wake']['phrase']) <= 50:
        raise ValueError('唤醒词长度需为 1–50')
    if not isinstance(config['tts']['voice'], str) or not 1 <= len(config['tts']['voice']) <= 100:
        raise ValueError('音色不能为空')
    if config['asr']['language'] not in ('Chinese', 'English', 'auto'):
        raise ValueError('识别语言必须为 Chinese / English / auto')
    for section, key, low, high in [('llm','max_tokens',16,2048), ('tts','sample_rate',8000,48000),
            ('wake','idle_seconds',5,600), ('audio','onset_ms',96,1000),
            ('audio','silence_ms',320,2000), ('audio','max_utterance_seconds',2,30)]:
        value = config[section][key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{section}.{key} 需要为 {low}–{high} 的整数')
    if type(config['retention_days']) is not int or not 1 <= config['retention_days'] <= 365:
        raise ValueError('记录保留天数需为 1–365')
    a = config['audio']
    if type(a['barge_in']) is not bool or not isinstance(a['vad_model'], str):
        raise ValueError('音频配置无效')
    if type(a['speech_threshold']) not in (int, float) or not .3 <= a['speech_threshold'] <= .95:
        raise ValueError('人声阈值需为 0.3–0.95')
    t = config['tools']
    if not isinstance(t, dict) or set(t) != set(DEFAULT['tools']):
        raise ValueError('工具配置字段不完整或包含未知字段')
    if any(type(t[name]) is not bool for name in ('weather', 'news', 'system_info')):
        raise ValueError('工具开关需要为布尔值')
    if not isinstance(t['default_city'], str) or len(t['default_city']) > 80:
        raise ValueError('默认城市最多 80 字符')
    if t['news_source'] not in ('chinanews', 'bbc'):
        raise ValueError('无效新闻源')
    if t['planner'] not in ('llm','rex'):
        raise ValueError('无效工具规划器')
    for key, low, high in [('news_limit', 1, 5), ('timeout_seconds', 5, 30)]:
        if type(t[key]) is not int or not low <= t[key] <= high:
            raise ValueError(f'tools.{key} 需要为 {low}–{high} 的整数')
    return config


class ConfigFile:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            original = json.loads(self.path.read_text())
            self.value = validate(original)
            if self.value != original:
                self.save(self.value)
        else:
            self.save(copy.deepcopy(DEFAULT))

    def get(self):
        return copy.deepcopy(self.value)

    def save(self, value):
        value = validate(value)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix='.settings-')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(value, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        self.value = copy.deepcopy(value)
