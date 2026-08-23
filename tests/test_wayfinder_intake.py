from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "wayfinder" / "scripts"


def load_local(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CLI = load_local("_wayfinder_cli_intake_tests", SCRIPTS / "wayfinder.py")
INTAKE = CLI._INTAKE
INIT = CLI.init_wayfinder
STATE = CLI._STATE


def project_byte_snapshot(root: Path) -> dict[str, bytes]:
    """Capture all local project files without following symbolic links."""
    snapshot: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = b"SYMLINK\0" + os.fsencode(os.readlink(path))
        elif path.is_file():
            snapshot[relative] = path.read_bytes()
    return snapshot


class WayfinderIntakeTests(unittest.TestCase):
    actor = "Example Owner"

    def initialize(self, root: Path, slug: str = "sample") -> Path:
        with redirect_stdout(io.StringIO()):
            result = INIT.main(
                [
                    "--root",
                    str(root),
                    "--slug",
                    slug,
                    "--destination",
                    "An initial destination awaiting structured intake.",
                ]
            )
        self.assertEqual(0, result)
        return root / ".codex" / "wayfinder" / "efforts" / slug

    def start_intake(self, root: Path, intent: str = "Build a secure web application") -> tuple[Path, dict]:
        effort = self.initialize(root)
        state = INTAKE.start_intake(root, None, intent)
        self.assertEqual("Q-001", state["intake"]["current_question"]["id"])
        self.assertEqual(1, state["intake"]["revision"])
        return effort, state

    def choose(self, root: Path, state: dict, choice: str, *, source: str = "CHAT") -> dict:
        question = state["intake"]["current_question"]
        self.assertEqual("choice", question["answer_type"])
        self.assertIsNotNone(question["decision_id"])
        return INTAKE.record_intake_choice(
            root,
            None,
            question["decision_id"],
            state["intake"]["revision"],
            self.actor,
            source,
            choice,
        )

    def frame_domain(self, root: Path, state: dict, domain: str) -> dict:
        state = self.choose(root, state, domain)
        answers = {
            "Q-002": "Invited users complete onboarding and reach their workspace.",
            "Q-003": "Ten invited users finish onboarding without operator assistance.",
            "Q-004": "A signed local acceptance report covering all ten journeys.",
            "Q-005": "No deployment or external write without Example Owner approval.",
            "Q-006": "Public launch, billing, and production deployment are outside this effort.",
            "Q-007": self.actor,
        }
        for question_id, answer in answers.items():
            self.assertEqual(question_id, state["intake"]["current_question"]["id"])
            state = INTAKE.record_intake_answer(
                root,
                None,
                question_id,
                state["intake"]["revision"],
                self.actor,
                "CHAT",
                answer,
            )
        return state

    def frame_software(self, root: Path, state: dict) -> dict:
        return self.frame_domain(root, state, "SOFTWARE")

    def software_to_architecture_choice(self, root: Path, state: dict) -> dict:
        state = self.frame_software(root, state)
        for question_id, choice in (
            ("Q-SW-001", "WEB-APP"),
            ("Q-SW-002", "BALANCED"),
            ("Q-SW-003", "GROWING"),
            ("Q-SW-004", "INTERNAL-DATA"),
        ):
            self.assertEqual(question_id, state["intake"]["current_question"]["id"])
            state = self.choose(root, state, choice)
        for question_id, answer in (
            ("Q-SW-005", "The project is a new service with no reusable application baseline."),
            ("Q-SW-006", "It integrates with the existing identity and reporting interfaces."),
            ("Q-SW-007", "The delivery team is strongest in Python and browser standards."),
            ("Q-SW-008", "Aisha Khan owns day-two operations and incident response."),
            ("Q-SW-009", "The first usable release is required within six weeks."),
            ("Q-SW-010", "Prefer reversible services and a bounded monthly operating cost."),
        ):
            self.assertEqual(question_id, state["intake"]["current_question"]["id"])
            state = INTAKE.record_intake_answer(
                root,
                None,
                question_id,
                state["intake"]["revision"],
                self.actor,
                "CHAT",
                answer,
            )
        self.assertEqual("Q-SW-011", state["intake"]["current_question"]["id"])
        return state

    def finance_with_owned_unknown_complete(self, root: Path) -> tuple[Path, dict]:
        effort, state = self.start_intake(root, "Prepare controlled regulatory statutory reports")
        state = self.frame_domain(root, state, "FINANCE_REPORTING")
        for choice in ("REGULATORY", "ACCOUNTING-SYSTEM", "AUDITABILITY"):
            state = self.choose(root, state, choice)
        facts = {
            "Q-FR-004": "UNKNOWN: the statutory reporting jurisdiction is not confirmed; OWNER: Aisha Khan",
            "Q-FR-005": "IFRS subject to qualified review of local presentation requirements.",
            "Q-FR-006": "Calendar-year reporting with the approved cutoff policy.",
            "Q-FR-007": "AED presentation with the approved statutory materiality threshold.",
            "Q-FR-008": "The controlled ledger and source owners provide retained lineage.",
            "Q-FR-009": "Every material balance is reconciled and exceptions are retained.",
            "Q-FR-010": "Preparer, reviewer, access, and change roles are segregated.",
            "Q-FR-011": "A qualified statutory-reporting reviewer signs the retained close package.",
            "Q-FR-012": "Auditable statutory statements with retained workpapers are required.",
            "Q-FR-013": "The statutory filing deadline cannot move.",
        }
        for question_id, answer in facts.items():
            self.assertEqual(question_id, state["intake"]["current_question"]["id"])
            state = INTAKE.record_intake_answer(
                root, None, question_id, state["intake"]["revision"], self.actor, "CHAT", answer
            )
        self.assertEqual("Q-FR-014", state["intake"]["current_question"]["id"])
        state = self.choose(root, state, "FR-LEDGER-LED")
        self.assertEqual("COMPLETE", state["intake"]["status"])
        self.assertFalse(state["exit"]["planning_exit_ready"])
        return effort, state

    @staticmethod
    def technology_options() -> list[dict]:
        common = {
            "mvp_speed": "Supports a bounded MVP route.",
            "scale_beyond_mvp": "Has a documented growth path beyond the first release.",
            "reliability": "Supports explicit failure handling and recovery ownership.",
            "efficiency": "Keeps delivery and operating work visible.",
            "cost": "Requires a separately approved cost decision before purchase.",
            "complexity": "Has a moderate, inspectable complexity envelope.",
            "lock_in": "Uses explicit boundaries so replacement remains possible.",
            "security_privacy": "Requires access control and data-classification validation.",
            "team_fit": "Matches the recorded small cross-functional team.",
            "reversibility": "Can be replaced behind the recorded component boundary.",
        }
        return [
            {
                "id": "TECH-001",
                "name": "Python standard-library service",
                "version_or_constraint": "Python 3 compatible standard library",
                "summary": "A small service using only host-provided Python capabilities.",
                **common,
                "recommendation": True,
                "rationale": "Best fits the bounded local route and avoids an unapproved dependency commitment.",
                "evidence_refs": [],
                "primary_sources": ["https://docs.python.org/3/"],
            },
            {
                "id": "TECH-002",
                "name": "Node native-fetch service",
                "version_or_constraint": "A supported Node release with native fetch",
                "summary": "A small service using the runtime HTTP and web-platform APIs.",
                **common,
                "recommendation": False,
                "rationale": "Credible when the delivery team has stronger Node operating experience.",
                "evidence_refs": [],
                "primary_sources": ["https://nodejs.org/docs/latest/api/globals.html"],
            },
        ]

    def test_classifier_is_domain_neutral_and_requires_explicit_selection(self) -> None:
        cases = (
            ("Coordinate a partner initiative with a useful outcome.", "OTHER", True),
            ("Build a SaaS web app with an API, backend, and database.", "SOFTWARE", False),
            ("Renovate a construction facility with a contractor at the building site.", "GENERAL_PROJECT", False),
            ("Improve month-end accounting reconciliation and financial reporting.", "FINANCE_REPORTING", False),
        )
        for intent, expected_domain, expected_ambiguous in cases:
            with self.subTest(intent=intent):
                result = INTAKE.classify_intent(intent)
                self.assertEqual(expected_domain, result["proposed"])
                self.assertEqual(expected_ambiguous, result["ambiguous"])
                self.assertIsNone(result["selected"])
                self.assertIsNone(result["primary_domain"])
                self.assertIsNone(result["selection_source"])

    def test_classifier_uses_word_boundaries_and_exposes_hybrid_confirmation(self) -> None:
        collisions = INTAKE.classify_intent("Prevent capital leakage during a partner initiative.")
        self.assertEqual("OTHER", collisions["proposed"])
        self.assertNotIn("API", collisions["signals"])
        self.assertNotIn("EVENT", collisions["signals"])

        hybrid = INTAKE.classify_intent("Automate MONTH-END close reconciliation.")
        self.assertEqual("FINANCE_REPORTING", hybrid["proposed"])
        self.assertTrue(hybrid["hybrid_candidate"])
        self.assertTrue(hybrid["ambiguous"])
        self.assertIn("SOFTWARE", hybrid["suggested_secondary_domains"])

    def test_hybrid_workstream_can_be_recorded_immediately_without_losing_primary_question(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _effort, state = self.start_intake(root, "Automate month-end close reconciliation.")
            self.assertTrue(state["intake"]["secondary_confirmation"]["recommended"])
            state = self.choose(root, state, "FINANCE_REPORTING")
            self.assertEqual("Q-002", state["intake"]["current_question"]["id"])
            self.assertTrue(state["intake"]["secondary_confirmation"]["can_record_now"])

            state = INTAKE.add_secondary_workstream(
                root,
                None,
                state["intake"]["revision"],
                self.actor,
                "CHAT",
                "SOFTWARE",
                "A controlled close automation produces reviewable reconciliations.",
                "Aisha Khan",
            )
            self.assertEqual("Q-002", state["intake"]["current_question"]["id"])
            self.assertEqual("FINANCE_REPORTING", state["intake"]["domain"]["primary_domain"])
            workstream = state["intake"]["domain"]["secondary_workstreams"][0]
            self.assertEqual("SOFTWARE", workstream["domain"])
            self.assertIn("Q-SW-011", workstream["required_questions"])
            receipt = state["intake"]["receipts"][-1]
            self.assertEqual("Q-HY-001", receipt["question_id"])
            self.assertEqual((2, 3), (receipt["old_revision"], receipt["new_revision"]))
            self.assertTrue(workstream["decision_ids"])

            for question_id, answer in (
                ("Q-002", "The month-end close produces controlled reconciliations and review evidence."),
                ("Q-003", "Every material account is reconciled and signed off by the deadline."),
                ("Q-004", "A retained close package shows source, reconciliation, review, and signoff."),
                ("Q-005", "No posting, purchase, or deployment occurs without Example Owner approval."),
                ("Q-006", "Statutory filing and production deployment are outside this planning effort."),
                ("Q-007", self.actor),
            ):
                self.assertEqual(question_id, state["intake"]["current_question"]["id"])
                state = INTAKE.record_intake_answer(
                    root, None, question_id, state["intake"]["revision"], self.actor, "CHAT", answer
                )
            for choice in ("MONTH-END", "MULTIPLE-SYSTEMS", "AUDITABILITY"):
                state = self.choose(root, state, choice)
            for question_id in (
                "Q-FR-004", "Q-FR-005", "Q-FR-006", "Q-FR-007", "Q-FR-008",
                "Q-FR-009", "Q-FR-010", "Q-FR-011", "Q-FR-012", "Q-FR-013",
            ):
                state = INTAKE.record_intake_answer(
                    root,
                    None,
                    question_id,
                    state["intake"]["revision"],
                    self.actor,
                    "CHAT",
                    f"Finance readiness fact for {question_id} has an accountable owner.",
                )
            finance = next(item for item in state["intake"]["comparisons"] if item["id"] == "CMP-FR-001")
            state = self.choose(root, state, finance["recommended_option"])
            for choice in ("API-AUTOMATION", "BALANCED", "GROWING", "INTERNAL-DATA"):
                state = self.choose(root, state, choice)
            for question_id in ("Q-SW-005", "Q-SW-006", "Q-SW-007", "Q-SW-008", "Q-SW-009", "Q-SW-010"):
                state = INTAKE.record_intake_answer(
                    root,
                    None,
                    question_id,
                    state["intake"]["revision"],
                    self.actor,
                    "CHAT",
                    f"Software readiness fact for {question_id} is explicit and reviewable.",
                )
            architecture = next(item for item in state["intake"]["comparisons"] if item["id"] == "CMP-SW-001")
            state = self.choose(root, state, architecture["recommended_option"])
            state = INTAKE.propose_technology_options(
                root,
                None,
                state["intake"]["revision"],
                "Wayfinder research",
                "CLI",
                self.technology_options(),
            )
            state = self.choose(root, state, "TECH-001")
            self.assertEqual("COMPLETE", state["intake"]["status"])
            self.assertIsNone(state["intake"]["current_question"])
            self.assertEqual(
                {"FINANCE_REPORTING", "SOFTWARE"},
                {state["intake"]["domain"]["primary_domain"], state["intake"]["domain"]["secondary_workstreams"][0]["domain"]},
            )
            ids = [item["decision_id"] for item in state["intake"]["decision_bindings"]]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertIn(
                next(item["decision_id"] for item in state["intake"]["decision_bindings"] if item["question_id"] == "Q-SW-012"),
                state["intake"]["domain"]["secondary_workstreams"][0]["decision_ids"],
            )

    def test_nonsoftware_comparisons_use_domain_specific_criteria_and_no_selection(self) -> None:
        construction = INTAKE._general_comparison(
            {"project_priority": "SAFETY-QUALITY", "project_uncertainty": "HIGH-UNCERTAINTY"}
        )
        accounting = INTAKE._finance_comparison(
            {"finance_sources": "MULTIPLE-SYSTEMS", "finance_priority": "AUDITABILITY"}
        )
        self.assertEqual("GENERAL_PROJECT", construction["domain"])
        self.assertEqual("FINANCE_REPORTING", accounting["domain"])
        self.assertIn("safety_quality", construction["criteria"])
        self.assertIn("regulatory_dependency", construction["criteria"])
        self.assertIn("auditability", accounting["criteria"])
        self.assertIn("reconciliation_effort", accounting["criteria"])
        for comparison in (construction, accounting):
            self.assertIsNone(comparison["selected_option"])
            self.assertEqual("CONDITIONAL", comparison["recommendation_status"])
            self.assertIsNone(comparison["recommended_option"])
            self.assertEqual(0, sum(item["recommendation"] for item in comparison["options"]))
            self.assertIn("No route is recommended", comparison["recommendation_rationale"])
            self.assertTrue(all(item["rationale"] for item in comparison["options"]))

    def test_non_git_cli_init_can_start_a_fresh_resumable_intake(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse((root / ".git").exists())
            output = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                initialized = CLI.main(
                    [
                        "init",
                        "--root",
                        str(root),
                        "--slug",
                        "non-git-project",
                        "--destination",
                        "A local project reaches an observable result.",
                    ]
                )
                started = CLI.main(
                    [
                        "intake",
                        "start",
                        "--root",
                        str(root),
                        "--intent",
                        "Plan a construction renovation with a contractor.",
                    ]
                )
            self.assertEqual(0, initialized, errors.getvalue())
            self.assertEqual(0, started, errors.getvalue())
            self.assertFalse((root / ".git").exists())
            state = STATE.build_state(root)
            intake = state["intake"]
            self.assertEqual("AVAILABLE", intake["state"])
            self.assertEqual("AWAITING_HUMAN_CHOICE", intake["status"])
            self.assertEqual("GENERAL_PROJECT", intake["domain"]["proposed"])
            self.assertEqual("Q-001", intake["current_question"]["id"])
            self.assertEqual("D-001", intake["current_question"]["decision_id"])
            self.assertTrue(
                (root / ".codex" / "wayfinder" / "efforts" / "non-git-project" / "INTAKE.json").is_file()
            )

    def test_project_root_falls_back_without_git_and_rejects_files_before_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_file = root / "not-a-project-directory"
            project_file.write_text("plain file\n", encoding="utf-8")
            with mock.patch.object(INIT.subprocess, "run", side_effect=FileNotFoundError):
                self.assertEqual(root.resolve(), INIT.git_root(root))
            with mock.patch.object(STATE.subprocess, "run", side_effect=FileNotFoundError):
                self.assertEqual(root.resolve(), STATE.git_root(root))
            with mock.patch.object(STATE.subprocess, "run") as run:
                with self.assertRaises(STATE.WayfinderError):
                    STATE.git_root(project_file)
                run.assert_not_called()

    def test_explicit_choice_creates_stable_decision_evidence_and_typed_edge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            effort, state = self.start_intake(root, "Plan a construction renovation")
            self.assertTrue((effort / "decisions" / "D-001.md").is_file())
            state = self.choose(root, state, "GENERAL_PROJECT", source="CLI")

            manifest = json.loads((effort / "EFFORT.json").read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    "id": "D-001",
                    "path": "decisions/D-001.md",
                    "status": "RESOLVED",
                    "phase_id": "p2-resolve",
                    "destination_blocking": False,
                },
                manifest["decisions"][0],
            )
            self.assertEqual(
                {"id": "E-001", "path": "evidence/E-001.md", "subject_revision": 1},
                manifest["evidence"][0],
            )
            self.assertEqual([{"from": "E-001", "type": "informs", "to": "D-001"}], manifest["edges"])
            decision = (effort / "decisions" / "D-001.md").read_text(encoding="utf-8")
            evidence = (effort / "evidence" / "E-001.md").read_text(encoding="utf-8")
            self.assertIn("- **Status:** RESOLVED", decision)
            self.assertIn("- **Informs:** E-001", decision)
            self.assertIn("explicitly selected General project (GENERAL_PROJECT)", decision)
            self.assertIn("- **Method:** OBSERVATION", evidence)
            self.assertIn("- **Decisions affected:** D-001", evidence)
            self.assertIn("explicitly selected General project (GENERAL_PROJECT) for D-001", evidence)
            self.assertEqual([{"source": "E-001", "target": "D-001", "type": "informs"}], state["edges"])
            self.assertEqual("RESOLVED", next(node for node in state["nodes"] if node["id"] == "D-001")["status"])
            self.assertEqual("E-001", state["intake"]["receipts"][0]["evidence_id"])

    def test_custom_domain_option_selects_the_other_branch_and_preserves_revision_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            effort, state = self.start_intake(root, "Plan a research-led public programme")
            question = state["intake"]["current_question"]
            state = INTAKE.propose_intake_alternative(
                root,
                None,
                question["decision_id"],
                state["intake"]["revision"],
                self.actor,
                "CHAT",
                {
                    "id": "RESEARCH-PROGRAMME",
                    "label": "Research programme",
                    "description": "A research-led route outside the predefined domain labels.",
                },
            )
            state = self.choose(root, state, "RESEARCH-PROGRAMME")
            self.assertEqual("OTHER", state["intake"]["domain"]["primary_domain"])
            self.assertEqual("RESEARCH-PROGRAMME", state["intake"]["domain"]["selected_option"])
            self.assertEqual("Q-002", state["intake"]["current_question"]["id"])
            decision_text = (effort / "decisions" / f"{question['decision_id']}.md").read_text(encoding="utf-8")
            self.assertIn("- **Revision:** 2", decision_text)
            self.assertIn("## Option revision history", decision_text)
            self.assertIn("| OPEN | RESOLVED |", decision_text)

    def test_framing_answers_advance_one_at_a_time_and_leave_healthy_p2_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            effort, state = self.start_intake(root)
            state = self.frame_software(root, state)

            self.assertEqual("p2-resolve", state["current_phase"]["id"])
            self.assertEqual([], state["health"]["issues"])
            self.assertTrue(state["manifest_contract"]["doctor_passed"])
            self.assertEqual("Q-SW-001", state["intake"]["current_question"]["id"])
            self.assertEqual("D-002", state["intake"]["current_question"]["decision_id"])
            self.assertEqual(8, state["intake"]["revision"])
            self.assertEqual(7, state["intake"]["progress"]["answered"])
            self.assertEqual(7, len(state["intake"]["receipts"]))
            for index, receipt in enumerate(state["intake"]["receipts"], 1):
                self.assertEqual(f"IR-{index:04d}", receipt["receipt_id"])
                self.assertEqual((index, index + 1), (receipt["old_revision"], receipt["new_revision"]))
            baseline = state["implementation_baseline"]
            self.assertEqual("sample", baseline["effort_id"])
            self.assertEqual(8, baseline["intake_revision"])
            self.assertEqual("SOFTWARE", baseline["primary_domain"])
            self.assertEqual(["D-001"], [item["id"] for item in baseline["applicable_decisions"]])

            manifest = json.loads((effort / "EFFORT.json").read_text(encoding="utf-8"))
            map_text = (effort / "MAP.md").read_text(encoding="utf-8")
            self.assertEqual("p2-resolve", manifest["current_phase_id"])
            self.assertEqual("Invited users complete onboarding and reach their workspace.", manifest["effort"]["destination"])
            self.assertIn("Ten invited users finish onboarding without operator assistance.", map_text)
            self.assertIn("A signed local acceptance report covering all ten journeys.", map_text)
            self.assertIn("Material route choices require approval by Example Owner.", map_text)
            self.assertIn("## Fog / not yet formulated", map_text)
            self.assertIn("No unformulated fog remains.", map_text)
            self.assertNotIn("{{OBSERVABLE_SUCCESS}}", map_text)

    def test_evidence_satisfaction_has_agent_provenance_and_never_records_a_choice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _effort, state = self.start_intake(root, "Renovate a construction facility.")
            state = self.frame_domain(root, state, "GENERAL_PROJECT")
            for choice in ("CONSTRUCTION", "SAFETY-QUALITY", "PARTLY-DEFINED"):
                state = self.choose(root, state, choice)
            self.assertEqual("Q-GP-004", state["intake"]["current_question"]["id"])
            (root / "site-survey.txt").write_text("Dubai site boundary confirmed.\n", encoding="utf-8")
            before_decisions = list(state["intake"]["decision_bindings"])

            state = INTAKE.record_intake_evidence_answer(
                root,
                None,
                "Q-GP-004",
                state["intake"]["revision"],
                "Wayfinder evidence review",
                "CLI",
                "The operating site is the surveyed Dubai facility",
                "site-survey.txt",
            )
            answer = state["intake"]["answers"][-1]
            receipt = state["intake"]["receipts"][-1]
            self.assertEqual("ESTABLISHED", answer["readiness"])
            self.assertEqual("site-survey.txt", answer["support"])
            self.assertEqual("EVIDENCE", receipt["kind"])
            self.assertEqual("Wayfinder evidence review", receipt["actor"])
            self.assertEqual(before_decisions, state["intake"]["decision_bindings"])
            self.assertEqual("Q-GP-005", state["intake"]["current_question"]["id"])

    def test_exit_baseline_exactly_freezes_intake_and_nonblocking_domain_choice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            effort, state = self.start_intake(root, "Renovate a construction facility.")
            state = self.frame_domain(root, state, "GENERAL_PROJECT")
            for choice in ("CONSTRUCTION", "SAFETY-QUALITY", "PARTLY-DEFINED"):
                state = self.choose(root, state, choice)
            for question_id in (
                "Q-GP-004", "Q-GP-005", "Q-GP-006", "Q-GP-007", "Q-GP-008",
                "Q-GP-009", "Q-GP-010", "Q-GP-011", "Q-GP-012",
            ):
                state = INTAKE.record_intake_answer(
                    root,
                    None,
                    question_id,
                    state["intake"]["revision"],
                    self.actor,
                    "CHAT",
                    f"Recorded project fact for {question_id} with accountable ownership.",
                )
            state = self.choose(root, state, state["intake"]["comparisons"][0]["recommended_option"])
            self.assertEqual("COMPLETE", state["intake"]["status"])
            self.assertTrue(state["exit"]["planning_exit_ready"])

            manifest_bytes = (effort / "EFFORT.json").read_bytes()
            manifest = json.loads(manifest_bytes)
            manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
            decisions = [
                node for node in state["nodes"]
                if node["kind"] == "decision" and node["destination_blocking"] and node["status"] == "RESOLVED"
            ]
            decision_rows = "\n".join(
                f"| {node['id']} | {node['resolution']} | {node['decision_authority']} | "
                f"{', '.join(node['evidence'])} | {node['revision']} |"
                for node in decisions
            )
            baseline = state["implementation_baseline"]
            baseline_pairs = ", ".join(
                f"{item['id']}@{item['revision']}" for item in baseline["applicable_decisions"]
            )
            self.assertIn("D-001@1", baseline_pairs, "the nonblocking primary-domain choice is material")
            completed_at = INTAKE._now()
            exit_text = f"""# Wayfinder Completion Receipt: Sample

- **Effort:** sample
- **Schema:** 3
- **Receipt status:** CURRENT
- **Destination revision:** {manifest['effort']['destination_revision']}
- **Completed at:** {completed_at}
- **Completed by:** {self.actor}
- **Manifest hash:** {manifest_hash}

## Destination accepted for execution planning

{manifest['effort']['destination']}

## Success conditions

| ID | Observable condition | Route evidence | Status |
| --- | --- | --- | --- |
| SC-001 | Ten invited users finish onboarding without operator assistance. | E-001 | CURRENT |

## Resolved destination-blocking Decisions

| ID | Resolution | Decision authority | Evidence | Revision |
| --- | --- | --- | --- | --- |
{decision_rows}

## Validated assumptions and accepted risks

| Assumption | Status | Evidence or accepted-risk receipt | Revalidate when |
| --- | --- | --- | --- |

## Active invariants

| ID | Invariant | Enforcement | Evidence | Revalidate when |
| --- | --- | --- | --- | --- |

## Delivery Gates defined for later evaluation

| ID | Delivery condition | Responsible party | Revalidates Decisions | Gates milestone | Freshness rule |
| --- | --- | --- | --- | --- | --- |

## Remaining non-blocking unknowns

- None.

## Revalidation triggers

- Revalidate if the destination, site facts, or route changes materially.

## Execution baseline and handoff

- **Primary domain:** GENERAL_PROJECT
- **Recommended next workflow:** work breakdown, schedule, and delivery controls
- **Effort ID:** sample
- **Manifest hash:** {manifest_hash}
- **Destination revision:** {manifest['effort']['destination_revision']}
- **Intake revision:** {state['intake']['revision']}
- **Applicable Decision revisions:** {baseline_pairs}
- **Primary map:** .codex/wayfinder/efforts/sample/MAP.md
- **Decision index:** .codex/wayfinder/efforts/sample/decisions
- **Evidence index:** .codex/wayfinder/efforts/sample/evidence

## Completion validation

- [x] Destination decisions are terminal.
- [x] Fog is clear.
- [x] Assumptions are settled.
- [x] Dependents were inspected.
- [x] Evidence is fresh.
- [x] Gates are defined if required.
- [x] Execution planning can proceed.
"""
            (effort / "EXIT.md").write_text(exit_text, encoding="utf-8")
            completed = STATE.build_state(root)
            self.assertEqual([], completed["health"]["issues"])
            self.assertTrue(completed["exit"]["receipt"]["valid"])
            self.assertTrue(completed["exit"]["complete"])
            self.assertEqual(
                baseline["applicable_decisions"],
                [
                    {**item, "status": next(node["status"] for node in state["nodes"] if node["id"] == item["id"])}
                    for item in completed["exit"]["receipt"]["implementation_baseline"]["applicable_decisions"]
                ],
            )

    def test_fact_readiness_contract_accepts_three_explicit_forms_and_rejects_shortcuts(self) -> None:
        established = INTAKE._validate_fact_answer(
            "ESTABLISHED: Dubai operating site confirmed; EVIDENCE: local permit register E-024"
        )
        unknown = INTAKE._validate_fact_answer(
            "UNKNOWN: final inspection date is not confirmed; OWNER: Aisha Khan"
        )
        not_applicable = INTAKE._validate_fact_answer(
            "N/A: This internal reporting effort has no physical-site permit requirement"
        )
        self.assertEqual(
            {
                "readiness": "ESTABLISHED",
                "detail": "Dubai operating site confirmed",
                "support": "local permit register E-024",
            },
            established,
        )
        self.assertEqual("UNKNOWN", unknown["readiness"])
        self.assertEqual("Aisha Khan", unknown["support"])
        self.assertEqual("NOT_APPLICABLE", not_applicable["readiness"])
        self.assertIn("no physical-site permit", not_applicable["detail"])

        invalid = (
            "ESTABLISHED: Dubai operating site confirmed",
            "UNKNOWN: final inspection date is not confirmed; OWNER: assistant",
            "UNKNOWN: final inspection date is not confirmed; OWNER: unassigned",
            "N/A: N/A",
            "ESTABLISHED: site confirmed; EVIDENCE: local register\u202e",
        )
        for answer in invalid:
            with self.subTest(answer=ascii(answer)):
                with self.assertRaises(INTAKE.IntakeError) as raised:
                    INTAKE._validate_fact_answer(answer)
                self.assertEqual(422, raised.exception.http_status)

    def test_owned_regulatory_unknown_revalidation_is_append_only_and_requires_new_route_choice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            effort, state = self.finance_with_owned_unknown_complete(root)
            before_revision = state["intake"]["revision"]
            original_answer = next(
                item for item in state["intake"]["answers"] if item["question_id"] == "Q-FR-004"
            )
            original_receipt = next(
                item
                for item in state["intake"]["receipts"]
                if item["question_id"] == "Q-FR-004" and item["kind"] == "FACT"
            )
            prior_binding = next(
                item for item in state["intake"]["decision_bindings"] if item["question_id"] == "Q-FR-014"
            )
            prior_comparison = next(
                item for item in state["intake"]["comparisons"] if item["id"] == "CMP-FR-001"
            )
            prior_comparison_raw = next(
                item
                for item in json.loads((effort / "INTAKE.json").read_text(encoding="utf-8"))["comparisons"]
                if item["id"] == "CMP-FR-001"
            )

            unresolved_bytes = project_byte_snapshot(root)
            with self.assertRaises(INTAKE.IntakeError) as still_unknown:
                INTAKE.revalidate_intake_fact(
                    root,
                    None,
                    "Q-FR-004",
                    before_revision,
                    self.actor,
                    "CHAT",
                    "Unknown whether the statutory reporting jurisdiction has been confirmed.",
                )
            self.assertEqual("INTAKE_VALIDATION", still_unknown.exception.code)
            self.assertEqual(unresolved_bytes, project_byte_snapshot(root))

            output = io.StringIO()
            with redirect_stdout(output):
                result = CLI.main(
                    [
                        "intake", "revalidate-fact", "--root", str(root), "--question-id", "Q-FR-004",
                        "--expect-revision", str(before_revision), "--actor", self.actor, "--source", "CHAT",
                        "--answer", "United Arab Emirates statutory reporting jurisdiction is confirmed.", "--json",
                    ]
                )
            self.assertEqual(0, result)
            revised = json.loads(output.getvalue())
            self.assertEqual(before_revision + 1, revised["intake"]["revision"])
            self.assertEqual("AWAITING_HUMAN_CHOICE", revised["intake"]["status"])
            current = revised["intake"]["current_question"]
            self.assertEqual("Q-RV-001", current["id"])
            self.assertEqual(prior_binding["decision_id"], current["revalidates"])
            self.assertFalse(revised["exit"]["planning_exit_ready"])
            self.assertIn("Q-RV-001", revised["intake"]["readiness"]["blocking_questions"])

            self.assertEqual(
                original_answer,
                next(item for item in revised["intake"]["answers"] if item["question_id"] == "Q-FR-004"),
            )
            self.assertIn(original_receipt, revised["intake"]["receipts"])
            history = revised["intake"]["fact_revalidations"]
            self.assertEqual(1, len(history))
            self.assertEqual("FRV-0001", history[0]["id"])
            self.assertEqual(original_answer, history[0]["previous_answer"])
            self.assertEqual(original_receipt["receipt_id"], history[0]["supersedes_receipt_id"])
            self.assertEqual(prior_comparison_raw, history[0]["prior_comparison"])
            self.assertEqual("HUMAN_ANSWERED", history[0]["replacement"]["readiness"])
            receipt = revised["intake"]["receipts"][-1]
            self.assertEqual("FACT_REVALIDATED", receipt["kind"])
            self.assertEqual(history[0]["receipt_id"], receipt["receipt_id"])
            effective = next(
                item for item in revised["intake"]["effective_facts"] if item["question_id"] == "Q-FR-004"
            )
            self.assertTrue(effective["revalidated"])
            self.assertEqual("HUMAN_ANSWERED", effective["readiness"])
            self.assertEqual(receipt["receipt_id"], effective["effective_receipt_id"])

            new_decision_id = current["decision_id"]
            new_node = next(item for item in revised["nodes"] if item["id"] == new_decision_id)
            self.assertEqual([prior_binding["decision_id"]], new_node["revalidates"])
            self.assertIn(
                {"source": new_decision_id, "target": prior_binding["decision_id"], "type": "revalidates"},
                revised["edges"],
            )
            active_comparison = next(
                item for item in revised["intake"]["comparisons"] if item["id"] == "CMP-FR-001"
            )
            self.assertNotEqual(prior_comparison["facts_digest"], active_comparison["facts_digest"])
            self.assertIsNone(active_comparison["selected_option"])

            after_revalidation = project_byte_snapshot(root)
            with self.assertRaises(INTAKE.IntakeError) as stale:
                INTAKE.revalidate_intake_fact(
                    root, None, "Q-FR-004", before_revision, self.actor, "CHAT", "A stale answer must not win."
                )
            self.assertEqual("INTAKE_REVISION_CONFLICT", stale.exception.code)
            self.assertEqual(after_revalidation, project_byte_snapshot(root))

            completed = INTAKE.record_intake_choice(
                root,
                None,
                new_decision_id,
                revised["intake"]["revision"],
                self.actor,
                "CHAT",
                "FR-DATA-LAYER",
            )
            self.assertEqual("COMPLETE", completed["intake"]["status"])
            self.assertTrue(completed["intake"]["readiness"]["exit_ready"])
            self.assertTrue(completed["exit"]["planning_exit_ready"])
            active_comparison = next(
                item for item in completed["intake"]["comparisons"] if item["id"] == "CMP-FR-001"
            )
            self.assertEqual("FR-DATA-LAYER", active_comparison["selected_option"])
            baseline_ids = {item["id"] for item in completed["implementation_baseline"]["applicable_decisions"]}
            self.assertIn(prior_binding["decision_id"], baseline_ids)
            self.assertIn(new_decision_id, baseline_ids)
            self.assertEqual("RESOLVED", next(item for item in completed["nodes"] if item["id"] == prior_binding["decision_id"])["status"])
            self.assertEqual("RESOLVED", next(item for item in completed["nodes"] if item["id"] == new_decision_id)["status"])
            self.assertTrue((effort / "decisions" / f"{new_decision_id}.md").is_file())
            completed_bytes = project_byte_snapshot(root)
            with self.assertRaises(INTAKE.IntakeError) as repeated:
                INTAKE.revalidate_intake_fact(
                    root,
                    None,
                    "Q-FR-004",
                    completed["intake"]["revision"],
                    self.actor,
                    "CHAT",
                    "A second edit must not replace the now-effective answer.",
                )
            self.assertEqual("INTAKE_NOT_READY", repeated.exception.code)
            self.assertEqual(completed_bytes, project_byte_snapshot(root))

    def test_fact_revalidation_evidence_pointer_and_atomic_rollback_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _effort, state = self.finance_with_owned_unknown_complete(root)
            unsafe = root / "jurisdiction-proof.txt"
            outside = Path(directory).parent / f"wayfinder-proof-{os.getpid()}.txt"
            outside.write_text("external proof", encoding="utf-8")
            unsafe.symlink_to(outside)
            before = project_byte_snapshot(root)
            try:
                with self.assertRaises(INTAKE.IntakeError) as escaped:
                    INTAKE.revalidate_intake_fact(
                        root,
                        None,
                        "Q-FR-004",
                        state["intake"]["revision"],
                        "Wayfinder evidence review",
                        "CLI",
                        "United Arab Emirates statutory jurisdiction is confirmed.",
                        "jurisdiction-proof.txt",
                    )
                self.assertEqual("INTAKE_VALIDATION", escaped.exception.code)
                self.assertEqual(before, project_byte_snapshot(root))
            finally:
                unsafe.unlink()
                outside.unlink()

            unsafe.write_text("Approved local jurisdiction register.", encoding="utf-8")
            established = INTAKE.revalidate_intake_fact(
                root,
                None,
                "Q-FR-004",
                state["intake"]["revision"],
                "Wayfinder evidence review",
                "CLI",
                "United Arab Emirates statutory jurisdiction is confirmed.",
                "jurisdiction-proof.txt",
            )
            effective = next(
                item for item in established["intake"]["effective_facts"] if item["question_id"] == "Q-FR-004"
            )
            self.assertEqual("ESTABLISHED", effective["readiness"])
            self.assertEqual("jurisdiction-proof.txt", effective["support"])
            self.assertEqual("AWAITING_HUMAN_CHOICE", established["intake"]["status"])
            established = self.choose(root, established, "FR-LEDGER-LED")
            self.assertTrue(established["exit"]["planning_exit_ready"])
            unsafe.unlink()
            replacement_outside = Path(directory).parent / f"wayfinder-proof-replaced-{os.getpid()}.txt"
            replacement_outside.write_text("changed external proof", encoding="utf-8")
            unsafe.symlink_to(replacement_outside)
            try:
                compromised = STATE.build_state(root)
                self.assertEqual("INVALID", compromised["intake"]["status"])
                self.assertFalse(compromised["exit"]["planning_exit_ready"])
                self.assertIn("INTAKE_INVALID", {item["code"] for item in compromised["health"]["issues"]})
            finally:
                replacement_outside.unlink()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _effort, state = self.finance_with_owned_unknown_complete(root)
            before = project_byte_snapshot(root)
            injected = INTAKE.IntakeError("Injected committed-state validation failure.", "INTAKE_NOT_READY", 409)
            with mock.patch.object(INTAKE, "_validate_committed", side_effect=injected):
                with self.assertRaises(INTAKE.IntakeError):
                    INTAKE.revalidate_intake_fact(
                        root,
                        None,
                        "Q-FR-004",
                        state["intake"]["revision"],
                        self.actor,
                        "CHAT",
                        "United Arab Emirates statutory reporting jurisdiction is confirmed.",
                    )
            self.assertEqual(before, project_byte_snapshot(root))

    def test_software_unknown_fact_revalidation_fails_closed_without_silent_technology_blessing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _effort, state = self.start_intake(root)
            state = self.frame_software(root, state)
            for choice in ("WEB-APP", "BALANCED", "GROWING", "INTERNAL-DATA"):
                state = self.choose(root, state, choice)
            for answer in (
                "UNKNOWN: reusable application baseline has not been inspected; OWNER: Example Owner",
                "It integrates with the existing identity and reporting interfaces.",
                "The delivery team is strongest in Python and browser standards.",
                "Aisha Khan owns day-two operations and incident response.",
                "The first usable release is required within six weeks.",
                "Prefer reversible services and a bounded monthly operating cost.",
            ):
                state = INTAKE.record_intake_answer(
                    root, None, state["intake"]["current_question"]["id"], state["intake"]["revision"], self.actor, "CHAT", answer
                )
            state = self.choose(root, state, "SW-MODULAR")
            options = self.technology_options()
            for option in options:
                option["recommendation"] = False
            state = INTAKE.propose_technology_options(
                root, None, state["intake"]["revision"], "Wayfinder research", "CLI", options
            )
            state = self.choose(root, state, "TECH-001")
            before = project_byte_snapshot(root)
            with self.assertRaises(INTAKE.IntakeError) as rejected:
                INTAKE.revalidate_intake_fact(
                    root,
                    None,
                    "Q-SW-005",
                    state["intake"]["revision"],
                    self.actor,
                    "CHAT",
                    "The application baseline has now been inspected and is reusable.",
                )
            self.assertEqual("INTAKE_NOT_READY", rejected.exception.code)
            self.assertEqual(before, project_byte_snapshot(root))

    def test_general_project_fact_answer_is_validated_and_resumable_in_the_real_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _effort, state = self.start_intake(root, "Plan a construction renovation with contractors")
            state = self.frame_domain(root, state, "GENERAL_PROJECT")
            for question_id, choice in (
                ("Q-GP-001", "CONSTRUCTION"),
                ("Q-GP-002", "SAFETY-QUALITY"),
                ("Q-GP-003", "PARTLY-DEFINED"),
            ):
                self.assertEqual(question_id, state["intake"]["current_question"]["id"])
                state = self.choose(root, state, choice)

            self.assertEqual("Q-GP-004", state["intake"]["current_question"]["id"])
            before = project_byte_snapshot(root)
            with self.assertRaises(INTAKE.IntakeError):
                INTAKE.record_intake_answer(
                    root,
                    None,
                    "Q-GP-004",
                    state["intake"]["revision"],
                    self.actor,
                    "CHAT",
                    "UNKNOWN: operating site is not confirmed; OWNER: assistant",
                )
            self.assertEqual(before, project_byte_snapshot(root))

            state = INTAKE.record_intake_answer(
                root,
                None,
                "Q-GP-004",
                state["intake"]["revision"],
                self.actor,
                "CHAT",
                "ESTABLISHED: Dubai operating site confirmed; EVIDENCE: local site register E-024",
            )
            answer = state["intake"]["answers"][-1]
            self.assertEqual("Q-GP-004", answer["question_id"])
            self.assertEqual("ESTABLISHED", answer["readiness"])
            self.assertEqual("Dubai operating site confirmed", answer["detail"])
            self.assertEqual("local site register E-024", answer["support"])
            self.assertEqual("Q-GP-005", state["intake"]["current_question"]["id"])

    def test_stale_invalid_agent_and_control_inputs_fail_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _effort, state = self.start_intake(root)
            question = state["intake"]["current_question"]
            before = project_byte_snapshot(root)

            with self.assertRaises(INTAKE.IntakeError) as stale:
                INTAKE.record_intake_choice(
                    root, None, question["decision_id"], 2, self.actor, "CLI", "SOFTWARE"
                )
            self.assertEqual("INTAKE_REVISION_CONFLICT", stale.exception.code)
            self.assertEqual(409, stale.exception.http_status)

            with self.assertRaises(INTAKE.IntakeError) as invalid:
                INTAKE.record_intake_choice(
                    root, None, question["decision_id"], 1, self.actor, "CLI", "NOT-ALLOWED"
                )
            self.assertEqual("INTAKE_INVALID_OPTION", invalid.exception.code)
            self.assertEqual(422, invalid.exception.http_status)

            with self.assertRaises(INTAKE.IntakeError) as agent:
                INTAKE.record_intake_choice(
                    root, None, question["decision_id"], 1, "assistant", "CLI", "SOFTWARE"
                )
            self.assertEqual("INTAKE_INVALID_ACTOR", agent.exception.code)

            with self.assertRaises(INTAKE.IntakeError) as control_actor:
                INTAKE.record_intake_choice(
                    root, None, question["decision_id"], 1, "Example\u202e Owner", "CLI", "SOFTWARE"
                )
            self.assertEqual("INTAKE_INVALID_ACTOR", control_actor.exception.code)
            self.assertEqual(before, project_byte_snapshot(root))

            state = self.choose(root, state, "SOFTWARE")
            after_choice = project_byte_snapshot(root)
            with self.assertRaises(INTAKE.IntakeError) as control_answer:
                INTAKE.record_intake_answer(
                    root,
                    None,
                    "Q-002",
                    state["intake"]["revision"],
                    self.actor,
                    "CLI",
                    "A plausible outcome\x1b[2J",
                )
            self.assertEqual("INTAKE_VALIDATION", control_answer.exception.code)
            self.assertEqual(after_choice, project_byte_snapshot(root))

    def test_software_comparison_then_grounded_named_options_require_two_human_choices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _effort, state = self.start_intake(root)
            state = self.software_to_architecture_choice(root, state)

            architecture = next(item for item in state["intake"]["comparisons"] if item["id"] == "CMP-SW-001")
            required = {
                "mvp_speed",
                "scale_beyond_mvp",
                "reliability",
                "efficiency",
                "cost",
                "complexity",
                "lock_in",
                "security_privacy",
                "recommendation",
                "rationale",
            }
            self.assertIsNone(architecture["selected_option"])
            self.assertTrue(all(required.issubset(option) for option in architecture["options"]))
            self.assertEqual(1, sum(option["recommendation"] for option in architecture["options"]))

            state = self.choose(root, state, architecture["recommended_option"])
            self.assertEqual("AWAITING_TECH_OPTIONS", state["intake"]["status"])
            self.assertIsNone(state["intake"]["current_question"])
            selected_architecture = next(
                item for item in state["intake"]["comparisons"] if item["id"] == "CMP-SW-001"
            )
            self.assertEqual(architecture["recommended_option"], selected_architecture["selected_option"])

            state = INTAKE.propose_technology_options(
                root,
                None,
                state["intake"]["revision"],
                "Wayfinder research",
                "CLI",
                self.technology_options(),
            )
            named = next(item for item in state["intake"]["comparisons"] if item["id"] == "CMP-TECH-001")
            self.assertEqual("TECH-001", named["recommended_option"])
            self.assertIsNone(named["selected_option"])
            self.assertEqual("Q-SW-012", state["intake"]["current_question"]["id"])
            self.assertEqual("AWAITING_HUMAN_CHOICE", state["intake"]["status"])
            for option in named["options"]:
                self.assertTrue(option["team_fit"])
                self.assertTrue(option["reversibility"])
                self.assertTrue(option["primary_sources"])

            # A human may explicitly choose a grounded alternative other than the advisory recommendation.
            state = self.choose(root, state, "TECH-002")
            named = next(item for item in state["intake"]["comparisons"] if item["id"] == "CMP-TECH-001")
            self.assertEqual("TECH-001", named["recommended_option"])
            self.assertEqual("TECH-002", named["selected_option"])
            self.assertEqual("COMPLETE", state["intake"]["status"])
            self.assertIsNone(state["intake"]["current_question"])

            # Hybrid efforts retain the primary route and open a distinct, owned branch.
            state = INTAKE.add_secondary_workstream(
                root,
                None,
                state["intake"]["revision"],
                self.actor,
                "CHAT",
                "GENERAL_PROJECT",
                "A physical installation is accepted at the operating site.",
                "Aisha Khan",
            )
            self.assertEqual("SOFTWARE", state["intake"]["domain"]["primary_domain"])
            workstream = state["intake"]["domain"]["secondary_workstreams"][0]
            self.assertEqual("GENERAL_PROJECT", workstream["domain"])
            self.assertEqual("Aisha Khan", workstream["authority"])
            self.assertEqual("Q-GP-001", state["intake"]["current_question"]["id"])
            self.assertTrue(workstream["decision_ids"])

    def test_unknown_software_fact_suppresses_architecture_and_named_technology_recommendations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _effort, state = self.start_intake(root)
            state = self.frame_software(root, state)
            for choice in ("WEB-APP", "BALANCED", "GROWING", "INTERNAL-DATA"):
                state = self.choose(root, state, choice)
            for answer in (
                "UNKNOWN: reusable application baseline has not been inspected; OWNER: Example Owner",
                "It integrates with the existing identity and reporting interfaces.",
                "The delivery team is strongest in Python and browser standards.",
                "Aisha Khan owns day-two operations and incident response.",
                "The first usable release is required within six weeks.",
                "Prefer reversible services and a bounded monthly operating cost.",
            ):
                state = INTAKE.record_intake_answer(
                    root,
                    None,
                    state["intake"]["current_question"]["id"],
                    state["intake"]["revision"],
                    self.actor,
                    "CHAT",
                    answer,
                )
            architecture = next(item for item in state["intake"]["comparisons"] if item["id"] == "CMP-SW-001")
            self.assertEqual("CONDITIONAL", architecture["recommendation_status"])
            self.assertIsNone(architecture["recommended_option"])
            self.assertFalse(any(item["recommendation"] for item in architecture["options"]))
            state = self.choose(root, state, "SW-MODULAR")
            options = self.technology_options()
            for option in options:
                option["recommendation"] = False
            state = INTAKE.propose_technology_options(
                root,
                None,
                state["intake"]["revision"],
                "Wayfinder research",
                "CLI",
                options,
            )
            named = next(item for item in state["intake"]["comparisons"] if item["id"] == "CMP-TECH-001")
            self.assertEqual("CONDITIONAL", named["recommendation_status"])
            self.assertIsNone(named["recommended_option"])
            self.assertFalse(any(item["recommendation"] for item in named["options"]))
            self.assertIn("software current environment", named["recommendation_rationale"])

    def test_failed_post_write_validation_rolls_back_every_project_byte(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            effort, state = self.start_intake(root)
            before = project_byte_snapshot(root)
            question = state["intake"]["current_question"]
            injected = INTAKE.IntakeError("Injected committed-state validation failure.", "INTAKE_NOT_READY", 409)
            with mock.patch.object(INTAKE, "_validate_committed", side_effect=injected):
                with self.assertRaises(INTAKE.IntakeError) as raised:
                    INTAKE.record_intake_choice(
                        root,
                        None,
                        question["decision_id"],
                        state["intake"]["revision"],
                        self.actor,
                        "CLI",
                        "SOFTWARE",
                    )
            self.assertEqual("INTAKE_NOT_READY", raised.exception.code)
            self.assertEqual(before, project_byte_snapshot(root))
            self.assertFalse((effort / ".INTAKE.transaction").exists())
            restored = STATE.build_state(root)
            self.assertEqual(1, restored["intake"]["revision"])
            self.assertEqual("OPEN", next(node for node in restored["nodes"] if node["id"] == "D-001")["status"])

    def test_dashboard_decision_recording_is_explicit_and_off_by_default(self) -> None:
        default = CLI.parser().parse_args(["dashboard"])
        interactive = CLI.parser().parse_args(["dashboard", "--interactive"])
        alias = CLI.parser().parse_args(["dashboard", "--record-decisions"])
        self.assertFalse(default.decision_recording)
        self.assertTrue(interactive.decision_recording)
        self.assertTrue(alias.decision_recording)

        with mock.patch.object(CLI, "serve") as serve:
            result = CLI.main(["dashboard", "--interactive", "--port", "0"])
        self.assertEqual(0, result)
        self.assertTrue(serve.call_args.kwargs["decision_recording"])
        self.assertEqual(0, serve.call_args.kwargs["port"])


if __name__ == "__main__":
    unittest.main()
