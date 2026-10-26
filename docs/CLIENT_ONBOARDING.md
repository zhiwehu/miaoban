# 客户端接入

「客户端接入」面向普通用户：选择客户端、准备并连接、检查结果。IP、端口和 MQTT 参数默认收在高级设置里，历史记录也可展开查看。

已验证客户端：网页（Chrome + AirPods）和 EchoEar ESP32-S3 / 16 MB 猫头。硬件支持小智 WebSocket 与 MQTT + UDP；不把其他板型标为已验证。客户端目录在 `voicehub/clients.py`，新增客户端应登记型号、安装说明和连接步骤，并经过验证再加入。

## 使用流程

1. 选择客户端。网页无需安装固件，可以直接打开网页聊天并允许麦克风。硬件需匹配型号的应用固件。
2. 按向导连接。设备服务启动后可下载连接配置，填入对应设备；下载不会自动改写或刷写设备。
3. 设备在线后显示名称、状态和实际协议。MQTT 会话提供音频收发与丢包计数。
4. 点击会话的对话记录或日志入口。日志按会话筛选，可以清除筛选；离线设备也可从最近 20 个会话中查找。

## 管理员设置

- 网页始终使用当前后台 `/ws`。硬件设备专用监听可配置私有 IPv4 / 端口并单独启停，不开放管理路由。
- MQTT 网关连接已有 Broker，不管理 Broker 进程。首次配置账号、ACL 和设备配网令牌参阅 MQTT_UDP.md。
- 默认发现数据目录下的 `mqtt/config.json`，兼容显式 `--mqtt-config`。状态接口不返回密码或配网令牌。
- 修改地址或设备启用状态前需先停止对应服务。禁用的 MQTT 设备无法建立新会话或获取配网配置。
- 网卡地址显示在高级设置中，旧 UDP 地址不在当前机器时会提示。启动失败不影响管理台。
- 启停偏好与设备监听地址保存在数据目录 `connections.json`，后台重启后恢复。首次部署默认不开放设备监听或 MQTT 网关。
- 停止设备 WebSocket 监听只断开该监听上的设备；不影响网页和 MQTT。停止 MQTT 会结束其设备会话。
- 连接配置下载含设备访问凭据，仅管理员可获取并分发给对应设备。不要公开下载文件。

## 固件发布

当前源码仓库不附带私有固件、NVS、模型或原厂资源。未安装固件包时页面明确说明，并保留构建说明；本版不实现浏览器自动刷写。

部署管理员可放置以下文件：

```
<数据目录>/firmware/echoear-s3/application.bin
<数据目录>/firmware/echoear-s3/manifest.json
```

清单字段：`board` 为 `echoear-s3`，`kind` 为 `application`，`file` 为 `application.bin`，`version` 为发布版本，`sha256` 为应用文件实际 SHA256。

固定型号、文件名、大小范围和校验值匹配后才提供管理员下载。管理员应另行确认芯片、板型、分区、资源兼容，且固件不含私有编译配置或凭据。校验和只证明文件一致性，不等同于签名或兼容认证。

## 管理接口

- `GET /api/connections`：客户端目录、非秘密配置、在线协议、本机地址和最近会话。
- `PUT /api/connections/websocket`：`host` / `port`。
- `PUT /api/connections/mqtt`：`broker_host` / `broker_port` / `udp_host` / `udp_port` / `devices`。设备字段只接受现有设备 ID 到布尔值的映射，不新增账号或修改凭据。
- `POST /api/connections/{websocket|mqtt}/{start|stop|restart}`：启停接入服务。
- `GET /api/clients/echoear-s3/connection?protocol=xiaozhi_websocket`：显式下载含设备访问令牌的连接配置。
- `GET /api/clients/echoear-s3/connection?protocol=mqtt_udp&device=<id>`：下载某个已启用设备的独立配网入口，需设备监听和 MQTT 网关就绪。
- `GET /api/clients/echoear-s3/firmware`：下载清单校验通过的应用文件。

上述接口沿用管理员认证和同源写操作限制，不在设备专用监听提供。
