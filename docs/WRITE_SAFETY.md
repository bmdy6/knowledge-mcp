# 写入安全

## 写入前

1. 读取目标文件；
2. 确认 scope 和路径一致；
3. 用原文片段作为版本条件；
4. 生成 diff；
5. 确认后调用 `apply_change`（新主题才用 `create_document`，合并旧文件写进 `replaces`）。

## 写入时

- 文档只操作 `personal/` 或 `company/`，根目录的隐藏 `.knowledge-mcp.lock` 只用于写入互斥；
- 禁止路径穿越；
- 禁止默认写入 `archive/`；
- 新文章不能覆盖已有文件；
- 新文章固定写 `status: review`，创建接口不接受 `status` 参数；
- 同一条来源在同一个 scope 里只能被声明一次，重复导入直接拒绝，不静默生成副本；
- 期望原文必须只出现一次；
- 采用 UTF-8 写回；
- 保留文件其他内容；
- 不自动执行 Git commit、push 或合并。

所有 MCP 写入共用本地跨进程锁，覆盖读取、校验和文件提交，异常或进程退出会释放。等待超过 10 秒会返回 `VaultBusyError`；不要手动删除锁文件以重试。它仅协调使用本协议的 MCP 进程，不阻止外部编辑器、Git 或云盘直接改文件，网络文件系统未验证。多文件合并也不是可回滚事务；使用其他写入工具前暂停 MCP 写入，并保留备份。详细行为见 [MCP 契约](MCP_CONTRACT.md)。

## 写入后

- 立即重新读取目标片段；
- 运行 Markdown 结构校验；
- 检查 `git diff --check`；
- 由人审阅 diff；
- 写入后由 Git 提交留痕，写错用 `git revert` 撤回；确认发生在使用时，AI 指出位置、人回原文核实。

## 必须保留的绕行路径

当 MCP 不可用时，维护者仍可以：

```bash
git diff
git restore --staged .
uv run pytest -q
```

这些不是日常入口，但它们是恢复能力。不能为了“统一”而删掉。
