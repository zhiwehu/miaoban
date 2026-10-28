# MCP 意图 LoRA 训练

目标：为 RexUniNLU 基础模型训练原生分类 adapter，输出 null / weather / news /
system_info。仅分类，不执行工具，不自动替换线上路由。多工具、ASR 音频和开放参数
提取不在第一版训练范围。缺少城市仍属于天气意图，参数提取器须询问城市后才执行。

设计：rank 16、alpha 32、dropout 0.05；冻结全部基础权重，对 12 层注意力 Q/K/V
及原 Rex ffnq/ffnk 注入 LoRA。保留原 `[CLASSIFY]` 输入、schema、全矩阵 circle loss
和官方 prediction_step。保存低秩张量及配置，不复制或改写原权重。
导出后验证独立重新加载和合并权重的预测一致性。

数据：本地编写的合成请求，训练/验证/测试句式组分离，保存模板来源和样本标识。
现有 34 条比较样例只作最终回归，不参与训练/早停。测试集在确定训练配置后评估，
不得根据测试错误回填本轮训练。合成集成绩不能作为真实用户流量准确率。

第一版验收目标（事先记录）：独立合成测试准确率 ≥90%，无需工具误触发率 ≤5%。
达不到时仍交付权重和真实结果，不降低指标或宣称生产可用。使用验证集选 checkpoint。

验证：`python -m unittest training.test_lora -v`；训练和评测通过 scripts 下 CLI。
运行环境继续使用 data/rex-runtime 中的兼容依赖；基础模型 SHA256 保持与前次一致。

方法依据：[LoRA 原论文](https://arxiv.org/abs/2106.09685)，
[Rex 官方模型](https://modelscope.cn/models/iic/nlp_deberta_rex-uninlu_chinese-base)。
