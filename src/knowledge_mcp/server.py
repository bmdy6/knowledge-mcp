from __future__ import annotations

import argparse
import difflib
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase
from pydantic import ConfigDict

from .resources import RepositoryResources
from .vault import Vault

# FastMCP builds each tool's argument model from the Python signature and, by
# default, silently drops unknown fields. That would let a stale caller still
# send ``create_document(status="active")`` and get a normal write, so the
# removal of the parameter would be invisible instead of rejected. Forbidding
# extra fields turns any such call into a validation error before the tool runs.
# This is SDK-internal behaviour; `tests/test_stdio_mcp.py` asserts it so an SDK
# upgrade that changes it fails the suite instead of weakening the contract.
ArgModelBase.model_config = ConfigDict(
    arbitrary_types_allowed=True,
    extra="forbid",
)

mcp = FastMCP("knowledge-mcp")

# 工具注解：告诉客户端哪些工具只读。非交互客户端（如 Codex exec）会自动放行只读工具，
# 写入类工具仍需确认。写入都限制在知识库内，并且有内容校验，所以都不是 openWorld。
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
CREATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
EDIT = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
DELETE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)


def get_vault() -> Vault:
    root = os.environ.get("KNOWLEDGE_VAULT", "").strip()
    if not root:
        raise ValueError("set --vault <path> or KNOWLEDGE_VAULT")
    return Vault(Path(root).expanduser())



def get_resources() -> RepositoryResources:
    return RepositoryResources(get_vault().root)



@mcp.tool(annotations=READ)
def search(
    scope: str,
    query: str,
    limit: int = 20,
    include_archive: bool = False,
) -> list[dict[str, object]]:
    """Return matches; a final warning record marks skipped, invalid notes.

    Match records have path/line/snippet fields. A diagnostic record instead
    has warning, partial=true and skipped (relative paths and error codes).
    """
    diagnostics: list[dict[str, str]] = []
    results = [
        Vault.serialize_result(result)
        for result in get_vault().search(
            scope, query, limit=limit, include_archive=include_archive,
            diagnostics=diagnostics,
        )
    ]
    if diagnostics:
        results.append({
            "warning": "Partial search: some notes could not be searched. Repair the skipped notes and search again.",
            "partial": True,
            "skipped": diagnostics,
        })
    return results


@mcp.tool(annotations=READ)
def list_documents(
    scope: str,
    prefix: str = "",
    include_archive: bool = False,
) -> list[str]:
    """List Markdown paths within an explicitly selected scope."""
    return get_vault().list_documents(
        scope, prefix=prefix, include_archive=include_archive
    )


@mcp.tool(annotations=READ)
def validate_vault(
    scope: str | None = None,
    include_archive: bool = False,
) -> list[dict[str, str]]:
    """Validate Markdown metadata and scope boundaries without writing."""
    return [
        Vault.serialize_finding(finding)
        for finding in get_vault().validate(
            scope,
            include_archive=include_archive,
        )
    ]


@mcp.tool(annotations=READ)
def read(scope: str, path: str, include_archive: bool = False) -> str:
    """Read one Markdown document from the selected scope."""
    return get_vault().read(scope, path, include_archive=include_archive)


@mcp.tool(annotations=READ)
def read_range(
    scope: str,
    path: str,
    start_line: int,
    end_line: int,
    include_archive: bool = False,
) -> dict[str, object]:
    """Read a 1-based inclusive line range from a Markdown document."""
    return get_vault().read_range(
        scope,
        path,
        start_line,
        end_line,
        include_archive=include_archive,
    )


@mcp.tool(annotations=READ)
def show_diff(
    scope: str,
    path: str,
    expected_text: str,
    replacement: str,
) -> dict[str, str]:
    """Preview a unique, version-checked local text replacement."""
    change = get_vault().propose_change(scope, path, expected_text, replacement)
    change["diff"] = "".join(
        difflib.unified_diff(
            change["before"].splitlines(keepends=True),
            change["after"].splitlines(keepends=True),
            fromfile=change["path"],
            tofile=change["path"],
        )
    )
    return change


@mcp.tool(annotations=EDIT)
def apply_change(
    scope: str,
    path: str,
    expected_text: str,
    replacement: str,
    replaces: list[str] | None = None,
) -> dict[str, object]:
    """Apply one local text replacement after checking the current version.

    ``replaces`` lists existing documents absorbed into this one by the edit
    (for example a concept card folded into a topic article). They are deleted
    only after the patch is written, and the patched body must still hold at
    least 70% of their combined length.
    """
    return get_vault().apply_change(
        scope, path, expected_text, replacement, replaces=tuple(replaces or ())
    )


