# 内容模型

知识库包含 `personal/`、`company/`，以及可选的 `archive/`。每个 scope 用自己的 `README.md` 作为概览；文件名和子目录由维护者选择，不要求特定编号或个人布局。

## 文档类型

| type | 用途 |
| --- | --- |
| `article` | 一个完整主题，可在原文中持续补充 |
| `reference` | 一个外部来源的链接、关键摘录与说明 |
| `research` | 有结论及证据的调研 |
| `guide` | 可重复执行的步骤 |
| `decision` | 选择、取舍与理由 |
| `task` | 任务描述、思考、方法、结果和链接 |

`task` 正文必须包含 `## 任务描述`、`## 解决思考`、`## 解决方法`、`## 结果`、`## 链接` 五节，可额外包含其他小节；缺少时创建拒绝，校验报告 `missing-task-sections`。未记录的事实不要补造。

## Frontmatter

完整示例：

```yaml
---
title: 合成示例
type: article
status: review
scope: personal
created: 2026-01-01
updated: 2026-01-01
tags:
  - 示例
series: 示例系列
sources:
  - https://example.com/source
---
```

- `title`：非空单行标题。正文的一级标题与之保持一致。
- `type`：上表中的类型。
- `status`：`active`、`review` 或 `deprecated`；`create_document` 固定写 `review`，不接受 `status` 参数。
- `scope`：`personal` 或 `company`，应与所在目录一致。归档也应声明来源 scope。
- `created`、`updated`：`YYYY-MM-DD`，创建接口在未指定时写当天日期。导入已有文档时可显式保留原日期。
- `tags`：可选的非空字符串列表。
- `series`：可选的单行系列名。
- `sources`：可选的单行路径或 URL 列表，同一 scope 中创建文档时不允许重复认领已有来源。

`validate_vault` 检查 scope、类型、状态、标签、任务章节、编码和可读性等；旧文档缺少 scope 会产生 warning。它不是完整的编辑审阅器，也不会证明内容正确。

## 创建和维护

先搜索已有主题，有对应内容就补到原文；确为新主题才创建。调用 `create_document` 时，`content` 只传正文，不传 frontmatter 或一级标题，标题由 `title` 自动生成。代码围栏内的 `#` 不视为标题。

局部修改用 `show_diff` 和 `apply_change`，保留未匹配内容。`expected_text` 是局部版本条件，不是整个文件的版本号；完整文件作为条件才能检测整篇变化。

合并用 `replaces`，先写入目标再移除旧文档，目标正文长度须至少达到旧正文合计长度的 70%。这是防大幅丢失的长度保护，不能证明语义已保留。删除时的 `successor` 使用相同长度保护；省略它会直接删除，须由调用方先确认授权。

示例均为合成内容，见 `examples/vault/`；示例概览包含各 scope 的三篇文档链接。未验证的内容明确写为待验证，不把自动生成内容当作已掌握的知识。
