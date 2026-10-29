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
