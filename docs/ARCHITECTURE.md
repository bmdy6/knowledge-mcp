# 架构

Markdown 是唯一知识源，MCP 提供按 scope 的检索和维护接口，Git 由维护者管理版本与恢复。

## 边界

- 程序代码和知识内容分开：启动时用 `--vault <路径>` 或 `KNOWLEDGE_VAULT` 指定知识库，无默认目录。
- `personal/` 与 `company/` 分别承载两个 scope；每次检索、读取和写入都显式选择 scope。
- `archive/` 保存历史材料，默认不参与检索；显式 `include_archive=true` 才能读取，仍按文档声明的 scope 过滤。归档文档应声明 scope，未声明的旧归档可在两个 scope 的显式归档查询中出现。
- 文档写入只允许 `personal/` 或 `company/` 内的 Markdown 文件，不写归档；根目录的隐藏 `.knowledge-mcp.lock` 仅用于本地跨进程写入互斥，不保存知识内容。
- 检索返回文件路径、真实行号和原文片段。排序使用 BM25、标题和技术词权重，不需要向量服务。
- 新文档固定为 `status: review`；状态不能代替读者对原文的核实。

## 文件职责

```text
src/knowledge_mcp/  MCP 入口、文件操作、排序、只读资源
tests/             单元测试与 stdio 协议测试
docs/              架构、内容模型、客户端接入与写入契约
skills/            检索、维护、写作的通用操作说明
examples/vault/    完全合成的示例知识库
```

通用说明随安装包分发；`knowledge://guide` 读取本项目的 MCP 契约，其他 guide / skill 资源读取本项目实际携带的文件。两个 scope 概览分别读取配置知识库中的 `personal/README.md` 与 `company/README.md`。

## 调用路径

```text
客户端 -> stdio MCP -> 显式配置的 Vault
search -> read_range -> 核实原文
read -> show_diff -> 获得授权 -> apply_change -> 回读 -> validate_vault
新主题 -> create_document -> 回读 -> validate_vault
```

路径与 scope 由文件操作层校验；局部修改要求 `expected_text` 在当前文件里恰好出现一次。预览不写文件，写入时重新检查。删除、移动及合并应先获得对应授权。

MCP 不自动提交、推送或合并。MCP 不可用时，维护者仍可使用普通文件工具、Git 和测试恢复内容。本轮不包含内容迁移、阅读应用、外部平台同步或自动发布。
