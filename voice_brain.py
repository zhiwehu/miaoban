#!/usr/bin/env python3
"""
本地语音大脑 —— 全部跑在 MLX (Metal) 上
========================================

复用 ~/ai/mac_voice_agent (Miya 项目) 里已经下好的模型：

    ASR   mlx-community/Qwen3-ASR-0.6B-8bit        语音识别
    LLM   mlx-community/Qwen3-4B-Instruct-2507-4bit   对话
    TTS   mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit  语音合成
    VAD   mlx-community/silero-vad                  语音活动检测（可选）

模型在标准 HF 缓存里，按 repo 名自动解析到 snapshots 目录，全程离线。

依赖：mlx / mlx_audio / mlx_lm / numpy / av (PyAV)
必须用 Miya 项目的 venv 跑：
    ~/ai/mac_voice_agent/.venv/bin/python
"""

from __future__ import annotations

import glob
import os
import threading
import wave
from pathlib import Path
from typing import Any

import numpy as np
from dialogue_expression import EXPRESSION_PROMPT

HF_CACHE = os.path.expanduser("~/.cache/huggingface/hub")

ASR_MODEL = "mlx-community/Qwen3-ASR-0.6B-8bit"
# 实测：4B-Instruct-2507（非思考版）比 0.6B **又快又好**
#   0.6B : 0.4s，回复经常只有"喵～"
#   4B   : 0.23-0.34s，回复自然完整
LLM_MODEL = "mlx-community/Qwen3-4B-Instruct-2507-4bit"
# Base 模型没有预设音色（只能克隆），所以用 CustomVoice：
# 支持的音色 = serena / vivian / uncle_fu / ryan / aiden / ono_anna / sohee / eric / dylan
TTS_MODEL = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"
TTS_VOICE = "Vivian"

SYSTEM_PROMPT = (
    "你叫小宝，是一个温暖、开朗、有好奇心的陪伴伙伴，喜欢听人分享日常，也愿意帮人解决问题。"
    "用自然、简洁、口语化的中文直接回应，通常1到3句话；先回答问题，再按需要接话。"
    "像朋友聊天一样亲切，有分寸地幽默，安慰时认真倾听，不编造经历或感知。"
    "介绍自己时说名字和能做的事，不把芯片、开发板或电子元件当作身份。"
    "没有猫的角色设定，不模仿猫叫，不描写爪子、耳朵、尾巴或虚构的肢体动作。"
    "输出实际要说的话，不用动作旁白、舞台说明、思考过程、表情符号或markdown。"
    "只根据用户的话和真实设备信息回答；听不清或不知道就坦诚说明，不编造传感器故障或调试过程。"
)



def resolve_model(repo: str) -> str:
    """把 HF repo 名解析成本地 snapshot 目录（不联网）。"""
    if os.path.isdir(repo):
        return os.path.abspath(repo)
    slug = "models--" + repo.replace("/", "--")
    hits = sorted(glob.glob(os.path.join(HF_CACHE, slug, "snapshots", "*")))
    if not hits:
        raise FileNotFoundError(f"本地找不到模型 {repo}（缓存里没有）")
    return hits[-1]


def write_wav(path: str, pcm: np.ndarray, sample_rate: int) -> None:
    """float32 [-1,1] -> 16bit WAV。"""
    pcm = np.clip(np.asarray(pcm, dtype=np.float32), -1.0, 1.0)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes((pcm * 32767.0).astype("<i2").tobytes())


class SilentSpeechError(RuntimeError):
    pass


