from datetime import date
from pathlib import Path

import pytest

from knowledge_mcp.vault import (
    AmbiguousChangeError,
    ArchiveAccessError,
    DocumentExistsError,
    DocumentNotFoundError,
    DuplicateSourceError,
    InvalidMetadataError,
    InvalidPathError,
    Vault,
    VersionConflictError,
    split_frontmatter,
)


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    for relative, content in {
        "personal/note.md": "---\ntitle: Personal\nscope: personal\n---\n# Personal\nsecretword\n",
        "company/note.md": "---\ntitle: Company\nscope: company\n---\n# Company\nsecretword\n",
        "archive/old.md": "# Archived\nsecretword\n",
    }.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp_path


def test_search_ranks_title_and_content_above_index_pages(
    vault_root: Path,
) -> None:
    """Relevance beats path order, so the article outranks the index that lists it.

    Path order used to put the topic map ahead of the article it points at, which
    made a query land on a navigation page instead of the document itself.
    """
    (vault_root / "personal").mkdir(parents=True, exist_ok=True)
    (vault_root / "personal/README.md").write_text(
        "---\ntitle: 知识库入口\nscope: personal\n---\n\n# 知识库入口\n\n- 依赖注入\n",
        encoding="utf-8",
    )
    (vault_root / "personal/学习方法").mkdir(parents=True, exist_ok=True)
    (vault_root / "personal/学习方法/学习点索引.md").write_text(
        "---\ntitle: 学习点索引\nscope: personal\n---\n\n# 学习点索引\n\n- 依赖注入\n",
        encoding="utf-8",
    )
    (vault_root / "personal/后端").mkdir(parents=True, exist_ok=True)
    (vault_root / "personal/后端/FastAPI.md").write_text(
        "---\n"
        "title: FastAPI\n"
        "scope: personal\n"
        "---\n"
        "\n"
        "# FastAPI\n"
        "\n"
        "依赖注入把可复用检查抽出来。\n",
        encoding="utf-8",
    )
    vault = Vault(vault_root)

    results = vault.search("personal", "依赖注入", limit=10)

    assert results[0].path == "personal/后端/FastAPI.md"
    assert results[0].title == "FastAPI"
    assert [hit.path for hit in results[1:]] == [
        "personal/README.md",
        "personal/学习方法/学习点索引.md",
    ]


def test_search_matches_every_word_of_a_multi_word_query(vault_root: Path) -> None:
    """A space-separated query matches per word, not as one literal string."""
    (vault_root / "personal/后端").mkdir(parents=True, exist_ok=True)
    (vault_root / "personal/后端/缓存策略.md").write_text(
        "---\ntitle: 缓存策略\ntype: article\nscope: personal\n---\n\n"
        "# 缓存策略\n\n缓存策略在多条线复用。\n\n适用场景：查询接口、离线报表\n",
        encoding="utf-8",
    )
    (vault_root / "personal/后端/只提一词.md").write_text(
        "---\ntitle: 只提一词\ntype: article\nscope: personal\n---\n\n"
        "# 只提一词\n\n这里只说缓存策略。\n",
        encoding="utf-8",
    )
    (vault_root / "personal/后端/无关.md").write_text(
        "---\ntitle: 无关\ntype: article\nscope: personal\n---\n\n# 无关\n\n完全不相干的内容。\n",
        encoding="utf-8",
    )
    vault = Vault(vault_root)

    results = vault.search("personal", "缓存策略 适用场景", limit=10)
    paths = [result.path for result in results]

    assert paths[0] == "personal/后端/缓存策略.md"
    assert "personal/后端/只提一词.md" in paths
    assert "personal/后端/无关.md" not in paths


