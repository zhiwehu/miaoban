#!/usr/bin/env python3
"""
喵伴 (xiaozhi-esp32) 本地后端 —— 把猫头接到自己身上
=====================================================

原理
----
猫头每次开机会向 `ota_url` 拉一次"版本 + 协议配置"，并把返回的 `mqtt` / `websocket`
配置写进 NVS。`ota_url` 本身存在 NVS 的 **wifi** 命名空间里（键名 `ota_url`）。

所以只要：
  1. 把 NVS 里的 `wifi/ota_url` 指向本机（经公网隧道暴露）
  2. 本机在那个地址上冒充一次版本检查
  3. 返回里只给 `websocket`（不给 `mqtt`），设备就会用 WebSocket 连我

就能拿到猫头的完全控制权：MCP 工具调用、让它说话、查状态。

本程序提供三个端点：
  * HTTP  /xiaozhi/ota/     -> 冒充小智服务器的版本检查
  * WS    /xiaozhi/v1/      -> 猫头连接上来的通道（承载 MCP）
  * HTTP  :8098/api/...     -> 本地控制 API（发 MCP 调用、看状态）

用法
----
    python3 xiaozhi_server.py --public-host gguvy-xxx.free.pinggy.net
    python3 xiaozhi_server.py --public-host ... --ota-path /xiaozhi/ota/

控制 API:
    curl localhost:8098/api/status
    curl localhost:8098/api/tools
    curl -X POST localhost:8098/api/call -d '{"tool":"self.audio_speaker.set_volume","arguments":{"volume":30}}'
    curl -X POST localhost:8098/api/say -d '{"text":"喵伴，我在呢"}'
"""

import argparse
import secrets
import concurrent.futures
import asyncio
import json
import ipaddress
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from aiohttp import web, WSMsgType
from dialogue_expression import parse_reply

try:
    import numpy as np
except ImportError:      # 没装 numpy 也不影响 MCP 控制路径
    np = None

# ------------------------------------------------------------------ 状态

STATE = {
    "public_host": "",
    "ota_path": "/xiaozhi/ota/",
    "ws_path": "/xiaozhi/v1/",
    "token": "miaoban-local-token",
    "device": None,          # {"ip":..., "hello":{...}, "connected_at":...}
    "last_mcp_response": None,
    "log": [],               # 设备发来的消息（最近 50 条）
    "audio_frames": 0,
    "mic": [],               # 麦克风音频帧 (bytes, 环形缓冲)
    "tts_cancel": False,
    "history": [],           # 对话历史
    "voice_enabled": True,
    "fw_version": "2.0.5",
    "last_user_text": "",
    "last_reply": "",
}
PENDING = {}                # id -> Future
WS = None                   # 当前设备连接

# --- 本地语音大脑（ASR/LLM/TTS，跑在 MLX 上）------------------------------
BRAIN = None
DECODER = None
VAD_TURN = None             # 切分用户说话轮次
VAD_BARGE = None            # 播报期间检测插话
TTS_TASK = None


class VoiceTurn:
    def __init__(self, ws):
        self.ws = ws
        self.cancelled = threading.Event()
        self.task = None
        self.producer = None

    def current(self):
        return not self.cancelled.is_set() and self.ws is WS and self.ws is not None

ACTIVE_TURN = None
VOICE_LOCK = None

def cancel_turn():
    turn = ACTIVE_TURN
    if turn is not None and not turn.cancelled.is_set():
        turn.cancelled.set()
        if turn.task is not None and not turn.task.done():
            turn.task.cancel()
    STATE["tts_cancel"] = True

def start_turn(factory):
    global ACTIVE_TURN, VOICE_LOCK
    cancel_turn()
    if VOICE_LOCK is None:
        VOICE_LOCK = asyncio.Lock()
    turn = VoiceTurn(WS)
    ACTIVE_TURN = turn
    STATE["tts_cancel"] = False
    async def run():
        global ACTIVE_TURN, TTS_TASK
        try:
            async with VOICE_LOCK:
                if turn.current():
                    return await factory(turn)
        finally:
            turn.cancelled.set()
            if turn.producer is not None:
                await run_model(turn.producer.join)
            if ACTIVE_TURN is turn:
                ACTIVE_TURN = None
            if TTS_TASK is turn.task:
                TTS_TASK = None
    turn.task = asyncio.create_task(run())
    return turn.task

