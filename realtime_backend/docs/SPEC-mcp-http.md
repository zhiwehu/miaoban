# Streamable HTTP 接入

沿用 ExtensionManager、管理员接口、参数校验及调用卡片。HTTP 客户端→注册→界面，不改 Rex。
POST /api/mcp/remote 保存 id/name/url/headers/allow_private/protocol_version，不联网。上传 mcpServers JSON 支持 type=http 同等配置。启用明确 trust=true 后发现工具；API 只返回认证头名，秘密保存0600，不进提示/日志。界面提供 Bearer 和自定义头，暂不支持 OAuth 登录。
支持2025-03-26/06-18/11-25握手会话，显式选择2026-07-28无会话元信息。支持 JSON 和请求内 SSE、分页、取消、超时及输出限制。不自动重试调用，不实现旧 HTTP+SSE 和断线恢复。
URL禁用户信息/query/fragment；公网HTTPS，私人网络显式授权。连接解析器检查IP阻止DNS重绑定/元数据/链路本地等，禁止重定向/继承代理和Cookie/平台自身管理端口。
验收：真实localhost HTTP服务测试保存不联网、信任、发现/参数/分页/JSON/SSE、会话、认证失败、重定向、错误/超大响应、超时取消、脱敏与地址限制；浏览器保存→启用→调用→禁用及320/768/1024/1440布局。
命令：python -m unittest discover -s tests -q；node --test tests/test_mcp_calls.mjs。

## 配置样例

上传 JSON 或通过界面填写等价字段：
```json
{"mcpServers":{"my-remote":{"type":"http","url":"https://example.com/mcp","headers":{"Authorization":"Bearer REPLACE_ME"},"protocol_version":"2025-11-25"}}}
```
URL 必须是最终 MCP 端点，不是网页/API首页；禁止通过 URL query 传 Token。连接后端本机服务时添加 allow_private=true，并使用 http://127.0.0.1:端口/mcp。不要连接平台自身8765端口。
2026 模式支持参数 x-mcp-header 镜像和安全编码；不支持需要用户交互的 input_required 结果，明确报错，不声称完成。
SSE仅支持保持到最终响应的请求内流；断线不重放工具。2025会话过期时本次明确失败，下一次用户发起调用会创建新会话。
实现参考：[2025传输规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)、[2026传输规范](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)。
