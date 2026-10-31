# VoiceHub WebSocket v1

端点 `/ws`，UTF-8 JSON 控制帧，binary 为原始音频。心跳由 WebSocket ping/pong
以及可选应用层 `ping`/`pong` 维持。首帧须在 5 秒内发送。

## 握手

```json
{
  "type": "hello",
  "version": 1,
  "device_id": "my-app",
  "token": "<device token, or use Bearer header>",
  "wake_mode": "manual",
  "audio": {"format": "pcm_s16le", "sample_rate": 16000},
  "capabilities": {"echo_cancellation": true, "continuous_audio": true}
}
```

`wake_mode` 可省略以继承默认配置。音频输入支持 16k mono `pcm_s16le` 或裸 `opus`。
本版通用输出始终为 16k mono PCM。握手返回：

```json
{
  "type": "hello", "version": 1,
  "session_id": "<32 hex>", "resume_token": "<opaque secret>",
  "wake_mode": "manual",
  "audio": {"format":"pcm_s16le", "sample_rate":16000, "channels":1},
  "capabilities": {"barge_in":true, "aec_verification":"client_declared", "playback_ack":true}
}
```

重连首帧加上 `session_id` 和 `resume_token`。会话 ID 不是凭证；恢复令牌只在握手
返回，服务端仅保存哈希。相同会话同时连接被拒绝，不会挤掉已在线客户端。
设备令牌相同代表同一受信任接入域，`device_id` 是显示标签，不是独立的授权身份。

## 客户端事件

| type | 字段 | 语义 |
|---|---|---|
| input.text | text: 1–4000 字符 | 激活并开始新一轮；会取消旧回复。录音模式下被忽略 |
| session.activate / wake | — | 激活聆听，音频无需重新握手；录音模式下无效果 |
| session.standby | — | 取消当前轮次并回到待机；正在录音时先保存录音 |
| interrupt | — | 显式打断并继续聆听；正在录音时等同于停止并保存 |
| abort | — | 设备取消播报；正在录音时等同于停止并保存 |
| recording.start | — | 开始录音（与网页开始按钮相同的会话级动作） |
| recording.stop | — | 停止录音（与网页停止按钮相同的会话级动作） |
| playback.done | turn_id | 本地播放缓冲确实排空后的回执 |
| ping | — | 返回 pong |

`input.text` 不是可安全重试的幂等操作。网络断开后不要自动重发；重连后先读取历史。
PCM 建议每 40ms 发送 1280 bytes；必须为完整 int16 样本，最多 8192 bytes/包。
Opus 最多 2048 bytes/包。按实际采样速率发送，禁止批量灌入整个文件。

## 服务端事件

握手之外的会话事件包含 `session_id`、可空 `turn_id`、连接内递增 `seq` 和 Unix 秒
`timestamp`。重连后新连接的 `seq` 重新开始；跨重连使用数据库记录 ID 或时间戳查询。

| type | 主要字段 |
|---|---|
| session.connected | device_id, protocol, wake_mode, barge_in |
| session.state | state: standby/listening/thinking/speaking/recording |
| session.activated | mode |
| speech.started | probability |
| transcript.final / message.user | text |
| reply.delta / reply.final | text |
| audio.start | sample_rate=16000, channels=1, format=pcm_s16le, elapsed_ms |
| audio.chunk | data: base64 PCM；必须按 turn_id 核对有效轮次 |
| audio.end | samples、drain_ms（估计剩余播放毫秒数）；只代表音频发送完，不代表播放完。客户端仍应在实际播放完后回传 playback.done |
| playback.clear | interrupted_turn_id, reason |
| playback.unconfirmed | — |
| turn.completed | delivery: generated/played, elapsed_ms |
| recording.started | recording: {id, session_id, device_id, started_at, duration_ms, format, sample_rate, channels, transcript_available, download_url:null, ...} |
| recording.confirming | recording, expires_in=20；先说了一次“停止录音模式”，等待“确认停止” |
| recording.confirm_expired | recording；20 秒内没有确认，继续录音 |
| recording.stopped | recording；此时 download_url 可用 |
| recording.transcript | recording_id, line（1 起）, text, stamp（录音内 mm:ss）, lines, elapsed_ms, merged；短停顿时会以 merged=true 更新上一行 |
| recording.transcription_unavailable | message；VAD/ASR 不可用或使用 HTTP ASR，录音仍在继续 |
| recording.input_ignored | message；录音模式下收到的文字消息被忽略 |
| error | code, message, 可选 error_class |