async def run_model(function, *args):
    # Keep the turn lock until a cancelled model call actually leaves its worker.
    future = asyncio.get_running_loop().run_in_executor(None, function, *args)
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(future)
        except Exception:
            pass
        raise


def brain():
    """懒加载本地语音大脑。"""
    global BRAIN
    if BRAIN is None:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from voice_brain import VoiceBrain
        BRAIN = VoiceBrain(tts_engine=os.environ.get("MIAOBAN_TTS_ENGINE", "mlx"))
    return BRAIN


def logline(msg):
    ts = time.strftime("%H:%M:%S")
    STATE["log"].append(f"[{ts}] {msg}")
    del STATE["log"][:-80]
    print(f"[{ts}] {msg}", flush=True)


# ------------------------------------------------------------------ OTA 响应

def ws_url() -> str:
    """猫头应该连的 WebSocket 地址。

    * 公网隧道模式：隧道把 443 转发到 8099，所以是 wss://<域名>/path（无端口）
    * 局域网模式：直连，必须带端口，且用明文 ws://
    """
    if STATE.get("tunnel_mode"):
        return f"wss://{STATE['public_host']}{STATE['ws_path']}"
    # 局域网模式：每次动态取本机 IP，换了网络（比如热点重连）也能自动跟上
    host = STATE.get("advertise_host") or lan_ip()
    return f"ws://{host}:{STATE.get('port', 8099)}{STATE['ws_path']}"


def ota_response():
    """冒充小智服务器的版本检查响应。

    故意 **不返回 mqtt 段**：设备端 `ota_->HasMqttConfig()` 为 false 时
    才会选 WebsocketProtocol（见 xiaozhi-esp32 main/application.cc:551）。
    firmware.version 保持当前版本，避免设备以为自己该升级。
    """
    url = ws_url()
    host = urlparse(url).hostname
    if host == 'localhost' or not host:
        raise web.HTTPServiceUnavailable(text='LAN address is not ready')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_loopback or address.is_unspecified):
        # A temporary network outage must not overwrite the robot's saved endpoint.
        raise web.HTTPServiceUnavailable(text='LAN address is not ready')
    return {
        "firmware": {
            # 必须和猫头当前固件版本一致，否则它会以为自己该升级
            # （太白 fork 刷上去后是 2.0.5）
            "version": STATE.get("fw_version", "2.0.5"),
            "url": "",
        },
        "websocket": {
            "url": url,
            "token": STATE["token"],
        },
    }


# ------------------------------------------------------------------ 设备连接

async def device_handler(request):
    """猫头连上来的 WebSocket 通道。"""
    global WS, DECODER, VAD_TURN, VAD_BARGE
    if STATE.get("tunnel_mode") and not secrets.compare_digest(
            request.headers.get("Authorization", ""), "Bearer " + STATE["token"]):
        raise web.HTTPUnauthorized()
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    cancel_turn()
    WS = ws
    DECODER = VAD_TURN = VAD_BARGE = None
    peer = request.remote
    logline(f"设备已连接: {peer} path={request.path}")

    try:
        try:
            raw = await asyncio.wait_for(ws.receive(), timeout=15)
        except Exception as e:
            logline(f"等待设备 hello 超时: {e}")
            return ws

        try:
            hello = json.loads(raw.data)
        except Exception:
            hello = {"raw": str(raw.data)[:400]}
        logline(f"设备 hello: {json.dumps(hello, ensure_ascii=False)[:300]}")

        if WS is not ws:
            return ws
        STATE["device"] = {
            "peer": str(peer),
            "hello": hello,
            "connected_at": time.time(),
        }

        # 服务端 hello：必须带 transport=websocket，否则设备直接报错退出
        server_hello = {
            "type": "hello",
            "transport": "websocket",
            "session_id": "miaoban-local-1",
            "audio_params": {
                "format": "opus",
                "sample_rate": 16000,
                "channels": 1,
                "frame_duration": 60,
            },
        }
        await ws.send_str(json.dumps(server_hello))
        logline("已回复服务端 hello")

        async for msg in ws:
            if WS is not ws:
                break
            if msg.type != WSMsgType.TEXT:
                if msg.type == WSMsgType.BINARY:
                    STATE["audio_frames"] += 1
                    # 存一份麦克风音频（环形缓冲），可用于 ASR / 验证 AEC
                    STATE["mic"].append(msg.data)
                    if len(STATE["mic"]) > 1200:      # 约 60 秒
                        del STATE["mic"][:300]
                    await _on_mic_audio(msg.data)
                continue
            try:
                m = json.loads(msg.data)
            except Exception:
                continue
            t = m.get("type")
            if t == "mcp":
                payload = m.get("payload", {})
                mid = payload.get("id")
                logline(f"<- MCP 响应 id={mid}: "
                        f"{json.dumps(payload, ensure_ascii=False)[:400]}")
                STATE["last_mcp_response"] = payload
                fut = PENDING.get(mid)
                if fut and not fut.done():
                    fut.set_result(payload)
            elif t == "listen" and m.get("state") == "detect":
                logline(f"<- wake detected: {m.get('text', '')}")
                if ACTIVE_TURN is None and not STATE.get("pending_reply"):
                    STATE["pending_reply"] = {"text": "我在，请说。", "emotion": "happy"}
            elif t == "listen" and m.get("state") == "start":
                logline(f"<- listen start (mode={m.get('mode')})")
                # 设备真正进入聆听状态了 -> 如果排了自动回复，现在就发
                if STATE.get("pending_reply"):
                    item = STATE.pop("pending_reply")
                    start_turn(lambda turn: send_say(item["text"], item.get("user_text"),
                                                 item.get("emotion", "happy"), turn=turn))
            elif t == "abort":
                # 设备打断了我（唤醒词命中 / 用户插话）-> 必须立刻停止推音频
                reason = m.get("reason", "?")
                logline(f"<- abort (reason={reason}) -> 打断当前播报")
                cancel_turn()
                try:
                    await ws.send_str(json.dumps({"type": "tts", "state": "stop"}))
                except Exception:
                    pass
            else:
                logline(f"<- {t}: {json.dumps(m, ensure_ascii=False)[:200]}")
    finally:
        if WS is ws:
            cancel_turn()
            WS = None
            STATE["device"] = None
            DECODER = VAD_TURN = VAD_BARGE = None
            logline("设备断开")
    return ws


