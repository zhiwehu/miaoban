# ADR-0001：把单体仓库拆成 AIHub 多仓库

- 状态：**提议（proposed）**——等待确认后进入阶段 0
- 日期：2026-10-31
- 决策范围：仓库边界、跨仓库契约、迁移顺序
- 相关文档：[CAPABILITY-MAP.md](../../CAPABILITY-MAP.md)、[PROTOCOL.md](../PROTOCOL.md)、[CLIENT_ONBOARDING.md](../CLIENT_ONBOARDING.md)

---

## 1. 背景

当前仓库把四类完全不同的东西放在了一起，而且已经出现了同步成本：

| 现状 | 规模 | 问题 |
|---|---:|---|
| `realtime_backend/voicehub/` | 85 文件 / 11,524 行 | 一个包里塞了协议、模型推理、工具、录音、配置五种职责 |
| `realtime_backend/voicehub/static/` | 24 文件 / 6,908 行 | 网页客户端与服务端同仓库、同进程部署 |
| `firmware/` | 215 文件 / 63,114 行 | ESP-IDF 工程，与 Python 服务无代码依赖 |
| 根目录逆向工具 `*.py` | 20 文件 / 3,547 行 | 逆向阶段产物，含硬编码绝对路径 |
| `realtime_backend/tests/` | 91 文件 / 6,601 行 | Python + Node + C++ 混在一个测试目录 |

三个已经实际造成困扰的耦合：

1. **`app.py` 是双向依赖的枢纽**：829 行，import 30 个模块；同时被 **12 个模块反向 import**（`HUB`、`local_admin`、`create_device_app`）。任何 router 都无法单独搬走。
2. **`LocalModels` 兼任依赖注入容器**：`app.py:152-163` 往 `self.local` 上挂了 12 个属性（`speakers`/`minutes`/`services`/`planner`/`extensions`/`workflows`/`calendar`…），`tool_chat.py:17` 通过 `pipeline.local.planner` 取用它。模型对象和运行时上下文被混成一个东西。
3. **运行目录不是 git 仓库**：`realtime_backend/` 与 `opensource/miaoban-voicehub/realtime_backend/` 是两棵树，靠 AGENTS.md 里手工 `cp` 同步。仓库数一旦增加，这套流程会直接失效。

## 2. 目标与非目标

**目标**

- 每块代码有唯一归属，跨仓库依赖单向、无环。
- 网页客户端、固件、模型服务可以独立发布和独立升级。
- 仓库名与代码标识统一用 `AIHub`，不再出现 `miaoban` 前缀。
- 每个仓库能跑自己的测试；core 与 models 之间有契约测试防漂移。

**非目标**

- 不做多租户、不做集群、不做跨机部署。全部假设单台 Apple Silicon Mac。
- 不为拆分引入服务网格、消息队列或容器编排。
- 不重写业务逻辑；迁移期间功能与行为保持不变。

## 3. 命名

| 层级 | 名称 |
|---|---|
| 产品/组织 | **AIHub** |
| 仓库前缀 | `aihub-*` |
| Python 包 | `aihub_server`、`aihub_models`、`aihub_tools`、`aihub_common` |
| 保留的子系统名 | “声桥 / VoiceHub” 仅作为实时语音子系统的中文描述，不作代码标识 |

包改名 `voicehub` → `aihub` 是纯机械改动，**单独一次提交**，与目录重组分开，便于 review 和回退。

## 4. 仓库划分