`session.state=recording` 表示音频正在写入 WAV，此时普通语音不产生
`transcript.final`、`message.user`、`reply.*` 或工具调用。每个 VAD 停顿后的语音段
只产生一条 `recording.transcript`，客户端按 `line` 去重后换行显示。停顿短于约
1 秒时服务端把新文字接到上一行并补句号，事件带 `merged=true` 和更新后的整行
`text`；客户端应替换该行而不是追加。录音提示（开始确认、停止二次确认、最终保存
提示）使用普通 `audio.start/chunk/end` 播放，但不写对话历史或录音转写。实时转写和
语音停止只在 ASR 为本机服务时启用；HTTP ASR 会收到
`recording.transcription_unavailable`，录音音频不会为此上传。

收到 `playback.clear` 时立即停止正在播放的音源、清空所有排队音频、使旧 turn_id
失效；此后任何迟到的旧轮次片段必须丢弃。新 `audio.start` 建立新轮次播放所有权。
服务端输出会按音频时长节流，客户端仍应限制播放缓冲，不能无限排队。

## 管理 API

- `GET /api/status`：在线会话与 VAD 文件可用性；在线会话带 `recording` 字段。
- `GET /api/config`、`PUT /api/config`：完整配置快照，写入先校验再原子保存。
- `GET /api/sessions?before=<updated>`：最近 100 个，时间游标向前翻页。
- `GET /api/sessions/{sid}/messages?before=<id>`：最多 100 条，按时间正序返回。
- `GET /api/logs?session_id=<sid>&before=<id>`：最近 100 条，倒序返回。
- `POST /api/sessions/{sid}/interrupt`：停止在线会话；录音中则停止并保存录音。
- `GET /api/events`：管理 WebSocket，含实时文字增量，不含音频。
- `POST /api/login`：`{"token":"<admin token>"}`，HttpOnly/SameSite Strict Cookie。
- `GET /api/recordings?session_id=<sid>`：列出录音及活动状态；不返回文件路径。
- `POST /api/recordings`：`{"session_id":"<32 hex>"}` 开始录音；会话必须在线，重复开始返回 409。
- `POST /api/recordings/{id}/stop`：停止并保存；会话已断线时直接完成文件。
- `GET /api/recordings/{id}/audio`：下载已完成的 WAV；活动中的文件返回 404。
- `GET /api/recordings/{id}/transcript`：读取逐行转写（活动录音也可读）；返回
  `{id,status,available,text,lines,count}`。`lines` 每项为
  `{stamp:"mm:ss", elapsed_ms, text}`，`stamp` 是录音内相对时间；没有文字时
  `available=false`。保存到磁盘的 `<id>.txt` 每行同样带 `[mm:ss]`。
- `DELETE /api/recordings/{id}`：删除 WAV、txt 与元数据；活动中的录音返回 409。

管理事件流积压超过 256 条时发 `events.resync`，应重新请求会话/日志快照。
它不是持久消息队列。记录存储失败不伪装成持久化成功。

本地管理只信任回环来源与 localhost/回环 Host，浏览器写操作与 WebSocket 拒绝跨站
Origin。局域网设备使用设备令牌；远程管理使用管理员令牌。浏览器静态资源可以访问，
但受保护 API 和会话仍需认证。HTTPS/WSS 需部署端自行提供。


## 指定网卡的设备专用入口（2026-10-24）

`--device-host 192.168.2.122 --device-port 8766` 增加独立监听，只注册 `/ws`、
`/xiaozhi/v1/` 和带私有令牌路径的OTA配置，不注册管理API或静态页面。
设备入口即使来自回环也必须提供设备令牌，不能利用本机管理信任绕过认证。
管理界面继续由127.0.0.1:8765提供。28项后端测试包含路由隔离、强制认证和OTA返回地址。

## 旧协议的播放准备确认

支持确认的设备在hello.features声明 `playback_ready: true`。后台tts.start带当前
turn_id；设备在主任务完成Speaking状态切换和解码器重置之后，回复tts.ready，带同一
turn_id、device_state（Speaking=6）、rx_audio_packets、dropped_audio_packets。
设备还回传decoded_audio_packets、played_audio_packets、output_enabled；准备确认必须同时
满足Speaking状态和音频输出已开启。后台只接受当前轮次的确认，2秒内未准备好则报告
playback_not_ready并停止本轮，不发送音频。
取消与断线会取消等待；其他轮次的确认不能释放当前轮次。未声明能力的旧设备仍走兼容路径。
tts.stop也带turn_id；支持的设备回复tts.stopped及相同计数，用于诊断收包/状态丢包，
此回复不表示实际扬声器播放完毕，不得据此把历史标记为played。