# ------------------------------------------------------------------ HTTP (OTA)

async def ota_handler(request):
    """冒充小智服务器的版本检查。设备用 POST 带 system info 过来。"""
    if STATE.get("tunnel_mode"):
        path = STATE["ota_path"]
        secret = path.rstrip("/").rsplit("/", 1)[-1]
        if len(secret) < 32 or not secrets.compare_digest(request.path, path):
            raise web.HTTPNotFound()
    try:
        body = await request.text()
    except Exception:
        body = ""
    logline(f"HTTP {request.method} <ota> "
            f"(body {len(body)}B) -> ota 响应")
    return web.json_response(ota_response())


# ------------------------------------------------------------------ TTS

def parse_ogg_opus(data):
    """从 Ogg 容器里抽出裸 Opus 包（跳过 OpusHead / OpusTags）。"""
    packets = []
    pos = 0
    while pos + 27 <= len(data):
        if data[pos:pos + 4] != b"OggS":
            break
        nseg = data[pos + 26]
        seg_table = data[pos + 27:pos + 27 + nseg]
        body_start = pos + 27 + nseg
        body = data[body_start:body_start + sum(seg_table)]
        off = 0
        cur = b""
        for slen in seg_table:
            cur += body[off:off + slen]
            off += slen
            if slen < 255:          # 255 表示包还要继续
                packets.append(cur)
                cur = b""
        if cur:
            packets.append(cur)
        pos = body_start + sum(seg_table)
    # 去掉 OpusHead / OpusTags
    out = []
    for p in packets:
        if p.startswith(b"OpusHead") or p.startswith(b"OpusTags"):
            continue
        out.append(p)
    return out


