# MCP 契约

本项目通过 stdio 提供下面的工具与资源。启动必须指定 `--vault <路径>` 或 `KNOWLEDGE_VAULT`；参数优先，无当前目录默认值。所有文档操作必须显式指定 `personal` 或 `company` scope。

## 工具

| 工具 | 参数 |
| --- | --- |
| `search` | `scope, query, limit=20, include_archive=false` |
| `list_documents` | `scope, prefix="", include_archive=false` |
| `validate_vault` | `scope=null, include_archive=false` |
| `read` | `scope, path, include_archive=false` |
| `read_range` | `scope, path, start_line, end_line, include_archive=false` |
| `show_diff` | `scope, path, expected_text, replacement` |
| `apply_change` | `scope, path, expected_text, replacement, replaces=null` |
| `create_document` | `scope, path, title, content, document_type="article", tags=null, series=null, sources=null, created=null, updated=null, replaces=null` |
| `delete_document` | `scope, path, successor=null` |
| `move_document` | `scope, path, new_path` |

未知参数会被拒绝，创建接口不能接收 `status`。不提供依赖个人文件名或日记布局的专用写入工具。

路径相对 vault，可带匹配 scope 前缀，也可相对已选 scope；显式另一 scope、绝对路径、`..`、非 Markdown 路径均拒绝。归档只支持显式开启后的读取、列表、搜索和校验，不支持写入。

## 搜索和读取

搜索结果含 `scope`、`path`、`line_start`、`line_end`、`title`、`snippet`，行号从 1 开始。检索正文和 frontmatter 中的 title、series、tags，返回原始位置；状态等管理字段不作为普通关键词。

BM25 综合词频与文档长度，中文按二元组匹配，标题和英文技术词加权，导航页降权。结果定位到文档中选中的命中章节，不保证是首个字面命中；用 `read_range` 核实上下文。搜索默认排除 archive，开启后仍排除显式声明另一 scope 的归档。未声明 scope 的旧归档可能在两个 scope 的归档搜索中出现。

`read` 返回完整文本。`read_range` 返回 `scope`、`path`、`start_line`、`end_line`、`lines`；每条 line 含 `line` 和 `text`。起始行越界或行区间无效时报错，结束行超过文件末尾则截到末尾。

## 写入

`show_diff` 返回 `path`、`before`、`after`、`diff`，只预览不写入。`apply_change` 重新检查 `expected_text` 在当前文件中恰好出现一次，再局部替换并返回 `path`、`content`。预览与提交之间有人改动匹配片段，写入会拒绝；其他片段的变更不被覆盖。它不接受文件版本号。

新文档固定写 `status: review`，正文不带 frontmatter 或一级标题。来源去重、任务章节、可选导入日期等约束见 [内容模型](CONTENT_MODEL.md)。创建不覆盖已有文件。

`replaces` 指定已被合并的旧文档，`successor` 指定删除前承接正文的文件；两者使用至少 70% 正文长度保护，不能代替人工语义核对。删除、移动或合并须先有相应授权。MCP 本身不执行确认对话，也不替调用方管理审批、Git 提交或备份。

操作顺序：先读原文，预览 diff，确认授权，写入，回读，再校验。过期或歧义条件不能用删掉版本保护的方式重试。

## 资源

| URI | 来源 |
| --- | --- |
| `knowledge://guide` | 随包的 `docs/MCP_CONTRACT.md` |
| `knowledge://architecture` | 随包的 `docs/ARCHITECTURE.md` |
| `knowledge://content-model` | 随包的 `docs/CONTENT_MODEL.md` |
| `knowledge://maintenance-skill` | 随包的 `skills/knowledge-maintenance/SKILL.md` |
| `knowledge://search-skill` | 随包的 `skills/knowledge-search/SKILL.md` |
| `knowledge://writing-skill` | 随包的 `skills/writing/SKILL.md` |
| `knowledge://personal-overview` | 指定 vault 的 `personal/README.md` |
| `knowledge://company-overview` | 指定 vault 的 `company/README.md` |

## 错误处理

| 错误 | 下一步 |
| --- | --- |
| `InvalidScopeError` / `InvalidPathError` | 修正 scope 或路径 |
| `DocumentNotFoundError` | 先 list 或 search |
| `ArchiveAccessError` | 确认需要历史材料后显式开启归档 |
| `VersionConflictError` | 重读当前内容，再生成 diff |
| `AmbiguousChangeError` | 扩大原文上下文，使匹配唯一 |
| `DocumentExistsError` | 读取已有文档，不覆盖 |
| `DuplicateSourceError` | 更新已认领来源的文档 |
| `InvalidMetadataError` | 按内容模型修正输入 |

MCP 工具错误通过协议错误结果返回；资源缺失会报告资源不可读。`validate_vault` 返回 finding 列表，空列表表示未发现问题，不能据此证明所有内容或权限策略正确。