def test_search_keeps_partial_matches_when_one_word_is_missing(
    vault_root: Path,
) -> None:
    (vault_root / "personal/后端").mkdir(parents=True, exist_ok=True)
    (vault_root / "personal/后端/只提一词.md").write_text(
        "---\ntitle: 只提一词\ntype: article\nscope: personal\n---\n\n"
        "# 只提一词\n\n这里只说缓存策略。\n",
        encoding="utf-8",
    )
    vault = Vault(vault_root)

    results = vault.search("personal", "缓存策略 完全不存在的词", limit=10)

    assert [result.path for result in results] == ["personal/后端/只提一词.md"]


def test_search_points_at_the_line_with_the_most_query_words(
    vault_root: Path,
) -> None:
    (vault_root / "personal/后端").mkdir(parents=True, exist_ok=True)
    (vault_root / "personal/后端/两处提到.md").write_text(
        "---\ntitle: 两处提到\ntype: article\nscope: personal\n---\n\n"
        "# 两处提到\n\n"
        "第一段只说缓存策略。\n\n"
        "第二段只说适用场景。\n\n"
        "缓存策略 和 适用场景 都在这一行。\n",
        encoding="utf-8",
    )
    vault = Vault(vault_root)

    results = vault.search("personal", "缓存策略 适用场景", limit=5)

    assert results[0].path == "personal/后端/两处提到.md"
    assert "都在这一行" in results[0].snippet


def test_search_reports_the_paragraph_around_the_best_matching_line(
    vault_root: Path,
) -> None:
    """Without subheadings, search returns the paragraph containing the best hit."""
    target = vault_root / "personal/multi-hit.md"
    target.write_text(
        "---\ntitle: 多命中\nscope: personal\n---\n"
        "\n"
        "# 多命中\n"
        "\n"
        "第一段没有关键词。\n"
        "第二段提到 asyncio 并发。\n"
        "第三段又提到 asyncio。\n",
        encoding="utf-8",
    )
    vault = Vault(vault_root)

    results = vault.search("personal", "asyncio", limit=5)

    hit = next(item for item in results if item.path == "personal/multi-hit.md")
    assert hit.line_start == 8
    assert hit.line_end == 10
    assert "第二段" in hit.snippet



def test_search_returns_the_enclosing_markdown_section(vault_root: Path) -> None:
    target = vault_root / "personal/sectioned.md"
    target.write_text(
        "---\ntitle: 英语\nscope: personal\n---\n\n"
        "# 英语\n\n"
        "## 2026-10-01\n\n"
        "这一段没有目标词。\n\n"
        "## 2026-10-06\n\n"
        "### 正反馈\n\n"
        "英语练习需要正反馈，最好每次都有一个小输出。\n"
        "下一行仍属于这个小节。\n\n"
        "### 其他\n\n"
        "别的内容。\n",
        encoding="utf-8",
    )
    vault = Vault(vault_root)

    result = vault.search("personal", "正反馈")[0]

    lines = target.read_text(encoding="utf-8").splitlines()
    start = lines.index("### 正反馈") + 1
    end = lines.index("下一行仍属于这个小节。") + 1
    assert result.line_start == start
    assert result.line_end == end
    assert "每次都有一个小输出" in result.snippet


def test_search_prefers_newer_dated_section_when_relevance_is_equal(
    vault_root: Path,
) -> None:
    target = vault_root / "personal/evolving.md"
    target.write_text(
        "---\ntitle: 英语\nscope: personal\n---\n\n"
        "# 英语\n\n"
        "## 2026-09-01\n\n"
        "### 练习\n\n"
        "英语正反馈来自完成练习。\n\n"
        "## 2026-10-06\n\n"
        "### 练习\n\n"
        "英语正反馈来自主动输出。\n",
        encoding="utf-8",
    )
    vault = Vault(vault_root)

    result = vault.search("personal", "英语正反馈")[0]

    assert "主动输出" in result.snippet
    assert "完成练习" not in result.snippet