def tts_opus_packets(text, voice=None):
    """文字 -> 16kHz 单声道 60ms 一帧的裸 Opus 包。"""
    import tempfile
    if voice is None:
        voice = STATE.get("tts_voice") or pick_voice()
    with tempfile.TemporaryDirectory() as td:
        aiff = os.path.join(td, "t.aiff")
        ogg = os.path.join(td, "t.opus")
        r = subprocess.run(["say", "-v", voice, "-o", aiff, text],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"say 失败: {r.stderr}")
        r = subprocess.run(["ffmpeg", "-y", "-i", aiff, "-ar", "16000", "-ac", "1",
                            "-c:a", "libopus", "-b:a", "24k",
                            "-frame_duration", "60", "-f", "ogg", ogg],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg 失败: {r.stderr[-400:]}")
        data = open(ogg, "rb").read()
    pkts = parse_ogg_opus(data)
    if len(pkts) < 3:
        raise RuntimeError(f"TTS 音频异常（只有 {len(pkts)} 帧），音色可能读不了中文")
    return pkts


def pick_voice():
    """挑一个**真的能读中文**的 macOS 音色。

    注意：Eddy / Flo / Rocko 这类是「 novelty」音色，会被 zh_CN 标上但其实
    读不了中文（输出只有 20ms）。Tingting 才是标准中文女声。
    """
    pref = ["Tingting", "Sandy", "Shelley", "Reed", "Grandma", "Grandpa"]
    avail = set()
    try:
        r = subprocess.run(["say", "-v", "?"], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            if "zh_CN" in line:
                avail.add(line.split(" ")[0])
    except Exception:
        pass
    for name in pref:
        if name in avail:
            return name
    return "Tingting"


async def send_say(text, user_text=None, emotion="happy", pace=0.06, tail=1.2, turn=None):
    """让喵伴开口说话（走小智的 tts 协议）。

    顺序: stt -> llm -> tts(start) -> tts(sentence_start) -> 二进制 Opus 帧 -> tts(stop)

    ⚠️ 关键：必须按 **实时速度** 推帧。
       设备端 audio_decode_queue_ 上限是 MAX_DECODE_PACKETS_IN_QUEUE =
       1200/60 = 20 帧，队列满时 PushPacketToDecodeQueue(wait=false) 会
       **直接丢包**。一次性灌 60+ 帧的话，后面的音频全被丢掉 —— 表现为
       「猫头一声不吭」或只发出极短的一下。
    """
    if turn is None:
        return await start_turn(lambda owned: send_say(text, user_text, emotion, pace, tail, turn=owned))
    if not turn.current():
        return {"ok": False, "cancelled": True}
    ws = turn.ws
    if WS is None:
        raise RuntimeError("设备当前未连接")
    if user_text:
        await ws.send_str(json.dumps({"type": "stt", "text": user_text}))
    await ws.send_str(json.dumps({"type": "llm", "emotion": emotion}))
    await ws.send_str(json.dumps({"type": "tts", "state": "start"}))
    await ws.send_str(json.dumps({"type": "tts", "state": "sentence_start",
                                  "text": text}))
    if STATE.get("voice_enabled"):
        # 优先用本地 Qwen3-TTS（MLX），比 macOS say 自然得多
        from voice_loop import pcm_to_opus_packets, resample
        b = brain()
        pcm, rate = await run_model(lambda: b.synthesize(text, stop_event=turn.cancelled))
        pcm16 = resample(pcm, rate, 16000)
        pkts = await run_model(pcm_to_opus_packets, pcm16, 16000)
    else:
        pkts = await run_model(tts_opus_packets, text)
    logline(f"推送 TTS: {text!r} ({len(pkts)} 帧 x 60ms, 实时速率)")
    global TTS_TASK
    TTS_TASK = asyncio.current_task()
    sent = 0
    for p in pkts:
        if not turn.current():
            logline(f"TTS 被打断，已停在第 {sent}/{len(pkts)} 帧")
            break
        await ws.send_bytes(p)
        sent += 1
        await asyncio.sleep(pace)
    # 等设备把队列里的音频放完再收尾，否则 stop 会把尾巴冲掉
    if turn.current():
        await asyncio.sleep(tail)
        if turn.current():
            await ws.send_str(json.dumps({"type": "tts", "state": "stop"}))
    if TTS_TASK is asyncio.current_task():
        TTS_TASK = None
    return {"ok": True, "frames": sent, "total": len(pkts), "text": text}


# ------------------------------------------------------------------ 语音回路

CJK_PUNCT = "，。？！,.?!、；;：:~～…· \t\n"

WAKE_WORDS = ["小宝同学", "小保同学", "小包同学", "你好喵伴", "你好喵爸", "你好幺爸", "你好妙伴", "喵伴", "你好喵", "喵喵"]

# 唤醒词及常见误识会出现的字。短句里出现这些字就认为是唤醒词，不当问题处理。
# ⚠️ 不能放「你」「好」这种常见字，否则「你讲个故事吗」会被误杀。
# ⚠️ 也不能用「长度≤5」一刀切，「天怎么样」只有 4 个字但它是真问题。
WAKE_CHARS = set("喵伴爸幺妙苗秒办板半猫")
WAKE_EXACT = {"小宝同学", "小保同学", "小包同学", "你好", "您好", "你好喵", "喵", "喵喵", "嗨"}


def is_wake_word_only(text: str) -> bool:
    """判断这一轮是不是只听到了唤醒词。

    Qwen3-ASR 对「你好喵伴」的常见误识：你好喵爸 / 你好幺爸 / 好秒办 / 你好妙伴 …
    这些都不是用户在问问题，不该触发回答。
    """
    s = "".join(ch for ch in text if ch not in CJK_PUNCT)
    if not s:
        return True
    if s in WAKE_EXACT:
        return True
    if len(s) <= 6 and any(ch in WAKE_CHARS for ch in s):
        return True
    return False


def strip_wake_word(text: str) -> str:
    """把开头的唤醒词去掉，只留正题（用户可能连着说：「你好喵伴今天天气怎么样」）。"""
    s = text
    for w in sorted(WAKE_WORDS, key=len, reverse=True):
        idx = s.find(w)
        if idx == 0:
            s = s[len(w):]
            break
        if 0 < idx <= 2:
            s = s[idx + len(w):]
            break
    return s.lstrip(CJK_PUNCT).strip()


async def _on_mic_audio(packet: bytes):
    """设备发来的麦克风音频（裸 Opus 包）-> 解码 -> VAD -> 切出轮次。"""
    global DECODER, VAD_TURN, VAD_BARGE
    if not STATE.get("voice_enabled"):
        return
    try:
        if DECODER is None:
            from voice_loop import OpusDecoder
            DECODER = OpusDecoder()
        if VAD_TURN is None:
            from voice_loop import SpeechVAD, EnergyVAD
            VAD_TURN = await run_model(SpeechVAD)
            VAD_BARGE = EnergyVAD()
        pcm = DECODER.decode(packet)
        if len(pcm) == 0:
            return
        # 诊断：麦克风电平（标定 VAD 阈值用）
        _mic_frames = STATE.get("_mic_frames", 0) + 1
        STATE["_mic_frames"] = _mic_frames
        if _mic_frames % 25 == 0:
            rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))
            STATE["mic_rms"] = rms
        # Energy alone cannot distinguish user speech from speaker echo.
        # Use half-duplex audio until a verified echo-aware detector is available.
        # Explicit device abort messages remain handled by the protocol loop.
        if ((ACTIVE_TURN is not None and ACTIVE_TURN.current()) or
                (TTS_TASK is not None and not TTS_TASK.done())):
            VAD_BARGE.reset()
            VAD_TURN.reset()
            return
        segment = await run_model(VAD_TURN.feed, pcm)
        if segment is not None and len(segment) >= 6400:  # VAD has confirmed speech; retain short replies.
            start_turn(lambda turn: handle_turn(segment, turn))
    except Exception as e:
        logline(f"麦克风处理异常: {type(e).__name__}: {e}")


