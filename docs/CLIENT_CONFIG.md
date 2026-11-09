# 客户端接入

命令通过 stdio 提供 MCP，知识库路径必须显式配置：

```bash
uvx --from . knowledge-mcp --vault examples/vault
```

这是在项目根目录使用本地包的命令；相对 vault 路径按进程当前目录解析。客户端的工作目录不确定时使用绝对路径：

```toml
[mcp_servers.knowledge]
command = "uvx"
args = ["--from", "/path/to/knowledge-mcp", "knowledge-mcp", "--vault", "/path/to/vault"]
```

也可以省略 `--vault`，设置环境变量 `KNOWLEDGE_VAULT=/path/to/vault`。两者同时提供时参数优先；都没有时退出并向 stderr 输出一行配置错误。旧变量 `KNOWLEDGE_MCP_ROOT` 不再使用。路径必须是已经存在的目录。

接入后先读取 `knowledge://guide`；检索、写作和维护再读取对应 `knowledge://*-skill`。通用资源来自安装包，scope 概览来自指定 vault，不要求 vault 内包含程序代码或 skills。不要把凭据写入客户端配置或知识示例。