| # | 仓库 | 职责 | 规模（迁入时） | 运行时形态 |
|---|---|---|---:|---|
| 1 | `aihub-common` | 音频重采样、PCM16、口语化文本等无业务原语 | ~250 行 | 被依赖的纯库 |
| 2 | `aihub-server` | WS/MQTT/OTA 网关、会话状态机、配置与存储、模型 **HTTP 客户端**、录音采集、管理 API | ~5.3k 行 | 常驻进程 :8765 / :8766 |
| 3 | `aihub-models` | ASR/LLM/TTS worker、MLX 加载、声纹、重分轨、降噪、会议纪要、媒体后处理 job | ~3.6k 行 | HTTP 服务 :8781 / :8782 / :8767 |
| 4 | `aihub-tools` | MCP 工具、工作流、日历/邮件/计忆、Rex 路由、训练与评测脚本 | ~2.9k 行 + 36 脚本 | pip 包，**同进程** |
| 5 | `aihub-web` | 网页控制台 + 聊天客户端 + 构建流程 | 6.9k 行 | 静态产物 |
| 6 | `aihub-firmware` | 猫头/底座固件、逆向工具、刷机前端、镜像校验与打包 | 63k 行 C++ / 3.5k 行 Py / 1.5k 行 JS | 固件 + 浏览器 |
| 7 | `aihub`（可选） | 架构文章、跨仓库索引、LICENSE、发布脚本 | — | 文档 |

**为什么 `aihub-tools` 不做成网络服务**：`session.py → tools.py → tool_chat.py → rex_planner` 位于每一轮对话的热路径上，拆成 RPC 会额外增加一次跳转、一个失败点和一个超时面。**拆仓库不等于拆进程**——它是 pip 包，由 server 依赖。

**为什么 `aihub-models` 必须做成服务**：边界已经存在。`services.py` 里 `PORTS={'asr':8781,'llm':8782,'tts':8767}`，`model_worker.py` 与 `qwen_tts.py` 已经是独立 aiohttp 进程。这一步只是把既成事实正式化并加版本号。

## 5. 依赖方向

必须无环：

```
                 aihub-common
                  ↑        ↑
        aihub-server   aihub-models
          ↑    ↑            ↑
          │    └────────────┘  (tools 仅通过 server 的 Pipeline 接口使用模型)
    aihub-web  aihub-firmware
          │         │
          └─────────┴──→ 网络（WS v1 / OTA / REST）

    aihub-tools  ——被 server import（pip 依赖）——→ aihub-common
```

规则：

- `aihub-common` 不 import 任何兄弟包。
- `aihub-models` 只依赖 `aihub-common`，**不依赖 server**。
- `aihub-tools` 只依赖 `aihub-common`，通过 server 注入的 `Pipeline` 协议访问模型，不直接 import `aihub_server`。
- `aihub-server` 依赖 common + tools；对 models 只通过 HTTP。
- web / firmware 只依赖文档化的网络契约。

## 6. 模块归属明细

### 6.1 `aihub-common`（新建，约 250 行）

| 文件 | 来源 | 说明 |
|---|---|---|
| `aihub_common/audio.py` | `voicehub/audio.py` (166) | `to_pcm16`、重采样、`vad_path` |
| `aihub_common/speech_text.py` | `voicehub/speech_text.py` | `spoken_text` 口语化 |
| `aihub_common/stamp.py` | 从 `recording.py` 抽出 | `stamp_ms` / `stamp_text`，`rediarize.py` 需要 |
| `aihub_common/errors.py` | 从 `models.py` 抽出 | `ProviderError` |

这三处共用的函数目前分别被 server 与 models 双方 import（`models.py:16-17`、`rediarize.py:18,21`、`qwen_tts.py`、`model_worker.py`）。不抽出来只能复制，复制必然漂移。**用 250 行的包换掉两条分叉，成本最低。**

### 6.2 `aihub-server`（约 5.3k 行）