@mcp.tool(annotations=CREATE)
def create_document(
    scope: str,
    path: str,
    title: str,
    content: str,
    document_type: str = "article",
    tags: list[str] | None = None,
    series: str | None = None,
    sources: list[str] | None = None,
    created: str | None = None,
    updated: str | None = None,
    replaces: list[str] | None = None,
) -> dict[str, object]:
    """Create one Markdown document; it always starts as ``review``.

    Before creating, ``search`` for an existing document on the same topic and
    extend it with ``apply_change`` instead; new files are for new topics only.

    ``document_type``:
    - ``article``: one topic, merged from all sources, kept up to date in place;
    - ``reference``: notes on one external source (link + key excerpts, never
      the full original);
    - ``research``: a research conclusion worth keeping (not deferred/P2 notes);
    - ``guide``: repeatable steps; ``decision``: a choice and its reasons;
    - ``task`` (company): one task per file, content must contain
      ``## 任务描述`` ``## 解决思考`` ``## 解决方法`` ``## 结果`` ``## 链接``;
      ``created`` is the task date.

    No ``status`` argument is accepted: a caller (including another agent) must
    not be able to create content that already claims to be human-confirmed.
    Confirming an existing document goes through ``apply_change``.

    ``created``/``updated`` are only for importing existing documents that must
    keep their original dates; new content leaves them unset.

    ``replaces``: when merging several notes into one topic article, list the
    old document paths here. They are deleted after the new file is written,
    their ``sources`` may be re-listed in ``sources``, and the merged body must
    keep at least 70% of their combined length (drop duplicates, not ideas).
    """
    return get_vault().create_document(
        scope,
        path,
        title,
        content,
        document_type=document_type,
        tags=tags or [],
        series=series,
        sources=sources or [],
        created=created,
        updated=updated,
        replaces=replaces or [],
    )


@mcp.tool(annotations=DELETE)
def delete_document(
    scope: str,
    path: str,
    successor: str | None = None,
) -> dict[str, str]:
    """Delete one Markdown document.

    ``successor`` names the document that absorbed this one's content; when
    given, the deletion is refused unless the successor body holds at least 70%
    of the deleted body. Use it for approved cleanup (superseded notes, cards
    that were folded into an article), and omit it only for content a human has
    explicitly agreed to drop.
    """
    return get_vault().delete_document(scope, path, successor=successor)


@mcp.tool(annotations=EDIT)
def move_document(scope: str, path: str, new_path: str) -> dict[str, str]:
    """Move or rename one document within its scope; content is unchanged.

    Use it to place a file under its topic directory or drop a legacy filename
    prefix. The target must not exist; emptied old folders are removed.
    """
    return get_vault().move_document(scope, path, new_path)


@mcp.resource(
    "knowledge://guide",
    name="knowledge-guide",
    title="Knowledge repository guide",
    description="The shipped MCP protocol and safe editing workflow.",
    mime_type="text/markdown",
)
def knowledge_guide() -> str:
    return get_resources().read("guide")


@mcp.resource(
    "knowledge://content-model",
    name="knowledge-content-model",
    title="Knowledge content model",
    description="Document types, scopes, status values, and intake rules.",
    mime_type="text/markdown",
)
def knowledge_content_model() -> str:
    return get_resources().read("content-model")


@mcp.resource(
    "knowledge://architecture",
    name="knowledge-architecture",
    title="Knowledge repository architecture",
    description="The canonical architecture and non-goals of the repository.",
    mime_type="text/markdown",
)
def knowledge_architecture() -> str:
    return get_resources().read("architecture")


@mcp.resource(
    "knowledge://maintenance-skill",
    name="knowledge-maintenance-skill",
    title="Knowledge maintenance skill",
    description="Canonical rules for modifying documents safely.",
    mime_type="text/markdown",
)
def knowledge_maintenance_skill() -> str:
    return get_resources().read("maintenance-skill")


@mcp.resource(
    "knowledge://search-skill",
    name="knowledge-search-skill",
    title="Knowledge search skill",
    description="Canonical rules for returning searchable source locations.",
    mime_type="text/markdown",
)
def knowledge_search_skill() -> str:
    return get_resources().read("search-skill")


@mcp.resource(
    "knowledge://writing-skill",
    name="knowledge-writing-skill",
    title="Knowledge writing skill",
    description="Canonical rules for creating confirmed documents.",
    mime_type="text/markdown",
)
def knowledge_writing_skill() -> str:
    return get_resources().read("writing-skill")


@mcp.resource(
    "knowledge://personal-overview",
    name="knowledge-personal-overview",
    title="Personal knowledge overview",
    description="The personal scope entry document.",
    mime_type="text/markdown",
)
def knowledge_personal_overview() -> str:
    return get_resources().read("personal-overview")


@mcp.resource(
    "knowledge://company-overview",
    name="knowledge-company-overview",
    title="Company knowledge overview",
    description="The company scope entry document.",
    mime_type="text/markdown",
)
def knowledge_company_overview() -> str:
    return get_resources().read("company-overview")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="knowledge-mcp")
    parser.add_argument("--vault", help="Path to the Markdown vault (or KNOWLEDGE_VAULT)")
    arguments = parser.parse_args(argv)
    root = arguments.vault if arguments.vault is not None else os.environ.get("KNOWLEDGE_VAULT", "")
    if not root.strip():
        parser.exit(2, "knowledge-mcp: set --vault <path> or KNOWLEDGE_VAULT\n")
    vault = Path(root).expanduser().resolve()
    if not vault.is_dir():
        parser.exit(2, "knowledge-mcp: vault must be an existing directory\n")
    os.environ["KNOWLEDGE_VAULT"] = str(vault)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
