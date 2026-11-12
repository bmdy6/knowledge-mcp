# MCP 契约

本项目通过 stdio 提供下面的工具与资源。启动必须指定 `--vault <路径>` 或 `KNOWLEDGE_VAULT`；参数优先，无当前目录默认值。所有文档操作必须显式指定 `personal` 或 `company` scope。

## 工具

| 工具 | 参数 |
| --- | --- |
| `search` | `scope, query, limit=20, include_archive=false` |
| `find_document` | `scope, query, limit=20, include_archive=false` |
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

已知标题、路径、文件名或来源时，先用 `find_document`。它按原值定位，不做模糊匹配。标题忽略大小写，路径相对 vault 或已选 scope，文件名可省略 `.md`，`sources` 中的来源值按大小写精确比较。

返回对象含 `scope`、`include_archive`、`searched_roots`、`matches`、`partial`、`skipped`、`truncated`。`searched_roots` 列出实际扫描的相对目录。`matches` 每条含 `path`、`title`、`match_kind`，优先返回 `path`，然后是 `title`、`source`。同类命中先列当前资料，再列归档。`truncated` 表示命中数超过 limit。`source` 表示该笔记声明了来源，返回路径仍是本地笔记。用 `search` 找正文里的链接、关键词或近似名称，用 `read` 核对文档。

搜索结果含 `scope`、`path`、`line_start`、`line_end`、`title`、`snippet`，行号从 1 开始。检索正文和 frontmatter 中的 title、series、tags，返回原始位置；状态等管理字段不作为普通关键词。

遇到 YAML 损坏或不可读文件时，搜索继续返回健康笔记的命中，并在返回列表末尾追加一个诊断记录：`{warning, partial: true, skipped: [{path, code}]}`。调用方必须先判断是否含 `warning`，不能把诊断当作有行号的命中；`limit` 只限制命中数量。即使没有命中，也会返回该诊断，不能把它解释为完整搜索后没有结果。`code` 为 `invalid-frontmatter`、`unreadable-document` 或 `symlink-search-root`，只带相对路径，不包含笔记原文或解析异常。`find_document` 在返回对象中提供同样的 `partial` 和 `skipped` 信息。`search` 的列表结构不变。`validate_vault` 可辅助定位并修复笔记后重新搜索。

BM25 综合词频与文档长度，中文按二元组匹配，标题和英文技术词加权，导航页降权。结果定位到文档中选中的命中章节，不保证是首个字面命中；用 `read_range` 核实上下文。搜索默认排除 archive，开启后仍排除显式声明另一 scope 的归档。未声明 scope 的旧归档可能在两个 scope 的归档搜索中出现。

`read` 返回完整文本。`read_range` 返回 `scope`、`path`、`start_line`、`end_line`、`lines`；每条 line 含 `line` 和 `text`。起始行越界或行区间无效时报错，结束行超过文件末尾则截到末尾。

## 写入

`show_diff` 返回 `path`、`before`、`after`、`diff`，只预览不写入。`apply_change` 重新检查 `expected_text` 在当前文件中恰好出现一次，再局部替换并返回 `path`、`content`。预览与提交之间有人改动匹配片段，写入会拒绝；其他片段的变更不被覆盖。它不接受文件版本号。

同一个本地 vault 的 MCP 写入操作共用跨进程锁：取得锁后才读取当前原文和校验，直到替换、移动或删除完成后释放。并发修改不同片段会依次基于最新内容应用；完整原文已过期则拒绝写入。等待超过 10 秒返回 `VaultBusyError`，异常或进程退出会释放锁。锁在 vault 根创建隐藏文件 `.knowledge-mcp.lock`，不参与 Markdown 搜索，也不包含笔记内容；运行期间不要删除它，可加入知识库自己的 `.gitignore`。

锁支持 macOS/Linux 和 Windows，仅协调使用此 MCP 的进程。手工编辑器、Git、云盘同步或其他直接写文件的程序不遵守此锁，不能保证与它们并发修改时不冲突；网络文件系统的锁语义未验证。需要这些操作时应暂停 MCP 写入。锁不使多文件合并成为可回滚事务，仍需备份。

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
| `VaultBusyError` | 等其他写入结束后重试；不要删除锁文件 |
| `AmbiguousChangeError` | 扩大原文上下文，使匹配唯一 |
| `DocumentExistsError` | 读取已有文档，不覆盖 |
| `DuplicateSourceError` | 更新已认领来源的文档 |
| `InvalidMetadataError` | 按内容模型修正输入 |

MCP 工具错误通过协议错误结果返回；资源缺失会报告资源不可读。`validate_vault` 返回 finding 列表，空列表表示未发现问题，不能据此证明所有内容或权限策略正确。
