# 本地小模型决策、参数抽取与 MCP 执行

首批只读工具契约：

| 工具 | 参数 | 校验 |
|---|---|---|
| weather | city、day_offset | 城市必填；日期 0/1/2；省略日期查三天概况 |
| news | source、limit、keyword | 来源 bbc/chinanews；条数 1–5；关键词原文片段 |
| system_info | sections | 可省略；否则为 cpu/memory/disk/uptime 的非空去重列表 |

小模型在独立 CPU 子进程中执行两次原生 Rex 推理：

1. `[CLASSIFY]`＋四类 schema 决定 null/weather/news/system_info。
2. 若需工具，正文＋对应字段 schema 抽取带字符 offset 的实体。

原文片段必须与 offset 一致。日期、数量、来源别名和系统指标由固定代码归一化，
再通过 MCP 同一参数校验器；不存在任意 shell、任意 URL 或模型编造参数的执行路径。

规划结果：

```
{"status":"ready","tool":"weather","arguments":{"city":"上海","day_offset":1}}
{"status":"chat","tool":null,"arguments":{}}
{"status":"clarify","tool":"weather","arguments":{},"message":"你想查哪个城市的天气？"}
```

缺参追问不调用 MCP。天气城市可用明确的上一轮天气参数或用户配置的默认城市，
不得凭空推测位置。补充城市时保留上轮已识别日期；取消/闲聊清除本轮参数上下文。
来源不支持、数量越界或日期超出范围时追问，不静默更换来源或截断数量。

VoiceHub 工具编排器先输出 ToolNotice，经 TTS 排空后发 tool.started，再执行
MCP tools/list / tools/call。事件保存规划器、工具和已校验参数，沿用对话日志保留规则。
结果交给原 LLM 口语总结。LLM 不再参与 Rex 模式的工具选择和参数生成。

`tools.planner` 可选 llm/rex；旧配置迁移为 llm。联合 adapter 训练与验收完成后
切换实际配置为 rex，新连接生效。前端向旧服务保存时不发送其不支持的新字段。
取消请求时终止规划子进程或工具子进程，下一轮可重新加载，不输出旧响应。

独立兼容依赖、固定基础权重与 adapter 均在 data 中。后端主进程不导入 PyTorch
或旧 Transformers，不下载模型，也不将本地模型推理发送到外部服务。
首版每轮支持一个工具；多工具组合需另行定义拆分和部分失败语义。
