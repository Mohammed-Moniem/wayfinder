#!/usr/bin/env python3
"""Deterministic, read-only state computation for Wayfinder efforts.

The parser intentionally supports both the V2 Markdown ticket format and the
V3 EFFORT.json index. Markdown remains the canonical place for decision detail;
the manifest supplies typed lifecycle metadata and references.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import unicodedata
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 3
NODE_ID = re.compile(r"^(?:D|G)-\d{3,}$")
STABLE_ID = re.compile(r"^(?:D|G|E|M)-\d{3,}$")
ANY_NODE_ID = re.compile(r"\b(?:D|G|E|M)-\d{3,}\b", re.IGNORECASE)
EVIDENCE_ID = re.compile(r"\bE-\d{3,}\b", re.IGNORECASE)
CONTROL_PATH = re.compile(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")
FIELD_LINE = re.compile(r"^\s*-\s*\*\*([^*]+?):\*\*\s*(.*?)\s*$")
HEADING = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)

DECISION_STATUSES = {
    "OPEN",
    "CLAIMED",
    "BLOCKED",
    "RESOLVED",
    "REOPENED",
    "SUPERSEDED",
}
GATE_STATUSES = {
    "OPEN",  # accepted for V2 compatibility and treated as PENDING
    "CLAIMED",  # accepted for V2 compatibility and treated as RUNNING
    "BLOCKED",
    "RESOLVED",  # accepted for V2 compatibility and treated as PASSED
    "REOPENED",
    "SUPERSEDED",
    "PENDING",
    "READY",
    "RUNNING",
    "DEFINED",
    "EVALUATING",
    "PASSED",
    "FAILED",
    "STALE",
    "WAIVED",
}
TERMINAL_DECISION = {"RESOLVED", "SUPERSEDED"}
TERMINAL_GATE = {"PASSED", "WAIVED", "RESOLVED", "SUPERSEDED"}
OPENISH_DECISION = {"OPEN", "REOPENED"}
OPENISH_GATE = {"OPEN", "REOPENED", "PENDING", "READY", "DEFINED", "STALE"}
CLAIMED_STATUSES = {"CLAIMED", "RUNNING", "EVALUATING"}
BLOCKED_STATUSES = {"BLOCKED", "FAILED"}
GATE_CHECK_STATUSES = {"PENDING", "EVALUATING", "PASSED", "FAILED", "STALE", "WAIVED"}
DECISION_TRANSITIONS = {
    "OPEN": {"CLAIMED", "BLOCKED", "RESOLVED", "SUPERSEDED"},
    "CLAIMED": {"OPEN", "BLOCKED", "RESOLVED", "REOPENED", "SUPERSEDED"},
    "BLOCKED": {"OPEN", "REOPENED", "SUPERSEDED"},
    "RESOLVED": {"REOPENED", "SUPERSEDED"},
    "REOPENED": {"CLAIMED", "BLOCKED", "RESOLVED", "SUPERSEDED"},
    "SUPERSEDED": set(),
}
GATE_TRANSITIONS = {
    "DEFINED": {"PENDING", "SUPERSEDED"},
    "PENDING": {"EVALUATING", "WAIVED", "SUPERSEDED"},
    "EVALUATING": {"PASSED", "FAILED", "PENDING"},
    "PASSED": {"STALE", "SUPERSEDED"},
    "FAILED": {"PENDING", "EVALUATING", "WAIVED", "SUPERSEDED"},
    "STALE": {"PENDING", "EVALUATING", "WAIVED", "SUPERSEDED"},
    "WAIVED": {"STALE", "SUPERSEDED"},
    "SUPERSEDED": set(),
}

PHASES = (
    {
        "id": "p1-frame",
        "label": "Frame destination",
        "description": "Define the observable destination, success conditions, constraints, scope, and authority boundaries.",
        "checkpoint": {
            "id": "cp1-destination",
            "label": "Destination framing review",
            "recommended_run": True,
            "due_when": "The destination and boundaries are first written or materially revised.",
            "reason": "Run Wayfinder after the destination and boundaries are first framed or materially revised.",
        },
    },
    {
        "id": "p2-resolve",
        "label": "Resolve route",
        "description": "Turn fog into precise decisions and settle destination-blocking route choices.",
        "checkpoint": {
            "id": "cp2-route",
            "label": "Decision-route review",
            "recommended_run": True,
            "due_when": "A destination-blocking route decision is actionable or its prerequisite settles.",
            "reason": "Resume Wayfinder while destination-blocking route decisions remain unsettled.",
        },
    },
    {
        "id": "p3-prove",
        "label": "Prove route",
        "description": "Validate material assumptions, establish feasibility, and define delivery gates.",
        "checkpoint": {
            "id": "cp3-proof",
            "label": "Evidence and feasibility review",
            "recommended_run": True,
            "due_when": "Material evidence arrives, a high-impact assumption changes, or recorded evidence becomes stale.",
            "reason": "Run Wayfinder when material evidence arrives, an assumption changes, or route evidence expires.",
        },
    },
    {
        "id": "p4-ready",
        "label": "Ready for execution",
        "description": "Perform the final consistency and exit review and create the completion handoff.",
        "checkpoint": {
            "id": "cp4-handoff",
            "label": "Execution handoff review",
            "recommended_run": True,
            "due_when": "The route appears decision-complete and is about to enter its domain-appropriate execution plan.",
            "reason": "Run Wayfinder immediately before the route is handed to execution.",
        },
    },
    {
        "id": "p5-delivery",
        "label": "Delivery & revalidation",
        "description": "Evaluate gates during delivery and revalidate only after failure, staleness, or material change.",
        "checkpoint": {
            "id": "cp5-revalidate",
            "label": "Delivery revalidation",
            "recommended_run": False,
            "due_when": "A Gate fails or becomes stale, delivery contradicts a route premise, or the destination or route materially changes.",
            "reason": "Do not rerun during routine delivery; revalidate only after a failed or stale Gate or material change.",
        },
    },
)
PHASE_IDS = {phase["id"] for phase in PHASES}
CHECKPOINT_SCHEMA = {
    phase["checkpoint"]["id"]: (phase["id"], phase["checkpoint"]["label"])
    for phase in PHASES
}
MILESTONE_SCHEMA = {
    "M-001": ("p1-frame", "Destination baseline"),
    "M-002": ("p2-resolve", "Decision-complete route"),
    "M-003": ("p3-prove", "Evidence-sufficient route"),
    "M-004": ("p4-ready", "Completion handoff"),
    "M-005": ("p5-delivery", "Delivery milestone"),
}
MILESTONE_CRITERIA = {
    "M-001": "Observable success conditions, constraints, scope, and authority boundaries are explicit.",
    "M-002": "Destination-blocking route Decisions are formulated and resolved.",
    "M-003": "Material assumptions and feasibility claims have fresh evidence, and delivery Gates are defined.",
    "M-004": "The exit contract passes and EXIT.md records the accepted route.",
    "M-005": "Delivery evaluates defined Gates; failures and staleness target linked Decisions for revalidation.",
}
LEGACY_P4_CONTRACT = {
    "label": "Ready for spec",
    "description": "Perform the final consistency and exit review and create the completion handoff.",
    "checkpoint_label": "Pre-spec handoff review",
    "due_when": "The route appears decision-complete and is about to enter specification.",
    "reason": "Run Wayfinder immediately before the route is handed to specification.",
}


class WayfinderError(RuntimeError):
    """A safe, user-facing Wayfinder state error."""


def _intake_public_state(
    effort_dir: Path, project_root: Path, manifest: Mapping[str, Any]
) -> tuple[dict[str, Any], list[Diagnostic]]:
    """Load the optional intake reader without creating a package dependency."""
    name = "_wayfinder_intake_v1"
    module = sys.modules.get(name)
    if module is None:
        path = Path(__file__).resolve(strict=True).with_name("wayfinder_intake.py")
        if not path.is_file() or path.is_symlink():
            return (
                {"state": "UNAVAILABLE", "status": "UNAVAILABLE", "revision": 0},
                [Diagnostic("error", "INTAKE_MODULE_UNAVAILABLE", "The local intake state reader is unavailable.")],
            )
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            return (
                {"state": "UNAVAILABLE", "status": "UNAVAILABLE", "revision": 0},
                [Diagnostic("error", "INTAKE_MODULE_UNAVAILABLE", "The local intake state reader is unavailable.")],
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            return (
                {"state": "UNAVAILABLE", "status": "UNAVAILABLE", "revision": 0},
                [Diagnostic("error", "INTAKE_MODULE_UNAVAILABLE", "The local intake state reader is unavailable.")],
            )
    try:
        payload, raw_diagnostics = module.public_intake_state(effort_dir, project_root, manifest)
    except BaseException:
        return (
            {"state": "INVALID", "status": "INVALID", "revision": 0},
            [Diagnostic("error", "INTAKE_INVALID", "The local intake state is malformed, inconsistent, or unsafe.")],
        )
    diagnostics = [
        Diagnostic(
            item.get("severity") if item.get("severity") in {"error", "warning"} else "error",
            _public_scalar(item.get("code"), "INTAKE_INVALID", 80),
            _public_scalar(item.get("message"), "The local intake state is invalid.", 500),
            path=_public_scalar(item.get("path"), "[intake]", 200),
        )
        for item in raw_diagnostics[:MAX_DIAGNOSTICS]
        if isinstance(item, Mapping)
    ]
    return payload, diagnostics


class _ManifestJSONIntegrityError(ValueError):
    pass


MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_BUILD_READ_BYTES = 64 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 10_000
MAX_ACTIVITY_ENTRIES = 1_000
MAX_MANIFEST_NODES = 5_000
MAX_MANIFEST_EVIDENCE = 5_000
MAX_MANIFEST_EDGES = 10_000
MAX_MANIFEST_ENTRIES = 20_000
MAX_DIAGNOSTICS = 1_000
MAX_NODE_RELATIONSHIPS = 2_048
MAX_TOTAL_RELATIONSHIPS = 10_000
MAX_PUBLIC_EDGES = 10_000
EDGE_TYPES = {"requires", "revalidates", "informs", "gates"}
ACTIVITY_FIELDS = {"id", "type", "timestamp", "node_id", "message", "actor"}
NODE_RELATIONSHIP_FIELDS = ("requires", "revalidates", "informs", "gates", "dependents", "unlocks", "evidence")


class _BuildReadBudget:
    def __init__(self, total: int) -> None:
        self.remaining = total


_BUILD_READ_BUDGET: ContextVar[_BuildReadBudget | None] = ContextVar(
    "wayfinder_build_read_budget", default=None
)


def _read_regular_bytes(path: Path, label: str, max_bytes: int = MAX_ARTIFACT_BYTES) -> bytes:
    """Read a bounded regular file without following its final path component."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise WayfinderError(f"{label} is unavailable or unsafe.") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise WayfinderError(f"{label} must be a regular file.")
        if metadata.st_size > max_bytes:
            raise WayfinderError(f"{label} exceeds the {max_bytes}-byte safety limit.")
        budget = _BUILD_READ_BUDGET.get()
        if budget is not None and metadata.st_size > budget.remaining:
            raise WayfinderError("Wayfinder artifact reads exceed the per-build safety budget.")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, max_bytes + 1 - total))
            if not chunk:
                break
            if budget is not None:
                if len(chunk) > budget.remaining:
                    raise WayfinderError("Wayfinder artifact reads exceed the per-build safety budget.")
                budget.remaining -= len(chunk)
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise WayfinderError(f"{label} exceeds the {max_bytes}-byte safety limit.")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _read_regular_text(path: Path, label: str, max_bytes: int = MAX_ARTIFACT_BYTES) -> str:
    try:
        return _read_regular_bytes(path, label, max_bytes).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WayfinderError(f"{label} is not valid UTF-8.") from exc


def _bounded_directory_entries(path: Path) -> tuple[list[Path], bool]:
    """Enumerate at most the safety limit without following directory children."""
    entries: list[Path] = []
    try:
        with os.scandir(path) as iterator:
            for entry in iterator:
                if len(entries) == MAX_DIRECTORY_ENTRIES:
                    return [], True
                entries.append(path / entry.name)
    except OSError as exc:
        raise WayfinderError(f"Cannot enumerate {path.name}/ safely.") from exc
    return sorted(entries, key=lambda item: item.name), False


def _activity_scalar(value: Any, limit: int) -> tuple[str, bool]:
    """Return compact single-line activity text and whether input was omitted."""
    if value is None:
        return "", False
    if not isinstance(value, (str, int, float, bool)):
        return "", True
    cleaned = "".join(
        " " if unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"} else character
        for character in str(value)
    )
    return " ".join(cleaned.split())[:limit], False


def _public_scalar(value: Any, fallback: str = "", limit: int = 1_000) -> str:
    """Render only JSON-like scalars; mappings/sequences never become repr text."""
    if value is None or not isinstance(value, (str, int, float, bool)):
        return fallback
    cleaned = "".join(
        f"\\u{ord(character):04x}"
        if unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        else character
        for character in str(value)
    )
    compact = " ".join(cleaned.split())[:limit]
    return compact or fallback


def _contains_unsafe_text(value: str) -> bool:
    return bool(CONTROL_PATH.search(value)) or any(
        unicodedata.category(character) in {"Zl", "Zp", "Cs"} for character in value
    )


def _activity_summaries(manifest: Mapping[str, Any]) -> tuple[list[dict[str, str]], list[Diagnostic]]:
    """Project untrusted activity into the exact bounded dashboard contract."""
    raw = manifest.get("activity", [])
    if not isinstance(raw, list):
        return [], [Diagnostic("warning", "ACTIVITY_INVALID", "EFFORT.json activity must be a list of bounded summaries.")]

    diagnostics: list[Diagnostic] = []
    if len(raw) > MAX_ACTIVITY_ENTRIES:
        diagnostics.append(
            Diagnostic(
                "warning",
                "ACTIVITY_LIMIT",
                f"Activity exceeds the {MAX_ACTIVITY_ENTRIES}-entry dashboard limit; later entries were omitted.",
            )
        )
    invalid_or_omitted = False
    result: list[dict[str, str]] = []
    for entry in raw[:MAX_ACTIVITY_ENTRIES]:
        if not isinstance(entry, Mapping):
            invalid_or_omitted = True
            continue
        if any(str(key) not in ACTIVITY_FIELDS for key in entry):
            invalid_or_omitted = True
        event_id, invalid_id = _activity_scalar(entry.get("id"), 64)
        event_type, invalid_type_value = _activity_scalar(entry.get("type"), 32)
        timestamp, invalid_timestamp = _activity_scalar(entry.get("timestamp"), 64)
        node_id, invalid_node_value = _activity_scalar(entry.get("node_id"), 32)
        message, invalid_message = _activity_scalar(entry.get("message"), 500)
        actor, invalid_actor = _activity_scalar(entry.get("actor"), 120)
        invalid_or_omitted = invalid_or_omitted or any(
            (invalid_id, invalid_type_value, invalid_timestamp, invalid_node_value, invalid_message, invalid_actor)
        )
        event_type = event_type.lower()
        if event_type not in {"update", "invalidation"}:
            invalid_or_omitted = True
            continue
        canonical_node_id = node_id.upper()
        if canonical_node_id and not NODE_ID.fullmatch(canonical_node_id):
            canonical_node_id = ""
            invalid_or_omitted = True
        result.append(
            {
                "id": event_id,
                "type": event_type,
                "timestamp": timestamp,
                "node_id": canonical_node_id,
                "message": message,
                "actor": actor,
            }
        )
    if invalid_or_omitted:
        diagnostics.append(
            Diagnostic(
                "warning",
                "ACTIVITY_FIELDS_OMITTED",
                "Unsafe, unknown, nested, or noncanonical activity fields were omitted from dashboard state.",
            )
        )
    return result, diagnostics


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    code: str
    message: str
    node_id: str | None = None
    path: str | None = None

    def payload(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "severity": self.severity,
            "code": self.code,
            "message": _public_scalar(self.message, "Wayfinder diagnostic.", 2_000),
        }
        if self.node_id and NODE_ID.fullmatch(self.node_id):
            result["node_id"] = self.node_id
        if self.path:
            result["path"] = _public_scalar(self.path, "[unavailable]", 500)
        return result


@dataclass
class Node:
    id: str
    kind: str
    title: str
    question: str
    status: str
    path: str
    autonomy: str = "HYBRID"
    responsible_party: str = "Codex and user"
    next_actor: str = "Codex"
    decision_authority: str = "User"
    waiver_authority: str = "User"
    phase: str = "p2-resolve"
    destination_blocking: bool = True
    post_build: bool = False
    requires: list[str] = field(default_factory=list)
    revalidates: list[str] = field(default_factory=list)
    informs: list[str] = field(default_factory=list)
    gates: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    resolution: str = ""
    delivery_condition: str = ""
    summary: str = ""
    recommendation: str = ""
    consequence_of_waiting: str = ""
    unlocks: list[str] = field(default_factory=list)
    raw_fields: dict[str, str] = field(default_factory=dict, repr=False)
    transitions: list[dict[str, str | None]] = field(default_factory=list, repr=False)
    dependent_inspections: list[dict[str, str]] = field(default_factory=list, repr=False)
    checks: list[dict[str, str]] = field(default_factory=list, repr=False)
    evaluation_receipts: list[dict[str, str]] = field(default_factory=list, repr=False)
    waiver_receipts: list[dict[str, str]] = field(default_factory=list, repr=False)
    waiting_reason: str = ""

    @property
    def terminal(self) -> bool:
        allowed = TERMINAL_GATE if self.kind == "gate" else TERMINAL_DECISION
        return self.status in allowed

    def payload(self) -> dict[str, Any]:
        legal_statuses = GATE_STATUSES if self.kind == "gate" else DECISION_STATUSES
        public_status = self.status if self.status in legal_statuses else "INVALID"
        public_autonomy = self.autonomy if self.autonomy in {"AFK", "HITL", "HYBRID"} else "INVALID"
        public_phase = self.phase if self.phase in PHASE_IDS else "unknown"
        return {
            "id": self.id,
            "kind": self.kind if self.kind in {"decision", "gate"} else "decision",
            "title": _public_scalar(self.title, self.id, 300),
            "question": _public_scalar(self.question, self.id, 1_000),
            "status": public_status,
            "autonomy": public_autonomy,
            "responsible_party": _public_scalar(self.responsible_party, "Unassigned", 200),
            "next_actor": _public_scalar(self.next_actor, "Unassigned", 200),
            "decision_authority": _public_scalar(self.decision_authority, "Unassigned", 200),
            "waiver_authority": _public_scalar(self.waiver_authority, "Unassigned", 200),
            "phase": public_phase,
            "destination_blocking": self.destination_blocking,
            "post_build": self.post_build,
            "requires": list(self.requires),
            "revalidates": list(self.revalidates),
            "informs": list(self.informs),
            "gates": list(self.gates),
            "summary": _public_scalar(self.summary, "", 1_000),
            "recommendation": _public_scalar(self.recommendation, "", 1_000),
            "consequence_of_waiting": _public_scalar(self.consequence_of_waiting, "", 1_000),
            "unlocks": list(self.unlocks),
            "evidence": list(self.evidence),
            "revision": _revision_number(self.raw_fields.get("revision", "")),
            "resolution": _public_scalar(self.resolution, "", 2_000),
            "path": _public_scalar(self.path, "[unavailable]", 500),
            "waiting_reason": _public_scalar(self.waiting_reason, "", 1_000),
        }


def git_root(start: Path) -> Path:
    """Return a Git root, or the explicitly selected real project directory."""
    if start.is_symlink():
        raise WayfinderError("Project root must be a real directory, not a symbolic link.")
    try:
        resolved = start.resolve(strict=True)
    except OSError as exc:
        raise WayfinderError("Project root is unavailable or unsafe.") from exc
    if not resolved.is_dir():
        raise WayfinderError("Project root must be an existing directory.")
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
        return resolved
    return Path(result.stdout.strip()).resolve(strict=True)


def _safe_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=True)).as_posix()
    except ValueError:
        return path.name