async def handle_turn(segment, turn=None):
    """一轮用户说话结束：ASR -> LLM -> TTS -> 推给设备。"""
    global TTS_TASK
    if turn is None:
        return await start_turn(lambda owned: handle_turn(segment, owned))
    if not turn.current():
        return
    ws = turn.ws
    b = brain()
    loop = asyncio.get_running_loop()
    t0 = time.monotonic()
    try:
        logline(f"ASR 中… ({len(segment)/16000:.1f}s 音频)")
        text = await run_model(b.transcribe, segment, 16000)
        if not turn.current():
            return
        t_asr = time.monotonic() - t0
        text = (text or "").strip()
        logline(f"ASR: {text!r}  ({t_asr:.2f}s)")
        if not text:
            return
        if is_wake_word_only(text):
            # 只听到唤醒词（用户还在组织下一句）—— 不回答，继续等
            logline("ASR 检出唤醒词，回应后继续聆听")
            return await send_say("我在，请说。", turn=turn)
        text = strip_wake_word(text) or text
        STATE["last_user_text"] = text
        if turn.current():
            await ws.send_str(json.dumps({"type": "stt", "text": text}))

        # 触发一次唤醒（让猫头进入说话态）
        if turn.current():
            await ws.send_str(json.dumps({"type": "llm", "emotion": "thinking"}))

        history = STATE["history"] + [{"role": "user", "content": text}]
        # LLM and TTS are independent workers. A completed first sentence enters
        # TTS while the LLM continues; both use bounded queues and one turn token.
        import queue
        from streaming_reply import SpeechSentences
        sentences = queue.Queue(maxsize=4)
        pkt_q = asyncio.Queue(maxsize=24)
        DONE = object()
        generated_reply = []
        result = {"emotion": "neutral"}
        errors = []

        def enqueue_sentence(value):
            while not turn.cancelled.is_set():
                try:
                    sentences.put(value, timeout=.05)
                    return
                except queue.Full:
                    pass

        def generate_text():
            parser = SpeechSentences()
            previous_emotion = "neutral"
            def publish_emotion():
                nonlocal previous_emotion
                result["emotion"] = parser.emotion
                if parser.emotion != previous_emotion:
                    emit(("emotion", parser.emotion))
                    previous_emotion = parser.emotion
            try:
                if hasattr(b, "chat_stream"):
                    source = b.chat_stream(history, stop_event=turn.cancelled)
                else:
                    source = iter([getattr(b, "chat_expressive", b.chat)(history)])
                try:
                    for token in source:
                        if turn.cancelled.is_set(): break
                        ready = parser.feed(token)
                        publish_emotion()
                        for sentence in ready:
                            result["emotion"] = parser.emotion
                            generated_reply.append(sentence)
                            enqueue_sentence(sentence)
                    if not turn.cancelled.is_set():
                        result["emotion"] = parser.emotion
                        ready = parser.finish()
                        publish_emotion()
                        for sentence in ready:
                            generated_reply.append(sentence)
                            enqueue_sentence(sentence)
                        result["emotion"] = parser.emotion
                        if not generated_reply:
                            fallback = "我刚才没想好，可以再说一次吗？"
                            generated_reply.append(fallback)
                            result["emotion"] = "confused"
                            enqueue_sentence(fallback)
                finally:
                    close = getattr(source, "close", None)
                    if close: close()
                result["llm_seconds"] = time.monotonic() - t0 - t_asr
            except Exception as exc:
                errors.append(exc)
            finally:
                enqueue_sentence(DONE)

        def emit(value):
            future = asyncio.run_coroutine_threadsafe(pkt_q.put(value), loop)
            while not turn.cancelled.is_set():
                try:
                    future.result(timeout=.05)
                    return
                except concurrent.futures.TimeoutError:
                    pass
            future.cancel()

        def produce_audio():
            from voice_loop import OpusEncoder, StreamResampler
            enc = OpusEncoder()
            rsp = StreamResampler()
            llm = threading.Thread(target=generate_text, name="reply-text", daemon=True)
            llm.start()
            try:
                while not turn.cancelled.is_set():
                    try: sentence = sentences.get(timeout=.05)
                    except queue.Empty: continue
                    if sentence is DONE: break
                    emit(("sentence", sentence, result["emotion"]))
                    stream = b.tts_stream(sentence, stop_event=turn.cancelled)
                    try:
                        for chunk, rate in stream:
                            if turn.cancelled.is_set(): break
                            for packet in enc.encode(rsp.push(chunk, rate)):
                                emit(packet)
                    finally:
                        stream.close()
                if errors: raise errors[0]
                if not turn.cancelled.is_set():
                    for packet in enc.flush(): emit(packet)
            except Exception as exc:
                errors.append(exc)
                emit(("error", str(exc)))
                turn.cancelled.set()
            finally:
                # Wake the LLM's bounded queue even if audio failed or was aborted.
                llm.join()
                if not turn.cancelled.is_set(): emit(DONE)

        turn.producer = threading.Thread(target=produce_audio, name="reply-audio", daemon=True)
        turn.producer.start()

        sent = 0
        first_audio_at = None
        next_t = None
        pending_sentence = None
        while True:
            pkt = await pkt_q.get()
            if pkt is DONE or not turn.current():
                break
            if isinstance(pkt, tuple):
                if pkt[0] == "emotion":
                    STATE["last_emotion"] = pkt[1]
                    await ws.send_str(json.dumps({"type": "llm", "emotion": pkt[1]}))
                    logline(f"-> 表情指令: {pkt[1]}")
                    continue
                if pkt[0] == "error":
                    raise RuntimeError(pkt[1])
                if pkt[0] == "sentence":
                    if sent == 0:
                        pending_sentence = pkt
                    else:
                        await ws.send_str(json.dumps({"type": "tts", "state": "sentence_start", "text": pkt[1]}))
                    continue
            now = time.monotonic()
            if next_t is None:
                next_t = now
                first_audio_at = now
                TTS_TASK = asyncio.current_task()
                emotion = result["emotion"]
                STATE["last_emotion"] = emotion
                logline(f"-> 播报表情: {emotion}")
                await ws.send_str(json.dumps({"type": "llm", "emotion": emotion}))
                await ws.send_str(json.dumps({"type": "tts", "state": "start"}))
                if pending_sentence:
                    await ws.send_str(json.dumps({"type": "tts", "state": "sentence_start", "text": pending_sentence[1]}))
                logline(f"⏱ 首包音频 {now-t0:.2f}s (ASR {t_asr:.2f}s; LLM/TTS 并行流式)")
            else:
                delay = next_t - now
                if delay > 0:
                    await asyncio.sleep(delay)
                elif -delay > 0.4:
                    next_t = now        # 落后太多就重新对齐，不要补发突发
            if not turn.current():
                break
            if sent >= 500:  # Absolute 30s limit for one streamed reply.
                raise RuntimeError("Reply exceeded 30s playback limit; stopping audio")
            try:
                await ws.send_bytes(pkt)
            except Exception:
                logline("发送中断（设备已断开）")
                break
            sent += 1
            next_t += 0.06              # 60ms 一帧，严格绝对时间

        if errors:
            raise errors[0]
        if turn.current():
            reply = ''.join(generated_reply)
            STATE["last_reply"] = reply
            STATE["history"].extend([{"role": "user", "content": text},
                                      {"role": "assistant", "content": reply}])
            del STATE["history"][:-8]
            logline(f"LLM 完整回复: {reply!r} ({result.get('llm_seconds', 0):.2f}s)")
        played = sent * 0.06
        wall = time.monotonic() - (first_audio_at or time.monotonic())
        # wall ≈ played 说明推送节奏跟上了实时，不会欠载
        logline(f"TTS 完成：{sent} 帧 / {played:.1f}s 音频 / 实际推送 {wall:.1f}s"
                f" (节奏{'正常' if wall <= played * 1.15 else '偏慢!'})"
                + ("（被打断）" if STATE.get("tts_cancel") else ""))
        if turn.current():
            await asyncio.sleep(0.8)
            if turn.current():
                await ws.send_str(json.dumps({"type": "tts", "state": "stop"}))
        if TTS_TASK is asyncio.current_task():
            TTS_TASK = None
    except Exception as e:
        logline(f"语音回路异常: {type(e).__name__}: {e}")
        if ws is WS:
            try: await ws.send_str(json.dumps({"type": "tts", "state": "stop"}))
            except Exception: pass
        import traceback
        traceback.print_exc()


