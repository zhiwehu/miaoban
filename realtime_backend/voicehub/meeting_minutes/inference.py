"""Local-only Qwen3.5 inference for meeting minutes.

The model is loaded lazily in a dedicated single-thread executor.  Nothing here
falls back to a remote provider: if the configured model is not in the local
Hugging Face cache, the job fails with ``minutes_model_not_cached``.
"""
import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_MODEL = 'mlx-community/Qwen3.5-4B-MLX-4bit'
MODEL_ID = os.environ.get('VOICEHUB_MINUTES_MODEL', DEFAULT_MODEL).strip() or DEFAULT_MODEL


class MinutesError(RuntimeError):
    def __init__(self, code, message=None):
        self.code = code
        self.message = message or code
        super().__init__(code)


class MinutesEngine:
    def __init__(self, model_id=None):
        self.model_id = (model_id or MODEL_ID).strip()
        self._model = None
        self._tokenizer = None
        self._load_lock = threading.Lock()
        self._semaphore = asyncio.Semaphore(1)
        self._pool = ThreadPoolExecutor(max_workers=1,
                                        thread_name_prefix='voicehub-minutes')
        self._stopping = threading.Event()

    def resolve(self):
        path = Path(self.model_id).expanduser()
        if path.is_dir():
            if (path/'config.json').is_file():
                return str(path)
            raise MinutesError('minutes_model_not_cached',
                               '本地会议纪要模型目录不完整。')
        try:
            from huggingface_hub import snapshot_download
            return snapshot_download(
                self.model_id, local_files_only=True,
                allow_patterns=['*.json', '*.safetensors', '*.jinja',
                                '*.model', '*.tiktoken', '*.txt'])
        except Exception:
            raise MinutesError(
                'minutes_model_not_cached',
                f'本地未缓存会议纪要模型 {self.model_id}，请先下载后再生成。') from None

    async def check_available(self):
        await asyncio.to_thread(self.resolve)

    def _load(self):
        with self._load_lock:
            if self._model is not None:
                return self._model, self._tokenizer
            path = self.resolve()
            os.environ.setdefault('HF_HUB_OFFLINE', '1')
            os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
            try:
                import mlx_lm
                model, tokenizer = mlx_lm.load(path)
            except Exception:
                raise MinutesError(
                    'minutes_model_load_failed',
                    '本地会议纪要模型加载失败，请检查模型文件。') from None
            self._model, self._tokenizer = model, tokenizer
            return model, tokenizer

    def _generate_sync(self, messages, max_tokens, stop_event):
        model, tokenizer = self._load()
        try:
            prompt = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True,
                enable_thinking=False)
        except TypeError:
            prompt = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True)
        import mlx_lm
        output = []
        for result in mlx_lm.stream_generate(
                model, tokenizer, prompt=prompt, max_tokens=max_tokens):
            if stop_event.is_set() or self._stopping.is_set():
                break
            output.append(result.text)
            if sum(map(len, output)) > 20000:
                break
        return ''.join(output)

    async def generate(self, messages, *, max_tokens=1400, timeout=240):
        if not isinstance(messages, list) or not messages:
            raise MinutesError('minutes_invalid_request')
        if type(max_tokens) is not int or not 64 <= max_tokens <= 4096:
            raise MinutesError('minutes_invalid_request')
        stop_event = threading.Event()
        async with self._semaphore:
            try:
                future = asyncio.get_running_loop().run_in_executor(
                    self._pool, self._generate_sync, messages, max_tokens, stop_event)
                return await asyncio.wait_for(asyncio.shield(future), timeout)
            except TimeoutError:
                stop_event.set()
                raise MinutesError('minutes_timeout',
                                   '本地模型生成超时，请稍后重试。') from None
            except MinutesError:
                stop_event.set()
                raise
            except Exception:
                stop_event.set()
                raise MinutesError('minutes_generation_failed',
                                   '本地模型生成失败，请稍后重试。') from None

    def close(self):
        self._stopping.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
