import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_search_returns_healthy_matches_and_visible_partial_diagnostic(tmp_path: Path) -> None:
    (tmp_path / "personal").mkdir()
    (tmp_path / "personal/healthy.md").write_text("# Healthy\nquestion\n", encoding="utf-8")
    (tmp_path / "personal/broken.md").write_text(
        "---\ntitle: [private-marker\n---\n", encoding="utf-8"
    )
    asyncio.run(_assert_partial_search(tmp_path))


async def _assert_partial_search(root: Path) -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "knowledge_mcp.server", "--vault", str(root)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for query in ("question", "no-match"):
                response = await session.call_tool("search", {"scope": "personal", "query": query})
                assert not response.isError
                entries = response.structuredContent["result"]
                diagnostic = entries[-1]
                assert diagnostic["partial"] is True
                assert "Partial search" in diagnostic["warning"]
                assert diagnostic["skipped"] == [{"path": "personal/broken.md", "code": "invalid-frontmatter"}]
                content = "\n".join(item.text for item in response.content)
                assert "Partial search" in content
                assert "private-marker" not in content
                assert str(root) not in content
                assert json.loads(response.content[-1].text) == diagnostic
                matches = entries[:-1]
                if query == "question":
                    assert [match["path"] for match in matches] == ["personal/healthy.md"]
                    match = matches[0]
                    source = await session.call_tool("read_range", {
                        "scope": "personal", "path": match["path"],
                        "start_line": match["line_start"], "end_line": match["line_end"],
                    })
                    assert not source.isError
                    assert "question" in str(source.structuredContent)
                else:
                    assert matches == []
            (root / "personal/broken.md").unlink()
            healthy = await session.call_tool("search", {"scope": "personal", "query": "question"})
            assert not healthy.isError
            assert [entry["path"] for entry in healthy.structuredContent["result"]] == ["personal/healthy.md"]
            assert len(healthy.content) == 1
            empty = await session.call_tool("search", {"scope": "personal", "query": "no-match"})
            assert empty.structuredContent == {"result": []}
            assert empty.content == []
    assert not (root / ".knowledge-mcp.lock").exists()


def test_stdio_exposes_tools_resources_and_validation(tmp_path: Path) -> None:
    (tmp_path / "personal").mkdir(parents=True, exist_ok=True)
    (tmp_path / "personal/README.md").write_text(
        "---\ntitle: Personal\nscope: personal\n---\n# Personal\n",
        encoding="utf-8",
    )
    (tmp_path / "company").mkdir(parents=True, exist_ok=True)
    (tmp_path / "company/README.md").write_text(
        "---\ntitle: Company\nscope: company\n---\n# Company\n",
        encoding="utf-8",
    )
    asyncio.run(_assert_stdio_contract(tmp_path))


async def _assert_stdio_contract(root: Path) -> None:
    environment = os.environ.copy()
    environment["KNOWLEDGE_VAULT"] = str(root)
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(
            None,
            [
                str(Path(__file__).resolve().parents[1] / "src"),
                environment.get("PYTHONPATH", ""),
            ],
        )
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "knowledge_mcp.server"],
        env=environment,
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            assert {"search", "read", "validate_vault", "create_document", "move_document"} <= tool_names
            assert "record_daily_note" not in tool_names
            by_name = {tool.name: tool for tool in tools.tools}
            for name in ("search", "list_documents", "validate_vault", "read", "read_range", "show_diff"):
                assert by_name[name].annotations.readOnlyHint is True, name
            for name in ("create_document", "apply_change", "move_document", "delete_document"):
                assert by_name[name].annotations.readOnlyHint is False, name
            assert by_name["delete_document"].annotations.destructiveHint is True
            create_schema = next(t for t in tools.tools if t.name == "create_document").inputSchema
            assert "replaces" in create_schema.get("properties", {})

            # ``create_document`` no longer accepts ``status``; a stale caller
            # sending it must be rejected by the protocol layer instead of being
            # silently written as ``review``. This asserts the SDK actually
            # enforces extra-argument rejection, so an upgrade that reverts to
            # silently dropping unknown fields fails here.
            create = next(t for t in tools.tools if t.name == "create_document")
            assert "status" not in create.inputSchema.get("properties", {})
            rejected = await session.call_tool(
                "create_document",
                {
                    "scope": "personal",
                    "path": "personal/00-overview/rejected.md",
                    "title": "Rejected",
                    "content": "正文",
                    "status": "active",
                },
            )
            assert rejected.isError
            assert not (root / "personal/00-overview/rejected.md").exists()

            # ``create_document`` must reject top-level H1 headings in content
            h1_rejected = await session.call_tool(
                "create_document",
                {
                    "scope": "personal",
                    "path": "personal/00-overview/h1-rejected.md",
                    "title": "H1 Rejected",
                    "content": "# Heading in Content\n\n正文",
                },
            )
            assert h1_rejected.isError
            assert not (root / "personal/00-overview/h1-rejected.md").exists()

            accepted = await session.call_tool(
                "create_document",
                {
                    "scope": "personal",
                    "path": "personal/00-overview/created.md",
                    "title": "Created",
                    "content": "正文",
                },
            )
            assert not accepted.isError
            created = (root / "personal/00-overview/created.md").read_text(
                encoding="utf-8"
            )
            assert "status: review" in created

            resources = await session.list_resources()
            resource_uris = {str(resource.uri) for resource in resources.resources}
            assert "knowledge://guide" in resource_uris
            assert "knowledge://issue-workflow" not in resource_uris
            guide = await session.read_resource("knowledge://guide")
            assert "# MCP 契约" in str(guide.contents[0].text)

            validation = await session.call_tool("validate_vault", {})
            assert not validation.isError
            assert validation.content == []
