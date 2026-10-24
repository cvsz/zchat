#!/usr/bin/env python3
"""Validate ztemplate structure and local Markdown links."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PATHS = (
    "README.md",
    "ABOUT.md",
    "AGENTS.md",
    "CLAUDE.md",
    "OPENCODE.md",
    "ZEAZ-INTRODUCTION.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "CHANGELOG.md",
    "ROADMAP.md",
    "IMPLEMENTATION-CHECKLIST.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/dependabot.yml",
    ".github/workflows/ci.yml",
    "docs/ai/README.md",
    "docs/ai/guides",
    "docs/ai/playbooks",
    "docs/ai/prompts",
    "skills/zeaz-skill-finder/SKILL.md",
    "components.d/zeaz-engineering.yml",
    "plugins.d/zeaz-skills.yml",
    "ecc-install.json",
    "scripts/github_admin.py",
    "docs/ai/guides/github-repository-admin.md",
)

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    ".venv-lin",
    ".venv-runtime",
    ".venv-win",
    ".venv-observer",
    "venv",
    "build",
    "dist",
    ".ruff_cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "camfrog-client",
    "line",
    "reports",
}

LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "tel:", "#", "data:")
CHANGELOG_VERSION_RE = re.compile(r"^##\s+\[", re.MULTILINE)


def validate_required_paths() -> list[str]:
    errors: list[str] = []
    for rel in REQUIRED_PATHS:
        if not (ROOT / rel).exists():
            errors.append(f"missing required path: {rel}")
    return errors


def validate_changelog() -> list[str]:
    errors: list[str] = []
    changelog = ROOT / "CHANGELOG.md"
    if not changelog.exists():
        return ["CHANGELOG.md: file does not exist"]
    try:
        text = changelog.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"CHANGELOG.md: cannot read file: {exc}"]
    if not CHANGELOG_VERSION_RE.search(text):
        errors.append("CHANGELOG.md: missing version heading (expected '## [')")
    return errors


def normalize_link_target(raw: str) -> str:
    target = raw.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = target.split("#", 1)[0].split("?", 1)[0]
    return unquote(target)


def find_markdown_files() -> list[Path]:
    md_paths: list[Path] = []
    def fail_on_walk_error(error: OSError) -> None:
        raise error

    for dirpath, dirnames, filenames in os.walk(ROOT, onerror=fail_on_walk_error):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for filename in filenames:
            if filename.endswith(".md"):
                md = Path(dirpath) / filename
                if not any(part in EXCLUDED_DIRS for part in md.relative_to(ROOT).parts):
                    md_paths.append(md)
    return sorted(md_paths)


def validate_markdown_links() -> list[str]:
    errors: list[str] = []
    try:
        markdown_files = find_markdown_files()
    except OSError as exc:
        return [f"repository scan failed: {exc}"]
    for md in markdown_files:
        if any(part in EXCLUDED_DIRS for part in md.relative_to(ROOT).parts):
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"{md.relative_to(ROOT)}: cannot read file: {exc}")
            continue
        for match in LINK_RE.finditer(text):
            raw = match.group(1).strip()
            if not raw or raw.startswith(SKIP_PREFIXES):
                continue
            target = normalize_link_target(raw)
            if not target:
                continue
            link_base = ROOT if md.relative_to(ROOT).as_posix() == "templates/project-readme.md" else md.parent
            resolved = (link_base / target).resolve()
            try:
                resolved.relative_to(ROOT.resolve())
            except ValueError:
                errors.append(f"{md.relative_to(ROOT)}: link escapes repository: {raw}")
                continue
            if not resolved.exists():
                errors.append(f"{md.relative_to(ROOT)}: broken local link: {raw}")
    return errors


def validate_all() -> list[str]:
    return validate_required_paths() + validate_changelog() + validate_markdown_links()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate ztemplate structure and local Markdown links.")
    parser.add_argument("--json", action="store_true", help="output errors as JSON array to stdout")
    parser.add_argument("--fix", action="store_true", help="request automatic fixes (not implemented)")
    args = parser.parse_args(argv)

    if args.fix:
        print("ERROR: --fix is not implemented; no changes were made.", file=sys.stderr)
        return 2

    errors = validate_all()
    if args.json:
        print(json.dumps(errors, indent=2))
        return 1 if errors else 0

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Repository structure and local Markdown links are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
