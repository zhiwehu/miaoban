# 根目录工具链（逆向与实机调试）

仓库根目录下的 Python 脚本是**喵伴逆向和实机调试时用的工具**，不是日常运行入口。
日常使用只需要 `realtime_backend/`（声桥 VoiceHub）。

这些脚本保留下来是为了让每个结论都可以复现：读固件、改 NVS、抓串口、驱动电机。
它们是按当时的环境写的，路径、端口和串口名都针对这台 Mac，换机器需要改。

## 旧后端（已被声桥取代）

| 脚本 | 用途 |
|---|---|
| `xiaozhi_server.py` | 早期「假小智云端」：8098 控制 API + 8099 设备 WebSocket，曾长期作为猫头后端 |
| `voice_brain.py` | 上述后端里的本地 ASR/LLM/TTS 管线 |
| `voice_loop.py` | 更早的语音主循环（含能量 VAD、流式重采样） |
| `streaming_reply.py` | 按句切分、流式合成与播放的早期实现 |
| `auto_reply.py` | 最小可用的自动应答 |
| `restart_backend.py`、`start.sh` | 旧后端的重启与启动包装 |

声桥接管后这套代码只用于对照；它与 `realtime_backend/` 不共享运行时。

## 设备与串口

| 脚本 | 用途 |
|---|---|
| `device_ports.py` | 按 USB 串口和 MAC 识别猫头/底座，返回 `/dev/cu.*` |
| `switch_wifi.py` | 把 Mac 当前 SSID 与指定密码写入猫头 NVS，并设置 `ota_url`（BSON 格式，不是明文 NVS） |
| `nvs_edit.py` | 读写 NVS 分区条目（用于核对 `wifi` 命名空间） |
| `watch_connect.py` | 串口连接监视 |

## JTAG 与固件

| 脚本 | 用途 |
|---|---|
| `jt.py` | OpenOCD JTAG 封装：halt / 读寄存器 / 写内存 / resume |
| `bootstack.py` | 进入下载模式与串口引导相关操作 |

底座舵机 PWM、`duty` 写入和越位判定都用 `jt.py` 直接操作寄存器验证过；
结论写在根目录 `README.md`（本仓库为 `docs/ARTICLE.zh-CN.md`）。

## 表情、动作与资源

| 脚本 | 用途 |
|---|---|
| `miaoban.py` | 早期整体实验脚本：串联串口、表情和语音 |
| `motor_demo.py`、`motor_test.py` | 底座电机手动测试 |
| `dialogue_expression.py` | 从回复文本解析出表情/动作标记 |
| `make_face_assets.py` | 生成表情资源（`.eaf`） |
| `configure_wake_assets.py` | 打包/解包唤醒词资源 |

## 语音合成服务

| 脚本 | 用途 |
|---|---|
| `voicestudio_client.py` | 本地 VoiceStudio HTTP 客户端（API Key 由调用方传入，不写死） |
| `check_voicestudio.py` | 服务可用性检查 |

## MCP 配置示例

`amap.json`、`amap-official.json`、`12306-mcp.json`、`filesystem.json` 是网页
「MCP 工具 → 上传配置」的例子，只用占位符，不含真实密钥。高德的 Key 需要替换成
你自己的。

## 测试

`tests/` 下的 `.py` 覆盖上面这些工具（NVS 编码、串口识别、唤醒词切分、表达式解析等），
`.cc` 覆盖固件侧的纯逻辑。运行 Python 测试：

```sh
python -m pytest tests -q          # 需要在仓库根目录执行，CWD 会进入 sys.path
```

`tests/test_nvs_exact.py` 依赖一个已删除的 `nvs_exact` 模块，没有收录。
