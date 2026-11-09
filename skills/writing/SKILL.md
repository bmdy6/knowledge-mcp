# writing

创建正式文档前，先读 `knowledge://content-model` 和目标 scope 的概览资源，遵守该 vault 的内容约定。

1. 先用 `search(scope, query)` 找已有主题，有对应文档就通过 `show_diff` / `apply_change` 补充。
2. 确认是新主题后，选择 `personal/` 或 `company/` 内的 Markdown 路径；文件和目录不要求特定个人布局。
3. 用 `create_document` 传入 title、正文、类型及必要来源。正文不带 frontmatter 或一级标题，创建接口自动生成。
4. 同一 scope 内不要重复认领 sources；遇到 `DuplicateSourceError` 就回到已有文档。
5. 回读全文，运行 `validate_vault(scope)` 并审阅 Git diff。

类型包括 article、reference、research、guide、decision、task。task 需要 `## 任务描述`、`## 解决思考`、`## 解决方法`、`## 结果`、`## 链接` 五节；原文未记录的事实不要编造。

事实、判断和待验证内容分开，保留关键背景及来源。外部内容优先保留链接与必要摘录。新文档固定为 review，不等于人已认可其内容。待学习素材不要写成已掌握知识，也不要未经授权自动记录私人信息。

合并旧文章须先获得对应授权，列出 replaces 并检查内容保留程度。写入后不自动提交、推送或合并。当前说明不依赖任何固定日记、外部应用或发布流程。
