# 来源与许可范围

本项目的原创后端、底座控制与新增逻辑按根目录MIT许可发布。猫头工程包含
来自既有小智/喵伴固件的派生代码，保留firmware/head/LICENSE的原始版权声明；
根目录许可不删除或替代它们的权利声明。

| 来源 | 本项目如何使用 |
|---|---|
| [xiaozhi-echoear-taibai](https://gitee.com/liunix61/xiaozhi-echoear-taibai) | 猫头板级集成基础，原工程LICENSE为MIT |
| [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) | 固件和协议生态，保留原版权说明 |
| [MLX Audio](https://github.com/Blaizzy/mlx-audio)、[MLX LM](https://github.com/ml-explore/mlx-lm) | 作为外部Python依赖，不内嵌其源码或模型 |
| [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-0.6B)、[Qwen3-4B](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507)、[Qwen3-TTS](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice) | 官方模型卡标注Apache-2.0；模型和MLX转换权重不在包中，下载时核对对应仓库许可 |
| [Silero VAD](https://github.com/snakers4/silero-vad) | 从官方固定提交另行下载ONNX，模型不在包中 |
| ESP-IDF、ESP-SR、codec组件 | 构建时由上游工程获取，按各组件许可使用；不打包managed_components |

不打包原厂Flash、NVS、模型权重、私有数据和依赖缓存。源码中的 Bosch BMI270
驱动保留其 BSD-3-Clause 文件头；组件管理器下载的组件仍适用各自许可证。
字体、表情、ESP-SR 模型从对应组件获取，不被根目录 MIT 重新许可。

猫头是本地已运行工程的源码快照，保留核心和 EchoEar/common 板级源码；其他板型不在
支持范围。原目录没有 Git 元数据，不能证明其与某个上游提交的精确补丁关系。
公开 fork 的参考提交 7f8b1bf25d3b4ac6611f73a62d03b7155d1646c1 仅供来源定位。
本次快照的逐文件哈希见 SOURCE_MANIFEST.json。

提示音准备脚本使用小智 v2.0.5 对应提交
860d12a12c7df9e933ff710b6f9061bf58d61ab2，逐文件验证 SHA256；资源本体不提交。
它不是猫头源码的已验证基线。下载的资源按其来源许可使用。
默认生成资源不保证与现有“小宝同学”唤醒词和眼睛动画一致，保留设备原有资源需单独核对。

许可证核对记录截至 2026-10-25。

MQTT 开发分支另使用外部 Mosquitto Broker、aiomqtt 和 cryptography，适用各自许可；不内嵌其源码或二进制。

浏览器 EchoEar 升级器本地打包 Espressif `esptool-js` 0.7.0（Apache-2.0），其 bundle 包含 pako 2.1.0（MIT AND Zlib）。原始许可及固定下载来源、完整性值位于 `realtime_backend/voicehub/static/vendor/esptool-js-0.7.0/`。页面不从 CDN 加载刷写脚本。