class VoiceBrain:
    """ASR / LLM / TTS 的懒加载封装。

    每个模型只在第一次用到时加载；加载和推理都放在工作线程里跑，
    避免阻塞 asyncio 事件循环（MLX 的 Metal 调用是同步的）。
    """

    def __init__(self, asr_model=ASR_MODEL, llm_model=LLM_MODEL, tts_model=TTS_MODEL, tts_engine="mlx"):
        self.asr_engine = os.environ.get("MIAOBAN_ASR_ENGINE", "mlx")
        if self.asr_engine not in ("mlx", "voicestudio"):
            raise ValueError("Unsupported ASR engine")
        self._voicestudio = None
        if self.asr_engine == "voicestudio" or tts_engine == "voicestudio":
            from voicestudio_client import VoiceStudioClient
            self._voicestudio = VoiceStudioClient(
                os.environ.get("VOICESTUDIO_URL", "http://127.0.0.1:3900"),
                os.environ.get("OMNIVOICE_API_KEY", ""))
        self.asr_model_id = asr_model
        self.llm_model_id = llm_model
        self.tts_model_id = tts_model
        if tts_engine not in ("mlx", "macos", "voicestudio"): raise ValueError("Unsupported TTS engine")
        self.tts_engine = tts_engine

        self._vad: Any = None
        self._asr: Any = None
        self._llm: Any = None
        self._tokenizer: Any = None
        self._tts: Any = None
        self._speech_process = None
        self._lock = threading.Lock()
        self._tts_inference_lock = threading.Lock()

        # ASR/TTS 推理比较重，各给一个单线程池
        import concurrent.futures
        self._asr_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="miaoban-asr")
        self._tts_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="miaoban-tts")
        self._llm_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="miaoban-llm")

    # ------------------------------------------------------------ 加载

    def load_asr(self) -> None:
        if self._asr is not None:
            return
        with self._lock:
            if self._asr is not None:
                return
            from mlx_audio.stt import load
            print(f"[brain] 加载 ASR: {self.asr_model_id}", flush=True)
            self._asr = load(resolve_model(self.asr_model_id))
            print("[brain] ASR 就绪", flush=True)

    def load_tts(self) -> None:
        if self._tts is not None:
            return
        with self._lock:
            if self._tts is not None:
                return
            from mlx_audio.tts import load
            print(f"[brain] 加载 TTS: {self.tts_model_id}", flush=True)
            self._tts = load(resolve_model(self.tts_model_id))
            print("[brain] TTS 就绪", flush=True)

    def load_llm(self) -> None:
        if self._llm is not None:
            return
        with self._lock:
            if self._llm is not None:
                return
            import mlx_lm
            print(f"[brain] 加载 LLM: {self.llm_model_id}", flush=True)
            self._llm, self._tokenizer = mlx_lm.load(resolve_model(self.llm_model_id))
            print("[brain] LLM 就绪", flush=True)

    def warmup(self) -> None:
        """预热三个模型（第一次加载会有 Metal 编译开销）。"""
        if self.asr_engine == "mlx": self.load_asr()
        if self.tts_engine == "mlx": self.load_tts()
        self.load_llm()
        if self.tts_engine == "macos":
            for _ in self.tts_stream("你好。"): pass

    # ------------------------------------------------------------ ASR

    def transcribe(self, pcm: np.ndarray, sample_rate: int = 16000) -> str:
        """PCM float32 -> 文本。"""
        pcm = np.asarray(pcm, dtype=np.float32)
        if pcm.size == 0:
            return ""
        if self.asr_engine == "voicestudio":
            return self._asr_pool.submit(
                self._voicestudio.transcribe, pcm, sample_rate).result()
        self.load_asr()

        def _run() -> str:
            if self._vad is None:
                from mlx_audio.vad import load
                self._vad = load(resolve_model("mlx-community/silero-vad"))
            speech = self._vad.get_speech_timestamps(
                pcm, sample_rate=sample_rate, min_speech_duration_ms=160)
            if not speech:
                return ""
            import tempfile
            with tempfile.TemporaryDirectory(prefix="miaoban-asr-") as d:
                path = os.path.join(d, "speech.wav")
                write_wav(path, pcm, sample_rate)
                result = self._asr.generate(path)
                return str(getattr(result, "text", result)).strip()

        return self._asr_pool.submit(_run).result()

    # ------------------------------------------------------------ LLM

    def chat(self, history: list[dict], max_tokens: int = 160, expressive: bool = False) -> str:
        """history: [{'role':'user'|'assistant','content':...}] -> 回复文本。

        一次性文本接口用于兼容调用；机器人对话使用 chat_stream 按句进入 TTS。
        max_tokens=160 只是防止模型跑飞，大概 100~150 个汉字。
        """
        self.load_llm()
        messages = [{"role": "system", "content": SYSTEM_PROMPT + (EXPRESSION_PROMPT if expressive else "")}] + list(history)

        def _run() -> str:
            import mlx_lm
            try:
                prompt = self._tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True,
                    enable_thinking=False)
            except TypeError:
                prompt = self._tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True)
            out = mlx_lm.generate(
                self._llm, self._tokenizer, prompt=prompt,
                max_tokens=max_tokens, verbose=False)
            text = str(out).strip()
            # Qwen3 默认会先吐 <think> 思考链，剥掉只留最终回答
            if "</think>" in text:
                text = text.split("</think>")[-1].strip()
            if text.startswith("<think>"):
                text = ""
            return text

        return self._llm_pool.submit(_run).result()

    def chat_expressive(self, history: list[dict]) -> str:
        return self.chat(history, expressive=True)

    def chat_stream(self, history, stop_event=None, max_tokens=160):
        """Yield generated text immediately, on the dedicated LLM worker."""
        def generate():
            self.load_llm()
            import mlx_lm
            messages = [{"role": "system", "content": SYSTEM_PROMPT + EXPRESSION_PROMPT}] + list(history)
            try:
                prompt = self._tokenizer.apply_chat_template(messages, tokenize=False,
                    add_generation_prompt=True, enable_thinking=False)
            except TypeError:
                prompt = self._tokenizer.apply_chat_template(messages, tokenize=False,
                    add_generation_prompt=True)
            for response in mlx_lm.stream_generate(self._llm, self._tokenizer,
                    prompt=prompt, max_tokens=max_tokens):
                yield response.text
        yield from self._worker_stream(self._llm_pool, generate, stop_event)

    @staticmethod
    def _worker_stream(pool, factory, stop_event=None):
        """Bounded handoff; closing a consumer closes its model on its own worker."""
        import queue
        output = queue.Queue(maxsize=4)
        closed = threading.Event()
        done = object()
        errors = []
        def stopped():
            return closed.is_set() or (stop_event is not None and stop_event.is_set())
        def put(value):
            while not stopped():
                try:
                    output.put(value, timeout=.05)
                    return
                except queue.Full:
                    pass
        def work():
            from contextlib import closing
            try:
                with closing(iter(factory())) as iterator:
                    for value in iterator:
                        if stopped(): break
                        put(value)
            except Exception as error:
                errors.append(error)
            finally:
                put(done)
        future = pool.submit(work)
        try:
            while not stopped():
                try: value = output.get(timeout=.05)
                except queue.Empty:
                    if future.done(): break
                    continue
                if value is done: break
                yield value
            if errors and not stopped(): raise errors[0]
        finally:
            closed.set()
            future.result()

    # ------------------------------------------------------------ TTS

    def tts_stream(self, text: str, voice: str = TTS_VOICE,
                   language: str = "Chinese", stop_event=None):
        # Streaming generators own model state until they finish or are cancelled.
        with self._tts_inference_lock:
            if stop_event is not None and stop_event.is_set():
                return
            if self.tts_engine == "voicestudio":
                generate = lambda text, voice, language: self._voicestudio.speech(
                    text, os.environ.get("VOICESTUDIO_VOICE", "alloy"), stop_event)
            else:
                generate = self._macos_tts_stream if self.tts_engine == "macos" else self._tts_stream_unlocked
            for chunk in self._worker_stream(self._tts_pool,
                    lambda: generate(text, voice, language), stop_event):
                if stop_event is not None and stop_event.is_set():
                    return
                yield chunk

    def _macos_tts_stream(self, text, voice, language):
        import subprocess
        import struct
        import select
        import time
        import json
        if not text.strip(): return
        process = self._speech_process
        if process is None or process.poll() is not None:
            helper = Path(__file__).with_name("native") / "stream_speech"
            if not helper.is_file():
                raise RuntimeError("Build native/stream_speech.swift before starting the backend")
            process = subprocess.Popen([str(helper)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self._speech_process = process
        complete = False
        deadline = time.monotonic()+30
        def read_exact(size):
            output = bytearray()
            while len(output)<size:
                if time.monotonic()>deadline: raise TimeoutError("Native speech buffer timeout")
                ready, _, _ = select.select([process.stdout], [], [], .1)
                if not ready: continue
                part = os.read(process.stdout.fileno(), size-len(output))
                if not part: raise RuntimeError("Native speech helper disconnected")
                output.extend(part)
            return bytes(output)
        try:
            process.stdin.write((json.dumps(text, ensure_ascii=False)+'\n').encode())
            process.stdin.flush()
            while True:
                rate, count = struct.unpack('<II', read_exact(8))
                if rate==0 and count==0:
                    complete = True
                    break
                if not 8000<=rate<=96000 or not 0<count<=96000:
                    raise RuntimeError("Invalid native speech buffer")
                yield np.frombuffer(read_exact(count*4), dtype='<f4').copy(), rate
        finally:
            if not complete:
                process.terminate()
                try: process.wait(timeout=2)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
                self._speech_process = None

    def _tts_stream_unlocked(self, text: str, voice: str = TTS_VOICE,
                   language: str = "Chinese"):
        """Use Miya's sampling profile; retry silent passes before any audio escapes."""
        import re
        text = re.sub(r"[~～]+", "，", text).strip()
        for attempt, temperature in enumerate((.5, .85, 1.0)):
            emitted = False
            try:
                for chunk in self._tts_attempt(text, voice, language, temperature):
                    emitted = True
                    yield chunk
                return
            except SilentSpeechError:
                if emitted: raise
                if attempt == 2:
                    print("[brain] TTS 连续静音，使用本机语音兜底", flush=True)
                    yield from self._macos_tts_stream(text, voice, language)
                    return
                print(f"[brain] TTS 静音重试 {attempt+1}/2", flush=True)

    def _tts_attempt(self, text, voice, language, temperature):
        """流式合成：**逐块** yield (pcm_float32, sample_rate)。

        这是降低端到端延迟的关键 —— 不用等整段生成完，第一块出来就能推给对方。
        同时保留复读循环保护（累计超过估时就停）。
        """
        if not text.strip():
            return
        self.load_tts()
        spoken = sum(1 for ch in text if ch.isalnum())
        # A bounded sentence must not drive the speaker for a minute on failed EOS.
        # Keep room for slow speech, but fail the turn instead of replaying a fallback
        # after audio has already escaped to the device.
        max_seconds = min(20.0, max(15.0, 3.0 + spoken * 0.45))
        rate = 24000
        generated = 0.0
        it = self._tts.generate(
            text=text,
            voice=voice,
            lang_code=language,
            stream=True,
            streaming_interval=0.8,
            max_tokens=int(max_seconds * 12.5) + 16,
            temperature=temperature,
            top_p=1.0,
            top_k=50,
            repetition_penalty=1.05,
        )
        heard_voice = False
        quiet_seconds = 0.0
        pending_quiet = []
        try:
            for item in it:
                audio = getattr(item, "audio", None)
                if audio is None: audio = getattr(item, "samples", None)
                if audio is None: continue
                arr = np.asarray(audio, dtype=np.float32).reshape(-1)
                rate = int(getattr(item, "sample_rate", None) or rate)
                seconds = len(arr)/rate
                generated += seconds
                if generated > max_seconds:
                    raise RuntimeError("TTS did not reach EOS within its audio budget")
                rms = float(np.sqrt(np.mean(arr*arr))) if arr.size else 0
                if rms < .003:
                    quiet_seconds += seconds
                    if heard_voice:
                        pending_quiet.append((arr, rate))
                        # A codec that never emits EOS must not keep the device
                        # speaking for 30 seconds after its last audible word.
                        if quiet_seconds >= 1.6: break
                    elif quiet_seconds >= 3.2:
                        raise SilentSpeechError("TTS generated only silence")
                    continue
                heard_voice = True
                quiet_seconds = 0.0
                yield from pending_quiet
                pending_quiet.clear()
                yield arr, rate
            if not heard_voice:
                raise SilentSpeechError("TTS generated only silence")
            # Preserve a short natural sentence boundary, not a silent codec loop.
            if pending_quiet:
                arr, r = pending_quiet[0]
                yield arr[:int(.12*r)], r
        finally:
            close = getattr(it, "close", None)
            if close: close()
            tokenizer = getattr(self._tts, "speech_tokenizer", None)
            reset = getattr(getattr(tokenizer, "decoder", None), "reset_streaming_state", None)
            if reset: reset()

    def synthesize(self, text: str, voice: str = TTS_VOICE,
                   language: str = "Chinese", stop_event=None) -> tuple[np.ndarray, int]:
        """一次性返回整段（非流式场景用；对话路径用 tts_stream）。"""
        chunks, rate = [], 24000
        for arr, r in self.tts_stream(text, voice, language, stop_event=stop_event):
            chunks.append(arr)
            rate = r
        if not chunks:
            return np.zeros(0, dtype=np.float32), rate
        return np.concatenate(chunks), rate
