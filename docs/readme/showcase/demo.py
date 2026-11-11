"""Replay source-backed search and diff against a temporary synthetic vault."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    with tempfile.TemporaryDirectory(prefix="notes-demo-") as scratch:
        vault = Path(scratch) / "vault"
        shutil.copytree(Path(__file__).resolve().parents[3] / "examples/vault", vault)
        file = vault / "personal/reading.md"
        before = hashlib.sha256(file.read_bytes()).hexdigest()
        params = StdioServerParameters(command=sys.executable, args=["-m", "knowledge_mcp.server", "--vault", str(vault)], env=os.environ.copy())
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                resources = await session.list_resources()
                async def call(name, args):
                    response = await session.call_tool(name, args)
                    assert not response.isError
                    return response.structuredContent
                hits = (await call("search", {"scope": "personal", "query": "question"}))["result"]
                hit = next(h for h in hits if h.get("path") == "personal/reading.md")
                source = await call("read_range", {"scope": "personal", "path": hit["path"], "start_line": hit["line_start"], "end_line": hit["line_end"]})
                old = "Write one question after each session and check it against the source."
                diff = await call("show_diff", {"scope": "personal", "path": hit["path"], "expected_text": old, "replacement": "Write two questions after each session and check them against the source."})
                assert old in str(source)
                print(json.dumps({"tool_count": len(tools.tools), "resource_count": len(resources.resources), "matched_notes": len([h for h in hits if "path" in h]), "path": hit["path"], "line_start": hit["line_start"], "line_end": hit["line_end"], "before": next(line[1:].split(" after")[0].removeprefix("Write ") for line in diff["diff"].splitlines() if line.startswith("-Write ")), "after": next(line[1:].split(" after")[0].removeprefix("Write ") for line in diff["diff"].splitlines() if line.startswith("+Write ")), "diff": diff["diff"], "file_unchanged": before == hashlib.sha256(file.read_bytes()).hexdigest()}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
