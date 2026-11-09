from pathlib import Path

import pytest

from knowledge_mcp.resources import (
    RepositoryResourceNotFoundError,
    RepositoryResources,
)


def test_repository_resources_read_canonical_files(tmp_path: Path) -> None:
    guide = tmp_path / "docs/MCP_CONTRACT.md"
    guide.parent.mkdir()
    guide.write_text("# Guide\n", encoding="utf-8")
    architecture = tmp_path / "docs/ARCHITECTURE.md"
    architecture.write_text("# Architecture\n", encoding="utf-8")
    resources = RepositoryResources(tmp_path / "vault", guidance_root=tmp_path)

    assert resources.read("guide") == "# Guide\n"
    assert resources.read("architecture") == "# Architecture\n"


def test_repository_resources_reject_unknown_or_missing_files(tmp_path: Path) -> None:
    resources = RepositoryResources(tmp_path, guidance_root=tmp_path)

    with pytest.raises(RepositoryResourceNotFoundError):
        resources.read("unknown")
    with pytest.raises(RepositoryResourceNotFoundError):
        resources.read("guide")


def test_overviews_come_from_vault_and_guidance_from_package(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    for scope in ("personal", "company"):
        path = vault / scope / "README.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"# Synthetic {scope}\n", encoding="utf-8")
    # Vault-local guidance must not replace the shipped contract.
    (vault / "docs").mkdir()
    (vault / "docs/MCP_CONTRACT.md").write_text("untrusted override", encoding="utf-8")
    resources = RepositoryResources(vault)
    assert resources.read("personal-overview") == "# Synthetic personal\n"
    assert resources.read("company-overview") == "# Synthetic company\n"
    assert "untrusted override" not in resources.read("guide")
    for name in ("guide", "architecture", "content-model", "search-skill", "maintenance-skill", "writing-skill"):
        assert resources.read(name).strip()


def test_overview_cannot_follow_link_into_another_scope(tmp_path: Path) -> None:
    (tmp_path / "personal").mkdir()
    (tmp_path / "company").mkdir()
    company = tmp_path / "company/README.md"
    company.write_text("# Synthetic company\n", encoding="utf-8")
    (tmp_path / "personal/README.md").symlink_to(company)
    with pytest.raises(RepositoryResourceNotFoundError):
        RepositoryResources(tmp_path).read("personal-overview")
