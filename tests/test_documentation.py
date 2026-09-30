from __future__ import annotations

import re
import tomllib
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).parents[1]
_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


def _anchors(markdown: str) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for heading in _HEADING.findall(markdown):
        plain = re.sub(r"[`*_~]", "", heading).strip().lower()
        slug = re.sub(r"[^\w\- ]", "", plain, flags=re.UNICODE).replace(" ", "-")
        slug = re.sub(r"-+", "-", slug)
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def test_markdown_links_and_local_anchors_resolve() -> None:
    markdown_files = [
        *ROOT.glob("*.md"),
        *ROOT.joinpath("docs").rglob("*.md"),
    ]
    assert markdown_files

    failures: list[str] = []
    for source in markdown_files:
        text = source.read_text(encoding="utf-8")
        for raw_target in _LINK.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if target.startswith(("https://", "http://", "mailto:")):
                continue
            file_part, _, fragment = target.partition("#")
            destination = (
                source if not file_part else (source.parent / unquote(file_part)).resolve()
            )
            if not destination.exists():
                failures.append(f"{source.relative_to(ROOT)} -> missing {target}")
                continue
            if fragment and destination.suffix.lower() == ".md":
                anchors = _anchors(destination.read_text(encoding="utf-8"))
                if unquote(fragment).lower() not in anchors:
                    failures.append(f"{source.relative_to(ROOT)} -> missing anchor {target}")

    assert failures == []


def test_release_documentation_is_complete_and_truthful() -> None:
    required = {
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "docs/acceptance.md",
        "docs/architecture.md",
        "docs/extensions.md",
        "docs/threat-model.md",
    }
    assert required <= {
        str(path.relative_to(ROOT)).replace("\\", "/")
        for path in [*ROOT.glob("*.md"), *ROOT.joinpath("docs").rglob("*.md")]
    }

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for target in required - {"SECURITY.md", "docs/architecture.md"}:
        assert target in readme
    assert "evalforge demo" in readme
    assert "/dashboard" in readme

    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{version}] - 2026-09-27" in changelog

    threat_model = (ROOT / "docs" / "threat-model.md").read_text(encoding="utf-8")
    for term in (
        "Trust boundaries",
        "Assets",
        "Threat actors",
        "Denial of service",
        "Prompt injection",
        "Residual risks",
        "Authentication",
    ):
        assert term in threat_model

    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "uv sync --frozen --group dev" in contributing
    assert "RED" in contributing and "GREEN" in contributing
    assert "pytest" in contributing and "mypy" in contributing and "ruff" in contributing

    extensions = (ROOT / "docs" / "extensions.md").read_text(encoding="utf-8")
    assert "CandidateAdapter" in extensions
    assert "metric" in extensions.lower()
    assert "schema_version" in extensions

    acceptance = (ROOT / "docs" / "acceptance.md").read_text(encoding="utf-8")
    assert "tests/test_release_acceptance.py" in acceptance
    assert "Authentication and multi-tenancy | Not implemented" in acceptance
    assert "Full regression analytics dashboard | Not implemented" in acceptance
    assert "M4 local operations and release hardening | Complete" in acceptance
    assert "M5 governed semantic similarity and embedding metrics | Complete" in acceptance
    assert "Remaining M5 evaluator catalog, mutations, and scorecards | In progress" in acceptance

    deployment = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    assert "--factory evalforge.server:create_app_from_environment" in deployment
    assert "evalforge-smoke --base-url http://127.0.0.1:8000" in deployment
    assert "evalforge.server:app" not in deployment
    assert "--url " not in deployment
    assert "signal handling is tested" not in deployment

    threat_model = (ROOT / "docs" / "threat-model.md").read_text(encoding="utf-8")
    assert "API body limit" not in threat_model
    assert "request deadline" not in threat_model

    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "uv run --frozen --group dev mypy\n" in contributing
    assert "mypy --strict src tests" not in contributing