```
aihub_server/
├── __init__.py / __main__.py        # 入口（原 voicehub/__main__.py）
├── app.py                           # 只做组装，目标 ≤ 200 行
├── context.py        [新]           # HUB AppKey、Runtime 容器、DEVICE_ONLY
├── security.py       [新]           # local_admin / admin / same_origin（从 app.py 抽出）
├── routers/          [新]           # 每个文件自带 setup(app)
│   ├── chat.py  config.py  recording.py  minutes.py
│   ├── mcp.py   workflows.py  calendar.py  skills.py
│   ├── connections.py  device.py    # OTA / MQTT-OTA
│   └── clients.py                    # 客户端目录 / 连接配置 / 固件下载 / 备份
├── session.py                       # 957 行，会话状态机（含录音模式）
├── input.py  audio.py  connections.py
├── mqtt_gateway.py  mqtt_service.py  mqtt_udp.py
├── device_mcp.py  client_verification.py
├── models.py          # 只保留 Pipeline = HTTP 适配器
├── services.py        # 保留为“启动 models worker 的薄封装”
├── config.py  store.py  phrasing.py
├── recording.py  recording_api.py
├── provisioning.py  device_backups.py  clients.py
└── firmware_manifest.py             # 从 firmware_release.py 缩为 manifest 校验
```

迁移时**删除**：`modems.py` 中 `provider == 'mlx'` 的两个分支（`models.py:156-165`、`models.py:205-214`）。`config.py:46-47` 已把 `mlx` 归一化为 `builtin`，这两个分支不可达。删掉后 `Pipeline` 不再引用 `LocalModels`，模型层拆分就没有阻力了。

### 6.3 `aihub-models`（约 3.6k 行）

```
aihub_models/
├── __main__.py                      # python -m aihub_models serve --kinds asr,llm,tts
├── worker.py                        # 原 model_worker.py（/v1/* + /health）
├── tts_qwen.py                      # 原 qwen_tts.py（:8767）
├── local.py                         # 原 LocalModels（MLX 加载 + 单工作线程）
├── supervisor.py                    # 原 services.py 的进程管理部分
├── speaker/engine.py                # 原 speaker.py 的 SpeakerEngine（speechbrain）
├── diarize.py                       # 原 rediarize.py（离线重分轨）
├── cleanup.py                       # 原 audio_cleanup.py（ffmpeg 降噪）
├── minutes/                         # 原 meeting_minutes/（1,355 行，8 文件）
├── media_jobs.py        [新]        # 媒体后处理 job API（见 8.4）
└── cli.py                           # download-vad / serve / embed 子命令
```

注意：`speaker.py` 需要拆开。`SpeakerTracker`（第 60-192 行）是纯 numpy 余弦聚类、不加载模型，**留在 server**；`SpeakerEngine`（第 193 行起）加载 `speechbrain/spkrec-ecapa-voxceleb`，**迁到 models**。这样默认的“停止后统一重分析”不受影响，只有可选的实时分轨要跨进程。

`scripts/` 里与推理相关的 `download_vad.py`、`rediarize_recording.py`、`build_speech_only.py`、`build_clean_audio.py` 一并迁入。**VAD 本身留在 server**——它必须在音频流内联低延迟运行，不能过网络。

### 6.4 `aihub-tools`（约 2.9k 行 + 脚本）

`mcp_tools/`(489) `workflows/`(655) `calendar_*` `everyday_*` `rex_*` `slot_normalizer.py` `tool_chat.py` `mcp_client.py` `mcp_http.py` `mcp_extensions.py` `mcp_management.py` `common_tools.py` `email_mcp.py` `extension_routing.py` `music_player.py` `file_organizer.py` `file_management.py` `timer_reminders.py` `skills*` `background.py`，加 `training/`(364) 与 `scripts/` 里 20 个 router/rex 训练评测脚本，加 `data/training/router-everyday-v5/`。

`rex_planner_worker.py` 已经跑在独立子进程里（用 torch + `training/rex_runtime`），与 server 之间只有 JSON 帧，边界本来就干净，随 tools 一起走。

### 6.5 `aihub-web`（6,908 行）

`static/` 全部：`index.html`(40 KB)、`app.js`(92 KB)、`style.css`(40 KB)、`*-ui.js`、`connections.js`、`capture.js`，**除**刷机相关文件（见 7.1）。

当前无构建流程，`app.js` 是单个 92 KB 文件。建议迁入时引入 vite：产出 `dist/`，由 server 从 `<data>/web/` 或构建产物目录提供，`index.html` 不再内联业务逻辑。

