# 声桥 VoiceHub

独立开发的 Mac 实时语音后端。客户端通过 WebSocket 接入，会话、模型和 UI 不依赖
ESP32；附带网页语音客户端和小智协议适配。运行时代码全部位于本目录，不导入旧项目代码。

当前版本为 **0.1 开发预览**。真实本地语音管线和合成语音打断已联调；真实外放 AEC、
ESP32 新服务接入和长时间运行尚未验收，不能据此宣称生产稳定。

## 启动

在这台 Mac 上已有兼容的 Python 运行库，直接运行：

```sh
cd /Users/huzhiwei/ai/miaoban/realtime_backend
./run.sh
```

需要后台常驻（关掉终端也继续跑）时用 `start.sh`：

```sh
./start.sh              # 后台启动；已在运行则复用
./start.sh --status     # 看进程、控制台和设备通道地址
./start.sh --restart    # 先停后启（改了代码/配置后用这个）
./start.sh --stop       # 停止
```

`start.sh` 把 PID 写到 `data/server.pid`、日志追加到 `data/server.log`，
控制台固定 `127.0.0.1:8765`；设备通道是否开启由 `data/connections.json` 里的
`device_enabled`/`device_host`/`device_port` 决定，脚本启动后若发现本机私网地址
和保存的设备地址不一致会给出提示。

打开 <http://127.0.0.1:8765/>，进入“连接实验室”：

1. 点击“连接文字”，输入一句话，检查本地 LLM 和播放。
2. 点击“开启麦克风”，授予浏览器权限，默认手动激活后可连续对话。
3. 说话结束后等待语音回复；支持 AEC 的客户端可在播报期间插话。也可点击“打断回复”。
4. 空闲 30 秒后回到待机；点击“激活聆听”，或使用选定的唤醒机制再次进入对话。

喵伴旧协议客户端在空闲超时后会关闭本轮 WebSocket 连接，由设备恢复待机画面和
本地唤醒词检测。再次喊唤醒词时建立新连接，避免设备留在聆听画面而后台已经待机。

### 本地 Qwen3-TTS（MLX）

本机可使用已缓存的 `mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit`：

```sh
python scripts/start_qwen_tts.py
```

该脚本启动或复用127.0.0.1:8767服务，离线加载模型并预热，保持单独的常驻MLX工作线程。
后台「模型配置 → 语音合成」选择兼容HTTP API，模型ID填上述模型，API Base URL填
`http://127.0.0.1:8767/v1`，音色`Serena`，PCM采样率`24000`。
无需真实API密钥，密钥环境变量字段可保留`VOICE_TTS_KEY`。
保存后新连接使用配置。诊断目录中的`start_device_listener.py`也会在选择本地Qwen时
先确保此服务启动，再启动后台。

服务仅开放本机健康检查和受限的PCM合成接口，限制为该模型及其预设音色，最多2个
在途请求；客户端断开会取消生成。模型与网络服务分开运行，避免TTS排在同一GPU工作
队列的LLM之后而互相等待。日志为`data/qwen-tts.log`，不保存合成文本或音频。
每句首块生成慢于播放所需速度时，本句会先合成完再发送，避免已知慢启动导致句内
断续；快于实时则直接流式输出。缓存仍受30秒音频上限约束。日志`tts.delivery`中的
`sentence`表示整句缓存、`stream`表示流式；这不能保证推理中途突然加重GPU负载时
永不出现延迟，应结合当次真实负载和包间隔检查。
接口文档：POST `/v1/audio/speech`，JSON含`model,input,voice,response_format:"pcm"`，
返回24kHz单声道s16le流；GET `/health`返回预热状态、模型、音色列表和采样率。

生图和其他本地模型会争用GPU；测首包延迟和生成速度时应记录负载。不要把高负载下
的慢速结果当作该模型的固定性能，也不能将空闲测速视为繁忙时的保证。

