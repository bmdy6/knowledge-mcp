# 写入安全

## 写入前

1. 读取目标文件；
2. 确认 scope 和路径一致；
3. 用原文片段作为版本条件；
4. 生成 diff；
5. 确认后调用 `apply_change`（新主题才用 `create_document`，合并旧文件写进 `replaces`）。

## 写入时

- 只操作 `personal/` 或 `company/`；
- 禁止路径穿越；
- 禁止默认写入 `archive/`；
- 新文章不能覆盖已有文件；
- 新文章固定写 `status: review`，创建接口不接受 `status` 参数；
- 同一条来源在同一个 scope 里只能被声明一次，重复导入直接拒绝，不静默生成副本；
- 期望原文必须只出现一次；
- 采用 UTF-8 写回；
- 保留文件其他内容；
- 不自动执行 Git commit、push 或合并。

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