### 6.6 `aihub-firmware`（63k 行 C++ + 3.5k 行 Py + 1.5k 行 JS）

| 内容 | 来源 |
|---|---|
| `firmware/head/` `firmware/base_controller/` `firmware/shared/` | 原样 |
| `tools/reverse/` | 根目录 20 个逆向脚本（`xiaozhi_server.py`、`miaoban.py`、`voice_brain.py`、`voice_loop.py`、`jt.py`、`nvs_edit.py`、`bootstack.py`、`switch_wifi.py`、`make_face_assets.py` 等） |
| `tools/native/stream_speech.swift` | `native/` |
| `tools/build_echoear_upgrade.py`、`package_echoear_release.py` | `scripts/` |
| `tools/test_firmware_logic.py` + `tests/*.cc`（24 文件 / 881 行） | `tests/` 根目录 |
| `flash/` 浏览器刷机前端 + `vendor/esptool-js-0.7.0` | `static/echoear-*.js|mjs` |
| `image.py` | 原 `voicehub/firmware_release.py`（ESP 镜像头/段/CRC/SHA256 校验） |

## 7. 专题决策

### 7.1 配网与刷机

**事实**：真正的刷机已经全部在浏览器里完成。服务端从未接触 USB。阅读代码可以确认：

- 浏览器侧（≈1,500 行）：`echoear-installer.js`(23 KB) + `echoear-flash-reader.mjs` + `echoear-nvs.mjs` + `echoear-upgrade-core.mjs` + `echoear-incremental-backup.mjs` + `echoear-backup-storage.mjs` + `echoear-device-backup.mjs` + `echoear-response.mjs`，通过 Web Serial 读写，备份保存在用户本机。
- 服务端侧（合计 362 行）：`provisioning.py`(96)、`device_backups.py`(129)、`clients.py`(66)、`firmware_release.py`(71)，以及 `/xiaozhi/ota/{token}/`、`/xiaozhi/mqtt-ota/{token}/` 两个端点。

**决策**：按“协议端点”与“设备工具”切开。

| 保留在 `aihub-server` | 迁到 `aihub-firmware` |
|---|---|
| `/xiaozhi/ota/{token}/`、`/xiaozhi/mqtt-ota/{token}/`——设备开机必须轮询，**搬走就断网** | 浏览器刷机助手全套 + `vendor/esptool-js-0.7.0` |
| 设备令牌签发、局域网监听启停（`provisioning.py`） | `image.py`：ESP 镜像头/段/CRC/SHA256 校验（属于固件知识，不是服务端知识） |
| 固件产物与 manifest 的**存放和下载**，只校验 `board`/`kind`/`sha256`/大小 | `build_echoear_upgrade.py`、`package_echoear_release.py` |
| 设备备份**落盘**（33 MB 整片，浏览器上传，服务端只验校验和） | 备份的读取/校验/恢复逻辑（已在浏览器侧） |

配网本身（Wi-Fi 写入）发生在设备配网页，服务端只回答“本机哪个网段、令牌是什么”。这属于**服务端配置**，不是刷机。

两个必须提前处理的约束：

1. **Web Serial 要求 secure context**（https 或 localhost）。刷机页不能从 `file://` 直接打开。迁出后由 `aihub-firmware` 以 release 静态目录发布，或挂 GitHub Pages。**不能**指望用户双击 HTML。
2. 刷机面板当前是 `index.html:163` 的 `<article id="echoear-installer">`，由 `index.html:12` 的 `<script type="module" src="/static/echoear-installer.js">` 加载。拆出时要同时改 `index.html` 与 `connections.js` 的入口。

### 7.2 录音

**事实**：录音是会话的一个模式，不是旁边的模块。`session.py` 957 行里：

- 出现 `recording` 字样 **193 行**
- 37 个函数里 **20 个**与录音相关
- 开始录音会 `await self.interrupt('recording')`，与唤醒/轮次/打断状态机互相咬合
- 采集是实时流：100 包/秒，每 2 秒 fsync，进程被杀可恢复