启动脚本优先使用 `VOICEHUB_PYTHON`，其次本目录 `.venv`，然后这台机器已有的模型
运行环境，最后系统 Python。新环境安装：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-local.txt
brew install ffmpeg
./run.sh
```

仅用 HTTP 模型服务时安装 `requirements.txt` 即可。MLX 需要 Apple Silicon 和可访问
Metal 的桌面运行环境。默认 TTS 为 macOS 自带的 Tingting，不需要加载 TTS 神经模型。
使用 `say -v '?'` 查看已安装音色。首次推理会懒加载 ASR/LLM，耗时明显高于热启动。

## 模型与配置

在“模型与语音”分别配置：

| 模块 | 本地支持 | HTTP 兼容接口 |
|---|---|---|
| ASR | MLX Audio Qwen3-ASR | `POST {base}/audio/transcriptions`，multipart WAV，返回 `text` |
| LLM | MLX LM 聊天模型 | `POST {base}/chat/completions`，SSE `choices[].delta.content` |
| TTS | macOS `say` | `POST {base}/audio/speech`，`response_format=pcm` |

Base URL 应包含服务要求的 `/v1`，不要填写具体方法路径。HTTP TTS 必须返回
**单声道、16bit little-endian 裸 PCM**，采样率在配置里填写，服务端会连续重采样为 16k。
不把 WAV、MP3 或供应商私有格式当作 PCM 播放。具体供应商仍需逐家联调；兼容协议测试
不等于已测试所有云厂商。端到端语音模型和已有 Grok Bot 会话未在此版实现。

## 常用 MCP 工具

左侧 **MCP 工具** 可查看已注册工具、参数、示例、决策方式，并单独启停。
网页聊天和对话记录会在对应轮次显示调用卡片，可展开查看实际参数、耗时和错误；
打断显示取消，缺少结束证据的旧记录显示状态未确认。刷新后仍可查看历史调用。
默认参数设置仍在“模型与语音”，启停和参数修改都对新连接生效。
支持上传单个 JSON stdio 服务配置或 Python MCP ZIP 包，默认不执行，明确勾选信任并启用后
验证 initialize/tools/list；每个工具可手动输入 JSON 测试。启用服务以当前用户权限运行，
没有 OS 沙箱，不自动安装依赖。可下载页面中的回显示例验证上传流程。
上传格式和限制见 [音乐与用户扩展](docs/SPEC-mcp-music-extensions.md)。

2026-10-28 已完成 Rex 分类与参数抽取联合训练并在本机启用，当前默认新闻源为中新网。
独立合成测试 329 条的完整计划正确率为 93.31%，实体抽取 F1 为 98.55%；仍有概念解释
误路由和首次语音生成延迟。训练、边界及真实调用证据见
[联合训练与联调报告](../diagnostics/2026-10-28/rex-slots-v2/REPORT.md)。

2026-10-31 按当前 MCP 工具集重新训练了决策分类（`rex-everyday-v5`，七个标签：
null/weather/news/system_info/shopping_list/timer/item_memory）。数据在 v4b 合成集上
补充了 656 条来自真实对话形态的硬负例（单独唤醒词、寒暄、评价应答、聊「今天过得怎么样」、
只提到主题而没有请求），测试集保持与 v4b 逐字节一致。真实日志 378 轮对照：准确率
87.9% → 89.6%，无工具误触发率 7.6% → 3.6%。构建与评测脚本为
[build_router_v5_data.py](scripts/build_router_v5_data.py)、
[evaluate_rex_real_logs.py](scripts/evaluate_rex_real_logs.py)；上线方式写
`data/rex-planner-active.json`。音乐、文件整理和日程仍由规则路由，不进入这个分类器。

“模型与语音 → 常用 MCP 工具”可分别启用天气、新闻和本机系统信息，设置默认城市、
新闻来源、条数及超时。旧配置自动补齐；保存后重新连接客户端生效。

工具规划器可选择“对话模型规划”或“本地 Rex 小模型（分类＋参数抽取）”。Rex 模式
使用本机联合训练的 rank-16 adapter，在隔离 CPU 进程中完成工具分类和原文实体抽取，
日期、数量、来源与系统指标由代码归一化并校验。原三类工具使用 Rex，音乐的明确指令用约束解析；
启用用户扩展后，额外使用动态目录 LLM 规划检查外部工具请求，再处理原工具候选。LLM 也负责普通聊天与结果总结。
必要参数缺失时先追问，不调用 MCP。模型暂时不可用会明确反馈，不静默切到云端。
详细契约见 [决策、参数与 MCP](docs/SPEC-rex-mcp-parameters.md)。

- 天气：通过 Open-Meteo 查询当前天气及未来三天预报。未提供城市且未配置默认城市时，
  助手应先询问。返回匹配城市、时区和日期，遇到同名地点应核对。
- 新闻：读取选定 RSS 源的最新条目，可按关键词筛选；默认 BBC World，英文内容由
  LLM 用中文摘要。中新网中文源可选；不同网络下可用性不同。不是全网新闻搜索，
  源不可达时明确报告失败，不擅自换源。新闻发布时间与抓取时间分别保留。
- 系统信息：只读取运行后端的电脑 CPU、内存、磁盘和开机时长，不读取进程、文件或账号。
  支持 `sections:["cpu","memory","disk","uptime"]` 的子集；省略时返回全部概况。
- 本地音乐：在 MCP 页设置后端电脑上的音乐目录并启用。可搜索、播放、暂停、继续、停止；
  支持 MP3/WAV/M4A/AAC/AIFF/FLAC，具体解码由 macOS afplay 决定。多首匹配时返回候选。
  音频输出到后端电脑，不代表已向机器人传输音乐流。扫描最多 5000 首，拒绝目录外符号链接。

示例：“上海明天天气怎么样？”“读三条最新新闻。”“这台电脑内存用了多少？”

耗时工具前先合成并发送“我查一下，请稍等”一类提示，按音频时钟等待这段音频排空后
才启动工具。客户端仍需正确播放服务端音频；这不等于对物理扬声器做了完成测量。
成功后播报结果；失败或超时有中文语音反馈。事件日志提供 `tool.started`、
`tool.completed`、`tool.failed` 和耗时。打断会取消该轮工具子进程，不输出旧结果。

工具通过独立进程 `python -m voicehub.common_tools` 提供标准 stdio MCP：初始化、
能力协商、工具发现和调用。除内置服务外支持用户导入 stdio 服务；尚未实现 Streamable HTTP 或智能家居。

每个工具独立放在 `voicehub/mcp_tools/` 下：`weather.py`、`news.py`、`system_info.py`、`music.py`。
各文件包含参数 Schema、提示语和统一的异步 `execute(http, **arguments)` 实现；
`mcp_tools/__init__.py` 显式注册工具，`shared.py` 提供有大小限制的 HTTP 读取和时间戳。
`common_tools.py` 只处理 MCP 协议、分发、超时和统一错误；`tools.py` 从注册表生成目录并校验参数。
扩展时新增工具模块、注册并补充边界测试；Rex 还需补充标签、实体字段和训练数据。

“对话模型规划”通过受限 JSON 计划选择工具，不要求 HTTP 服务提供原生 function calling，
每轮增加一次 LLM 规划推理。“Rex 小模型”对原三类工具以原文字符 offset
核对抽取字段，缺参或越界先追问。新增用户工具不自动训练 Rex，而由动态工具目录参与规划。
禁用所有内置和用户扩展工具后恢复原有对话链路（明确日历问题仍由本机时钟回答）。
天气和新闻的实际可用性依赖本机网络及上游服务。

协议与数据接口：[MCP stdio](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)、
[Open-Meteo](https://open-meteo.com/en/docs)。本轮验收见 [MCP 验证记录](docs/MCP-VALIDATION.md)。

密钥栏填写环境变量名称，例如启动前设置 `VOICE_LLM_KEY`；实际密钥不写入配置、
前端或日志。HTTP 提供商在实际调用时才读取该环境变量。更改模型或唤醒配置只对
**新连接**生效，现有会话的配置快照不变；请重新连接客户端。

默认本地模型：

- `mlx-community/Qwen3-ASR-0.6B-8bit`
- `mlx-community/Qwen3-4B-Instruct-2507-4bit`

本地适配器只加载已缓存的权重，或配置中的本地模型目录，不自动下载。
新模型请提前使用官方 Hugging Face 工具下载。只要求推理所需文件，不要求 README 等
仓库说明文件也存在。最多一个 GPU 工作任务加一个排队任务；等待超过 5 秒明确报告 busy。
底层不可取消的 ASR/权重加载会继续占用 worker，但不会阻塞 WebSocket 事件循环或输出旧结果。

## 人声检测与唤醒

检测使用 Silero ONNX（CPU），支持常见 `state/sr` 接口及 faster-whisper v6 的 `h/c` 接口。
可在 UI 填写模型文件路径，也可设置 `VOICE_VAD_MODEL`。留空时查找已安装的
faster-whisper 资产及本机 uv 缓存；没有模型时文字仍可用，语音会明确报告不可用。
UI 的“可用”表示找到了文件，不代表该路径下任意模型均已通过推理验证。

| 唤醒模式 | 行为与限制 |
|---|---|
| device | 设备自己检测唤醒，发送 `wake` / `session.activate`；词表在设备配置 |
| backend | 客户端持续上传，VAD 分句后 ASR 检测唤醒词，可接同句话中的提问 |
| manual | 用户按钮或触摸激活；浏览器实验室默认采用该模式 |

后端唤醒目前是**分句转写检测**，并非低延迟专用关键词引擎。未命中唤醒词的待机
转写不写入对话或事件记录；如果使用 HTTP ASR，转写所需音频仍会送往你配置的服务。
切换到后端唤醒要求客户端声明并真正支持 `continuous_audio`。普通设备不能仅靠服务端
开关就变成待机持续上传。

自然插话要求客户端在播报时仍上传经过 AEC 的音频，并声明 `echo_cancellation=true`。
服务器以连续人声概率确认起声，立刻取消旧轮次并发出 `playback.clear`；客户端必须
停止正在播放的源并清空缓冲。仅发送停止标记而不清除音频队列不算完成打断。
浏览器的 AEC 声明仅说明启用了浏览器约束，实际抗回声效果仍需外放测试。

## 连接其他设备

默认只监听本机。机器人接入时，仅在指定私网 IP 开放设备通道，管理界面继续保留在 127.0.0.1:8765：

```sh
./run.sh --device-host 192.168.2.122 --device-port 8766
./run.sh --print-device-token
```

在受信任局域网通过设备令牌连接；公网部署必须增加 HTTPS/WSS 终止，不直接暴露明文
端口。令牌不放在通用 WebSocket URL 查询参数中，使用 Bearer Header 或首帧 `token`。

Mac 本地管理可直接访问；设备专用监听不提供任何管理 API 或静态页面，强制验证设备令牌；不要将控制监听改为
0.0.0.0。需要远程管理时另行明确配置与授权，当前仍仅允许本机访问。管理员令牌查询：

```sh
./run.sh --print-admin-token
```

手机浏览器麦克风需要 HTTPS（手机访问 Mac 的 HTTP 局域网地址不等于 localhost）。

### ESP32 小智适配

- WebSocket：`ws://<Mac-IP>:8766/xiaozhi/v1/`
- OTA 配置：`http://<Mac-IP>:8766/xiaozhi/ota/<设备令牌>/`
- 小智侧音频：16k mono Opus，服务端输出 60ms 帧；没有硬件播放回执时记录为“已生成”。
- 适配连接、listen/wake、abort、stt、tts、llm；本版不提供设备 MCP 调用或电机控制。
- 后端唤醒/自然插话需要固件持续上传及 AEC 能力协商，未知能力不会自动当作支持。
- OTA 路径本身含凭证，禁止写到公网访问日志中；本服务默认关闭 HTTP 访问日志。

