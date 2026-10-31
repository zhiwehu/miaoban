# 实时语音后端：模块边界

状态：用户于 2026-10-24 确认模块划分，并明确要求独立重新开发。
当前实现位于 `realtime_backend/`，使用与验证说明见 `realtime_backend/README.md`。

目标：Mac 上可长期运行的设备无关语音服务。ESP32、网页、App 和其他设备通过
WebSocket 接入，模型可配置，支持自然插话、可选唤醒、对话记录和诊断界面。

| 模块 ID | 职责 | 依赖 |
|---|---|---|
| model-adapters | ASR/LLM/TTS 及端到端语音模型接口、能力声明和取消 | — |
| conversation-store | 会话、轮次、消息和结构化诊断事件的持久化与查询 | — |
| realtime-session | 会话状态机、流式处理、音频队列、打断和资源调度 | model-adapters, conversation-store |
| wake-policy | 设备唤醒事件、服务端唤醒检测及手动激活的统一策略 | realtime-session |
| client-gateway | 通用 WebSocket 协议、小智协议适配、认证与连接生命周期 | realtime-session, wake-policy |
| management-console | 配置、在线设备、对话和日志的本地 Web 控制台 | client-gateway, conversation-store |

按可运行的纵向切片交付，不先重写全部模块：

1. 现有模型与小智协议接入独立会话核心，记录轮次与取消事件。
2. 验证设备持续采音/AEC，完成自然插话和旧音频清除。
3. 增加持久配置、远程模型适配和对话/日志存储。
4. 实现 Mac 本地控制台及通用 WebSocket 网页测试客户端。
5. 加入可选后端唤醒，完成跨客户端和长时间运行验证。

现有固件、备份和运行服务保留；固件改动、刷写和服务切换单独记录。
第一版不承诺 Grok Bot 私有会话接入、手机原生 App、独立 Mac 安装包或多机集群。
端到端语音先定义适配边界；具体提供商须有实际接口与联调后才标记支持。

规格索引：

- realtime-session：[SPEC-realtime-session.md](SPEC-realtime-session.md)
- 其他模块：按交付顺序补充各自规格，接口须与会话核心契约一致。