**决策**：采集留在 server，后处理搬到 models。

| 留在 `aihub-server` | 搬到 `aihub-models` |
|---|---|
| `recording.py`（840 行，只依赖 numpy） | `speaker/engine.py` 声纹提取（speechbrain） |
| `recording_api.py`（192 行） | `diarize.py` 离线重分轨（359 行） |
| `session.py` 内录音状态机 | `cleanup.py` 降噪（ffmpeg，72 行） |
| `SpeakerTracker` 聚类（纯 numpy） | `minutes/` 会议纪要（1,355 行） |
| | `media_jobs.py` 异步 job 编排（新增） |

理由：后处理是**文件进、文件出**（`<id>.wav` → ready/clean/txt/json/minutes），耗时几十秒到几分钟，吃 GPU，与 ASR/TTS 抢显存——天然适合独立服务。采集如果把每包音频送 RPC，会新增一个热路径跳转，且 models 崩溃时正在录的文件可能丢数据。

**不做独立“录音服务”**。触发条件：将来要支持手机录音上传或设备离线补传时，输入从实时流变成文件，那时再抽出来，边界自然干净。

### 7.3 明确不拆的部分

| 内容 | 原因 |
|---|---|
| VAD（Silero ONNX，`audio.py`） | 必须在音频流内联运行，延迟敏感 |
| `device_mcp.py` | 设备 MCP 通道的服务端代理，属于协议 |
| `/xiaozhi/ota/` 端点 | 设备启动依赖 |
| 工具规划（`tool_chat.py`） | 在每轮对话热路径上 |
| `SpeakerTracker` 聚类 | 纯 numpy，无模型，跨进程调用不划算 |
| `native_speech`（macOS `say`） | 系统能力，无模型依赖 |

## 8. 跨仓库契约

拆分前必须先写契约，否则两边会漂移。四项中两项已存在，两项需新建。

### 8.1 客户端 WS 协议 v1（已存在）

见 [PROTOCOL.md](../PROTOCOL.md)。`hello.version=1`，事件名与字段不改。新增事件只能追加，不能改语义。

### 8.2 管理 REST（需加版本前缀）

现状：`/api/{name}`、`/api/sessions/{sid}/messages` 等，无版本。网页客户端独立发布后，**阶段 2 前必须**加 `v1` 前缀或在响应头带 `X-AIHub-Api: 1`，否则 web 与 server 无法独立升级。

### 8.3 模型服务 API v1（已存在，需补文档）

三个 worker 共用，`Authorization: Bearer <worker token>`：

| 端点 | 请求 | 响应 |
|---|---|---|
| `GET /health` | — | `{service_id, model, ready, error, active, sample_rate}` |
| `POST /v1/audio/transcriptions` | multipart：`model`、`file`（16k 单声道 s16le WAV，≤30 s）、可选 `language: zh\|en` | `{text}` |
| `POST /v1/chat/completions` | `{model, messages, max_tokens, stream:true}` | SSE，`choices[].delta.content` |
| `POST /v1/audio/speech` | `{model, input, voice, response_format:"pcm"}` | `audio/pcm` 流 + `X-Audio-Sample-Rate` |

错误统一 `{error:{code,message}}`。当前模型 ID 与语音合成都被硬编码为服务启动参数（`--model`／`--kind`），契约要求：**模型不匹配一律拒绝**，不允许静默降级。

### 8.4 媒体后处理 job API（新建）

沿用现有 `meeting_minutes/store.py` 的 job 模式（`queued|running|interrupted|done|failed`，重启时把未完成标为 `interrupted`）：

```
POST   /v1/media/jobs        {kind, recording_id, options}
                             kind ∈ speech_only | clean | diarize | minutes
GET    /v1/media/jobs/{id}   {status, stage, progress, outputs:[{name,path,sha256}], error}
GET    /v1/media/jobs?recording_id=<32hex>
POST   /v1/media/jobs/{id}/cancel
```

