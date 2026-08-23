#!/usr/bin/env python3
"""Wayfinder V3 local lifecycle, diagnostics, state, and dashboard CLI."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Sequence


MINIMUM_PYTHON = (3, 11)


def _require_supported_python(version_info: Sequence[int] | None = None) -> None:
    current = tuple((sys.version_info if version_info is None else version_info)[:2])
    if current < MINIMUM_PYTHON:
        raise SystemExit(
            "wayfinder: Python 3.11 or newer is required; "
            f"found {current[0]}.{current[1]}."
        )


_require_supported_python()


def _load_local(name: str, filename: str) -> Any:
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    path = Path(__file__).resolve(strict=True).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load local Wayfinder module {filename}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


init_wayfinder = _load_local("_wayfinder_init_v3", "init_wayfinder.py")
_STATE = _load_local("_wayfinder_state_v3", "wayfinder_state.py")
_SERVER = _load_local("_wayfinder_server_v3", "wayfinder_server.py")
_INTAKE = _load_local("_wayfinder_intake_v1", "wayfinder_intake.py")
WayfinderError = _STATE.WayfinderError
build_state = _STATE.build_state
state_json = _STATE.state_json
status_text = _STATE.status_text
serve = _SERVER.serve
terminal_safe_text = _SERVER.terminal_safe_text


def _write_terminal(value: object) -> None:
    """Write code-composed layout after every untrusted scalar is sanitized."""
    sys.stdout.write(terminal_safe_text(value, allow_newlines=True))


def _terminal_safe_data(value: Any) -> Any:
    """Recursively make artifact-derived scalar text safe before composition."""
    if isinstance(value, str):
        return terminal_safe_text(value)
    if isinstance(value, dict):
        return {key: _terminal_safe_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_terminal_safe_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_terminal_safe_data(item) for item in value)
    return value


def _root_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Path to the target project folder (Git is optional).")


def _effort_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--effort", help="Effort slug or project-relative effort/MAP.md path. Defaults to ACTIVE.")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init", aliases=["start"], help="Create and activate a local V3 effort.")
    _root_argument(initialize)
    initialize.add_argument("--slug", required=True)
    initialize.add_argument("--destination", required=True)
    initialize.add_argument(
        "--expect-active",
        help="Exact current ACTIVE value required to replace it; omission safely requires no ACTIVE pointer.",
    )

    status = commands.add_parser("status", help="Show computed lifecycle state and the rerun recommendation.")
    _root_argument(status)
    _effort_argument(status)
    status.add_argument("--json", action="store_true", help="Emit the stable dashboard payload as JSON.")

    resume = commands.add_parser("resume", help="Inspect how to resume the active effort (read-only; resolves nothing).")
    _root_argument(resume)
    _effort_argument(resume)
    resume.add_argument("--json", action="store_true", help="Emit the stable dashboard payload as JSON.")

    revalidate = commands.add_parser(
        "revalidate",
        help="Inspect delivery revalidation triggers (read-only; changes no ticket).",
    )
    _root_argument(revalidate)
    _effort_argument(revalidate)
    revalidate.add_argument("--gate", help="Optionally focus the check on one G-NNN gate.")
    revalidate.add_argument("--json", action="store_true", help="Emit the stable dashboard payload as JSON.")

    complete = commands.add_parser(
        "complete",
        help="Validate planning-exit eligibility (read-only; writes no EXIT.md).",
    )
    _root_argument(complete)
    _effort_argument(complete)
    complete.add_argument("--json", action="store_true", help="Emit the stable dashboard payload as JSON.")

    migrate = commands.add_parser(
        "migrate",
        help="Preview a V2-to-V3 migration without changing project files.",
    )
    _root_argument(migrate)
    _effort_argument(migrate)
    migrate.add_argument("--check", action="store_true", required=True, help="Required safety flag; preview only.")
    migrate.add_argument("--json", action="store_true", help="Emit a deterministic migration preview as JSON.")

    doctor = commands.add_parser("doctor", help="Validate IDs, statuses, references, cycles, and derived views.")
    _root_argument(doctor)
    _effort_argument(doctor)
    doctor.add_argument("--json", action="store_true", help="Emit the stable dashboard payload as JSON.")

    dashboard = commands.add_parser(
        "dashboard",
        aliases=["serve"],
        help="Serve the local dashboard on 127.0.0.1 (read-only by default; --interactive enables only scoped intake recording).",
    )
    _root_argument(dashboard)
    _effort_argument(dashboard)
    dashboard.add_argument(
        "--port",
        type=int,
        default=0,
        help="Loopback port; defaults to a fresh OS-assigned port. Set a fixed port explicitly only when needed.",
    )
    dashboard.add_argument("--quiet", action="store_true", help="Suppress request logging.")
    dashboard.add_argument(
        "--interactive",
        "--record-decisions",
        dest="decision_recording",
        action="store_true",
        help="Enable only validated local intake answers and option selections; grants no general write or implementation authority.",
    )

    intake = commands.add_parser("intake", help="Run or inspect the deterministic one-question-at-a-time project intake.")
    intake_commands = intake.add_subparsers(dest="intake_command", required=True)
    intake_start = intake_commands.add_parser("start", help="Start intake and present the first explicit human-choice boundary.")
    _root_argument(intake_start)
    _effort_argument(intake_start)
    intake_start.add_argument("--intent", required=True, help="One-line description of the intended effort.")
    intake_start.add_argument("--json", action="store_true", help="Emit the refreshed stable public state as JSON.")
    intake_status = intake_commands.add_parser("status", help="Show the current intake question without changing state.")
    _root_argument(intake_status)
    _effort_argument(intake_status)
    intake_status.add_argument("--json", action="store_true", help="Emit the stable public state as JSON.")
    intake_answer = intake_commands.add_parser("answer", help="Record exactly the current text answer or allowed Decision option.")
    _root_argument(intake_answer)
    _effort_argument(intake_answer)
    intake_answer.add_argument("--expect-revision", type=int, required=True, help="Exact intake revision CAS guard.")
    intake_answer.add_argument("--actor", required=True, help="Human participant recording the answer.")
    intake_answer.add_argument("--source", default="CLI", choices=sorted(_INTAKE.SOURCE_IDS), help="Bounded answer provenance source.")
    intake_answer.add_argument("--question-id", help="Current Q-* ID for a plain-text answer.")
    intake_answer.add_argument("--answer", help="Plain-text answer for --question-id.")
    intake_answer.add_argument("--decision-id", help="Current bound D-NNN ID for an explicit option choice.")
    intake_answer.add_argument("--choice", help="Allowed option ID for --decision-id.")
    intake_answer.add_argument("--json", action="store_true", help="Emit the refreshed stable public state as JSON.")
    intake_evidence = intake_commands.add_parser(
        "establish-fact",
        help="Satisfy only the current readiness fact from a cited safe evidence pointer; records no human choice.",
    )
    _root_argument(intake_evidence)
    _effort_argument(intake_evidence)
    intake_evidence.add_argument("--expect-revision", type=int, required=True, help="Exact intake revision CAS guard.")
    intake_evidence.add_argument("--actor", required=True, help="Human or agent that inspected the cited evidence.")
    intake_evidence.add_argument("--source", default="CLI", choices=sorted(_INTAKE.SOURCE_IDS))
    intake_evidence.add_argument("--question-id", required=True, help="Current readiness-fact Q-* ID.")
    intake_evidence.add_argument("--fact", required=True, help="Plain-language fact established by the evidence.")
    intake_evidence.add_argument(
        "--evidence-pointer",
        required=True,
        help="Indexed E-NNN, safe project-relative regular file, or credential-free HTTPS primary source.",
    )
    intake_evidence.add_argument("--json", action="store_true", help="Emit the refreshed stable public state as JSON.")
    intake_revalidate_fact = intake_commands.add_parser(
        "revalidate-fact",
        help="Append-only replacement of a prior UNKNOWN fact; opens a new dependent human route Decision.",
    )
    _root_argument(intake_revalidate_fact)
    _effort_argument(intake_revalidate_fact)
    intake_revalidate_fact.add_argument("--expect-revision", type=int, required=True, help="Exact intake revision CAS guard.")
    intake_revalidate_fact.add_argument("--actor", required=True, help="Human answerer or evidence inspector.")
    intake_revalidate_fact.add_argument("--source", default="CLI", choices=sorted(_INTAKE.SOURCE_IDS))
    intake_revalidate_fact.add_argument("--question-id", required=True, help="Earlier readiness-fact Q-* ID whose original answer is UNKNOWN.")
    intake_revalidate_fact.add_argument("--answer", help="Normal human answer replacing the earlier UNKNOWN fact.")
    intake_revalidate_fact.add_argument("--fact", help="Plain-language fact established by --evidence-pointer.")
    intake_revalidate_fact.add_argument(
        "--evidence-pointer",
        help="Indexed E-NNN, safe project-relative regular file, or credential-free HTTPS primary source.",
    )
    intake_revalidate_fact.add_argument("--json", action="store_true", help="Emit the refreshed stable public state as JSON.")
    intake_tech = intake_commands.add_parser(
        "propose-tech",
        help="Import grounded named technology alternatives without selecting or implementing one.",
    )
    _root_argument(intake_tech)
    _effort_argument(intake_tech)
    intake_tech.add_argument("--expect-revision", type=int, required=True, help="Exact intake revision CAS guard.")
    intake_tech.add_argument("--actor", required=True, help="Human or agent that prepared the grounded comparison.")
    intake_tech.add_argument("--source", default="CLI", choices=sorted(_INTAKE.SOURCE_IDS))
    intake_tech.add_argument("--options-file", type=Path, required=True, help="Bounded JSON object containing an options array.")
    intake_tech.add_argument("--json", action="store_true", help="Emit the refreshed stable public state as JSON.")
    intake_revise_tech = intake_commands.add_parser(
        "revise-tech",
        help="Replace the current open named-technology comparison under revision CAS without selecting an option.",
    )
    _root_argument(intake_revise_tech)
    _effort_argument(intake_revise_tech)
    intake_revise_tech.add_argument("--expect-revision", type=int, required=True, help="Exact intake revision CAS guard.")
    intake_revise_tech.add_argument("--decision-id", required=True, help="Current bound Q-SW-012 Decision ID.")
    intake_revise_tech.add_argument("--actor", required=True, help="Human or agent that prepared the revised grounded comparison.")
    intake_revise_tech.add_argument("--source", default="CLI", choices=sorted(_INTAKE.SOURCE_IDS))
    intake_revise_tech.add_argument("--options-file", type=Path, required=True, help="Bounded JSON object containing the replacement options array.")
    intake_revise_tech.add_argument("--json", action="store_true", help="Emit the refreshed stable public state as JSON.")
    intake_option = intake_commands.add_parser(
        "propose-option",
        help="Append one human-proposed option to the current open non-comparison Decision; does not select it.",
    )
    _root_argument(intake_option)
    _effort_argument(intake_option)
    intake_option.add_argument("--expect-revision", type=int, required=True, help="Exact intake revision CAS guard.")
    intake_option.add_argument("--decision-id", required=True, help="Current bound open Decision ID.")
    intake_option.add_argument("--actor", required=True, help="Human participant proposing the option.")
    intake_option.add_argument("--source", default="CLI", choices=sorted(_INTAKE.SOURCE_IDS))
    intake_option.add_argument("--option-id", required=True, help="Stable canonical ID for the proposed option.")
    intake_option.add_argument("--label", required=True, help="Plain-language option label.")
    intake_option.add_argument("--description", required=True, help="Plain-language consequence or meaning.")
    intake_option.add_argument("--json", action="store_true", help="Emit the refreshed stable public state as JSON.")
    intake_workstream = intake_commands.add_parser(
        "add-workstream",
        help="Explicitly add one bounded secondary domain workstream after primary-domain confirmation.",
    )
    _root_argument(intake_workstream)
    _effort_argument(intake_workstream)
    intake_workstream.add_argument("--expect-revision", type=int, required=True, help="Exact intake revision CAS guard.")
    intake_workstream.add_argument("--actor", required=True, help="Human making the secondary-workstream choice.")
    intake_workstream.add_argument("--source", default="CLI", choices=sorted(_INTAKE.SOURCE_IDS))
    intake_workstream.add_argument("--domain", required=True, choices=list(_INTAKE.DOMAIN_IDS))
    intake_workstream.add_argument("--outcome", required=True, help="Observable outcome owned by this workstream.")
    intake_workstream.add_argument("--authority", required=True, help="Human authority for the workstream choices.")
    intake_workstream.add_argument("--json", action="store_true", help="Emit the refreshed stable public state as JSON.")
    return result


def _doctor_text(state: dict) -> str:
    health = state["health"]
    lines = [
        f"Wayfinder doctor: {health['status']}",
        f"Errors: {len(health['issues'])} | Warnings: {len(health['warnings'])}",
    ]
    for item in health["issues"] + health["warnings"]:
        location = f" [{item['node_id']}]" if item.get("node_id") else ""
        lines.append(f"- {item['severity'].upper()} {item['code']}{location}: {item['message']}")
    if not health["issues"] and not health["warnings"]:
        lines.append("No structural problems found.")
    return "\n".join(lines) + "\n"


def _revalidation_text(state: dict, gate_id: str | None) -> str:
    lines = ["Wayfinder revalidation check (read-only)"]
    if gate_id:
        gate = next((node for node in state["nodes"] if node["id"] == gate_id), None)
        if gate is None:
            raise WayfinderError(f"Unknown gate {gate_id}.")
        if gate["kind"] != "gate":
            raise WayfinderError(f"{gate_id} is a decision, not a delivery gate.")
        lines.extend(
            [
                f"Gate: {gate['id']} — {gate['title']}",
                f"Status: {gate['status']}",
                "Revalidates: " + (", ".join(gate["revalidates"]) or "none declared"),
            ]
        )
    recommendation = state["run_recommendation"]
    lines.extend(
        [
            f"Recommendation: {recommendation['label']}",
            f"Why: {recommendation['reason']}",
            "No decisions, gates, or project files were changed.",
        ]
    )
    return "\n".join(lines) + "\n"


def _completion_text(state: dict) -> str:
    eligible = bool(state["exit"]["pre_spec_ready"])
    lines = [
        "Wayfinder completion check (read-only)",
        f"Planning exit eligible: {'yes' if eligible else 'no'}",
        f"Pre-spec exit eligible: {'yes' if eligible else 'no'} (compatibility label)",
    ]
    if eligible:
        handoff = state["exit"].get("execution_handoff", "execution planning")
        lines.append(f"The route may be handed to {handoff}; delivery gates remain for execution-time evaluation.")
    else:
        unresolved = state["exit"]["unresolved_destination_decisions"]
        if unresolved:
            lines.append("Unresolved destination decisions: " + ", ".join(unresolved))
        if state["exit"]["high_impact_open_assumptions"]:
            lines.append("Open high-impact assumptions: " + ", ".join(state["exit"]["high_impact_open_assumptions"]))
        if state["exit"]["unformulated_fog"]:
            lines.append(f"Unformulated fog items: {len(state['exit']['unformulated_fog'])}")
        if state["health"]["issues"]:
            lines.append(f"Structural errors: {len(state['health']['issues'])}")
        if any(item["code"] == "MIGRATION_REQUIRED_FOR_COMPLETION" for item in state["health"]["issues"]):
            lines.append("Migration required: run `wayfinder migrate --check`, then create and validate the schema-3 proof artifacts before completing.")
    lines.append("No EXIT.md or project artifact was written.")
    return "\n".join(lines) + "\n"


def _intake_text(state: dict) -> str:
    intake = state.get("intake") if isinstance(state.get("intake"), dict) else {}
    lines = [
        "Wayfinder conversational intake",
        f"Status: {intake.get('status', 'NOT_STARTED')}",
        f"Revision: {intake.get('revision', 0)}",
    ]
    domain = intake.get("domain")
    if isinstance(domain, dict):
        selected = domain.get("primary_domain") or domain.get("selected")
        lines.append(f"Primary domain: {selected or 'awaiting explicit choice'}")
        suggestions = domain.get("suggested_secondary_domains")
        if isinstance(suggestions, list) and suggestions:
            lines.append("Secondary confirmation recommended: " + ", ".join(str(item) for item in suggestions))
    question = intake.get("current_question")
    if isinstance(question, dict):
        lines.extend([f"Current question: {question.get('id')}", str(question.get("prompt") or "")])
        decision_id = question.get("decision_id")
        if decision_id:
            lines.append(f"Bound Decision: {decision_id}")
        options = question.get("options")
        if isinstance(options, list) and options:
            lines.append("Allowed options:")
            for option in options:
                if isinstance(option, dict):
                    lines.append(f"- {option.get('id')}: {option.get('label')}")
    else:
        lines.append("No unanswered intake question remains.")
    lines.append("No implementation or external action was authorized.")
    return "\n".join(lines) + "\n"


def _manifest_state(state: dict) -> str:
    """Read the fixed pre-truncation manifest classification; unknown fails closed."""
    contract = state.get("manifest_contract")
    if not isinstance(contract, dict):
        return "invalid-manifest"
    value = contract.get("state")
    if value in {"absent-legacy-v2", "invalid-manifest", "unsupported-schema", "schema-3"}:
        return value
    return "invalid-manifest"


def _lifecycle_guard_failure(state: dict) -> str | None:
    """Return a fixed fail-closed reason for commands that imply V3 lifecycle use."""
    manifest_state = _manifest_state(state)
    if manifest_state == "absent-legacy-v2":
        return "Migration required: this effort has no schema-3 EFFORT.json; preview migration before lifecycle use."
    if manifest_state == "invalid-manifest":
        return "EFFORT.json is invalid or unsafe; recover it before lifecycle use."
    if manifest_state == "unsupported-schema":
        return "EFFORT.json is not schema 3; use migration recovery before lifecycle use."
    contract = state.get("manifest_contract")
    if not isinstance(contract, dict) or contract.get("lifecycle_ready") is not True:
        return "Wayfinder doctor reports structural errors; repair them before lifecycle use."
    return None


def _lifecycle_block_text(command: str, reason: str) -> str:
    return (
        f"Wayfinder {command} check (read-only)\n"
        "Lifecycle command allowed: no\n"
        f"Blocked: {reason}\n"
        "No decisions, receipts, or project files were changed.\n"
    )


def _migration_preview(state: dict) -> dict:
    manifest_state = _manifest_state(state)
    legacy = manifest_state == "absent-legacy-v2"
    unsupported = manifest_state == "unsupported-schema"
    invalid = manifest_state == "invalid-manifest"
    contract = state.get("manifest_contract") if isinstance(state.get("manifest_contract"), dict) else {}
    doctor_passed = contract.get("doctor_passed") is True
    inferred_gates = sorted(
        item["node_id"]
        for item in state["diagnostics"]
        if item["code"] == "LEGACY_GATE_INFERRED" and item.get("node_id")
    )
    structural_errors = [
        item
        for item in state["health"]["issues"]
        if item["code"] != "MIGRATION_REQUIRED_FOR_COMPLETION"
    ]
    return {
        "mode": "preview-only",
        "manifest_state": manifest_state,
        "needed": legacy or unsupported or invalid,
        "migration_required": legacy or unsupported,
        "recovery_required": unsupported or invalid,
        "repair_required": manifest_state == "schema-3" and not doctor_passed,
        "doctor_passed": doctor_passed,
        "safe_to_generate_new_manifest": legacy,
        "would_write": ["EFFORT.json"] if legacy else [],
        "decision_count": state["counts"]["decisions"],
        "inferred_gates_requiring_confirmation": inferred_gates,
        "structural_errors_to_fix_first": len(structural_errors),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command in {"init", "start"}:
            init_args = [
                "--root",
                str(args.root),
                "--slug",
                args.slug,
                "--destination",
                args.destination,
            ]
            if args.expect_active is not None:
                init_args.extend(["--expect-active", args.expect_active])
            return init_wayfinder.main(init_args)
        if args.command in {"dashboard", "serve"}:
            serve(
                args.root,
                args.effort,
                port=args.port,
                quiet=args.quiet,
                decision_recording=args.decision_recording,
            )
            return 0

        if args.command == "intake":
            if args.intake_command == "start":
                state = _INTAKE.start_intake(args.root, args.effort, args.intent)
            elif args.intake_command == "answer":
                text_mode = args.question_id is not None or args.answer is not None
                choice_mode = args.decision_id is not None or args.choice is not None
                if text_mode == choice_mode:
                    raise _INTAKE.IntakeError(
                        "Use exactly one pair: --question-id with --answer, or --decision-id with --choice.",
                        "INTAKE_VALIDATION",
                        422,
                    )
                if text_mode:
                    if args.question_id is None or args.answer is None:
                        raise _INTAKE.IntakeError("Both --question-id and --answer are required.", "INTAKE_VALIDATION", 422)
                    state = _INTAKE.record_intake_answer(
                        args.root, args.effort, args.question_id, args.expect_revision, args.actor, args.source, args.answer
                    )
                else:
                    if args.decision_id is None or args.choice is None:
                        raise _INTAKE.IntakeError("Both --decision-id and --choice are required.", "INTAKE_VALIDATION", 422)
                    state = _INTAKE.record_intake_choice(
                        args.root, args.effort, args.decision_id, args.expect_revision, args.actor, args.source, args.choice
                    )
            elif args.intake_command == "propose-tech":
                options = _INTAKE.load_technology_options(args.options_file)
                state = _INTAKE.propose_technology_options(
                    args.root, args.effort, args.expect_revision, args.actor, args.source, options
                )
            elif args.intake_command == "revise-tech":
                options = _INTAKE.load_technology_options(args.options_file)
                state = _INTAKE.replace_technology_options(
                    args.root,
                    args.effort,
                    args.decision_id,
                    args.expect_revision,
                    args.actor,
                    args.source,
                    options,
                )
            elif args.intake_command == "propose-option":
                state = _INTAKE.propose_intake_alternative(
                    args.root,
                    args.effort,
                    args.decision_id,
                    args.expect_revision,
                    args.actor,
                    args.source,
                    {"id": args.option_id, "label": args.label, "description": args.description},
                )
            elif args.intake_command == "establish-fact":
                state = _INTAKE.record_intake_evidence_answer(
                    args.root,
                    args.effort,
                    args.question_id,
                    args.expect_revision,
                    args.actor,
                    args.source,
                    args.fact,
                    args.evidence_pointer,
                )
            elif args.intake_command == "revalidate-fact":
                human_mode = args.answer is not None
                evidence_mode = args.fact is not None or args.evidence_pointer is not None
                if human_mode == evidence_mode:
                    raise _INTAKE.IntakeError(
                        "Use exactly one mode: --answer, or --fact with --evidence-pointer.",
                        "INTAKE_VALIDATION",
                        422,
                    )
                if evidence_mode and (args.fact is None or args.evidence_pointer is None):
                    raise _INTAKE.IntakeError(
                        "Both --fact and --evidence-pointer are required for evidence-established revalidation.",
                        "INTAKE_VALIDATION",
                        422,
                    )
                state = _INTAKE.revalidate_intake_fact(
                    args.root,
                    args.effort,
                    args.question_id,
                    args.expect_revision,
                    args.actor,
                    args.source,
                    args.answer if human_mode else args.fact,
                    None if human_mode else args.evidence_pointer,
                )
            elif args.intake_command == "add-workstream":
                state = _INTAKE.add_secondary_workstream(
                    args.root,
                    args.effort,
                    args.expect_revision,
                    args.actor,
                    args.source,
                    args.domain,
                    args.outcome,
                    args.authority,
                )
            else:
                state = build_state(args.root, args.effort)
            if args.json:
                _write_terminal(state_json(state))
            else:
                _write_terminal(_intake_text(_terminal_safe_data(state)))
            return 1 if state.get("intake", {}).get("status") in {"INVALID", "RECOVERY_REQUIRED"} else 0

        state = build_state(args.root, args.effort)
        if args.command == "migrate":
            preview = _migration_preview(state)
            if args.json:
                _write_terminal(json.dumps(preview, indent=2, sort_keys=True) + "\n")
            else:
                _write_terminal("Wayfinder migration check (preview-only)\n")
                _write_terminal(f"Manifest state: {preview['manifest_state']}\n")
                _write_terminal(f"Migration or recovery needed: {'yes' if preview['needed'] else 'no'}\n")
                _write_terminal(f"Recovery required: {'yes' if preview['recovery_required'] else 'no'}\n")
                _write_terminal(f"Schema-3 repair required: {'yes' if preview['repair_required'] else 'no'}\n")
                _write_terminal("No project files were changed.\n")
            return 1 if preview["structural_errors_to_fix_first"] else 0

        if args.command in {"resume", "revalidate", "complete"}:
            guard_failure = _lifecycle_guard_failure(state)
            if guard_failure is not None:
                if args.json:
                    _write_terminal(state_json(state))
                else:
                    _write_terminal(_lifecycle_block_text(args.command, guard_failure))
                return 1

        if args.command == "revalidate" and args.gate:
            args.gate = args.gate.strip().upper()
            gate = next((node for node in state["nodes"] if node["id"] == args.gate), None)
            if gate is None:
                raise WayfinderError(f"Unknown gate {args.gate}.")
            if gate["kind"] != "gate":
                raise WayfinderError(f"{args.gate} is a decision, not a delivery gate.")

        if args.json:
            _write_terminal(state_json(state))
        else:
            terminal_state = _terminal_safe_data(state)
            if args.command in {"status", "resume"}:
                _write_terminal(status_text(terminal_state))
            elif args.command == "revalidate":
                _write_terminal(_revalidation_text(terminal_state, args.gate))
            elif args.command == "complete":
                _write_terminal(_completion_text(terminal_state))
            else:
                _write_terminal(_doctor_text(terminal_state))
        if args.command == "doctor" and state["health"]["issues"]:
            return 1
        if args.command == "complete" and not state["exit"]["pre_spec_ready"]:
            return 1
        return 0
    except (_INTAKE.IntakeError, WayfinderError, OSError, UnicodeError, ValueError) as exc:
        print(terminal_safe_text(f"wayfinder: {exc}"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
