# MQTT + UDP 接入与对比测试

## 接口合同

复用现有设备会话、模型管线、播放准备确认及控制台。WebSocket 为默认入口；MQTT
通过可选参数启用，Broker 使用 Mosquitto，MQTT 控制与 UDP 音频在同一会话中关联。
仅实现本仓库的小智 MQTT v3 / Opus 16kHz 单声道、60ms 帧。

每个已配置设备仅可发布 `voicehub/<id>/up`、订阅 `voicehub/<id>/down`。
Broker 密码与 ACL 负责设备认证和隔离；后端使用独立服务账号。拒绝 retained 控制消息。
每次 hello 分配新的 AES-128 key 与 SSRC，收到有效上行 UDP 后固定发送端地址。
校验包长、方向、会话标识和递增序列号；丢包继续，重复/乱序丢弃；队列有上限。
上下行使用不同 flags，避免相同 key/counter 重复使用。关闭、取消、掉线和重连均释放旧会话。

原协议 AES-CTR 没有 MAC，无法提供报文完整性；LAN 测试不等于适合直接开放公网。
当前板级 MQTT 客户端只在 8883 使用 TLS/公共证书根；1883 为受信任局域网测试入口，
控制消息和会话密钥可被同网段监听。不能把加密 UDP 描述成完整的安全传输。

## 启动与配网

默认保持 WebSocket，不启用 MQTT。Apple Silicon 上可使用 Homebrew 安装 Broker：

```sh
brew install mosquitto
cd realtime_backend
.venv/bin/python -m pip install -r requirements-mqtt.txt
.venv/bin/python scripts/setup_mqtt.py --host 192.168.0.10 --device YOUR_DEVICE_ID --device benchmark
mosquitto -c data/mqtt/mosquitto.conf
# 另一个终端：
./run.sh --device-host 192.168.0.10 --device-port 8766 --mqtt-config data/mqtt/config.json
```

IP 必须替换为自己的 Mac 局域网地址，设备 ID 应与固件 OTA 请求的 Device-Id 一致。
生成目录为 0700，凭据为 0600。已有目录拒绝覆盖，保留密码哈希、ACL 与独立配网令牌。
首次运行不自动修改设备。不要把 config.json、URL、NVS 或 Broker 日志提交到仓库。
Broker 和后端使用不同账号；每个设备都有独立账号和 Topic ACL。

新配网入口只在设备专用 HTTP 监听开放：
`POST /xiaozhi/mqtt-ota/<PER_DEVICE_PROVISION_TOKEN>/`，还必须匹配 Device-Id。
令牌从私有 config.json 中取得，不写进公开文档。共享 WebSocket 令牌不能获取 MQTT 凭据；
原来的 WebSocket OTA 路由行为保持不变。先部署新的猫头应用，再按自己的 NVS 配置方式
设置新入口。Broker 的 MQTT 控制端口为 TCP 1883，音频为 UDP 8884；仅用于受信任 LAN。

猫头应用增加 subscribe_topic 的显式订阅和 playback_ready 能力；后端等待 ready 才发送 UDP。
不刷入原厂资源或整片擦除。不需要为这项功能更新底座固件。
回退时还原自己的 WebSocket OTA 地址并重启猫头；未修改的 WebSocket 地址/令牌可继续使用。

`/api/status` 增加 mqtt_ready。断线后关闭旧音频会话并自动重连 Broker；新 hello 使用新密钥。
`transport.closed` 事件记录收发包、上行缺包和队列丢包，均不含密钥或音频正文。

## 测速工具

```sh
.venv/bin/python scripts/benchmark_transports.py \
  --mqtt-config data/mqtt/config.json --device benchmark \
  --ws-url ws://192.168.0.10:8766/xiaozhi/v1/ --rounds 10
# 另测同一组模型的文字到首个音频包；不包含 ASR 和扬声器：
.venv/bin/python scripts/benchmark_transports.py \
  --mqtt-config data/mqtt/config.json --device benchmark \
  --rounds 5 --prompt '请只回答两个字：你好。'
```

