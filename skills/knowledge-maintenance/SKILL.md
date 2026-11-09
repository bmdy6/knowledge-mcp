# knowledge-maintenance

维护明确指定 vault 中的现有文档，先读、再 diff、再写、再验证。

```text
read / read_range
show_diff(scope, path, expected_text, replacement)
确认改动在授权范围内
apply_change(scope, path, expected_text, replacement)
read / read_range
validate_vault(scope)
```

`expected_text` 必须在当前文件恰好出现一次：没有匹配表示条件过期，多个匹配表示片段有歧义。先重读，再扩大上下文；不要去掉保护强行写。它是局部文本条件，不能检测文件其他位置的修改。

一次只改一件事，保留未匹配内容。`show_diff` 不写入，也不会保留可供以后跳过检查的锁。创建新主题用 `create_document`，固定 `status: review`，不能覆盖或写归档。

合并用 `replaces`；重命名和搬目录用 `move_document`；删除用 `delete_document`。删除的 `successor` 与合并都要求承接正文至少达到旧正文长度的 70%，这不能证明语义相同。移动不自动修复其他文档的引用。

删除、移动、批量重写或合并前确认相应授权。personal 与 company 不能跨域搬移。回读、校验并检查 Git diff；不要自动 commit、push 或合并。完整契约见 `knowledge://guide`，模型见 `knowledge://content-model`。不依赖未携带的脚本或其他 skills。