**2026-10-24 已将喵伴接入声桥设备通道。** 设备地址为192.168.2.122:8766，管理界面为
127.0.0.1:8765。应用与抽样资源保持不变，原配置备份和校验见
`../diagnostics/2026-10-24/voicehub-connect/`。已观察到真实设备的识别、回复和speaking状态；
硬件无播放完成回执，不将generated当作物理播完。

## 数据和诊断

默认 `data/`（权限 0700），可用 `--data /path` 指定：

- `settings.json`：原子替换写入的配置快照。
- `auth.json`：0600 权限的管理/设备令牌，不进入源码。
- `history.sqlite3`：SQLite WAL，消息、状态、错误与耗时。默认保留 30 天，启动及每小时清理。

不保存原始麦克风/播放音频；保留被打断回复并标记 interrupted。`generated` 只代表生成，
`played` 表示客户端发来匹配播放回执，不代表服务端测量到了物理扬声器声音。
本地数据库未做额外磁盘加密，访问控制依赖 macOS 用户及目录权限。

每连接入站队列最多 64 包，最多 100 音频包/秒，过载明确断开；最多 8 个在线会话。
音频输出按实际时长发送。网页客户端同时限制上传积压与播放积压，断线恢复身份和历史，
不重播旧音频，不自动重发上一条用户消息。

