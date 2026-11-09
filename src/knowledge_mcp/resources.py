from __future__ import annotations

from pathlib import Path


RESOURCE_FILES: dict[str, str] = {
    "guide": "docs/MCP_CONTRACT.md",
    "architecture": "docs/ARCHITECTURE.md",
    "content-model": "docs/CONTENT_MODEL.md",
    "maintenance-skill": "skills/knowledge-maintenance/SKILL.md",
    "search-skill": "skills/knowledge-search/SKILL.md",
    "writing-skill": "skills/writing/SKILL.md",
    "personal-overview": "personal/README.md",
    "company-overview": "company/README.md",
}


class RepositoryResourceError(Exception):
    """Base error for repository guidance resources."""


class RepositoryResourceNotFoundError(RepositoryResourceError):
    pass


class RepositoryResources:
    """Read-only access to the small set of guidance files exposed by MCP."""

    def __init__(self, root: Path, *, guidance_root: Path | None = None):
        self.root = root.resolve()
        if guidance_root is None:
            package = Path(__file__).resolve().parent
            bundled = package / "_resources"
            guidance_root = bundled if bundled.is_dir() else package.parents[1]
        self.guidance_root = guidance_root.resolve()

    def read(self, name: str) -> str:
        relative = RESOURCE_FILES.get(name)
        if relative is None:
            raise RepositoryResourceNotFoundError(f"Unknown resource: {name}")

        is_overview = name in {"personal-overview", "company-overview"}
        base = self.root if is_overview else self.guidance_root
        unresolved = base / relative
        target = unresolved.resolve()
        boundary = base / Path(relative).parts[0] if is_overview else base
        try:
            target.relative_to(boundary)
        except ValueError as exc:
            raise RepositoryResourceNotFoundError(
                f"Resource resolves outside its allowed root: {name}"
            ) from exc
        if unresolved.is_symlink() or not target.is_file():
            raise RepositoryResourceNotFoundError(
                f"Resource file is unavailable: {relative}"
            )
        try:
            return target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RepositoryResourceNotFoundError(
                f"Resource could not be read: {relative}"
            ) from exc
