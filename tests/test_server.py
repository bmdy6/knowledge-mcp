from pathlib import Path
import os
import subprocess
import sys

import pytest

from knowledge_mcp import server
from knowledge_mcp.vault import Vault


def test_startup_requires_explicit_vault(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment.pop("KNOWLEDGE_VAULT", None)
    environment["KNOWLEDGE_MCP_ROOT"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "knowledge_mcp.server"],
        cwd=tmp_path,
        env=environment,
        input="",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        "knowledge-mcp: set --vault <path> or KNOWLEDGE_VAULT"
    ]


def test_get_vault_uses_explicit_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_VAULT", str(tmp_path))
    assert server.get_vault().root == tmp_path.resolve()


def test_cli_vault_overrides_environment(tmp_path: Path, monkeypatch) -> None:
    environment_vault = tmp_path / "environment"
    cli_vault = tmp_path / "cli"
    environment_vault.mkdir()
    cli_vault.mkdir()
    monkeypatch.setenv("KNOWLEDGE_VAULT", str(environment_vault))
    roots = []
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: roots.append(server.get_vault().root))
    server.main(["--vault", str(cli_vault)])
    assert roots == [cli_vault.resolve()]


def test_cli_uses_environment_without_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_VAULT", str(tmp_path))
    roots = []
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: roots.append(server.get_vault().root))
    server.main([])
    assert roots == [tmp_path.resolve()]


def test_cli_rejects_missing_vault_directory(tmp_path: Path, monkeypatch, capsys) -> None:
    with pytest.raises(SystemExit) as error:
        server.main(["--vault", str(tmp_path / "missing")])
    assert error.value.code == 2
    assert "vault must be an existing directory" in capsys.readouterr().err


def test_validate_vault_returns_serialized_findings(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "personal/invalid.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "---\nscope: company\n---\n# Invalid\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "get_vault", lambda: Vault(tmp_path))

    findings = server.validate_vault("personal")

    assert findings[0]["code"] == "scope-mismatch"
    assert findings[0]["path"] == "personal/invalid.md"


def test_create_document_passes_series_and_sources_to_vault(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(server, "get_vault", lambda: Vault(tmp_path))

    result = server.create_document(
        "personal",
        "personal/40-knowledge/series-note.md",
        "系列文章",
        "正文",
        series="示范系列",
        sources=["archive/original.md"],
    )

    content = (tmp_path / result["path"]).read_text(encoding="utf-8")
    assert "series: 示范系列" in content
    assert "  - archive/original.md" in content
    assert "status: review" in content


def test_create_document_tool_rejects_status_argument(
    tmp_path: Path, monkeypatch
) -> None:
    """A stale caller passing ``status`` must fail loudly, not be ignored."""
    monkeypatch.setattr(server, "get_vault", lambda: Vault(tmp_path))

    with pytest.raises(TypeError):
        server.create_document(
            "personal",
            "personal/40-knowledge/active-note.md",
            "想直接确认",
            "正文",
            status="active",
        )

    assert not (tmp_path / "personal/40-knowledge/active-note.md").exists()