## 测试

```sh
VOICEHUB_PYTHON=/path/to/python ./run.sh
# 在另一个终端：
/path/to/python -m unittest discover -s tests -v
/path/to/python scripts/smoke_local.py
/path/to/python scripts/smoke_barge_in.py
```

两个 smoke 脚本都只向本机 8765 发请求；请在默认本地模型配置下执行。它们使用合成
语音，不读取真实麦克风，不发送云请求。打断测试验证干净输入链路，不验证声学 AEC。
测试结果及尚未完成的现场验收见 [验证记录](docs/VALIDATION.md)。

`Ctrl+C` 停止前台服务。此版未安装开机自启、未配置公网隧道，也未替换旧后端。

Streamable HTTP MCP：在 MCP 工具页填写服务地址、可选 Bearer Token/自定义认证头，保存后确认信任并启用验证。支持 JSON/SSE 响应与参数调用；公网使用 HTTPS，私人网络需明确授权。原 stdio 上传继续可用。暂不支持旧 HTTP+SSE、OAuth 登录或断线重放。详见 [HTTP 接入说明](docs/SPEC-mcp-http.md)。

### 动态任务链和独立邮件 MCP

MCP 页面支持自然语言任务规划、受控文件生成、SMTP 配置和逐次发送确认。独立邮件 MCP 提供草稿、状态和发送接口；授权码不进入模型上下文。使用方式、支持格式与限制见 [任务链与邮件 MCP](docs/MCP-email-workflows.md)。

### Skills 与后台执行

Skills 界面、默认接口和自动触发已暂时停用，历史技能定义与任务记录保留。MCP 后台执行和自动播报继续可用，执行记录位于 MCP 页的「后台任务与结果」。静默转入后台的查询在约 1 秒内完成时直接播报结果；仍在等待时才给一次与请求相关的简短反馈，完成后自动播报结果。历史设计见 [Skills](docs/SKILLS.md)。

### 日程与提醒 MCP

MCP 页的日程与提醒卡片支持本地日程查询、创建和取消，并在原设备在线时自动语音提醒。使用与离线补播、时区和单次提醒限制见 [日程 MCP](docs/MCP-calendar.md)。
