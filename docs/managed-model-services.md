# 内置模型服务

在「模型与语音」页面为 ASR、LLM、TTS 选择「内置服务（可启停）」并保存，然后分别启动。状态每两秒刷新，包含加载中、运行中、已停止、失败及端口占用；显示进程 PID、进行中的请求数及模型修改后的重启提示。

- 每项服务独立运行在子进程内。停止或重启会中断该项服务正在处理的请求，并释放模型进程。
- 后台退出时保留模型服务；重新启动后台后会校验 PID、进程创建时间、工作目录及服务身份再接管。
- 默认仅监听 127.0.0.1：ASR 8781、LLM 8782、TTS 8767。每个新进程使用独立随机凭证，凭证不会返回管理 API。
- 只使用本机已缓存模型，不自动下载。安装 `realtime_backend/requirements.txt` 和 `requirements-local.txt`，需 Apple Silicon / MLX。
- ASR、LLM 使用配置中的 MLX 模型。TTS 使用 Qwen3-TTS CustomVoice（默认推荐 `mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit`、Serena、24000 Hz）。TTS 其他模型架构和采样率不支持。
- 原有 MLX、macOS 与 HTTP 配置继续兼容；HTTP 服务由外部管理。切换提供商前需先停止仍在运行的内置服务。
- 服务日志和私有身份记录位于后台数据目录的 `services/`。加载失败、进程异常退出和端口冲突会反映在状态中。端口冲突不会结束或接管未知程序。
- 可识别并接管同目录旧版 `qwen-tts.pid` 指向的 Qwen TTS 进程；首次重启后替换为带随机鉴权的服务进程。

## 管理 API

沿用后台现有管理员认证及同源限制，不在设备专用监听端口暴露。

- `GET /api/services`：三项状态数组；不包含服务令牌。
- `POST /api/services/{asr|llm|tts}/{start|stop|restart}`：返回操作后的状态。启动为异步加载，需要轮询到 `running`。
- 不支持的操作、外部服务控制、端口冲突返回 HTTP 409 和 `error.code` / `error.message`。
- 更换正在运行的模型后显示 `needs_restart`，推理返回 `service_needs_restart`，重启后使用新模型。

## 验证

在 `realtime_backend` 目录执行 `python -m unittest discover -s tests -q`。覆盖真实子进程生命周期、后台重建后的接管、端口冲突、异常退出、鉴权、配置变更、模型工作进程协议及现有网关/音频/MQTT 功能。模型工作进程的协议测试使用注入引擎；实际模型加载与推理须另做本机 smoke 验证。

真实缓存模型的完整验证：`PYTHONPATH=. python scripts/smoke_managed_services.py`。使用临时数据目录及 18781 / 18782 / 18767 端口，运行 LLM 回复、TTS 合成、ASR 识别，再逐项重启并停止；最后清理临时进程。
