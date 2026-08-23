#!/usr/bin/env python3
"""Build and verify deterministic, cross-host Wayfinder packages."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
from typing import Iterable, Sequence
from urllib.parse import unquote
import zipfile


ROOT = Path(__file__).resolve().parent.parent
CANONICAL_SKILL = ROOT / "skills" / "wayfinder"
CODEX_MANIFEST = ROOT / ".codex-plugin" / "plugin.json"
LICENSE_FILE = ROOT / "LICENSE"
NOTICE_FILE = ROOT / "NOTICE.md"
PACKAGE_MANIFEST_NAME = "WAYFINDER-PACKAGE.json"
CODEX_MARKETPLACE_NAME = "wayfinder-local"
PACKAGE_FORMATS = ("openai-skill", "claude-skill", "openai-plugin", "claude-plugin")
ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

EPHEMERAL_DIRECTORIES = {
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "build",
    "coverage",
    "dist",
    "htmlcov",
    "node_modules",
    "scratch",
    "temp",
    "tmp",
    "venv",
}
EPHEMERAL_FILES = {".DS_Store"}
EPHEMERAL_SUFFIXES = {".log", ".pyc", ".pyo", ".swp"}
PRIVATE_DIRECTORIES = {".agents", ".claude", ".codex", ".cursor", ".git"}
PRIVATE_STATE_DIRECTORIES = {"session", "sessions"}
PRIVATE_SUFFIXES = {".credentials.json", ".jks", ".key", ".p12", ".pem", ".pfx"}
PRIVATE_IDENTITY_FILES = {"id_dsa", "id_ecdsa", "id_ed25519", "id_rsa", "identity"}
MAX_SOURCE_FILE_BYTES = 2_000_000
MINIMUM_PYTHON = (3, 11)
SECRET_PLACEHOLDERS = ("YOUR_", "EXAMPLE_", "DUMMY_", "REDACTED", "CHANGEME")
SECRET_PATTERNS = (
    (
        "private-key",
        re.compile(
            r"-----BEGIN (?:(?:RSA|EC|DSA|OPENSSH|PGP) )?(?:ENCRYPTED )?"
            r"PRIVATE KEY(?: BLOCK)?-----"
        ),
    ),
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("github-fine-grained-token", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("aws-access-key", re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}")),
    ("stripe-live-secret", re.compile(r"sk_live_[A-Za-z0-9]{16,}")),
    ("service-token", re.compile(r"sk-[A-Za-z0-9_-]{24,}")),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_-]{30,}")),
    ("npm-token", re.compile(r"npm_[A-Za-z0-9]{24,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("bearer-token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{24,}={0,2}")),
)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret)"
    r"(?:[_-][a-z0-9]+)*"
    r"\s*[:=]\s*(?:\"([^\"\s]{16,4096})\"|'([^'\s]{16,4096})'|([A-Za-z0-9_./+=:-]{16,4096}))"
)
BINARY_CONTROL_BYTES = frozenset(range(0x20)) - {0x09, 0x0A, 0x0D}


class PackageError(ValueError):
    """Raised when source or archive content violates the package contract."""


def require_supported_python(version_info: Sequence[int] | None = None) -> None:
    current = tuple((sys.version_info if version_info is None else version_info)[:2])
    if current < MINIMUM_PYTHON:
        raise PackageError(
            "Python 3.11 or newer is required; "
            f"found {current[0]}.{current[1]}."
        )


@dataclass(frozen=True)
class SkillFile:
    path: PurePosixPath
    data: bytes
    mode: int


@dataclass(frozen=True)
class ArchiveEntry:
    path: PurePosixPath
    data: bytes
    mode: int = 0o644


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            # JSON keys are attacker-controlled when an archive is verified.
            # Report the structural defect without reflecting the key value.
            raise PackageError("duplicate JSON key")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (OSError, json.JSONDecodeError, PackageError) as exc:
        raise PackageError(f"invalid JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PackageError(f"JSON object required at {path}")
    return value


def _is_private_file(name: str) -> bool:
    lowered = name.casefold()
    return (
        lowered == ".env"
        or lowered.startswith(".env.")
        or lowered.startswith("credentials")
        or lowered.startswith("secrets")
        or lowered in PRIVATE_IDENTITY_FILES
        or any(lowered.endswith(suffix) for suffix in PRIVATE_SUFFIXES)
    )


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    return -sum(
        (count / len(value)) * math.log2(count / len(value))
        for count in Counter(value).values()
    )


def _secret_kinds(text: str) -> tuple[str, ...]:
    """Return high-confidence secret categories without retaining matched values."""

    found: set[str] = set()
    for kind, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            if not match.group(0).upper().startswith(SECRET_PLACEHOLDERS):
                found.add(kind)
    for match in SECRET_ASSIGNMENT.finditer(text):
        value = next(group for group in match.groups() if group is not None)
        if not value.upper().startswith(SECRET_PLACEHOLDERS) and _entropy(value) >= 3.0:
            found.add("credential-assignment")
    return tuple(sorted(found))


def _safe_source_label(path: PurePosixPath) -> str:
    raw = path.as_posix()
    if _secret_kinds(raw):
        return f"<redacted-path:{_sha256(raw.encode('utf-8', 'replace'))[:12]}>"
    return raw


def _redacted_source_label(path: PurePosixPath) -> str:
    raw = path.as_posix()
    return f"<redacted-path:{_sha256(raw.encode('utf-8', 'replace'))[:12]}>"


def _safe_archive_label(name: str) -> str:
    return f"<archive-path:{_sha256(name.encode('utf-8', 'replace'))[:12]}>"


def _validated_source_bytes(path: Path, relative: PurePosixPath, size: int) -> bytes:
    """Read one bounded, unambiguous text source and reject secret-like content."""

    label = _safe_source_label(relative)
    if size > MAX_SOURCE_FILE_BYTES:
        raise PackageError(
            f"skill source file exceeds the {MAX_SOURCE_FILE_BYTES}-byte package limit: {label}"
        )
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_SOURCE_FILE_BYTES + 1)
    except OSError as exc:
        raise PackageError(f"unable to read skill source file: {label}") from exc
    if len(data) > MAX_SOURCE_FILE_BYTES:
        raise PackageError(
            f"skill source file exceeds the {MAX_SOURCE_FILE_BYTES}-byte package limit: {label}"
        )
    if len(data) != size:
        raise PackageError(f"skill source file changed while packaging: {label}")
    if any(byte in BINARY_CONTROL_BYTES for byte in data):
        raise PackageError(f"skill source contains ambiguous binary content: {label}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackageError(f"skill source is not unambiguous UTF-8 text: {label}") from exc
    kinds = _secret_kinds(text)
    if kinds:
        raise PackageError(
            f"skill source contains prohibited secret-like content ({','.join(kinds)}): {label}"
        )
    return data


def collect_skill_files(skill_root: Path = CANONICAL_SKILL) -> tuple[SkillFile, ...]:
    """Return canonical regular files while rejecting unsafe/private source state."""

    require_supported_python()
    if skill_root.is_symlink():
        raise PackageError("canonical skill root must be a real directory, not a symlink")
    try:
        root = skill_root.resolve(strict=True)
    except OSError as exc:
        raise PackageError(f"canonical skill root is unavailable: {exc}") from exc
    if not root.is_dir():
        raise PackageError("canonical skill root must be a directory")

    result: list[SkillFile] = []
    for directory, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current = Path(directory)
        retained: list[str] = []
        for name in sorted(directory_names):
            path = current / name
            relative = PurePosixPath(path.relative_to(root).as_posix())
            label = _safe_source_label(relative)
            if _secret_kinds(relative.as_posix()):
                raise PackageError(f"skill source path contains prohibited secret-like content: {label}")
            if path.is_symlink():
                raise PackageError(f"skill source contains a symlink: {label}")
            if name in EPHEMERAL_DIRECTORIES:
                continue
            lowered = name.casefold()
            if lowered in PRIVATE_DIRECTORIES or lowered in PRIVATE_STATE_DIRECTORIES or name.startswith("."):
                raise PackageError(f"skill source contains private state: {_redacted_source_label(relative)}")
            retained.append(name)
        directory_names[:] = retained

        for name in sorted(file_names):
            path = current / name
            relative = PurePosixPath(path.relative_to(root).as_posix())
            label = _safe_source_label(relative)
            if _secret_kinds(relative.as_posix()):
                raise PackageError(f"skill source path contains prohibited secret-like content: {label}")
            if path.is_symlink():
                raise PackageError(f"skill source contains a symlink: {label}")
            if name in EPHEMERAL_FILES or path.suffix.casefold() in EPHEMERAL_SUFFIXES:
                continue
            if name.startswith(".") or _is_private_file(name):
                raise PackageError(f"skill source contains private state: {_redacted_source_label(relative)}")
            metadata = path.stat()
            if not stat.S_ISREG(metadata.st_mode):
                raise PackageError(f"skill source contains a non-regular file: {label}")
            mode = 0o755 if metadata.st_mode & 0o111 else 0o644
            result.append(SkillFile(relative, _validated_source_bytes(path, relative, metadata.st_size), mode))

    files = tuple(sorted(result, key=lambda item: item.path.as_posix()))
    paths = {item.path.as_posix() for item in files}
    if "SKILL.md" not in paths:
        raise PackageError("canonical skill is missing SKILL.md")
    _validate_local_links(files)
    return files


def _normalize_relative(base: PurePosixPath, target: str) -> PurePosixPath:
    if not target or target.startswith("/") or "\\" in target or "\x00" in target:
        raise PackageError(f"unsafe local link target: {target!r}")
    stack = list(base.parts)
    for part in PurePosixPath(target).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not stack:
                raise PackageError(f"local link escapes the skill package: {target}")
            stack.pop()
        else:
            stack.append(part)
    return PurePosixPath(*stack)


def _validate_local_links(files: Sequence[SkillFile]) -> None:
    available = {item.path.as_posix() for item in files}
    for item in files:
        if item.path.suffix.casefold() != ".md":
            continue
        try:
            text = item.data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PackageError(f"Markdown is not UTF-8: {item.path}") from exc
        for match in MARKDOWN_LINK.finditer(text):
            raw = match.group(1).strip().strip("<>")
            if not raw or raw.startswith(("#", "http://", "https://", "mailto:")):
                continue
            target = unquote(raw.split("#", 1)[0].split("?", 1)[0])
            normalized = _normalize_relative(item.path.parent, target).as_posix()
            if normalized not in available and not any(path.startswith(normalized + "/") for path in available):
                raise PackageError(f"broken local link in {item.path}: {target}")


def canonical_manifest(files: Sequence[SkillFile]) -> dict[str, object]:
    inventory = [
        {
            "bytes": len(item.data),
            "mode": f"{item.mode:04o}",
            "path": item.path.as_posix(),
            "sha256": _sha256(item.data),
        }
        for item in files
    ]
    return {
        "files": inventory,
        "schema_version": 1,
        "skill": "wayfinder",
        "source": "skills/wayfinder",
        "tree_sha256": _sha256(_json_bytes(inventory)),
    }


def _release_metadata() -> dict[str, object]:
    try:
        raw = CODEX_MANIFEST.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackageError("Codex manifest is unavailable") from exc
    kinds = _secret_kinds(raw)
    if kinds:
        raise PackageError(
            "Codex release metadata contains prohibited secret-like content "
            f"({','.join(kinds)})"
        )
    manifest = _load_json(CODEX_MANIFEST)
    required = ("version", "author", "homepage", "repository", "license")
    missing = [key for key in required if key not in manifest]
    if missing:
        raise PackageError(f"Codex manifest is missing release metadata: {', '.join(missing)}")
    for key in ("version", "homepage", "repository", "license"):
        if not isinstance(manifest[key], str) or not manifest[key]:
            raise PackageError(f"Codex manifest {key} must be a non-empty string")
    if SEMVER.fullmatch(manifest["version"]) is None:
        raise PackageError("Codex manifest version must be valid semantic versioning")
    author = manifest["author"]
    if not isinstance(author, dict) or not isinstance(author.get("name"), str) or not author["name"]:
        raise PackageError("Codex manifest author must contain a name")
    if "url" in author and (not isinstance(author["url"], str) or not author["url"]):
        raise PackageError("Codex manifest author URL must be a non-empty string")
    return manifest


def _canonical_skill_entry(files: Sequence[SkillFile]) -> SkillFile:
    matches = [item for item in files if item.path == PurePosixPath("SKILL.md")]
    if len(matches) != 1:
        raise PackageError("canonical skill must contain exactly one SKILL.md")
    return matches[0]


def _frontmatter_values(entry: SkillFile) -> dict[str, str]:
    try:
        text = entry.data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackageError("canonical SKILL.md must be UTF-8") from exc
    if not text.startswith("---\n"):
        raise PackageError("canonical SKILL.md frontmatter must start at byte zero")
    closing = text.find("\n---\n", 4)
    if closing < 0:
        raise PackageError("canonical SKILL.md frontmatter is not closed")
    result: dict[str, str] = {}
    for line in text[4:closing].splitlines():
        if ":" not in line:
            raise PackageError("canonical SKILL.md contains invalid frontmatter")
        key, value = line.split(":", 1)
        key = key.strip()
        if key in result:
            raise PackageError("canonical SKILL.md contains duplicate frontmatter")
        result[key] = value.strip().strip('"')
    if set(result) != {"name", "description"}:
        raise PackageError("canonical SKILL.md must contain exactly name and description")
    return result


def _claude_skill_entry(files: Sequence[SkillFile], prefix: PurePosixPath) -> ArchiveEntry:
    canonical = _canonical_skill_entry(files)
    _frontmatter_values(canonical)
    text = canonical.data.decode("utf-8")
    closing = text.find("\n---\n", 4)
    adapted = text[:closing] + "\ndisable-model-invocation: true" + text[closing:]
    return ArchiveEntry(prefix / "SKILL.md", adapted.encode("utf-8"), canonical.mode)


def _standalone_codex_manifest(metadata: dict[str, object]) -> dict[str, object]:
    author = metadata["author"]
    assert isinstance(author, dict)
    return {
        "author": author,
        "description": "A persistent decision map for ambiguous, multi-phase projects.",
        "homepage": metadata["homepage"],
        "interface": {
            "capabilities": ["Planning", "Research", "Code"],
            "category": "Developer Tools",
            "defaultPrompt": ["Map this uncertain effort with Wayfinder."],
            "developerName": author["name"],
            "displayName": "Wayfinder",
            "longDescription": "A decision-first planning workflow with evidence, checkpoints, and a local project dashboard.",
            "shortDescription": "Map uncertain projects into a decision-complete route.",
            "websiteURL": metadata["homepage"],
        },
        "keywords": ["codex", "planning", "skills", "wayfinder"],
        "license": metadata["license"],
        "name": "wayfinder",
        "repository": metadata["repository"],
        "skills": "./skills/",
        "version": metadata["version"],
    }


def _standalone_claude_manifest(metadata: dict[str, object]) -> dict[str, object]:
    author = metadata["author"]
    assert isinstance(author, dict)
    claude_author = {"name": author["name"]}
    if isinstance(author.get("url"), str):
        claude_author["url"] = author["url"]
    return {
        "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
        "author": claude_author,
        "description": "A persistent decision map for ambiguous, multi-phase projects.",
        "displayName": "Wayfinder",
        "homepage": metadata["homepage"],
        "keywords": ["claude-code", "planning", "skills", "wayfinder"],
        "license": metadata["license"],
        "name": "wayfinder",
        "repository": metadata["repository"],
        "skills": "./skills/",
        "version": metadata["version"],
    }


def _standalone_codex_marketplace() -> dict[str, object]:
    """Describe the extracted archive itself as a local Codex marketplace."""

    return {
        "interface": {"displayName": "Wayfinder Local"},
        "name": CODEX_MARKETPLACE_NAME,
        "plugins": [
            {
                "category": "Developer Tools",
                "name": "wayfinder",
                "policy": {
                    "authentication": "ON_INSTALL",
                    "installation": "AVAILABLE",
                },
                "source": {
                    "path": ".",
                    "source": "local",
                },
            }
        ],
    }


def _generated_inventory(entries: Sequence[ArchiveEntry]) -> list[dict[str, object]]:
    return [
        {
            "bytes": len(entry.data),
            "mode": f"{entry.mode:04o}",
            "path": entry.path.as_posix(),
            "sha256": _sha256(entry.data),
        }
        for entry in sorted(entries, key=lambda item: item.path.as_posix())
    ]


def _release_document_entries(kind: str) -> list[ArchiveEntry]:
    """Include the exact public license and notice in every distribution."""

    prefix = PurePosixPath("wayfinder") if kind.endswith("-skill") else PurePosixPath()
    entries: list[ArchiveEntry] = []
    for name, path in (("LICENSE", LICENSE_FILE), ("NOTICE.md", NOTICE_FILE)):
        if path.is_symlink():
            raise PackageError(f"release document must not be a symlink: {name}")
        try:
            info = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise PackageError(f"release document is unavailable: {name}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise PackageError(f"release document must be a regular file: {name}")
        data = _validated_source_bytes(path, PurePosixPath(name), info.st_size)
        entries.append(ArchiveEntry(prefix / name, data, 0o644))
    return entries


def _package_manifest(
    kind: str,
    files: Sequence[SkillFile],
    metadata: dict[str, object],
    generated_entries: Sequence[ArchiveEntry],
) -> dict[str, object]:
    manifest: dict[str, object] = {
        "canonical": canonical_manifest(files),
        "generated_files": _generated_inventory(generated_entries),
        "install_layout": {
            "openai-skill": "openai-skills-directory",
            "claude-skill": "claude-skills-directory",
            "openai-plugin": "openai-plugin",
            "claude-plugin": "claude-plugin",
        }[kind],
        "package_format": kind,
        "schema_version": 1,
        "version": metadata["version"],
    }
    if kind == "openai-plugin":
        manifest["codex_marketplace"] = CODEX_MARKETPLACE_NAME
    return manifest


def expected_entries(kind: str, files: Sequence[SkillFile] | None = None) -> tuple[ArchiveEntry, ...]:
    require_supported_python()
    if kind not in PACKAGE_FORMATS:
        raise PackageError(f"unsupported package format: {kind}")
    skill_files = tuple(files) if files is not None else collect_skill_files()
    is_plugin = kind.endswith("-plugin")
    is_claude = kind.startswith("claude-")
    prefix = PurePosixPath("skills/wayfinder") if is_plugin else PurePosixPath("wayfinder")
    entries = [
        ArchiveEntry(prefix / item.path, item.data, item.mode)
        for item in skill_files
        if not (is_claude and item.path == PurePosixPath("SKILL.md"))
    ]
    metadata = _release_metadata()
    generated_entries: list[ArchiveEntry] = _release_document_entries(kind)
    entries.extend(generated_entries)
    if kind == "claude-skill":
        generated_entries.append(_claude_skill_entry(skill_files, prefix))
        entries.append(generated_entries[-1])
    elif kind == "openai-plugin":
        adapter_entries = [
            ArchiveEntry(PurePosixPath(".codex-plugin/plugin.json"), _json_bytes(_standalone_codex_manifest(metadata))),
            ArchiveEntry(
                PurePosixPath(".agents/plugins/marketplace.json"),
                _json_bytes(_standalone_codex_marketplace()),
            ),
        ]
        generated_entries.extend(adapter_entries)
        entries.extend(adapter_entries)
    elif kind == "claude-plugin":
        adapter_entries = [
            ArchiveEntry(PurePosixPath(".claude-plugin/plugin.json"), _json_bytes(_standalone_claude_manifest(metadata))),
            _claude_skill_entry(skill_files, prefix),
        ]
        generated_entries.extend(adapter_entries)
        entries.extend(adapter_entries)
    entries.append(
        ArchiveEntry(
            PurePosixPath(PACKAGE_MANIFEST_NAME),
            _json_bytes(_package_manifest(kind, skill_files, metadata, generated_entries)),
        )
    )
    return tuple(sorted(entries, key=lambda item: item.path.as_posix()))


def _zip_info(entry: ArchiveEntry) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(entry.path.as_posix(), ARCHIVE_TIMESTAMP)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = (stat.S_IFREG | entry.mode) << 16
    return info


def _write_archive(path: Path, entries: Sequence[ArchiveEntry], force: bool) -> None:
    if path.exists() and not force:
        raise PackageError(f"output already exists (pass --force to replace it): {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(temporary, mode="w", compression=zipfile.ZIP_STORED) as archive:
            for entry in entries:
                archive.writestr(_zip_info(entry), entry.data)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_packages(output_directory: Path, formats: Iterable[str], force: bool = False) -> tuple[Path, ...]:
    require_supported_python()
    files = collect_skill_files()
    version = _release_metadata()["version"]
    if not isinstance(version, str) or not version:
        raise PackageError("release version must be a non-empty string")
    outputs: list[Path] = []
    for kind in formats:
        path = output_directory / f"wayfinder-{kind}-{version}.zip"
        _write_archive(path, expected_entries(kind, files), force)
        verify_archive(path, kind=kind, files=files)
        outputs.append(path)
    return tuple(outputs)


def _safe_archive_name(name: str) -> PurePosixPath:
    label = _safe_archive_label(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or "\x00" in name
        or re.match(r"^[A-Za-z]:", name)
    ):
        raise PackageError(f"unsafe archive path: {label!r}")
    path = PurePosixPath(name)
    if path.as_posix() != name or any(part in {"", ".", ".."} for part in path.parts):
        raise PackageError(f"unsafe archive path: {label!r}")
    return path


def verify_archive(path: Path, *, kind: str | None = None, files: Sequence[SkillFile] | None = None) -> dict[str, object]:
    require_supported_python()
    skill_files = tuple(files) if files is not None else collect_skill_files()
    try:
        with zipfile.ZipFile(path, mode="r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise PackageError("archive contains duplicate entries")
            for info in infos:
                _safe_archive_name(info.filename)
                label = _safe_archive_label(info.filename)
                file_type = (info.external_attr >> 16) & 0o170000
                if info.is_dir() or file_type != stat.S_IFREG:
                    raise PackageError(f"archive entry is not a real file: {label}")
                if info.date_time != ARCHIVE_TIMESTAMP or info.compress_type != zipfile.ZIP_STORED:
                    raise PackageError(f"archive entry is not deterministic: {label}")
            if PACKAGE_MANIFEST_NAME not in names:
                raise PackageError(f"archive is missing {PACKAGE_MANIFEST_NAME}")
            package_info = archive.getinfo(PACKAGE_MANIFEST_NAME)
            if package_info.file_size > 2_000_000:
                raise PackageError("package manifest exceeds the 2 MB validation limit")
            try:
                package_manifest = json.loads(
                    archive.read(PACKAGE_MANIFEST_NAME),
                    object_pairs_hook=_no_duplicate_keys,
                )
            except (UnicodeDecodeError, json.JSONDecodeError, PackageError) as exc:
                raise PackageError(f"invalid package manifest: {exc}") from exc
            detected = package_manifest.get("package_format") if isinstance(package_manifest, dict) else None
            package_kind = kind or detected
            if package_kind not in PACKAGE_FORMATS or detected != package_kind:
                raise PackageError("package format is missing or inconsistent")
            expected = expected_entries(package_kind, skill_files)
            expected_by_name = {entry.path.as_posix(): entry for entry in expected}
            if set(names) != set(expected_by_name):
                missing = sorted(set(expected_by_name) - set(names))
                extra = [_safe_archive_label(item) for item in sorted(set(names) - set(expected_by_name))]
                raise PackageError(f"archive inventory mismatch (missing={missing}, extra={extra})")
            for info in infos:
                expected_entry = expected_by_name[info.filename]
                label = _safe_archive_label(info.filename)
                if info.file_size != len(expected_entry.data):
                    raise PackageError(f"archive size differs from canonical source: {label}")
                if archive.read(info) != expected_entry.data:
                    raise PackageError(f"archive content differs from canonical source: {label}")
                mode = (info.external_attr >> 16) & 0o777
                if mode != expected_entry.mode:
                    raise PackageError(f"archive mode differs from canonical source: {label}")
    except (OSError, zipfile.BadZipFile) as exc:
        # The archive path and the ZIP parser's diagnostic can both contain
        # attacker-controlled text. Keep verification failures non-reflective.
        raise PackageError("invalid archive: unreadable or malformed ZIP") from exc
    return {
        "archive": str(path),
        "format": package_kind,
        "sha256": _sha256(path.read_bytes()),
        "tree_sha256": canonical_manifest(skill_files)["tree_sha256"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    build = subcommands.add_parser("build", help="build deterministic development or release archives")
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--format", choices=("all", *PACKAGE_FORMATS), default="all")
    build.add_argument("--force", action="store_true")
    verify = subcommands.add_parser("verify", help="verify archives against the canonical skill")
    verify.add_argument("archives", type=Path, nargs="+")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        require_supported_python()
        arguments = _parser().parse_args(argv)
        if arguments.command == "build":
            formats = PACKAGE_FORMATS if arguments.format == "all" else (arguments.format,)
            results = [verify_archive(path) for path in build_packages(arguments.output_dir, formats, arguments.force)]
        else:
            results = [verify_archive(path) for path in arguments.archives]
    except PackageError as exc:
        print(f"ERROR {exc}")
        return 1
    for result in results:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