def test_search_ranks_technical_terms_above_prose(vault_root: Path) -> None:
    """An English identifier is a stronger signal than a spelled-out sentence."""
    (vault_root / "personal/talk.md").write_text(
        "---\ntitle: 口语记录\nscope: personal\n---\n\n"
        "# 口语记录\n\n"
        "合成讨论反复提到 pydantic 的用法与杂项，\n"
        "这一段再次重复 pydantic，然后又重复 pydantic。\n",
        encoding="utf-8",
    )
    (vault_root / "personal/40-knowledge").mkdir(parents=True)
    (vault_root / "personal/40-knowledge/pydantic-model.md").write_text(
        "---\ntitle: pydantic 请求模型\nscope: personal\n---\n\n"
        "# pydantic 请求模型\n\n定义字段和类型。\n",
        encoding="utf-8",
    )
    vault = Vault(vault_root)

    results = vault.search("personal", "pydantic", limit=10)

    assert results[0].path == "personal/40-knowledge/pydantic-model.md"


def test_search_is_scoped(vault_root: Path) -> None:
    vault = Vault(vault_root)

    personal = vault.search("personal", "secretword")
    company = vault.search("company", "secretword")

    assert [item.path for item in personal] == ["personal/note.md"]
    assert [item.path for item in company] == ["company/note.md"]
    assert personal[0].line_start == 6


def test_search_matches_title_tags_and_series_with_frontmatter_line(
    vault_root: Path,
) -> None:
    target = vault_root / "personal/series-note.md"
    target.write_text(
        "---\n"
        "title: Agent 服务化\n"
        "type: article\n"
        "status: review\n"
        "scope: personal\n"
        "series: 服务化学习\n"
        "tags:\n"
        "  - agent\n"
        "  - architecture\n"
        "---\n"
        "\n"
        "# Agent 服务化\n"
        "\n"
        "正文没有匹配词。\n",
        encoding="utf-8",
    )
    vault = Vault(vault_root)

    title_result = vault.search("personal", "Agent 服务化")
    tag_result = vault.search("personal", "architecture")
    series_result = vault.search("personal", "服务化学习")

    assert title_result[0].path == "personal/series-note.md"
    assert title_result[0].line_start == 2
    assert tag_result[0].line_start == 9
    assert series_result[0].line_start == 6


def test_search_accepts_inline_yaml_tags(vault_root: Path) -> None:
    target = vault_root / "personal/inline-tags.md"
    target.write_text(
        "---\n"
        "title: Inline tags\n"
        "scope: personal\n"
        "tags: [agent, inline-tag]\n"
        "---\n"
        "\n"
        "正文没有匹配词。\n",
        encoding="utf-8",
    )
    vault = Vault(vault_root)

    result = vault.search("personal", "inline-tag")

    assert [item.path for item in result] == ["personal/inline-tags.md"]
    assert result[0].line_start == 4


def test_split_frontmatter_parses_inline_yaml_tags() -> None:
    metadata, body = split_frontmatter(
        "---\n"
        "title: 内联标签\n"
        "tags: [agent, inline-tag]\n"
        "---\n"
        "\n"
        "正文。\n"
    )

    assert metadata["tags"] == ["agent", "inline-tag"]
    assert body == "\n正文。\n"


def test_archive_is_not_searched_by_default(vault_root: Path) -> None:
    vault = Vault(vault_root)

    assert all(item.path != "archive/old.md" for item in vault.search("personal", "secretword"))
    assert any(
        item.path == "archive/old.md"
        for item in vault.search("personal", "secretword", include_archive=True)
    )


def test_archive_read_requires_explicit_opt_in(vault_root: Path) -> None:
    vault = Vault(vault_root)

    with pytest.raises(ArchiveAccessError):
        vault.read("personal", "archive/old.md")

    assert "Archived" in vault.read(
        "personal", "archive/old.md", include_archive=True
    )


