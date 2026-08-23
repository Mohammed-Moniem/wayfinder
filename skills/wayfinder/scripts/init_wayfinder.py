#!/usr/bin/env python3
"""Create a new local Wayfinder effort without external dependencies."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from typing import Iterator, Sequence
import unicodedata


SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
MINIMUM_PYTHON = (3, 11)


def require_supported_python(version_info: Sequence[int] | None = None) -> None:
    current = tuple((sys.version_info if version_info is None else version_info)[:2])
    if current < MINIMUM_PYTHON:
        raise SystemExit(
            "wayfinder-init: Python 3.11 or newer is required; "
            f"found {current[0]}.{current[1]}."
        )


require_supported_python()


def contains_unsafe_text(value: str) -> bool:
    return bool(CONTROL.search(value)) or any(
        unicodedata.category(character) in {"Zl", "Zp", "Cs"} for character in value
    )


def git_root(start: Path) -> Path:
    if start.is_symlink():
        raise SystemExit("Project root must be a real directory, not a symbolic link.")
    resolved = start.resolve(strict=True)
    if not resolved.is_dir():
        raise SystemExit("Project root must be an existing directory.")
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=resolved,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return resolved
    if result.returncode != 0:
        # Wayfinder is project-local, not Git-dependent.  When Git is absent,
        # the explicitly selected real directory is the project boundary.
        return resolved
    return Path(result.stdout.strip()).resolve(strict=True)


def require_secure_directory_api() -> None:
    required = (os.open, os.mkdir, os.rename, os.unlink)
    if (
        any(function not in os.supports_dir_fd for function in required)
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
    ):
        raise SystemExit("Secure descriptor-relative initialization is unavailable on this platform.")


def open_directory(parent: int, name: str, create: bool = False) -> int:
    if create:
        try:
            os.mkdir(name, 0o755, dir_fd=parent)
        except FileExistsError:
            pass
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(name, flags, dir_fd=parent)
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise SystemExit("Wayfinder directory validation failed.")
    return descriptor


def write_new(parent: int, name: str, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(name, flags, 0o600, dir_fd=parent)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def remove_new_tree(parent: int, name: str) -> None:
    """Remove only a newly created descriptor-relative tree without following links."""
    try:
        descriptor = open_directory(parent, name)
    except FileNotFoundError:
        return
    try:
        for entry in os.listdir(descriptor):
            metadata = os.stat(entry, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                remove_new_tree(descriptor, entry)
            else:
                os.unlink(entry, dir_fd=descriptor)
    finally:
        os.close(descriptor)
    os.rmdir(name, dir_fd=parent)


def read_active_bytes(parent: int) -> bytes | None:
    """Read exact ACTIVE bytes descriptor-relatively without following links."""
    try:
        descriptor = os.open("ACTIVE", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except FileNotFoundError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise SystemExit("Existing ACTIVE pointer must be a regular file.")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 4096:
                raise SystemExit("Existing ACTIVE pointer is unexpectedly large.")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def active_value(payload: bytes | None) -> str | None:
    if payload is None:
        return None
    try:
        value = payload.decode("utf-8").strip()
    except UnicodeError as exc:
        raise SystemExit("Existing ACTIVE pointer is not valid UTF-8.") from exc
    if contains_unsafe_text(value):
        raise SystemExit("Existing ACTIVE pointer contains unsafe control or bidirectional format characters.")
    return value or None


def read_active(parent: int) -> str | None:
    return active_value(read_active_bytes(parent))


@contextmanager
def active_lock(parent: int) -> Iterator[None]:
    """Serialize ACTIVE compare-and-swap and replacement operations."""
    lock_name = "ACTIVE.lock"
    try:
        descriptor = os.open(
            lock_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
    except FileExistsError as exc:
        raise SystemExit("Another Wayfinder process is updating ACTIVE; retry after it finishes.") from exc
    os.close(descriptor)
    try:
        yield
    finally:
        try:
            os.unlink(lock_name, dir_fd=parent)
        except FileNotFoundError:
            pass


def replace_active_bytes(parent: int, payload: bytes) -> None:
    temporary = f".ACTIVE.{secrets.token_hex(8)}"
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600, dir_fd=parent)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.rename(temporary, "ACTIVE", src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass


def replace_active(parent: int, relative_map: str) -> None:
    replace_active_bytes(parent, (relative_map + "\n").encode("utf-8"))


def restore_active(parent: int, previous: bytes | None) -> None:
    """Restore the exact pre-transaction ACTIVE bytes or exact absence."""
    if previous is None:
        try:
            os.unlink("ACTIVE", dir_fd=parent)
        except FileNotFoundError:
            pass
        os.fsync(parent)
        return
    replace_active_bytes(parent, previous)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--slug", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument(
        "--expect-active",
        help="Compare-and-swap guard for replacing ACTIVE. Omit or use NONE only when no ACTIVE pointer should exist.",
    )
    args = parser.parse_args(argv)

    if not SLUG.fullmatch(args.slug):
        raise SystemExit("Slug must be 1-64 lowercase letters, digits, or hyphens.")
    if not args.destination.strip() or len(args.destination) > 500 or contains_unsafe_text(args.destination):
        raise SystemExit("Destination must be one non-empty printable line of at most 500 characters.")
    if args.expect_active is not None and contains_unsafe_text(args.expect_active):
        raise SystemExit("--expect-active contains unsafe control or bidirectional format characters.")

    require_secure_directory_api()
    root = git_root(args.root)

    assets = Path(__file__).resolve(strict=True).parent.parent / "assets"
    title = args.slug.replace("-", " ").title()
    templates = {
        "MAP.md": assets / "MAP.md",
        "ASSUMPTIONS.md": assets / "ASSUMPTIONS.md",
        "INVARIANTS.md": assets / "INVARIANTS.md",
        "EFFORT.json": assets / "EFFORT.json",
    }
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    descriptors: list[int] = []
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(root_fd)
        codex_fd = open_directory(root_fd, ".codex", create=True)
        descriptors.append(codex_fd)
        wayfinder_fd = open_directory(codex_fd, "wayfinder", create=True)
        descriptors.append(wayfinder_fd)
        with active_lock(wayfinder_fd):
            expected_arg = args.expect_active or "NONE"
            expected = None if expected_arg.strip().upper() == "NONE" else expected_arg.strip()
            previous_active = read_active_bytes(wayfinder_fd)
            current = active_value(previous_active)
            if current != expected:
                shown = current if current is not None else "NONE"
                raise SystemExit(f"ACTIVE conflict: expected {ascii(expected_arg)}, found {ascii(shown)}.")
            efforts_fd = open_directory(wayfinder_fd, "efforts", create=True)
            descriptors.append(efforts_fd)
            staging = f".{args.slug}.staging-{secrets.token_hex(8)}"
            staging_exists = False
            final_exists = False
            staging_fd: int | None = None
            active_write_attempted = False
            try:
                os.mkdir(staging, 0o700, dir_fd=efforts_fd)
                staging_exists = True
                staging_fd = open_directory(efforts_fd, staging)
                os.mkdir("decisions", 0o755, dir_fd=staging_fd)
                os.mkdir("gates", 0o755, dir_fd=staging_fd)
                os.mkdir("evidence", 0o755, dir_fd=staging_fd)
                for name, source in templates.items():
                    content = source.read_text(encoding="utf-8")
                    if name == "EFFORT.json":
                        payload = json.loads(content)
                        effort = payload["effort"]
                        effort["id"] = args.slug
                        effort["title"] = title
                        effort["destination"] = args.destination.strip()
                        effort["destination_revision"] = 1
                        effort["created_at"] = created_at
                        effort["updated_at"] = created_at
                        # New efforts use domain-neutral planning/execution copy.
                        p4 = next(item for item in payload["phases"] if item["id"] == "p4-ready")
                        p4["label"] = "Ready for execution"
                        p4["description"] = "Perform the final consistency and exit review and create the completion handoff."
                        cp4 = next(item for item in payload["checkpoints"] if item["id"] == "cp4-handoff")
                        cp4["label"] = "Execution handoff review"
                        cp4["due_when"] = "The route appears decision-complete and is about to enter its domain-appropriate execution plan."
                        cp4["reason"] = "Run Wayfinder immediately before the route is handed to execution."
                        phase_ids = [item["id"] for item in payload["phases"]]
                        current_index = phase_ids.index(payload["current_phase_id"])
                        for index, phase in enumerate(payload["phases"]):
                            phase["state"] = "active" if index == current_index else ("complete" if index < current_index else "upcoming")
                        for index, checkpoint in enumerate(payload["checkpoints"]):
                            checkpoint["status"] = (
                                "COMPLETE"
                                if index < current_index
                                else ("DUE" if index == current_index else "UPCOMING")
                            )
                            checkpoint["completed_at"] = None
                        for index, milestone in enumerate(payload["milestones"]):
                            milestone["status"] = "COMPLETE" if index < current_index else "PENDING"
                        content = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
                    else:
                        # Insert untrusted/user destination text last so literal
                        # scaffold tokens in it are never rewritten.
                        content = (
                            content.replace("{{TITLE}}", title)
                            .replace("{{SLUG}}", args.slug)
                            .replace("{{CREATED_AT}}", created_at)
                            .replace("{{DESTINATION}}", args.destination.strip())
                        )
                    write_new(staging_fd, name, content)
                os.fsync(staging_fd)
                os.close(staging_fd)
                staging_fd = None

                # Reserve the exact final name without overwriting any existing
                # effort, then atomically replace our empty reservation.
                try:
                    os.mkdir(args.slug, 0o700, dir_fd=efforts_fd)
                except FileExistsError as exc:
                    raise SystemExit("That Wayfinder effort already exists.") from exc
                final_exists = True
                os.rename(staging, args.slug, src_dir_fd=efforts_fd, dst_dir_fd=efforts_fd)
                staging_exists = False
                os.fsync(efforts_fd)

                relative_map = f".codex/wayfinder/efforts/{args.slug}/MAP.md"
                active_write_attempted = True
                replace_active(wayfinder_fd, relative_map)
            except BaseException as original_error:
                if staging_fd is not None:
                    os.close(staging_fd)
                    staging_fd = None
                rollback_error: BaseException | None = None
                if active_write_attempted:
                    try:
                        restore_active(wayfinder_fd, previous_active)
                    except BaseException as exc:
                        rollback_error = exc
                if staging_exists:
                    remove_new_tree(efforts_fd, staging)
                if final_exists and rollback_error is None:
                    remove_new_tree(efforts_fd, args.slug)
                if rollback_error is not None:
                    raise SystemExit(
                        "Wayfinder initialization failed and ACTIVE rollback could not be durably confirmed; the new effort was preserved to avoid a dangling pointer."
                    ) from original_error
                raise
    except OSError as exc:
        raise SystemExit(f"Secure Wayfinder initialization failed: {exc.strerror or 'filesystem error'}.") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)

    print(relative_map)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
