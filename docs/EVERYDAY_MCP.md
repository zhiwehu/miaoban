# 日常 MCP 与 Rex 候选训练

新增三个独立工具，默认停用，配置与测试位于对应 MCP 卡片。Skills 继续暂停。

| MCP | action | 参数 |
| --- | --- | --- |
| shopping_list | add / list / complete / remove | items：1–10 个商品原文，保留数量；list_name 可选 |
| timer | start / status / pause / resume / cancel | name 可选；start 必须有 duration_seconds（1–86400） |
| item_memory | remember / recall / forget / list | remember 需要 item 与 location；recall/forget 需要 item |

例子：“把两盒牛奶加入购物清单”“牛奶买好了，标记一下”“给泡面倒计时五分钟”“记住护照在书房抽屉”“护照放哪了”。

购物清单和位置记录保存在本机，供本工作台共享。位置只按明确请求记录，查不到不能补写；忘记记录会清除存储的位置文本。批量条目有歧义时整个操作不执行，要求说明完整名称。清单支持重新购买已经标记买好的商品。

计时器绑定语音设备，最多同时 20 个。页面创建的计时器只在后台任务结果提示。暂停保留剩余时长；恢复后重新计算截止时间。计时器与语音轮次生命周期分离，插话不取消，到点在会话安静后播报，被打断会重播。主机需要持续运行；重启或设备重连后最多补播过去 5 分钟的到点提醒。超出补播窗口可查询状态，不承诺关机时响铃。

## 决策与参数

候选 v4 使用同一份 Rex + rank-16 LoRA 分两次分类：7 类工具（含 null）与新工具的 13 个动作。运行时 Rex 选择工具，辅助动作分类只做评测；对话模型提取固定动作及对应字段，不能由 Rex 动作分类直接授权写操作。主机检查 JSON 模式和原文依据，时长由程序换算，不依赖模型做算术。缺少必要参数追问，工具失败不声称成功。旧 adapter 仍兼容，候选不会在训练时自动替换线上模型。运行新候选时，天气、新闻、系统信息的参数抽取继续使用已验证的 v2 adapter；同一份基础模型只切换小型 LoRA 参数，不额外加载一份基础权重，避免新任务训练影响旧工具参数。

识别为日常工具请求后先播报收到，再执行提取和 MCP；完成由原有后台任务机制播报与记录。Rex 未确定且文本包含日常工具线索时，允许一次限定在这三个已启用工具内的模型补充决策；普通聊天不因此转入后台任务。

## 训练步骤

先按 [MCP.md](MCP.md) 准备 Rex 基础权重、离线依赖及 v2 adapter。旧训练集需依次由 build_router_training_data.py、build_router_slot_data.py、build_router_v3_data.py 生成。命令从 realtime_backend 目录执行，依赖本机 PyTorch 和 safetensors：

```sh
python scripts/build_everyday_router_data.py
python scripts/train_everyday_rex.py --smoke
python scripts/train_everyday_rex.py
# 若验证显示表达覆盖不足，只扩充训练集；冻结验证/测试文件不重写内容
python scripts/expand_everyday_router_data.py
python scripts/train_everyday_rex.py --data data/training/router-everyday-v4b \
  --initial data/adapters/rex-everyday-v4 --output data/adapters/rex-everyday-v4b \
  --epochs 2 --learning-rate 0.00003
python scripts/evaluate_everyday_rex.py --adapter data/adapters/rex-everyday-v4b
```

v4 训练 6,462 条、验证 1,050 条、测试 853 条。v4b 将训练扩为 8,550 条；验证与测试 SHA256 不变。另有 63 条手工挑战用例，以及原有 v2/v3 工具回归集。训练与验证按表达模板及实体划分；这些仍是合成数据，不能推断真实用户准确率。独立测试只在候选选定后执行，不用于挑 epoch。训练集生成器、训练与评测代码可公开，权重、用户记录及本地设置不提交。

具体评测结果在训练完成后另行记录；仅通过单元测试不代表模型可切换。初期训练日志的验证曾未关闭 dropout，已增加回归测试修复；这些临时数字不作为发布准确率，最终统一按 CPU + eval 模式重评。

人工验收见 [2026-10-30 测试清单](MANUAL_TEST_2026-10-30.md)，覆盖页面、文字和实机语音；结果待人工填写。