工具只模拟设备：ready 回执不是物理播放证据。首轮预热排除，之后交错交换测试顺序。
MQTT hello 使用已连接的 Broker 通道，WebSocket hello 包含新建 TCP 连接；二者有意反映
现有设备的连接方式。控制往返和模型首包必须分开解读。

## 2026-10-25 软件测量

M4 Max/128GB，同一 Mac 上的两个软件客户端，经相同局域网地址访问后端。
10 次连接、各 100 次控制往返（首轮预热排除）：

| 指标，中位数 | WebSocket | MQTT＋UDP |
|---|---:|---:|
| hello 握手 | 3.11ms | 1.47ms |
| 控制 ping/pong 往返 | 0.57ms | 1.47ms |

另一组相同本地模型、固定文字输入、每协议 5 次的交错测试：首个音频包中位数分别约
5.44 秒和 5.08 秒。该组 WebSocket 经回环地址、MQTT 经本机局域网地址，且模型输出/推理
有波动；差值不能归因为协议收益。两条路径共享同一模型实例与配置。

以上是软件客户端测量。以下为另行完成的喵伴实机联调。

## 喵伴实机对照

同一猫头应用、相同后端与模型，在两种协议间只切换本地配网入口。
原唤醒词、表情资源、分区和底座固件保留。用户确认两种协议各两轮有声音，
包括等待约20秒后的继续对话；主观评价为“差别不明显”。

| 观测 | MQTT＋UDP | WebSocket |
|---|---:|---:|
| 记录轮次 | 5 | 3 |
| 接收 / 解码 / 播放包数 | 510 / 510 / 510 | 58 / 58 / 58 |
| 设备状态丢包 | 0 | 0 |
| 暖机后完整语句送入处理至首个音频包发出 | 520–845ms（4轮） | 454–2801ms（3轮） |
| 播放准备往返 | 22–30ms（4轮） | 13–83ms（3轮） |

MQTT 第一轮冷启动约4457ms，其中 ASR 约2368ms、首次输出准备约1065ms，未混入暖机范围。
“完整语句送入处理”从服务端 VAD 分句后的 Turn 开始计时，不包括 VAD 静音等待或实际
扬声器的声学延迟。首包指标通过相同的 transport.first_audio 事件记录，覆盖 Opus 尾帧。

本次问题与回复长度、模型推理耗时不同，识别结果中没有跨协议完全匹配的输入组；因此
这些是联调观测，不能用两组范围或均值直接归因于传输协议。两者声音正常、听感无明显
速度差异，本次未验证出稳定提速。对照结束后选择恢复 MQTT，WebSocket 入口继续保留。

当前 MQTT 只完成本设备与本局域网验证，仍需更长时间、多设备及不同网络条件测试。

## 验证顺序

1. UDP 帧校验与序列、方向隔离单元测试。
2. 使用真实 Broker 测试认证、Topic ACL、会话共用、ready 后出包、goodbye 与重连。
3. 构建猫头应用，只更新应用并保持设备资源与配置可回退。
4. 同一硬件、同一网络、相同模型、相同短句；预热后交错比较两种传输。
5. 分别记录 hello、回复准备到 ready、首个音频包、接收/解码/播放计数、丢包。
   用户说完到实际扬声器出声须实机复听/测量，不能用模型首包冒充。

未完成的实机与性能项不标记为已验收；不能预设 MQTT 比 WebSocket 更快。

## 依赖资料

- [Mosquitto 配置、密码与 ACL](https://mosquitto.org/man/mosquitto-conf-5.html)
- [aiomqtt 连接生命周期](https://aiomqtt.bo3hm.com/connecting-to-the-broker)
- [cryptography：AES-CTR 与完整性限制](https://cryptography.io/en/latest/hazmat/primitives/symmetric-encryption/)
