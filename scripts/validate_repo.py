#!/usr/bin/env python3
"""Validate the standalone public Wayfinder repository contract."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import sys


ROOT = Path(__file__).resolve().parent.parent
MINIMUM_PYTHON = (3, 11)
VERSION = "0.2.0"
REPOSITORY = "https://github.com/Mohammed-Moniem/wayfinder"
SKILL = ROOT / "skills" / "wayfinder"
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
FORBIDDEN_TEXT = (
    "0.2.0-" + "dev.1",
    "codex-" + "engineering-system",
    "[TO" + "DO:",
)
BANNED_HTTP_CLIENT = "ax" + "ios"
REQUIRED_FILES = (
    ".agents/plugins/marketplace.json",
    ".codex-plugin/plugin.json",
    ".github/workflows/validate.yml",
    ".github/workflows/release.yml",
    ".gitignore",
    "AGENTS.md",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "NOTICE.md",
    "README.md",
    "SECURITY.md",
    "docs/ARCHITECTURE.md",
    "docs/INSTALLATION.md",
    "docs/PORTABLE-WAYFINDER.md",
    "docs/images/dashboard.png",
    "scripts/package_wayfinder.py",
    "scripts/scan_secrets.py",
    "skills/wayfinder/SKILL.md",
    "skills/wayfinder/agents/openai.yaml",
)
FORBIDDEN_ROOTS = (
    ".claude-plugin",
    ".codex",
    ".claude",
    ".cursor",
    "build",
    "dist",
    "evals",
    "hooks",
    "node_modules",
)


def fail(message: str) -> None:
    raise ValueError(message)


def load_packager():
    path = ROOT / "scripts" / "package_wayfinder.py"
    spec = importlib.util.spec_from_file_location("wayfinder_public_packager", path)
    if spec is None or spec.loader is None:
        fail("unable to load package_wayfinder.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        fail(f"{path.relative_to(ROOT)} must start with YAML frontmatter")
    closing = text.find("\n---\n", 4)
    if closing < 0:
        fail(f"{path.relative_to(ROOT)} has unclosed YAML frontmatter")
    values: dict[str, str] = {}
    for line in text[4:closing].splitlines():
        if ":" not in line:
            fail(f"{path.relative_to(ROOT)} has malformed frontmatter")
        key, value = line.split(":", 1)
        key = key.strip()
        if key in values:
            fail(f"{path.relative_to(ROOT)} has duplicate frontmatter key")
        values[key] = value.strip().strip('"')
    return values


def validate_required_tree() -> None:
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).is_file()]
    if missing:
        fail(f"required public files are missing: {', '.join(missing)}")
    for name in FORBIDDEN_ROOTS:
        if (ROOT / name).exists() or (ROOT / name).is_symlink():
            fail(f"private, generated, or unrelated root must not be published: {name}")
    skill_names = sorted(path.name for path in (ROOT / "skills").iterdir() if path.is_dir())
    if skill_names != ["wayfinder"]:
        fail(f"public repository must contain exactly the wayfinder skill: {skill_names}")
    for path in ROOT.rglob("*"):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path.is_symlink():
            fail(f"public repository contains a symlink: {path.relative_to(ROOT)}")
        if path.is_file() and path.suffix.casefold() == ".zip":
            fail(f"generated release ZIP must not be committed: {path.relative_to(ROOT)}")


def validate_manifests() -> None:
    plugin = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    if plugin.get("name") != "wayfinder" or plugin.get("version") != VERSION:
        fail("Codex plugin identity must be wayfinder 0.2.0")
    if plugin.get("repository") != REPOSITORY or plugin.get("homepage") != REPOSITORY + "#readme":
        fail("Codex plugin repository metadata is stale")
    if plugin.get("skills") != "./skills/":
        fail("Codex plugin must expose ./skills/")
    author = plugin.get("author")
    if not isinstance(author, dict) or author.get("name") != "Mohammed Osman":
        fail("Codex plugin author metadata is incomplete")

    marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
    if marketplace.get("name") != "wayfinder-local":
        fail("marketplace name must be wayfinder-local")
    entries = marketplace.get("plugins")
    if not isinstance(entries, list) or len(entries) != 1:
        fail("marketplace must contain exactly one plugin")
    entry = entries[0]
    if entry.get("name") != "wayfinder" or entry.get("source") != {"source": "local", "path": "."}:
        fail("marketplace must point at the standalone plugin root")

    values = frontmatter(SKILL / "SKILL.md")
    if set(values) != {"name", "description"} or values["name"] != "wayfinder":
        fail("canonical SKILL.md must contain exactly name and description")
    if "explicit" not in values["description"].casefold():
        fail("skill description must preserve the explicit-invocation boundary")
    openai = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
    if "allow_implicit_invocation: false" not in openai:
        fail("OpenAI metadata must disable implicit invocation")


def validate_text_and_links() -> None:
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            if path.suffix.casefold() not in {".png"}:
                fail(f"unexpected binary file: {path.relative_to(ROOT)}")
            continue
        relative = path.relative_to(ROOT)
        for marker in FORBIDDEN_TEXT:
            if marker.casefold() in text.casefold():
                fail(f"stale or unfinished public text in {relative}: {marker}")
        if re.search(rf"(?i)\b{re.escape(BANNED_HTTP_CLIENT)}\b", text):
            fail(f"banned HTTP client reference in {relative}")
        if relative.parts[:1] == ("tests",) and "Mohammed Osman" in text:
            fail(f"test fixture contains a real-person name: {relative}")
        if path.suffix.casefold() != ".md":
            continue
        for raw in MARKDOWN_LINK.findall(text):
            raw = raw.strip().strip("<>")
            if not raw or raw.startswith(("#", "http://", "https://", "mailto:")):
                continue
            target = raw.split("#", 1)[0].split("?", 1)[0]
            candidate = (path.parent / target).resolve()
            try:
                candidate.relative_to(ROOT.resolve())
            except ValueError:
                fail(f"Markdown link escapes repository in {relative}")
            if not candidate.exists():
                fail(f"broken Markdown link in {relative}: {target}")


def validate_package_contract() -> None:
    packager = load_packager()
    files = packager.collect_skill_files()
    if not files or files[0].path != PurePosixPath("SKILL.md"):
        fail("canonical skill inventory is missing SKILL.md")
    for kind in packager.PACKAGE_FORMATS:
        entries = packager.expected_entries(kind, files)
        names = {entry.path.as_posix() for entry in entries}
        license_path = "wayfinder/LICENSE" if kind.endswith("-skill") else "LICENSE"
        notice_path = "wayfinder/NOTICE.md" if kind.endswith("-skill") else "NOTICE.md"
        if license_path not in names or notice_path not in names:
            fail(f"{kind} omits license or notice")


def main() -> int:
    if tuple(sys.version_info[:2]) < MINIMUM_PYTHON:
        print("Validation requires Python 3.11 or newer.", file=sys.stderr)
        return 2
    try:
        validate_required_tree()
        validate_manifests()
        validate_text_and_links()
        validate_package_contract()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 1
    file_count = sum(
        1
        for path in ROOT.rglob("*")
        if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts
    )
    print(f"Validation passed: standalone Wayfinder {VERSION}, {file_count} public files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