def test_archive_with_explicit_other_scope_is_still_isolated(vault_root: Path) -> None:
    company_archive = vault_root / "archive/company-old.md"
    company_archive.write_text(
        "---\nscope: company\n---\n# Company old\nsecretword\n",
        encoding="utf-8",
    )
    vault = Vault(vault_root)

    assert all(
        item.path != "archive/company-old.md"
        for item in vault.search("personal", "secretword", include_archive=True)
    )
    with pytest.raises(InvalidPathError):
        vault.read("personal", "archive/company-old.md", include_archive=True)


def test_validate_reports_metadata_problems(vault_root: Path) -> None:
    invalid = vault_root / "personal/invalid.md"
    invalid.write_text(
        "---\nscope: company\ntype: unknown\nstatus: pending\n---\n# Invalid\n",
        encoding="utf-8",
    )

    findings = Vault(vault_root).validate("personal")

    by_code = {finding.code for finding in findings}
    assert {"scope-mismatch", "invalid-type", "invalid-status"} <= by_code


def test_validate_scans_archive_once_and_respects_scope(vault_root: Path) -> None:
    archive = vault_root / "archive/company-old.md"
    archive.write_text(
        "---\nscope: company\ntype: unknown\n---\n# Company old\n",
        encoding="utf-8",
    )
    findings = Vault(vault_root).validate(include_archive=True)

    matching = [finding for finding in findings if finding.path == "archive/company-old.md"]
    assert len(matching) == 1
    assert matching[0].scope == "archive"
    assert matching[0].code == "invalid-type"

    personal_findings = Vault(vault_root).validate(
        "personal", include_archive=True
    )
    assert all(
        finding.path != "archive/company-old.md" for finding in personal_findings
    )


def test_scope_escape_is_rejected(vault_root: Path) -> None:
    with pytest.raises(InvalidPathError):
        Vault(vault_root).read("personal", "company/note.md")

    mismatched = vault_root / "personal/mismatched.md"
    mismatched.write_text(
        "---\nscope: company\n---\n# Wrong scope\n", encoding="utf-8"
    )
    with pytest.raises(InvalidPathError):
        Vault(vault_root).read("personal", "personal/mismatched.md")


def test_path_traversal_is_rejected(vault_root: Path) -> None:
    with pytest.raises(InvalidPathError):
        Vault(vault_root).read("personal", "personal/../company/note.md")


def test_apply_change_absorbs_replaced_documents(vault_root: Path) -> None:
    """A card folded into an existing topic article is deleted after the patch."""
    _note(vault_root, "personal/主题/main.md", "甲" * 200)
    _note(vault_root, "personal/概念/card.md", "乙" * 100)
    vault = Vault(vault_root)
    result = vault.apply_change(
        "personal",
        "主题/main.md",
        "甲" * 200,
        "甲" * 200 + "\n\n## 新一节\n\n" + "乙" * 100,
        replaces=["personal/概念/card.md"],
    )
    assert result["replaced"] == ["personal/概念/card.md"]
    assert not (vault_root / "personal/概念/card.md").exists()
    assert not (vault_root / "personal/概念").exists()


def test_apply_change_refuses_to_absorb_when_body_too_short(vault_root: Path) -> None:
    _note(vault_root, "personal/主题/main.md", "甲" * 20)
    _note(vault_root, "personal/概念/card.md", "乙" * 500)
    vault = Vault(vault_root)
    with pytest.raises(InvalidMetadataError):
        vault.apply_change(
            "personal",
            "主题/main.md",
            "甲" * 20,
            "甲" * 20 + "丙",
            replaces=["personal/概念/card.md"],
        )
    assert (vault_root / "personal/概念/card.md").exists()
    assert vault.read("personal", "主题/main.md").endswith("甲" * 20)