硬性约束：

- 服务启动时传 `--media-root <data>/recordings`，**只接受该根目录内的路径**，拒绝任意路径。
- job 状态落盘 `<data>/media-jobs/<recording_id>/<job_id>.json`，原子写、0600。
- 同机部署共享该目录；跨机部署（非目标）才走上传下载。

### 8.5 设备产物 manifest（schema 归 firmware）

保持现有字段不变：`board`、`kind`、`file`、`version`、`sha256`、`install{partition_bytes, partition_sha256, upgrade_from_sha256[], validated}`。

`board`/`kind`/`file` 为固定字面量，镜像深度校验由 firmware 仓库的 `image.py` 在**打包时**完成。server 只校验 `sha256` 与大小范围。这样 firmware 换板型不需要改 server。

### 8.6 共享数据目录

单机部署，`<data>/` 同时被两个服务访问：

```
<data>/
├── settings.json  auth.json  history.sqlite3     server
├── recordings/    录音 WAV / txt / speech.json    server 写，models 读改写
├── media-jobs/    后处理 job 状态                 models 写，server 读
├── meeting_minutes/                               models
├── speaker-cache/ 声纹模型缓存                    models
├── services/      worker pid/token                server
└── web/           网页构建产物                    server
```

## 9. 目标目录结构（阶段 0 落地的单仓库形态）

目录即仓库——每个 `packages/*` 后续用 `git subtree split` 单独成库：

```
aihub/
├── packages/
│   ├── aihub-common/    src/aihub_common/
│   ├── aihub-server/    src/aihub_server/
│   ├── aihub-models/    src/aihub_models/
│   └── aihub-tools/     src/aihub_tools/
├── apps/web/
├── firmware/
├── scripts/             跨仓库发布脚本
└── docs/adr/
```

## 10. 阶段 0：仓库内重组（前置，必做）

**不拆仓库**，但在单仓库内把边界做成事实。这是唯一能安全拆分的前提。

### 10.1 打破 `app.py` 环（必须最先做）

1. 新建 `context.py`：`HUB` AppKey、`Runtime` 容器、`DEVICE_ONLY`。
2. 新建 `security.py`：`local_admin`、`admin`、`same_origin`（从 `app.py:41-75` 抽出）。
3. 每个 router 文件提供 `setup(app)` 自行注册路由；`app.py` 只做组装。
4. 12 个反向 import `HUB` 的模块改为 `from .context import HUB`。

验收：`grep -rn "from .app import" voicehub/` 结果为空。

### 10.2 拆 `models.py`

1. 删除不可达的 `provider == 'mlx'` 分支（见 6.2）。
2. `Pipeline` 只保留 `builtin` / `http` / `macos` 三条路径。
3. `LocalModels` 整体迁往 models 包；server 不再 import MLX。

### 10.3 引入 `Runtime` 容器

把 `app.py:152-163` 挂在 `self.local` 上的 12 个属性改为显式 `Runtime` 字段。`tool_chat.py:17` 的 `pipeline.local.planner` 改为 `pipeline.runtime.planner`。

验收：`grep -rn "\.local\." voicehub/` 只剩 `LocalModels` 自身。

### 10.4 目录迁移

`realtime_backend/voicehub/` → `packages/aihub-server/src/aihub_server/`，文件按 6.2 归位；`realtime_backend/scripts/` 按 6.3/6.4/6.6 三分；`tests/` 按归属拆到各包。

**注意**：这一步会改 `run.sh`、`start.sh`、`start.py` 的项目根发现逻辑，也会让运行目录 `realtime_backend/` 失效（见 10.7）。

### 10.5 包改名 `voicehub` → `aihub`

机械替换 import 与文档，**单独一次提交**。

### 10.6 清理硬编码与死链

