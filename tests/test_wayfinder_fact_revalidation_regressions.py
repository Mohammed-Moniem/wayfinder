from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


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


CLI = load_local("_wayfinder_cli_fact_revalidation_regressions", SCRIPTS / "wayfinder.py")
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


class WayfinderFactRevalidationRegressionTests(unittest.TestCase):
    actor = "Aisha Khan"

    def initialize(self, root: Path) -> Path:
        with redirect_stdout(io.StringIO()):
            result = INIT.main(
                [
                    "--root",
                    str(root),
                    "--slug",
                    "two-unknowns",
                    "--destination",
                    "Prepare controlled regulatory statutory reports.",
                ]
            )
        self.assertEqual(0, result)
        return root / ".codex" / "wayfinder" / "efforts" / "two-unknowns"

    def choose(self, root: Path, state: dict, choice: str) -> dict:
        question = state["intake"]["current_question"]
        self.assertEqual("choice", question["answer_type"])
        return INTAKE.record_intake_choice(
            root,
            None,
            question["decision_id"],
            state["intake"]["revision"],
            self.actor,
            "CHAT",
            choice,
        )

    def answer(self, root: Path, state: dict, answer: str) -> dict:
        question = state["intake"]["current_question"]
        return INTAKE.record_intake_answer(
            root,
            None,
            question["id"],
            state["intake"]["revision"],
            self.actor,
            "CHAT",
            answer,
        )

    def complete_finance_route_with_two_owned_unknowns(self, root: Path) -> tuple[Path, dict]:
        effort = self.initialize(root)
        state = INTAKE.start_intake(root, None, "Prepare controlled regulatory statutory reports")
        state = self.choose(root, state, "FINANCE_REPORTING")
        for answer in (
            "Finance owners receive auditable statutory reports.",
            "The qualified reporting authority accepts the retained close package.",
            "A retained signed close package demonstrates the result.",
            "No filing, purchase, or external write occurs without explicit approval.",
            "External filing and production-system changes are outside this planning effort.",
            self.actor,
        ):
            state = self.answer(root, state, answer)
        for choice in ("REGULATORY", "ACCOUNTING-SYSTEM", "AUDITABILITY"):
            state = self.choose(root, state, choice)
        facts = {
            "Q-FR-004": "UNKNOWN: statutory reporting jurisdiction is not confirmed; OWNER: Aisha Khan",
            "Q-FR-005": "UNKNOWN: applicable reporting basis is not confirmed; OWNER: Aisha Khan",
            "Q-FR-006": "Calendar-year reporting uses the approved cutoff policy.",
            "Q-FR-007": "AED presentation uses the approved statutory materiality threshold.",
            "Q-FR-008": "The controlled ledger and source owners provide retained lineage.",
            "Q-FR-009": "Every material balance is reconciled and exceptions are retained.",
            "Q-FR-010": "Preparer, reviewer, access, and change roles are segregated.",
            "Q-FR-011": "A qualified statutory-reporting reviewer signs the retained close package.",
            "Q-FR-012": "Auditable statutory statements with retained workpapers are required.",
            "Q-FR-013": "The statutory filing deadline cannot move.",
        }
        for question_id, answer in facts.items():
            self.assertEqual(question_id, state["intake"]["current_question"]["id"])
            state = self.answer(root, state, answer)
        comparison = next(item for item in state["intake"]["comparisons"] if item["id"] == "CMP-FR-001")
        self.assertEqual("CONDITIONAL", comparison["recommendation_status"])
        self.assertIsNone(comparison["recommended_option"])
        self.assertTrue(all(not option["recommendation"] for option in comparison["options"]))
        state = self.choose(root, state, "FR-LEDGER-LED")
        self.assertEqual("COMPLETE", state["intake"]["status"])
        self.assertFalse(state["exit"]["planning_exit_ready"])
        self.assertEqual(
            {"Q-FR-004", "Q-FR-005"},
            set(state["intake"]["readiness"]["blocking_questions"]),
        )
        return effort, state

    def test_two_unknowns_revalidate_sequentially_without_losing_history_or_selecting_a_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            effort, initial = self.complete_finance_route_with_two_owned_unknowns(root)
            initial_answers = {
                item["question_id"]: item
                for item in initial["intake"]["answers"]
                if item["question_id"] in {"Q-FR-004", "Q-FR-005"}
            }
            initial_route = next(
                item for item in initial["intake"]["decision_bindings"] if item["question_id"] == "Q-FR-014"
            )

            first = INTAKE.revalidate_intake_fact(
                root,
                None,
                "Q-FR-004",
                initial["intake"]["revision"],
                self.actor,
                "CHAT",
                "United Arab Emirates statutory reporting jurisdiction is confirmed.",
            )
            first_question = first["intake"]["current_question"]
            first_comparison = next(
                item for item in first["intake"]["comparisons"] if item["id"] == "CMP-FR-001"
            )
            self.assertEqual("Q-RV-001", first_question["id"])
            self.assertEqual("choice", first_question["answer_type"])
            self.assertTrue(first_question["human_choice_required"])
            self.assertEqual(first["intake"]["revision"], first_question["expected_revision"])
            self.assertEqual(initial_route["decision_id"], first_question["revalidates"])
            self.assertEqual(
                {item["id"] for item in first_comparison["options"]},
                {item["id"] for item in first_question["options"]},
            )
            self.assertEqual("CONDITIONAL", first_comparison["recommendation_status"])
            self.assertIsNone(first_comparison["recommended_option"])
            self.assertIsNone(first_comparison["selected_option"])
            self.assertTrue(all(not option["recommendation"] for option in first_comparison["options"]))
            self.assertEqual(
                initial_answers,
                {
                    item["question_id"]: item
                    for item in first["intake"]["answers"]
                    if item["question_id"] in initial_answers
                },
            )

            pending_bytes = project_byte_snapshot(root)
            with self.assertRaises(INTAKE.IntakeError) as premature_second:
                INTAKE.revalidate_intake_fact(
                    root,
                    None,
                    "Q-FR-005",
                    first["intake"]["revision"],
                    self.actor,
                    "CHAT",
                    "IFRS reporting basis is confirmed.",
                )
            self.assertEqual("INTAKE_NOT_READY", premature_second.exception.code)
            self.assertEqual(pending_bytes, project_byte_snapshot(root))

            after_first_choice = self.choose(root, first, "FR-DATA-LAYER")
            first_revalidation_decision = first_question["decision_id"]
            self.assertFalse(after_first_choice["exit"]["planning_exit_ready"])
            self.assertEqual(["Q-FR-005"], after_first_choice["intake"]["readiness"]["blocking_questions"])
            self.assertEqual(
                {initial_route["decision_id"], first_revalidation_decision},
                {item["id"] for item in after_first_choice["implementation_baseline"]["applicable_decisions"]}
                & {initial_route["decision_id"], first_revalidation_decision},
            )
            raw_after_first = json.loads((effort / "INTAKE.json").read_text(encoding="utf-8"))
            first_history = raw_after_first["fact_revalidations"][0]
            first_selected_comparison = next(
                item for item in raw_after_first["comparisons"] if item["id"] == "CMP-FR-001"
            )

            stale_bytes = project_byte_snapshot(root)
            with self.assertRaises(INTAKE.IntakeError) as stale:
                INTAKE.revalidate_intake_fact(
                    root,
                    None,
                    "Q-FR-005",
                    first["intake"]["revision"],
                    self.actor,
                    "CHAT",
                    "IFRS reporting basis is confirmed.",
                )
            self.assertEqual("INTAKE_REVISION_CONFLICT", stale.exception.code)
            self.assertEqual(stale_bytes, project_byte_snapshot(root))

            second = INTAKE.revalidate_intake_fact(
                root,
                None,
                "Q-FR-005",
                after_first_choice["intake"]["revision"],
                self.actor,
                "CHAT",
                "IFRS reporting basis is confirmed for the statutory statements.",
            )
            second_question = second["intake"]["current_question"]
            second_comparison = next(
                item for item in second["intake"]["comparisons"] if item["id"] == "CMP-FR-001"
            )
            second_revalidation_decision = second_question["decision_id"]
            self.assertEqual("Q-RV-002", second_question["id"])
            self.assertEqual("choice", second_question["answer_type"])
            self.assertTrue(second_question["human_choice_required"])
            self.assertTrue(second_question["destination_blocking"])
            self.assertEqual(second["intake"]["revision"], second_question["expected_revision"])
            self.assertEqual(first_revalidation_decision, second_question["revalidates"])
            self.assertEqual(
                {item["id"] for item in second_comparison["options"]},
                {item["id"] for item in second_question["options"]},
            )
            self.assertEqual("GROUNDED", second_comparison["recommendation_status"])
            self.assertEqual(1, sum(bool(item["recommendation"]) for item in second_comparison["options"]))
            self.assertIsNotNone(second_comparison["recommended_option"])
            self.assertIsNone(second_comparison["selected_option"])
            current_binding = next(
                item for item in second["intake"]["decision_bindings"] if item["decision_id"] == second_revalidation_decision
            )
            self.assertEqual("OPEN", current_binding["status"])
            self.assertIsNone(current_binding["selected_option"])
            self.assertFalse(second["exit"]["planning_exit_ready"])

            history = second["intake"]["fact_revalidations"]
            self.assertEqual(["FRV-0001", "FRV-0002"], [item["id"] for item in history])
            self.assertEqual(first_history, history[0])
            self.assertEqual(initial_answers["Q-FR-005"], history[1]["previous_answer"])
            self.assertEqual(first_selected_comparison, history[1]["prior_comparison"])
            self.assertEqual(first_revalidation_decision, history[1]["prior_decision_id"])
            self.assertEqual(second_revalidation_decision, history[1]["revalidation_decision_id"])
            self.assertIn(
                {
                    "source": second_revalidation_decision,
                    "target": first_revalidation_decision,
                    "type": "revalidates",
                },
                second["edges"],
            )

            final = self.choose(root, second, "FR-LEDGER-LED")
            self.assertTrue(final["intake"]["readiness"]["exit_ready"])
            self.assertTrue(final["exit"]["planning_exit_ready"])
            final_comparison = next(
                item for item in final["intake"]["comparisons"] if item["id"] == "CMP-FR-001"
            )
            self.assertEqual("FR-LEDGER-LED", final_comparison["selected_option"])
            chain_ids = {initial_route["decision_id"], first_revalidation_decision, second_revalidation_decision}
            baseline = final["implementation_baseline"]
            self.assertTrue(chain_ids.issubset({item["id"] for item in baseline["applicable_decisions"]}))
            self.assertEqual(final["intake"]["revision"], baseline["intake_revision"])
            self.assertEqual("FINANCE_REPORTING", baseline["primary_domain"])
            self.assertEqual(
                hashlib.sha256((effort / "EFFORT.json").read_bytes()).hexdigest(),
                baseline["manifest_hash"],
            )
            for decision_id in chain_ids:
                self.assertEqual(
                    "RESOLVED",
                    next(item for item in final["nodes"] if item["id"] == decision_id)["status"],
                )
                self.assertTrue((effort / "decisions" / f"{decision_id}.md").is_file())

            final_bytes = project_byte_snapshot(root)
            for question_id in ("Q-FR-004", "Q-FR-005"):
                with self.subTest(question_id=question_id):
                    with self.assertRaises(INTAKE.IntakeError) as repeated:
                        INTAKE.revalidate_intake_fact(
                            root,
                            None,
                            question_id,
                            final["intake"]["revision"],
                            self.actor,
                            "CHAT",
                            "A repeated edit must not replace append-only history.",
                        )
                    self.assertEqual("INTAKE_NOT_READY", repeated.exception.code)
                    self.assertEqual(final_bytes, project_byte_snapshot(root))


if __name__ == "__main__":
    unittest.main()
