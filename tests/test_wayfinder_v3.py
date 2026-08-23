from __future__ import annotations

from contextlib import redirect_stdout
import http.client
import hashlib
import io
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "wayfinder" / "scripts"
CLI = SCRIPTS / "wayfinder.py"


def load_local(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


STATE_MODULE = load_local("_wayfinder_state_v3", SCRIPTS / "wayfinder_state.py")
SERVER_MODULE = load_local("_wayfinder_server_v3", SCRIPTS / "wayfinder_server.py")
INIT_MODULE = load_local("_wayfinder_init_failure_v3", SCRIPTS / "init_wayfinder.py")
CLI_MODULE = load_local("_wayfinder_cli_v3", CLI)
build_state = STATE_MODULE.build_state
make_server = SERVER_MODULE.make_server


def project_byte_snapshot(root: Path) -> dict[str, bytes]:
    """Capture every project file byte-for-byte for read-only CLI assertions."""
    snapshot: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = b"SYMLINK\0" + os.fsencode(os.readlink(path))
        elif path.is_file():
            snapshot[relative] = path.read_bytes()
    return snapshot


def ticket(
    node_id: str,
    title: str,
    status: str,
    prerequisites: str = "None",
    *,
    kind: str | None = None,
    post_build: bool | None = None,
    v3: bool = True,
) -> str:
    if not v3:
        return (
            f"# {node_id} — {title}\n\n"
            f"- **Question:** What should happen for {title.lower()}?\n"
            "- **Type:** ANALYSIS\n"
            "- **Owner:** HYBRID\n"
            f"- **Status:** {status}\n"
            f"- **Prerequisites:** {prerequisites}\n"
            "- **Dependents:** None\n"
            "- **Blocks / affects:** Example destination\n"
            "- **Evidence:** None\n"
            "- **Resolution:**\n"
            "- **Invalidation rule:** Reopen when evidence changes.\n"
        )
    node_kind = (kind or "DECISION").upper()
    is_gate = node_kind == "GATE"
    terminal_decision = not is_gate and status in {"RESOLVED", "SUPERSEDED"}
    effective_post_build = post_build if post_build is not None else is_gate
    lines = [
        f"# {node_id} — {title}",
        "",
        f"- **Question:** What should happen for {title.lower()}?",
        f"- **Kind:** {node_kind}",
        f"- **Phase:** {'p5-delivery' if is_gate else 'p2-resolve'}",
        *([f"- **Post build:** {'true' if effective_post_build else 'false'}"] if is_gate or post_build is not None else []),
        "- **Type:** ANALYSIS",
        "- **Autonomy:** HYBRID",
        "- **Responsible party:** Example owner",
        *( ["- **Waiver authority:** Release owner"] if is_gate else ["- **Decision authority:** Example owner"] ),
        "- **Next actor:** Codex",
        f"- **Status:** {status}",
        "- **Destination blocking:** true",
        f"- **Requires:** {prerequisites}",
        *( ["- **Revalidates:** D-001", "- **Informs:** E-001", "- **Gates:** M-005"] if is_gate else ["- **Revalidates:** none", "- **Informs:** E-001"] ),
        "- **Dependents:** None",
        "- **Blocks / affects:** Example destination",
        "- **Evidence:** E-001",
        "- **Claimed by:** none",
        "- **Claimed at:** none",
        "- **Claim expires at:** none",
        "- **Revision:** 1",
        f"- **Resolution:** {'Chosen verified route.' if terminal_decision else ''}",
        "- **Invalidation rule:** Reopen when evidence changes.",
    ]
    if is_gate:
        lines.extend(
            [
                "- **Subject revision:** 1",
                "- **Defined at:** 2026-08-22T00:00:00Z",
                f"- **Last evaluated at:** {'2026-08-22T00:00:00Z' if status in {'PASSED', 'FAILED', 'STALE'} else 'never'}",
                "- **Revalidate when:** Destination revision changes.",
                "",
                "## Delivery condition",
                "",
                "The exact release remains within the measured load budget.",
                "",
                "## Checks",
                "",
                "| ID | Method | Expected result | Evidence required | Status |",
                "| --- | --- | --- | --- | --- |",
                f"| C-001 | COMMAND | The load probe passes. | Saved E-001 result. | {status if status in {'PASSED', 'FAILED', 'STALE'} else 'PENDING'} |",
            ]
        )
        if status in {"PASSED", "FAILED", "STALE"}:
            lines.extend(
                [
                    "",
                    "## Evaluation receipt",
                    "",
                    "| Evaluated by | Timestamp | Outcome | Evidence | Subject revision | Rationale |",
                    "| --- | --- | --- | --- | --- | --- |",
                    f"| Example | 2026-08-22T00:00:00Z | {status} | E-001 | 1 | Gate evaluation recorded. |",
                ]
            )
        if status == "WAIVED":
            lines.extend(
                [
                    "",
                    "## Waiver receipt",
                    "",
                    "| Waived by | Authority source | Timestamp | Scope | Expiry / revalidate when | Rationale |",
                    "| --- | --- | --- | --- | --- | --- |",
                    "| Release owner | Release owner | 2026-08-22T00:00:00Z | This delivery gate only. | Destination revision changes. | Approved scoped waiver. |",
                ]
            )
    paths = {
        "decision": {
            "OPEN": [(None, "OPEN")],
            "CLAIMED": [(None, "OPEN"), ("OPEN", "CLAIMED")],
            "BLOCKED": [(None, "OPEN"), ("OPEN", "BLOCKED")],
            "RESOLVED": [(None, "OPEN"), ("OPEN", "RESOLVED")],
            "REOPENED": [(None, "OPEN"), ("OPEN", "RESOLVED"), ("RESOLVED", "REOPENED")],
            "SUPERSEDED": [(None, "OPEN"), ("OPEN", "SUPERSEDED")],
        },
        "gate": {
            "DEFINED": [(None, "DEFINED")],
            "PENDING": [(None, "DEFINED"), ("DEFINED", "PENDING")],
            "EVALUATING": [(None, "DEFINED"), ("DEFINED", "PENDING"), ("PENDING", "EVALUATING")],
            "PASSED": [(None, "DEFINED"), ("DEFINED", "PENDING"), ("PENDING", "EVALUATING"), ("EVALUATING", "PASSED")],
            "FAILED": [(None, "DEFINED"), ("DEFINED", "PENDING"), ("PENDING", "EVALUATING"), ("EVALUATING", "FAILED")],
            "STALE": [(None, "DEFINED"), ("DEFINED", "PENDING"), ("PENDING", "EVALUATING"), ("EVALUATING", "PASSED"), ("PASSED", "STALE")],
            "WAIVED": [(None, "DEFINED"), ("DEFINED", "PENDING"), ("PENDING", "WAIVED")],
        },
    }
    history = paths["gate" if is_gate else "decision"].get(status, [])
    lines.extend(
        [
            "",
            "## Append-only transition history",
            "",
            "| From | To | Actor | Timestamp | Reason | Evidence |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for before, after in history:
        transition_evidence = "E-001" if (
            (not is_gate and after in {"RESOLVED", "SUPERSEDED"})
            or (is_gate and after in {"PASSED", "FAILED", "STALE"})
        ) else "none"
        transition_actor = "Release owner" if after == "WAIVED" else "Example"
        transition_reason = (
            "Approved scoped waiver."
            if after == "WAIVED"
            else ("Gate evaluation recorded." if is_gate and after in {"PASSED", "FAILED", "STALE"} else "Fixture")
        )
        lines.append(f"| {before or '—'} | {after} | {transition_actor} | 2026-08-22T00:00:00Z | {transition_reason} | {transition_evidence} |")
    return "\n".join(lines) + "\n"


def create_effort(repo: Path, slug: str = "launch") -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, timeout=5)
    effort = repo / ".codex" / "wayfinder" / "efforts" / slug
    (effort / "decisions").mkdir(parents=True)
    (effort / "gates").mkdir()
    (effort / "evidence").mkdir()
    pointer = f".codex/wayfinder/efforts/{slug}/MAP.md"
    (repo / ".codex" / "wayfinder" / "ACTIVE").write_text(pointer + "\n", encoding="utf-8")
    (effort / "MAP.md").write_text(
        "# Wayfinder Map: Example Launch\n\n"
        "## Destination\n\nA safe example reaches production.\n\n"
        "## Success conditions\n\n"
        "| ID | Observable condition | Evidence required | Status |\n"
        "| --- | --- | --- | --- |\n"
        "| SC-001 | Example succeeds. | E-001 route observation. | OPEN |\n\n"
        "## Constraints and authority boundaries\n\n- No external writes.\n\n"
        "## Explicit out of scope\n\n- Automatic deployment.\n\n"
        "## Frontier\n\n"
        "## Fog / Not Yet Specified\n\n"
        "## Decision graph\n\n"
        "## Recent invalidations\n\n- None.\n\n"
        "## Exit gate\n\n- [ ] Route complete.\n",
        encoding="utf-8",
    )
    (effort / "ASSUMPTIONS.md").write_text("# Assumptions\n", encoding="utf-8")
    (effort / "INVARIANTS.md").write_text("# Invariants\n", encoding="utf-8")
    return effort


def write_manifest(
    effort: Path,
    decisions: list[dict],
    gates: list[dict] | None = None,
    edges: list[dict] | None = None,
    current_phase_id: str = "p2-resolve",
) -> None:
    decisions = [{"destination_blocking": True, **entry} for entry in decisions]
    gates = [{"destination_blocking": True, **entry} for entry in (gates or [])]
    manifest = json.loads((ROOT / "skills" / "wayfinder" / "assets" / "EFFORT.json").read_text(encoding="utf-8"))
    manifest["effort"] = {
        "id": effort.name,
        "title": "Example Launch",
        "destination": "A safe example reaches production.",
        "destination_revision": 1,
        "state": "ACTIVE",
        "created_at": "2026-08-22T00:00:00Z",
        "updated_at": "2026-08-22T00:00:00Z",
    }
    manifest["current_phase_id"] = current_phase_id
    phase_ids = [phase["id"] for phase in manifest["phases"]]
    current_index = phase_ids.index(current_phase_id)
    for index, phase in enumerate(manifest["phases"]):
        phase["state"] = "active" if index == current_index else ("complete" if index < current_index else "upcoming")
    for index, checkpoint in enumerate(manifest["checkpoints"]):
        checkpoint["status"] = "DUE" if index == current_index else ("COMPLETE" if index < current_index else "UPCOMING")
    if current_phase_id == "p5-delivery":
        manifest["checkpoints"][-1]["status"] = "DORMANT"
    for index, milestone in enumerate(manifest["milestones"]):
        milestone["status"] = "COMPLETE" if index < current_index else "PENDING"
    manifest["decisions"] = decisions
    manifest["gates"] = gates
    manifest["evidence"] = [{"id": "E-001", "path": "evidence/E-001.md", "subject_revision": 1}]
    normalized_edges = list(edges or [])
    for entry, directory_name in [
        *((item, "decisions") for item in decisions),
        *((item, "gates") for item in gates),
    ]:
        node_id = entry["id"]
        ticket_path = effort / directory_name / f"{node_id}.md"
        fields: dict[str, str] = {}
        if ticket_path.is_file():
            metadata = ticket_path.read_text(encoding="utf-8").split("\n## ", 1)[0]
            for line in metadata.splitlines():
                match = re.match(r"^\s*-\s*\*\*([^*]+?):\*\*\s*(.*?)\s*$", line)
                if match:
                    key = re.sub(r"[^a-z0-9]+", "_", match.group(1).lower()).strip("_")
                    fields.setdefault(key, match.group(2))
        for edge_type in ("requires", "revalidates", "gates"):
            for target in sorted(set(re.findall(r"\b(?:D|G|M)-\d{3,}\b", fields.get(edge_type, ""), flags=re.IGNORECASE))):
                normalized_edges.append({"from": node_id, "type": edge_type, "to": target.upper()})
        for evidence_id in sorted(set(re.findall(r"\bE-\d{3,}\b", fields.get("informs", ""), flags=re.IGNORECASE))):
            normalized_edges.append({"from": evidence_id.upper(), "type": "informs", "to": node_id})
    deduplicated = {(edge["from"], edge["type"], edge["to"]): edge for edge in normalized_edges}
    manifest["edges"] = list(deduplicated.values())
    manifest["activity"] = []
    (effort / "evidence" / "E-001.md").write_text(
        "# E-001: Verified route evidence\n\n"
        "- **Kind:** EVIDENCE\n"
        "- **Method:** OBSERVATION\n"
        "- **Observed at:** 2026-08-22T00:00:00Z\n"
        f"- **Subject / revision:** {effort.name} / 1\n"
        "- **Source:** Local fixture\n"
        "- **Source type:** LOCAL-OBSERVATION\n"
        "- **Collector:** Example tester\n"
        "- **Basis:** OBSERVED\n"
        "- **Confidence:** HIGH\n"
        "- **Sensitivity:** INTERNAL\n"
        "- **Content hash:** unknown\n"
        "- **Revalidate when:** Destination revision changes.\n\n"
        "## Conclusion\n\nThe route evidence supports the recorded decision.\n",
        encoding="utf-8",
    )
    (effort / "EFFORT.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def write_valid_exit(effort: Path) -> None:
    manifest_path = effort / "EFFORT.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    decisions = [entry for entry in manifest["decisions"] if entry.get("destination_blocking") and entry.get("status") in {"RESOLVED", "SUPERSEDED"}]
    gates = [entry for entry in manifest["gates"] if entry.get("status") != "SUPERSEDED"]
    decision_rows = "\n".join(f"| {entry['id']} | Chosen verified route. | Example owner | E-001 | 1 |" for entry in decisions)
    gate_rows = "\n".join(
        f"| {entry['id']} | The exact release remains within the measured load budget. | Example owner | D-001 | M-005 | Destination revision changes. |"
        for entry in gates
    )
    (effort / "EXIT.md").write_text(
        "# Wayfinder Completion Receipt: Example Launch\n\n"
        f"- **Effort:** {effort.name}\n"
        "- **Schema:** 3\n"
        "- **Receipt status:** CURRENT\n"
        "- **Destination revision:** 1\n"
        "- **Completed at:** 2026-08-22T00:00:00Z\n"
        "- **Completed by:** Example owner\n"
        f"- **Manifest hash:** {digest}\n\n"
        "## Destination accepted for specification\n\nA safe example reaches production.\n\n"
        "## Success conditions\n\n"
        "| ID | Observable condition | Route evidence | Status |\n"
        "| --- | --- | --- | --- |\n"
        "| SC-001 | Example succeeds. | E-001 | ROUTED |\n\n"
        "## Validated assumptions and accepted risks\n\n"
        "| Assumption | Status | Evidence or accepted-risk receipt | Revalidate when |\n"
        "| --- | --- | --- | --- |\n\n"
        "## Active invariants\n\n"
        "| ID | Invariant | Enforcement | Evidence | Revalidate when |\n"
        "| --- | --- | --- | --- | --- |\n\n"
        "## Resolved destination-blocking Decisions\n\n"
        "| ID | Resolution | Decision authority | Evidence | Revision |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"{decision_rows}\n\n"
        "## Delivery Gates defined for later evaluation\n\n"
        "| ID | Delivery condition | Responsible party | Revalidates Decisions | Gates milestone | Freshness rule |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"{gate_rows}\n\n"
        "## Remaining non-blocking unknowns\n\n- None.\n\n"
        "## Revalidation triggers\n\n- Destination revision changes.\n\n"
        "## Completion validation\n\n"
        "- [x] Decisions settled.\n- [x] Fog settled.\n- [x] Assumptions settled.\n"
        "- [x] Dependents inspected.\n- [x] Evidence fresh.\n- [x] Gates defined.\n- [x] Specification ready.\n",
        encoding="utf-8",
    )


class WayfinderV3Tests(unittest.TestCase):
    def test_dashboard_uses_a_fresh_loopback_origin_by_default(self) -> None:
        arguments = CLI_MODULE.parser().parse_args(["dashboard"])
        self.assertEqual(0, arguments.port)
        signature = inspect.signature(SERVER_MODULE.make_server)
        self.assertEqual(0, signature.parameters["port"].default)

    def test_terminal_output_escapes_artifact_controls_and_bidi_formatting(self) -> None:
        osc = "\x1b]52;c;c2VudGluZWw=\x07"
        bidi = "\u202e"
        separators = "\u2028\u2029"
        surrogate = "\ud800"
        rendered = SERVER_MODULE.terminal_safe_text(
            f"before{osc}{bidi}{separators}{surrogate}after\nnext", allow_newlines=True
        )
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\x07", rendered)
        self.assertNotIn(bidi, rendered)
        self.assertNotIn("\u2028", rendered)
        self.assertNotIn("\u2029", rendered)
        self.assertNotIn(surrogate, rendered)
        self.assertIn("\\u001b]52", rendered)
        self.assertIn("\\u202e", rendered)
        self.assertIn("\\u2028\\u2029\\ud800", rendered)
        self.assertEqual(1, rendered.count("\n"))

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(
                ticket("D-001", "Route", "OPEN"), encoding="utf-8"
            )
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
            )
            manifest = json.loads((effort / "EFFORT.json").read_text(encoding="utf-8"))
            manifest["effort"]["title"] = f"Route {osc}{bidi}\nState: complete | Health: healthy"
            (effort / "EFFORT.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(CLI), "status", "--root", str(repo)],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("\x1b", combined)
            self.assertNotIn("\x07", combined)
            self.assertNotIn(bidi, combined)
            self.assertIn("\\u001b]52", combined)
            self.assertIn("\\u202e", combined)
            self.assertIn("\\u000aState: complete", combined)
            self.assertNotIn("\nState: complete | Health: healthy\n", combined)

    def test_northstar_style_v2_computes_exactly_five_actionable_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            specs = {
                "D-001": ("Destination and release sequence", "RESOLVED", "None"),
                "D-002": ("Cloud account recovery", "BLOCKED", "D-001"),
                "D-003": ("Production data isolation", "OPEN", "D-001"),
                "D-004": ("Tenant isolation contract", "RESOLVED", "D-001"),
                "D-005": ("Coverage publication standard", "OPEN", "D-001"),
                "D-006": ("Measured worker economics", "OPEN", "D-002, D-003, D-009"),
                "D-007": ("Payment ownership readiness", "OPEN", "D-001"),
                "D-008": ("Credit ledger contract", "RESOLVED", "D-001"),
                "D-009": ("Model routing", "OPEN", "D-002, D-003"),
                "D-010": ("Packet validation", "OPEN", "D-003, D-009"),
                "D-011": ("Legal approval", "OPEN", "D-005, D-018"),
                "D-012": ("Monitoring ownership", "OPEN", "D-001"),
                "D-013": ("Privacy baseline", "RESOLVED", "D-001"),
                "D-014": ("Private source control", "OPEN", "D-001"),
                "D-015": ("Final public release approval", "BLOCKED", "D-006, D-016, D-019"),
                "D-016": ("Complete release evidence", "OPEN", "D-002, D-003, D-019"),
                "D-017": ("Manual application boundary", "RESOLVED", "D-001"),
                "D-018": ("Operations contract", "OPEN", "D-003, D-012"),
                "D-019": ("Production topology and scalability gate", "OPEN", "D-002, D-003, D-012, D-014"),
            }
            rows = [
                "| ID | Question | Type | Owner | Status | Prerequisites |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
            for node_id, (title, status, prerequisites) in specs.items():
                (effort / "decisions" / f"{node_id}.md").write_text(
                    ticket(node_id, title, status, prerequisites, v3=False), encoding="utf-8"
                )
                if status in {"OPEN", "BLOCKED"}:
                    rows.append(f"| {node_id} | {title}? | ANALYSIS | HYBRID | {status} | {prerequisites} |")
            map_text = (effort / "MAP.md").read_text(encoding="utf-8")
            map_text = map_text.replace("## Frontier\n", "## Frontier\n\n" + "\n".join(rows) + "\n")
            (effort / "MAP.md").write_text(map_text, encoding="utf-8")

            state = build_state(repo)
            self.assertEqual(5, state["counts"]["actionable"])
            self.assertEqual(
                ["D-003", "D-005", "D-007", "D-012", "D-014"],
                state["views"]["actionable"],
            )
            self.assertEqual(3, state["counts"]["gates"])
            self.assertEqual([], state["health"]["issues"])
            self.assertTrue(any(item["code"] == "FRONTIER_STALE" for item in state["health"]["warnings"]))

    def test_post_build_gate_does_not_block_pre_spec_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(
                ticket("D-001", "Architecture choice", "RESOLVED"), encoding="utf-8"
            )
            (effort / "gates" / "G-001.md").write_text(
                ticket("G-001", "Release load gate", "DEFINED", "D-001", kind="GATE", post_build=True),
                encoding="utf-8",
            )
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve", "destination_blocking": True}],
                [{"id": "G-001", "path": "gates/G-001.md", "status": "DEFINED", "phase_id": "p5-delivery", "destination_blocking": True, "post_build": True, "waiver_authority": "Release owner"}],
                [{"from": "G-001", "type": "requires", "to": "D-001"}],
                current_phase_id="p4-ready",
            )

            state = build_state(repo)
            self.assertTrue(state["exit"]["pre_spec_ready"])
            self.assertFalse(state["exit"]["complete"])
            self.assertEqual(["G-001"], state["exit"]["pending_delivery_gates"])
            self.assertEqual("checkpoint", state["run_recommendation"]["level"])
            self.assertEqual("cp4-handoff", state["run_recommendation"]["checkpoint_id"])
            self.assertEqual([], state["health"]["issues"])
            gate = next(node for node in state["nodes"] if node["id"] == "G-001")
            self.assertEqual("Release owner", gate["waiver_authority"])
            self.assertEqual("Example owner", next(node for node in state["nodes"] if node["id"] == "D-001")["decision_authority"])

            complete = subprocess.run(
                [sys.executable, str(CLI), "complete", "--root", str(repo)],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(0, complete.returncode, complete.stdout + complete.stderr)
            self.assertIn("Pre-spec exit eligible: yes", complete.stdout)
            self.assertFalse((effort / "EXIT.md").exists())

    def test_doctor_detects_missing_ids_and_requires_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            for node_id, requirement in (("D-001", "D-002"), ("D-002", "D-001, D-999")):
                (effort / "decisions" / f"{node_id}.md").write_text(
                    ticket(node_id, node_id, "OPEN", requirement), encoding="utf-8"
                )
            write_manifest(
                effort,
                [
                    {"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"},
                    {"id": "D-002", "path": "decisions/D-002.md", "status": "OPEN", "phase_id": "p2-resolve"},
                ],
            )
            result = subprocess.run(
                [sys.executable, str(CLI), "doctor", "--root", str(repo), "--json"],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            state = json.loads(result.stdout)
            codes = {item["code"] for item in state["health"]["issues"]}
            self.assertIn("REFERENCE_MISSING", codes)
            self.assertIn("DEPENDENCY_CYCLE", codes)
            self.assertEqual("required", state["run_recommendation"]["level"])

    def test_run_recommendation_moves_from_now_to_dormant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            path = effort / "decisions" / "D-001.md"
            path.write_text(ticket("D-001", "Route choice", "OPEN"), encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
            )
            active = build_state(repo)
            self.assertEqual("now", active["run_recommendation"]["level"])
            self.assertTrue(active["run_recommendation"]["recommended"])

            path.write_text(ticket("D-001", "Route choice", "RESOLVED"), encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve"}],
                current_phase_id="p5-delivery",
            )
            eligible = build_state(repo)
            self.assertTrue(eligible["exit"]["pre_spec_ready"])
            self.assertFalse(eligible["exit"]["complete"])
            self.assertEqual("checkpoint", eligible["run_recommendation"]["level"])
            write_valid_exit(effort)
            complete = build_state(repo)
            self.assertTrue(complete["exit"]["complete"])
            self.assertEqual("dormant", complete["run_recommendation"]["level"])
            self.assertFalse(complete["run_recommendation"]["recommended"])

    def test_routine_delivery_gate_is_dormant_but_failure_targets_revalidation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            decision_path = effort / "decisions" / "D-001.md"
            gate_path = effort / "gates" / "G-001.md"
            decision_path.write_text(ticket("D-001", "Route choice", "RESOLVED"), encoding="utf-8")
            gate_path.write_text(
                ticket("G-001", "Delivery load check", "DEFINED", "D-001", kind="GATE", post_build=True),
                encoding="utf-8",
            )
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve"}],
                [{"id": "G-001", "path": "gates/G-001.md", "status": "DEFINED", "phase_id": "p5-delivery", "post_build": True}],
                [
                    {"from": "G-001", "type": "requires", "to": "D-001"},
                    {"from": "G-001", "type": "revalidates", "to": "D-001"},
                ],
                current_phase_id="p5-delivery",
            )
            eligible = build_state(repo)
            self.assertTrue(eligible["exit"]["pre_spec_ready"])
            self.assertFalse(eligible["exit"]["complete"])
            write_valid_exit(effort)
            routine = build_state(repo)
            self.assertTrue(routine["exit"]["complete"])
            self.assertEqual(["G-001"], routine["exit"]["pending_delivery_gates"])
            self.assertEqual(["G-001"], routine["views"]["delivery_gates"])
            self.assertEqual([], routine["views"]["actionable"])
            self.assertEqual("dormant", routine["run_recommendation"]["level"])
            self.assertFalse(routine["run_recommendation"]["recommended"])

            manifest_path = effort / "EFFORT.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["gates"][0]["status"] = "FAILED"
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            gate_path.write_text(
                ticket("G-001", "Delivery load check", "FAILED", "D-001", kind="GATE", post_build=True),
                encoding="utf-8",
            )
            failed = build_state(repo)
            self.assertEqual("now", failed["run_recommendation"]["level"])
            self.assertTrue(failed["run_recommendation"]["recommended"])
            self.assertEqual(["G-001"], failed["views"]["revalidation"])
            self.assertEqual("RESOLVED", next(node for node in failed["nodes"] if node["id"] == "D-001")["status"])

    def test_init_json_escapes_quoted_destination_and_cas_conflict_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, timeout=5)
            destination = 'Route with "quoted" choice \\ path and literal {{SLUG}} plus {{CREATED_AT}}'
            first = subprocess.run(
                [sys.executable, str(CLI), "start", "--root", str(repo), "--slug", "safe", "--destination", destination],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(0, first.returncode, first.stdout + first.stderr)
            manifest_path = repo / ".codex" / "wayfinder" / "efforts" / "safe" / "EFFORT.json"
            self.assertEqual(destination, json.loads(manifest_path.read_text(encoding="utf-8"))["effort"]["destination"])
            self.assertIn(destination, (manifest_path.parent / "MAP.md").read_text(encoding="utf-8"))
            second = subprocess.run(
                [sys.executable, str(CLI), "start", "--root", str(repo), "--slug", "second", "--destination", "Second"],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertNotEqual(0, second.returncode)
            self.assertFalse((repo / ".codex" / "wayfinder" / "efforts" / "second").exists())
            active_path = repo / ".codex" / "wayfinder" / "ACTIVE"
            original_active = active_path.read_text(encoding="utf-8").strip()
            guarded = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "start",
                    "--root",
                    str(repo),
                    "--slug",
                    "guarded",
                    "--destination",
                    "Guarded route",
                    "--expect-active",
                    original_active,
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(0, guarded.returncode, guarded.stdout + guarded.stderr)
            self.assertEqual(
                ".codex/wayfinder/efforts/guarded/MAP.md",
                active_path.read_text(encoding="utf-8").strip(),
            )
            self.assertTrue(manifest_path.exists())
            for slug, unsafe_destination in (("bad-bidi", "Route\u202eoverride"), ("bad-c1", "Route\x85override")):
                rejected = subprocess.run(
                    [sys.executable, str(CLI), "start", "--root", str(repo), "--slug", slug, "--destination", unsafe_destination],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
                self.assertNotEqual(0, rejected.returncode)
                self.assertFalse((repo / ".codex" / "wayfinder" / "efforts" / slug).exists())

    def test_init_rolls_back_a_mid_write_failure_without_partial_effort(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, timeout=5)
            original_write = INIT_MODULE.write_new
            calls = 0

            def fail_after_one_file(parent: int, name: str, content: str) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected template write failure")
                original_write(parent, name, content)

            with mock.patch.object(INIT_MODULE, "write_new", side_effect=fail_after_one_file):
                with self.assertRaises(SystemExit):
                    INIT_MODULE.main(
                        [
                            "--root",
                            str(repo),
                            "--slug",
                            "rollback",
                            "--destination",
                            "Rollback-safe route",
                        ]
                    )

            wayfinder = repo / ".codex" / "wayfinder"
            efforts = wayfinder / "efforts"
            self.assertFalse((efforts / "rollback").exists())
            self.assertEqual([], list(efforts.iterdir()))
            self.assertFalse((wayfinder / "ACTIVE").exists())
            self.assertFalse((wayfinder / "ACTIVE.lock").exists())

    def test_complete_rejects_status_only_decision_and_invalid_transition_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            decision_path = effort / "decisions" / "D-001.md"
            decision_path.write_text(ticket("D-001", "Route authority", "RESOLVED"), encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve"}],
                current_phase_id="p4-ready",
            )
            write_valid_exit(effort)
            self.assertTrue(build_state(repo)["exit"]["complete"])

            malformed = decision_path.read_text(encoding="utf-8")
            malformed = malformed.replace("- **Decision authority:** Example owner", "- **Decision authority:**")
            malformed = malformed.replace("- **Evidence:** E-001", "- **Evidence:** none")
            malformed = malformed.replace("- **Resolution:** Chosen verified route.", "- **Resolution:**")
            malformed = malformed.replace(
                "| OPEN | RESOLVED | Example | 2026-08-22T00:00:00Z | Fixture | E-001 |",
                "| OPEN | RESOLVED |  | not-a-timestamp |  | none |",
            )
            decision_path.write_text(malformed, encoding="utf-8")

            state = build_state(repo)
            codes = {item["code"] for item in state["health"]["issues"]}
            self.assertFalse(state["exit"]["pre_spec_ready"])
            self.assertFalse(state["exit"]["complete"])
            self.assertTrue(
                {
                    "DECISION_AUTHORITY_REQUIRED",
                    "DECISION_EVIDENCE_REQUIRED",
                    "DECISION_RESOLUTION_REQUIRED",
                    "TERMINAL_TRANSITION_EVIDENCE_REQUIRED",
                    "TRANSITION_ACTOR_REQUIRED",
                    "TRANSITION_REASON_REQUIRED",
                    "TRANSITION_TIMESTAMP_INVALID",
                    "EXIT_DECISION_ROW_CONFLICT",
                }.issubset(codes),
                codes,
            )
            result = subprocess.run(
                [sys.executable, str(CLI), "complete", "--root", str(repo)],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)

    def test_defined_gate_requires_accountability_checks_freshness_and_typed_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(
                ticket("D-001", "Route choice", "RESOLVED"), encoding="utf-8"
            )
            gate_path = effort / "gates" / "G-001.md"
            malformed = ticket("G-001", "Delivery proof", "DEFINED", "D-001", kind="GATE", post_build=True)
            for before, after in (
                ("- **Responsible party:** Example owner", "- **Responsible party:**"),
                ("- **Waiver authority:** Release owner", "- **Waiver authority:**"),
                ("- **Next actor:** Codex", "- **Next actor:**"),
                ("- **Revalidates:** D-001", "- **Revalidates:** none"),
                ("- **Gates:** M-005", "- **Gates:** none"),
                ("- **Subject revision:** 1", "- **Subject revision:**"),
                ("- **Revalidate when:** Destination revision changes.", "- **Revalidate when:**"),
                ("The exact release remains within the measured load budget.", "{{OBSERVABLE_GATE_CONDITION}}"),
                ("| C-001 | COMMAND | The load probe passes. | Saved E-001 result. | PENDING |\n", ""),
            ):
                malformed = malformed.replace(before, after)
            gate_path.write_text(malformed, encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve"}],
                [{"id": "G-001", "path": "gates/G-001.md", "status": "DEFINED", "phase_id": "p5-delivery", "post_build": True}],
                current_phase_id="p4-ready",
            )
            manifest_path = effort / "EFFORT.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["edges"] = [
                edge
                for edge in manifest["edges"]
                if not (edge["from"] == "G-001" and edge["type"] in {"revalidates", "gates"})
            ]
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

            state = build_state(repo)
            codes = {item["code"] for item in state["health"]["issues"]}
            self.assertFalse(state["exit"]["pre_spec_ready"])
            self.assertTrue(
                {
                    "ACCOUNTABILITY_FIELD_REQUIRED",
                    "WAIVER_AUTHORITY_REQUIRED",
                    "GATE_CONDITION_REQUIRED",
                    "GATE_SUBJECT_REVISION_REQUIRED",
                    "GATE_FRESHNESS_REQUIRED",
                    "GATE_REVALIDATES_REQUIRED",
                    "GATE_MILESTONE_LINK_REQUIRED",
                    "GATE_CHECK_REQUIRED",
                }.issubset(codes),
                codes,
            )

    def test_route_evidence_must_be_current_indexed_and_receipt_refs_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            decision_path = effort / "decisions" / "D-001.md"
            decision_path.write_text(
                ticket("D-001", "Evidence-backed route", "RESOLVED").replace(
                    "| OPEN | RESOLVED | Example | 2026-08-22T00:00:00Z | Fixture | E-001 |",
                    "| OPEN | RESOLVED | Example | 2026-08-22T00:00:00Z | Fixture | E-999 |",
                ),
                encoding="utf-8",
            )
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve"}],
                current_phase_id="p4-ready",
            )
            evidence_path = effort / "evidence" / "E-001.md"
            evidence_path.write_text(
                evidence_path.read_text(encoding="utf-8").replace("launch / 1", "launch / 0"),
                encoding="utf-8",
            )
            (effort / "evidence" / "E-002.md").write_text(
                evidence_path.read_text(encoding="utf-8")
                .replace("# E-001:", "# E-002:")
                .replace("launch / 0", "launch / 1"),
                encoding="utf-8",
            )

            state = build_state(repo)
            codes = {item["code"] for item in state["health"]["issues"]}
            self.assertFalse(state["exit"]["pre_spec_ready"])
            self.assertTrue(
                {
                    "EVIDENCE_REFERENCE_MISSING",
                    "EVIDENCE_UNINDEXED",
                    "EVIDENCE_INDEX_CONFLICT",
                    "ROUTE_EVIDENCE_STALE",
                }.issubset(codes),
                codes,
            )

    def test_changed_decision_requires_complete_dependent_inspection_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            first_path = effort / "decisions" / "D-001.md"
            first_path.write_text(
                ticket("D-001", "Changed route", "RESOLVED").replace("- **Revision:** 1", "- **Revision:** 2"),
                encoding="utf-8",
            )
            (effort / "decisions" / "D-002.md").write_text(
                ticket("D-002", "Dependent route", "RESOLVED", "D-001"), encoding="utf-8"
            )
            write_manifest(
                effort,
                [
                    {"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve"},
                    {"id": "D-002", "path": "decisions/D-002.md", "status": "RESOLVED", "phase_id": "p2-resolve"},
                ],
                edges=[{"from": "D-002", "type": "requires", "to": "D-001"}],
                current_phase_id="p4-ready",
            )
            missing = build_state(repo)
            self.assertIn("DEPENDENT_INSPECTION_REQUIRED", {item["code"] for item in missing["health"]["issues"]})

            receipt = (
                "## Dependent inspections\n\n"
                "| Trigger | Dependent | Outcome | Evidence | Actor | Timestamp |\n"
                "| --- | --- | --- | --- | --- | --- |\n"
                "| D-001 revision 2 | D-002 | STILL-VALID | E-001 | Example | 2026-08-22T00:00:00Z |\n\n"
            )
            first_path.write_text(
                first_path.read_text(encoding="utf-8").replace("## Append-only transition history", receipt + "## Append-only transition history"),
                encoding="utf-8",
            )
            valid = build_state(repo)
            self.assertNotIn("DEPENDENT_INSPECTION_REQUIRED", {item["code"] for item in valid["health"]["issues"]})
            self.assertTrue(valid["exit"]["pre_spec_ready"])

    def test_manifest_lifecycle_schema_and_typed_edges_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(ticket("D-001", "Route", "OPEN"), encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
            )
            manifest_path = effort / "EFFORT.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["current_phase_id"] = "p9-invented"
            manifest["phases"][0]["label"] = "Invent destination"
            manifest["phases"][0]["description"] = "Arbitrary phase contract."
            manifest["checkpoints"][0]["phase_id"] = "p2-resolve"
            manifest["checkpoints"][0]["run_recommended"] = False
            manifest["checkpoints"][0]["due_when"] = "Whenever."
            manifest["milestones"][0]["label"] = "Invented milestone"
            manifest["milestones"][0]["criteria"] = ["Anything goes."]
            manifest["edges"].append(dict(manifest["edges"][0]))
            manifest["edges"].extend(
                [
                    {"from": "D-001", "type": "informs", "to": "E-001"},
                    {"from": "D-001", "type": "requires", "to": "D-001"},
                ]
            )
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

            state = build_state(repo)
            codes = {item["code"] for item in state["health"]["issues"]}
            self.assertTrue(
                {
                    "CURRENT_PHASE_INVALID",
                    "CURRENT_PHASE_STATE_CONFLICT",
                    "PHASE_LABEL_INVALID",
                    "PHASE_DESCRIPTION_INVALID",
                    "CHECKPOINT_MAPPING_INVALID",
                    "CHECKPOINT_RECOMMENDATION_INVALID",
                    "CHECKPOINT_DETAIL_INVALID",
                    "MILESTONE_MAPPING_INVALID",
                    "MILESTONE_CRITERIA_INVALID",
                    "EDGE_DUPLICATE",
                    "EDGE_KIND_INVALID",
                    "EDGE_SELF_REFERENCE",
                }.issubset(codes),
                codes,
            )

    def test_invalid_manifest_and_ticket_ids_never_enter_state_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(
                ticket("D-001", "Safe route", "OPEN"), encoding="utf-8"
            )
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
            )
            sentinel = "INVALID-ID-SENTINEL;COMMAND"
            declared = ticket("D-777", "Injected ID ticket", "OPEN")
            declared = declared.replace(
                "- **Question:**",
                f"- **ID:** X;{sentinel}\n- **Question:**",
                1,
            )
            (effort / "decisions" / "D-777.md").write_text(declared, encoding="utf-8")
            manifest_path = effort / "EFFORT.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["decisions"].append(
                {"id": f"X;{sentinel}", "path": "decisions/D-777.md", "status": "OPEN"}
            )
            manifest["gates"].append(
                {"id": f"G-888\n{sentinel}", "path": "gates/G-888.md", "status": "DEFINED"}
            )
            manifest["evidence"].append(
                {"id": f"E-888;{sentinel}", "path": "evidence/E-888.md", "subject_revision": 1}
            )
            manifest["edges"].append(
                {"from": f"X;{sentinel}", "type": "requires", "to": "D-001"}
            )
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

            state = build_state(repo)
            codes = {item["code"] for item in state["health"]["issues"]}
            self.assertTrue(
                {"NODE_ID_INVALID", "TICKET_ID_INVALID", "EVIDENCE_ID_INVALID", "EDGE_ID_INVALID"}.issubset(codes),
                codes,
            )
            self.assertEqual(["D-001"], [node["id"] for node in state["nodes"]])
            serialized = STATE_MODULE.state_json(state)
            self.assertNotIn(sentinel, serialized)
            self.assertTrue(
                all(
                    re.fullmatch(r"(?:D|G|E|M)-\d{3,}", endpoint)
                    for edge in state["edges"]
                    for endpoint in (edge["source"], edge["target"])
                )
            )

    def test_long_requires_chain_is_iterative_and_v2_discovery_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            decisions: list[dict] = []
            previous = "None"
            for index in range(1, 1201):
                node_id = f"D-{index:04d}"
                (effort / "decisions" / f"{node_id}.md").write_text(
                    ticket(node_id, f"Route step {index}", "OPEN", previous),
                    encoding="utf-8",
                )
                decisions.append(
                    {"id": node_id, "path": f"decisions/{node_id}.md", "status": "OPEN", "phase_id": "p2-resolve"}
                )
                previous = node_id
            write_manifest(effort, decisions)

            state = build_state(repo)
            self.assertEqual(1200, state["counts"]["total"])
            self.assertEqual(["D-0001"], state["views"]["actionable"])
            self.assertNotIn("DEPENDENCY_CYCLE", {item["code"] for item in state["health"]["issues"]})

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            for index in range(1, 4):
                node_id = f"D-{index:03d}"
                (effort / "decisions" / f"{node_id}.md").write_text(
                    ticket(node_id, f"Legacy route {index}", "OPEN", v3=False), encoding="utf-8"
                )
            with mock.patch.object(STATE_MODULE, "MAX_DIRECTORY_ENTRIES", 2):
                state = build_state(repo)
            self.assertIn("TICKET_DIRECTORY_LIMIT", {item["code"] for item in state["health"]["issues"]})
            self.assertEqual(0, state["counts"]["total"])

    def test_activity_is_an_exact_bounded_secret_free_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(ticket("D-001", "Route", "OPEN"), encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
            )
            manifest_path = effort / "EFFORT.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["activity"] = [
                {
                    "id": "activity-1\ncontinued",
                    "type": "UPDATE",
                    "timestamp": "2026-08-22T00:00:00Z\nforged",
                    "node_id": "d-001",
                    "message": "First\nsecond\x1b\u202e line",
                    "actor": "Jay\u2029Admin",
                    "api_key": "SECRET-ACTIVITY-KEY",
                    "details": {"token": "SECRET-NESTED-TOKEN"},
                },
                {
                    "id": "activity-2",
                    "type": "invalidation",
                    "timestamp": "2026-08-22T01:00:00Z",
                    "node_id": {"token": "SECRET-NODE-TOKEN"},
                    "message": "Route changed.",
                    "actor": "Reviewer",
                },
                {
                    "id": "activity-3",
                    "type": "update",
                    "timestamp": "2026-08-22T02:00:00Z",
                    "node_id": "D-001",
                    "message": "OMITTED-ACTIVITY-SENTINEL",
                    "actor": "Reviewer",
                },
            ]
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

            with mock.patch.object(STATE_MODULE, "MAX_ACTIVITY_ENTRIES", 2):
                state = build_state(repo)
            self.assertEqual(2, len(state["activity"]))
            self.assertEqual(
                {"id", "type", "timestamp", "node_id", "message", "actor"},
                set(state["activity"][0]),
            )
            self.assertEqual("activity-1 continued", state["activity"][0]["id"])
            self.assertEqual("update", state["activity"][0]["type"])
            self.assertEqual("D-001", state["activity"][0]["node_id"])
            self.assertEqual("First second line", state["activity"][0]["message"])
            self.assertEqual("Jay Admin", state["activity"][0]["actor"])
            self.assertEqual("", state["activity"][1]["node_id"])
            codes = {item["code"] for item in state["diagnostics"]}
            self.assertTrue({"ACTIVITY_LIMIT", "ACTIVITY_FIELDS_OMITTED"}.issubset(codes), codes)
            serialized = STATE_MODULE.state_json(state)
            for sentinel in (
                "SECRET-ACTIVITY-KEY",
                "SECRET-NESTED-TOKEN",
                "SECRET-NODE-TOKEN",
                "OMITTED-ACTIVITY-SENTINEL",
                "api_key",
                "details",
            ):
                self.assertNotIn(sentinel, serialized)
            self.assertNotIn("\x1b", serialized)
            self.assertNotIn("\u202e", state["activity"][0]["message"])

    def test_duplicate_nodes_and_edge_amplification_are_bounded_before_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            ticket_path = effort / "decisions" / "D-001.md"
            ticket_path.write_text(ticket("D-001", "Large route", "OPEN") + ("x" * 1_000_000), encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
            )
            manifest_path = effort / "EFFORT.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["decisions"].extend([dict(manifest["decisions"][0]) for _ in range(300)])
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

            original_read = STATE_MODULE._read_regular_text
            ticket_reads = 0

            def counting_read(path, *args, **kwargs):
                nonlocal ticket_reads
                if Path(path).name == "D-001.md":
                    ticket_reads += 1
                return original_read(path, *args, **kwargs)

            with mock.patch.object(STATE_MODULE, "_read_regular_text", side_effect=counting_read):
                state = build_state(repo)
            self.assertEqual(1, ticket_reads)
            self.assertEqual(
                1,
                sum(item["code"] == "MANIFEST_NODE_DUPLICATE" for item in state["diagnostics"]),
            )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["decisions"] = manifest["decisions"][:1]
            duplicate_edge = {"from": "D-001", "type": "requires", "to": "D-999"}
            manifest["edges"] = [duplicate_edge for _ in range(5_000)]
            manifest_path.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
            state = build_state(repo)
            self.assertEqual(
                0,
                sum(
                    edge == {"source": "D-001", "target": "D-999", "type": "requires"}
                    for edge in state["edges"]
                ),
            )
            self.assertEqual(1, sum(item["code"] == "EDGE_DUPLICATE" for item in state["diagnostics"]))
            self.assertEqual(1, sum(item["code"] == "EDGE_INDEX_MISMATCH" for item in state["diagnostics"]))
            self.assertLess(len(STATE_MODULE.state_json(state)), 250_000)

            manifest["edges"] = [duplicate_edge for _ in range(STATE_MODULE.MAX_MANIFEST_EDGES + 1)]
            manifest_path.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
            state = build_state(repo)
            self.assertIn("MANIFEST_EDGE_LIMIT", {item["code"] for item in state["health"]["issues"]})
            self.assertLess(len(STATE_MODULE.state_json(state)), 250_000)

    def test_manifest_json_and_nested_public_scalars_fail_closed_without_reflection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(ticket("D-001", "Safe title", "OPEN"), encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
            )
            manifest_path = effort / "EFFORT.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            nested_sentinel = "SECRET-NESTED-PUBLIC-SCALAR"
            manifest["effort"]["title"] = {"unknown_private_key": nested_sentinel}
            manifest["effort"]["destination"] = {"token": nested_sentinel}
            manifest["decisions"][0]["title"] = {"private": nested_sentinel}
            manifest["decisions"][0]["status"] = {"private": nested_sentinel}
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            state = build_state(repo)
            serialized = STATE_MODULE.state_json(state)
            self.assertNotIn(nested_sentinel, serialized)
            self.assertEqual("Example Launch", state["project"]["title"])
            self.assertEqual("Safe title", state["nodes"][0]["title"])
            self.assertEqual("OPEN", state["nodes"][0]["status"])

            valid_text = json.dumps(manifest)
            duplicate_sentinel = "SECRET-DUPLICATE-KEY"
            duplicate_text = valid_text.replace(
                '"schema_version": 3',
                f'"schema_version": 3, "schema_version": "{duplicate_sentinel}"',
                1,
            )
            manifest_path.write_text(duplicate_text, encoding="utf-8")
            duplicate_state = build_state(repo)
            self.assertIn("MANIFEST_INVALID", {item["code"] for item in duplicate_state["health"]["issues"]})
            self.assertNotIn(duplicate_sentinel, STATE_MODULE.state_json(duplicate_state))

            nan_sentinel = "SECRET-NAN-MANIFEST"
            manifest_path.write_text(
                '{"schema_version":3,"effort":{"title":"' + nan_sentinel + '","destination_revision":NaN}}',
                encoding="utf-8",
            )
            nan_state = build_state(repo)
            self.assertIn("MANIFEST_INVALID", {item["code"] for item in nan_state["health"]["issues"]})
            self.assertNotIn(nan_sentinel, STATE_MODULE.state_json(nan_state))
            with self.assertRaises(ValueError):
                STATE_MODULE.state_json({"invalid": float("nan")})

            deep_sentinel = "SECRET-DEEP-MANIFEST"
            manifest_path.write_text(
                '{"schema_version":3,"nested":' + ("[" * 1_100) + '"' + deep_sentinel + '"' + ("]" * 1_100) + "}",
                encoding="utf-8",
            )
            deep_state = build_state(repo)
            self.assertIn("MANIFEST_INVALID", {item["code"] for item in deep_state["health"]["issues"]})
            self.assertNotIn(deep_sentinel, STATE_MODULE.state_json(deep_state))

    def test_duplicate_markdown_metadata_is_never_last_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            duplicate_sentinel = "DUPLICATE-METADATA-SENTINEL"
            decision_text = ticket("D-001", "Route", "OPEN").replace(
                "- **Status:** OPEN\n",
                f"- **Status:** OPEN\n- **Status:** RESOLVED-{duplicate_sentinel}\n",
                1,
            )
            (effort / "decisions" / "D-001.md").write_text(decision_text, encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
            )
            evidence_path = effort / "evidence" / "E-001.md"
            evidence_path.write_text(
                evidence_path.read_text(encoding="utf-8").replace(
                    "- **Source:** Local fixture\n",
                    f"- **Source:** Local fixture\n- **Source:** {duplicate_sentinel}\n",
                    1,
                ),
                encoding="utf-8",
            )
            write_valid_exit(effort)
            exit_path = effort / "EXIT.md"
            exit_path.write_text(
                exit_path.read_text(encoding="utf-8").replace(
                    "- **Receipt status:** CURRENT\n",
                    f"- **Receipt status:** CURRENT\n- **Receipt status:** {duplicate_sentinel}\n",
                    1,
                ),
                encoding="utf-8",
            )

            state = build_state(repo)
            codes = {item["code"] for item in state["health"]["issues"]}
            self.assertTrue(
                {"TICKET_METADATA_INVALID", "EVIDENCE_METADATA_INVALID", "EXIT_RECEIPT_INVALID"}.issubset(codes),
                codes,
            )
            self.assertEqual("OPEN", state["nodes"][0]["status"])
            self.assertNotIn(duplicate_sentinel, STATE_MODULE.state_json(state))

    def test_init_and_active_reject_unicode_separators_and_surrogates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, timeout=5)
            for unsafe in ("Route\u2028break", "Route\u2029break", "Route\ud800break"):
                with self.subTest(unsafe=ascii(unsafe)), self.assertRaises(SystemExit):
                    INIT_MODULE.main(["--root", str(repo), "--slug", "unsafe", "--destination", unsafe])
            with self.assertRaises(SystemExit):
                INIT_MODULE.main(
                    [
                        "--root",
                        str(repo),
                        "--slug",
                        "unsafe",
                        "--destination",
                        "Safe route",
                        "--expect-active",
                        "pointer\u2028forged",
                    ]
                )

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            pointer = f".codex/wayfinder/efforts/{effort.name}/MAP.md\u2028forged"
            (repo / ".codex" / "wayfinder" / "ACTIVE").write_text(pointer, encoding="utf-8")
            with self.assertRaises(STATE_MODULE.WayfinderError):
                build_state(repo)

    def test_unindexed_symlinks_are_not_parsed_and_unsafe_paths_are_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(ticket("D-001", "Route", "OPEN"), encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
            )
            outside_ticket = repo / "outside-D-999.md"
            outside_ticket.write_text(ticket("D-999", "Outside secret", "OPEN"), encoding="utf-8")
            (effort / "decisions" / "D-999.md").symlink_to(outside_ticket)
            outside_evidence = repo / "outside-E-003.md"
            outside_evidence.write_text("DO-NOT-READ-SENSITIVE-EVIDENCE", encoding="utf-8")
            (effort / "evidence" / "E-003.md").symlink_to(outside_evidence)
            source_evidence = effort / "evidence" / "E-001.md"
            (effort / "evidence" / "E-002.md").write_text(
                source_evidence.read_text(encoding="utf-8").replace("# E-001:", "# E-002:"),
                encoding="utf-8",
            )
            filename_sentinel = "UNSAFE-FILENAME-SENTINEL"
            (effort / "decisions" / f"D-996-\n{filename_sentinel}.md").write_text("DO-NOT-PARSE", encoding="utf-8")
            (effort / "evidence" / f"E-996-\n{filename_sentinel}.md").write_text("DO-NOT-PARSE", encoding="utf-8")
            manifest_path = effort / "EFFORT.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            absolute_sentinel = repo / "SECRET-ABSOLUTE-SENTINEL" / "ticket.md"
            manifest["decisions"].append(
                {"id": "D-998", "path": str(absolute_sentinel), "status": "OPEN", "phase_id": "p2-resolve", "destination_blocking": True}
            )
            control_path_sentinel = "CONTROL-PATH-SENTINEL"
            manifest["decisions"].append(
                {"id": "D-997", "path": f"decisions/D-997.md\n{control_path_sentinel}", "status": "OPEN", "phase_id": "p2-resolve", "destination_blocking": True}
            )
            manifest["evidence"].append(
                {"id": "E-998", "path": str(absolute_sentinel.parent / "evidence.md"), "subject_revision": 1}
            )
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

            state = build_state(repo)
            codes = {item["code"] for item in state["health"]["issues"]}
            self.assertNotIn("D-999", {node["id"] for node in state["nodes"]})
            self.assertTrue(
                {
                    "TICKET_PATH_ESCAPE",
                    "TICKET_NAME_INVALID",
                    "EVIDENCE_PATH_ESCAPE",
                    "EVIDENCE_NAME_INVALID",
                    "EVIDENCE_UNINDEXED",
                    "TICKET_PATH_INVALID",
                    "EVIDENCE_PATH_INVALID",
                }.issubset(codes),
                codes,
            )
            serialized = STATE_MODULE.state_json(state)
            self.assertNotIn(str(absolute_sentinel.parent), serialized)
            self.assertNotIn(control_path_sentinel, serialized)
            self.assertNotIn(filename_sentinel, serialized)
            self.assertNotIn("DO-NOT-READ-SENSITIVE-EVIDENCE", serialized)

    def test_exit_receipt_must_match_current_revision_hash_and_route_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(ticket("D-001", "Route", "RESOLVED"), encoding="utf-8")
            (effort / "gates" / "G-001.md").write_text(
                ticket("G-001", "Delivery proof", "DEFINED", "D-001", kind="GATE", post_build=True), encoding="utf-8"
            )
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve"}],
                [{"id": "G-001", "path": "gates/G-001.md", "status": "DEFINED", "phase_id": "p5-delivery", "post_build": True}],
                current_phase_id="p5-delivery",
            )
            write_valid_exit(effort)
            baseline = build_state(repo)
            self.assertTrue(baseline["exit"]["pre_spec_ready"])
            self.assertTrue(baseline["exit"]["complete"])

            exit_path = effort / "EXIT.md"
            malformed = exit_path.read_text(encoding="utf-8")
            malformed = malformed.replace("- **Destination revision:** 1", "- **Destination revision:** 2")
            malformed = malformed.replace(
                re.search(r"- \*\*Manifest hash:\*\* [0-9a-f]{64}", malformed).group(0),
                "- **Manifest hash:** " + ("0" * 64),
            )
            malformed = malformed.replace("| D-001 | Chosen verified route. | Example owner | E-001 | 1 |", "| D-001 | Different route. | Example owner | E-001 | 9 |")
            malformed = malformed.replace(
                "| G-001 | The exact release remains within the measured load budget. | Example owner | D-001 | M-005 | Destination revision changes. |",
                "| G-001 | Different condition. | Example owner | D-001 | M-005 | Destination revision changes. |",
            )
            exit_path.write_text(malformed, encoding="utf-8")

            state = build_state(repo)
            codes = {item["code"] for item in state["health"]["issues"]}
            self.assertTrue(state["exit"]["pre_spec_ready"])
            self.assertFalse(state["exit"]["complete"])
            self.assertEqual("invalid", state["exit"]["receipt"]["status"])
            self.assertTrue(
                {
                    "EXIT_REVISION_STALE",
                    "EXIT_MANIFEST_HASH_MISMATCH",
                    "EXIT_DECISION_ROW_CONFLICT",
                    "EXIT_GATE_ROW_CONFLICT",
                }.issubset(codes),
                codes,
            )

    def test_all_resolved_legacy_v2_requires_migration_before_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(
                ticket("D-001", "Legacy route", "RESOLVED", v3=False), encoding="utf-8"
            )
            state = build_state(repo)
            codes = {item["code"] for item in state["health"]["issues"]}
            self.assertFalse(state["exit"]["pre_spec_ready"])
            self.assertIn("MIGRATION_REQUIRED_FOR_COMPLETION", codes)
            complete = subprocess.run(
                [sys.executable, str(CLI), "complete", "--root", str(repo)],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(1, complete.returncode, complete.stdout + complete.stderr)
            self.assertIn("Migration required", complete.stdout)
            preview = subprocess.run(
                [sys.executable, str(CLI), "migrate", "--check", "--root", str(repo), "--json"],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
            self.assertTrue(json.loads(preview.stdout)["needed"])

    def test_cli_lifecycle_commands_fail_closed_for_unresolved_absent_manifest_v2(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(
                ticket("D-001", "Legacy unresolved route", "OPEN", v3=False), encoding="utf-8"
            )
            before = project_byte_snapshot(repo)

            for command in ("resume", "revalidate", "complete"):
                with self.subTest(command=command):
                    result = subprocess.run(
                        [sys.executable, str(CLI), command, "--root", str(repo)],
                        text=True,
                        capture_output=True,
                        check=False,
                        timeout=5,
                    )
                    self.assertEqual(1, result.returncode, result.stdout + result.stderr)
                    self.assertIn("Lifecycle command allowed: no", result.stdout)
                    self.assertIn("no schema-3 EFFORT.json", result.stdout)
                    self.assertNotIn("Recommendation: Continue", result.stdout)

            preview = subprocess.run(
                [sys.executable, str(CLI), "migrate", "--check", "--root", str(repo), "--json"],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
            payload = json.loads(preview.stdout)
            self.assertEqual("absent-legacy-v2", payload["manifest_state"])
            self.assertTrue(payload["needed"])
            self.assertTrue(payload["migration_required"])
            self.assertFalse(payload["recovery_required"])
            self.assertEqual(["EFFORT.json"], payload["would_write"])
            self.assertFalse((effort / "EFFORT.json").exists())
            self.assertEqual(before, project_byte_snapshot(repo))

    def test_cli_manifest_gate_is_independent_of_capped_public_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            map_path = effort / "MAP.md"
            map_path.write_text(
                map_path.read_text(encoding="utf-8").replace(
                    "## Frontier\n\n## Fog / Not Yet Specified",
                    "## Frontier\n\n| ID | Why actionable |\n| --- | --- |\n| D-0001 | Declared legacy frontier item. |\n\n## Fog / Not Yet Specified",
                ),
                encoding="utf-8",
            )
            for index in range(1, 1_006):
                node_id = f"D-{index:04d}"
                (effort / "decisions" / f"{node_id}.md").write_text(
                    ticket(node_id, f"Legacy route {index}", "OPEN", v3=False), encoding="utf-8"
                )

            state = build_state(repo)
            self.assertEqual("absent-legacy-v2", state["manifest_contract"]["state"])
            self.assertFalse(state["manifest_contract"]["lifecycle_ready"])
            self.assertNotIn("LEGACY_V2", {item["code"] for item in state["diagnostics"]})
            self.assertIn("DIAGNOSTIC_LIMIT", {item["code"] for item in state["diagnostics"]})

            result = subprocess.run(
                [sys.executable, str(CLI), "resume", "--root", str(repo)],
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertIn("Migration required", result.stdout)
            self.assertIn("Lifecycle command allowed: no", result.stdout)

    def test_cli_lifecycle_and_migration_preview_fail_closed_for_schema2_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(
                ticket("D-001", "Indexed route", "OPEN"), encoding="utf-8"
            )
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
            )
            manifest_path = effort / "EFFORT.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["schema_version"] = 2
            original = (json.dumps(manifest, indent=2) + "\n").encode()
            manifest_path.write_bytes(original)
            before = project_byte_snapshot(repo)

            for command in ("resume", "revalidate", "complete"):
                result = subprocess.run(
                    [sys.executable, str(CLI), command, "--root", str(repo)],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
                self.assertEqual(1, result.returncode, result.stdout + result.stderr)
                self.assertIn("Lifecycle command allowed: no", result.stdout)
                self.assertIn("not schema 3", result.stdout)

            preview = subprocess.run(
                [sys.executable, str(CLI), "migrate", "--check", "--root", str(repo), "--json"],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(1, preview.returncode, preview.stdout + preview.stderr)
            payload = json.loads(preview.stdout)
            self.assertEqual("unsupported-schema", payload["manifest_state"])
            self.assertTrue(payload["needed"])
            self.assertTrue(payload["migration_required"])
            self.assertTrue(payload["recovery_required"])
            self.assertEqual([], payload["would_write"])
            self.assertEqual(original, manifest_path.read_bytes())
            self.assertEqual(before, project_byte_snapshot(repo))

    def test_cli_lifecycle_and_migration_preview_fail_closed_for_malformed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(
                ticket("D-001", "Unreadable index route", "OPEN"), encoding="utf-8"
            )
            manifest_path = effort / "EFFORT.json"
            original = b'{"schema_version": 3, "private": "MALFORMED-SENTINEL"'
            manifest_path.write_bytes(original)
            before = project_byte_snapshot(repo)

            for command in ("resume", "revalidate", "complete"):
                result = subprocess.run(
                    [sys.executable, str(CLI), command, "--root", str(repo)],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
                self.assertEqual(1, result.returncode, result.stdout + result.stderr)
                self.assertIn("Lifecycle command allowed: no", result.stdout)
                self.assertIn("invalid or unsafe", result.stdout)
                self.assertNotIn("MALFORMED-SENTINEL", result.stdout + result.stderr)

            preview = subprocess.run(
                [sys.executable, str(CLI), "migrate", "--check", "--root", str(repo), "--json"],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(1, preview.returncode, preview.stdout + preview.stderr)
            payload = json.loads(preview.stdout)
            self.assertEqual("invalid-manifest", payload["manifest_state"])
            self.assertTrue(payload["needed"])
            self.assertFalse(payload["migration_required"])
            self.assertTrue(payload["recovery_required"])
            self.assertEqual([], payload["would_write"])
            self.assertNotIn("MALFORMED-SENTINEL", preview.stdout + preview.stderr)
            self.assertEqual(original, manifest_path.read_bytes())
            self.assertEqual(before, project_byte_snapshot(repo))

    def test_cli_lifecycle_commands_accept_a_healthy_valid_v3_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(
                ticket("D-001", "Proven route", "RESOLVED"), encoding="utf-8"
            )
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve"}],
                current_phase_id="p4-ready",
            )
            state = build_state(repo)
            self.assertEqual([], state["health"]["issues"])
            self.assertTrue(state["exit"]["pre_spec_ready"])
            before = project_byte_snapshot(repo)

            expected_output = {
                "resume": "Wayfinder:",
                "revalidate": "Wayfinder revalidation check",
                "complete": "Pre-spec exit eligible: yes",
            }
            for command, marker in expected_output.items():
                result = subprocess.run(
                    [sys.executable, str(CLI), command, "--root", str(repo)],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertIn(marker, result.stdout)
                self.assertNotIn("Lifecycle command allowed: no", result.stdout)

            doctor = subprocess.run(
                [sys.executable, str(CLI), "doctor", "--root", str(repo)],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(0, doctor.returncode, doctor.stdout + doctor.stderr)
            preview = subprocess.run(
                [sys.executable, str(CLI), "migrate", "--check", "--root", str(repo), "--json"],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
            payload = json.loads(preview.stdout)
            self.assertEqual("schema-3", payload["manifest_state"])
            self.assertFalse(payload["needed"])
            self.assertFalse(payload["migration_required"])
            self.assertFalse(payload["recovery_required"])
            self.assertFalse(payload["repair_required"])
            self.assertTrue(payload["doctor_passed"])
            self.assertEqual([], payload["would_write"])
            self.assertEqual(before, project_byte_snapshot(repo))

    def test_fresh_v3_placeholders_cannot_pass_destination_framing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, timeout=5)
            started = subprocess.run(
                [sys.executable, str(CLI), "start", "--root", str(repo), "--slug", "fresh", "--destination", "A concrete destination."],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(0, started.returncode, started.stdout + started.stderr)
            state = build_state(repo)
            codes = {item["code"] for item in state["health"]["issues"]}
            self.assertFalse(state["exit"]["pre_spec_ready"])
            self.assertTrue(
                {"MAP_SUCCESS_CONDITIONS_INVALID", "MAP_CONSTRAINTS_REQUIRED", "MAP_SCOPE_BOUNDARY_REQUIRED"}.issubset(codes),
                codes,
            )

    def test_active_invariants_and_settled_assumptions_are_proven_and_receipted_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(ticket("D-001", "Route", "RESOLVED"), encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve"}],
                current_phase_id="p4-ready",
            )
            (effort / "ASSUMPTIONS.md").write_text(
                "# Assumption Ledger\n\n"
                "| ID | Assumption | Impact | Confidence | Evidence | Status | Destination blocking | Blocks / affects | Revalidate when |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| A-001 | Capacity route holds. | HIGH | HIGH | E-001 | VALIDATED | true | Destination | Destination revision changes. |\n"
                "| A-002 | Scoped policy risk is accepted. | CRITICAL | MEDIUM | none | ACCEPTED-RISK | true | Destination | Policy scope changes. |\n\n"
                "## Accepted-risk receipts\n\n"
                "| Receipt | Assumption | Accepted by | Authority source | Accepted at | Exact scope | Expiry / revalidate when | Rationale |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| AR-001 | A-002 | Example approver | Product authority | 2026-08-22T00:00:00Z | Exact launch route | Policy scope changes. | Scoped risk accepted. |\n",
                encoding="utf-8",
            )
            invariant_path = effort / "INVARIANTS.md"
            invariant_path.write_text(
                "# Invariant Ledger\n\n"
                "| ID | Invariant | Scope | Rationale | Enforcement | Evidence | Status | Responsible party | Revalidate when |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| I-001 | Tenant isolation remains enforced. | Delivery | Prevent cross-tenant access. | CI isolation review. | E-001 | ACTIVE | Platform owner | Destination revision changes. |\n",
                encoding="utf-8",
            )
            write_valid_exit(effort)
            exit_path = effort / "EXIT.md"
            receipt = exit_path.read_text(encoding="utf-8")
            receipt = receipt.replace(
                "| --- | --- | --- | --- |\n\n## Active invariants",
                "| --- | --- | --- | --- |\n"
                "| A-001 | VALIDATED | E-001 | Destination revision changes. |\n"
                "| A-002 | ACCEPTED-RISK | AR-001 | Policy scope changes. |\n\n"
                "## Active invariants",
                1,
            )
            receipt = receipt.replace(
                "| --- | --- | --- | --- | --- |\n\n## Resolved destination-blocking Decisions",
                "| --- | --- | --- | --- | --- |\n"
                "| I-001 | Tenant isolation remains enforced. | CI isolation review. | E-001 | Destination revision changes. |\n\n"
                "## Resolved destination-blocking Decisions",
                1,
            )
            exit_path.write_text(receipt, encoding="utf-8")

            valid = build_state(repo)
            self.assertEqual([], valid["health"]["issues"])
            self.assertTrue(valid["exit"]["complete"])

            wrong_receipt = receipt.replace("| A-001 | VALIDATED | E-001 |", "| A-001 | VALIDATED | E-999 |")
            wrong_receipt = wrong_receipt.replace("| I-001 | Tenant isolation remains enforced. |", "| I-001 | Different invariant. |")
            exit_path.write_text(wrong_receipt, encoding="utf-8")
            mismatched = build_state(repo)
            mismatch_codes = {item["code"] for item in mismatched["health"]["issues"]}
            self.assertTrue(mismatched["exit"]["pre_spec_ready"])
            self.assertFalse(mismatched["exit"]["complete"])
            self.assertTrue({"EXIT_ASSUMPTION_ROW_CONFLICT", "EXIT_INVARIANT_ROW_CONFLICT"}.issubset(mismatch_codes), mismatch_codes)

            exit_path.write_text(receipt, encoding="utf-8")
            invariant_path.write_text(
                invariant_path.read_text(encoding="utf-8").replace("E-001 | ACTIVE | Platform owner", "E-999 | ACTIVE | Not assigned"),
                encoding="utf-8",
            )
            broken_ledger = build_state(repo)
            broken_codes = {item["code"] for item in broken_ledger["health"]["issues"]}
            self.assertFalse(broken_ledger["exit"]["pre_spec_ready"])
            self.assertTrue({"ACTIVE_INVARIANT_INCOMPLETE", "EVIDENCE_REFERENCE_MISSING"}.issubset(broken_codes), broken_codes)

            invariant_path.write_text(
                invariant_path.read_text(encoding="utf-8").replace("E-999 | ACTIVE | Not assigned", "E-001 | ACTIVE | Platform owner"),
                encoding="utf-8",
            )
            (effort / "ASSUMPTIONS.md").write_text(
                (effort / "ASSUMPTIONS.md").read_text(encoding="utf-8").replace("| AR-001 | A-002 | Example approver |", "| AR-001 | A-002 | Not recorded |"),
                encoding="utf-8",
            )
            invalid_acceptance = build_state(repo)
            self.assertIn("ACCEPTED_RISK_RECEIPT_INVALID", {item["code"] for item in invalid_acceptance["health"]["issues"]})

    def test_artifact_directory_symlinks_are_rejected_without_enumeration(self) -> None:
        for directory_name, expected_code, sentinel_name in (
            ("decisions", "TICKET_DIRECTORY_ESCAPE", "D-777-SECRET-EXTERNAL.md"),
            ("gates", "TICKET_DIRECTORY_ESCAPE", "G-777-SECRET-EXTERNAL.md"),
            ("evidence", "EVIDENCE_DIRECTORY_ESCAPE", "E-777-SECRET-EXTERNAL.md"),
        ):
            with self.subTest(directory_name=directory_name), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                effort = create_effort(repo)
                write_manifest(effort, [])
                artifact_dir = effort / directory_name
                for child in artifact_dir.iterdir():
                    child.unlink()
                artifact_dir.rmdir()
                external = repo / f"external-{directory_name}"
                external.mkdir()
                (external / sentinel_name).write_text("SENSITIVE-DIRECTORY-CONTENT", encoding="utf-8")
                artifact_dir.symlink_to(external, target_is_directory=True)

                state = build_state(repo)
                serialized = STATE_MODULE.state_json(state)
                self.assertIn(expected_code, {item["code"] for item in state["health"]["issues"]})
                self.assertNotIn(sentinel_name, serialized)
                self.assertNotIn("SENSITIVE-DIRECTORY-CONTENT", serialized)

    def test_fifo_artifacts_fail_fast_as_non_regular_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, timeout=5)
            base = repo / ".codex" / "wayfinder"
            (base / "efforts").mkdir(parents=True)
            os.mkfifo(base / "ACTIVE")
            for command in (
                [sys.executable, str(CLI), "status", "--root", str(repo), "--json"],
                [sys.executable, str(CLI), "start", "--root", str(repo), "--slug", "fifo", "--destination", "Safe route"],
            ):
                result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=3)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("regular file", result.stderr)
            self.assertFalse((base / "efforts" / "fifo").exists())

        for fixed_name, expected_code in (
            ("EFFORT.json", "MANIFEST_INVALID"),
            ("ASSUMPTIONS.md", "ASSUMPTIONS_INVALID"),
            ("INVARIANTS.md", "INVARIANTS_INVALID"),
            ("EXIT.md", "EXIT_RECEIPT_INVALID"),
        ):
            with self.subTest(fixed_name=fixed_name), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                effort = create_effort(repo)
                (effort / "decisions" / "D-001.md").write_text(ticket("D-001", "Route", "OPEN"), encoding="utf-8")
                write_manifest(
                    effort,
                    [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
                )
                target = effort / fixed_name
                if target.exists():
                    target.unlink()
                os.mkfifo(target)
                result = subprocess.run(
                    [sys.executable, str(CLI), "status", "--root", str(repo), "--json"],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=3,
                )
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                state = json.loads(result.stdout)
                self.assertIn(expected_code, {item["code"] for item in state["health"]["issues"]})

    def test_symlinked_metadata_roots_and_fixed_artifacts_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            for target in ("wayfinder", "efforts"):
                repo = workspace / f"repo-{target}"
                external = workspace / f"external-{target}"
                repo.mkdir()
                external.mkdir()
                subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, timeout=5)
                if target == "wayfinder":
                    (repo / ".codex").mkdir()
                    (repo / ".codex" / "wayfinder").symlink_to(external, target_is_directory=True)
                else:
                    base = repo / ".codex" / "wayfinder"
                    base.mkdir(parents=True)
                    (base / "efforts").symlink_to(external, target_is_directory=True)
                with self.assertRaises(STATE_MODULE.WayfinderError):
                    build_state(repo)

        for fixed_name, expected_code in (
            ("ASSUMPTIONS.md", "ASSUMPTIONS_PATH_ESCAPE"),
            ("INVARIANTS.md", "FIXED_ARTIFACT_PATH_ESCAPE"),
            ("EFFORT.json", "MANIFEST_INVALID"),
        ):
            with self.subTest(fixed_name=fixed_name), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                effort = create_effort(repo)
                (effort / "decisions" / "D-001.md").write_text(ticket("D-001", "Route", "OPEN"), encoding="utf-8")
                write_manifest(
                    effort,
                    [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
                )
                outside = repo / f"outside-{fixed_name}"
                outside.write_text("SENSITIVE-FIXED-CONTENT", encoding="utf-8")
                path = effort / fixed_name
                path.unlink()
                path.symlink_to(outside)
                state = build_state(repo)
                self.assertIn(expected_code, {item["code"] for item in state["health"]["issues"]})
                self.assertNotIn("SENSITIVE-FIXED-CONTENT", STATE_MODULE.state_json(state))

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            outside_map = repo / "outside-MAP.md"
            outside_map.write_text("SENSITIVE-MAP-CONTENT", encoding="utf-8")
            (effort / "MAP.md").unlink()
            (effort / "MAP.md").symlink_to(outside_map)
            with self.assertRaises(STATE_MODULE.WayfinderError):
                build_state(repo)

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo, "direct")
            nested = effort.parent / "nested" / "direct"
            nested.mkdir(parents=True)
            (nested / "MAP.md").write_text((effort / "MAP.md").read_text(encoding="utf-8"), encoding="utf-8")
            with self.assertRaises(STATE_MODULE.WayfinderError):
                build_state(repo, ".codex/wayfinder/efforts/nested/direct")

    def test_server_is_loopback_read_only_and_leaves_no_project_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(
                ticket("D-001", "Route choice", "OPEN"), encoding="utf-8"
            )
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
            )
            before = sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*") if path.is_file())
            try:
                server = make_server(repo, port=0, quiet=True)
            except PermissionError as exc:
                # Some hermetic CI/sandbox profiles prohibit even loopback socket
                # binds. The same test exercises the full server when loopback is
                # available; do not weaken the production bind policy to evade it.
                self.skipTest(f"sandbox prohibits loopback sockets: {exc}")
            self.assertEqual("127.0.0.1", server.server_address[0])
            launch_url = SERVER_MODULE.dashboard_url(server)
            parsed_launch = urlsplit(launch_url)
            capability_root = parsed_launch.path.rstrip("/")
            self.assertEqual("127.0.0.1", parsed_launch.hostname)
            self.assertEqual(server.server_address[1], parsed_launch.port)
            self.assertGreaterEqual(len(server.wayfinder_capability), 32)
            self.assertEqual(f"/{server.wayfinder_capability}", capability_root)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                connection.request("GET", "/api/state")
                response = connection.getresponse()
                unauthenticated_body = response.read().decode("utf-8")
                self.assertEqual(404, response.status)
                self.assertNotIn("schema_version", unauthenticated_body)
                self.assertEqual("nosniff", response.getheader("X-Content-Type-Options"))
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                connection.request("GET", f"{capability_root}/api/state")
                response = connection.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(200, response.status)
                self.assertEqual(3, payload["schema_version"])
                self.assertEqual("nosniff", response.getheader("X-Content-Type-Options"))
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                connection.request("HEAD", f"{capability_root}/")
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                self.assertEqual(b"", response.read())
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                connection.request("POST", "/api/state", body=b"{}")
                response = connection.getresponse()
                self.assertEqual(404, response.status)
                self.assertIsNone(response.getheader("Allow"))
                response.read()
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                connection.request("POST", f"{capability_root}/api/state", body=b"{}")
                response = connection.getresponse()
                self.assertEqual(405, response.status)
                self.assertEqual("GET, HEAD", response.getheader("Allow"))
                response.read()
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                connection.request("PROPFIND", f"{capability_root}/api/state")
                response = connection.getresponse()
                self.assertEqual(405, response.status)
                self.assertEqual("GET, HEAD", response.getheader("Allow"))
                self.assertEqual("nosniff", response.getheader("X-Content-Type-Options"))
                response.read()
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                connection.request("PROPFIND", f"{capability_root}/api/state", headers={"Host": "attacker.example"})
                response = connection.getresponse()
                self.assertEqual(421, response.status)
                self.assertEqual("nosniff", response.getheader("X-Content-Type-Options"))
                response.read()
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                connection.request("GET", f"{capability_root}/%2e%2e/wayfinder.py")
                response = connection.getresponse()
                self.assertEqual(404, response.status)
                response.read()
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                connection.request("GET", f"{capability_root}/api/state", headers={"Host": "attacker.example"})
                response = connection.getresponse()
                self.assertEqual(421, response.status)
                self.assertEqual("nosniff", response.getheader("X-Content-Type-Options"))
                response.read()
                connection.close()

                original_build_state = SERVER_MODULE.build_state
                state_error_output = io.StringIO()
                state_error_sentinel = "PRIVATE-STATE-ERROR-SENTINEL"
                try:
                    SERVER_MODULE.build_state = mock.Mock(
                        side_effect=RuntimeError(state_error_sentinel)
                    )
                    with mock.patch("sys.stderr", state_error_output):
                        connection = http.client.HTTPConnection(
                            "127.0.0.1", server.server_address[1], timeout=3
                        )
                        connection.request("GET", f"{capability_root}/api/state")
                        response = connection.getresponse()
                        error_payload = response.read().decode("utf-8")
                        self.assertEqual(500, response.status)
                        self.assertEqual("nosniff", response.getheader("X-Content-Type-Options"))
                        self.assertEqual(
                            {"error": "Wayfinder state is unavailable"},
                            json.loads(error_payload),
                        )
                        self.assertNotIn(state_error_sentinel, error_payload)
                        connection.close()
                finally:
                    SERVER_MODULE.build_state = original_build_state
                self.assertEqual("", state_error_output.getvalue())

                asset_error_output = io.StringIO()
                asset_error_sentinel = "PRIVATE-ASSET-ERROR-SENTINEL"
                original_read_bytes = Path.read_bytes

                def fail_dashboard_asset(path: Path) -> bytes:
                    if path.name == "app.js":
                        raise RuntimeError(asset_error_sentinel)
                    return original_read_bytes(path)

                with mock.patch.object(Path, "read_bytes", fail_dashboard_asset), mock.patch(
                    "sys.stderr", asset_error_output
                ):
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", server.server_address[1], timeout=3
                    )
                    connection.request("GET", f"{capability_root}/app.js")
                    response = connection.getresponse()
                    asset_error_payload = response.read().decode("utf-8")
                    self.assertEqual(500, response.status)
                    self.assertEqual("nosniff", response.getheader("X-Content-Type-Options"))
                    self.assertEqual("Wayfinder dashboard asset is unavailable\n", asset_error_payload)
                    self.assertNotIn(asset_error_sentinel, asset_error_payload)
                    connection.close()
                self.assertEqual("", asset_error_output.getvalue())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

            log_server = make_server(repo, port=0, quiet=False)
            log_thread = threading.Thread(target=log_server.serve_forever, daemon=True)
            log_thread.start()
            log_output = io.StringIO()
            try:
                with mock.patch("sys.stderr", log_output):
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", log_server.server_address[1], timeout=3
                    )
                    capability = log_server.wayfinder_capability
                    encoded_capability = f"%{ord(capability[0]):02X}{capability[1:]}"
                    connection.request(
                        "GET", f"/{encoded_capability}/api/state"
                    )
                    response = connection.getresponse()
                    self.assertEqual(200, response.status)
                    response.read()
                    connection.close()
            finally:
                log_server.shutdown()
                log_server.server_close()
                log_thread.join(timeout=3)
            logged = log_output.getvalue()
            self.assertIn("GET 200", logged)
            self.assertNotIn(capability, logged)
            self.assertNotIn(capability[1:], logged)
            self.assertNotIn(encoded_capability, logged)
            self.assertNotIn("/api/state", logged)

            after = sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*") if path.is_file())
            self.assertEqual(before, after)
            self.assertFalse((effort / "dashboard").exists())
            self.assertFalse((repo / ".codex" / "wayfinder" / "dashboard").exists())

    def test_init_transaction_restores_exact_active_bytes_at_pointer_fsync_seam(self) -> None:
        for prior in (None, b".codex/wayfinder/efforts/original/MAP.md  \n\n"):
            with self.subTest(prior=prior), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, timeout=5)
                wayfinder = repo / ".codex" / "wayfinder"
                (wayfinder / "efforts").mkdir(parents=True)
                if prior is not None:
                    (wayfinder / "ACTIVE").write_bytes(prior)

                real_fsync = os.fsync
                calls = 0

                def fail_after_active_rename(descriptor: int) -> None:
                    nonlocal calls
                    calls += 1
                    # staging directory, efforts directory, ACTIVE temp file,
                    # then the Wayfinder directory after ACTIVE was renamed.
                    if calls == 4:
                        raise OSError("injected ACTIVE directory fsync failure")
                    real_fsync(descriptor)

                arguments = [
                    "--root",
                    str(repo),
                    "--slug",
                    "transactional",
                    "--destination",
                    "A rollback-safe route.",
                ]
                if prior is not None:
                    arguments.extend(["--expect-active", prior.decode("utf-8").strip()])
                with mock.patch.object(INIT_MODULE.os, "fsync", side_effect=fail_after_active_rename):
                    with self.assertRaises(SystemExit):
                        INIT_MODULE.main(arguments)

                active = wayfinder / "ACTIVE"
                if prior is None:
                    self.assertFalse(active.exists())
                else:
                    self.assertEqual(prior, active.read_bytes())
                self.assertFalse((wayfinder / "efforts" / "transactional").exists())
                self.assertFalse((wayfinder / "ACTIVE.lock").exists())

    def test_gate_is_delivery_only_and_waiver_receipt_triggers_cp5_revalidation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            decision_path = effort / "decisions" / "D-001.md"
            gate_path = effort / "gates" / "G-001.md"
            decision_path.write_text(ticket("D-001", "Route", "RESOLVED"), encoding="utf-8")
            gate_path.write_text(
                ticket("G-001", "Scoped delivery waiver", "WAIVED", "D-001", kind="GATE", post_build=True),
                encoding="utf-8",
            )
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve"}],
                [{"id": "G-001", "path": "gates/G-001.md", "status": "WAIVED", "phase_id": "p5-delivery", "post_build": True}],
                current_phase_id="p5-delivery",
            )

            valid = build_state(repo)
            self.assertEqual([], valid["health"]["issues"])
            self.assertEqual("p5-delivery", valid["current_phase"]["id"])
            self.assertEqual(["G-001"], valid["views"]["revalidation"])
            self.assertEqual("cp5-revalidate", valid["run_recommendation"]["checkpoint_id"])
            self.assertTrue(valid["run_recommendation"]["recommended"])

            invalid_waiver = gate_path.read_text(encoding="utf-8").replace(
                "| Release owner | Release owner | 2026-08-22T00:00:00Z |",
                "| Codex | Different authority | 2026-08-22T00:00:00Z |",
            )
            gate_path.write_text(invalid_waiver, encoding="utf-8")
            invalid = build_state(repo)
            self.assertIn("GATE_WAIVER_RECEIPT_INVALID", {item["code"] for item in invalid["health"]["issues"]})

            gate_path.write_text(
                ticket("G-001", "Scoped delivery waiver", "WAIVED", "D-001", kind="GATE", post_build=False),
                encoding="utf-8",
            )
            manifest_path = effort / "EFFORT.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["gates"][0]["post_build"] = False
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            not_delivery = build_state(repo)
            self.assertIn("GATE_POST_BUILD_REQUIRED", {item["code"] for item in not_delivery["health"]["issues"]})

    def test_gate_checks_and_evaluation_receipt_are_unique_legal_and_status_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(ticket("D-001", "Route", "RESOLVED"), encoding="utf-8")
            gate_path = effort / "gates" / "G-001.md"
            gate_path.write_text(ticket("G-001", "Delivery check", "PASSED", "D-001", kind="GATE"), encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve"}],
                [{"id": "G-001", "path": "gates/G-001.md", "status": "PASSED", "phase_id": "p5-delivery", "post_build": True}],
                current_phase_id="p5-delivery",
            )
            self.assertEqual([], build_state(repo)["health"]["issues"])

            malformed = gate_path.read_text(encoding="utf-8").replace(
                "| C-001 | COMMAND | The load probe passes. | Saved E-001 result. | PASSED |",
                "| C-001 | COMMAND | The load probe passes. | Saved E-001 result. | PENDING |\n"
                "| C-001 | COMMAND | The load probe passes. | Saved E-001 result. | INVENTED |",
            )
            malformed = malformed.replace("| PASSED | E-001 | 1 | Gate evaluation recorded. |", "| FAILED | E-001 | 1 | Gate evaluation recorded. |")
            gate_path.write_text(malformed, encoding="utf-8")
            codes = {item["code"] for item in build_state(repo)["health"]["issues"]}
            self.assertTrue(
                {"GATE_CHECK_DUPLICATE", "GATE_CHECK_STATUS_INVALID", "GATE_PASS_CHECK_CONFLICT", "GATE_EVALUATION_RECEIPT_INVALID"}.issubset(codes),
                codes,
            )

    def test_dependent_inspection_binds_current_revision_and_latest_transition_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            decision_path = effort / "decisions" / "D-001.md"
            decision_path.write_text(
                ticket("D-001", "Changed route", "RESOLVED").replace("- **Revision:** 1", "- **Revision:** 2"),
                encoding="utf-8",
            )
            (effort / "decisions" / "D-002.md").write_text(ticket("D-002", "Dependent", "RESOLVED", "D-001"), encoding="utf-8")
            write_manifest(
                effort,
                [
                    {"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve"},
                    {"id": "D-002", "path": "decisions/D-002.md", "status": "RESOLVED", "phase_id": "p2-resolve"},
                ],
                current_phase_id="p4-ready",
            )
            stale_receipt = (
                "## Dependent inspections\n\n"
                "| Trigger | Dependent | Outcome | Evidence | Actor | Timestamp |\n"
                "| --- | --- | --- | --- | --- | --- |\n"
                "| D-001 revision 1 | D-002 | STILL-VALID | E-001 | Example | 2026-08-21T00:00:00Z |\n\n"
            )
            decision_path.write_text(
                decision_path.read_text(encoding="utf-8").replace("## Append-only transition history", stale_receipt + "## Append-only transition history"),
                encoding="utf-8",
            )
            self.assertIn("DEPENDENT_INSPECTION_REQUIRED", {item["code"] for item in build_state(repo)["health"]["issues"]})

            current = decision_path.read_text(encoding="utf-8").replace("revision 1", "revision 2").replace("2026-08-21T00:00:00Z", "2026-08-22T00:00:00Z")
            decision_path.write_text(current, encoding="utf-8")
            self.assertNotIn("DEPENDENT_INSPECTION_REQUIRED", {item["code"] for item in build_state(repo)["health"]["issues"]})

            decision_path.write_text(current.replace("2026-08-22T00:00:00Z |\n\n## Append", "2099-08-22T00:00:00Z |\n\n## Append", 1), encoding="utf-8")
            self.assertIn("DEPENDENT_INSPECTION_REQUIRED", {item["code"] for item in build_state(repo)["health"]["issues"]})

    def test_refuted_blocking_assumption_requires_targeted_current_decision_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(ticket("D-001", "Route", "RESOLVED"), encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve"}],
                current_phase_id="p4-ready",
            )
            assumptions = (
                "# Assumption Ledger\n\n"
                "| ID | Assumption | Impact | Confidence | Evidence | Status | Destination blocking | Blocks / affects | Revalidate when |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| A-001 | The old route premise holds. | CRITICAL | HIGH | E-001 | REFUTED | true | D-001 | Destination revision changes. |\n\n"
                "## Refutation receipts\n\n"
                "| Assumption | Decision | Outcome | Evidence | Actor | Timestamp | Decision revision |\n"
                "| --- | --- | --- | --- | --- | --- | --- |\n"
            )
            path = effort / "ASSUMPTIONS.md"
            path.write_text(assumptions, encoding="utf-8")
            missing = build_state(repo)
            self.assertIn("REFUTED_ASSUMPTION_INSPECTION_REQUIRED", {item["code"] for item in missing["health"]["issues"]})

            path.write_text(assumptions + "| A-001 | D-001 | STILL-VALID | E-001 | Example | 2026-08-22T00:00:00Z | 1 |\n", encoding="utf-8")
            valid = build_state(repo)
            self.assertNotIn("REFUTED_ASSUMPTION_INSPECTION_REQUIRED", {item["code"] for item in valid["health"]["issues"]})
            self.assertTrue(valid["exit"]["pre_spec_ready"])

            path.write_text(path.read_text(encoding="utf-8").replace("| 1 |", "| 2 |"), encoding="utf-8")
            self.assertIn("REFUTED_ASSUMPTION_INSPECTION_REQUIRED", {item["code"] for item in build_state(repo)["health"]["issues"]})

    def test_ticket_kind_and_all_overlapping_manifest_detail_conflicts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            path = effort / "decisions" / "D-001.md"
            path.write_text(ticket("D-001", "Canonical route", "OPEN").replace("- **Kind:** DECISION", "- **Kind:** GATE"), encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
            )
            kind_state = build_state(repo)
            self.assertIn("TICKET_KIND_CONFLICT", {item["code"] for item in kind_state["health"]["issues"]})
            self.assertEqual("decision", kind_state["nodes"][0]["kind"])

            path.write_text(ticket("D-001", "Canonical route", "OPEN"), encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
            )
            manifest_path = effort / "EFFORT.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["decisions"][0].update(
                {
                    "title": "Manifest route",
                    "question": "A different question?",
                    "status": "BLOCKED",
                    "type": "PROTOTYPE",
                    "responsible_party": "Different owner",
                    "requires": ["D-999"],
                }
            )
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            conflict = build_state(repo)
            self.assertIn("MANIFEST_TICKET_CONFLICT", {item["code"] for item in conflict["health"]["issues"]})
            node = conflict["nodes"][0]
            self.assertEqual("Canonical route", node["title"])
            self.assertEqual("OPEN", node["status"])
            self.assertEqual([], node["requires"])

    def test_manifest_typed_edge_index_must_exactly_match_markdown_before_union(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(ticket("D-001", "Prerequisite", "RESOLVED"), encoding="utf-8")
            (effort / "decisions" / "D-002.md").write_text(ticket("D-002", "Dependent", "OPEN", "D-001"), encoding="utf-8")
            write_manifest(
                effort,
                [
                    {"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve"},
                    {"id": "D-002", "path": "decisions/D-002.md", "status": "OPEN", "phase_id": "p2-resolve"},
                ],
            )
            manifest_path = effort / "EFFORT.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["edges"] = [
                edge for edge in manifest["edges"] if not (edge["from"] == "D-002" and edge["type"] == "requires")
            ]
            manifest["edges"].append({"from": "D-001", "type": "requires", "to": "D-999"})
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

            state = build_state(repo)
            self.assertIn("EDGE_INDEX_MISMATCH", {item["code"] for item in state["health"]["issues"]})
            self.assertIn({"source": "D-002", "type": "requires", "target": "D-001"}, state["edges"])
            self.assertNotIn({"source": "D-001", "type": "requires", "target": "D-999"}, state["edges"])

    def test_effort_identity_and_lifecycle_order_are_validated_but_payload_is_derived(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(ticket("D-001", "Route", "OPEN"), encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
                current_phase_id="p2-resolve",
            )
            manifest_path = effort / "EFFORT.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["effort"]["id"] = "different-effort"
            manifest["phases"][0]["state"] = "upcoming"
            manifest["phases"][2]["state"] = "complete"
            manifest["checkpoints"][0]["status"] = "UPCOMING"
            manifest["milestones"][0]["status"] = "PENDING"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

            state = build_state(repo)
            codes = {item["code"] for item in state["health"]["issues"]}
            self.assertTrue(
                {"EFFORT_ID_DIRECTORY_CONFLICT", "PHASE_ORDER_INVALID", "CHECKPOINT_ORDER_INVALID", "MILESTONE_ORDER_INVALID"}.issubset(codes),
                codes,
            )
            self.assertEqual("launch", state["project"]["slug"])
            self.assertEqual(["complete", "active", "upcoming"], [phase["state"] for phase in state["phases"][:3]])
            self.assertEqual(["complete", "pending"], [milestone["state"] for milestone in state["milestones"][:2]])

    def test_exit_unknown_set_is_exact_and_full_exit_defers_nonblocking_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(ticket("D-001", "Blocking route", "RESOLVED"), encoding="utf-8")
            nonblocking = ticket("D-002", "Deferred choice", "OPEN").replace("- **Destination blocking:** true", "- **Destination blocking:** false")
            (effort / "decisions" / "D-002.md").write_text(nonblocking, encoding="utf-8")
            write_manifest(
                effort,
                [
                    {"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve", "destination_blocking": True},
                    {"id": "D-002", "path": "decisions/D-002.md", "status": "OPEN", "phase_id": "p2-resolve", "destination_blocking": False},
                ],
                current_phase_id="p5-delivery",
            )
            (effort / "ASSUMPTIONS.md").write_text(
                "# Assumption Ledger\n\n"
                "| ID | Assumption | Impact | Confidence | Evidence | Status | Destination blocking | Blocks / affects | Revalidate when |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| A-001 | A deferred minor detail. | LOW | MEDIUM | none | OPEN | false | Local detail | Detail becomes material. |\n",
                encoding="utf-8",
            )
            write_valid_exit(effort)
            mismatch = build_state(repo)
            self.assertTrue(mismatch["exit"]["pre_spec_ready"])
            self.assertFalse(mismatch["exit"]["complete"])
            self.assertIn("EXIT_UNKNOWN_SET_MISMATCH", {item["code"] for item in mismatch["health"]["issues"]})

            exit_path = effort / "EXIT.md"
            exit_path.write_text(
                exit_path.read_text(encoding="utf-8").replace("- None.", "- D-002 — deferred choice.\n- A-001 — deferred minor assumption."),
                encoding="utf-8",
            )
            complete = build_state(repo)
            self.assertTrue(complete["exit"]["complete"])
            self.assertEqual([], complete["views"]["actionable"])
            self.assertEqual(["D-002"], complete["views"]["deferred"])
            self.assertEqual(["A-001", "D-002"], complete["exit"]["remaining_nonblocking_unknowns"])
            self.assertEqual("dormant", complete["run_recommendation"]["level"])

    def test_evidence_subject_revision_syntax_is_exact_and_bound_to_effort_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(ticket("D-001", "Route", "RESOLVED"), encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve"}],
                current_phase_id="p4-ready",
            )
            evidence = effort / "evidence" / "E-001.md"
            baseline = evidence.read_text(encoding="utf-8")
            evidence.write_text(baseline.replace("launch / 1", "other-effort / 1"), encoding="utf-8")
            conflict_codes = {item["code"] for item in build_state(repo)["health"]["issues"]}
            self.assertIn("EVIDENCE_SUBJECT_EFFORT_CONFLICT", conflict_codes)

            evidence.write_text(baseline.replace("launch / 1", "launch revision 1"), encoding="utf-8")
            syntax = build_state(repo)
            self.assertFalse(syntax["exit"]["pre_spec_ready"])
            self.assertIn("EVIDENCE_SUBJECT_SYNTAX_INVALID", {item["code"] for item in syntax["health"]["issues"]})

    def test_large_ticket_relationships_and_public_edges_fail_closed_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            path = effort / "decisions" / "D-001.md"
            path.write_text(ticket("D-001", "Bounded graph", "OPEN"), encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
            )
            huge_requires = ", ".join(f"D-{index:06d}" for index in range(100_000, 200_000))
            path.write_text(
                path.read_text(encoding="utf-8").replace("- **Requires:** None", f"- **Requires:** {huge_requires}"),
                encoding="utf-8",
            )

            state = build_state(repo)
            self.assertIn("NODE_RELATIONSHIP_LIMIT", {item["code"] for item in state["health"]["issues"]})
            self.assertEqual([], state["nodes"][0]["requires"])
            self.assertLess(len(STATE_MODULE.state_json(state)), 250_000)

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            (effort / "decisions" / "D-001.md").write_text(ticket("D-001", "First", "RESOLVED"), encoding="utf-8")
            (effort / "decisions" / "D-002.md").write_text(ticket("D-002", "Second", "OPEN", "D-001"), encoding="utf-8")
            write_manifest(
                effort,
                [
                    {"id": "D-001", "path": "decisions/D-001.md", "status": "RESOLVED", "phase_id": "p2-resolve"},
                    {"id": "D-002", "path": "decisions/D-002.md", "status": "OPEN", "phase_id": "p2-resolve"},
                ],
            )
            with mock.patch.object(STATE_MODULE, "MAX_PUBLIC_EDGES", 0):
                state = build_state(repo)
            self.assertEqual([], state["edges"])
            self.assertIn("PUBLIC_EDGE_LIMIT", {item["code"] for item in state["health"]["issues"]})

    def test_rejected_artifact_values_are_nonreflective_in_public_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            effort = create_effort(repo)
            decision_path = effort / "decisions" / "D-001.md"
            gate_path = effort / "gates" / "G-001.md"
            decision_path.write_text(ticket("D-001", "Canonical route", "OPEN"), encoding="utf-8")
            gate_path.write_text(ticket("G-001", "Delivery proof", "DEFINED", kind="GATE"), encoding="utf-8")
            write_manifest(
                effort,
                [{"id": "D-001", "path": "decisions/D-001.md", "status": "OPEN", "phase_id": "p2-resolve"}],
                [
                    {
                        "id": "G-001",
                        "path": "gates/G-001.md",
                        "status": "DEFINED",
                        "phase_id": "p5-delivery",
                        "post_build": True,
                    }
                ],
            )
            write_valid_exit(effort)

            sentinels = {
                "PRIVATE-CONFLICT-SENTINEL",
                "PRIVATE-EVIDENCE-SENTINEL",
                "PRIVATE-STATUS-SENTINEL",
                "PRIVATE-AUTONOMY-SENTINEL",
                "PRIVATE-PHASE-SENTINEL",
                "PRIVATE-TRANSITION-SENTINEL",
                "PRIVATE-CHECK-SENTINEL",
                "PRIVATE-UPDATED-AT-SENTINEL",
            }
            manifest_path = effort / "EFFORT.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["decisions"][0]["title"] = "PRIVATE-CONFLICT-SENTINEL"
            manifest["effort"]["updated_at"] = "PRIVATE-UPDATED-AT-SENTINEL"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

            decision_text = decision_path.read_text(encoding="utf-8")
            decision_text = decision_text.replace("- **Status:** OPEN", "- **Status:** PRIVATE-STATUS-SENTINEL")
            decision_text = decision_text.replace("- **Autonomy:** HYBRID", "- **Autonomy:** PRIVATE-AUTONOMY-SENTINEL")
            decision_text = decision_text.replace("- **Phase:** p2-resolve", "- **Phase:** PRIVATE-PHASE-SENTINEL")
            decision_text = decision_text.replace("| — | OPEN |", "| — | PRIVATE-TRANSITION-SENTINEL |")
            decision_path.write_text(decision_text, encoding="utf-8")

            gate_path.write_text(
                gate_path.read_text(encoding="utf-8").replace(
                    "| C-001 | COMMAND |", "| C-001 | PRIVATE-CHECK-SENTINEL |"
                ),
                encoding="utf-8",
            )
            evidence_path = effort / "evidence" / "E-001.md"
            evidence_path.write_text(
                evidence_path.read_text(encoding="utf-8").replace(
                    "- **Confidence:** HIGH", "- **Confidence:** PRIVATE-EVIDENCE-SENTINEL"
                ),
                encoding="utf-8",
            )
            rejected_hash = "a17e" * 16
            exit_path = effort / "EXIT.md"
            exit_path.write_text(
                re.sub(
                    r"(?m)^- \*\*Manifest hash:\*\* [0-9a-f]{64}$",
                    f"- **Manifest hash:** {rejected_hash}",
                    exit_path.read_text(encoding="utf-8"),
                ),
                encoding="utf-8",
            )

            state = build_state(repo)
            serialized = STATE_MODULE.state_json(state)
            for sentinel in sentinels | {rejected_hash}:
                self.assertNotIn(sentinel, serialized)
            decision = next(node for node in state["nodes"] if node["id"] == "D-001")
            self.assertEqual("INVALID", decision["status"])
            self.assertEqual("INVALID", decision["autonomy"])
            self.assertEqual("unknown", decision["phase"])
            self.assertEqual("current status is not actionable", decision["waiting_reason"])
            self.assertEqual("", state["exit"]["receipt"]["manifest_hash"])


if __name__ == "__main__":
    unittest.main()
