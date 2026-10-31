# router-everyday-v5 — 决策分类训练集

训练 Rex 决策分类 adapter `rex-everyday-v5` 的全量数据。七个标签：
`null` / `weather` / `news` / `system_info` / `shopping_list` / `timer` / `item_memory`。

音乐、文件整理和日程**不在**这个分类器里，它们由规则路由在分类器之前处理。

## 内容

| 文件 | 条数 | 说明 |
|---|---|---|
| `train.jsonl` | 9206 | 训练 |
| `validation.jsonl` | 1230 | 早停与 checkpoint 选择 |
| `test.jsonl` | 853 | 独立测试，与 v4b **逐字节一致**，用于跨版本比较 |
| `manifest.json` | — | 条数、标签分布、每个文件的 sha256 |

行格式：

```json
{"text": "帮我把牛奶加到购物清单", "label": "shopping_list", "action": "add",
 "family": "...", "synthetic": true, "id": "…"}
```

`label` 是决策标签；`action` 只出现在购物清单/计时器/物品位置三类上；`slots` 出现在
weather/news/system_info 上，用于联合训练实体抽取。

## 怎么生成的

```sh
cd realtime_backend
python scripts/build_router_v5_data.py \
    --base data/training/router-everyday-v4b \
    --output data/training/router-everyday-v5
```

构建器是确定性的（seed `20261031`），但 `--base` 需要 v4b，而 v4b 需要
v3、v3 需要 v1。完整再生链（每一步都用固定 seed，可复现）：

```sh
python scripts/build_router_training_data.py --output data/training/router-v1
python scripts/build_router_v3_data.py        --output data/training/router-slots-v3
python scripts/build_everyday_router_data.py  --output data/training/router-everyday-v4b
python scripts/build_router_v5_data.py        --base data/training/router-everyday-v4b \
                                             --output data/training/router-everyday-v5
```

因为中间集没有入库，**这份提交的 v5 是权威版本**；`manifest.json` 里的 sha256 用来
发现重建后的漂移。

## v5 相对 v4b 的增量

多了 **656 条硬负例**（train 656 / validation 180），全部是模板化生成的 `null` 样本，
形态来自真实对话里被误判成工具的那些句子：

- 单独出现的唤醒词（小宝 / 小宝同学 / 喵伴及其连说）
- 寒暄与在场确认（你好呀 / 哈喽 / 在吗 / 早上好）
- 评价与应答（还可以啊 / 还行吧 / 挺好的 / 一般般）
- 聊自己的状态（今天过得好吗 / 今天过得怎么样 / 我今天有点累）
- 只提到主题、没有请求（天气不错 / 看到一条新闻 / 电脑有点卡）
- 询问能力与身份（你能做什么 / 你是谁）
- 感谢、告别、上一轮话题后的承接

`test.jsonl` 未被改动，所以与 v4b 的分数可以直接比较。

## 隐私

- 本目录**全部是合成数据**：模板 + 实体词表（城市、商品、物品、地点等），不含任何
  真实用户语句。
- 真实对话日志导出的评测集**不入库**（`scripts/evaluate_rex_real_logs.py` 直接读本机
  `data/history.sqlite3`），构建器只把它当作“禁止出现在训练里”的名单。
- 模型权重与 adapter（`*.safetensors`）不入库，只提交数据与代码。

## 训练与评测

```sh
python scripts/train_everyday_rex.py --data data/training/router-everyday-v5 \
    --initial data/adapters/rex-mcp-router-r16-slots-v2 \
    --learning-rate 1e-4 --epochs 4 --output data/adapters/rex-everyday-v5

python scripts/evaluate_rex_real_logs.py                       # 当前线上 adapter
python scripts/evaluate_rex_real_logs.py --adapter data/adapters/rex-everyday-v5
```

验证集准确率 98.2%，null 误触发 0.16%；真实日志 378 轮：准确率 87.9% → 89.6%，
无工具轮次误触发 7.6% → 3.6%。
