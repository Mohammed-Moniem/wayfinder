from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "wayfinder" / "scripts"


def load_local(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLI = load_local("_wayfinder_cli_regression_tests", SCRIPTS / "wayfinder.py")
INTAKE = CLI._INTAKE
INIT = CLI.init_wayfinder


def project_byte_snapshot(root: Path) -> dict[str, bytes]:
    """Capture project bytes without following symbolic links."""
    snapshot: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = b"SYMLINK\0" + os.fsencode(os.readlink(path))
        elif path.is_file():
            snapshot[relative] = path.read_bytes()
    return snapshot


class WayfinderIntakeRegressionTests(unittest.TestCase):
    actor = "Aisha Khan"

    def initialize(self, root: Path, slug: str = "regression") -> Path:
        with redirect_stdout(io.StringIO()):
            result = INIT.main(
                [
                    "--root",
                    str(root),
                    "--slug",
                    slug,
                    "--destination",
                    "A destination awaiting structured intake.",
                ]
            )
        self.assertEqual(0, result)
        return root / ".codex" / "wayfinder" / "efforts" / slug

    def start(self, root: Path, intent: str) -> tuple[Path, dict]:
        effort = self.initialize(root)
        state = INTAKE.start_intake(root, None, intent)
        self.assertEqual("Q-001", state["intake"]["current_question"]["id"])
        return effort, state

    def choose(self, root: Path, state: dict, choice: str, *, source: str = "CHAT") -> dict:
        question = state["intake"]["current_question"]
        return INTAKE.record_intake_choice(
            root,
            None,
            question["decision_id"],
            state["intake"]["revision"],
            self.actor,
            source,
            choice,
        )

    def answer(self, root: Path, state: dict, answer: str, *, source: str = "CHAT") -> dict:
        question = state["intake"]["current_question"]
        return INTAKE.record_intake_answer(
            root,
            None,
            question["id"],
            state["intake"]["revision"],
            self.actor,
            source,
            answer,
        )

    def frame(self, root: Path, state: dict, domain: str) -> dict:
        state = self.choose(root, state, domain)
        for answer in (
            "The intended users receive the agreed observable result.",
            "The named acceptance authority verifies the complete result.",
            "A retained acceptance record demonstrates the result.",
            "No purchase, filing, deployment, or external write occurs without explicit approval.",
            "Production execution and external submission are outside this planning effort.",
            self.actor,
        ):
            state = self.answer(root, state, answer)
        return state

    @staticmethod
    def technology_options(*, include_user_alternative: bool = False) -> list[dict]:
        common = {
            "mvp_speed": "Supports a bounded MVP route.",
            "scale_beyond_mvp": "Has a documented growth path beyond the first release.",
            "reliability": "Supports explicit failure handling and recovery ownership.",
            "efficiency": "Keeps delivery and operating work visible.",
            "cost": "Requires a separately approved cost decision before purchase.",
            "complexity": "Has a moderate and inspectable complexity envelope.",
            "lock_in": "Uses explicit boundaries so replacement remains possible.",
            "security_privacy": "Requires access-control and data-classification validation.",
            "team_fit": "Matches the recorded small cross-functional team.",
            "reversibility": "Can be replaced behind the recorded component boundary.",
        }
        options = [
            {
                "id": "TECH-001",
                "name": "Python standard-library service",
                "version_or_constraint": "A supported Python 3 release",
                "summary": "A small service using host-provided Python capabilities.",
                **common,
                "recommendation": True,
                "rationale": "Best fits the bounded route and avoids an unapproved dependency commitment.",
                "evidence_refs": [],
                "primary_sources": ["https://docs.python.org/3/"],
            },
            {
                "id": "TECH-002",
                "name": "Node web-platform service",
                "version_or_constraint": "A supported Node release with native fetch",
                "summary": "A small service using runtime web-platform APIs.",
                **common,
                "recommendation": False,
                "rationale": "Credible when the delivery team has stronger Node operating experience.",
                "evidence_refs": [],
                "primary_sources": ["https://nodejs.org/docs/latest/api/globals.html"],
            },
        ]
        if include_user_alternative:
            options.append(
                {
                    "id": "TECH-003",
                    "name": "SQLite-backed Python service",
                    "version_or_constraint": "A supported Python 3 release and SQLite 3",
                    "summary": "The user's requested alternative, bounded to a local transactional store.",
                    **common,
                    "recommendation": False,
                    "rationale": "Viable for a bounded workload if the recorded concurrency limit remains true.",
                    "evidence_refs": [],
                    "primary_sources": ["https://www.sqlite.org/docs.html"],
                }
            )
        return options

    def software_at_named_technology_choice(self, root: Path) -> tuple[Path, dict]:
        effort, state = self.start(root, "Build a secure web application for a nontechnical owner")
        state = self.frame(root, state, "SOFTWARE")
        for choice in ("WEB-APP", "BALANCED", "GROWING", "INTERNAL-DATA"):
            state = self.choose(root, state, choice)
        for answer in (
            "This is a new service with no reusable application baseline.",
            "It integrates with the existing identity and reporting interfaces.",
            "The team is strongest in Python and browser standards.",
            "Aisha Khan owns day-two operations and incident response.",
            "The first useful release is required within six weeks.",
            "Prefer reversible services and a bounded monthly operating cost.",
        ):
            state = self.answer(root, state, answer)
        architecture = next(
            item for item in state["intake"]["comparisons"] if item["id"] == "CMP-SW-001"
        )
        state = self.choose(root, state, architecture["recommended_option"])
        state = INTAKE.propose_technology_options(
            root,
            None,
            state["intake"]["revision"],
            "Wayfinder research",
            "CLI",
            self.technology_options(),
        )
        self.assertEqual("Q-SW-012", state["intake"]["current_question"]["id"])
        return effort, state

    def finance_at_branch_facts(self, root: Path) -> tuple[Path, dict]:
        effort, state = self.start(root, "Prepare controlled regulatory statutory reports")
        state = self.frame(root, state, "FINANCE_REPORTING")
        for choice in ("REGULATORY", "ACCOUNTING-SYSTEM", "AUDITABILITY"):
            state = self.choose(root, state, choice)
        self.assertEqual("Q-FR-004", state["intake"]["current_question"]["id"])
        return effort, state

    def test_user_proposed_option_is_revisioned_without_becoming_a_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            effort, state = self.start(root, "Plan a research-led public programme")
            question = state["intake"]["current_question"]
            before_revision = state["intake"]["revision"]
            before_options = {item["id"] for item in question["options"]}
            before_receipts = list(state["intake"]["receipts"])
            proposed = {
                "id": "RESEARCH-PROGRAMME",
                "label": "Research programme",
                "description": "A research-led effort whose route is not represented by the offered domain labels.",
            }
            self.assertTrue(
                callable(getattr(INTAKE, "propose_intake_alternative", None)),
                "Wayfinder needs a revision-CAS API for a user-proposed current-choice alternative.",
            )

            revised = INTAKE.propose_intake_alternative(
                root=root,
                effort=None,
                decision_id=question["decision_id"],
                expected_revision=before_revision,
                actor=self.actor,
                source="CHAT",
                option=proposed,
            )

            self.assertEqual(before_revision + 1, revised["intake"]["revision"])
            current = revised["intake"]["current_question"]
            self.assertEqual(question["id"], current["id"])
            self.assertEqual(question["decision_id"], current["decision_id"])
            self.assertEqual(before_options | {proposed["id"]}, {item["id"] for item in current["options"]})
            binding = next(
                item
                for item in revised["intake"]["decision_bindings"]
                if item["decision_id"] == question["decision_id"]
            )
            self.assertEqual("OPEN", binding["status"])
            self.assertIsNone(binding["selected_option"])
            self.assertEqual(before_receipts, revised["intake"]["receipts"][:-1])
            receipt = revised["intake"]["receipts"][-1]
            self.assertEqual("OPTION_PROPOSAL", receipt["kind"])
            self.assertEqual(question["decision_id"], receipt["decision_id"])
            self.assertEqual(proposed, receipt["option"])
            decision_text = (effort / "decisions" / f"{question['decision_id']}.md").read_text(encoding="utf-8")
            self.assertIn("- **Revision:** 2", decision_text)
            for option_id in before_options | {proposed["id"]}:
                self.assertIn(option_id, decision_text)

            after_first_write = project_byte_snapshot(root)
            with self.assertRaises(INTAKE.IntakeError) as stale:
                INTAKE.propose_intake_alternative(
                    root=root,
                    effort=None,
                    decision_id=question["decision_id"],
                    expected_revision=before_revision,
                    actor=self.actor,
                    source="CHAT",
                    option={
                        "id": "SECOND-ALTERNATIVE",
                        "label": "Second alternative",
                        "description": "A stale concurrent proposal that must not overwrite the first.",
                    },
                )
            self.assertEqual("INTAKE_REVISION_CONFLICT", stale.exception.code)
            self.assertEqual(after_first_write, project_byte_snapshot(root))

    def test_named_technology_replacement_preserves_history_and_requires_a_new_choice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            effort, state = self.software_at_named_technology_choice(root)
            question = state["intake"]["current_question"]
            before_revision = state["intake"]["revision"]
            raw_before = json.loads((effort / "INTAKE.json").read_text(encoding="utf-8"))
            comparison_before = next(
                item for item in raw_before["comparisons"] if item["id"] == "CMP-TECH-001"
            )
            self.assertTrue(
                callable(getattr(INTAKE, "replace_technology_options", None)),
                "Wayfinder needs a revision-CAS boundary for a grounded named-tech replacement.",
            )
            self.assertEqual(1, comparison_before.get("revision"))

            revised = INTAKE.replace_technology_options(
                root=root,
                effort=None,
                decision_id=question["decision_id"],
                expected_revision=before_revision,
                actor="Wayfinder research",
                source="CHAT",
                options=self.technology_options(include_user_alternative=True),
            )

            self.assertEqual(before_revision + 1, revised["intake"]["revision"])
            self.assertEqual("Q-SW-012", revised["intake"]["current_question"]["id"])
            self.assertEqual(question["decision_id"], revised["intake"]["current_question"]["decision_id"])
            binding = next(
                item
                for item in revised["intake"]["decision_bindings"]
                if item["decision_id"] == question["decision_id"]
            )
            self.assertEqual("OPEN", binding["status"])
            self.assertIsNone(binding["selected_option"])
            raw_after = json.loads((effort / "INTAKE.json").read_text(encoding="utf-8"))
            active = next(item for item in raw_after["comparisons"] if item["id"] == "CMP-TECH-001")
            self.assertEqual(2, active["revision"])
            self.assertIsNone(active["selected_option"])
            self.assertIn("TECH-003", {item["id"] for item in active["options"]})
            history = raw_after["comparison_history"]
            self.assertEqual(1, len(history))
            self.assertEqual("CMP-TECH-001", history[0]["comparison_id"])
            self.assertEqual(1, history[0]["revision"])
            self.assertEqual("CHAT", history[0]["source"])
            self.assertEqual(comparison_before, history[0]["snapshot"])
            self.assertTrue(history[0]["superseded_at"])
            self.assertTrue(history[0]["superseded_by"])
            receipt = raw_after["receipts"][-1]
            self.assertEqual("TECH_OPTIONS_REVISED", receipt["kind"])
            self.assertEqual(question["decision_id"], receipt["decision_id"])
            self.assertEqual((1, 2), (receipt["old_comparison_revision"], receipt["new_comparison_revision"]))

            after_first_write = project_byte_snapshot(root)
            with self.assertRaises(INTAKE.IntakeError) as stale:
                INTAKE.replace_technology_options(
                    root=root,
                    effort=None,
                    decision_id=question["decision_id"],
                    expected_revision=before_revision,
                    actor="Wayfinder research",
                    source="CHAT",
                    options=self.technology_options(include_user_alternative=True),
                )
            self.assertEqual("INTAKE_REVISION_CONFLICT", stale.exception.code)
            self.assertEqual(after_first_write, project_byte_snapshot(root))

    def test_general_project_comparison_uses_material_site_permit_and_safety_facts(self) -> None:
        occupied_site = {
            "project_form": "CONSTRUCTION",
            "project_priority": "SAFETY-QUALITY",
            "project_uncertainty": "PARTLY-DEFINED",
            "project_site": "An occupied hospital site must remain operational during the work.",
            "project_schedule": "Clinical operations allow only short overnight work windows.",
            "project_vendors": "A specialist live-site contractor has not yet been appointed.",
            "project_permits": "UNKNOWN: live-site permits remain unresolved; OWNER: Aisha Khan",
            "project_resources": "Isolation equipment and clinical escorts are scarce.",
            "project_safety": "Patient safety and infection-control obligations dominate delivery.",
            "project_dependencies": "Each phase depends on a clinical-area shutdown approval.",
            "project_acceptance": "Facilities and clinical safety owners inspect every phase.",
            "project_contingency": "Stop work and restore the area if isolation fails.",
        }
        cleared_site = {
            **occupied_site,
            "project_site": "An empty greenfield warehouse site is fully available.",
            "project_schedule": "The full site is continuously available for the planned period.",
            "project_vendors": "The appointed contractor and suppliers are mobilized.",
            "project_permits": "All required permits and approvals are issued.",
            "project_resources": "The approved crew, equipment, and budget are available.",
            "project_safety": "The isolated site follows the approved ordinary construction plan.",
            "project_dependencies": "No operating shutdown or third-party access dependency remains.",
            "project_acceptance": "The owner performs one documented completion inspection.",
            "project_contingency": "The approved schedule contingency is available.",
        }

        occupied = INTAKE._general_comparison(occupied_site)
        cleared = INTAKE._general_comparison(cleared_site)
        occupied_rationale = self._comparison_rationale(occupied)
        cleared_rationale = self._comparison_rationale(cleared)
        self.assertNotEqual(occupied, cleared)
        self.assertNotEqual(occupied_rationale, cleared_rationale)
        self.assertTrue(
            any(term in occupied_rationale.casefold() for term in ("permit", "safety", "occupied", "hospital")),
            occupied_rationale,
        )

    def test_finance_comparison_uses_reporting_basis_signoff_and_deadline_facts(self) -> None:
        regulatory = {
            "reporting_need": "REGULATORY",
            "finance_sources": "ACCOUNTING-SYSTEM",
            "finance_priority": "AUDITABILITY",
            "finance_jurisdiction": "United Arab Emirates statutory reporting.",
            "finance_basis": "IFRS with an unresolved local presentation question.",
            "finance_period_cutoff": "Calendar year with a strict statutory cutoff.",
            "finance_currency_materiality": "AED presentation with statutory materiality.",
            "finance_source_lineage": "The ledger is owned by the controller.",
            "finance_reconciliation": "Every material balance requires retained reconciliation evidence.",
            "finance_controls": "Separate preparer and qualified reviewer roles are mandatory.",
            "finance_signoff": "A qualified external reporting reviewer must sign off.",
            "finance_format": "Auditable statutory statements and retained workpapers.",
            "finance_deadline": "The statutory filing deadline cannot move.",
        }
        management = {
            **regulatory,
            "reporting_need": "MANAGEMENT",
            "finance_jurisdiction": "Internal group reporting with no filing conclusion.",
            "finance_basis": "An approved internal management-reporting basis.",
            "finance_period_cutoff": "A flexible weekly operating cutoff.",
            "finance_currency_materiality": "USD management view with an internal threshold.",
            "finance_controls": "The finance manager prepares and reviews the internal pack.",
            "finance_signoff": "The operating director accepts the management pack.",
            "finance_format": "A concise internal variance report.",
            "finance_deadline": "The internal meeting date may move by two days.",
        }

        statutory = INTAKE._finance_comparison(regulatory)
        internal = INTAKE._finance_comparison(management)
        statutory_rationale = self._comparison_rationale(statutory)
        internal_rationale = self._comparison_rationale(internal)
        self.assertNotEqual(statutory, internal)
        self.assertNotEqual(statutory_rationale, internal_rationale)
        self.assertTrue(
            any(
                term in statutory_rationale.casefold()
                for term in ("regulatory", "statutory", "basis", "sign-off", "signoff", "deadline")
            ),
            statutory_rationale,
        )

    @staticmethod
    def _comparison_rationale(comparison: dict) -> str:
        recommended = comparison.get("recommended_option")
        option = next(
            (item for item in comparison.get("options", []) if item.get("id") == recommended),
            {},
        )
        return " ".join(
            value
            for value in (comparison.get("recommendation_rationale"), option.get("rationale"))
            if isinstance(value, str)
        )

    def test_regulatory_reporting_rejects_not_applicable_critical_facts(self) -> None:
        facts = {
            "Q-FR-004": "United Arab Emirates statutory reporting under the named local authority.",
            "Q-FR-005": "IFRS subject to qualified review of local presentation requirements.",
            "Q-FR-006": "Calendar-year reporting with the approved cutoff policy.",
            "Q-FR-007": "AED presentation with the approved statutory materiality threshold.",
            "Q-FR-008": "The controlled ledger and source owners provide retained lineage.",
            "Q-FR-009": "Every material balance is reconciled and exceptions are retained.",
            "Q-FR-010": "Preparer, reviewer, access, and change roles are segregated.",
            "Q-FR-011": "A qualified statutory-reporting reviewer signs the retained close package.",
        }
        for target in ("Q-FR-004", "Q-FR-005", "Q-FR-011"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _effort, state = self.finance_at_branch_facts(root)
                for question_id, answer in facts.items():
                    self.assertEqual(question_id, state["intake"]["current_question"]["id"])
                    if question_id == target:
                        before = project_byte_snapshot(root)
                        with self.assertRaises(INTAKE.IntakeError) as rejected:
                            state = self.answer(
                                root,
                                state,
                                "N/A: this required regulatory fact was incorrectly treated as optional",
                            )
                        self.assertEqual("INTAKE_REGULATORY_REQUIREMENT", rejected.exception.code)
                        self.assertEqual(422, rejected.exception.http_status)
                        self.assertEqual(before, project_byte_snapshot(root))
                        break
                    state = self.answer(root, state, answer)

    def test_owned_regulatory_unknown_remains_unresolved_and_blocks_planning_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _effort, state = self.finance_at_branch_facts(root)
            state = self.answer(
                root,
                state,
                "UNKNOWN: the statutory reporting jurisdiction is not confirmed; OWNER: Aisha Khan",
            )
            for answer in (
                "IFRS subject to qualified review of local presentation requirements.",
                "Calendar-year reporting with the approved cutoff policy.",
                "AED presentation with the approved statutory materiality threshold.",
                "The controlled ledger and source owners provide retained lineage.",
                "Every material balance is reconciled and exceptions are retained.",
                "Preparer, reviewer, access, and change roles are segregated.",
                "A qualified statutory-reporting reviewer signs the retained close package.",
                "Auditable statutory statements with retained workpapers are required.",
                "The statutory filing deadline cannot move.",
            ):
                state = self.answer(root, state, answer)
            route_choice = state["intake"]["current_question"]["options"][0]["id"]
            state = self.choose(root, state, route_choice)

            jurisdiction = next(
                item for item in state["intake"]["answers"] if item["question_id"] == "Q-FR-004"
            )
            self.assertEqual("UNKNOWN", jurisdiction["readiness"])
            self.assertEqual("Aisha Khan", jurisdiction["support"])
            self.assertFalse(state["exit"]["planning_exit_ready"])
            self.assertTrue(
                state["exit"]["unresolved_destination_decisions"]
                or state["exit"]["remaining_nonblocking_unknowns"],
                "The owned regulatory unknown must remain visible in canonical exit state.",
            )


if __name__ == "__main__":
    unittest.main()