def test_delete_document_requires_absorbing_successor(vault_root: Path) -> None:
    _note(vault_root, "personal/概念/card.md", "乙" * 300)
    _note(vault_root, "personal/主题/main.md", "甲" * 100)
    vault = Vault(vault_root)
    with pytest.raises(InvalidMetadataError):
        vault.delete_document(
            "personal", "概念/card.md", successor="personal/主题/main.md"
        )
    assert (vault_root / "personal/概念/card.md").exists()

    vault.apply_change(
        "personal",
        "主题/main.md",
        "甲" * 100,
        "甲" * 100 + "\n\n" + "乙" * 300,
    )
    result = vault.delete_document(
        "personal", "概念/card.md", successor="personal/主题/main.md"
    )
    assert result["deleted"] == "true"
    assert not (vault_root / "personal/概念/card.md").exists()
    assert not (vault_root / "personal/概念").exists()


def test_delete_document_without_successor_is_unconditional(vault_root: Path) -> None:
    _note(vault_root, "personal/工具与工作流/旧清单.md", "丙" * 50)
    vault = Vault(vault_root)
    assert vault.delete_document("personal", "工具与工作流/旧清单.md")["deleted"] == "true"
    assert not (vault_root / "personal/工具与工作流/旧清单.md").exists()


def test_scope_relative_path_is_accepted(vault_root: Path) -> None:
    vault = Vault(vault_root)
    created = vault.create_document("personal", "主题/x.md", "X", "正文")
    assert created["path"] == "personal/主题/x.md"
    assert vault.read("personal", "主题/x.md") == vault.read("personal", "personal/主题/x.md")
    with pytest.raises(InvalidPathError):
        vault.read("personal", "company/note.md")


def test_list_prefix_accepts_scope_but_rejects_another_scope(vault_root: Path) -> None:
    vault = Vault(vault_root)
    vault.create_document("personal", "主题/x.md", "X", "正文")
    assert vault.list_documents("personal", prefix="主题") == ["personal/主题/x.md"]
    assert vault.list_documents("personal", prefix="personal/主题") == ["personal/主题/x.md"]
    with pytest.raises(InvalidPathError):
        vault.list_documents("personal", prefix="company")
    with pytest.raises(InvalidPathError):
        vault.read("personal", "主题/../../company/note.md")


def test_read_range_returns_original_line_numbers(vault_root: Path) -> None:
    result = Vault(vault_root).read_range("personal", "personal/note.md", 6, 6)

    assert result["lines"] == [{"line": 6, "text": "secretword"}]

    with pytest.raises(ValueError):
        Vault(vault_root).read_range("personal", "personal/note.md", 99, 99)



def test_change_requires_unique_current_text(vault_root: Path) -> None:
    vault = Vault(vault_root)

    with pytest.raises(VersionConflictError):
        vault.propose_change("personal", "personal/note.md", "not present", "replacement")

    repeated = vault_root / "personal/repeated.md"
    repeated.write_text("same same", encoding="utf-8")
    with pytest.raises(AmbiguousChangeError):
        vault.propose_change("personal", "personal/repeated.md", "same", "new")


def test_apply_change_preserves_unmatched_content(vault_root: Path) -> None:
    vault = Vault(vault_root)

    result = vault.apply_change(
        "personal", "personal/note.md", "secretword", "updated"
    )

    assert "# Personal" in result["content"]
    assert "updated" in result["content"]
    assert "secretword" not in result["content"]
    assert (vault_root / "personal/note.md").read_text(encoding="utf-8") == result["content"]


def test_create_document_writes_readable_frontmatter(vault_root: Path) -> None:
    result = Vault(vault_root).create_document(
        "personal",
        "personal/40-knowledge/new-note.md",
        "New note",
        "本文内容。",
        document_type="guide",
        tags=["one", "two"],
        series="示范系列",
        sources=["archive/original.md", "https://example.com/source"],
    )

    assert result["path"] == "personal/40-knowledge/new-note.md"
    assert "status: review" in result["content"]
    assert "scope: personal" in result["content"]
    assert "series: 示范系列" in result["content"]
    assert "  - archive/original.md" in result["content"]
    assert "  - https://example.com/source" in result["content"]
    assert "  - one" in result["content"]
    assert "# New note" in result["content"]
    assert (vault_root / result["path"]).read_text(encoding="utf-8") == result["content"]


