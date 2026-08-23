#!/usr/bin/env python3
"""Scan the current repository and reachable Git history for likely secrets."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterable
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
MAX_FILE = 16_000_000
MAX_HISTORY_COMMITS = 100_000
MAX_HISTORY_PATHS = 2_000_000
PLACEHOLDERS = ("YOUR_", "EXAMPLE_", "DUMMY_", "REDACTED", "CHANGEME")


def detector_patterns() -> list[tuple[str, re.Pattern[str]]]:
    return [
        (
            "private-key",
            re.compile(
                "-----BEGIN "
                + r"(?:(?:RSA|EC|DSA|OPENSSH|PGP) )?(?:ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----"
            ),
        ),
        ("github-token", re.compile("gh" + r"[pousr]_[A-Za-z0-9]{30,}")),
        ("github-fine-grained-token", re.compile("github" + r"_pat_[A-Za-z0-9_]{20,}")),
        ("aws-access-key", re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}")),
        ("slack-token", re.compile("xo" + r"x[baprs]-[A-Za-z0-9-]{20,}")),
        ("stripe-live-secret", re.compile("sk" + r"_live_[A-Za-z0-9]{16,}")),
        ("service-token", re.compile("sk" + r"-[A-Za-z0-9_-]{24,}")),
        ("google-api-key", re.compile("AI" + r"za[0-9A-Za-z_-]{30,}")),
        ("npm-token", re.compile("npm" + r"_[A-Za-z0-9]{24,}")),
        ("jwt", re.compile("eyJ" + r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
        ("bearer-token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{24,}={0,2}")),
    ]


ASSIGNMENT = re.compile(
    r"(?i)(?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret)"
    r"(?:[_-][a-z0-9]+)*"
    r"\s*[:=]\s*(?:\"([^\"\s]{16,4096})\"|'([^'\s]{16,4096})'|([A-Za-z0-9_./+=:-]{16,4096}))"
)


def entropy(value: str) -> float:
    if not value:
        return 0.0
    return -sum((count / len(value)) * math.log2(count / len(value)) for count in Counter(value).values())


def findings(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for detector, pattern in detector_patterns():
        for match in pattern.finditer(text):
            value = match.group(0)
            if not value.upper().startswith(PLACEHOLDERS):
                found.append((detector, value))
    for match in ASSIGNMENT.finditer(text):
        value = next(group for group in match.groups() if group is not None)
        if not value.upper().startswith(PLACEHOLDERS) and entropy(value) >= 3.5:
            found.append(("high-entropy-secret-assignment", value))
    return found


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]


def safe_label(value: str) -> str:
    return json.dumps(value[:500], ensure_ascii=True)[1:-1]


def report_label(value: str) -> str:
    if findings(value):
        return f"<redacted-path:{fingerprint(value)}>"
    return safe_label(value)


def git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    return environment


def run_git(
    args: list[str], input_text: str | None = None, root: Path = ROOT
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        env=git_environment(),
        timeout=30,
    )


def worktree_files(root: Path = ROOT) -> Iterable[tuple[str, bytes]]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=False,
        env=git_environment(),
        timeout=10,
    )
    if listed.returncode == 0:
        paths = [item for item in listed.stdout.split(b"\0") if item]
        for raw in paths:
            path = root / raw.decode("utf-8", "surrogateescape")
            if not path.is_symlink() and path.is_file():
                label = path.relative_to(root).as_posix()
                if path.stat().st_size > MAX_FILE:
                    raise RuntimeError(f"unscanned file exceeds {MAX_FILE} bytes: {report_label(label)}")
                yield label, path.read_bytes()
        return
    resolved_root = root.resolve(strict=True)
    for path in root.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file() or ".git" in path.parts:
                continue
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(resolved_root):
                raise RuntimeError("fallback path escapes the scan root")
            label = path.relative_to(root).as_posix()
            if path.stat().st_size > MAX_FILE:
                raise RuntimeError(f"unscanned file exceeds {MAX_FILE} bytes: {report_label(label)}")
            yield label, path.read_bytes()
        except OSError as exc:
            raise RuntimeError("unable to inspect a fallback worktree path") from exc


def index_records(root: Path = ROOT) -> Iterable[tuple[str, bytes]]:
    listed = subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=root,
        capture_output=True,
        check=False,
        env=git_environment(),
        timeout=10,
    )
    if listed.returncode != 0:
        raise RuntimeError("unable to enumerate the Git index")
    for record in (item for item in listed.stdout.split(b"\0") if item):
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise RuntimeError("unable to parse a Git index entry")
        mode, object_id, stage = fields
        label = raw_path.decode("utf-8", "surrogateescape")
        if stage != b"0":
            raise RuntimeError(f"unmerged index entry cannot be scanned safely: {report_label(label)}")
        if mode == b"160000":
            yield f"{label}@index", b""
            continue
        size = subprocess.run(
            ["git", "cat-file", "-s", object_id.decode("ascii")],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            env=git_environment(),
            timeout=10,
        )
        if size.returncode != 0:
            raise RuntimeError("unable to inspect an indexed Git object")
        if int(size.stdout.strip()) > MAX_FILE:
            raise RuntimeError(f"unscanned index object exceeds {MAX_FILE} bytes: {report_label(label)}")
        blob = subprocess.run(
            ["git", "cat-file", "blob", object_id.decode("ascii")],
            cwd=root,
            capture_output=True,
            check=False,
            env=git_environment(),
            timeout=10,
        )
        if blob.returncode != 0:
            raise RuntimeError("unable to read an indexed Git object")
        yield f"{label}@index", blob.stdout


def scan_records(records: Iterable[tuple[str, bytes]], scope: str) -> list[str]:
    reports: list[str] = []
    for label, raw in records:
        display = report_label(label)
        for detector, value in findings(label):
            reports.append(f"{scope}:{display}:path-{detector}:{fingerprint(value)}")
        decoded = [raw.decode("latin-1")]
        if b"\x00" in raw and len(raw) % 2 == 0:
            for encoding in ("utf-16-le", "utf-16-be"):
                try:
                    decoded.append(raw.decode(encoding))
                except UnicodeDecodeError:
                    pass
        for text in decoded:
            for detector, value in findings(text):
                reports.append(f"{scope}:{display}:{detector}:{fingerprint(value)}")
    return reports


def history_records(root: Path = ROOT) -> Iterable[tuple[str, bytes]]:
    inside = run_git(["rev-parse", "--is-inside-work-tree"], root=root)
    if inside.returncode != 0:
        return
    replacements = run_git(["for-each-ref", "--format=%(refname)", "refs/replace/"], root=root)
    if replacements.returncode != 0:
        raise RuntimeError("unable to inspect Git replacement references")
    if replacements.stdout.strip():
        raise RuntimeError("history scan refuses Git replacement references")
    graft_location = run_git(["rev-parse", "--path-format=absolute", "--git-path", "info/grafts"], root=root)
    if graft_location.returncode != 0 or not graft_location.stdout.strip():
        raise RuntimeError("unable to resolve the Git graft path")
    if os.path.lexists(graft_location.stdout.strip()):
        raise RuntimeError("history scan refuses Git grafts")
    shallow = run_git(["rev-parse", "--is-shallow-repository"], root=root)
    if shallow.returncode == 0 and shallow.stdout.strip() == "true":
        raise RuntimeError("history scan refuses a shallow repository")
    refs = run_git(["for-each-ref", "--format=%(refname)"], root=root)
    if refs.returncode != 0:
        raise RuntimeError("unable to enumerate Git references")
    for refname in (line for line in refs.stdout.splitlines() if line):
        yield f"ref:{refname}", b""
    commits = run_git(["rev-list", "--all"], root=root)
    if commits.returncode != 0:
        raise RuntimeError("unable to enumerate reachable commits")
    commit_ids = [line for line in commits.stdout.splitlines() if line]
    if len(commit_ids) > MAX_HISTORY_COMMITS:
        raise RuntimeError(f"history exceeds the {MAX_HISTORY_COMMITS} commit scan limit")
    seen_paths: set[tuple[str, str]] = set()
    for commit_id in commit_ids:
        tree = subprocess.run(
            ["git", "ls-tree", "-r", "-z", "--full-tree", commit_id],
            cwd=root,
            capture_output=True,
            check=False,
            env=git_environment(),
            timeout=30,
        )
        if tree.returncode != 0:
            raise RuntimeError("unable to enumerate a reachable Git tree")
        for record in (item for item in tree.stdout.split(b"\0") if item):
            metadata, separator, raw_path = record.partition(b"\t")
            fields = metadata.split()
            if not separator or len(fields) != 3:
                raise RuntimeError("unable to parse a reachable Git tree entry")
            _, _, raw_object_id = fields
            try:
                object_id = raw_object_id.decode("ascii")
                label = raw_path.decode("utf-8", "surrogateescape")
            except UnicodeDecodeError as exc:
                raise RuntimeError("unable to decode a reachable Git tree entry") from exc
            identity = (object_id, label)
            if identity in seen_paths:
                continue
            seen_paths.add(identity)
            if len(seen_paths) > MAX_HISTORY_PATHS:
                raise RuntimeError(f"history exceeds the {MAX_HISTORY_PATHS} path scan limit")
            yield f"{label}@{object_id[:12]}", b""
    objects = run_git(["-c", "core.quotePath=true", "rev-list", "--objects", "--all"], root=root)
    if objects.returncode != 0:
        raise RuntimeError("unable to enumerate Git history")
    paths: dict[str, str] = {}
    for line in objects.stdout.splitlines():
        object_id, _, path = line.partition(" ")
        paths.setdefault(object_id, path or "<unmapped>")
    if not paths:
        return
    checked = run_git(
        ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        "\n".join(paths) + "\n",
        root,
    )
    if checked.returncode != 0:
        raise RuntimeError("unable to inspect Git objects")
    for line in checked.stdout.splitlines():
        object_id, kind, size_text = line.split(" ", 2)
        if kind not in {"blob", "commit", "tag"}:
            continue
        size = int(size_text)
        if size > MAX_FILE:
            label = paths.get(object_id, f"<{kind}>")
            raise RuntimeError(f"unscanned Git object exceeds {MAX_FILE} bytes: {report_label(label)}")
        blob = subprocess.run(
            ["git", "cat-file", kind, object_id],
            cwd=root,
            capture_output=True,
            check=False,
            env=git_environment(),
            timeout=10,
        )
        if blob.returncode == 0:
            label = paths.get(object_id, f"<{kind}>")
            yield f"{label}@{object_id[:12]}", blob.stdout
        else:
            raise RuntimeError("unable to read a reachable Git object")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="scan worktree and reachable history")
    group.add_argument("--worktree", action="store_true", help="scan current tracked and untracked files")
    group.add_argument("--history", action="store_true", help="scan all reachable Git blobs")
    parser.add_argument("--json", action="store_true", help="emit machine-readable redacted results")
    args = parser.parse_args()

    scan_worktree = args.all or args.worktree or not args.history
    scan_history = args.all or args.history
    reports: list[str] = []
    try:
        if scan_worktree:
            reports.extend(scan_records(worktree_files(), "worktree"))
            reports.extend(scan_records(index_records(), "index"))
        if scan_history:
            reports.extend(scan_records(history_records(), "history"))
    except RuntimeError as exc:
        print(f"Secret scan error: {exc}", file=sys.stderr)
        return 2
    reports = sorted(set(reports))
    if args.json:
        print(json.dumps({"clean": not reports, "findings": reports}, separators=(",", ":")))
    elif reports:
        for report in reports:
            print(f"POTENTIAL_SECRET {report}")
        print(f"Secret scan failed with {len(reports)} redacted finding(s).")
    else:
        scopes = "worktree, index, and history" if scan_worktree and scan_history else "worktree and index" if scan_worktree else "history"
        print(f"Secret scan clean: {scopes}.")
    return 1 if reports else 0


if __name__ == "__main__":
    raise SystemExit(main())