def _within(parent: Path, child: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def _wayfinder_roots(project_root: Path) -> tuple[Path, Path]:
    """Return project-local metadata roots only when no directory is a symlink."""
    codex = project_root / ".codex"
    base = codex / "wayfinder"
    efforts = base / "efforts"
    for path, label in ((codex, ".codex"), (base, ".codex/wayfinder"), (efforts, ".codex/wayfinder/efforts")):
        if path.is_symlink() or not path.is_dir() or not _within(project_root, path):
            raise WayfinderError(f"{label} must be a real directory inside the project, not a symbolic link or escaped path.")
    return base, efforts


def _safe_effort_map(project_root: Path, efforts_root: Path, effort_dir: Path) -> Path:
    if (
        effort_dir.is_symlink()
        or not effort_dir.is_dir()
        or not _within(efforts_root, effort_dir)
        or effort_dir.parent.resolve(strict=True) != efforts_root.resolve(strict=True)
    ):
        raise WayfinderError("The selected effort must be a real directory inside .codex/wayfinder/efforts/.")
    map_path = effort_dir / "MAP.md"
    if (
        map_path.is_symlink()
        or not map_path.is_file()
        or not _within(effort_dir, map_path)
        or not _within(project_root, map_path)
    ):
        raise WayfinderError("The selected effort MAP.md must be a real file inside its project-local effort directory.")
    return map_path


def resolve_effort(root: Path, effort: str | Path | None = None) -> tuple[Path, Path]:
    """Resolve an effort from a safe explicit selector or ACTIVE pointer."""
    project_root = git_root(root)
    base, efforts_root = _wayfinder_roots(project_root)
    if effort is not None:
        selector = Path(effort)
        if selector.is_absolute():
            candidate = selector
        elif len(selector.parts) == 1:
            candidate = base / "efforts" / selector
        else:
            candidate = project_root / selector
        if candidate.name == "MAP.md":
            if candidate.is_symlink():
                raise WayfinderError("The selected effort MAP.md cannot be a symbolic link.")
            candidate = candidate.parent
        if candidate.is_symlink():
            raise WayfinderError("The selected effort directory cannot be a symbolic link.")
        candidate = candidate.resolve(strict=True)
        _safe_effort_map(project_root, efforts_root, candidate)
        return project_root, candidate

    active = base / "ACTIVE"
    try:
        if active.is_symlink():
            raise WayfinderError("ACTIVE must be a regular file, not a symbolic link.")
        raw = _read_regular_text(active, "ACTIVE", 4096)
    except FileNotFoundError as exc:
        raise WayfinderError("No active Wayfinder effort. Run `wayfinder init` first.") from exc
    if _contains_unsafe_text(raw.rstrip("\n")):
        raise WayfinderError("ACTIVE contains unsafe control, separator, surrogate, or bidirectional text.")
    lines = raw.splitlines()
    if len(lines) != 1 or not lines[0].strip() or len(raw) > 4096:
        raise WayfinderError("ACTIVE must contain exactly one short project-relative MAP.md path.")
    pointer = Path(lines[0].strip())
    if pointer.is_absolute() or pointer.name != "MAP.md" or ".." in pointer.parts:
        raise WayfinderError("ACTIVE contains an unsafe or invalid map path.")
    raw_map_path = project_root / pointer
    if raw_map_path.is_symlink() or raw_map_path.parent.is_symlink():
        raise WayfinderError("ACTIVE cannot select a symbolic-link effort or MAP.md.")
    map_path = raw_map_path.resolve(strict=True)
    _safe_effort_map(project_root, efforts_root, map_path.parent)
    return project_root, map_path.parent


def _field_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _parse_fields(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    section = re.search(r"^##\s+", text, flags=re.MULTILINE)
    metadata = text[: section.start()] if section else text
    for line in metadata.splitlines():
        match = FIELD_LINE.match(line)
        if match:
            key = _field_key(match.group(1))
            if key in result:
                raise WayfinderError("Markdown metadata contains duplicate protected fields.")
            result[key] = match.group(2).strip()
    return result


def _extract_ids(value: Any, *, evidence: bool = False) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        combined: list[str] = []
        for item in value:
            combined.extend(_extract_ids(item, evidence=evidence))
        return sorted(set(combined))
    if isinstance(value, Mapping):
        candidate = value.get("id") or value.get("to") or value.get("target")
        return _extract_ids(candidate, evidence=evidence)
    pattern = EVIDENCE_ID if evidence else ANY_NODE_ID
    return sorted({match.upper() for match in pattern.findall(str(value))})


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if not isinstance(value, (str, int, float)):
        return default
    normalized = _public_scalar(value, "", 40).lower()
    if normalized in {"true", "yes", "1", "required", "blocking"}:
        return True
    if normalized in {"false", "no", "0", "optional", "non-blocking", "nonblocking"}:
        return False
    return default


def _canonical_index_path(kind: str, artifact_id: str, value: Any) -> str | None:
    id_patterns = {
        "decision": r"D-\d{3,}",
        "gate": r"G-\d{3,}",
        "evidence": r"E-\d{3,}",
    }
    if kind not in id_patterns or not re.fullmatch(id_patterns[kind], artifact_id):
        return None
    expected = f"{kind}s/{artifact_id}.md" if kind in {"decision", "gate"} else f"evidence/{artifact_id}.md"
    raw = str(value or expected)
    if _contains_unsafe_text(raw) or "\\" in raw or raw != expected:
        return None
    return raw


def _heading(text: str, fallback: str) -> str:
    match = HEADING.search(text)
    if not match:
        return fallback
    title = match.group(1).strip()
    title = re.sub(r"^(?:D|G)-\d{3,}\s*[—:-]\s*", "", title, flags=re.IGNORECASE)
    return title.strip() or fallback


def _is_legacy_gate(node_id: str, title: str, question: str, fields: Mapping[str, str]) -> bool:
    explicit = fields.get("kind") or fields.get("artifact_kind")
    if explicit:
        return explicit.strip().upper() in {"GATE", "CHECK", "CHECKPOINT"}
    material = f"{title} {question}".lower()
    patterns = (
        "final public release approval",
        "complete release evidence",
        "production topology and scalability gate",
        "exact release candidate pass",
        "approve public access to the exact validated",
    )
    return any(pattern in material for pattern in patterns)


def _parties(autonomy: str, fields: Mapping[str, str]) -> tuple[str, str]:
    responsible = fields.get("responsible_party", "").strip()
    next_actor = fields.get("next_actor", "").strip()
    if responsible and next_actor:
        return responsible, next_actor
    defaults = {
        "AFK": ("Codex", "Codex"),
        "HITL": ("User", "User"),
        "HYBRID": ("Codex and user", "Codex"),
    }
    fallback = defaults.get(autonomy, ("Unassigned", "Unassigned"))
    return responsible or fallback[0], next_actor or fallback[1]


def _transition_rows(text: str) -> list[dict[str, str | None]]:
    match = re.search(
        r"^##\s+Append-only transition history\s*$\n(.*?)(?=^##\s+|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return []
    result: list[dict[str, str | None]] = []
    for line in match.group(1).splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 6 or cells[0].lower() == "from" or set(cells[0]) <= {"-", ":"}:
            continue
        before = cells[0].strip().upper()
        after = cells[1].strip().upper()
        if not after or "{{" in after:
            continue
        normalized_before = None if before in {"", "—", "-", "NONE", "N/A"} else before
        result.append(
            {
                "from": normalized_before,
                "to": after,
                "actor": cells[2],
                "timestamp": cells[3],
                "reason": cells[4],
                "evidence": cells[5],
            }
        )
    return result


def parse_ticket(path: Path, project_root: Path, kind_hint: str | None = None) -> Node:
    text = _read_regular_text(path, "Wayfinder ticket")
    fields = _parse_fields(text)
    filename_id = path.stem.upper()
    explicit_id = (fields.get("id") or filename_id).strip().upper()
    title = _heading(text, explicit_id)
    question = fields.get("question", title).strip()
    declared_kind = fields.get("kind", "").strip().lower()
    kind = declared_kind or (kind_hint or "").strip().lower()
    if kind not in {"decision", "gate"}:
        kind = "gate" if _is_legacy_gate(explicit_id, title, question, fields) else "decision"
    status = fields.get("status", "OPEN").strip().upper().replace(" ", "-")
    autonomy = (fields.get("autonomy") or fields.get("owner") or "HYBRID").strip().upper()
    responsible, next_actor = _parties(autonomy, fields)
    decision_authority = fields.get("decision_authority", "").strip()
    if not decision_authority:
        decision_authority = "Codex" if autonomy == "AFK" else "User"
    waiver_authority = fields.get("waiver_authority", "").strip() or "User"
    post_build = _bool(fields.get("post_build"), kind == "gate")
    phase_default = "p5-delivery" if post_build else "p2-resolve"
    phase = (fields.get("phase_id") or fields.get("phase") or phase_default).strip()
    requires_value = fields.get("requires") or fields.get("prerequisites")
    recommendation = fields.get("recommendation", "").strip()
    summary = fields.get("summary", "").strip()
    if not summary:
        summary = fields.get("current_hypothesis", "").strip() or question
    consequence = (
        fields.get("consequence_of_waiting", "").strip()
        or fields.get("blocks_affects", "").strip()
    )
    evidence = _extract_ids(fields.get("evidence"), evidence=True)
    resolution = fields.get("resolution", "").strip() or _substantive_section(text, "Resolution")
    delivery_condition = _substantive_section(text, "Delivery condition")
    return Node(
        id=explicit_id,
        kind=kind,
        title=title,
        question=question,
        status=status,
        path=_safe_relative(project_root, path),
        autonomy=autonomy,
        responsible_party=responsible,
        next_actor=next_actor,
        decision_authority=decision_authority,
        waiver_authority=waiver_authority,
        phase=phase,
        destination_blocking=_bool(fields.get("destination_blocking"), True),
        post_build=post_build,
        requires=_extract_ids(requires_value),
        revalidates=_extract_ids(fields.get("revalidates")),
        informs=_extract_ids(fields.get("informs")),
        gates=_extract_ids(fields.get("gates")),
        dependents=_extract_ids(fields.get("dependents")),
        evidence=evidence,
        resolution=resolution,
        delivery_condition=delivery_condition,
        summary=summary,
        recommendation=recommendation,
        consequence_of_waiting=consequence,
        unlocks=_extract_ids(fields.get("unlocks")),
        raw_fields=fields,
        transitions=_transition_rows(text),
        dependent_inspections=_dependent_inspection_rows(text),
        checks=_gate_check_rows(text),
        evaluation_receipts=_evaluation_receipt_rows(text),
        waiver_receipts=_waiver_receipt_rows(text),
    )


def _section(text: str, name: str) -> str:
    match = re.search(
        rf"^##\s+{re.escape(name)}\s*$\n(.*?)(?=^##\s+|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


def _substantive_section(text: str, name: str) -> str:
    value = _section(text, name).strip()
    if not value or "{{" in value:
        return ""
    lowered = value.lower()
    if lowered.startswith("leave empty until") or lowered.startswith("store a concise result"):
        return ""
    return value


def _table_cells(text: str, section_name: str) -> list[list[str]]:
    section = _section(text, section_name)
    result: list[list[str]] = []
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or set(cells[0]) <= {"-", ":"}:
            continue
        result.append(cells)
    return result[1:] if result else []


def _dependent_inspection_rows(text: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for cells in _table_cells(text, "Dependent inspections"):
        if len(cells) < 6 or not ANY_NODE_ID.fullmatch(cells[1].upper()):
            continue
        result.append(
            {
                "trigger": cells[0],
                "dependent": cells[1].upper(),
                "outcome": cells[2].upper(),
                "evidence": cells[3],
                "actor": cells[4],
                "timestamp": cells[5],
            }
        )
    return result


def _gate_check_rows(text: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for cells in _table_cells(text, "Checks"):
        if len(cells) < 5:
            continue
        result.append(
            {
                "id": cells[0].upper(),
                "method": cells[1].upper().strip("`"),
                "expected": cells[2],
                "evidence_required": cells[3],
                "status": cells[4].upper(),
            }
        )
    return result


def _evaluation_receipt_rows(text: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for cells in _table_cells(text, "Evaluation receipt"):
        if len(cells) < 6:
            continue
        result.append(
            {
                "actor": cells[0],
                "timestamp": cells[1],
                "outcome": cells[2].upper(),
                "evidence": cells[3],
                "subject_revision": cells[4],
                "rationale": cells[5],
            }
        )
    return result


def _waiver_receipt_rows(text: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for cells in _table_cells(text, "Waiver receipt"):
        if len(cells) < 6:
            continue
        result.append(
            {
                "actor": cells[0],
                "authority": cells[1],
                "timestamp": cells[2],
                "scope": cells[3],
                "expiry": cells[4],
                "rationale": cells[5],
            }
        )
    return result


def _destination(map_text: str) -> str:
    value = _section(map_text, "Destination")
    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", value):
        compact = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if compact:
            paragraphs.append(compact)
    return "\n\n".join(paragraphs)


def _map_title(map_text: str, slug: str) -> str:
    match = HEADING.search(map_text)
    if not match:
        return slug.replace("-", " ").title()
    title = match.group(1).strip()
    return re.sub(r"^Wayfinder\s+Map:\s*", "", title, flags=re.IGNORECASE).strip()


def _frontier_ids(map_text: str) -> list[str]:
    frontier = _section(map_text, "Frontier")
    result: list[str] = []
    for line in frontier.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and NODE_ID.fullmatch(cells[0].upper()):
            result.append(cells[0].upper())
    return sorted(set(result))


def _unformulated_fog(map_text: str) -> list[str]:
    fog = ""
    for heading in ("Fog / Not Yet Specified", "Fog / not yet formulated", "Fog"):
        fog = _section(map_text, heading)
        if fog:
            break
    result: list[str] = []
    for line in fog.splitlines():
        stripped = line.strip()
        if stripped.startswith("-") and not ANY_NODE_ID.search(stripped):
            value = stripped.lstrip("- ").strip()
            if value.rstrip(".").strip().upper() not in {"NONE", "NO UNFORMULATED FOG REMAINS"}:
                result.append(value)
    return result


def _map_framing_audit(
    map_text: str,
    manifest: Mapping[str, Any],
    project_root: Path,
    map_path: Path,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    relative = _safe_relative(project_root, map_path)
    effort = manifest.get("effort", {}) if isinstance(manifest.get("effort"), Mapping) else {}
    destination = _destination(map_text)
    if _ambiguous_required(destination):
        diagnostics.append(Diagnostic("error", "MAP_DESTINATION_REQUIRED", "MAP.md needs a concrete Destination.", path=relative))
    elif _normalized_prose(destination) != _normalized_prose(_public_scalar(effort.get("destination"), "", 4_000)):
        diagnostics.append(Diagnostic("error", "MAP_DESTINATION_CONFLICT", "MAP.md Destination must exactly match EFFORT.json.", path=relative))

    success_rows = _table_cells(map_text, "Success conditions")
    valid_success = bool(success_rows)
    seen_success: set[str] = set()
    for cells in success_rows:
        if len(cells) < 4 or not re.fullmatch(r"SC-\d{3,}", cells[0], flags=re.IGNORECASE):
            valid_success = False
            continue
        success_id = cells[0].upper()
        if success_id in seen_success:
            valid_success = False
        seen_success.add(success_id)
        if _ambiguous_required(cells[1]) or _ambiguous_required(cells[2]) or str(cells[3]).strip().upper() not in {"OPEN", "ROUTED", "VALIDATED", "SATISFIED", "SUPERSEDED"}:
            valid_success = False
    if not valid_success:
        diagnostics.append(Diagnostic("error", "MAP_SUCCESS_CONDITIONS_INVALID", "MAP.md needs unique SC-NNN rows with concrete observable conditions, evidence requirements, and legal statuses.", path=relative))

    constraints = _section(map_text, "Constraints and authority boundaries") or _section(map_text, "Constraints")
    if _ambiguous_required(constraints) or "{{" in constraints or not any(line.strip().startswith("-") for line in constraints.splitlines()):
        diagnostics.append(Diagnostic("error", "MAP_CONSTRAINTS_REQUIRED", "MAP.md needs concrete constraints and authority boundaries.", path=relative))
    out_of_scope = _section(map_text, "Explicit out of scope")
    if _ambiguous_required(out_of_scope) or "{{" in out_of_scope or not any(line.strip().startswith("-") for line in out_of_scope.splitlines()):
        diagnostics.append(Diagnostic("error", "MAP_SCOPE_BOUNDARY_REQUIRED", "MAP.md needs a concrete Explicit out of scope boundary.", path=relative))
    return diagnostics


def _load_json(path: Path) -> tuple[dict[str, Any], str | None]:
    def object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _ManifestJSONIntegrityError("duplicate object key")
            result[key] = value
        return result

    def finite_numbers_only(_value: str) -> Any:
        raise _ManifestJSONIntegrityError("non-finite number")

    try:
        value = json.loads(
            _read_regular_text(path, "EFFORT.json"),
            object_pairs_hook=object_without_duplicates,
            parse_constant=finite_numbers_only,
        )
    except FileNotFoundError:
        return {}, None
    except RecursionError:
        return {}, "JSON nesting exceeds the safety limit"
    except _ManifestJSONIntegrityError:
        return {}, "JSON violates duplicate-key or finite-number integrity rules"
    except ValueError:
        return {}, "JSON syntax or numeric value is invalid"
    except (OSError, UnicodeError, json.JSONDecodeError, WayfinderError):
        return {}, "manifest cannot be read safely"
    if not isinstance(value, dict):
        return {}, "top-level JSON value must be an object"
    return value, None


def _manifest_collection_size(value: Any) -> int:
    if isinstance(value, Mapping):
        return len(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return len(value)
    return 0


def _bounded_manifest_inputs(
    manifest: Mapping[str, Any],
) -> tuple[dict[str, Any], list[Diagnostic], set[str]]:
    """Bound every iterable manifest surface before parsing artifact files."""
    safe = dict(manifest)
    diagnostics: list[Diagnostic] = []
    blocked: set[str] = set()
    fields = ("phases", "checkpoints", "milestones", "decisions", "gates", "evidence", "edges", "activity")
    counts = {field_name: _manifest_collection_size(manifest.get(field_name)) for field_name in fields}
    if sum(counts.values()) > MAX_MANIFEST_ENTRIES:
        for field_name in fields:
            safe[field_name] = []
        blocked.update({"nodes", "evidence", "edges", "activity", "lifecycle"})
        diagnostics.append(
            Diagnostic(
                "error",
                "MANIFEST_ENTRY_LIMIT",
                f"EFFORT.json exceeds the {MAX_MANIFEST_ENTRIES}-entry aggregate safety limit; indexed collections were not processed.",
                path="[oversized manifest collections]",
            )
        )
        return safe, diagnostics, blocked

    if counts["phases"] > len(PHASES) or counts["checkpoints"] > len(CHECKPOINT_SCHEMA) or counts["milestones"] > len(MILESTONE_SCHEMA):
        for field_name in ("phases", "checkpoints", "milestones"):
            safe[field_name] = []
        blocked.add("lifecycle")
        diagnostics.append(
            Diagnostic("error", "MANIFEST_LIFECYCLE_LIMIT", "EFFORT.json lifecycle collections exceed the fixed five-entry contract.")
        )
    if counts["decisions"] + counts["gates"] > MAX_MANIFEST_NODES:
        safe["decisions"] = []
        safe["gates"] = []
        blocked.add("nodes")
        diagnostics.append(
            Diagnostic(
                "error",
                "MANIFEST_NODE_LIMIT",
                f"EFFORT.json exceeds the {MAX_MANIFEST_NODES}-node safety limit; indexed tickets were not processed.",
            )
        )
    if counts["evidence"] > MAX_MANIFEST_EVIDENCE:
        safe["evidence"] = []
        blocked.add("evidence")
        diagnostics.append(
            Diagnostic(
                "error",
                "MANIFEST_EVIDENCE_LIMIT",
                f"EFFORT.json exceeds the {MAX_MANIFEST_EVIDENCE}-evidence safety limit; indexed evidence was not processed.",
            )
        )
    if counts["edges"] > MAX_MANIFEST_EDGES:
        safe["edges"] = []
        blocked.add("edges")
        diagnostics.append(
            Diagnostic(
                "error",
                "MANIFEST_EDGE_LIMIT",
                f"EFFORT.json exceeds the {MAX_MANIFEST_EDGES}-edge safety limit; typed edges were not processed.",
            )
        )
    return safe, diagnostics, blocked


def _manifest_node_refs(
    manifest: Mapping[str, Any], diagnostics: list[Diagnostic] | None = None
) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    seen_ids: set[str] = set()
    duplicate_found = False
    for plural, kind in (("decisions", "decision"), ("gates", "gate")):
        raw = manifest.get(plural, [])
        if isinstance(raw, Mapping):
            values: Iterable[Any] = (
                {"id": key, **(value if isinstance(value, Mapping) else {})}
                for key, value in raw.items()
            )
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            values = raw
        else:
            values = []
        for entry in values:
            if isinstance(entry, str):
                item = {"id": entry}
            elif isinstance(entry, Mapping):
                item = dict(entry)
            else:
                continue
            item["kind"] = kind
            node_id = _public_scalar(item.get("id"), "", 64).upper()
            if node_id and node_id in seen_ids:
                duplicate_found = True
                continue
            if node_id:
                seen_ids.add(node_id)
            result.append((kind, item))
    if duplicate_found and diagnostics is not None:
        diagnostics.append(
            Diagnostic(
                "error",
                "MANIFEST_NODE_DUPLICATE",
                "EFFORT.json indexes at least one node ID more than once; only the first entry was inspected.",
            )
        )
    return result


def _merge_manifest_node(node: Node, entry: Mapping[str, Any]) -> None:
    """Fill index-only detail without allowing the index to overwrite Markdown.

    V3 deliberately keeps ticket detail canonical in Markdown.  The manifest is
    still checked for exact agreement wherever it repeats that detail, but a
    conflicting index must never silently change the state derived from the
    ticket while diagnostics are being rendered.
    """
    scalar = {
        "title": "title",
        "question": "question",
        "status": "status",
        "autonomy": "autonomy",
        "responsible_party": "responsible_party",
        "next_actor": "next_actor",
        "decision_authority": "decision_authority",
        "waiver_authority": "waiver_authority",
        "phase_id": "phase",
        "phase": "phase",
        "summary": "summary",
        "recommendation": "recommendation",
        "consequence_of_waiting": "consequence_of_waiting",
    }
    for source, target in scalar.items():
        ticket_key = "phase" if source == "phase_id" else source
        if ticket_key in node.raw_fields or (source == "title" and node.title):
            continue
        value = entry.get(source)
        safe_value = _public_scalar(value, "", 2_000)
        if safe_value:
            setattr(node, target, safe_value)
    if "status" not in node.raw_fields and "status" in entry and isinstance(entry["status"], (str, int, float, bool)):
        safe_status = _public_scalar(entry["status"], "", 40)
        if safe_status:
            node.status = safe_status.upper().replace(" ", "-")
    if "destination_blocking" not in node.raw_fields and "destination_blocking" in entry:
        node.destination_blocking = _bool(entry["destination_blocking"], node.destination_blocking)
    if "post_build" not in node.raw_fields and "post_build" in entry:
        node.post_build = _bool(entry["post_build"], node.post_build)
    for key in ("requires", "revalidates", "informs", "gates", "unlocks"):
        if key not in node.raw_fields and key in entry:
            setattr(node, key, _extract_ids(entry[key]))
    if "evidence" not in node.raw_fields and "evidence" in entry:
        node.evidence = _extract_ids(entry["evidence"], evidence=True)


def _manifest_ticket_conflicts(node: Node, entry: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """Return explicit index/detail disagreements; absence is not disagreement."""
    comparisons: list[tuple[str, Any, Any, str]] = []
    scalar_fields = (
        ("title", "title", node.title, "prose"),
        ("question", "question", node.question, "prose"),
        ("kind", "kind", node.kind, "enum"),
        ("status", "status", node.status, "enum"),
        ("autonomy", "autonomy", node.autonomy, "enum"),
        ("responsible_party", "responsible_party", node.responsible_party, "prose"),
        ("next_actor", "next_actor", node.next_actor, "prose"),
        ("decision_authority", "decision_authority", node.decision_authority, "prose"),
        ("waiver_authority", "waiver_authority", node.waiver_authority, "prose"),
        ("phase", "phase", node.phase, "enum"),
        ("summary", "summary", node.summary, "prose"),
        ("recommendation", "recommendation", node.recommendation, "prose"),
        ("consequence_of_waiting", "consequence_of_waiting", node.consequence_of_waiting, "prose"),
    )
    for field_name, raw_key, ticket_value, mode in scalar_fields:
        manifest_key = "phase_id" if field_name == "phase" and "phase_id" in entry else field_name
        # Heading title is always canonical.  Every other value is overlapping
        # only when the ticket explicitly declares the corresponding field.
        ticket_declares = field_name == "title" or raw_key in node.raw_fields
        if ticket_declares and manifest_key in entry:
            comparisons.append((field_name, ticket_value, entry.get(manifest_key), mode))
    if "destination_blocking" in entry and "destination_blocking" in node.raw_fields:
        comparisons.append(("destination_blocking", node.destination_blocking, entry.get("destination_blocking"), "bool"))
    if "post_build" in entry and "post_build" in node.raw_fields:
        comparisons.append(("post_build", node.post_build, entry.get("post_build"), "bool"))
    relationships = (
        ("requires", node.requires, False),
        ("revalidates", node.revalidates, False),
        ("informs", node.informs, True),
        ("gates", node.gates, False),
        ("dependents", node.dependents, False),
        ("unlocks", node.unlocks, False),
        ("evidence", node.evidence, True),
    )
    for field_name, ticket_values, evidence in relationships:
        if field_name in node.raw_fields and field_name in entry:
            manifest_values = _extract_ids(entry.get(field_name), evidence=evidence)
            if sorted(set(ticket_values)) != manifest_values:
                return_value = (
                    field_name,
                    ", ".join(sorted(set(ticket_values))) or "none",
                    ", ".join(manifest_values) or "none",
                )
                # Relationship disagreement is already normalized and should
                # not be coerced through scalar/string handling below.
                comparisons.append((field_name, return_value[1], return_value[2], "exact"))
    handled = {field_name for field_name, *_ in scalar_fields} | {
        "destination_blocking",
        "post_build",
        "requires",
        "revalidates",
        "informs",
        "gates",
        "dependents",
        "unlocks",
        "evidence",
        "id",
        "path",
        "phase_id",
    }
    # Future manifest summaries are still subject to the no-drift rule: if a
    # scalar key is explicitly repeated in both representations, compare it.
    for key in sorted((set(node.raw_fields) & set(entry)) - handled):
        comparisons.append((key, node.raw_fields[key], entry.get(key), "prose"))
    result: list[tuple[str, str, str]] = []
    for field_name, ticket_value, manifest_value, mode in comparisons:
        manifest_scalar = _public_scalar(manifest_value, "[invalid scalar]", 500)
        ticket_scalar = _public_scalar(ticket_value, "[invalid scalar]", 500)
        if mode == "bool":
            ticket_normalized = ticket_value if isinstance(ticket_value, bool) else None
            manifest_normalized = manifest_value if isinstance(manifest_value, bool) else None
        elif mode == "prose":
            ticket_normalized = _normalized_prose(ticket_scalar)
            manifest_normalized = _normalized_prose(manifest_scalar)
        elif mode == "exact":
            ticket_normalized = ticket_scalar
            manifest_normalized = manifest_scalar
        else:
            ticket_normalized = ticket_scalar.upper()
            manifest_normalized = manifest_scalar.upper()
        if ticket_normalized != manifest_normalized:
            result.append((field_name, ticket_scalar, manifest_scalar))
    return result


def _placeholder_node(entry: Mapping[str, Any], project_root: Path, effort_dir: Path) -> Node:
    node_id = _public_scalar(entry.get("id"), "UNKNOWN", 64).upper()
    kind = _public_scalar(entry.get("kind"), "decision", 32).lower()
    path = _public_scalar(entry.get("path"), f"{kind}s/{node_id}.md", 500)
    resolved = effort_dir / path
    return Node(
        id=node_id,
        kind=kind if kind in {"decision", "gate"} else "decision",
        title=_public_scalar(entry.get("title"), node_id, 300),
        question=_public_scalar(entry.get("question"), _public_scalar(entry.get("title"), node_id, 300), 1_000),
        status=_public_scalar(entry.get("status"), "PENDING" if kind == "gate" else "OPEN", 40).upper(),
        path=_safe_relative(project_root, resolved),
        phase=_public_scalar(entry.get("phase_id"), "p5-delivery" if kind == "gate" else "p2-resolve", 80),
        destination_blocking=_bool(entry.get("destination_blocking"), True),
        post_build=_bool(entry.get("post_build"), kind == "gate"),
        decision_authority=_public_scalar(entry.get("decision_authority"), "User", 200),
        waiver_authority=_public_scalar(entry.get("waiver_authority"), "User", 200),
        requires=_extract_ids(entry.get("requires")),
        revalidates=_extract_ids(entry.get("revalidates")),
        informs=_extract_ids(entry.get("informs")),
        gates=_extract_ids(entry.get("gates")),
        evidence=_extract_ids(entry.get("evidence"), evidence=True),
    )


def _manifest_edges(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    raw = manifest.get("edges", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return result
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        source = _public_scalar(entry.get("from") or entry.get("source"), "", 64).upper()
        target = _public_scalar(entry.get("to") or entry.get("target"), "", 64).upper()
        edge_type = _public_scalar(entry.get("type"), "requires", 32).lower()
        identity = (source, target, edge_type)
        if (
            STABLE_ID.fullmatch(source)
            and STABLE_ID.fullmatch(target)
            and edge_type in EDGE_TYPES
            and identity not in seen
        ):
            seen.add(identity)
            result.append({"source": source, "target": target, "type": edge_type})
    return sorted(result, key=lambda item: (item["source"], item["type"], item["target"]))


def _manifest_edge_input_diagnostics(manifest: Mapping[str, Any]) -> list[Diagnostic]:
    """Diagnose malformed edge input without reflecting untrusted values."""
    diagnostics: list[Diagnostic] = []
    raw = manifest.get("edges", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return diagnostics
    seen: set[tuple[str, str, str]] = set()
    duplicate_found = False
    invalid_entry_found = False
    invalid_id_found = False
    invalid_type_found = False
    for entry in raw:
        if not isinstance(entry, Mapping):
            invalid_entry_found = True
            continue
        source = _public_scalar(entry.get("from") or entry.get("source"), "", 64).upper()
        target = _public_scalar(entry.get("to") or entry.get("target"), "", 64).upper()
        edge_type = _public_scalar(entry.get("type"), "", 32).lower()
        if not STABLE_ID.fullmatch(source) or not STABLE_ID.fullmatch(target):
            invalid_id_found = True
        elif edge_type not in EDGE_TYPES:
            invalid_type_found = True
        else:
            identity = (source, target, edge_type)
            if identity in seen:
                duplicate_found = True
            seen.add(identity)
    if invalid_entry_found:
        diagnostics.append(Diagnostic("error", "EDGE_ENTRY_INVALID", "EFFORT.json contains malformed typed-edge entries."))
    if invalid_id_found:
        diagnostics.append(Diagnostic("error", "EDGE_ID_INVALID", "EFFORT.json contains typed edges with noncanonical endpoint IDs."))
    if invalid_type_found:
        diagnostics.append(Diagnostic("error", "EDGE_TYPE_INVALID", "EFFORT.json contains typed edges with invalid relationship types."))
    if duplicate_found:
        diagnostics.append(Diagnostic("error", "EDGE_DUPLICATE", "EFFORT.json contains duplicate typed edges; duplicates were omitted."))
    return diagnostics


def _all_edges(nodes: Mapping[str, Node], manifest_edges: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    values: set[tuple[str, str, str]] = set()
    for node in nodes.values():
        for edge_type in ("requires", "revalidates", "informs", "gates"):
            for target in getattr(node, edge_type):
                if edge_type == "informs" and target.startswith("E-"):
                    values.add((target, node.id, edge_type))
                else:
                    values.add((node.id, target, edge_type))
    for edge in manifest_edges:
        values.add((edge["source"], edge["target"], edge["type"]))
    return [
        {"source": source, "target": target, "type": edge_type}
        for source, target, edge_type in sorted(values)
    ]


def _ticket_edge_identities(nodes: Mapping[str, Node]) -> set[tuple[str, str, str]]:
    """Return the canonical typed-edge index derived only from Markdown detail."""
    return {
        (edge["source"], edge["type"], edge["target"])
        for edge in _all_edges(nodes, [])
    }


def _bound_node_relationships(nodes: Mapping[str, Node]) -> list[Diagnostic]:
    """Fail closed before graph work or public serialization can amplify refs."""
    diagnostics: list[Diagnostic] = []
    oversized: list[Node] = []
    for node in sorted(nodes.values(), key=lambda item: item.id):
        node_oversized = False
        for field_name in NODE_RELATIONSHIP_FIELDS:
            values = sorted(set(getattr(node, field_name)))
            if len(values) > MAX_NODE_RELATIONSHIPS:
                values = []
                node_oversized = True
            setattr(node, field_name, values)
        if node_oversized:
            oversized.append(node)
    for node in oversized:
        diagnostics.append(
            Diagnostic(
                "error",
                "NODE_RELATIONSHIP_LIMIT",
                f"{node.id} exceeds the per-node relationship/reference safety limit; its relationships were omitted.",
                node_id=node.id,
                path=node.path,
            )
        )

    total = sum(
        len(getattr(node, field_name))
        for node in nodes.values()
        for field_name in NODE_RELATIONSHIP_FIELDS
    )
    if total > MAX_TOTAL_RELATIONSHIPS:
        for node in nodes.values():
            for field_name in NODE_RELATIONSHIP_FIELDS:
                setattr(node, field_name, [])
        diagnostics.append(
            Diagnostic(
                "error",
                "RELATIONSHIP_BUDGET_EXCEEDED",
                "Wayfinder relationships exceed the aggregate public-state safety budget; all relationship arrays were omitted.",
            )
        )
    return diagnostics


def _requires_cycles(nodes: Mapping[str, Node]) -> list[list[str]]:
    graph = {node_id: [value for value in node.requires if value in nodes] for node_id, node in nodes.items()}
    color: dict[str, int] = {node_id: 0 for node_id in nodes}
    cycles: set[tuple[str, ...]] = set()

    def canonical(cycle: list[str]) -> tuple[str, ...]:
        body = cycle[:-1]
        if not body:
            return tuple(cycle)
        # A DFS back-edge cycle contains each node once. Rotating at the
        # lexicographically smallest stable ID gives the same canonical form
        # without allocating every possible rotation.
        index = min(range(len(body)), key=body.__getitem__)
        chosen = tuple(body[index:] + body[:index])
        return chosen + (chosen[0],)

    # Iterative DFS avoids Python's recursion ceiling for large but valid maps.
    for start in sorted(nodes):
        if color[start] != 0:
            continue
        color[start] = 1
        path: list[str] = [start]
        positions: dict[str, int] = {start: 0}
        frames: list[tuple[str, Any]] = [(start, iter(sorted(graph[start])))]
        while frames:
            node_id, targets = frames[-1]
            try:
                target = next(targets)
            except StopIteration:
                frames.pop()
                positions.pop(node_id, None)
                path.pop()
                color[node_id] = 2
                continue
            if color[target] == 0:
                color[target] = 1
                positions[target] = len(path)
                path.append(target)
                frames.append((target, iter(sorted(graph[target]))))
            elif color[target] == 1:
                index = positions[target]
                cycles.add(canonical(path[index:] + [target]))
    return [list(cycle) for cycle in sorted(cycles)]


def _high_impact_open_assumptions(effort_dir: Path) -> list[str]:
    path = effort_dir / "ASSUMPTIONS.md"
    try:
        text = _read_regular_text(path, "ASSUMPTIONS.md")
    except (FileNotFoundError, OSError, UnicodeError, WayfinderError):
        return []
    result: list[str] = []
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 6 or not re.fullmatch(r"A-\d{3,}", cells[0], flags=re.IGNORECASE):
            continue
        impact = cells[2].upper()
        status = cells[5].upper()
        if impact in {"HIGH", "CRITICAL"} and status == "OPEN":
            result.append(cells[0].upper())
    return sorted(result)


def _assumption_audit(
    effort_dir: Path,
    project_root: Path,
    manifest_present: bool,
    nodes: Mapping[str, Node],
) -> tuple[list[str], list[Diagnostic], list[dict[str, Any]], set[str], list[str], list[dict[str, Any]]]:
    path = effort_dir / "ASSUMPTIONS.md"
    if path.is_symlink() or not _within(effort_dir, path) or not _within(project_root, path):
        return [], [Diagnostic("error", "ASSUMPTIONS_PATH_ESCAPE", "ASSUMPTIONS.md must be a real file inside the effort.", path=_safe_relative(project_root, path))], [], set(), [], []
    try:
        text = _read_regular_text(path, "ASSUMPTIONS.md")
    except FileNotFoundError:
        if manifest_present:
            return [], [Diagnostic("error", "ASSUMPTIONS_MISSING", "V3 effort is missing ASSUMPTIONS.md.", path=_safe_relative(project_root, path))], [], set(), [], []
        return [], [], [], set(), [], []
    except (OSError, UnicodeError, WayfinderError):
        return [], [Diagnostic("error", "ASSUMPTIONS_INVALID", "ASSUMPTIONS.md cannot be read safely.", path=_safe_relative(project_root, path))], [], set(), [], []

    assumptions: dict[str, dict[str, Any]] = {}
    accepted_risk_receipts: dict[str, list[list[str]]] = {}
    refutation_receipts: dict[tuple[str, str], list[list[str]]] = {}
    diagnostics: list[Diagnostic] = []
    relative = _safe_relative(project_root, path)
    # The canonical assumption ledger is the top-level table.  Receipt tables
    # deliberately reuse A-NNN values and must never be parsed as assumptions.
    ledger_text = re.split(r"^##\s+", text, maxsplit=1, flags=re.MULTILINE)[0]
    for line in ledger_text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and re.fullmatch(r"A-\d{3,}", cells[0], flags=re.IGNORECASE):
            cells.extend([""] * (9 - len(cells)))
            assumption_id = cells[0].upper()
            impact = cells[2].upper()
            status = cells[5].upper()
            if assumption_id in assumptions:
                diagnostics.append(Diagnostic("error", "ASSUMPTION_DUPLICATE", f"{assumption_id} appears more than once.", path=relative))
            assumptions[assumption_id] = {
                "id": assumption_id,
                "assumption": cells[1],
                "impact": impact,
                "confidence": cells[3],
                "evidence": _extract_ids(cells[4], evidence=True),
                "status": status,
                "destination_blocking": cells[6].strip().lower() == "true",
                "affected_decisions": sorted(
                    value for value in _extract_ids(cells[7]) if value.startswith("D-")
                ),
                "revalidate_when": cells[8],
            }
            if impact not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
                diagnostics.append(Diagnostic("error", "ASSUMPTION_IMPACT_INVALID", f"{assumption_id} has an invalid impact value.", path=relative))
            if status not in {"OPEN", "VALIDATED", "REFUTED", "ACCEPTED-RISK", "SUPERSEDED"}:
                diagnostics.append(Diagnostic("error", "ASSUMPTION_STATUS_INVALID", f"{assumption_id} has an invalid status value.", path=relative))
            if cells[6].strip().lower() not in {"true", "false"}:
                diagnostics.append(Diagnostic("error", "ASSUMPTION_BLOCKING_INVALID", f"{assumption_id} must declare Destination blocking as exactly true or false.", path=relative))

    for cells in _table_cells(text, "Accepted-risk receipts"):
        cells.extend([""] * (8 - len(cells)))
        if re.fullmatch(r"AR-\d{3,}", cells[0], flags=re.IGNORECASE) and re.fullmatch(r"A-\d{3,}", cells[1], flags=re.IGNORECASE):
            accepted_risk_receipts.setdefault(cells[1].upper(), []).append(cells)
    for cells in _table_cells(text, "Refutation receipts"):
        cells.extend([""] * (7 - len(cells)))
        if re.fullmatch(r"A-\d{3,}", cells[0], flags=re.IGNORECASE) and re.fullmatch(r"D-\d{3,}", cells[1], flags=re.IGNORECASE):
            key = (cells[0].upper(), cells[1].upper())
            refutation_receipts.setdefault(key, []).append(cells)

    high_open = sorted(
        assumption_id
        for assumption_id, assumption in assumptions.items()
        if assumption["impact"] in {"HIGH", "CRITICAL"} and assumption["status"] == "OPEN"
    )
    settled: list[dict[str, Any]] = []
    route_evidence: set[str] = set()
    for assumption_id, assumption in sorted(assumptions.items()):
        impact = assumption["impact"]
        status = assumption["status"]
        if impact not in {"HIGH", "CRITICAL"} or status == "OPEN":
            continue
        references: list[str]
        if status == "ACCEPTED-RISK":
            valid_receipts: list[str] = []
            for cells in accepted_risk_receipts.get(assumption_id, []):
                required = cells[2:8]
                accepting_party = cells[2].strip().lower()
                if (
                    all(not _ambiguous_required(value) for value in required)
                    and accepting_party not in {"codex", "agent", "ai"}
                    and _iso_datetime(cells[4]) is not None
                ):
                    valid_receipts.append(cells[0].upper())
            if not valid_receipts:
                diagnostics.append(Diagnostic("error", "ACCEPTED_RISK_RECEIPT_INVALID", f"{assumption_id} is high-impact ACCEPTED-RISK without a complete human AR-NNN receipt.", path=relative))
            references = sorted(set(valid_receipts))
        else:
            references = list(assumption["evidence"])
            route_evidence.update(references)
            if not references:
                diagnostics.append(Diagnostic("error", "ASSUMPTION_EVIDENCE_REQUIRED", f"Settled high-impact {assumption_id} needs E-NNN evidence.", path=relative))
        if _ambiguous_required(assumption["assumption"]) or _ambiguous_required(assumption["revalidate_when"]):
            diagnostics.append(Diagnostic("error", "ASSUMPTION_DETAIL_REQUIRED", f"Settled high-impact {assumption_id} needs concrete text and a freshness rule.", path=relative))
        if status == "REFUTED" and assumption["destination_blocking"]:
            affected = assumption["affected_decisions"]
            if not affected:
                diagnostics.append(Diagnostic("error", "REFUTED_ASSUMPTION_DECISIONS_REQUIRED", f"Destination-blocking refuted {assumption_id} must name affected D-NNN Decisions.", path=relative))
            for decision_id in affected:
                decision = nodes.get(decision_id)
                if decision is None or decision.kind != "decision":
                    diagnostics.append(Diagnostic("error", "REFUTED_ASSUMPTION_DECISION_MISSING", f"{assumption_id} names an unavailable affected Decision {decision_id}.", path=relative))
                    continue
                if decision.status in {"REOPENED", "SUPERSEDED"}:
                    continue
                candidates = refutation_receipts.get((assumption_id, decision_id), [])
                revision = _revision_number(decision.raw_fields.get("revision", ""))
                latest_transition = _latest_transition_time(decision)
                valid_candidates: list[list[str]] = []
                for receipt in candidates:
                    receipt_time = _iso_datetime(receipt[5])
                    evidence = _extract_ids(receipt[3], evidence=True)
                    receipt_revision = int(receipt[6]) if receipt[6].isdigit() and int(receipt[6]) > 0 else None
                    if (
                        receipt[2].strip().upper() == "STILL-VALID"
                        and evidence
                        and not _ambiguous_required(receipt[4])
                        and receipt_time is not None
                        and receipt_time <= datetime.now(timezone.utc)
                        and (latest_transition is None or receipt_time >= latest_transition)
                        and receipt_revision == revision
                    ):
                        valid_candidates.append(receipt)
                        route_evidence.update(evidence)
                if len(valid_candidates) != 1:
                    diagnostics.append(Diagnostic("error", "REFUTED_ASSUMPTION_INSPECTION_REQUIRED", f"{assumption_id} requires exactly one current STILL-VALID receipt for settled {decision_id}, or that Decision must be reopened or superseded.", node_id=decision_id, path=relative))
        settled.append({**assumption, "references": references})
    remaining_nonblocking = sorted(
        assumption_id
        for assumption_id, assumption in assumptions.items()
        if assumption["status"] == "OPEN" and not assumption["destination_blocking"]
    )
    public_records = [
        {
            "id": item["id"],
            "summary": _public_scalar(item["assumption"], "", 1_000),
            "impact": item["impact"] if item["impact"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"} else "INVALID",
            "confidence": _public_scalar(item.get("confidence"), "", 100),
            "status": item["status"] if item["status"] in {"OPEN", "VALIDATED", "REFUTED", "ACCEPTED-RISK", "SUPERSEDED"} else "INVALID",
            "destination_blocking": bool(item["destination_blocking"]),
            "affects": list(item["affected_decisions"]),
            "evidence": list(item["evidence"]),
            "revalidate_when": _public_scalar(item["revalidate_when"], "", 1_000),
        }
        for item in sorted(assumptions.values(), key=lambda value: value["id"])
    ][:MAX_ACTIVITY_ENTRIES]
    return high_open, diagnostics, settled, route_evidence, remaining_nonblocking, public_records


def _invariant_audit(
    effort_dir: Path,
    project_root: Path,
    manifest_present: bool,
) -> tuple[list[dict[str, Any]], list[Diagnostic], set[str], list[dict[str, Any]]]:
    diagnostics: list[Diagnostic] = []
    path = effort_dir / "INVARIANTS.md"
    relative = _safe_relative(project_root, path)
    if path.is_symlink() or not _within(effort_dir, path) or not _within(project_root, path):
        return [], [Diagnostic("error", "FIXED_ARTIFACT_PATH_ESCAPE", "INVARIANTS.md must be a real file inside the effort.", path=relative)], set(), []
    try:
        text = _read_regular_text(path, "INVARIANTS.md")
    except FileNotFoundError:
        if manifest_present:
            return [], [Diagnostic("error", "FIXED_ARTIFACT_MISSING", "V3 effort is missing INVARIANTS.md.", path=relative)], set(), []
        return [], [], set(), []
    except (OSError, UnicodeError, WayfinderError):
        return [], [Diagnostic("error", "INVARIANTS_INVALID", "INVARIANTS.md cannot be read safely.", path=relative)], set(), []

    active: list[dict[str, Any]] = []
    evidence_ids: set[str] = set()
    public_records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or not re.fullmatch(r"I-\d{3,}", cells[0], flags=re.IGNORECASE):
            continue
        cells.extend([""] * (9 - len(cells)))
        invariant_id = cells[0].upper()
        if invariant_id in seen:
            diagnostics.append(Diagnostic("error", "INVARIANT_DUPLICATE", f"{invariant_id} appears more than once.", path=relative))
        seen.add(invariant_id)
        status = cells[6].upper()
        public_records.append(
            {
                "id": invariant_id,
                "invariant": _public_scalar(cells[1], "", 1_000),
                "status": status if status in {"ACTIVE", "DEPRECATED", "SUPERSEDED"} else "INVALID",
                "scope": _public_scalar(cells[2], "", 1_000),
                "rationale": _public_scalar(cells[3], "", 1_000),
                "enforcement": _public_scalar(cells[4], "", 1_000),
                "evidence": _extract_ids(cells[5], evidence=True),
                "responsible_party": _public_scalar(cells[7], "", 200),
                "revalidate_when": _public_scalar(cells[8], "", 1_000),
            }
        )
        if status not in {"ACTIVE", "DEPRECATED", "SUPERSEDED"}:
            diagnostics.append(Diagnostic("error", "INVARIANT_STATUS_INVALID", f"{invariant_id} has an invalid status value.", path=relative))
            continue
        if status != "ACTIVE":
            continue
        evidence = _extract_ids(cells[5], evidence=True)
        required = (cells[1], cells[2], cells[3], cells[4], cells[7], cells[8])
        if any(_ambiguous_required(value) for value in required) or not evidence:
            diagnostics.append(Diagnostic("error", "ACTIVE_INVARIANT_INCOMPLETE", f"Active {invariant_id} needs scope, rationale, enforcement, E-NNN evidence, responsible party, and freshness rule.", path=relative))
        evidence_ids.update(evidence)
        active.append(
            {
                "id": invariant_id,
                "invariant": cells[1],
                "enforcement": cells[4],
                "evidence": evidence,
                "revalidate_when": cells[8],
            }
        )
    return active, diagnostics, evidence_ids, public_records[:MAX_ACTIVITY_ENTRIES]


def _evidence_audit(
    effort_dir: Path,
    project_root: Path,
    manifest: Mapping[str, Any],
    nodes: Mapping[str, Node],
    ledger_evidence: Iterable[str] = (),
    input_blocked: bool = False,
) -> tuple[list[Diagnostic], list[dict[str, Any]]]:
    diagnostics: list[Diagnostic] = []
    public_records: list[dict[str, Any]] = []
    if input_blocked:
        return diagnostics, public_records
    raw_refs = manifest.get("evidence", [])
    refs: list[Mapping[str, Any]] = []
    if isinstance(raw_refs, Mapping):
        refs = [{"id": key, **(value if isinstance(value, Mapping) else {})} for key, value in raw_refs.items()]
    elif isinstance(raw_refs, list):
        refs = [value if isinstance(value, Mapping) else {"id": value} for value in raw_refs]
    indexed: dict[str, Path] = {}
    indexed_entries: dict[str, Mapping[str, Any]] = {}
    seen_ids: set[str] = set()
    for entry in refs:
        evidence_id = _public_scalar(entry.get("id"), "", 64).upper()
        if not re.fullmatch(r"E-\d{3,}", evidence_id):
            diagnostics.append(Diagnostic("error", "EVIDENCE_ID_INVALID", "Manifest contains an invalid evidence ID.", path="[invalid evidence id]"))
            continue
        if evidence_id in seen_ids:
            diagnostics.append(Diagnostic("error", "EVIDENCE_DUPLICATE", f"Manifest indexes evidence {evidence_id} more than once."))
        seen_ids.add(evidence_id)
        canonical_path = _canonical_index_path("evidence", evidence_id, entry.get("path"))
        if canonical_path is None:
            diagnostics.append(Diagnostic("error", "EVIDENCE_PATH_INVALID", f"Manifest evidence {evidence_id} must use its exact canonical evidence/E-NNN.md path.", path="[invalid indexed evidence path]"))
            continue
        raw_path = canonical_path
        candidate = effort_dir / raw_path
        if not _within(effort_dir, candidate):
            diagnostics.append(Diagnostic("error", "EVIDENCE_MISSING", f"Manifest evidence {evidence_id} references a missing or unsafe file.", path="[unsafe indexed evidence path]"))
            continue
        if not candidate.is_file():
            diagnostics.append(Diagnostic("error", "EVIDENCE_MISSING", f"Manifest evidence {evidence_id} references a missing or unsafe file.", path=_safe_relative(project_root, candidate)))
            continue
        indexed[evidence_id] = candidate
        indexed_entries[evidence_id] = entry

    discovered: dict[str, Path] = {}
    evidence_dir = effort_dir / "evidence"
    invalid_evidence_name = False
    if evidence_dir.is_symlink() or not _within(effort_dir, evidence_dir) or not _within(project_root, evidence_dir):
        diagnostics.append(Diagnostic("error", "EVIDENCE_DIRECTORY_ESCAPE", "evidence/ must be a real directory inside the effort; external entries were not enumerated.", path="[unsafe evidence directory]"))
    elif evidence_dir.is_dir():
        try:
            evidence_entries, limit_exceeded = _bounded_directory_entries(evidence_dir)
        except WayfinderError:
            diagnostics.append(Diagnostic("error", "EVIDENCE_DIRECTORY_ESCAPE", "evidence/ could not be enumerated safely.", path="[unsafe evidence directory]"))
            evidence_entries, limit_exceeded = [], False
        if limit_exceeded:
            diagnostics.append(Diagnostic("error", "EVIDENCE_DIRECTORY_LIMIT", f"evidence/ exceeds the {MAX_DIRECTORY_ENTRIES}-entry safety limit; no entries were read.", path="[oversized evidence directory]"))
        for path in evidence_entries:
            if path.suffix.lower() != ".md":
                continue
            if not re.fullmatch(r"E-\d{3,}\.md", path.name):
                invalid_evidence_name = True
                continue
            if path.is_symlink() or not _within(effort_dir, path) or not _within(project_root, path):
                diagnostics.append(Diagnostic("error", "EVIDENCE_PATH_ESCAPE", f"Unindexed evidence path escapes the effort: {path.name}.", path=path.name))
                continue
            discovered[path.stem.upper()] = path
    if invalid_evidence_name:
        diagnostics.append(Diagnostic("error", "EVIDENCE_NAME_INVALID", "evidence/ contains a noncanonical E-NNN.md filename that was not read or exposed.", path="[invalid evidence filename]"))
    if manifest:
        for evidence_id, path in sorted(discovered.items()):
            if evidence_id not in indexed:
                diagnostics.append(Diagnostic("error", "EVIDENCE_UNINDEXED", f"Evidence {evidence_id} exists but is absent from EFFORT.json.", path=_safe_relative(project_root, path)))
    known = set(indexed) if manifest else set(discovered)
    ledger_evidence_ids = set(ledger_evidence)
    for evidence_id in sorted(ledger_evidence_ids - known):
        diagnostics.append(Diagnostic("error", "EVIDENCE_REFERENCE_MISSING", f"A route ledger references missing evidence {evidence_id}.", path=_safe_relative(project_root, effort_dir)))
    node_evidence: dict[str, set[str]] = {}
    for node in nodes.values():
        references = set(node.evidence) | set(node.informs)
        for transition in node.transitions:
            references.update(_extract_ids(transition.get("evidence"), evidence=True))
        for inspection in node.dependent_inspections:
            references.update(_extract_ids(inspection.get("evidence"), evidence=True))
        node_evidence[node.id] = references
        for evidence_id in sorted(references):
            if evidence_id not in known:
                diagnostics.append(Diagnostic("error", "EVIDENCE_REFERENCE_MISSING", f"{node.id} references missing evidence {evidence_id}.", node_id=node.id, path=node.path))

    if not manifest:
        return diagnostics, public_records
    effort_meta = manifest.get("effort", {}) if isinstance(manifest.get("effort"), Mapping) else {}
    effort_id = _public_scalar(effort_meta.get("id"), "", 100)
    destination_revision = effort_meta.get("destination_revision")
    route_evidence = {
        evidence_id
        for node in nodes.values()
        if (node.kind == "decision" and node.destination_blocking and node.terminal) or (node.kind == "gate" and node.status != "SUPERSEDED")
        for evidence_id in node_evidence.get(node.id, set())
    }
    route_evidence.update(ledger_evidence_ids)
    for evidence_id, path in sorted(indexed.items()):
        try:
            text = _read_regular_text(path, f"Evidence {evidence_id}")
        except (OSError, UnicodeError, WayfinderError):
            diagnostics.append(Diagnostic("error", "EVIDENCE_INVALID", f"{evidence_id} cannot be read safely.", path=_safe_relative(project_root, path)))
            continue
        heading_id = re.search(r"^#\s+(E-\d{3,})\b", text, flags=re.MULTILINE | re.IGNORECASE)
        if not heading_id or heading_id.group(1).upper() != evidence_id:
            diagnostics.append(Diagnostic("error", "EVIDENCE_ID_CONFLICT", f"{evidence_id} filename/index disagrees with its heading.", path=_safe_relative(project_root, path)))
        try:
            fields = _parse_fields(text)
        except WayfinderError:
            diagnostics.append(Diagnostic("error", "EVIDENCE_METADATA_INVALID", f"{evidence_id} contains duplicate or malformed metadata fields.", path=_safe_relative(project_root, path)))
            continue
        required = (
            "method",
            "observed_at",
            "subject_revision",
            "source",
            "source_type",
            "collector",
            "basis",
            "confidence",
            "sensitivity",
            "revalidate_when",
        )
        missing = [name for name in required if _ambiguous_required(fields.get(name))]
        if missing:
            diagnostics.append(Diagnostic("error", "EVIDENCE_PROVENANCE_INCOMPLETE", f"{evidence_id} has missing or ambiguous provenance: {', '.join(missing)}.", path=_safe_relative(project_root, path)))
        observed = fields.get("observed_at", "")
        if not _ambiguous_required(observed) and _iso_datetime(observed) is None:
            diagnostics.append(Diagnostic("error", "EVIDENCE_TIMESTAMP_INVALID", f"{evidence_id} Observed at must be a timezone-aware ISO timestamp.", path=_safe_relative(project_root, path)))
        elif _iso_datetime(observed) and _iso_datetime(observed) > datetime.now(timezone.utc):
            diagnostics.append(Diagnostic("error", "EVIDENCE_TIMESTAMP_FUTURE", f"{evidence_id} Observed at cannot be in the future.", path=_safe_relative(project_root, path)))
        enums = {
            "method": {"RESEARCH", "PROTOTYPE", "EXPERIMENT", "ANALYSIS", "OBSERVATION"},
            "source_type": {"PRIMARY", "AUTHORITATIVE", "SECONDARY", "LOCAL-OBSERVATION"},
            "basis": {"OBSERVED", "INFERRED"},
            "confidence": {"LOW", "MEDIUM", "HIGH"},
            "sensitivity": {"PUBLIC", "INTERNAL", "RESTRICTED"},
        }
        for field_name, allowed in enums.items():
            value = fields.get(field_name, "").strip().upper()
            if value and value not in allowed:
                diagnostics.append(Diagnostic("error", "EVIDENCE_ENUM_INVALID", f"{evidence_id} has an invalid {field_name} value.", path=_safe_relative(project_root, path)))
        subject_value = fields.get("subject_revision", "")
        subject_match = re.fullmatch(
            r"([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)\s*/\s*([1-9]\d*)",
            subject_value,
        )
        actual_subject = subject_match.group(1) if subject_match else ""
        actual_revision = int(subject_match.group(2)) if subject_match else None
        if subject_match is None:
            diagnostics.append(Diagnostic("error", "EVIDENCE_SUBJECT_SYNTAX_INVALID", f"{evidence_id} Subject / revision must be exactly '<effort-id> / <positive revision>'.", path=_safe_relative(project_root, path)))
        elif actual_subject != effort_id:
            diagnostics.append(Diagnostic("error", "EVIDENCE_SUBJECT_EFFORT_CONFLICT", f"{evidence_id} subject does not match the current effort identity.", path=_safe_relative(project_root, path)))
        if evidence_id in route_evidence:
            indexed_revision = indexed_entries[evidence_id].get("subject_revision")
            if not isinstance(indexed_revision, int) or isinstance(indexed_revision, bool) or indexed_revision < 1:
                diagnostics.append(Diagnostic("error", "EVIDENCE_INDEX_REVISION_REQUIRED", f"Manifest evidence {evidence_id} needs a positive integer subject_revision.", path=_safe_relative(project_root, path)))
            elif actual_revision != indexed_revision:
                diagnostics.append(Diagnostic("error", "EVIDENCE_INDEX_CONFLICT", f"Manifest evidence {evidence_id} subject_revision disagrees with its artifact.", path=_safe_relative(project_root, path)))
            if isinstance(destination_revision, int) and actual_revision != destination_revision:
                diagnostics.append(Diagnostic("error", "ROUTE_EVIDENCE_STALE", f"{evidence_id} subject revision does not match the current destination revision.", path=_safe_relative(project_root, path)))
            freshness = fields.get("revalidate_when", "")
            freshness_deadline = _iso_datetime(freshness) if freshness else None
            if freshness_deadline and freshness_deadline <= datetime.now(timezone.utc):
                diagnostics.append(Diagnostic("error", "ROUTE_EVIDENCE_EXPIRED", f"{evidence_id} freshness deadline has passed.", path=_safe_relative(project_root, path)))
            if not _substantive_section(text, "Conclusion") and not _substantive_section(text, "Evidence"):
                diagnostics.append(Diagnostic("error", "ROUTE_EVIDENCE_BODY_REQUIRED", f"{evidence_id} needs a substantive Conclusion or Evidence body.", path=_safe_relative(project_root, path)))
        title_match = re.search(rf"^#\s+{re.escape(evidence_id)}\s*[—:-]?\s*(.*?)\s*$", text, flags=re.MULTILINE | re.IGNORECASE)
        conclusion = _substantive_section(text, "Conclusion")
        freshness_rule = fields.get("revalidate_when", "")
        deadline = _iso_datetime(freshness_rule) if freshness_rule else None
        freshness = "expired" if deadline and deadline <= datetime.now(timezone.utc) else (
            "current" if actual_revision == destination_revision else ("rule-based" if not _ambiguous_required(freshness_rule) else "unknown")
        )
        public_records.append(
            {
                "id": evidence_id,
                "title": _public_scalar(title_match.group(1) if title_match else evidence_id, evidence_id, 300),
                "method": fields.get("method", "").upper() if fields.get("method", "").upper() in enums["method"] else "INVALID",
                "observed_at": observed if _iso_datetime(observed) is not None else "",
                "subject_revision": actual_revision,
                "source": _public_scalar(fields.get("source"), "", 1_000),
                "source_type": fields.get("source_type", "").upper() if fields.get("source_type", "").upper() in enums["source_type"] else "INVALID",
                "collector": _public_scalar(fields.get("collector"), "", 200),
                "basis": fields.get("basis", "").upper() if fields.get("basis", "").upper() in enums["basis"] else "INVALID",
                "confidence": fields.get("confidence", "").upper() if fields.get("confidence", "").upper() in enums["confidence"] else "INVALID",
                "sensitivity": fields.get("sensitivity", "").upper() if fields.get("sensitivity", "").upper() in enums["sensitivity"] else "INVALID",
                "revalidate_when": _public_scalar(freshness_rule, "", 1_000),
                "freshness": freshness,
                "path": _safe_relative(project_root, path),
                "conclusion": _public_scalar(conclusion, "", 2_000),
            }
        )
    return diagnostics, public_records[:MAX_ACTIVITY_ENTRIES]


def _normalized_prose(value: str) -> str:
    return " ".join(value.split())


def _section_metadata(text: str, heading: str) -> dict[str, str] | None:
    section = _section(text, heading)
    if not section:
        return None
    result: dict[str, str] = {}
    for line in section.splitlines():
        match = FIELD_LINE.match(line)
        if not match:
            continue
        key = _field_key(match.group(1))
        if key in result:
            return None
        result[key] = match.group(2).strip()
    return result


def _applicable_decision_nodes(
    nodes: Mapping[str, Node], intake: Mapping[str, Any] | None
) -> list[Node]:
    """Return the exact decisions that form the implementation trace baseline."""
    intake_ids: set[str] = set()
    if isinstance(intake, Mapping) and intake.get("state") == "AVAILABLE":
        bindings = intake.get("decision_bindings")
        if isinstance(bindings, list):
            intake_ids = {
                item.get("decision_id")
                for item in bindings
                if isinstance(item, Mapping)
                and item.get("status") == "RESOLVED"
                and isinstance(item.get("decision_id"), str)
            }
    return [
        node
        for node in sorted(nodes.values(), key=lambda item: item.id)
        if node.kind == "decision"
        and node.terminal
        and (node.destination_blocking or node.id in intake_ids)
    ]


def _decision_revision_receipt(value: Any) -> list[dict[str, int]] | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized.upper() == "NONE":
        return []
    pairs: list[dict[str, int]] = []
    seen: set[str] = set()
    for item in normalized.split(","):
        match = re.fullmatch(r"\s*(D-\d{3,})@(\d+)\s*", item, flags=re.IGNORECASE)
        if match is None:
            return None
        decision_id = match.group(1).upper()
        revision = int(match.group(2))
        if decision_id in seen or revision < 1:
            return None
        seen.add(decision_id)
        pairs.append({"id": decision_id, "revision": revision})
    return sorted(pairs, key=lambda item: item["id"])


def _exit_audit(
    effort_dir: Path,
    project_root: Path,
    manifest: Mapping[str, Any],
    nodes: Mapping[str, Node],
    settled_assumptions: Sequence[Mapping[str, Any]],
    active_invariants: Sequence[Mapping[str, Any]],
    remaining_nonblocking: Sequence[str],
    intake: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[Diagnostic]]:
    receipt_path = effort_dir / "EXIT.md"
    relative = _safe_relative(project_root, receipt_path)
    base = {"status": "missing", "valid": False, "path": relative, "destination_revision": None}
    if not receipt_path.exists() and not receipt_path.is_symlink():
        return base, []
    if receipt_path.is_symlink() or not _within(effort_dir, receipt_path) or not _within(project_root, receipt_path):
        return {**base, "status": "invalid"}, [Diagnostic("error", "EXIT_PATH_ESCAPE", "EXIT.md resolves outside the effort.", path=relative)]
    try:
        text = _read_regular_text(receipt_path, "EXIT.md")
    except (OSError, UnicodeError, WayfinderError):
        return {**base, "status": "invalid"}, [Diagnostic("error", "EXIT_RECEIPT_INVALID", "EXIT.md cannot be read safely.", path=relative)]

    diagnostics: list[Diagnostic] = []
    try:
        fields = _parse_fields(text)
    except WayfinderError:
        return {**base, "status": "invalid"}, [
            Diagnostic("error", "EXIT_RECEIPT_INVALID", "EXIT.md contains duplicate or malformed receipt metadata fields.", path=relative)
        ]
    effort_meta = manifest.get("effort", {}) if isinstance(manifest.get("effort"), Mapping) else {}
    expected_revision = effort_meta.get("destination_revision")
    receipt_revision = _revision_number(fields.get("destination_revision", ""))
    required_fields = ("effort", "schema", "receipt_status", "destination_revision", "completed_at", "completed_by", "manifest_hash")
    missing = [field_name for field_name in required_fields if _ambiguous_required(fields.get(field_name))]
    if missing:
        diagnostics.append(Diagnostic("error", "EXIT_FIELDS_REQUIRED", f"EXIT.md has missing or ambiguous fields: {', '.join(missing)}.", path=relative))
    if fields.get("effort") != effort_meta.get("id"):
        diagnostics.append(Diagnostic("error", "EXIT_EFFORT_CONFLICT", "EXIT.md effort does not match EFFORT.json.", path=relative))
    if fields.get("schema") != str(SCHEMA_VERSION):
        diagnostics.append(Diagnostic("error", "EXIT_SCHEMA_INVALID", f"EXIT.md schema must be {SCHEMA_VERSION}.", path=relative))
    if fields.get("receipt_status", "").upper() != "CURRENT":
        diagnostics.append(Diagnostic("error", "EXIT_STATUS_NOT_CURRENT", "EXIT.md receipt status must be CURRENT.", path=relative))
    if receipt_revision != expected_revision:
        diagnostics.append(Diagnostic("error", "EXIT_REVISION_STALE", "EXIT.md destination revision does not match the current manifest revision.", path=relative))
    completed_at = _iso_datetime(fields.get("completed_at", ""))
    if completed_at is None or completed_at > datetime.now(timezone.utc):
        diagnostics.append(Diagnostic("error", "EXIT_TIMESTAMP_INVALID", "EXIT.md Completed at must be a non-future timezone-aware timestamp.", path=relative))
    manifest_updated_at = _iso_datetime(_public_scalar(effort_meta.get("updated_at"), "", 64))
    if completed_at and manifest_updated_at and completed_at < manifest_updated_at:
        diagnostics.append(Diagnostic("error", "EXIT_BEFORE_MANIFEST_UPDATE", "EXIT.md predates the current manifest update.", path=relative))
    try:
        actual_hash = hashlib.sha256(_read_regular_bytes(effort_dir / "EFFORT.json", "EFFORT.json")).hexdigest()
    except (OSError, WayfinderError):
        actual_hash = ""
    receipt_hash = fields.get("manifest_hash", "").lower()
    public_receipt_hash = (
        receipt_hash
        if re.fullmatch(r"[0-9a-f]{64}", receipt_hash) and receipt_hash == actual_hash
        else ""
    )
    if not public_receipt_hash:
        diagnostics.append(Diagnostic("error", "EXIT_MANIFEST_HASH_MISMATCH", "EXIT.md Manifest hash does not match the current EFFORT.json bytes.", path=relative))

    accepted_destination = _substantive_section(text, "Destination accepted for execution planning")
    if not accepted_destination:
        accepted_destination = _substantive_section(text, "Destination accepted for specification")
    if _normalized_prose(accepted_destination) != _normalized_prose(_public_scalar(effort_meta.get("destination"), "", 4_000)):
        diagnostics.append(Diagnostic("error", "EXIT_DESTINATION_CONFLICT", "EXIT.md accepted destination does not match EFFORT.json.", path=relative))

    assumption_rows = _table_cells(text, "Validated assumptions and accepted risks")
    assumption_row_map: dict[str, list[str]] = {}
    for cells in assumption_rows:
        if len(cells) < 4 or not re.fullmatch(r"A-\d{3,}", cells[0], flags=re.IGNORECASE):
            continue
        assumption_id = cells[0].upper()
        if assumption_id in assumption_row_map:
            diagnostics.append(Diagnostic("error", "EXIT_ASSUMPTION_ROW_DUPLICATE", f"EXIT.md lists assumption {assumption_id} more than once.", path=relative))
        assumption_row_map[assumption_id] = cells
    expected_assumptions = {str(item["id"]): item for item in settled_assumptions}
    if set(assumption_row_map) != set(expected_assumptions):
        diagnostics.append(Diagnostic("error", "EXIT_ASSUMPTION_SET_MISMATCH", "EXIT.md must list exactly the settled high-impact assumptions and accepted-risk receipts.", path=relative))
    for assumption_id, cells in sorted(assumption_row_map.items()):
        expected = expected_assumptions.get(assumption_id)
        if expected is None:
            continue
        references = set(re.findall(r"\b(?:E|AR)-\d{3,}\b", cells[2], flags=re.IGNORECASE))
        references = {value.upper() for value in references}
        if (
            cells[1].strip().upper() != str(expected["status"]).upper()
            or references != set(expected["references"])
            or _normalized_prose(cells[3]) != _normalized_prose(str(expected["revalidate_when"]))
        ):
            diagnostics.append(Diagnostic("error", "EXIT_ASSUMPTION_ROW_CONFLICT", f"EXIT.md assumption row {assumption_id} does not match the canonical ledger.", path=relative))

    invariant_rows = _table_cells(text, "Active invariants")
    invariant_row_map: dict[str, list[str]] = {}
    for cells in invariant_rows:
        if len(cells) < 5 or not re.fullmatch(r"I-\d{3,}", cells[0], flags=re.IGNORECASE):
            continue
        invariant_id = cells[0].upper()
        if invariant_id in invariant_row_map:
            diagnostics.append(Diagnostic("error", "EXIT_INVARIANT_ROW_DUPLICATE", f"EXIT.md lists invariant {invariant_id} more than once.", path=relative))
        invariant_row_map[invariant_id] = cells
    expected_invariants = {str(item["id"]): item for item in active_invariants}
    if set(invariant_row_map) != set(expected_invariants):
        diagnostics.append(Diagnostic("error", "EXIT_INVARIANT_SET_MISMATCH", "EXIT.md must list exactly the current active invariants.", path=relative))
    for invariant_id, cells in sorted(invariant_row_map.items()):
        expected = expected_invariants.get(invariant_id)
        if expected is None:
            continue
        if (
            _normalized_prose(cells[1]) != _normalized_prose(str(expected["invariant"]))
            or _normalized_prose(cells[2]) != _normalized_prose(str(expected["enforcement"]))
            or set(_extract_ids(cells[3], evidence=True)) != set(expected["evidence"])
            or _normalized_prose(cells[4]) != _normalized_prose(str(expected["revalidate_when"]))
        ):
            diagnostics.append(Diagnostic("error", "EXIT_INVARIANT_ROW_CONFLICT", f"EXIT.md invariant row {invariant_id} does not match the canonical ledger.", path=relative))

    decision_rows = _table_cells(text, "Resolved destination-blocking Decisions")
    decision_row_map: dict[str, list[str]] = {}
    for cells in decision_rows:
        if len(cells) < 5 or not re.fullmatch(r"D-\d{3,}", cells[0], flags=re.IGNORECASE):
            continue
        decision_id = cells[0].upper()
        if decision_id in decision_row_map:
            diagnostics.append(Diagnostic("error", "EXIT_DECISION_ROW_DUPLICATE", f"EXIT.md lists decision {decision_id} more than once.", path=relative))
        decision_row_map[decision_id] = cells
    decision_ids = set(decision_row_map)
    expected_decisions = {node.id for node in nodes.values() if node.kind == "decision" and node.destination_blocking and node.terminal}
    if decision_ids != expected_decisions:
        diagnostics.append(Diagnostic("error", "EXIT_DECISION_SET_MISMATCH", "EXIT.md must list exactly the current terminal destination-blocking Decisions.", path=relative))
    for decision_id, cells in sorted(decision_row_map.items()):
        if _ambiguous_required(cells[1]) or _ambiguous_required(cells[2]) or not EVIDENCE_ID.search(cells[3]) or _revision_number(cells[4]) is None:
            diagnostics.append(Diagnostic("error", "EXIT_DECISION_ROW_INVALID", f"EXIT.md decision row {decision_id} is incomplete.", path=relative))
            continue
        node = nodes.get(decision_id)
        if node is None or decision_id not in expected_decisions:
            continue
        receipt_evidence = set(_extract_ids(cells[3], evidence=True))
        decision_revision = _revision_number(cells[4])
        node_revision = _revision_number(node.raw_fields.get("revision", ""))
        if (
            _normalized_prose(cells[1]) != _normalized_prose(node.resolution)
            or _normalized_prose(cells[2]) != _normalized_prose(node.decision_authority)
            or receipt_evidence != set(node.evidence)
            or decision_revision != node_revision
        ):
            diagnostics.append(Diagnostic("error", "EXIT_DECISION_ROW_CONFLICT", f"EXIT.md decision row {decision_id} does not match the current canonical ticket.", path=relative))

    baseline_payload: dict[str, Any] = {}
    if isinstance(intake, Mapping) and intake.get("state") == "AVAILABLE":
        domain = intake.get("domain") if isinstance(intake.get("domain"), Mapping) else {}
        primary_domain = domain.get("primary_domain") or domain.get("selected")
        handoffs = {
            "SOFTWARE": "specification, tickets, and build planning",
            "GENERAL_PROJECT": "work breakdown, schedule, and delivery controls",
            "FINANCE_REPORTING": "reporting procedure, control, and review execution",
            "OTHER": "execution planning and controls",
        }
        next_workflow = handoffs.get(primary_domain)
        baseline_fields = _section_metadata(text, "Execution baseline and handoff")
        decision_revisions = [
            {"id": node.id, "revision": _revision_number(node.raw_fields.get("revision", ""))}
            for node in _applicable_decision_nodes(nodes, intake)
        ]
        intake_revision = intake.get("revision") if isinstance(intake.get("revision"), int) else None
        receipt_decisions = _decision_revision_receipt(
            baseline_fields.get("applicable_decision_revisions") if baseline_fields else None
        )
        baseline_payload = {
            "primary_domain": primary_domain if primary_domain in handoffs else None,
            "next_workflow": next_workflow or "",
            "effort_id": _public_scalar(effort_meta.get("id"), "", 100),
            "manifest_hash": public_receipt_hash,
            "destination_revision": receipt_revision,
            "intake_revision": intake_revision,
            "applicable_decisions": decision_revisions,
        }
        expected_map = f".codex/wayfinder/efforts/{effort_dir.name}/MAP.md"
        expected_decision_index = f".codex/wayfinder/efforts/{effort_dir.name}/decisions"
        expected_evidence_index = f".codex/wayfinder/efforts/{effort_dir.name}/evidence"
        baseline_valid = bool(
            baseline_fields
            and primary_domain in handoffs
            and baseline_fields.get("primary_domain") == primary_domain
            and baseline_fields.get("recommended_next_workflow") == next_workflow
            and baseline_fields.get("effort_id") == effort_meta.get("id")
            and baseline_fields.get("manifest_hash", "").lower() == public_receipt_hash
            and _revision_number(baseline_fields.get("destination_revision", "")) == receipt_revision
            and _revision_number(baseline_fields.get("intake_revision", "")) == intake_revision
            and receipt_decisions == decision_revisions
            and baseline_fields.get("primary_map") == expected_map
            and baseline_fields.get("decision_index") == expected_decision_index
            and baseline_fields.get("evidence_index") == expected_evidence_index
            and all(item["revision"] is not None for item in decision_revisions)
        )
        if not baseline_valid:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "EXIT_IMPLEMENTATION_BASELINE_INVALID",
                    "EXIT.md execution baseline must exactly bind the current domain, workflow, effort, manifest, destination and intake revisions, applicable Decision revisions, and canonical indexes.",
                    path=relative,
                )
            )

    gate_rows = _table_cells(text, "Delivery Gates defined for later evaluation")
    gate_row_map: dict[str, list[str]] = {}
    for cells in gate_rows:
        if len(cells) < 6 or not re.fullmatch(r"G-\d{3,}", cells[0], flags=re.IGNORECASE):
            continue
        gate_id = cells[0].upper()
        if gate_id in gate_row_map:
            diagnostics.append(Diagnostic("error", "EXIT_GATE_ROW_DUPLICATE", f"EXIT.md lists Gate {gate_id} more than once.", path=relative))
        gate_row_map[gate_id] = cells
    gate_ids = set(gate_row_map)
    expected_gates = {node.id for node in nodes.values() if node.kind == "gate" and node.status != "SUPERSEDED"}
    if gate_ids != expected_gates:
        diagnostics.append(Diagnostic("error", "EXIT_GATE_SET_MISMATCH", "EXIT.md must list exactly the current defined delivery Gates.", path=relative))
    for gate_id, cells in sorted(gate_row_map.items()):
        receipt_revalidates = {value for value in _extract_ids(cells[3]) if value.startswith("D-")}
        receipt_milestones = {value for value in _extract_ids(cells[4]) if value.startswith("M-")}
        if any(_ambiguous_required(value) for value in cells[1:]) or not receipt_revalidates or not receipt_milestones:
            diagnostics.append(Diagnostic("error", "EXIT_GATE_ROW_INVALID", f"EXIT.md Gate row {gate_id} is incomplete.", path=relative))
            continue
        node = nodes.get(gate_id)
        if node is None or gate_id not in expected_gates:
            continue
        if (
            _normalized_prose(cells[1]) != _normalized_prose(node.delivery_condition)
            or _normalized_prose(cells[2]) != _normalized_prose(node.responsible_party)
            or receipt_revalidates != set(node.revalidates)
            or receipt_milestones != set(node.gates)
            or _normalized_prose(cells[5]) != _normalized_prose(node.raw_fields.get("revalidate_when", ""))
        ):
            diagnostics.append(Diagnostic("error", "EXIT_GATE_ROW_CONFLICT", f"EXIT.md Gate row {gate_id} does not match the current canonical ticket and typed links.", path=relative))

    success_rows = _table_cells(text, "Success conditions")
    if not success_rows or any(len(cells) < 4 or _ambiguous_required(cells[0]) or _ambiguous_required(cells[1]) or not EVIDENCE_ID.search(cells[2]) or _ambiguous_required(cells[3]) for cells in success_rows):
        diagnostics.append(Diagnostic("error", "EXIT_SUCCESS_CONDITIONS_REQUIRED", "EXIT.md needs at least one complete success-condition route row.", path=relative))
    unknown_section = _section(text, "Remaining non-blocking unknowns")
    expected_unknowns = set(remaining_nonblocking)
    actual_unknowns: set[str] = set()
    unknown_shape_valid = bool(unknown_section)
    if unknown_section:
        bullets = [line.strip()[1:].strip() for line in unknown_section.splitlines() if line.strip().startswith("-")]
        if len(bullets) == 1 and bullets[0].rstrip(".").strip().upper() == "NONE":
            actual_unknowns = set()
        elif bullets:
            for bullet in bullets:
                matches = {value.upper() for value in re.findall(r"\b(?:A|D)-\d{3,}\b", bullet, flags=re.IGNORECASE)}
                if len(matches) != 1:
                    unknown_shape_valid = False
                actual_unknowns.update(matches)
        else:
            unknown_shape_valid = False
    if not unknown_shape_valid or actual_unknowns != expected_unknowns:
        diagnostics.append(Diagnostic("error", "EXIT_UNKNOWN_SET_MISMATCH", "EXIT.md must list exactly the current remaining non-blocking Decision and assumption IDs, or exactly None.", path=relative))
    triggers = _substantive_section(text, "Revalidation triggers")
    if _ambiguous_required(triggers):
        diagnostics.append(Diagnostic("error", "EXIT_REVALIDATION_TRIGGER_REQUIRED", "EXIT.md needs at least one concrete revalidation trigger.", path=relative))
    checks = re.findall(r"^-\s*\[([ xX])\]\s+", _section(text, "Completion validation"), flags=re.MULTILINE)
    if len(checks) < 7 or any(value.strip().lower() != "x" for value in checks):
        diagnostics.append(Diagnostic("error", "EXIT_VALIDATION_INCOMPLETE", "EXIT.md Completion validation must contain at least seven checked items.", path=relative))

    valid = not any(item.severity == "error" for item in diagnostics)
    return {
        "status": "current" if valid else "invalid",
        "valid": valid,
        "path": relative,
        "destination_revision": receipt_revision,
        "manifest_hash": public_receipt_hash,
        "implementation_baseline": baseline_payload,
    }, diagnostics


def _last_updated(paths: Iterable[Path]) -> str | None:
    stamps: list[float] = []
    for path in paths:
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            continue
    if not stamps:
        return None
    return datetime.fromtimestamp(max(stamps), tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ambiguous_required(value: Any) -> bool:
    if value is not None and not isinstance(value, (str, int, float, bool)):
        return True
    normalized = _public_scalar(value, "", 2_000)
    sentinel = re.sub(r"\s+", " ", normalized).upper()
    return (
        not normalized
        or "{{" in normalized
        or "}}" in normalized
        or " | " in normalized
        or sentinel
        in {
            "-",
            "—",
            "NONE",
            "UNKNOWN",
            "TBD",
            "TBC",
            "TODO",
            "UNASSIGNED",
            "NOT ASSIGNED",
            "NOT RECORDED",
            "PENDING",
            "N/A",
        }
    )


def _iso_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _revision_number(value: str) -> int | None:
    matches = re.findall(r"\d+", str(value))
    return int(matches[-1]) if matches else None


def _non_agent_actor(value: Any) -> bool:
    if _ambiguous_required(value):
        return False
    normalized = _public_scalar(value, "", 200).strip().lower()
    return normalized not in {"codex", "agent", "ai", "assistant", "system", "automation"}


def _latest_transition_time(node: Node) -> datetime | None:
    timestamps = [
        parsed
        for transition in node.transitions
        if (parsed := _iso_datetime(str(transition.get("timestamp") or ""))) is not None
    ]
    return max(timestamps) if timestamps else None


def _transition_diagnostics(node: Node, manifest_present: bool) -> list[Diagnostic]:
    if not node.transitions:
        if manifest_present:
            return [
                Diagnostic(
                    "error",
                    "TRANSITION_HISTORY_MISSING",
                    f"{node.id} has no parseable append-only transition history.",
                    node_id=node.id,
                    path=node.path,
                )
            ]
        return []
    diagnostics: list[Diagnostic] = []
    legal = GATE_TRANSITIONS if node.kind == "gate" else DECISION_TRANSITIONS
    creation = "DEFINED" if node.kind == "gate" else "OPEN"
    previous_to: str | None = None
    previous_timestamp: datetime | None = None
    for index, transition in enumerate(node.transitions):
        before = transition["from"]
        after = str(transition["to"])
        if index == 0:
            if before is not None or after != creation:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "TRANSITION_CREATION_INVALID",
                        f"{node.id} history must begin with — -> {creation}.",
                        node_id=node.id,
                        path=node.path,
                    )
                )
        else:
            if before != previous_to:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "TRANSITION_CHAIN_BROKEN",
                        f"{node.id} transition history contains a discontinuity.",
                        node_id=node.id,
                        path=node.path,
                    )
                )
            if before not in legal or after not in legal.get(before, set()):
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "TRANSITION_ILLEGAL",
                        f"{node.id} transition history contains a state change outside the legal table.",
                        node_id=node.id,
                        path=node.path,
                    )
                )
        if _ambiguous_required(transition.get("actor")):
            diagnostics.append(Diagnostic("error", "TRANSITION_ACTOR_REQUIRED", f"{node.id} has a transition without an actor.", node_id=node.id, path=node.path))
        timestamp = str(transition.get("timestamp") or "")
        parsed_timestamp = _iso_datetime(timestamp)
        if parsed_timestamp is None:
            diagnostics.append(Diagnostic("error", "TRANSITION_TIMESTAMP_INVALID", f"{node.id} has a transition without a valid timezone-aware timestamp.", node_id=node.id, path=node.path))
        else:
            if parsed_timestamp > datetime.now(timezone.utc):
                diagnostics.append(Diagnostic("error", "TRANSITION_TIMESTAMP_FUTURE", f"{node.id} has a future-dated transition.", node_id=node.id, path=node.path))
            if previous_timestamp and parsed_timestamp < previous_timestamp:
                diagnostics.append(Diagnostic("error", "TRANSITION_TIMESTAMP_ORDER", f"{node.id} transition history is not chronological.", node_id=node.id, path=node.path))
            previous_timestamp = parsed_timestamp
        if _ambiguous_required(transition.get("reason")):
            diagnostics.append(Diagnostic("error", "TRANSITION_REASON_REQUIRED", f"{node.id} has a transition without a reason.", node_id=node.id, path=node.path))
        previous_to = after
    ticket_status = node.raw_fields.get("status", node.status).strip().upper().replace(" ", "-")
    if previous_to != ticket_status:
        diagnostics.append(
            Diagnostic(
                "error",
                "TRANSITION_STATUS_DRIFT",
                f"{node.id} transition history does not end at the ticket's current status.",
                node_id=node.id,
                path=node.path,
            )
        )
    if node.kind == "decision" and node.destination_blocking and node.terminal:
        final_evidence = str(node.transitions[-1].get("evidence") or "")
        if not _extract_ids(final_evidence, evidence=True):
            diagnostics.append(
                Diagnostic(
                    "error",
                    "TERMINAL_TRANSITION_EVIDENCE_REQUIRED",
                    f"{node.id} terminal transition must cite an E-NNN receipt.",
                    node_id=node.id,
                    path=node.path,
                )
            )
    return diagnostics


def _manifest_schema_diagnostics(manifest: Mapping[str, Any], path: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    effort = manifest.get("effort")
    if not isinstance(effort, Mapping):
        return [Diagnostic("error", "EFFORT_METADATA_REQUIRED", "EFFORT.json requires an effort object.", path=path)]
    for field_name in ("id", "title", "destination", "state", "created_at", "updated_at"):
        if _ambiguous_required(effort.get(field_name)):
            diagnostics.append(Diagnostic("error", "EFFORT_METADATA_REQUIRED", f"EFFORT.json effort.{field_name} is required.", path=path))
    revision = effort.get("destination_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        diagnostics.append(Diagnostic("error", "DESTINATION_REVISION_INVALID", "EFFORT.json destination_revision must be a positive integer.", path=path))
    for field_name in ("created_at", "updated_at"):
        value = effort.get(field_name)
        if not _ambiguous_required(value) and _iso_datetime(_public_scalar(value, "", 64)) is None:
            diagnostics.append(Diagnostic("error", "EFFORT_TIMESTAMP_INVALID", f"EFFORT.json effort.{field_name} must be a timezone-aware timestamp.", path=path))

    current = _public_scalar(manifest.get("current_phase_id"), "", 80)
    if current not in PHASE_IDS:
        diagnostics.append(Diagnostic("error", "CURRENT_PHASE_INVALID", f"EFFORT.json current_phase_id must be one of {', '.join(sorted(PHASE_IDS))}.", path=path))

    raw_phases = manifest.get("phases")
    phase_ids = [_public_scalar(item.get("id"), "", 80) for item in raw_phases if isinstance(item, Mapping)] if isinstance(raw_phases, list) else []
    expected_phase_ids = [phase["id"] for phase in PHASES]
    if phase_ids != expected_phase_ids or len(raw_phases or []) != len(PHASES):
        diagnostics.append(Diagnostic("error", "PHASE_SCHEMA_INVALID", "EFFORT.json phases must contain the five fixed phases once, in canonical order.", path=path))
    else:
        active_ids: list[str] = []
        current_index = expected_phase_ids.index(current) if current in expected_phase_ids else -1
        for index, (item, expected) in enumerate(zip(raw_phases, PHASES)):
            legacy_p4 = expected["id"] == "p4-ready" and item.get("label") == LEGACY_P4_CONTRACT["label"]
            if item.get("label") != expected["label"] and not legacy_p4:
                diagnostics.append(Diagnostic("error", "PHASE_LABEL_INVALID", f"{expected['id']} label must match the fixed lifecycle contract.", path=path))
            legacy_description = expected["id"] == "p4-ready" and item.get("description") == LEGACY_P4_CONTRACT["description"]
            if item.get("description") != expected["description"] and not legacy_description:
                diagnostics.append(Diagnostic("error", "PHASE_DESCRIPTION_INVALID", f"{expected['id']} description must match the fixed lifecycle contract.", path=path))
            state = _public_scalar(item.get("state"), "[invalid]", 40).lower()
            if state not in {"active", "upcoming", "complete", "blocked"}:
                diagnostics.append(Diagnostic("error", "PHASE_STATE_INVALID", f"{expected['id']} has an invalid state value.", path=path))
            if state == "active":
                active_ids.append(expected["id"])
            expected_state = "active" if index == current_index else ("complete" if index < current_index else "upcoming")
            if current_index >= 0 and state != expected_state:
                diagnostics.append(Diagnostic("error", "PHASE_ORDER_INVALID", f"{expected['id']} must be {expected_state} relative to current_phase_id.", path=path))
        if active_ids != [current]:
            diagnostics.append(Diagnostic("error", "CURRENT_PHASE_STATE_CONFLICT", "Exactly current_phase_id must have phase state active.", path=path))

    raw_checkpoints = manifest.get("checkpoints")
    checkpoint_ids = [_public_scalar(item.get("id"), "", 80) for item in raw_checkpoints if isinstance(item, Mapping)] if isinstance(raw_checkpoints, list) else []
    if checkpoint_ids != list(CHECKPOINT_SCHEMA) or len(raw_checkpoints or []) != len(CHECKPOINT_SCHEMA):
        diagnostics.append(Diagnostic("error", "CHECKPOINT_SCHEMA_INVALID", "EFFORT.json checkpoints must contain the five fixed checkpoints once, in canonical order.", path=path))
    else:
        current_index = [phase["id"] for phase in PHASES].index(current) if current in PHASE_IDS else -1
        for index, item in enumerate(raw_checkpoints):
            checkpoint_id = _public_scalar(item["id"], "", 80)
            phase_id, label = CHECKPOINT_SCHEMA[checkpoint_id]
            checkpoint_contract = next(phase["checkpoint"] for phase in PHASES if phase["id"] == phase_id)
            legacy_checkpoint = checkpoint_id == "cp4-handoff" and item.get("label") == LEGACY_P4_CONTRACT["checkpoint_label"]
            if item.get("phase_id") != phase_id or (item.get("label") != label and not legacy_checkpoint):
                diagnostics.append(Diagnostic("error", "CHECKPOINT_MAPPING_INVALID", f"{checkpoint_id} must match its fixed phase and label mapping.", path=path))
            checkpoint_status = _public_scalar(item.get("status"), "", 40).upper()
            if checkpoint_status not in {"DUE", "UPCOMING", "COMPLETE", "DORMANT", "BLOCKED"}:
                diagnostics.append(Diagnostic("error", "CHECKPOINT_STATUS_INVALID", f"{checkpoint_id} has an invalid status.", path=path))
            elif current_index >= 0:
                allowed_statuses = (
                    {"COMPLETE"}
                    if index < current_index
                    else ({"UPCOMING"} if index > current_index else ({"DORMANT", "DUE", "BLOCKED"} if phase_id == "p5-delivery" else {"DUE", "BLOCKED"}))
                )
                if checkpoint_status not in allowed_statuses:
                    diagnostics.append(Diagnostic("error", "CHECKPOINT_ORDER_INVALID", f"{checkpoint_id} status is inconsistent with current_phase_id.", path=path))
            if not isinstance(item.get("run_recommended"), bool):
                diagnostics.append(Diagnostic("error", "CHECKPOINT_RECOMMENDATION_INVALID", f"{checkpoint_id} run_recommended must be boolean.", path=path))
            elif item.get("run_recommended") is not checkpoint_contract["recommended_run"]:
                diagnostics.append(Diagnostic("error", "CHECKPOINT_RECOMMENDATION_INVALID", f"{checkpoint_id} run_recommended does not match the fixed lifecycle contract.", path=path))
            legacy_details = checkpoint_id == "cp4-handoff" and item.get("due_when") == LEGACY_P4_CONTRACT["due_when"] and item.get("reason") == LEGACY_P4_CONTRACT["reason"]
            if (item.get("due_when") != checkpoint_contract["due_when"] or item.get("reason") != checkpoint_contract["reason"]) and not legacy_details:
                diagnostics.append(Diagnostic("error", "CHECKPOINT_DETAIL_INVALID", f"{checkpoint_id} due_when and reason must match the fixed lifecycle contract.", path=path))

    raw_milestones = manifest.get("milestones")
    milestone_ids = [_public_scalar(item.get("id"), "", 80) for item in raw_milestones if isinstance(item, Mapping)] if isinstance(raw_milestones, list) else []
    if milestone_ids != list(MILESTONE_SCHEMA) or len(raw_milestones or []) != len(MILESTONE_SCHEMA):
        diagnostics.append(Diagnostic("error", "MILESTONE_SCHEMA_INVALID", "EFFORT.json milestones must contain the five fixed milestones once, in canonical order.", path=path))
    else:
        current_index = [phase["id"] for phase in PHASES].index(current) if current in PHASE_IDS else -1
        for index, item in enumerate(raw_milestones):
            milestone_id = _public_scalar(item["id"], "", 80)
            phase_id, label = MILESTONE_SCHEMA[milestone_id]
            if item.get("phase_id") != phase_id or item.get("label") != label:
                diagnostics.append(Diagnostic("error", "MILESTONE_MAPPING_INVALID", f"{milestone_id} must match its fixed phase and label mapping.", path=path))
            milestone_status = _public_scalar(item.get("status"), "", 40).upper()
            if milestone_status not in {"PENDING", "DUE", "COMPLETE", "BLOCKED", "AT-RISK"}:
                diagnostics.append(Diagnostic("error", "MILESTONE_STATUS_INVALID", f"{milestone_id} has an invalid status.", path=path))
            elif current_index >= 0:
                allowed_statuses = {"COMPLETE"} if index < current_index else ({"PENDING"} if index > current_index else {"PENDING", "DUE", "BLOCKED", "AT-RISK"})
                if milestone_status not in allowed_statuses:
                    diagnostics.append(Diagnostic("error", "MILESTONE_ORDER_INVALID", f"{milestone_id} status is inconsistent with current_phase_id.", path=path))
            criteria = item.get("criteria")
            if criteria != [MILESTONE_CRITERIA[milestone_id]]:
                diagnostics.append(Diagnostic("error", "MILESTONE_CRITERIA_INVALID", f"{milestone_id} criteria must match the fixed lifecycle contract.", path=path))
    return diagnostics


def _validate(
    nodes: Mapping[str, Node],
    project_root: Path,
    effort_dir: Path,
    manifest: Mapping[str, Any],
    manifest_present: bool,
    manifest_error: str | None,
    map_text: str,
    missing_manifest_paths: Sequence[tuple[str, str]],
    invalid_ticket_metadata: Sequence[tuple[str, str]],
    invalid_manifest_paths: Sequence[str],
    invalid_manifest_ids: Sequence[str],
    manifest_edges: Sequence[Mapping[str, str]],
    unindexed_tickets: Sequence[str],
    unsafe_ticket_paths: Sequence[str],
    unsafe_ticket_directories: Sequence[str],
    unsafe_ticket_names: Sequence[str],
    unsafe_ticket_ids: Sequence[str],
    ticket_directory_limits: Sequence[str],
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    manifest_entries = {
        _public_scalar(entry.get("id"), "", 64).upper(): entry
        for _, entry in _manifest_node_refs(manifest)
        if NODE_ID.fullmatch(_public_scalar(entry.get("id"), "", 64).upper())
    }
    milestone_ids = {
        _public_scalar(item.get("id"), "", 64).upper()
        for item in manifest.get("milestones", [])
        if isinstance(item, Mapping)
    } if isinstance(manifest.get("milestones", []), list) else set()
    evidence_ids = {
        _public_scalar(item.get("id"), "", 64).upper()
        for item in manifest.get("evidence", [])
        if isinstance(item, Mapping)
    } if isinstance(manifest.get("evidence", []), list) else set()
    if manifest_error:
        diagnostics.append(
            Diagnostic("error", "MANIFEST_INVALID", "EFFORT.json is invalid or cannot be read safely.", path=_safe_relative(project_root, effort_dir / "EFFORT.json"))
        )
    elif manifest_present:
        version = manifest.get("schema_version")
        if version != SCHEMA_VERSION:
            diagnostics.append(
                Diagnostic("error", "SCHEMA_VERSION_UNSUPPORTED", f"EFFORT.json schema_version must be exactly {SCHEMA_VERSION}.", path=_safe_relative(project_root, effort_dir / "EFFORT.json"))
            )
        else:
            diagnostics.extend(_manifest_schema_diagnostics(manifest, _safe_relative(project_root, effort_dir / "EFFORT.json")))
            effort_meta = manifest.get("effort", {}) if isinstance(manifest.get("effort"), Mapping) else {}
            if _public_scalar(effort_meta.get("id"), "", 100) != effort_dir.name:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "EFFORT_ID_DIRECTORY_CONFLICT",
                        "EFFORT.json effort.id must exactly match its direct effort directory name.",
                        path=_safe_relative(project_root, effort_dir / "EFFORT.json"),
                    )
                )
    else:
        diagnostics.append(
            Diagnostic("warning", "LEGACY_V2", "No EFFORT.json manifest; state was derived from legacy V2 Markdown without mutation.", path=_safe_relative(project_root, effort_dir))
        )

    diagnostics.extend(_manifest_edge_input_diagnostics(manifest))

    for node_id, path in sorted(missing_manifest_paths):
        diagnostics.append(
            Diagnostic("error", "TICKET_MISSING", f"Manifest node {node_id} references a missing ticket.", node_id=node_id, path=path)
        )
    for node_id, path in sorted(invalid_ticket_metadata):
        diagnostics.append(
            Diagnostic("error", "TICKET_METADATA_INVALID", f"{node_id} contains duplicate or malformed metadata fields.", node_id=node_id, path=path)
        )
    for node_id in sorted(invalid_manifest_paths):
        diagnostics.append(Diagnostic("error", "TICKET_PATH_INVALID", f"Manifest node {node_id} must use its exact canonical kind/ID Markdown path.", node_id=node_id, path="[invalid indexed ticket path]"))
    for kind in sorted(invalid_manifest_ids):
        diagnostics.append(Diagnostic("error", "NODE_ID_INVALID", f"EFFORT.json contains a noncanonical {kind} ID; the entry was not read or exposed.", path="[invalid indexed node id]"))
    if manifest_present:
        for path in sorted(unindexed_tickets):
            diagnostics.append(Diagnostic("error", "TICKET_UNINDEXED", f"Ticket exists but is absent from EFFORT.json: {path}.", path=path))
    for path in sorted(unsafe_ticket_paths):
        diagnostics.append(Diagnostic("error", "TICKET_PATH_ESCAPE", f"Ticket path escapes the effort: {path}.", path=path))
    for directory_name in sorted(unsafe_ticket_directories):
        diagnostics.append(Diagnostic("error", "TICKET_DIRECTORY_ESCAPE", f"{directory_name}/ must be a real directory inside the effort; external entries were not enumerated.", path=f"[unsafe {directory_name} directory]"))
    for directory_name in sorted(set(unsafe_ticket_names)):
        diagnostics.append(Diagnostic("error", "TICKET_NAME_INVALID", f"{directory_name}/ contains a noncanonical ticket filename that was not read or exposed.", path="[invalid ticket filename]"))
    for directory_name in sorted(set(unsafe_ticket_ids)):
        diagnostics.append(Diagnostic("error", "TICKET_ID_INVALID", f"{directory_name}/ contains a ticket with a noncanonical declared ID; the ticket was not exposed.", path="[invalid declared ticket id]"))
    for directory_name in sorted(set(ticket_directory_limits)):
        diagnostics.append(Diagnostic("error", "TICKET_DIRECTORY_LIMIT", f"{directory_name}/ exceeds the {MAX_DIRECTORY_ENTRIES}-entry safety limit; no entries were read.", path=f"[oversized {directory_name} directory]"))

    for node_id, node in sorted(nodes.items()):
        if not NODE_ID.fullmatch(node_id):
            diagnostics.append(Diagnostic("error", "NODE_ID_INVALID", "A parsed node has a noncanonical ID.", path=node.path))
        allowed = GATE_STATUSES if node.kind == "gate" else DECISION_STATUSES
        if node.status not in allowed:
            diagnostics.append(
                Diagnostic("error", "STATUS_INVALID", f"{node_id} has an invalid status value for its artifact kind.", node_id=node_id, path=node.path)
            )
        if manifest:
            entry = manifest_entries.get(node_id, {})
            for field_name in ("responsible_party", "next_actor"):
                if _ambiguous_required(node.raw_fields.get(field_name)):
                    diagnostics.append(
                        Diagnostic("error", "ACCOUNTABILITY_FIELD_REQUIRED", f"{node_id} must name an explicit {field_name}.", node_id=node_id, path=node.path)
                    )
            if node.kind == "decision":
                raw_blocking = node.raw_fields.get("destination_blocking")
                if raw_blocking is None or raw_blocking.strip().lower() not in {"true", "false"}:
                    diagnostics.append(
                        Diagnostic("error", "DESTINATION_BLOCKING_REQUIRED", f"{node_id} must declare Destination blocking as exactly true or false.", node_id=node_id, path=node.path)
                    )
                if not isinstance(entry.get("destination_blocking"), bool):
                    diagnostics.append(
                        Diagnostic("error", "MANIFEST_BLOCKING_REQUIRED", f"Manifest decision {node_id} must index boolean destination_blocking.", node_id=node_id, path=node.path)
                    )
                if _ambiguous_required(node.raw_fields.get("decision_authority")):
                    diagnostics.append(
                        Diagnostic("error", "DECISION_AUTHORITY_REQUIRED", f"{node_id} must name an explicit decision authority.", node_id=node_id, path=node.path)
                    )
                if node.autonomy not in {"AFK", "HITL", "HYBRID"}:
                    diagnostics.append(
                        Diagnostic("error", "AUTONOMY_INVALID", f"{node_id} must use AFK, HITL, or HYBRID autonomy.", node_id=node_id, path=node.path)
                    )
                revision = node.raw_fields.get("revision", "")
                if not revision.isdigit() or int(revision) < 1:
                    diagnostics.append(
                        Diagnostic("error", "REVISION_REQUIRED", f"{node_id} must declare a positive integer Revision.", node_id=node_id, path=node.path)
                    )
                claim_fields = {
                    "claimed_by": node.raw_fields.get("claimed_by", ""),
                    "claimed_at": node.raw_fields.get("claimed_at", ""),
                    "claim_expires_at": node.raw_fields.get("claim_expires_at", ""),
                }
                if node.status == "CLAIMED":
                    for field_name, value in claim_fields.items():
                        if _ambiguous_required(value):
                            diagnostics.append(
                                Diagnostic("error", "CLAIM_FIELD_REQUIRED", f"{node_id} CLAIMED requires explicit {field_name}.", node_id=node_id, path=node.path)
                            )
                    claimed_at = _iso_datetime(claim_fields["claimed_at"])
                    expires_at = _iso_datetime(claim_fields["claim_expires_at"])
                    if not _ambiguous_required(claim_fields["claimed_at"]) and claimed_at is None:
                        diagnostics.append(Diagnostic("error", "CLAIM_TIMESTAMP_INVALID", f"{node_id} claimed_at must be a timezone-aware ISO timestamp.", node_id=node_id, path=node.path))
                    if not _ambiguous_required(claim_fields["claim_expires_at"]) and expires_at is None:
                        diagnostics.append(Diagnostic("error", "CLAIM_EXPIRY_INVALID", f"{node_id} claim_expires_at must be a timezone-aware ISO timestamp.", node_id=node_id, path=node.path))
                    if claimed_at and expires_at and expires_at <= claimed_at:
                        diagnostics.append(Diagnostic("error", "CLAIM_EXPIRY_ORDER", f"{node_id} claim expiry must be later than claimed_at.", node_id=node_id, path=node.path))
                    if expires_at and expires_at <= datetime.now(timezone.utc):
                        diagnostics.append(Diagnostic("warning", "CLAIM_STALE", f"{node_id} claim lease has expired and should be released or renewed.", node_id=node_id, path=node.path))
                else:
                    retained = [name for name, value in claim_fields.items() if not _ambiguous_required(value)]
                    if retained:
                        diagnostics.append(
                            Diagnostic("error", "CLAIM_RETAINED", f"{node_id} is not CLAIMED but retains active claim fields: {', '.join(retained)}.", node_id=node_id, path=node.path)
                        )
                if node.destination_blocking and node.terminal:
                    if _ambiguous_required(node.resolution):
                        diagnostics.append(Diagnostic("error", "DECISION_RESOLUTION_REQUIRED", f"{node_id} is terminal and blocking but has no substantive Resolution.", node_id=node_id, path=node.path))
                    if not node.evidence:
                        diagnostics.append(Diagnostic("error", "DECISION_EVIDENCE_REQUIRED", f"{node_id} is terminal and blocking but cites no E-NNN evidence.", node_id=node_id, path=node.path))
                    changed = (
                        _revision_number(node.raw_fields.get("revision", "")) not in {None, 1}
                        or any(
                            transition.get("from") == "RESOLVED" or transition.get("to") in {"REOPENED", "SUPERSEDED"}
                            for transition in node.transitions
                        )
                    )
                    expected_dependents = sorted(
                        set(node.dependents)
                        | {candidate.id for candidate in nodes.values() if node.id in candidate.requires}
                    )
                    if changed and expected_dependents:
                        grouped_receipts: dict[str, list[dict[str, str]]] = {}
                        for item in node.dependent_inspections:
                            grouped_receipts.setdefault(item["dependent"], []).append(item)
                        latest_transition = _latest_transition_time(node)
                        current_revision = _revision_number(node.raw_fields.get("revision", ""))
                        for dependent_id in expected_dependents:
                            candidates = grouped_receipts.get(dependent_id, [])
                            receipt = candidates[0] if len(candidates) == 1 else None
                            receipt_time = _iso_datetime(receipt["timestamp"]) if receipt else None
                            trigger = receipt["trigger"] if receipt else ""
                            revision_bound = bool(
                                current_revision is not None
                                and re.search(rf"\brevision\s*[:#]?\s*{current_revision}\b", trigger, flags=re.IGNORECASE)
                            )
                            receipt_valid = bool(
                                receipt
                                and receipt["outcome"] in {"STILL-VALID", "REOPENED", "SUPERSEDED"}
                                and EVIDENCE_ID.search(receipt["evidence"])
                                and not _ambiguous_required(receipt["actor"])
                                and receipt_time is not None
                                and receipt_time <= datetime.now(timezone.utc)
                                and (latest_transition is None or receipt_time >= latest_transition)
                                and not _ambiguous_required(receipt["trigger"])
                                and revision_bound
                            )
                            if not receipt_valid:
                                diagnostics.append(Diagnostic("error", "DEPENDENT_INSPECTION_REQUIRED", f"{node_id} needs a complete dependent-inspection receipt for {dependent_id}.", node_id=node_id, path=node.path))
            else:
                if node.status in {"OPEN", "CLAIMED", "RESOLVED", "REOPENED", "READY", "RUNNING"}:
                    diagnostics.append(
                        Diagnostic("error", "GATE_STATUS_LEGACY_IN_V3", f"{node_id} must use a legal V3 Gate status.", node_id=node_id, path=node.path)
                    )
                if _ambiguous_required(node.raw_fields.get("waiver_authority")):
                    diagnostics.append(
                        Diagnostic("error", "WAIVER_AUTHORITY_REQUIRED", f"{node_id} must name an explicit waiver authority.", node_id=node_id, path=node.path)
                    )
                raw_post_build = node.raw_fields.get("post_build", "").strip().lower()
                if raw_post_build != "true" or not node.post_build or entry.get("post_build") is not True:
                    diagnostics.append(Diagnostic("error", "GATE_POST_BUILD_REQUIRED", f"{node_id} must explicitly be post_build true in both its ticket and manifest index.", node_id=node_id, path=node.path))
                if node.phase != "p5-delivery" or entry.get("phase_id") != "p5-delivery":
                    diagnostics.append(Diagnostic("error", "GATE_PHASE_INVALID", f"{node_id} must use phase p5-delivery.", node_id=node_id, path=node.path))
                if node.status != "SUPERSEDED":
                    if _ambiguous_required(node.delivery_condition):
                        diagnostics.append(Diagnostic("error", "GATE_CONDITION_REQUIRED", f"{node_id} needs a substantive Delivery condition.", node_id=node_id, path=node.path))
                    actual_revision: int | None = None
                    if _ambiguous_required(node.raw_fields.get("subject_revision")):
                        diagnostics.append(Diagnostic("error", "GATE_SUBJECT_REVISION_REQUIRED", f"{node_id} needs a subject revision.", node_id=node_id, path=node.path))
                    else:
                        expected_revision = manifest.get("effort", {}).get("destination_revision") if isinstance(manifest.get("effort"), Mapping) else None
                        raw_subject_revision = node.raw_fields.get("subject_revision", "")
                        actual_revision = int(raw_subject_revision) if raw_subject_revision.isdigit() and int(raw_subject_revision) > 0 else None
                        if isinstance(expected_revision, int) and actual_revision != expected_revision:
                            diagnostics.append(Diagnostic("error", "GATE_SUBJECT_REVISION_STALE", f"{node_id} subject revision does not match the current destination revision.", node_id=node_id, path=node.path))
                    if _ambiguous_required(node.raw_fields.get("revalidate_when")):
                        diagnostics.append(Diagnostic("error", "GATE_FRESHNESS_REQUIRED", f"{node_id} needs a concrete Revalidate when rule.", node_id=node_id, path=node.path))
                    if not node.revalidates or any(target not in nodes or nodes[target].kind != "decision" for target in node.revalidates):
                        diagnostics.append(Diagnostic("error", "GATE_REVALIDATES_REQUIRED", f"{node_id} must revalidate at least one indexed Decision.", node_id=node_id, path=node.path))
                    if not node.gates or any(target not in milestone_ids for target in node.gates):
                        diagnostics.append(Diagnostic("error", "GATE_MILESTONE_LINK_REQUIRED", f"{node_id} must gate at least one declared milestone.", node_id=node_id, path=node.path))
                    if not node.checks:
                        diagnostics.append(Diagnostic("error", "GATE_CHECK_REQUIRED", f"{node_id} needs at least one concrete C-NNN Check.", node_id=node_id, path=node.path))
                    check_ids: set[str] = set()
                    for check in node.checks:
                        if not re.fullmatch(r"C-\d{3,}", check["id"]):
                            diagnostics.append(Diagnostic("error", "GATE_CHECK_ID_INVALID", f"{node_id} contains a noncanonical Check ID.", node_id=node_id, path=node.path))
                        elif check["id"] in check_ids:
                            diagnostics.append(Diagnostic("error", "GATE_CHECK_DUPLICATE", f"{node_id} repeats a canonical Check ID.", node_id=node_id, path=node.path))
                        check_ids.add(check["id"])
                        if check["method"] not in {"COMMAND", "PROBE", "REVIEW", "HUMAN-APPROVAL"}:
                            diagnostics.append(Diagnostic("error", "GATE_CHECK_METHOD_INVALID", f"{node_id} contains a Check with an invalid method.", node_id=node_id, path=node.path))
                        if _ambiguous_required(check["expected"]) or _ambiguous_required(check["evidence_required"]):
                            diagnostics.append(Diagnostic("error", "GATE_CHECK_INCOMPLETE", f"{node_id} contains a Check without a concrete expected result and evidence requirement.", node_id=node_id, path=node.path))
                        if check["status"] not in GATE_CHECK_STATUSES:
                            diagnostics.append(Diagnostic("error", "GATE_CHECK_STATUS_INVALID", f"{node_id} contains a Check with an invalid status.", node_id=node_id, path=node.path))

                    check_statuses = [check["status"] for check in node.checks]
                    if node.status == "PASSED" and (not check_statuses or any(value != "PASSED" for value in check_statuses)):
                        diagnostics.append(Diagnostic("error", "GATE_PASS_CHECK_CONFLICT", f"{node_id} may be PASSED only when every Check is PASSED.", node_id=node_id, path=node.path))
                    if node.status == "FAILED" and "FAILED" not in check_statuses:
                        diagnostics.append(Diagnostic("error", "GATE_FAILURE_CHECK_REQUIRED", f"{node_id} FAILED requires at least one FAILED Check.", node_id=node_id, path=node.path))
                    if node.status == "STALE" and "STALE" not in check_statuses:
                        diagnostics.append(Diagnostic("error", "GATE_STALE_CHECK_REQUIRED", f"{node_id} STALE requires at least one STALE Check.", node_id=node_id, path=node.path))

                    if node.status in {"PASSED", "FAILED", "STALE"}:
                        receipt = node.evaluation_receipts[0] if len(node.evaluation_receipts) == 1 else None
                        receipt_time = _iso_datetime(receipt["timestamp"]) if receipt else None
                        receipt_evidence = _extract_ids(receipt["evidence"], evidence=True) if receipt else []
                        final_transition = node.transitions[-1] if node.transitions else {}
                        final_evidence = _extract_ids(final_transition.get("evidence"), evidence=True)
                        receipt_valid = bool(
                            receipt
                            and not _ambiguous_required(receipt["actor"])
                            and receipt_time is not None
                            and receipt_time <= datetime.now(timezone.utc)
                            and receipt["outcome"] == node.status
                            and receipt_evidence
                            and receipt["subject_revision"].isdigit()
                            and int(receipt["subject_revision"]) == actual_revision
                            and not _ambiguous_required(receipt["rationale"])
                            and node.raw_fields.get("last_evaluated_at", "") == receipt["timestamp"]
                            and final_transition.get("to") == node.status
                            and _normalized_prose(str(final_transition.get("actor") or "")) == _normalized_prose(receipt["actor"])
                            and str(final_transition.get("timestamp") or "") == receipt["timestamp"]
                            and _normalized_prose(str(final_transition.get("reason") or "")) == _normalized_prose(receipt["rationale"])
                            and set(final_evidence) == set(receipt_evidence)
                        )
                        if not receipt_valid:
                            diagnostics.append(Diagnostic("error", "GATE_EVALUATION_RECEIPT_INVALID", f"{node_id} requires exactly one current evaluation receipt matching its final transition, evidence, outcome, and subject revision.", node_id=node_id, path=node.path))

                    if node.status == "WAIVED":
                        receipt = node.waiver_receipts[0] if len(node.waiver_receipts) == 1 else None
                        receipt_time = _iso_datetime(receipt["timestamp"]) if receipt else None
                        final_transition = node.transitions[-1] if node.transitions else {}
                        receipt_valid = bool(
                            receipt
                            and _non_agent_actor(receipt["actor"])
                            and _normalized_prose(receipt["authority"]) == _normalized_prose(node.waiver_authority)
                            and receipt_time is not None
                            and receipt_time <= datetime.now(timezone.utc)
                            and not _ambiguous_required(receipt["scope"])
                            and not _ambiguous_required(receipt["expiry"])
                            and not _ambiguous_required(receipt["rationale"])
                            and _normalized_prose(receipt["expiry"]) == _normalized_prose(node.raw_fields.get("revalidate_when", ""))
                            and final_transition.get("to") == "WAIVED"
                            and _normalized_prose(str(final_transition.get("actor") or "")) == _normalized_prose(receipt["actor"])
                            and str(final_transition.get("timestamp") or "") == receipt["timestamp"]
                            and _normalized_prose(str(final_transition.get("reason") or "")) == _normalized_prose(receipt["rationale"])
                        )
                        if not receipt_valid:
                            diagnostics.append(Diagnostic("error", "GATE_WAIVER_RECEIPT_INVALID", f"{node_id} WAIVED requires exactly one scoped, current, non-agent waiver receipt bound to its named authority and final transition.", node_id=node_id, path=node.path))
            diagnostics.extend(_transition_diagnostics(node, True))
        if node.phase not in PHASE_IDS:
            diagnostics.append(
                Diagnostic("warning", "PHASE_UNKNOWN", f"{node_id} references an unknown phase value.", node_id=node_id, path=node.path)
            )
        if not manifest_present and node.kind == "gate":
            diagnostics.append(
                Diagnostic("warning", "LEGACY_GATE_INFERRED", f"{node_id} was inferred to be a delivery gate; declare it explicitly in EFFORT.json.", node_id=node_id, path=node.path)
            )
        if node.status in (OPENISH_DECISION | OPENISH_GATE) and node.resolution:
            diagnostics.append(
                Diagnostic("warning", "PROVISIONAL_RESOLUTION", f"{node_id} is unresolved but has text in Resolution; move it to a hypothesis or recommendation field.", node_id=node_id, path=node.path)
            )
        for edge_type in ("requires", "revalidates", "informs", "gates"):
            for target in getattr(node, edge_type):
                if not manifest_present:
                    if target not in nodes and not (edge_type == "informs" and target.startswith("E-")):
                        diagnostics.append(Diagnostic("error", "REFERENCE_MISSING", f"{node_id} {edge_type} missing node {target}.", node_id=node_id, path=node.path))
                    continue
                legal = False
                if edge_type in {"requires", "revalidates"}:
                    legal = target in nodes and nodes[target].kind == "decision"
                elif edge_type == "informs":
                    legal = target in evidence_ids
                elif edge_type == "gates":
                    legal = node.kind == "gate" and target in milestone_ids
                if not legal:
                    known_target = target in nodes or target in evidence_ids or target in milestone_ids
                    code = "EDGE_KIND_INVALID" if known_target else "REFERENCE_MISSING"
                    diagnostics.append(Diagnostic("error", code, f"Illegal or missing {edge_type} relationship {node_id} -> {target}.", node_id=node_id, path=node.path))

    allowed_edge_types = {"requires", "revalidates", "informs", "gates"}
    seen_edges: set[tuple[str, str, str]] = set()
    for edge in manifest_edges:
        source = edge["source"]
        target = edge["target"]
        edge_type = edge["type"]
        identity = (source, edge_type, target)
        if identity in seen_edges:
            diagnostics.append(Diagnostic("error", "EDGE_DUPLICATE", f"Duplicate edge {source} {edge_type} {target}."))
        seen_edges.add(identity)
        if edge_type not in allowed_edge_types:
            diagnostics.append(Diagnostic("error", "EDGE_TYPE_INVALID", "EFFORT.json contains a typed edge with an invalid relationship type."))
            continue
        if source == target:
            diagnostics.append(Diagnostic("error", "EDGE_SELF_REFERENCE", f"Self-edge {source} {edge_type} {target} is forbidden.", node_id=source if source in nodes else None))
        source_node = nodes.get(source)
        target_node = nodes.get(target)
        legal_kind = (
            (edge_type == "requires" and source_node is not None and source_node.kind in {"decision", "gate"} and target_node is not None and target_node.kind == "decision")
            or (edge_type == "revalidates" and source_node is not None and source_node.kind in {"decision", "gate"} and target_node is not None and target_node.kind == "decision")
            or (edge_type == "informs" and source in evidence_ids and target_node is not None and target_node.kind in {"decision", "gate"})
            or (edge_type == "gates" and source_node is not None and source_node.kind == "gate" and target in milestone_ids)
        )
        if not legal_kind:
            diagnostics.append(Diagnostic("error", "EDGE_KIND_INVALID", f"Illegal typed edge {source} {edge_type} {target}.", node_id=source if source_node else None))

    indexed_edge_set = {
        (edge["source"], edge["type"], edge["target"])
        for edge in manifest_edges
    }
    canonical_edge_set = _ticket_edge_identities(nodes)
    if manifest_present and indexed_edge_set != canonical_edge_set:
        diagnostics.append(
            Diagnostic(
                "error",
                "EDGE_INDEX_MISMATCH",
                "EFFORT.json typed edges must exactly equal the canonical relationships declared by Markdown artifacts.",
                path=_safe_relative(project_root, effort_dir / "EFFORT.json"),
            )
        )

    for cycle in _requires_cycles(nodes):
        diagnostics.append(
            Diagnostic("error", "DEPENDENCY_CYCLE", "Requires cycle: " + " -> ".join(cycle) + ".", node_id=cycle[0])
        )

    # The V2 Frontier is derived state. Report drift, but never rewrite it.
    declared_frontier = set(_frontier_ids(map_text))
    if declared_frontier:
        computed = {
            node.id
            for node in nodes.values()
            if node.kind == "decision"
            and node.status in OPENISH_DECISION
            and all(nodes.get(req) is not None and nodes[req].terminal for req in node.requires)
        }
        for node_id in sorted(declared_frontier - computed):
            diagnostics.append(
                Diagnostic("warning", "FRONTIER_STALE", f"Legacy MAP.md lists {node_id} in Frontier, but it is not currently actionable.", node_id=node_id, path=_safe_relative(project_root, effort_dir / "MAP.md"))
            )
        for node_id in sorted(computed - declared_frontier):
            diagnostics.append(
                Diagnostic("warning", "FRONTIER_OMISSION", f"Legacy MAP.md omits actionable decision {node_id} from Frontier.", node_id=node_id, path=_safe_relative(project_root, effort_dir / "MAP.md"))
            )

    return sorted(diagnostics, key=lambda item: (item.severity != "error", item.code, item.node_id or "", item.message))


def _phase_payloads(
    manifest: Mapping[str, Any],
    current_phase_id: str,
    pre_spec_ready: bool,
    all_complete: bool,
    revalidation_due: bool = False,
) -> list[dict[str, Any]]:
    ids = [phase["id"] for phase in PHASES]
    current_index = ids.index(current_phase_id) if current_phase_id in ids else 1
    result: list[dict[str, Any]] = []
    manifest_phases = {
        item.get("id"): item
        for item in manifest.get("phases", [])
        if isinstance(item, Mapping) and item.get("id") in PHASE_IDS
    } if isinstance(manifest.get("phases"), list) else {}
    manifest_checkpoints = {
        item.get("id"): item
        for item in manifest.get("checkpoints", [])
        if isinstance(item, Mapping) and item.get("id") in CHECKPOINT_SCHEMA
    } if isinstance(manifest.get("checkpoints"), list) else {}
    for index, default in enumerate(PHASES):
        if index < current_index:
            state = "complete"
        elif index == current_index:
            state = "active"
        else:
            state = "upcoming"
        checkpoint_default = default["checkpoint"]
        manifest_phase = manifest_phases.get(default["id"], {})
        manifest_checkpoint = manifest_checkpoints.get(checkpoint_default["id"], {})
        phase_label = default["label"]
        phase_description = default["description"]
        checkpoint_label = checkpoint_default["label"]
        checkpoint_reason = checkpoint_default["reason"]
        if default["id"] == "p4-ready":
            if manifest_phase.get("label") == LEGACY_P4_CONTRACT["label"]:
                phase_label = LEGACY_P4_CONTRACT["label"]
            if manifest_phase.get("description") == LEGACY_P4_CONTRACT["description"]:
                phase_description = LEGACY_P4_CONTRACT["description"]
            if manifest_checkpoint.get("label") == LEGACY_P4_CONTRACT["checkpoint_label"]:
                checkpoint_label = LEGACY_P4_CONTRACT["checkpoint_label"]
            if manifest_checkpoint.get("reason") == LEGACY_P4_CONTRACT["reason"]:
                checkpoint_reason = LEGACY_P4_CONTRACT["reason"]
        if state == "complete":
            checkpoint_state = "complete"
        elif state == "upcoming":
            checkpoint_state = "upcoming"
        elif default["id"] == "p5-delivery" and not revalidation_due:
            checkpoint_state = "dormant"
        else:
            checkpoint_state = "due"
        checkpoint = {
            "id": checkpoint_default["id"],
            "label": checkpoint_label,
            "state": checkpoint_state,
            "recommended_run": bool(revalidation_due) if default["id"] == "p5-delivery" and state == "active" else bool(checkpoint_default["recommended_run"]),
            "reason": checkpoint_reason,
        }
        result.append(
            {
                "id": default["id"],
                "label": phase_label,
                "state": state,
                "description": phase_description,
                "checkpoint": checkpoint,
            }
        )
    return result


def _milestone_payloads(current_phase_id: str, full_exit_ready: bool, revalidation_due: bool) -> list[dict[str, Any]]:
    phase_ids = [phase["id"] for phase in PHASES]
    current_index = phase_ids.index(current_phase_id) if current_phase_id in PHASE_IDS else 1
    result: list[dict[str, Any]] = []
    for index, (milestone_id, (phase_id, label)) in enumerate(MILESTONE_SCHEMA.items()):
        if index < current_index:
            state = "complete"
        elif index > current_index:
            state = "pending"
        elif milestone_id == "M-004" and full_exit_ready:
            state = "complete"
        elif milestone_id == "M-005" and revalidation_due:
            state = "at-risk"
        else:
            state = "pending"
        result.append(
            {
                "id": milestone_id,
                "phase_id": phase_id,
                "label": label,
                "state": state,
                "criteria": MILESTONE_CRITERIA[milestone_id],
            }
        )
    return result


def _current_phase(
    manifest: Mapping[str, Any],
    unresolved_pre_spec: Sequence[Node],
    high_assumptions: Sequence[str],
    nodes: Iterable[Node],
) -> str:
    node_values = list(nodes)
    if any(
        node.kind == "gate" and node.status in {"FAILED", "STALE", "WAIVED"}
        for node in node_values
    ):
        return "p5-delivery"
    explicit = _public_scalar(manifest.get("current_phase_id"), "", 80)
    if explicit in PHASE_IDS:
        return explicit
    if unresolved_pre_spec:
        return "p2-resolve"
    if high_assumptions:
        return "p3-prove"
    if any(
        node.kind == "gate" and node.status in {"FAILED", "STALE", "WAIVED", "REOPENED", "EVALUATING", "RUNNING", "CLAIMED"}
        for node in node_values
    ):
        return "p5-delivery"
    return "p4-ready"


def _run_recommendation(
    diagnostics: Sequence[Diagnostic],
    actionable: Sequence[Node],
    waiting: Sequence[Node],
    blocked: Sequence[Node],
    claimed: Sequence[Node],
    revalidation_gates: Sequence[Node],
    phases: Sequence[Mapping[str, Any]],
    current_phase_id: str,
    pre_spec_ready: bool,
    full_exit_ready: bool,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    errors = [item for item in diagnostics if item.severity == "error"]
    structural_errors = [item for item in errors if not item.code.startswith("EXIT_")]
    checkpoint = next(phase["checkpoint"] for phase in phases if phase["id"] == current_phase_id)
    triggers: list[str] = [
        "destination or scope materially changes",
        "new evidence invalidates a decision or high-impact assumption",
        "a delivery gate fails, is waived, or becomes stale",
    ]

    if revalidation_gates and not structural_errors:
        return {
            "level": "now",
            "label": "Revalidate the affected route now",
            "reason": f"{len(revalidation_gates)} delivery gate(s) failed, were waived, or became stale; inspect only the decisions connected by revalidates edges.",
            "trigger": "failed, waived, or stale delivery gate",
            "checkpoint_id": "cp5-revalidate",
            "recommended": True,
            "triggers": triggers,
        }
    if errors:
        return {
            "level": "required",
            "label": "Repair the Wayfinder map now",
            "reason": f"Doctor found {len(errors)} structural error(s); route state is not trustworthy until they are fixed.",
            "trigger": "health diagnostic",
            "checkpoint_id": checkpoint["id"],
            "recommended": True,
            "triggers": triggers,
        }
    if actionable or claimed:
        count = len(actionable) + len(claimed)
        return {
            "level": "now",
            "label": "Continue this Wayfinder effort",
            "reason": f"{count} route decision(s) are actionable or already being worked.",
            "trigger": "unresolved route frontier",
            "checkpoint_id": checkpoint["id"],
            "recommended": True,
            "triggers": triggers,
        }
    if blocked:
        return {
            "level": "when-unblocked",
            "label": "Resume when the blocking input changes",
            "reason": f"{len(blocked)} route item(s) are blocked; unrelated work can continue, then Wayfinder should be resumed when an input arrives.",
            "trigger": "external blocker cleared",
            "checkpoint_id": checkpoint["id"],
            "recommended": False,
            "triggers": triggers,
        }
    if waiting and not pre_spec_ready:
        return {
            "level": "when-ready",
            "label": "Resume after prerequisites settle",
            "reason": f"{len(waiting)} route item(s) are waiting on prerequisites; rerun at the next dependency checkpoint.",
            "trigger": "prerequisite resolution",
            "checkpoint_id": checkpoint["id"],
            "recommended": False,
            "triggers": triggers,
        }
    if not pre_spec_ready and checkpoint["recommended_run"]:
        return {
            "level": "checkpoint",
            "label": f"Run the {checkpoint['label']}",
            "reason": checkpoint["reason"],
            "trigger": "lifecycle checkpoint due",
            "checkpoint_id": checkpoint["id"],
            "recommended": True,
            "triggers": triggers,
        }
    if pre_spec_ready and not full_exit_ready:
        return {
            "level": "checkpoint",
            "label": "Run the planning-exit handoff review",
            "reason": "Route decisions are settled. Run Wayfinder once at the Ready for execution checkpoint, then evaluate delivery gates during execution.",
            "trigger": "planning-exit handoff",
            "checkpoint_id": "cp4-handoff",
            "recommended": True,
            "triggers": triggers,
        }
    return {
        "level": "dormant",
        "label": "No rerun needed now",
        "reason": "The route is complete. Keep Wayfinder dormant unless a material change or failed, waived, or stale gate triggers targeted revalidation.",
        "trigger": "material change only",
        "checkpoint_id": "cp5-revalidate",
        "recommended": False,
        "triggers": triggers,
    }


def _build_state(root: Path, effort: str | Path | None = None) -> dict[str, Any]:
    """Build the complete deterministic dashboard/doctor payload."""
    project_root, effort_dir = resolve_effort(root, effort)
    map_path = effort_dir / "MAP.md"
    if (
        map_path.is_symlink()
        or not map_path.is_file()
        or not _within(effort_dir, map_path)
        or not _within(project_root, map_path)
    ):
        raise WayfinderError("MAP.md must be a real file inside the project-local effort directory.")
    try:
        map_text = _read_regular_text(map_path, "MAP.md")
    except (FileNotFoundError, OSError, UnicodeError, WayfinderError) as exc:
        raise WayfinderError("Cannot read the effort MAP.md safely.") from exc

    manifest_path = effort_dir / "EFFORT.json"
    manifest_present = manifest_path.exists() or manifest_path.is_symlink()
    if manifest_present and (
        manifest_path.is_symlink()
        or not _within(effort_dir, manifest_path)
        or not _within(project_root, manifest_path)
    ):
        manifest, manifest_error = {}, "path is a symbolic link or resolves outside the effort"
    else:
        manifest, manifest_error = _load_json(manifest_path)
    manifest_input_diagnostics: list[Diagnostic] = []
    blocked_manifest_inputs: set[str] = set()
    if manifest_error is None and manifest:
        manifest, manifest_input_diagnostics, blocked_manifest_inputs = _bounded_manifest_inputs(manifest)
    node_ref_diagnostics: list[Diagnostic] = []
    node_refs = _manifest_node_refs(manifest, node_ref_diagnostics)
    manifest_by_id: dict[str, dict[str, Any]] = {}
    referenced_paths: set[Path] = set()
    missing_manifest_paths: list[tuple[str, str]] = []
    invalid_ticket_metadata: list[tuple[str, str]] = []
    invalid_manifest_paths: list[str] = []
    invalid_manifest_ids: list[str] = []
    manifest_ticket_conflicts: list[tuple[str, str, str, str, str]] = []
    manifest_id_mismatches: list[tuple[str, str, str]] = []
    ticket_kind_mismatches: list[tuple[str, str, str, str]] = []
    nodes: dict[str, Node] = {}
    duplicate_ids: list[str] = []
    unindexed_tickets: list[str] = []
    unsafe_ticket_paths: list[str] = []
    unsafe_ticket_directories: list[str] = []
    unsafe_ticket_names: list[str] = []
    unsafe_ticket_ids: list[str] = []
    ticket_directory_limits: list[str] = []

    for kind, entry in node_refs:
        node_id = _public_scalar(entry.get("id"), "", 64).upper()
        if not node_id:
            continue
        expected_id = r"D-\d{3,}" if kind == "decision" else r"G-\d{3,}"
        if not re.fullmatch(expected_id, node_id):
            invalid_manifest_ids.append(kind)
            continue
        manifest_by_id[node_id] = entry
        canonical_path = _canonical_index_path(kind, node_id, entry.get("path"))
        if canonical_path is None:
            invalid_manifest_paths.append(node_id if NODE_ID.fullmatch(node_id) else "[invalid node id]")
            continue
        raw_path = canonical_path
        candidate = effort_dir / raw_path
        if candidate.is_symlink() or not _within(effort_dir, candidate) or not _within(project_root, candidate):
            missing_manifest_paths.append((node_id, "[unsafe indexed ticket path]"))
            continue
        referenced_paths.add(candidate.resolve(strict=False))
        if not candidate.is_file():
            missing_manifest_paths.append((node_id, _safe_relative(project_root, candidate)))
            nodes[node_id] = _placeholder_node(entry, project_root, effort_dir)
            continue
        try:
            node = parse_ticket(candidate, project_root, kind)
        except WayfinderError:
            invalid_ticket_metadata.append((node_id, _safe_relative(project_root, candidate)))
            nodes[node_id] = _placeholder_node(entry, project_root, effort_dir)
            continue
        except (OSError, UnicodeError):
            missing_manifest_paths.append((node_id, _safe_relative(project_root, candidate)))
            nodes[node_id] = _placeholder_node(entry, project_root, effort_dir)
            continue
        if node.id != node_id:
            declared_id = node.id if NODE_ID.fullmatch(node.id) else "[invalid ticket id]"
            manifest_id_mismatches.append((node_id, declared_id, node.path))
            node.id = node_id
        declared_kind = node.raw_fields.get("kind", "").strip().lower()
        if declared_kind and declared_kind != kind:
            ticket_kind_mismatches.append((node_id, declared_kind, kind, node.path))
            node.kind = kind
        for field_name, ticket_value, manifest_value in _manifest_ticket_conflicts(node, entry):
            manifest_ticket_conflicts.append((node_id, field_name, ticket_value, manifest_value, node.path))
        _merge_manifest_node(node, entry)
        if node_id in nodes:
            duplicate_ids.append(node_id)
        nodes[node_id] = node

    # Discover canonical Markdown not yet indexed. This preserves V2 behavior and
    # diagnoses partially migrated V3 efforts without mutating them.
    ticket_directories = () if "nodes" in blocked_manifest_inputs else (
        (effort_dir / "decisions", None),
        (effort_dir / "gates", "gate"),
    )
    for directory, kind_hint in ticket_directories:
        if directory.is_symlink() or not _within(effort_dir, directory) or not _within(project_root, directory):
            unsafe_ticket_directories.append(directory.name)
            continue
        if not directory.is_dir():
            continue
        try:
            directory_entries, limit_exceeded = _bounded_directory_entries(directory)
        except WayfinderError:
            unsafe_ticket_directories.append(directory.name)
            continue
        if limit_exceeded:
            ticket_directory_limits.append(directory.name)
            continue
        for path in directory_entries:
            if path.suffix.lower() != ".md":
                continue
            expected_prefix = "G" if kind_hint == "gate" else "D"
            if not re.fullmatch(rf"{expected_prefix}-\d{{3,}}\.md", path.name):
                unsafe_ticket_names.append(directory.name)
                continue
            if path.is_symlink() or not _within(effort_dir, path) or not _within(project_root, path):
                unsafe_ticket_paths.append(path.name)
                continue
            resolved = path.resolve(strict=False)
            if resolved in referenced_paths:
                continue
            if manifest:
                unindexed_tickets.append(_safe_relative(project_root, path))
            try:
                node = parse_ticket(path, project_root, kind_hint)
            except (OSError, UnicodeError, WayfinderError):
                continue
            if not re.fullmatch(rf"{expected_prefix}-\d{{3,}}", node.id):
                unsafe_ticket_ids.append(directory.name)
                continue
            expected_kind = "gate" if kind_hint == "gate" else "decision"
            declared_kind = node.raw_fields.get("kind", "").strip().lower()
            if declared_kind and declared_kind != expected_kind:
                ticket_kind_mismatches.append((node.id, declared_kind, expected_kind, node.path))
                node.kind = expected_kind
            if node.id in nodes:
                duplicate_ids.append(node.id)
                continue
            entry = manifest_by_id.get(node.id)
            if entry:
                _merge_manifest_node(node, entry)
            nodes[node.id] = node

    manifest_edges = _manifest_edges(manifest)
    relationship_diagnostics = _bound_node_relationships(nodes)

    # Generate reverse unlocks from canonical requires edges. Explicit unlocks are
    # preserved, then deduplicated.
    for node in nodes.values():
        for required in node.requires:
            if required in nodes:
                nodes[required].unlocks.append(node.id)
    for node in nodes.values():
        node.requires = sorted(set(node.requires))
        node.revalidates = sorted(set(node.revalidates))
        node.informs = sorted(set(node.informs))
        node.gates = sorted(set(node.gates))
        node.unlocks = sorted(set(node.unlocks))
    relationship_diagnostics.extend(_bound_node_relationships(nodes))
    public_edges = _all_edges(nodes, [])
    if len(public_edges) > MAX_PUBLIC_EDGES:
        public_edges = []
        relationship_diagnostics.append(
            Diagnostic(
                "error",
                "PUBLIC_EDGE_LIMIT",
                "The canonical typed-edge graph exceeds the public-state safety limit; public edges were omitted.",
            )
        )

    diagnostics = _validate(
        nodes,
        project_root,
        effort_dir,
        manifest,
        manifest_present,
        manifest_error,
        map_text,
        missing_manifest_paths,
        invalid_ticket_metadata,
        invalid_manifest_paths,
        invalid_manifest_ids,
        manifest_edges,
        unindexed_tickets,
        unsafe_ticket_paths,
        unsafe_ticket_directories,
        unsafe_ticket_names,
        unsafe_ticket_ids,
        ticket_directory_limits,
    )
    diagnostics.extend(manifest_input_diagnostics)
    diagnostics.extend(node_ref_diagnostics)
    diagnostics.extend(relationship_diagnostics)
    if manifest.get("schema_version") == SCHEMA_VERSION:
        diagnostics.extend(_map_framing_audit(map_text, manifest, project_root, map_path))
    for node_id in sorted(set(duplicate_ids)):
        diagnostics.append(Diagnostic("error", "NODE_DUPLICATE", f"Duplicate node ID {node_id}.", node_id=node_id))
    for manifest_id, _ticket_id, path in sorted(manifest_id_mismatches):
        diagnostics.append(
            Diagnostic(
                "error",
                "MANIFEST_TICKET_ID_CONFLICT",
                f"{manifest_id} declares an ID that conflicts with its canonical manifest index.",
                node_id=manifest_id,
                path=path,
            )
        )
    for node_id, declared_kind, expected_kind, path in sorted(ticket_kind_mismatches):
        diagnostics.append(
            Diagnostic(
                "error",
                "TICKET_KIND_CONFLICT",
                f"{node_id} declared Kind conflicts with its canonical directory and manifest kind.",
                node_id=node_id,
                path=path,
            )
        )
    for node_id, field_name, ticket_value, manifest_value, path in sorted(manifest_ticket_conflicts):
        public_field = field_name.replace("_", " ") if field_name in {
            "title", "question", "kind", "status", "autonomy", "responsible_party",
            "next_actor", "decision_authority", "waiver_authority", "phase", "summary",
            "recommendation", "consequence_of_waiting", "destination_blocking", "post_build",
            "requires", "revalidates", "informs", "gates", "dependents", "unlocks",
            "evidence", "type", "revision", "subject_revision", "revalidate_when",
            "claimed_by", "claimed_at", "claim_expires_at", "last_evaluated_at",
        } else "metadata"
        diagnostics.append(
            Diagnostic(
                "error",
                "MANIFEST_TICKET_CONFLICT",
                f"{node_id} {public_field} differs between its Markdown artifact and manifest index.",
                node_id=node_id,
                path=path,
            )
        )
    high_assumptions, assumption_diagnostics, settled_assumptions, assumption_evidence, nonblocking_assumptions, public_assumptions = _assumption_audit(
        effort_dir, project_root, manifest_present, nodes
    )
    diagnostics.extend(assumption_diagnostics)
    active_invariants, invariant_diagnostics, invariant_evidence, public_invariants = _invariant_audit(
        effort_dir, project_root, manifest_present
    )
    diagnostics.extend(invariant_diagnostics)
    evidence_diagnostics, public_evidence = _evidence_audit(
        effort_dir,
        project_root,
        manifest,
        nodes,
        assumption_evidence | invariant_evidence,
        input_blocked="evidence" in blocked_manifest_inputs,
    )
    diagnostics.extend(evidence_diagnostics)
    intake_payload, intake_diagnostics = _intake_public_state(effort_dir, project_root, manifest)
    diagnostics.extend(intake_diagnostics)
    activity, activity_diagnostics = _activity_summaries(manifest)
    diagnostics.extend(activity_diagnostics)
    diagnostics.sort(key=lambda item: (item.severity != "error", item.code, item.node_id or "", item.message))

    unresolved_pre_spec = [
        node
        for node in nodes.values()
        if node.kind == "decision" and node.destination_blocking and not node.terminal
    ]
    fog = _unformulated_fog(map_text)
    current_errors = [item for item in diagnostics if item.severity == "error"]
    if (
        not manifest_present
        and not unresolved_pre_spec
        and not high_assumptions
        and not fog
        and not current_errors
    ):
        diagnostics.append(
            Diagnostic(
                "error",
                "MIGRATION_REQUIRED_FOR_COMPLETION",
                "Legacy V2 route detail is inspectable, but completion requires migration to a valid schema-3 EFFORT.json proof contract.",
                path=_safe_relative(project_root, effort_dir),
            )
        )
        diagnostics.sort(key=lambda item: (item.severity != "error", item.code, item.node_id or "", item.message))
    route_errors = [item for item in diagnostics if item.severity == "error"]

    current_phase_id = _current_phase(manifest, unresolved_pre_spec, high_assumptions, nodes.values())
    actionable: list[Node] = []
    waiting: list[Node] = []
    blocked: list[Node] = []
    claimed: list[Node] = []
    delivery_gates: list[Node] = []
    revalidation_gates: list[Node] = []
    all_delivery_gates = [
        node for node in sorted(nodes.values(), key=lambda item: item.id) if node.kind == "gate" and node.post_build
    ]
    revalidation_gates = [
        node for node in all_delivery_gates if node.status in {"FAILED", "STALE", "WAIVED"}
    ]
    for node in sorted(nodes.values(), key=lambda item: item.id):
        if node.terminal:
            continue
        if node.kind == "gate" and node.post_build:
            delivery_gates.append(node)
            continue
        if node.status in CLAIMED_STATUSES:
            claimed.append(node)
            continue
        if node.status in BLOCKED_STATUSES:
            blocked.append(node)
            continue
        openish = OPENISH_GATE if node.kind == "gate" else OPENISH_DECISION
        if node.status not in openish:
            waiting.append(node)
            node.waiting_reason = "current status is not actionable"
            continue
        unsettled = [required for required in node.requires if required not in nodes or not nodes[required].terminal]
        if unsettled:
            node.waiting_reason = "waiting for " + ", ".join(unsettled)
            waiting.append(node)
            continue
        actionable.append(node)

    valid_v3_manifest = manifest_present and manifest_error is None and manifest.get("schema_version") == SCHEMA_VERSION
    intake_started = intake_payload.get("state") == "AVAILABLE"
    intake_complete = intake_payload.get("status") == "COMPLETE"
    intake_readiness = intake_payload.get("readiness") if isinstance(intake_payload.get("readiness"), Mapping) else {}
    intake_blocking_questions = sorted(
        item
        for item in intake_readiness.get("blocking_questions", [])
        if isinstance(item, str) and re.fullmatch(r"Q-(?:\d{3}|[A-Z]{2,4}-\d{3})", item)
    ) if isinstance(intake_readiness.get("blocking_questions"), list) else []
    intake_ready_for_exit = not intake_started or (
        intake_complete and intake_readiness.get("exit_ready") is True
    )
    pre_spec_ready = valid_v3_manifest and intake_ready_for_exit and not unresolved_pre_spec and not high_assumptions and not fog and not route_errors
    effort_meta = manifest.get("effort", {}) if isinstance(manifest.get("effort", {}), Mapping) else {}
    remaining_nonblocking = sorted(
        [
            node.id
            for node in nodes.values()
            if node.kind == "decision" and not node.destination_blocking and not node.terminal
        ]
        + nonblocking_assumptions
        + intake_blocking_questions
    )
    exit_receipt, exit_diagnostics = _exit_audit(
        effort_dir,
        project_root,
        manifest,
        nodes,
        settled_assumptions,
        active_invariants,
        remaining_nonblocking,
        intake_payload,
    )
    if exit_receipt["valid"] and not pre_spec_ready:
        exit_diagnostics.append(Diagnostic("error", "EXIT_PREMATURE", "EXIT.md is current-looking, but the route proof does not pass.", path=exit_receipt["path"]))
        exit_receipt["status"] = "invalid"
        exit_receipt["valid"] = False
    diagnostics.extend(exit_diagnostics)
    diagnostics.sort(key=lambda item: (item.severity != "error", item.code, item.node_id or "", item.message))
    errors = [item for item in diagnostics if item.severity == "error"]
    full_exit_ready = pre_spec_ready and bool(exit_receipt["valid"])
    deferred: list[Node] = []
    if full_exit_ready:
        for collection in (actionable, waiting, blocked, claimed):
            retained: list[Node] = []
            for node in collection:
                if node.kind == "decision" and not node.destination_blocking:
                    deferred.append(node)
                else:
                    retained.append(node)
            collection[:] = retained
        deferred = sorted({node.id: node for node in deferred}.values(), key=lambda item: item.id)
    phase_payloads = _phase_payloads(
        manifest,
        current_phase_id,
        pre_spec_ready,
        full_exit_ready,
        revalidation_due=bool(revalidation_gates),
    )
    milestone_payloads = _milestone_payloads(current_phase_id, full_exit_ready, bool(revalidation_gates))
    recommendation = _run_recommendation(
        diagnostics,
        actionable,
        waiting,
        blocked,
        claimed,
        revalidation_gates,
        phase_payloads,
        current_phase_id,
        pre_spec_ready,
        full_exit_ready,
        manifest,
    )

    title = _public_scalar(effort_meta.get("title"), _map_title(map_text, effort_dir.name), 300)
    destination = _public_scalar(effort_meta.get("destination"), _destination(map_text), 4_000)
    if errors:
        project_status = "needs-attention"
    elif revalidation_gates:
        project_status = "revalidation-due"
    elif full_exit_ready:
        project_status = "complete"
    elif pre_spec_ready:
        project_status = "ready-for-execution"
    else:
        project_status = "active"
    source_paths = [
        path
        for path in (map_path, manifest_path, effort_dir / "ASSUMPTIONS.md", effort_dir / "INVARIANTS.md")
        if not path.is_symlink() and _within(effort_dir, path) and _within(project_root, path)
    ]
    source_paths.extend(project_root / node.path for node in nodes.values())
    raw_updated_at = _public_scalar(effort_meta.get("updated_at"), "", 64)
    parsed_updated_at = _iso_datetime(raw_updated_at)
    last_updated = (
        parsed_updated_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if parsed_updated_at is not None
        else (_last_updated(source_paths) or "")
    )
    current_phase = next(phase for phase in phase_payloads if phase["id"] == current_phase_id)
    current_checkpoint = dict(current_phase["checkpoint"])
    current_checkpoint["phase_id"] = current_phase_id

    payload_diagnostics = list(diagnostics)
    if len(payload_diagnostics) > MAX_DIAGNOSTICS:
        omitted = payload_diagnostics[MAX_DIAGNOSTICS - 1 :]
        severity = "error" if any(item.severity == "error" for item in omitted) else "warning"
        payload_diagnostics = payload_diagnostics[: MAX_DIAGNOSTICS - 1] + [
            Diagnostic(
                severity,
                "DIAGNOSTIC_LIMIT",
                f"Additional diagnostics were omitted after the deterministic {MAX_DIAGNOSTICS}-item output limit.",
            )
        ]
    issue_payloads = [item.payload() for item in payload_diagnostics if item.severity == "error"]
    warning_payloads = [item.payload() for item in payload_diagnostics if item.severity == "warning"]
    counts = {
        "resolved": sum(1 for node in nodes.values() if node.terminal),
        "actionable": len(actionable),
        "waiting": len(waiting),
        "blocked": len(blocked),
        "claimed": len(claimed),
        "deferred": len(deferred),
        "gates": sum(1 for node in nodes.values() if node.kind == "gate"),
        "delivery_gates_pending": len(delivery_gates),
        "delivery_gates_revalidation": len(revalidation_gates),
        "decisions": sum(1 for node in nodes.values() if node.kind == "decision"),
        "total": len(nodes),
    }
    if not manifest_present:
        manifest_contract_state = "absent-legacy-v2"
    elif manifest_error is not None:
        manifest_contract_state = "invalid-manifest"
    elif manifest.get("schema_version") != SCHEMA_VERSION:
        manifest_contract_state = "unsupported-schema"
    else:
        manifest_contract_state = "schema-3"
    primary_domain = None
    intake_domain = intake_payload.get("domain")
    if isinstance(intake_domain, Mapping):
        candidate_domain = intake_domain.get("primary_domain") or intake_domain.get("selected")
        if candidate_domain in {"SOFTWARE", "GENERAL_PROJECT", "FINANCE_REPORTING", "OTHER"}:
            primary_domain = candidate_domain
    handoffs = {
        "SOFTWARE": "specification, tickets, and build planning",
        "GENERAL_PROJECT": "work breakdown, schedule, and delivery controls",
        "FINANCE_REPORTING": "reporting procedure, control, and review execution",
        "OTHER": "execution planning and controls",
    }
    execution_handoff = handoffs.get(primary_domain, "execution planning and controls")
    try:
        manifest_hash = hashlib.sha256(_read_regular_bytes(manifest_path, "EFFORT.json")).hexdigest() if valid_v3_manifest else ""
    except (OSError, WayfinderError):
        manifest_hash = ""
    applicable_decisions = [
        {
            "id": node.id,
            "revision": _revision_number(node.raw_fields.get("revision", "")),
            "status": node.status if node.status in DECISION_STATUSES else "INVALID",
        }
        for node in _applicable_decision_nodes(nodes, intake_payload)
    ]
    implementation_baseline = {
        "effort_id": _public_scalar(effort_meta.get("id"), "", 100),
        "manifest_hash": manifest_hash,
        "destination_revision": effort_meta.get("destination_revision") if isinstance(effort_meta.get("destination_revision"), int) and not isinstance(effort_meta.get("destination_revision"), bool) else None,
        "intake_revision": intake_payload.get("revision") if isinstance(intake_payload.get("revision"), int) else 0,
        "primary_domain": primary_domain,
        "applicable_decisions": applicable_decisions,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_contract": {
            "state": manifest_contract_state,
            "schema3_present": manifest_contract_state == "schema-3",
            "doctor_passed": not errors,
            "lifecycle_ready": manifest_contract_state == "schema-3" and not errors,
            "intake_ready_for_exit": intake_ready_for_exit,
        },
        "project": {
            "title": title,
            "slug": effort_dir.name,
            "status": project_status,
            "destination": destination,
            "local": True,
            "read_only": True,
            "last_updated": last_updated,
        },
        "intake": intake_payload,
        "implementation_baseline": implementation_baseline,
        "phases": phase_payloads,
        "milestones": milestone_payloads,
        "current_phase": current_phase,
        "current_checkpoint": current_checkpoint,
        "run_recommendation": recommendation,
        "counts": counts,
        "evidence": public_evidence,
        "assumptions": public_assumptions,
        "invariants": public_invariants,
        "nodes": [node.payload() for node in sorted(nodes.values(), key=lambda item: item.id)],
        "edges": public_edges,
        "views": {
            "actionable": [node.id for node in actionable],
            "waiting": [node.id for node in waiting],
            "blocked": [node.id for node in blocked],
            "claimed": [node.id for node in claimed],
            "deferred": [node.id for node in deferred],
            "delivery_gates": [node.id for node in all_delivery_gates],
            "revalidation": [node.id for node in revalidation_gates],
        },
        "activity": activity,
        "health": {
            "status": "needs-attention" if issue_payloads else ("warnings" if warning_payloads else "healthy"),
            "issues": issue_payloads,
            "warnings": warning_payloads,
        },
        "diagnostics": [item.payload() for item in payload_diagnostics],
        "exit": {
            "pre_spec_ready": pre_spec_ready,
            "planning_exit_ready": pre_spec_ready,
            "complete": full_exit_ready,
            "receipt": exit_receipt,
            "unresolved_destination_decisions": sorted(node.id for node in unresolved_pre_spec),
            "pending_delivery_gates": sorted(node.id for node in nodes.values() if node.kind == "gate" and not node.terminal),
            "high_impact_open_assumptions": high_assumptions,
            "unformulated_fog": fog,
            "remaining_nonblocking_unknowns": remaining_nonblocking,
            "execution_handoff": execution_handoff,
            "implementation_baseline": implementation_baseline,
        },
    }


def build_state(root: Path, effort: str | Path | None = None) -> dict[str, Any]:
    """Build state within an aggregate read budget, safe for concurrent callers."""
    token = _BUILD_READ_BUDGET.set(_BuildReadBudget(MAX_BUILD_READ_BYTES))
    try:
        return _build_state(root, effort)
    finally:
        _BUILD_READ_BUDGET.reset(token)


def state_json(state: Mapping[str, Any]) -> str:
    """Return stable JSON suitable for snapshot tests and the dashboard API."""
    return json.dumps(state, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"


def status_text(state: Mapping[str, Any]) -> str:
    project = state["project"]
    counts = state["counts"]
    recommendation = state["run_recommendation"]
    phase = state["current_phase"]
    health = state["health"]
    lines = [
        f"Wayfinder: {project['title']}",
        f"State: {project['status']} | Health: {health['status']}",
        f"Phase: {phase['label']} ({phase['id']})",
        (
            "Nodes: "
            f"{counts['resolved']} resolved, {counts['actionable']} actionable, "
            f"{counts['waiting']} waiting, {counts['blocked']} blocked, "
            f"{counts['claimed']} claimed, {counts['gates']} gates"
        ),
        f"Recommendation: {recommendation['label']}",
        f"Why: {recommendation['reason']}",
    ]
    if health["issues"]:
        lines.append(f"Doctor: {len(health['issues'])} error(s), {len(health['warnings'])} warning(s)")
    elif health["warnings"]:
        lines.append(f"Doctor: {len(health['warnings'])} warning(s)")
    else:
        lines.append("Doctor: healthy")
    return "\n".join(lines) + "\n"
