# MCP 工具、Rex 决策与参数抽取

各工具分别放在 `realtime_backend/voicehub/mcp_tools/`，文件包含参数 Schema、
提示语和异步 execute 实现，注册表负责发现；common_tools.py 处理 stdio MCP 协议。
天气使用 Open-Meteo，新闻读取中新网/BBC RSS，本机信息只读 CPU、内存、磁盘和运行时长。

启动后在“模型与语音”启用工具并选择规划器，新连接生效。无需工具时继续普通聊天。
查询前先发送提示语并排空提示音频；取消会结束本轮工具子进程。网络不可达会明确反馈。
网页聊天和历史对话按轮次显示调用卡片，左侧 MCP 工具页支持查看参数和独立开关。

## 开箱配置与可选 Rex

没有准备 Rex 资源时使用默认的“对话模型规划”。选择 Rex 前必须在后端 data/ 下准备：

- `models/rex-uninlu-chinese-base/`：官方 `iic/nlp_deberta_rex-uninlu_chinese-base` 权重、
  tokenizer、config 和配套原生 rex Python 源码。基础权重 SHA256 为
  `5be51d21f54d10f2d7741b403058c8e310c42f7b994454cab5ca024aea0382d2`。
- `rex-runtime/`：隔离依赖。实测 Transformers 4.48.3、tokenizers 0.21.4、datasets 2.21.0、
  accelerate 0.34.2、einops 0.8.1、jsonlines 3.1.0；项目 Python 还需 PyTorch 和 safetensors。
  原生 Rex 源码所需 ModelScope 等依赖需一并准备。本次未提供一键下载/环境安装器。
- `adapters/rex-mcp-router-r16-slots-v2/`：联合 adapter_config.json 与 adapter_model.safetensors。
  自定义格式 miaoban-rex-lora-slots-v2，使用配套加载器，不是通用 PEFT adapter。

模型权重、官方模型源码、依赖缓存、adapter 及训练数据不随本次 Git 提交发布，需遵守各自许可自行准备。
训练数据生成器和本项目训练代码随仓库提供；不要将生产数据放入仓库。

在 realtime_backend/ 中生成合成数据并训练：

```sh
python scripts/build_router_training_data.py
python scripts/build_router_slot_data.py
python scripts/train_rex_adapter.py --joint --epochs 8 --batch-size 16 \
  --data data/training/router-slots-v2 --output data/adapters/rex-mcp-router-r16-slots-v2
python scripts/evaluate_rex_slots.py --output ../diagnostics/rex-slots-v2
```

训练默认使用 MPS；其他环境请查看脚本参数并设置 device。新工具还需要新增分类标签、抽取字段、
归一化及独立测试数据，不能只添加工具文件就让当前 adapter 自动识别。

## 验证与限制

本机联合训练：2503 条训练、277 条验证、329 条独立合成测试，按模板族分组划分。
独立测试完整计划正确率 93.31%，实体 span micro F1 98.55%，无需工具样本误路由率 16.67%。
这些是合成数据指标，不是现场语音识别和真实用户准确率。已知错误包括概念解释误路由、未见时间词和多项查询。
耗时工具前的提示顺序已验证，但首次加载、LLM/TTS 生成仍可能较慢，不保证即时语音响应。
天气源可能瞬时超时，BBC 在本地测试中持续超时；可选择中新网。

本次开源目录 Python 后端 168 项测试通过，Node 调用状态测试 3 项通过。
管理页曾在隔离 Chrome 中验证实时状态、历史恢复、开关持久化和 320–1440 像素布局。
不把静音测试音频、客户端播放回执或已生成音频等同于机器人实体扬声器/AEC 验收。

```sh
python -m unittest discover -s tests -q
node --test tests/test_mcp_calls.mjs
```

训练数据检查另行运行（先生成数据）：`python -m unittest training.test_data training.test_slot_data`。
真实 smoke 脚本需要相应模型/网络资源，输出在本地 diagnostics/，不会自动上传。

## 音乐与用户扩展

MCP 页设置后端音乐目录并启用后，可搜索、播放、暂停、继续、停止；当前输出至 Mac 默认音频设备，未将歌曲流发送给 ESP32。上传支持单个 stdio JSON 配置或包含 mcp.json 的 Python ZIP。上传不执行，明确确认以当前用户权限运行后才做真实发现验证；不自动安装依赖，不提供 OS 沙箱。页面提供可下载的无依赖回显示例和 JSON 参数测试入口。

新增外部工具由动态工具目录 LLM 决策并校验参数，原 Rex adapter 不会自动学会新分类。详见 [音乐与扩展规范](../realtime_backend/docs/SPEC-mcp-music-extensions.md)。

Streamable HTTP 现已支持地址、Bearer/自定义认证、JSON/SSE 响应、参数调用。保存后明确启用才联网验证，支持2025会话和显式2026无会话模式；不支持旧版HTTP+SSE、OAuth登录或自动重放。详见 [HTTP 接入说明](../realtime_backend/docs/SPEC-mcp-http.md)。

## 日期误路由修复与 v3 候选训练

明确日期/星期问题由本机时钟直接回答，并清除上一轮待补参数；不再交给天气分类。
新增 scripts/router_stress_cases.py 和 evaluate_router_stress.py 生成、评估扩大后的冻结边界集，
包括概念、否定、上下文与缺参。v3 数据生成器排除该冻结集和旧测试集，按模板族划分。

继续训练需先按上文准备 v1/v2 数据、模型、v2 adapter 与 Rex 运行环境，然后在 realtime_backend/ 执行：

```sh
python scripts/evaluate_router_stress.py --output ../diagnostics/2026-10-29/router-stress-v1
python scripts/build_router_v3_data.py --output data/training/router-slots-v3
python -m unittest training.test_v3_data
python scripts/train_rex_adapter.py --joint --init-adapter data/adapters/rex-mcp-router-r16-slots-v2 \
  --data data/training/router-slots-v3 --output data/adapters/rex-mcp-router-r16-slots-v3
python scripts/evaluate_router_v3.py
```

v3 是可评估的候选训练流程；本次提交没有自动切换生产 adapter，也不包含权重或本地训练/诊断产物。

完整的告别、感谢、招呼和先不说了等话语直接进入日常回复，并清空上一轮工具待补参数。与音乐无关的 Rex 闲聊不额外依赖音乐 JSON 规划；“播放《再见》”等实际请求仍走工具链路。