def test_create_document_always_starts_as_review(vault_root: Path) -> None:
    """No creation path can produce a confirmed document."""
    vault = Vault(vault_root)

    created = vault.create_document(
        "personal",
        "personal/40-knowledge/always-review.md",
        "Always review",
        "正文。",
    )

    content = (vault_root / created["path"]).read_text(encoding="utf-8")
    assert "status: review" in content
    # ``active`` must only be reachable by changing an existing document, so the
    # creation path cannot be handed a status at all.
    with pytest.raises(TypeError):
        vault.create_document(
            "personal",
            "personal/40-knowledge/cannot-confirm.md",
            "Cannot confirm",
            "正文。",
            status="active",
        )
    assert not (vault_root / "personal/40-knowledge/cannot-confirm.md").exists()
    assert "status: active" not in content


TASK_BODY = "一句话结论。\n\n## 任务描述\n\na\n\n## 解决思考\n\nb\n\n## 解决方法\n\nc\n\n## 结果\n\nd\n\n## 链接\n\n- PR：未记录\n"


def test_create_task_requires_fixed_sections(vault_root: Path) -> None:
    vault = Vault(vault_root)

    created = vault.create_document(
        "company", "company/sample-project/ok.md", "OK", TASK_BODY, document_type="task"
    )
    assert "type: task" in created["content"]

    broken = TASK_BODY.replace("## 解决思考", "## 思路").replace("## 链接", "## 链接们")
    with pytest.raises(InvalidMetadataError, match="## 解决思考, ## 链接"):
        vault.create_document(
            "company", "company/sample-project/bad.md", "Bad", broken, document_type="task"
        )
    assert not (vault_root / "company/sample-project/bad.md").exists()


def test_validate_flags_task_missing_sections(vault_root: Path) -> None:
    target = vault_root / "company/sample-project/hand-written.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\ntitle: 手写\ntype: task\nscope: company\n---\n\n# 手写\n\n## 任务描述\n\nx\n",
        encoding="utf-8",
    )

    codes = {f.code for f in Vault(vault_root).validate("company")}
    assert "missing-task-sections" in codes