| 位置 | 问题 |
|---|---|
| `start.py:41`、`restart_backend.py:26`、`jt.py:8`、`motor_demo.py:11`、`motor_test.py:12`、`watch_connect.py:2` | 硬编码 `/Users/huzhiwei/...` |
| `SPEC-realtime-session.md:118-119`、`realtime_backend/docs/{VALIDATION,MCP-VALIDATION,SPEC-mcp-tools}.md`、`realtime_backend/README.md:14` | 同上 |
| `realtime_backend/README.md:321,451`、`docs/MCP.md:34,77` | 指向仓库外的 `../diagnostics/` |
| `clients.py` 的 `source_url` | 指向 `feature/voicehub-control-center` 分支，已过期 |

另：`SOURCE_MANIFEST.json`（63 KB 逐文件 sha256）改为 CI 生成，不提交。

### 10.7 修两棵树同步

现状是手工 `cp`（AGENTS.md 有完整流程与已知陷阱）。多仓库后必须换掉，建议：

- 运行目录改为 `git worktree`（`realtime_backend` 指向仓库同一分支），或
- 全部改为 editable install，从 venv 运行。

**这一步应在任何代码搬迁之前完成**，否则搬迁期间的差异会与运行目录的陈旧副本互相覆盖（历史上已发生过冲掉仓库独有测试的情况）。

## 11. 阶段 1：拆 `aihub-firmware` + `aihub-models`

先拆这两块，因为它们最独立、收益最大：

- **firmware**：零 Python 代码依赖，只共享 manifest schema。
- **models**：HTTP 边界已存在，只是把既成事实搬到另一个仓库。

顺序：先 firmware（无风险，先验证 subtree split 流程），再 models。

## 12. 阶段 2：拆 `aihub-web`

前置条件：8.2 的 API 版本前缀已落地。否则 web 独立发布后无法判断自己兼容哪个 server。

## 13. 阶段 3：`aihub-tools` 待定

观察它是否继续增长。如果继续以每月数百行的速度增长，独立成库；否则留在 server 里但用子包隔离（`aihub_server/tools/`）。

## 14. 每阶段验收

每个阶段结束时必须同时满足：

1. `git status` 干净，`git rev-list --left-right --count origin/main...main` 为 `0 0`。
2. 受影响仓库的测试全绿（排除已知长期失败用例 `test_wake_phrase_alone_activates_without_creating_empty_reply`）。
3. 新增一个**契约测试**：server 对着真实 models 服务跑一次 ASR→LLM→TTS，以及一次 media job。
4. 手工冒烟：网页客户端连接、设备通道握手、录音开始/停止/转写。

## 15. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 6 个仓库的运维成本压给单人 | CI、版本对齐、契约漂移 | 分阶段推进，每阶段可回退；阶段 0 不增加仓库数 |
| `app.py` 重构期间引入回归 | 会话状态机是核心 | 重构前先补路由级测试；每步跑全量测试 |
| 录音状态机被误拆 | 数据丢失 | 采集明确留在 server，只迁后处理 |
| web 与 server 版本错配 | 控制台白屏 | 阶段 2 前必须落 API 版本前缀 |
| 两棵树覆盖冲突 | 丢失仓库独有测试 | 10.7 先做，再动代码 |
| 历史在拆分中断裂 | 追溯困难 | 先在单仓库重组，再 `git subtree split` |

## 16. 未决问题

1. **GitHub 远端是否改名/重建**？当前远端是 `zhiwehu/miaoban.git`，与 `aihub-*` 命名不符。改名需用户操作，且所有本地 clone 的 remote URL 要更新。
2. **“声桥 / VoiceHub”是否保留为中文名？** 本文按“保留为子系统描述、不作代码标识”处理。
3. **`aihub-common` 是否值得单独成库？** 只有 250 行。替代方案：让 models 直接复制 `audio.py`/`speech_text.py`（会漂移），或让 models 依赖 server 包（会造成环）。当前判断是单独成库最干净，但可以在阶段 1 时复议。
4. **`aihub` 元仓库是否需要？** 架构文章、协议 SPEC、跨仓库索引放哪里，取决于是否要对外发布。
