# USB 一体化接入

用户授权：电脑通过 USB 给兼容 EchoEar 写入 Wi-Fi 和当前 VoiceHub 连接信息，自动准备设备监听，随后检查上线与语音；模型页移除重复 MCP 配置。

模块与顺序：MCP 配置归位 → 管理端自动准备设备监听 → 浏览器 NVS 保留式修改 → USB 备份/写入/读回/重启 → 上线与语音验证。

## 接口与行为

- POST /api/clients/echoear-s3/prepare，JSON {} 或 {host:本机网卡地址}；复用管理员认证和同源检查，选择当前物理局域网网卡并幂等启动设备专用监听。多个无法确定的网卡返回可选择网卡；不开放管理台，不替换正在工作的其他监听。
- 返回当前设备 OTA/WebSocket 配置，no-store；Wi-Fi 密码不发到后端、不存浏览器持久存储、不写日志。
- 用户 USB 识别通过后填写 SSID/密码，点击保存设置备份并连接。必须保存并重新读取校验本次完整原机 Flash 备份（16/32 MB，含原应用、设置与资源），再从当前设备重新读取比对，才写入准确 NVS 分区；读回 SHA256 核对后复位，失败保留恢复入口。
- 浏览器保留非目标 NVS 原始记录；更新 wifi/ssid,password,ota_url 及 websocket/url,token,version，移除旧 mqtt endpoint 和多余 Wi-Fi 槽以保证目标网络；不写应用/分区表/资源区，不跨设备恢复，不自动刷写未经实机验收的新应用。
- 支持已通过现有芯片、安全配置、精确分区及固件指纹校验的设备。损坏/加密/未知 NVS、空间不足和未知固件明确失败，不清空设备设置。
- 选取浏览器本机 VoiceHub 使用；远端电脑托管和 AP 客户端隔离需明确说明，不能把写入成功等同设备已上线。
- MCP 城市/新闻源/数量放在工具卡，规划器/超时放在 MCP 页面全局设置。模型页 GET/PUT /api/config/models 仅操作模型、音频、唤醒、提示词及记录保留；不读取或提交 MCP 字段。MCP 使用 /api/config/tools，按工具卡保存设置。

## 验证

Python unittest 覆盖管理员/跨域、网卡选择、端口冲突、监听幂等和设备 OTA 鉴权。
Node 覆盖 NVS CRC/字符串/多页/无空间、非目标原始记录保留、MAC/快照变化拦截、只写 NVS、读回失败及备份恢复。
生成合成 NVS，使用本机 ESP-IDF 官方工具独立校验修改后分区。浏览器检查流程、模型页与 MCP 页以及窄屏布局。实机写入由用户点击连接完成，记录上线与播放证据。

命令：在 realtime_backend 下运行 python -m unittest discover -s tests -q；node --test tests/*.mjs tests/*.cjs；node --check voicehub/static/echoear-installer.js。
沿用 Python/aiohttp 和原生 JS，不增加依赖。数据备份不提交仓库，文档不得包含用户凭据。