def _note(root: Path, rel: str, body: str, sources: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = f"sources:\n  - {sources}\n" if sources else ""
    path.write_text(
        f"---\ntitle: t\ntype: article\nscope: personal\n{meta}---\n\n{body}",
        encoding="utf-8",
    )


def test_merge_replaces_old_notes_and_reclaims_sources(vault_root: Path) -> None:
    _note(vault_root, "personal/old/a.md", "甩" * 100, sources="legacy/a.md")
    _note(vault_root, "personal/old/b.md", "乙" * 100)
    vault = Vault(vault_root)

    result = vault.create_document(
        "personal",
        "personal/主题/合并.md",
        "合并",
        "甩" * 100 + "乙" * 50,
        sources=["legacy/a.md"],
        replaces=["personal/old/a.md", "personal/old/b.md"],
    )

    assert result["replaced"] == ["personal/old/a.md", "personal/old/b.md"]
    assert (vault_root / "personal/主题/合并.md").exists()
    assert not (vault_root / "personal/old").exists()  # emptied folder pruned


def test_merge_rejects_content_loss_and_keeps_old_notes(vault_root: Path) -> None:
    _note(vault_root, "personal/old/a.md", "甩" * 100)
    vault = Vault(vault_root)

    with pytest.raises(InvalidMetadataError, match="must not drop content"):
        vault.create_document(
            "personal",
            "personal/主题/太短.md",
            "太短",
            "甩" * 10,
            replaces=["personal/old/a.md"],
        )
    assert (vault_root / "personal/old/a.md").exists()
    assert not (vault_root / "personal/主题/太短.md").exists()


def test_move_document_renames_within_scope(vault_root: Path) -> None:
    _note(vault_root, "personal/old/学习记录-x.md", "正文")
    _note(vault_root, "personal/主题/已存在.md", "正文")
    vault = Vault(vault_root)

    with pytest.raises(DocumentExistsError):
        vault.move_document(
            "personal", "personal/old/学习记录-x.md", "personal/主题/已存在.md"
        )
    with pytest.raises(InvalidPathError):
        vault.move_document("personal", "personal/old/学习记录-x.md", "company/x.md")

    moved = vault.move_document(
        "personal", "personal/old/学习记录-x.md", "personal/主题/x.md"
    )
    assert moved["to"] == "personal/主题/x.md"
    assert (vault_root / "personal/主题/x.md").read_text(encoding="utf-8").endswith("正文")
    assert not (vault_root / "personal/old").exists()


def test_create_document_refuses_overwrite_and_unsafe_metadata(vault_root: Path) -> None:
    vault = Vault(vault_root)

    with pytest.raises(DocumentExistsError):
        vault.create_document("personal", "personal/note.md", "Existing", "body")
    with pytest.raises(InvalidPathError):
        vault.create_document("personal", "archive/new.md", "Archive", "body")
    with pytest.raises(InvalidMetadataError):
        vault.create_document("personal", "personal/new.md", "Title", "---\nscope: personal")
    with pytest.raises(InvalidMetadataError):
        vault.create_document(
            "personal",
            "personal/new.md",
            "Title",
            "body",
            document_type="unknown",
        )
    with pytest.raises(InvalidMetadataError):
        vault.create_document(
            "personal",
            "personal/new.md",
            "Title",
            "body",
            series="",
        )
    with pytest.raises(InvalidMetadataError):
        vault.create_document(
            "personal",
            "personal/new.md",
            "Title",
            "body",
            sources=["valid", ""],
        )


def test_create_document_heading_validation(vault_root: Path) -> None:
    vault = Vault(vault_root)

    # Top-level H1 heading in content is rejected
    with pytest.raises(InvalidMetadataError, match="must not contain top-level Markdown headings"):
        vault.create_document(
            "personal",
            "personal/new-h1.md",
            "Title",
            "# An H1 In Content\n\nSome body text.",
        )

    # Indented H1 (up to 3 spaces) is also rejected
    with pytest.raises(InvalidMetadataError, match="must not contain top-level Markdown headings"):
        vault.create_document(
            "personal",
            "personal/new-indented-h1.md",
            "Title",
            "   # Indented H1\n\nSome body text.",
        )

    # Comments starting with # inside code blocks are allowed and not mistaken for headings
    doc_with_code = vault.create_document(
        "personal",
        "personal/new-code.md",
        "Code Doc",
        "```python\n# This is a python comment\nx = 1\n```\n\n```bash\n# bash comment\necho 1\n```",
    )
    assert "# This is a python comment" in doc_with_code["content"]

    # Subheadings (H2, H3) are allowed
    doc_with_subheadings = vault.create_document(
        "personal",
        "personal/new-subheadings.md",
        "Subheadings Doc",
        "## Section 1\n\nSome text.\n\n### Subsection 1.1\n\nMore text.",
    )
    assert "## Section 1" in doc_with_subheadings["content"]


def test_create_document_refuses_a_source_another_document_claims(
    vault_root: Path,
) -> None:
    """One source cannot become two documents.

    Importing the same legacy file under a second name is how duplicate
    knowledge appears: both documents look valid on their own and only the
    shared source reveals the collision.
    """
    vault = Vault(vault_root)
    source = "archive/synthetic-source.md"
    vault.create_document(
        "personal",
        "personal/40-knowledge/series-1.md",
        "同一个标题",
        "整理后的正文",
        series="Agent服务化",
        sources=[source],
    )

    with pytest.raises(DuplicateSourceError, match="already claimed by"):
        vault.create_document(
            "personal",
            "personal/notes/synthetic-source.md",
            "同一个标题",
            "照搬的正文",
            series="Agent服务化",
            sources=[source],
        )

    assert not (
        vault_root / "personal/notes/synthetic-source.md"
    ).exists()


def test_create_document_allows_distinct_sources_and_empty_source_list(
    vault_root: Path,
) -> None:
    """The source guard blocks reuse only, not ordinary creation."""
    vault = Vault(vault_root)

    vault.create_document(
        "personal",
        "personal/40-knowledge/a.md",
        "A",
        "正文",
        sources=["90-归档/来源一.md"],
    )
    vault.create_document(
        "personal",
        "personal/40-knowledge/b.md",
        "B",
        "正文",
        sources=["90-归档/来源二.md"],
    )
    # Documents without ``sources`` are unaffected: there is nothing to collide.
    vault.create_document("personal", "personal/40-knowledge/c.md", "C", "正文")

    assert (vault_root / "personal/40-knowledge/a.md").is_file()
    assert (vault_root / "personal/40-knowledge/b.md").is_file()
    assert (vault_root / "personal/40-knowledge/c.md").is_file()


def test_create_document_scopes_the_source_guard_per_scope(
    vault_root: Path,
) -> None:
    """A personal document must not block a company document's source."""
    vault = Vault(vault_root)
    (vault_root / "company/20-resources").mkdir(parents=True, exist_ok=True)

    vault.create_document(
        "personal",
        "personal/40-knowledge/shared-source.md",
        "个人版本",
        "正文",
        sources=["archive/shared.md"],
    )
    result = vault.create_document(
        "company",
        "company/20-resources/shared-source.md",
        "公司版本",
        "正文",
        sources=["archive/shared.md"],
    )

    assert result["scope"] == "company"


def test_validate_new_document_path_does_not_create_file(vault_root: Path) -> None:
    vault = Vault(vault_root)

    normalized = vault.validate_new_document_path(
        "personal", "personal/40-knowledge/planned.md"
    )

    assert normalized == "personal/40-knowledge/planned.md"
    assert not (vault_root / normalized).exists()


def _ranked_vault(tmp_path):
    (tmp_path / "personal" / "辅助材料").mkdir(parents=True)
    book = "---\ntitle: 系统架构\ntype: article\nstatus: review\nscope: personal\n---\n\n# 系统架构\n\n## 第 1 章 消息\n\n### 1.1 消息队列与重复\n\n同一条消息可能重复，消费端要幂等。接口调用会超时。\n"
    note = "---\ntitle: 旧笔记\ntype: article\nstatus: review\nscope: personal\n---\n\n# 旧笔记\n\n消息队列的旧记录。\n"
    (tmp_path / "personal" / "01-系统架构.md").write_text(book, encoding="utf-8")
    (tmp_path / "personal" / "辅助材料" / "02-旧笔记.md").write_text(note, encoding="utf-8")
    (tmp_path / "personal" / "辅助材料" / "无关.md").write_text("---\ntitle: 无关\nscope: personal\n---\n\n# 无关\n\n今天天气不错。\n", encoding="utf-8")
    return Vault(tmp_path)


def test_search_ignores_question_words(tmp_path):
    results = _ranked_vault(tmp_path).search("personal", "为什么要用消息队列")
    assert [r.path for r in results][0] == "personal/01-系统架构.md"


def test_search_matches_when_wording_differs(tmp_path):
    results = _ranked_vault(tmp_path).search("personal", "重复消费怎么办")
    assert results and results[0].path == "personal/01-系统架构.md"
    assert results[0].line_start <= 13 <= results[0].line_end


def test_search_ranks_book_above_auxiliary_note(tmp_path):
    results = _ranked_vault(tmp_path).search("personal", "消息队列")
    assert [r.path for r in results] == [
        "personal/01-系统架构.md",
        "personal/辅助材料/02-旧笔记.md",
    ]


def test_search_still_returns_nothing_for_unrelated_query(tmp_path):
    assert _ranked_vault(tmp_path).search("personal", "量子计算") == []
