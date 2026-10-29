# VoiceHub 后端

完整安装、模型准备、设备连接与限制见 [项目说明](../README.md)。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-local.txt
VOICEHUB_PYTHON="$PWD/.venv/bin/python" ./run.sh
```

默认管理地址：http://127.0.0.1:8765/ 。模型、adapter、凭据与运行数据不随源码发布。

- [MCP 与 Rex 准备](../docs/MCP.md)
- [音乐与用户上传扩展](docs/SPEC-mcp-music-extensions.md)
- [远程 Streamable HTTP](docs/SPEC-mcp-http.md)

回归测试：

```sh
.venv/bin/python -m unittest discover -s tests -q
node --test tests/test_mcp_calls.mjs
```

### 动态任务链和独立邮件 MCP

MCP 页面支持自然语言任务规划、受控文件生成、SMTP 配置和逐次发送确认。独立邮件 MCP 提供草稿、状态和发送接口；授权码不进入模型上下文。使用方式、支持格式与限制见 [任务链与邮件 MCP](docs/MCP-email-workflows.md)。

### Skills 与后台执行

在 Skills 技能页配置常用任务模板。语音调用先回应，任务在后台执行；插话不取消 MCP 或技能，完成后在原会话空闲时自动播报。参数、步骤、发送确认由主机校验。使用方式与限制见 [Skills](docs/SKILLS.md)。
