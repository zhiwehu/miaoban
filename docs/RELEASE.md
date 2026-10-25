# 源码发布记录 · 2026-10-25

仓库：[zhiwehu/miaoban](https://github.com/zhiwehu/miaoban)。
本次为 0.1 源码发布，包含本地后端、网页应用、EchoEar 猫头工程及 ESP32-C3 底座源码。
文章见 ARTICLE.zh-CN.md；构建说明见 ../firmware/README.md。

- 个人运行目录与发布源码分离，启动脚本使用自己的 Python 环境。
- 不包含真实对话、日志、令牌、NVS、设备 Flash、模型和原厂资源。
- 后端 49 项测试、C++ 9 项主机逻辑测试；具体结果与构建记录见 VALIDATION.md。
- 猫头按当前本地工程整理为快照，不虚构上游 Git 历史。
- 模型、提示音和 ESP-IDF 组件另行下载，来源与许可分别说明。
- SOURCE_MANIFEST.json 记录发布文件哈希，不代表逐位可复现固件。

尚未完成的真实外放自然插话、唤醒后跟随状态矩阵、多设备与长时间稳定性验证，
均保留为已知边界。现有实机固件可用不等于任意硬件均可直接刷写。