# ------------------------------------------------------------------ 控制 API

async def send_mcp(method, params, timeout=10):
    """在设备连接上发一条 MCP JSON-RPC，并等回响应。"""
    if WS is None:
        raise RuntimeError("设备当前未连接")
    mid = int(time.time() * 1000) % 100000000
    fut = asyncio.get_running_loop().create_future()
    PENDING[mid] = fut
    msg = {"type": "mcp", "payload": {
        "jsonrpc": "2.0", "method": method, "params": params, "id": mid}}
    await WS.send_str(json.dumps(msg))
    logline(f"-> MCP {method} id={mid}: {json.dumps(params, ensure_ascii=False)[:200]}")
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    finally:
        PENDING.pop(mid, None)


class ControlHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, indent=1).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/status":
            self._json({
                "public_host": STATE["public_host"],
                "ota_url": (f"{'https' if STATE.get('tunnel_mode') else 'http'}://"
                            f"{urlparse(ws_url()).netloc}{STATE['ota_path']}"),
                "device_connected": STATE["device"] is not None,
                "device": STATE["device"],
                "audio_frames": STATE["audio_frames"],
                "mic_rms": STATE.get("mic_rms"),
                "voice_enabled": STATE["voice_enabled"],
                "last_user_text": STATE["last_user_text"],
                "last_reply": STATE["last_reply"],
                "history": STATE["history"][-4:],
                "log": STATE["log"][-25:],
            })
        elif u.path == "/api/tools":
            try:
                r = asyncio.run_coroutine_threadsafe(
                    send_mcp("tools/list", {"cursor": ""}), LOOP).result(12)
                self._json(r)
            except Exception as e:
                self._json({"error": str(e)}, 500)
        elif u.path == "/api/mic.wav":
            # 把猫头传来的原始麦克风音频导出成 WAV（调试 ASR / VAD 用）
            try:
                from voice_loop import OpusDecoder
                dec = OpusDecoder()
                frames = list(STATE["mic"])
                pcm = (np.concatenate([dec.decode(p) for p in frames])
                       if frames and np is not None else np.zeros(0, np.float32))
                import io
                import wave
                buf = io.BytesIO()
                with wave.open(buf, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(16000)
                    w.writeframes((np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes())
                body = buf.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self._json({"error": str(e)}, 500)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        try:
            if u.path == "/api/call":
                r = asyncio.run_coroutine_threadsafe(
                    send_mcp("tools/call",
                             {"name": body["tool"], "arguments": body.get("arguments", {})}),
                    LOOP).result(15)
                self._json(r)
            elif u.path == "/api/say":
                text = body.get("text", "")
                if body.get("auto"):
                    # 等设备真正进入聆听状态再发（协议上更稳）
                    STATE["pending_reply"] = {
                        "text": text,
                        "user_text": body.get("user_text"),
                        "emotion": body.get("emotion", "happy"),
                    }
                    self._json({"ok": True, "queued": True, "text": text})
                else:
                    r = asyncio.run_coroutine_threadsafe(
                        send_say(text, body.get("user_text"),
                                 body.get("emotion", "happy")), LOOP).result(60)
                    self._json(r)
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)


# ------------------------------------------------------------------ main

LOOP = None


def lan_ip() -> str:
    """本机在局域网里的 IP（猫头直接访问这个地址）。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))     # 不会真的发包，只用来选路由
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


async def main():
    global LOOP
    LOOP = asyncio.get_running_loop()
    ap = argparse.ArgumentParser()
    ap.add_argument("--public-host", default=None,
                    help="猫头访问本机用的地址；留空则自动用本机局域网 IP（同一 WiFi 场景）")
    ap.add_argument("--tunnel", action="store_true",
                    help="公网 HTTPS 隧道模式（需同时指定 --public-host 域名）")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--bind", default="0.0.0.0",
                    help="监听地址；猫头在同一局域网必须绑 0.0.0.0")
    ap.add_argument("--api-port", type=int, default=8098)
    ap.add_argument("--ota-path", default="/xiaozhi/ota/")
    ap.add_argument("--ws-path", default="/xiaozhi/v1/")
    ap.add_argument("--token", default=os.environ.get("MIAOBAN_TOKEN") or secrets.token_urlsafe(32))
    ap.add_argument("--no-voice", action="store_true",
                    help="关掉本地 ASR/LLM/TTS 语音回路（只做 MCP 控制）")
    args = ap.parse_args()

    if args.tunnel and not args.public_host:
        ap.error("--tunnel 需要 --public-host 指定隧道域名")
    if args.tunnel and len(args.ota_path.rstrip("/").rsplit("/", 1)[-1]) < 32:
        ap.error("公网模式需要 --ota-path 指定含至少 32 字符随机密钥的私有路径")
    STATE["advertise_host"] = args.public_host
    STATE["public_host"] = args.public_host or lan_ip()
    STATE["ota_path"] = args.ota_path
    STATE["ws_path"] = args.ws_path
    STATE["token"] = args.token
    STATE["voice_enabled"] = not args.no_voice
    STATE["tunnel_mode"] = args.tunnel
    STATE["port"] = args.port

    # 控制 API 线程
    srv = ThreadingHTTPServer(("127.0.0.1", args.api_port), ControlHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    logline(f"控制 API  http://127.0.0.1:{args.api_port}/api/status")

    # 后台预热三个模型：不预热的话第一轮会多等 2 秒
    if STATE["voice_enabled"]:
        async def _warm():
            try:
                t = time.monotonic()
                await asyncio.get_running_loop().run_in_executor(None, brain().warmup)
                logline(f"本地语音大脑预热完成 ({time.monotonic()-t:.1f}s)")
            except Exception as e:
                logline(f"预热失败: {e}")
        asyncio.create_task(_warm())

    app = web.Application()
    app.router.add_route("*", args.ota_path, ota_handler)
    app.router.add_get(args.ws_path, device_handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, args.bind, args.port)
    await site.start()

    logline(f"监听 {args.bind}:{args.port}{STATE['ws_path']}")
    logline("版本检查地址见本机控制 API（不记录私有配置路径）")
    logline(f"设备 WebSocket -> {ws_url()}")
    logline("等待猫头连接…")
    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
