#!/usr/bin/env python3
"""Deterministic, host-neutral Wayfinder conversational intake mechanics."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, Callable, Iterator, Mapping, Sequence
import unicodedata
from urllib.parse import urlsplit

try:
    import fcntl
except ImportError:  # pragma: no cover - supported hosts are POSIX today.
    fcntl = None


INTAKE_SCHEMA_VERSION = 1
INTAKE_FILENAME = "INTAKE.json"
INTAKE_ID = "INTAKE-001"
FLOW_VERSION = 2
MAX_INTAKE_BYTES = 2 * 1024 * 1024
MAX_TEXT_ANSWER = 4_000
MAX_PUBLIC_ITEMS = 1_000
MAX_QUESTIONS = 96
MAX_RECEIPTS = MAX_QUESTIONS + 32
MAX_FACT_REVALIDATIONS = 16
DOMAIN_IDS = ("SOFTWARE", "GENERAL_PROJECT", "FINANCE_REPORTING", "OTHER")
SOURCE_IDS = {"CHAT", "CLI", "DASHBOARD", "API", "OTHER"}
DECISION_ID = re.compile(r"D-\d{3,}")
EVIDENCE_ID = re.compile(r"E-\d{3,}")
QUESTION_ID = re.compile(r"Q-(?:\d{3}|[A-Z]{2,4}-\d{3})")
OPTION_ID = re.compile(r"[A-Z][A-Z0-9_-]{1,63}")
SAFE_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
AMBIGUOUS = {
    "",
    "-",
    "NONE",
    "UNKNOWN",
    "TBD",
    "TBC",
    "TODO",
    "PENDING",
    "UNASSIGNED",
    "NOT ASSIGNED",
    "NOT RECORDED",
    "N/A",
}

COMMON_ORDER = (
    "Q-002",
    "Q-003",
    "Q-004",
    "Q-005",
    "Q-006",
    "Q-007",
)
DOMAIN_ORDER = {
    "SOFTWARE": (
        "Q-SW-001", "Q-SW-002", "Q-SW-003", "Q-SW-004", "Q-SW-005", "Q-SW-006",
        "Q-SW-007", "Q-SW-008", "Q-SW-009", "Q-SW-010", "Q-SW-011",
    ),
    "GENERAL_PROJECT": (
        "Q-GP-001", "Q-GP-002", "Q-GP-003", "Q-GP-004", "Q-GP-005", "Q-GP-006",
        "Q-GP-007", "Q-GP-008", "Q-GP-009", "Q-GP-010", "Q-GP-011", "Q-GP-012", "Q-GP-013",
    ),
    "FINANCE_REPORTING": (
        "Q-FR-001", "Q-FR-002", "Q-FR-003", "Q-FR-004", "Q-FR-005", "Q-FR-006",
        "Q-FR-007", "Q-FR-008", "Q-FR-009", "Q-FR-010", "Q-FR-011", "Q-FR-012",
        "Q-FR-013", "Q-FR-014",
    ),
    "OTHER": ("Q-OT-001", "Q-OT-002"),
}
FINAL_QUESTION = {
    "SOFTWARE": "Q-SW-012",
    "GENERAL_PROJECT": "Q-GP-013",
    "FINANCE_REPORTING": "Q-FR-014",
    "OTHER": "Q-OT-002",
}
COMPARISON_QUESTION = {
    "SOFTWARE": "Q-SW-011",
    "GENERAL_PROJECT": "Q-GP-013",
    "FINANCE_REPORTING": "Q-FR-014",
    "OTHER": "Q-OT-002",
}
INTAKE_SCAFFOLD_ERROR_CODES = {
    "CHECKPOINT_ORDER_INVALID",
    "MAP_SUCCESS_CONDITIONS_INVALID",
    "MAP_CONSTRAINTS_REQUIRED",
    "MAP_SCOPE_BOUNDARY_REQUIRED",
}
REGULATORY_REQUIRED_FACTS = {"Q-FR-004", "Q-FR-005", "Q-FR-011"}
COMPARISON_FACT_KEYS = {
    "SOFTWARE": (
        "software_current_environment",
        "software_integrations",
        "software_team_fit",
        "software_operations_owner",
        "software_delivery_constraints",
        "software_cost_lockin",
    ),
    "GENERAL_PROJECT": (
        "project_site",
        "project_schedule",
        "project_vendors",
        "project_permits",
        "project_resources",
        "project_safety",
        "project_dependencies",
        "project_acceptance",
        "project_contingency",
    ),
    "FINANCE_REPORTING": (
        "finance_jurisdiction",
        "finance_basis",
        "finance_period_cutoff",
        "finance_currency_materiality",
        "finance_source_lineage",
        "finance_reconciliation",
        "finance_controls",
        "finance_signoff",
        "finance_format",
        "finance_deadline",
    ),
}

FACT_COMPARISON_DOMAIN = {
    key: domain
    for domain, keys in COMPARISON_FACT_KEYS.items()
    for key in keys
}


class IntakeError(RuntimeError):
    """A bounded, non-reflective intake failure safe for CLI/API display."""

    def __init__(self, message: str, code: str = "INTAKE_VALIDATION", http_status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _contains_unsafe(value: str) -> bool:
    return bool(CONTROL.search(value)) or any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        for character in value
    )


def _bounded_text(value: Any, label: str, maximum: int, *, allow_ambiguous: bool = False) -> str:
    if not isinstance(value, str):
        raise IntakeError(f"{label} must be plain text.")
    result = value.strip()
    if not result or len(result) > maximum or _contains_unsafe(result):
        raise IntakeError(f"{label} must be one printable line of at most {maximum} characters.")
    if not allow_ambiguous and result.upper() in AMBIGUOUS:
        raise IntakeError(f"{label} must be explicit rather than a placeholder.")
    return result


def _human_actor(value: Any) -> str:
    try:
        actor = _bounded_text(value, "Actor", 120)
    except IntakeError as exc:
        raise IntakeError("Actor must identify a human participant.", "INTAKE_INVALID_ACTOR", 422) from exc
    if actor.lower() in {"codex", "agent", "ai", "assistant", "system", "automation", "wayfinder"}:
        raise IntakeError("Actor must identify the human who supplied the answer.", "INTAKE_INVALID_ACTOR", 422)
    return actor


def _source(value: Any) -> str:
    if not isinstance(value, str) or value.strip().upper() not in SOURCE_IDS:
        raise IntakeError("Source must be CHAT, CLI, DASHBOARD, API, or OTHER.")
    return value.strip().upper()


def _json_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IntakeError("JSON contains duplicate object keys.")
        result[key] = value
    return result


def _parse_json(payload: bytes, label: str) -> dict[str, Any]:
    if len(payload) > MAX_INTAKE_BYTES:
        raise IntakeError(f"{label} exceeds the intake safety limit.")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_json_object_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(IntakeError("JSON constants are invalid.")),
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError, IntakeError) as exc:
        raise IntakeError(f"{label} is invalid or unsafe.") from exc
    if not isinstance(value, dict):
        raise IntakeError(f"{label} must contain a JSON object.")
    return value


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise IntakeError("Intake state cannot be serialized safely.") from exc


def _option(option_id: str, label: str, description: str) -> dict[str, str]:
    return {"id": option_id, "label": label, "description": description}


DOMAIN_OPTIONS = [
    _option("SOFTWARE", "Software or digital product", "An application, API, automation, data system, or other software product."),
    _option("GENERAL_PROJECT", "General project", "A construction, operations, event, physical delivery, or organizational project."),
    _option("FINANCE_REPORTING", "Finance and reporting", "Accounting, close, consolidation, reconciliation, forecasting, or financial reporting work."),
    _option("OTHER", "Another domain", "A project that does not fit the software, general-project, or finance-reporting branches."),
]


def _choice_question(
    question_id: str,
    key: str,
    prompt: str,
    options: Sequence[Mapping[str, str]],
    why: str,
    title: str,
    *,
    destination_blocking: bool = True,
) -> dict[str, Any]:
    return {
        "id": question_id,
        "key": key,
        "prompt": prompt,
        "answer_type": "choice",
        "required": True,
        "human_choice_required": True,
        "options": [dict(option) for option in options],
        "why": why,
        "decision_title": title,
        "destination_blocking": destination_blocking,
    }


def _text_question(question_id: str, key: str, prompt: str, why: str, maximum: int = 2_000) -> dict[str, Any]:
    return {
        "id": question_id,
        "key": key,
        "prompt": prompt,
        "answer_type": "text",
        "required": True,
        "human_choice_required": False,
        "options": [],
        "why": why,
        "max_length": min(maximum, MAX_TEXT_ANSWER),
        "decision_title": None,
        "destination_blocking": False,
    }


def _fact_question(question_id: str, key: str, label: str) -> dict[str, Any]:
    return {
        "id": question_id,
        "key": key,
        "prompt": f"What should the route record about the {label}?",
        "answer_type": "fact",
        "required": True,
        "human_choice_required": False,
        "options": [],
        "why": (
            f"Planning readiness cannot silently skip the {label}. A normal answer is accepted; "
            "if it is unknown, name the owner, and if it is not applicable, give the reason."
        ),
        "max_length": 2_000,
        "decision_title": None,
        "destination_blocking": False,
        "fact_label": label,
    }


def _validate_fact_answer(value: Any, maximum: int = 2_000) -> dict[str, str]:
    answer = _bounded_text(value, "Readiness fact", maximum)
    established = re.fullmatch(r"ESTABLISHED:\s*(.+?);\s*EVIDENCE:\s*(.+)", answer, flags=re.IGNORECASE)
    unknown = re.fullmatch(r"UNKNOWN:\s*(.+?);\s*OWNER:\s*(.+)", answer, flags=re.IGNORECASE)
    not_applicable = re.fullmatch(r"N/A:\s*(.+)", answer, flags=re.IGNORECASE)
    if established:
        detail = _bounded_text(established.group(1), "Established fact", 1_000)
        support = _bounded_text(established.group(2), "Evidence pointer", 500)
        return {"readiness": "ESTABLISHED", "detail": detail, "support": support}
    if unknown:
        detail = _bounded_text(unknown.group(1), "Unknown fact", 1_000)
        support = _human_actor(unknown.group(2))
        return {"readiness": "UNKNOWN", "detail": detail, "support": support}
    if not_applicable:
        detail = _bounded_text(not_applicable.group(1), "Not-applicable reason", 1_000)
        return {"readiness": "NOT_APPLICABLE", "detail": detail, "support": detail}
    friendly_unknown = re.fullmatch(r"UNKNOWN\s*[—:-]\s*(.*?)(?:;|,)?\s*OWNER\s*[:—-]\s*(.+)", answer, flags=re.IGNORECASE)
    if friendly_unknown:
        detail = _bounded_text(friendly_unknown.group(1) or "Not yet established", "Unknown fact", 1_000)
        support = _human_actor(friendly_unknown.group(2))
        return {"readiness": "UNKNOWN", "detail": detail, "support": support}
    friendly_na = re.fullmatch(r"(?:N/?A|NOT APPLICABLE)\s*[—:-]\s*(.+)", answer, flags=re.IGNORECASE)
    if friendly_na:
        detail = _bounded_text(friendly_na.group(1), "Not-applicable reason", 1_000)
        return {"readiness": "NOT_APPLICABLE", "detail": detail, "support": detail}
    if re.match(r"^(?:ESTABLISHED|UNKNOWN|N/?A|NOT APPLICABLE)\s*[:\u2014-]", answer, flags=re.IGNORECASE):
        raise IntakeError(
            "A structured readiness answer is incomplete; include its required evidence, owner, or reason.",
            "INTAKE_VALIDATION",
            422,
        )
    return {"readiness": "HUMAN_ANSWERED", "detail": answer, "support": "Human answer receipt"}


QUESTION_DEFINITIONS: dict[str, dict[str, Any]] = {
    "Q-001": _choice_question(
        "Q-001",
        "domain",
        "Which kind of effort is this?",
        DOMAIN_OPTIONS,
        "The answer chooses the vocabulary and route comparisons; Wayfinder will not assume software.",
        "Confirm the effort domain",
        destination_blocking=False,
    ),
    "Q-002": _text_question("Q-002", "desired_outcome", "What observable outcome should exist when this effort succeeds?", "This becomes the canonical destination."),
    "Q-003": _text_question("Q-003", "success_condition", "What single observable condition would prove the destination was reached?", "This becomes the first success-condition row."),
    "Q-004": _text_question("Q-004", "success_evidence", "What evidence would verify that success condition?", "The route must say how success is demonstrated."),
    "Q-005": _text_question("Q-005", "constraints", "What is the most important constraint or authority boundary?", "The route must respect a concrete constraint."),
    "Q-006": _text_question("Q-006", "out_of_scope", "What is explicitly outside this effort?", "A clear boundary prevents scope drift."),
    "Q-007": _text_question("Q-007", "decision_authority", "Who has authority to approve the material route choices?", "Wayfinder must not answer the human side of a consequential choice."),
    "Q-SW-001": _choice_question(
        "Q-SW-001", "software_product_form", "What are we primarily planning?",
        [
            _option("WEB-APP", "Web application", "A browser-based product or internal tool."),
            _option("MOBILE-APP", "Mobile application", "A phone or tablet experience."),
            _option("API-AUTOMATION", "API or automation", "A service, integration, workflow, or background automation."),
            _option("DATA-SYSTEM", "Data or analytics system", "A pipeline, warehouse, reporting product, or data service."),
            _option("SOFTWARE-OTHER", "Other software", "A software shape not covered by the listed options."),
        ],
        "Product form changes delivery and technology tradeoffs.", "Choose the software product form",
    ),
    "Q-SW-002": _choice_question(
        "Q-SW-002", "software_priority", "Which tradeoff should dominate the first technology recommendation?",
        [
            _option("MVP-SPEED", "MVP speed", "Prioritize the shortest credible route to a usable first release."),
            _option("BALANCED", "Balanced path", "Balance delivery speed with durability and manageable operations."),
            _option("SCALE", "Scale beyond MVP", "Prioritize future load, growth, and change capacity."),
            _option("RELIABILITY", "Reliability", "Prioritize predictable behavior and operational resilience."),
            _option("LOW-COST", "Low cost", "Prioritize a low initial and ongoing cost envelope."),
            _option("PRIVACY-CONTROL", "Security and privacy control", "Prioritize data control, isolation, and auditability."),
        ],
        "The recommendation is transparent only when its dominant tradeoff is explicit.", "Choose the software technology priority",
    ),
    "Q-SW-003": _choice_question(
        "Q-SW-003", "software_scale", "What scale should the route plan for beyond the MVP?",
        [
            _option("SMALL", "Small and bounded", "A small user group or predictable workload."),
            _option("GROWING", "Growing", "Meaningful growth is expected but exact demand is uncertain."),
            _option("HIGH", "High or bursty", "Large, global, real-time, or sharply variable demand is plausible."),
            _option("UNKNOWN-SCALE", "Not known yet", "Scale is materially uncertain and needs an explicit gate."),
        ],
        "Scale affects architecture, operating burden, and reversibility.", "Choose the scale profile",
    ),
    "Q-SW-004": _choice_question(
        "Q-SW-004", "software_sensitivity", "What is the highest expected data sensitivity?",
        [
            _option("PUBLIC-DATA", "Public or low-sensitivity", "No material personal, confidential, or regulated data."),
            _option("INTERNAL-DATA", "Internal or confidential", "Business-confidential data requiring controlled access."),
            _option("PERSONAL-DATA", "Personal data", "User or employee data requiring privacy safeguards."),
            _option("REGULATED-DATA", "Regulated or highly sensitive", "Financial, health, identity, or other regulated data."),
            _option("UNKNOWN-SENSITIVITY", "Not known yet", "Sensitivity is uncertain and must remain a visible constraint."),
        ],
        "Privacy and security requirements can rule out otherwise fast options.", "Choose the data-sensitivity boundary",
    ),
    "Q-SW-005": _fact_question("Q-SW-005", "software_current_environment", "current codebase, environment, and reusable components"),
    "Q-SW-006": _fact_question("Q-SW-006", "software_integrations", "required integrations, data sources, and external dependencies"),
    "Q-SW-007": _fact_question("Q-SW-007", "software_team_fit", "team capability, support skills, and learning constraints"),
    "Q-SW-008": _fact_question("Q-SW-008", "software_operations_owner", "operational owner, support model, and reliability obligations"),
    "Q-SW-009": _fact_question("Q-SW-009", "software_delivery_constraints", "delivery date, platform, compliance, and deployment constraints"),
    "Q-SW-010": _fact_question("Q-SW-010", "software_cost_lockin", "cost envelope, vendor lock-in tolerance, and reversibility needs"),
    "Q-GP-001": _choice_question(
        "Q-GP-001", "project_form", "What kind of general project is this?",
        [
            _option("CONSTRUCTION", "Construction or renovation", "A physical site, facility, renovation, or build programme."),
            _option("OPERATIONS", "Operations change", "A process, supply-chain, service, or organizational operating change."),
            _option("EVENT", "Event or time-bound delivery", "A coordinated outcome with a fixed date or operating window."),
            _option("PHYSICAL-PRODUCT", "Physical product", "A manufactured, procured, or installed physical result."),
            _option("PROJECT-OTHER", "Other general project", "A nonsoftware project outside the listed forms."),
        ],
        "Project form changes safety, coordination, sequencing, and approval needs.", "Choose the general-project form",
    ),
    "Q-GP-002": _choice_question(
        "Q-GP-002", "project_priority", "Which route outcome matters most?",
        [
            _option("SCHEDULE", "Schedule", "Prioritize the fastest responsible route to the target date."),
            _option("COST-CERTAINTY", "Cost certainty", "Prioritize predictable commitments and controlled changes."),
            _option("SAFETY-QUALITY", "Safety and quality", "Prioritize assurance, inspection, and defect prevention."),
            _option("FLEXIBILITY", "Flexibility", "Preserve room to learn and change as the project develops."),
            _option("COORDINATION", "Coordination", "Prioritize alignment across owners, contractors, teams, or dependencies."),
        ],
        "A route cannot optimize schedule, certainty, assurance, and flexibility equally.", "Choose the general-project priority",
    ),
    "Q-GP-003": _choice_question(
        "Q-GP-003", "project_uncertainty", "How certain are the scope and delivery conditions?",
        [
            _option("WELL-DEFINED", "Well defined", "Scope, conditions, dependencies, and acceptance are mostly known."),
            _option("PARTLY-DEFINED", "Partly defined", "Important details are known, but discovery and coordination remain."),
            _option("HIGH-UNCERTAINTY", "Highly uncertain", "Scope, conditions, approvals, or feasibility need early learning."),
        ],
        "Uncertainty determines whether a fixed plan or staged learning route is safer.", "Choose the uncertainty profile",
    ),
    "Q-GP-004": _fact_question("Q-GP-004", "project_site", "site or operating location"),
    "Q-GP-005": _fact_question("Q-GP-005", "project_schedule", "schedule, target date, and critical timing"),
    "Q-GP-006": _fact_question("Q-GP-006", "project_vendors", "vendors, contractors, or procurement route"),
    "Q-GP-007": _fact_question("Q-GP-007", "project_permits", "permits, approvals, and jurisdiction"),
    "Q-GP-008": _fact_question("Q-GP-008", "project_resources", "people, equipment, budget, and other required resources"),
    "Q-GP-009": _fact_question("Q-GP-009", "project_safety", "safety and quality obligations"),
    "Q-GP-010": _fact_question("Q-GP-010", "project_dependencies", "external and internal dependencies"),
    "Q-GP-011": _fact_question("Q-GP-011", "project_acceptance", "inspection, acceptance, and handover criteria"),
    "Q-GP-012": _fact_question("Q-GP-012", "project_contingency", "contingency and change-control approach"),
    "Q-FR-001": _choice_question(
        "Q-FR-001", "reporting_need", "What is the primary finance or reporting need?",
        [
            _option("MONTH-END", "Month-end close", "Close activities, reconciliations, journals, and reporting timeliness."),
            _option("MANAGEMENT", "Management reporting", "Recurring internal performance and decision reporting."),
            _option("REGULATORY", "Regulatory or statutory reporting", "Auditable reporting against formal external requirements."),
            _option("CONSOLIDATION", "Consolidation", "Combining entities, currencies, ledgers, or reporting structures."),
            _option("FORECAST", "Forecasting and planning", "Budgets, forecasts, scenarios, and variance analysis."),
        ],
        "The reporting obligation determines the required controls and evidence.", "Choose the finance-reporting need",
    ),
    "Q-FR-002": _choice_question(
        "Q-FR-002", "finance_sources", "Where does the reporting data primarily come from?",
        [
            _option("SPREADSHEETS", "Spreadsheets", "Manually maintained workbooks or exported files."),
            _option("ACCOUNTING-SYSTEM", "Accounting system", "A primary ledger, ERP, or accounting platform."),
            _option("MULTIPLE-SYSTEMS", "Multiple systems", "Several operational and finance sources requiring integration."),
            _option("MIXED-SOURCES", "Mixed and partly manual", "A blend of systems, files, and manual adjustments."),
        ],
        "Source structure drives reconciliation effort, lineage, and automation feasibility.", "Choose the finance data-source profile",
    ),
    "Q-FR-003": _choice_question(
        "Q-FR-003", "finance_priority", "Which reporting quality should dominate the route?",
        [
            _option("AUDITABILITY", "Auditability and control", "Prioritize traceability, approvals, lineage, and repeatable controls."),
            _option("CLOSE-SPEED", "Close or reporting speed", "Prioritize shorter cycles and less manual waiting."),
            _option("RECONCILIATION", "Reconciliation quality", "Prioritize complete, explainable agreement across sources."),
            _option("SCALABILITY", "Scalability", "Prioritize growth across entities, periods, dimensions, and data volume."),
            _option("FINANCE-COST", "Low operating cost", "Prioritize a modest tool and maintenance footprint."),
        ],
        "The dominant quality sets the control and automation tradeoff.", "Choose the finance-reporting priority",
    ),
    "Q-FR-004": _fact_question("Q-FR-004", "finance_jurisdiction", "reporting jurisdiction and applicable authority"),
    "Q-FR-005": _fact_question("Q-FR-005", "finance_basis", "accounting or reporting basis"),
    "Q-FR-006": _fact_question("Q-FR-006", "finance_period_cutoff", "reporting period and cutoff policy"),
    "Q-FR-007": _fact_question("Q-FR-007", "finance_currency_materiality", "currencies and materiality threshold"),
    "Q-FR-008": _fact_question("Q-FR-008", "finance_source_lineage", "source systems, ownership, and lineage"),
    "Q-FR-009": _fact_question("Q-FR-009", "finance_reconciliation", "reconciliation method and exception treatment"),
    "Q-FR-010": _fact_question("Q-FR-010", "finance_controls", "preparer, reviewer, access, and change controls"),
    "Q-FR-011": _fact_question("Q-FR-011", "finance_signoff", "signoff authority and review evidence"),
    "Q-FR-012": _fact_question("Q-FR-012", "finance_format", "required report format, dimensions, and recipients"),
    "Q-FR-013": _fact_question("Q-FR-013", "finance_deadline", "delivery deadline and recurring cadence"),
    "Q-OT-001": _choice_question(
        "Q-OT-001", "other_priority", "Which quality should dominate the route comparison?",
        [
            _option("OTHER-SPEED", "Speed", "Prioritize the fastest credible route."),
            _option("OTHER-RELIABILITY", "Reliability", "Prioritize predictable repeatable outcomes."),
            _option("OTHER-COST", "Cost", "Prioritize a low commitment and operating burden."),
            _option("OTHER-REVERSIBILITY", "Reversibility", "Prioritize learning and the ability to change course."),
            _option("OTHER-GOVERNANCE", "Governance", "Prioritize authority, traceability, and controlled handoffs."),
        ],
        "The generic branch still needs an explicit decision criterion.", "Choose the route priority",
    ),
}


DOMAIN_SIGNALS: dict[str, tuple[tuple[str, int, str], ...]] = {
    "SOFTWARE": (
        ("software", 4, "SOFTWARE"), ("saas", 4, "SAAS"), ("web app", 4, "WEB_APP"),
        ("mobile app", 4, "MOBILE_APP"), ("api", 3, "API"), ("database", 3, "DATABASE"),
        ("frontend", 3, "FRONTEND"), ("backend", 3, "BACKEND"), ("code", 2, "CODE"),
        ("automation", 2, "AUTOMATION"), ("automate", 2, "AUTOMATION"),
        ("digital product", 3, "DIGITAL_PRODUCT"),
    ),
    "GENERAL_PROJECT": (
        ("construction", 5, "CONSTRUCTION"), ("renovation", 5, "RENOVATION"),
        ("contractor", 4, "CONTRACTOR"), ("building site", 5, "BUILDING_SITE"),
        ("warehouse operations", 4, "WAREHOUSE_OPERATIONS"), ("supply chain", 4, "SUPPLY_CHAIN"),
        ("event", 3, "EVENT"), ("facility", 3, "FACILITY"), ("procurement", 3, "PROCUREMENT"),
        ("operations", 2, "OPERATIONS"),
    ),
    "FINANCE_REPORTING": (
        ("accounting", 5, "ACCOUNTING"), ("month-end", 5, "MONTH_END"),
        ("month end", 5, "MONTH_END"), ("financial reporting", 5, "FINANCIAL_REPORTING"),
        ("balance sheet", 5, "BALANCE_SHEET"), ("profit and loss", 5, "PROFIT_AND_LOSS"),
        ("p&l", 4, "PROFIT_AND_LOSS"), ("reconciliation", 4, "RECONCILIATION"),
        ("journal entries", 4, "JOURNAL_ENTRIES"), ("consolidation", 4, "CONSOLIDATION"),
        ("ifrs", 5, "IFRS"), ("gaap", 5, "GAAP"), ("bookkeeping", 4, "BOOKKEEPING"),
        ("forecast", 3, "FORECAST"),
    ),
}


def classify_intent(intent: str) -> dict[str, Any]:
    """Return a deterministic proposal that always awaits explicit confirmation."""
    text = _bounded_text(intent, "Intent", 2_000).casefold()
    scores = {domain: 0 for domain in DOMAIN_IDS}
    signals: dict[str, list[str]] = {domain: [] for domain in DOMAIN_IDS}
    for domain, rules in DOMAIN_SIGNALS.items():
        for phrase, weight, signal in rules:
            if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text):
                scores[domain] += weight
                signals[domain].append(signal)
    ranked = sorted(
        ((score, domain) for domain, score in scores.items() if domain != "OTHER"),
        key=lambda item: (-item[0], DOMAIN_IDS.index(item[1])),
    )
    top_score, top_domain = ranked[0]
    second_score = ranked[1][0]
    tied = top_score > 0 and sum(1 for score, _domain in ranked if score == top_score) > 1
    if top_score == 0 or tied:
        proposed = "OTHER"
    else:
        proposed = top_domain
    gap = top_score - second_score
    if top_score >= 6 and gap >= 3:
        confidence = "HIGH"
    elif top_score >= 3 and gap >= 2:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    material_secondary = [
        domain
        for score, domain in ranked
        if score >= 2 and domain != proposed
    ][:3]
    hybrid_candidate = top_score > 0 and bool(material_secondary)
    ambiguous = top_score == 0 or tied or confidence == "LOW" or hybrid_candidate
    matched = sorted({signal for values in signals.values() for signal in values})[:64]
    alternatives = [domain for score, domain in ranked if score > 0 and domain != proposed][:3]
    return {
        "proposed": proposed,
        "confidence": confidence,
        "ambiguous": ambiguous,
        "hybrid_candidate": hybrid_candidate,
        "signals": matched,
        "alternatives": alternatives,
        "suggested_secondary_domains": material_secondary,
        "selected": None,
        "selected_option": None,
        "primary_domain": None,
        "secondary_workstreams": [],
        "selection_source": None,
    }


def _answers_by_key(intake: Mapping[str, Any]) -> dict[str, str]:
    """Return effective answers while retaining original answers as immutable history."""
    result: dict[str, str] = {}
    answers = intake.get("answers", [])
    if not isinstance(answers, list):
        return result
    snapshots = intake.get("question_snapshots", {})
    if not isinstance(snapshots, Mapping):
        return result
    for answer in answers:
        if not isinstance(answer, Mapping):
            continue
        question = snapshots.get(answer.get("question_id"))
        if isinstance(question, Mapping) and isinstance(question.get("key"), str) and isinstance(answer.get("value"), str):
            result[question["key"]] = answer["value"]
    revisions = intake.get("fact_revalidations", [])
    if isinstance(revisions, list):
        for revision in revisions:
            if not isinstance(revision, Mapping):
                continue
            question = snapshots.get(revision.get("question_id"))
            replacement = revision.get("replacement")
            if (
                isinstance(question, Mapping)
                and isinstance(question.get("key"), str)
                and isinstance(replacement, Mapping)
                and isinstance(replacement.get("value"), str)
            ):
                result[question["key"]] = replacement["value"]
    return result


def _comparison_id_for_domain(domain: str) -> str:
    return {
        "SOFTWARE": "CMP-SW-001",
        "GENERAL_PROJECT": "CMP-GP-001",
        "FINANCE_REPORTING": "CMP-FR-001",
        "OTHER": "CMP-OT-001",
    }[domain]


def _comparison_question_for_domain(domain: str) -> str:
    return COMPARISON_QUESTION[domain]


def _revalidation_question(
    question_id: str,
    fact_question_id: str,
    fact_label: str,
    comparison: Mapping[str, Any],
    prior_decision_id: str,
) -> dict[str, Any]:
    domain = comparison.get("domain")
    route_label = {
        "GENERAL_PROJECT": "delivery route",
        "FINANCE_REPORTING": "finance-reporting route",
        "OTHER": "route",
    }.get(domain, "route")
    return {
        **_choice_question(
            question_id,
            f"revalidated_{str(comparison.get('id', 'route')).lower().replace('-', '_')}",
            (
                f"Now that {fact_label} is confirmed, which updated {route_label} do you explicitly select?"
            ),
            [
                _option(item["id"], item["label"], item["summary"])
                for item in comparison.get("options", [])
                if isinstance(item, Mapping)
            ],
            (
                f"The earlier {prior_decision_id} selection depended on the UNKNOWN {fact_question_id} premise. "
                "It is preserved as history and is not silently reapproved."
            ),
            f"Revalidate the {route_label} after {fact_question_id}",
            destination_blocking=True,
        ),
        "revalidates": prior_decision_id,
    }


def _comparison_grounding(
    answers: Mapping[str, str],
    domain: str,
) -> tuple[list[dict[str, str]], str, str]:
    """Bind a comparison to every required branch fact without reflecting raw prose."""
    keys = COMPARISON_FACT_KEYS.get(domain, ())
    grounding: list[dict[str, str]] = []
    canonical: list[tuple[str, str]] = []
    conditional: list[str] = []
    for key in keys:
        value = answers.get(key)
        if isinstance(value, str) and value.strip():
            try:
                readiness = _validate_fact_answer(value)["readiness"]
            except IntakeError:
                readiness = "INVALID"
            canonical.append((key, value.strip()))
        else:
            readiness = "MISSING"
            canonical.append((key, ""))
        grounding.append({"key": key, "readiness": readiness})
        if readiness in {"MISSING", "UNKNOWN", "INVALID"}:
            conditional.append(key)
    digest = hashlib.sha256(
        json.dumps(canonical, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    status = "CONDITIONAL" if conditional else "GROUNDED"
    return grounding, digest, status


def _contains_any(answers: Mapping[str, str], keys: Sequence[str], terms: Sequence[str]) -> bool:
    haystack = " ".join(answers.get(key, "") for key in keys if isinstance(answers.get(key), str)).casefold()
    return any(term.casefold() in haystack for term in terms)


def _grounding_clause(domain: str, grounding: Sequence[Mapping[str, str]], status: str) -> str:
    unresolved = [item["key"].replace("_", " ") for item in grounding if item.get("readiness") in {"MISSING", "UNKNOWN", "INVALID"}]
    if unresolved:
        return "No route is recommended until these material facts are confirmed: " + ", ".join(unresolved) + "."
    return f"The comparison uses all {len(grounding)} recorded {domain.lower().replace('_', ' ')} readiness facts."


def _software_comparison(answers: Mapping[str, str]) -> dict[str, Any]:
    priority = answers.get("software_priority", "BALANCED")
    scale = answers.get("software_scale", "GROWING")
    sensitivity = answers.get("software_sensitivity", "INTERNAL-DATA")
    if priority == "MVP-SPEED" and sensitivity not in {"REGULATED-DATA"}:
        recommended = "SW-MANAGED"
    elif priority in {"SCALE", "RELIABILITY", "PRIVACY-CONTROL"} or scale == "HIGH" or sensitivity == "REGULATED-DATA":
        recommended = "SW-MODULAR"
    elif priority == "LOW-COST" and scale == "SMALL":
        recommended = "SW-MANAGED"
    else:
        recommended = "SW-MODULAR"
    options = [
        {
            "id": "SW-MANAGED", "label": "Managed application platform",
            "summary": "Use a managed product platform and hosted services to minimize setup and operations.",
            "mvp_speed": "Fastest path to a working MVP.",
            "scale_beyond_mvp": "Good for ordinary growth; unusual workloads may require migration or redesign.",
            "reliability": "Strong baseline reliability when provider limits and operating guidance are followed.",
            "efficiency": "Very efficient for a small team because infrastructure work is reduced.",
            "cost": "Low initial cost, with usage-based costs that may rise as the product grows.",
            "complexity": "Low initial complexity.",
            "lock_in": "Higher provider dependence and migration effort.",
            "security_privacy": "Provider controls help, but residency, isolation, and sensitive-data requirements must fit the service.",
        },
        {
            "id": "SW-MODULAR", "label": "Conventional modular stack",
            "summary": "Use replaceable application, data, and hosting components with explicit boundaries.",
            "mvp_speed": "Moderate MVP speed with more setup than a managed platform.",
            "scale_beyond_mvp": "Strong path beyond MVP without committing to the heaviest architecture early.",
            "reliability": "Strong when backups, observability, failure handling, and operating ownership are defined.",
            "efficiency": "Efficient for sustained product work, with a moderate operating burden.",
            "cost": "Moderate and comparatively predictable when capacity is managed deliberately.",
            "complexity": "Medium complexity with clear component boundaries.",
            "lock_in": "Low to medium lock-in because major components can be replaced.",
            "security_privacy": "Supports stronger isolation and data controls, but the team owns more configuration and assurance.",
        },
        {
            "id": "SW-CUSTOM", "label": "Custom high-control platform",
            "summary": "Design infrastructure and application components for exceptional scale, control, or regulated constraints.",
            "mvp_speed": "Slowest MVP path because platform work comes first.",
            "scale_beyond_mvp": "Highest theoretical scale and specialization when justified by evidence.",
            "reliability": "Potentially excellent, but only with mature engineering and operations.",
            "efficiency": "Low early efficiency; can become efficient at substantial, proven scale.",
            "cost": "Highest upfront and operating cost.",
            "complexity": "High design, delivery, and operating complexity.",
            "lock_in": "Lower vendor lock-in but higher lock-in to custom expertise and internal designs.",
            "security_privacy": "Maximum control is possible, with full responsibility for implementation, audit, and ongoing assurance.",
        },
    ]
    grounding, facts_digest, recommendation_status = _comparison_grounding(answers, "SOFTWARE")
    context = _grounding_clause("SOFTWARE", grounding, recommendation_status)
    context += " It considers the current environment and reuse, integrations, team capability, operational ownership, delivery constraints, and cost and lock-in tolerance."
    effective_recommended = recommended if recommendation_status == "GROUNDED" else None
    for option in options:
        option["recommendation"] = option["id"] == effective_recommended
        option["rationale"] = (
            f"Recommended because the recorded product form, priority, scale, sensitivity, and readiness facts favor this balance. {context}"
            if option["id"] == effective_recommended
            else (
                "No option is recommended while material software facts remain unresolved."
                if effective_recommended is None
                else "Viable only if its stated tradeoffs better match a later explicit constraint."
            )
        )
    return {
        "id": "CMP-SW-001", "kind": "architecture_strategy", "domain": "SOFTWARE", "title": "Architecture strategy comparison",
        "criteria": ["mvp_speed", "scale_beyond_mvp", "reliability", "efficiency", "cost", "complexity", "lock_in", "security_privacy"],
        "options": options, "recommended_option": effective_recommended, "selected_option": None,
        "recommendation_rationale": (
            f"{context} Any later recommendation will remain advisory and will not authorize implementation."
            if recommendation_status == "CONDITIONAL"
            else f"{context} The recommendation is advisory and is not an implementation authorization."
        ),
        "recommendation_status": recommendation_status,
        "grounding": grounding,
        "facts_digest": facts_digest,
    }


def _general_comparison(answers: Mapping[str, str]) -> dict[str, Any]:
    priority = answers.get("project_priority", "FLEXIBILITY")
    uncertainty = answers.get("project_uncertainty", "PARTLY-DEFINED")
    recommended = "GP-PHASED"
    if uncertainty == "HIGH-UNCERTAINTY":
        recommended = "GP-PILOT"
    elif priority in {"SCHEDULE", "COST-CERTAINTY"} and uncertainty == "WELL-DEFINED":
        recommended = "GP-SEQUENTIAL"
    options = [
        {
            "id": "GP-SEQUENTIAL", "label": "Sequential scope-and-contract route",
            "summary": "Set scope and approvals first, then deliver through a controlled sequence.",
            "schedule": "Fast when scope is genuinely stable; late discoveries are expensive.",
            "cost_certainty": "Strong initial commitment and change control.",
            "safety_quality": "Assurance is planned into defined stages.",
            "coordination": "Clear handoffs, with less flexibility across stages.",
            "flexibility": "Low after commitments are signed or work begins.",
            "regulatory_dependency": "Works well when approval requirements are known early.",
        },
        {
            "id": "GP-PHASED", "label": "Phased delivery with checkpoints",
            "summary": "Divide delivery into bounded phases with acceptance and decision checkpoints.",
            "schedule": "Moderate speed with early useful progress.",
            "cost_certainty": "Costs become firmer phase by phase.",
            "safety_quality": "Repeated inspection and acceptance reduce accumulated defects.",
            "coordination": "Strong alignment at explicit phase boundaries.",
            "flexibility": "Moderate flexibility before each next-phase commitment.",
            "regulatory_dependency": "Approvals can be mapped to the relevant phase.",
        },
        {
            "id": "GP-PILOT", "label": "Pilot then scale",
            "summary": "Test the riskiest site, process, or delivery segment before broader commitment.",
            "schedule": "Slower full rollout but faster learning about feasibility.",
            "cost_certainty": "Low initial commitment; later estimates use pilot evidence.",
            "safety_quality": "Risks and defects are exposed in a contained environment.",
            "coordination": "Requires a deliberate transfer from pilot to rollout owners.",
            "flexibility": "Highest ability to change course.",
            "regulatory_dependency": "Useful when approval or site conditions remain uncertain.",
        },
    ]
    grounding, facts_digest, recommendation_status = _comparison_grounding(answers, "GENERAL_PROJECT")
    context_flags: list[str] = []
    fact_keys = COMPARISON_FACT_KEYS["GENERAL_PROJECT"]
    if _contains_any(answers, fact_keys, ("occupied", "live site", "operational during")):
        context_flags.append("occupied-site continuity")
    if _contains_any(answers, fact_keys, ("hospital", "clinical", "patient")):
        context_flags.append("hospital and patient-safety obligations")
    if _contains_any(answers, ("project_permits",), ("unknown", "unresolved", "not yet", "pending")):
        context_flags.append("permit uncertainty")
    if _contains_any(answers, ("project_safety",), ("safety", "infection", "hazard", "quality")):
        context_flags.append("safety and quality controls")
    context = _grounding_clause("GENERAL_PROJECT", grounding, recommendation_status)
    context += " It considers site, schedule, vendors, permits, resources, safety, dependencies, acceptance, and contingency."
    if context_flags:
        context += " Material conditions: " + ", ".join(context_flags) + "."
    effective_recommended = recommended if recommendation_status == "GROUNDED" else None
    for option in options:
        option["recommendation"] = option["id"] == effective_recommended
        option["rationale"] = (
            f"Recommended for the recorded form, priority, uncertainty, and branch facts. {context}"
            if option["id"] == effective_recommended
            else (
                "No option is recommended while material project facts remain unresolved."
                if effective_recommended is None
                else "Viable if later evidence changes the dominant tradeoff."
            )
        )
    return {
        "id": "CMP-GP-001", "kind": "delivery_route", "domain": "GENERAL_PROJECT", "title": "Delivery route comparison",
        "criteria": ["schedule", "cost_certainty", "safety_quality", "coordination", "flexibility", "regulatory_dependency"],
        "options": options, "recommended_option": effective_recommended, "selected_option": None,
        "recommendation_rationale": (
            f"{context} No route is preferred yet; this planning aid does not authorize procurement, contracting, construction, or operations."
            if recommendation_status == "CONDITIONAL"
            else f"{context} This planning aid does not authorize procurement, contracting, construction, or operations."
        ),
        "recommendation_status": recommendation_status,
        "grounding": grounding,
        "facts_digest": facts_digest,
    }


def _finance_comparison(answers: Mapping[str, str]) -> dict[str, Any]:
    sources = answers.get("finance_sources", "MIXED-SOURCES")
    priority = answers.get("finance_priority", "AUDITABILITY")
    if sources == "SPREADSHEETS" and priority in {"FINANCE-COST", "CLOSE-SPEED"}:
        recommended = "FR-CONTROLLED-SHEETS"
    elif sources == "ACCOUNTING-SYSTEM" and priority != "SCALABILITY":
        recommended = "FR-LEDGER-LED"
    else:
        recommended = "FR-DATA-LAYER"
    options = [
        {
            "id": "FR-CONTROLLED-SHEETS", "label": "Controlled spreadsheet workflow",
            "summary": "Keep reporting in versioned workbooks with explicit ownership, reconciliations, and review controls.",
            "close_speed": "Fast to introduce; manual volume can slow recurring cycles.",
            "auditability": "Adequate only with strict version, review, evidence, and access controls.",
            "controls": "Relies heavily on procedural controls and independent review.",
            "scalability": "Limited across many entities, sources, or reporting dimensions.",
            "reconciliation_effort": "High manual reconciliation effort.",
            "cost": "Low tool cost with potentially high staff effort.",
            "complexity": "Low technical complexity, medium process complexity.",
            "lock_in": "Low vendor lock-in but high dependence on workbook knowledge.",
            "security_privacy": "Requires disciplined file access, retention, and distribution controls.",
        },
        {
            "id": "FR-LEDGER-LED", "label": "Accounting-system-led reporting",
            "summary": "Make the ledger or ERP the controlled source and keep adjustments and reporting close to it.",
            "close_speed": "Good when source transactions and close ownership are disciplined.",
            "auditability": "Strong native transaction lineage and role controls.",
            "controls": "Strong approval and period controls when configured correctly.",
            "scalability": "Good within the system's entity and reporting model.",
            "reconciliation_effort": "Moderate when important sources remain outside the ledger.",
            "cost": "Moderate licensing, configuration, and administration cost.",
            "complexity": "Medium process and configuration complexity.",
            "lock_in": "Medium to high platform dependence.",
            "security_privacy": "Centralized access control helps, subject to configuration and provider terms.",
        },
        {
            "id": "FR-DATA-LAYER", "label": "Governed finance data and reporting layer",
            "summary": "Reconcile multiple sources into a governed model before reporting and analysis.",
            "close_speed": "Slower to establish, then strong recurring automation potential.",
            "auditability": "Strong when lineage, transformations, approvals, and snapshots are retained.",
            "controls": "Supports automated completeness, validity, and reconciliation controls.",
            "scalability": "Strong across entities, sources, periods, and reporting dimensions.",
            "reconciliation_effort": "Lower recurring effort after substantial setup and exception design.",
            "cost": "Higher setup and data-governance cost.",
            "complexity": "High data-model, ownership, and operating complexity.",
            "lock_in": "Varies; portable models reduce but do not remove platform dependence.",
            "security_privacy": "Requires explicit sensitive-data classification, access, retention, and lineage controls.",
        },
    ]
    grounding, facts_digest, recommendation_status = _comparison_grounding(answers, "FINANCE_REPORTING")
    context_flags: list[str] = []
    fact_keys = COMPARISON_FACT_KEYS["FINANCE_REPORTING"]
    if answers.get("reporting_need") == "REGULATORY" or _contains_any(answers, fact_keys, ("regulatory", "statutory")):
        context_flags.append("regulatory or statutory obligation")
    if _contains_any(answers, ("finance_basis",), ("ifrs", "gaap", "basis", "presentation")):
        context_flags.append("reporting-basis conclusion")
    if _contains_any(answers, ("finance_signoff",), ("qualified", "signoff", "sign-off", "reviewer")):
        context_flags.append("qualified sign-off")
    if _contains_any(answers, ("finance_deadline", "finance_period_cutoff"), ("deadline", "cannot move", "strict", "cutoff")):
        context_flags.append("fixed deadline and cutoff")
    context = _grounding_clause("FINANCE_REPORTING", grounding, recommendation_status)
    context += " It considers jurisdiction, reporting basis, cutoff, currency and materiality, source lineage, reconciliation, controls, sign-off, format, and deadline."
    if context_flags:
        context += " Material conditions: " + ", ".join(context_flags) + "."
    effective_recommended = recommended if recommendation_status == "GROUNDED" else None
    for option in options:
        option["recommendation"] = option["id"] == effective_recommended
        option["rationale"] = (
            f"Recommended for the recorded need, source profile, control priority, and branch facts. {context}"
            if option["id"] == effective_recommended
            else (
                "No option is recommended while material finance-reporting facts remain unresolved."
                if effective_recommended is None
                else "Viable if reporting volume, controls, or ownership change."
            )
        )
    return {
        "id": "CMP-FR-001", "kind": "reporting_route", "domain": "FINANCE_REPORTING", "title": "Finance-reporting route comparison",
        "criteria": ["close_speed", "auditability", "controls", "scalability", "reconciliation_effort", "cost", "complexity", "lock_in", "security_privacy"],
        "options": options, "recommended_option": effective_recommended, "selected_option": None,
        "recommendation_rationale": (
            f"{context} Any later recommendation will not approve accounting treatment, filings, spending, or system changes."
            if recommendation_status == "CONDITIONAL"
            else f"{context} The recommendation does not approve accounting treatment, filings, spending, or system changes."
        ),
        "recommendation_status": recommendation_status,
        "grounding": grounding,
        "facts_digest": facts_digest,
    }


def _other_comparison(answers: Mapping[str, str]) -> dict[str, Any]:
    priority = answers.get("other_priority", "OTHER-REVERSIBILITY")
    recommended = {
        "OTHER-SPEED": "OT-DOCUMENTED",
        "OTHER-COST": "OT-DOCUMENTED",
        "OTHER-RELIABILITY": "OT-SPECIALIST",
        "OTHER-GOVERNANCE": "OT-SPECIALIST",
    }.get(priority, "OT-PILOT")
    options = [
        {"id": "OT-DOCUMENTED", "label": "Documented direct route", "summary": "Use a simple owned workflow with explicit acceptance.", "speed": "Fast", "reliability": "Moderate", "cost": "Low", "complexity": "Low", "reversibility": "Moderate", "governance": "Basic explicit ownership"},
        {"id": "OT-PILOT", "label": "Phased pilot route", "summary": "Test a bounded version, then expand using evidence.", "speed": "Moderate", "reliability": "Improves through learning", "cost": "Low initial commitment", "complexity": "Medium", "reversibility": "High", "governance": "Checkpoint-based"},
        {"id": "OT-SPECIALIST", "label": "Specialist-led route", "summary": "Use accountable domain expertise for the consequential choices.", "speed": "Moderate", "reliability": "Potentially high", "cost": "Higher", "complexity": "Medium", "reversibility": "Depends on commitments", "governance": "Strong expert accountability"},
    ]
    for option in options:
        option["recommendation"] = option["id"] == recommended
        option["rationale"] = "Recommended for the recorded dominant quality." if option["id"] == recommended else "Alternative if the dominant quality changes."
    return {
        "id": "CMP-OT-001", "kind": "route", "domain": "OTHER", "title": "Route comparison",
        "criteria": ["speed", "reliability", "cost", "complexity", "reversibility", "governance"],
        "options": options, "recommended_option": recommended, "selected_option": None,
        "recommendation_rationale": "This comparison preserves explicit human selection and grants no implementation authority.",
    }


def _comparison_for(intake: Mapping[str, Any], domain: str | None = None) -> dict[str, Any]:
    if domain is None:
        domain = intake.get("domain", {}).get("selected") if isinstance(intake.get("domain"), Mapping) else None
    answers = _answers_by_key(intake)
    if domain == "SOFTWARE":
        return _software_comparison(answers)
    if domain == "GENERAL_PROJECT":
        return _general_comparison(answers)
    if domain == "FINANCE_REPORTING":
        return _finance_comparison(answers)
    return _other_comparison(answers)


def _question_snapshot(question_id: str, intake: Mapping[str, Any]) -> dict[str, Any]:
    if question_id in COMPARISON_QUESTION.values() or question_id == "Q-SW-012":
        if question_id == "Q-SW-012":
            comparison = next(
                (item for item in intake.get("comparisons", []) if isinstance(item, Mapping) and item.get("id") == "CMP-TECH-001"),
                None,
            )
            if comparison is None:
                raise IntakeError("Named technology options must be proposed before human selection.", "INTAKE_NOT_READY", 409)
        else:
            comparison_domain = next(domain for domain, final_id in COMPARISON_QUESTION.items() if final_id == question_id)
            comparison = _comparison_for(intake, comparison_domain)
        options = [
            _option(option["id"], option["label"], option["summary"])
            for option in comparison["options"]
        ]
        domain = comparison["domain"]
        label = {
            "SOFTWARE": "technology route",
            "GENERAL_PROJECT": "delivery route",
            "FINANCE_REPORTING": "finance-reporting route",
            "OTHER": "route",
        }[domain]
        return _choice_question(
            question_id,
            "selected_route",
            f"Which {label} do you explicitly select?",
            options,
            "The recommendation remains advisory until the human authority selects an option.",
            f"Select the {label}",
        )
    if question_id not in QUESTION_DEFINITIONS:
        raise IntakeError("Question flow contains an unknown question ID.")
    return deepcopy(QUESTION_DEFINITIONS[question_id])


def _load_state_module() -> Any:
    for name in ("_wayfinder_state_v3", "_wayfinder_state_for_intake"):
        existing = sys.modules.get(name)
        if existing is not None and hasattr(existing, "build_state") and hasattr(existing, "resolve_effort"):
            return existing
    path = Path(__file__).resolve(strict=True).with_name("wayfinder_state.py")
    spec = importlib.util.spec_from_file_location("_wayfinder_state_for_intake", path)
    if spec is None or spec.loader is None:
        raise IntakeError("Wayfinder state engine is unavailable.", "INTAKE_NOT_READY", 409)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_wayfinder_state_for_intake"] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop("_wayfinder_state_for_intake", None)
        raise
    return module


def _within(root: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def _read_regular(path: Path, label: str, maximum: int = MAX_INTAKE_BYTES) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise IntakeError(f"{label} is unavailable or unsafe.", "INTAKE_RECOVERY_REQUIRED", 409) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise IntakeError(f"{label} is unavailable or unsafe.", "INTAKE_RECOVERY_REQUIRED", 409)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise IntakeError(f"{label} exceeds the intake safety limit.", "INTAKE_RECOVERY_REQUIRED", 409)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _iso_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 64 or _contains_unsafe(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_question_snapshot(question_id: str, value: Any) -> dict[str, Any]:
    if not QUESTION_ID.fullmatch(question_id) or not isinstance(value, Mapping):
        raise IntakeError("INTAKE.json contains an invalid question snapshot.", "INTAKE_RECOVERY_REQUIRED", 409)
    if value.get("id") != question_id or value.get("answer_type") not in {"choice", "text", "fact"}:
        raise IntakeError("INTAKE.json contains an invalid question snapshot.", "INTAKE_RECOVERY_REQUIRED", 409)
    for key in ("key", "prompt", "why"):
        _bounded_text(value.get(key), "Question metadata", 2_000, allow_ambiguous=True)
    if value.get("required") is not True or not isinstance(value.get("human_choice_required"), bool):
        raise IntakeError("INTAKE.json contains an invalid question contract.", "INTAKE_RECOVERY_REQUIRED", 409)
    options = value.get("options")
    if not isinstance(options, list) or len(options) > 32:
        raise IntakeError("INTAKE.json contains invalid question options.", "INTAKE_RECOVERY_REQUIRED", 409)
    seen: set[str] = set()
    normalized_options: list[dict[str, str]] = []
    for item in options:
        if not isinstance(item, Mapping):
            raise IntakeError("INTAKE.json contains invalid question options.", "INTAKE_RECOVERY_REQUIRED", 409)
        option_id = item.get("id")
        if not isinstance(option_id, str) or not OPTION_ID.fullmatch(option_id) or option_id in seen:
            raise IntakeError("INTAKE.json contains invalid question options.", "INTAKE_RECOVERY_REQUIRED", 409)
        seen.add(option_id)
        normalized_options.append(
            {
                "id": option_id,
                "label": _bounded_text(item.get("label"), "Option label", 200, allow_ambiguous=True),
                "description": _bounded_text(item.get("description"), "Option description", 1_000, allow_ambiguous=True),
            }
        )
    if value.get("answer_type") == "choice" and not normalized_options:
        raise IntakeError("A choice question must contain allowed options.", "INTAKE_RECOVERY_REQUIRED", 409)
    if value.get("answer_type") in {"text", "fact"} and normalized_options:
        raise IntakeError("A non-choice question cannot contain choice options.", "INTAKE_RECOVERY_REQUIRED", 409)
    result = {
        "id": question_id,
        "key": value["key"],
        "prompt": value["prompt"],
        "answer_type": value["answer_type"],
        "required": True,
        "human_choice_required": value["human_choice_required"],
        "options": normalized_options,
        "why": value["why"],
        "decision_title": value.get("decision_title") if isinstance(value.get("decision_title"), str) else None,
        "destination_blocking": value.get("destination_blocking") is True,
    }
    if "revalidates" in value:
        revalidates = value.get("revalidates")
        if not isinstance(revalidates, str) or not DECISION_ID.fullmatch(revalidates):
            raise IntakeError("A revalidation question must identify one canonical prior Decision.", "INTAKE_RECOVERY_REQUIRED", 409)
        result["revalidates"] = revalidates
    if result["answer_type"] in {"text", "fact"}:
        maximum = value.get("max_length")
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1 or maximum > MAX_TEXT_ANSWER:
            raise IntakeError("A text question has an invalid length limit.", "INTAKE_RECOVERY_REQUIRED", 409)
        result["max_length"] = maximum
    return result


def _validate_intake(value: Mapping[str, Any], effort_id: str, manifest: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") != INTAKE_SCHEMA_VERSION or value.get("intake_id") != INTAKE_ID:
        raise IntakeError("INTAKE.json schema is unsupported.", "INTAKE_RECOVERY_REQUIRED", 409)
    if value.get("flow_version") != FLOW_VERSION or value.get("effort_id") != effort_id:
        raise IntakeError("INTAKE.json does not match the selected effort.", "INTAKE_RECOVERY_REQUIRED", 409)
    revision = value.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise IntakeError("INTAKE.json revision is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    if value.get("status") not in {"IN_PROGRESS", "AWAITING_HUMAN_CHOICE", "AWAITING_TECH_OPTIONS", "COMPLETE"}:
        raise IntakeError("INTAKE.json status is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    intent = _bounded_text(value.get("intent"), "Intent", 2_000)
    domain = value.get("domain")
    if not isinstance(domain, Mapping):
        raise IntakeError("INTAKE.json domain classification is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    if domain.get("proposed") not in DOMAIN_IDS or domain.get("confidence") not in {"LOW", "MEDIUM", "HIGH"}:
        raise IntakeError("INTAKE.json domain classification is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    selected = domain.get("selected")
    domain_selected_option = domain.get("selected_option", selected)
    if selected is not None and selected not in DOMAIN_IDS:
        raise IntakeError("INTAKE.json selected domain is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    if (
        (selected is None and domain_selected_option is not None)
        or (
            selected is not None
            and (
                not isinstance(domain_selected_option, str)
                or not OPTION_ID.fullmatch(domain_selected_option)
            )
        )
    ):
        raise IntakeError("INTAKE.json selected domain option is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    if domain.get("ambiguous") not in {True, False} or domain.get("hybrid_candidate") not in {True, False}:
        raise IntakeError("INTAKE.json ambiguity marker is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    signals = domain.get("signals")
    alternatives = domain.get("alternatives")
    suggested_secondary = domain.get("suggested_secondary_domains")
    if (
        not isinstance(signals, list)
        or not isinstance(alternatives, list)
        or not isinstance(suggested_secondary, list)
        or len(signals) > 64
        or len(alternatives) > 3
        or len(suggested_secondary) > 3
    ):
        raise IntakeError("INTAKE.json domain signals are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    for signal in signals:
        if not isinstance(signal, str) or not OPTION_ID.fullmatch(signal):
            raise IntakeError("INTAKE.json domain signals are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    for alternative in alternatives:
        if alternative not in DOMAIN_IDS:
            raise IntakeError("INTAKE.json domain alternatives are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    suggested_primary = selected or domain.get("proposed")
    if (
        len(suggested_secondary) != len(set(suggested_secondary))
        or any(item not in DOMAIN_IDS or item == suggested_primary for item in suggested_secondary)
        or domain.get("hybrid_candidate") is not bool(suggested_secondary)
    ):
        raise IntakeError("INTAKE.json secondary-domain suggestions are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    if selected is None and domain.get("selection_source") is not None:
        raise IntakeError("INTAKE.json domain selection receipt is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    if selected is not None and domain.get("selection_source") != "HUMAN_EXPLICIT":
        raise IntakeError("INTAKE.json domain selection receipt is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    if domain.get("primary_domain") != selected:
        raise IntakeError("INTAKE.json primary domain conflicts with its explicit selection.", "INTAKE_RECOVERY_REQUIRED", 409)
    workstreams = domain.get("secondary_workstreams")
    if not isinstance(workstreams, list) or len(workstreams) > 3:
        raise IntakeError("INTAKE.json secondary workstreams are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    normalized_workstreams: list[dict[str, Any]] = []
    seen_workstreams: set[str] = set()
    for workstream in workstreams:
        if not isinstance(workstream, Mapping):
            raise IntakeError("INTAKE.json secondary workstreams are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        workstream_id = workstream.get("id")
        workstream_domain = workstream.get("domain")
        questions = workstream.get("required_questions")
        decision_ids = workstream.get("decision_ids")
        if (
            not isinstance(workstream_id, str)
            or not re.fullmatch(r"WS-\d{3,}", workstream_id)
            or workstream_id in seen_workstreams
            or workstream_domain not in DOMAIN_IDS
            or workstream_domain == selected
            or not isinstance(questions, list)
            or len(questions) > 32
            or not all(isinstance(item, str) and QUESTION_ID.fullmatch(item) for item in questions)
            or not isinstance(decision_ids, list)
            or len(decision_ids) > 32
            or not all(isinstance(item, str) and DECISION_ID.fullmatch(item) for item in decision_ids)
        ):
            raise IntakeError("INTAKE.json secondary workstreams are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        seen_workstreams.add(workstream_id)
        normalized_workstreams.append(
            {
                "id": workstream_id,
                "domain": workstream_domain,
                "outcome": _bounded_text(workstream.get("outcome"), "Secondary outcome", 1_000),
                "authority": _human_actor(workstream.get("authority")),
                "required_questions": list(questions),
                "decision_ids": list(decision_ids),
            }
        )

    order = value.get("question_order")
    snapshots = value.get("question_snapshots")
    if not isinstance(order, list) or not order or len(order) > MAX_QUESTIONS or len(order) != len(set(order)) or not isinstance(snapshots, Mapping):
        raise IntakeError("INTAKE.json question flow is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    normalized_snapshots: dict[str, dict[str, Any]] = {}
    for question_id in order:
        if not isinstance(question_id, str) or not QUESTION_ID.fullmatch(question_id):
            raise IntakeError("INTAKE.json question flow is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    if not set(snapshots).issubset(set(order)):
        raise IntakeError("INTAKE.json contains unindexed question snapshots.", "INTAKE_RECOVERY_REQUIRED", 409)
    for question_id, snapshot in snapshots.items():
        normalized_snapshots[question_id] = _validate_question_snapshot(question_id, snapshot)
    current = value.get("current_question_id")
    if current is not None and current not in order:
        raise IntakeError("INTAKE.json current question is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    if value.get("status") in {"COMPLETE", "AWAITING_TECH_OPTIONS"} and current is not None:
        raise IntakeError("A completed intake cannot retain a current question.", "INTAKE_RECOVERY_REQUIRED", 409)
    if value.get("status") not in {"COMPLETE", "AWAITING_TECH_OPTIONS"} and current is None:
        raise IntakeError("An incomplete intake must name its current question.", "INTAKE_RECOVERY_REQUIRED", 409)

    answers = value.get("answers")
    if not isinstance(answers, list) or len(answers) > MAX_QUESTIONS:
        raise IntakeError("INTAKE.json answers are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    normalized_answers: list[dict[str, str]] = []
    answered_ids: set[str] = set()
    for answer in answers:
        if not isinstance(answer, Mapping) or answer.get("question_id") not in normalized_snapshots:
            raise IntakeError("INTAKE.json answers are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        question_id = answer["question_id"]
        if question_id in answered_ids:
            raise IntakeError("INTAKE.json repeats an answer.", "INTAKE_RECOVERY_REQUIRED", 409)
        answered_ids.add(question_id)
        snapshot = normalized_snapshots[question_id]
        raw_value = answer.get("value")
        if snapshot["answer_type"] == "choice":
            allowed = {item["id"] for item in snapshot["options"]}
            if raw_value not in allowed:
                raise IntakeError("INTAKE.json contains a choice outside the allowed options.", "INTAKE_RECOVERY_REQUIRED", 409)
            normalized_value = raw_value
        elif snapshot["answer_type"] == "text":
            normalized_value = _bounded_text(raw_value, "Answer", snapshot["max_length"])
        else:
            fact = _validate_fact_answer(raw_value, snapshot["max_length"])
            normalized_value = _bounded_text(raw_value, "Readiness fact", snapshot["max_length"])
        actor = _bounded_text(answer.get("actor"), "Answer actor", 120)
        source = answer.get("source")
        timestamp = _iso_timestamp(answer.get("answered_at"))
        if source not in SOURCE_IDS or timestamp is None:
            raise IntakeError("INTAKE.json answer provenance is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        normalized_answer = {"question_id": question_id, "value": normalized_value, "actor": actor, "source": source, "answered_at": timestamp}
        if snapshot["answer_type"] == "fact":
            normalized_answer.update(fact)
        normalized_answers.append(normalized_answer)
    expected_answered = order[: len(normalized_answers)]
    if [item["question_id"] for item in normalized_answers] != expected_answered:
        raise IntakeError("INTAKE.json answers must form an exact questionnaire prefix.", "INTAKE_RECOVERY_REQUIRED", 409)
    expected_current = order[len(normalized_answers)] if len(normalized_answers) < len(order) else None
    if current != expected_current:
        raise IntakeError("INTAKE.json current question does not match questionnaire progress.", "INTAKE_RECOVERY_REQUIRED", 409)
    if current is not None and current not in normalized_snapshots:
        raise IntakeError("INTAKE.json current question has no immutable snapshot.", "INTAKE_RECOVERY_REQUIRED", 409)
    normalized_answers_by_key = {
        normalized_snapshots[item["question_id"]]["key"]: item
        for item in normalized_answers
    }
    selected_domain_answer = normalized_answers_by_key.get("domain", {}).get("value")
    if selected is not None and selected_domain_answer != domain_selected_option:
        raise IntakeError("INTAKE.json domain selection conflicts with its answer receipt.", "INTAKE_RECOVERY_REQUIRED", 409)
    if normalized_answers_by_key.get("reporting_need", {}).get("value") == "REGULATORY":
        for answer in normalized_answers:
            if answer["question_id"] in REGULATORY_REQUIRED_FACTS and answer.get("readiness") == "NOT_APPLICABLE":
                raise IntakeError(
                    "INTAKE.json treats a mandatory regulatory-reporting fact as not applicable.",
                    "INTAKE_RECOVERY_REQUIRED",
                    409,
                )

    bindings = value.get("decision_bindings")
    if not isinstance(bindings, list) or len(bindings) > MAX_QUESTIONS:
        raise IntakeError("INTAKE.json Decision bindings are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    manifest_statuses = {
        entry.get("id"): entry.get("status")
        for entry in manifest.get("decisions", [])
        if isinstance(entry, Mapping) and isinstance(entry.get("id"), str)
    } if isinstance(manifest.get("decisions"), list) else {}
    normalized_bindings: list[dict[str, Any]] = []
    binding_questions: set[str] = set()
    binding_decisions: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, Mapping):
            raise IntakeError("INTAKE.json Decision bindings are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        question_id = binding.get("question_id")
        decision_id = binding.get("decision_id")
        status = binding.get("status")
        if (
            question_id not in normalized_snapshots
            or normalized_snapshots[question_id]["answer_type"] != "choice"
            or not isinstance(decision_id, str)
            or not DECISION_ID.fullmatch(decision_id)
            or decision_id not in manifest_statuses
            or status not in {"OPEN", "RESOLVED"}
            or manifest_statuses.get(decision_id) != status
            or question_id in binding_questions
            or decision_id in binding_decisions
        ):
            raise IntakeError("INTAKE.json Decision bindings are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        binding_questions.add(question_id)
        binding_decisions.add(decision_id)
        selected_option = binding.get("selected_option")
        evidence_id = binding.get("evidence_id")
        if status == "OPEN" and (selected_option is not None or evidence_id is not None):
            raise IntakeError("An open intake Decision cannot contain a selection receipt.", "INTAKE_RECOVERY_REQUIRED", 409)
        if status == "RESOLVED":
            allowed = {item["id"] for item in normalized_snapshots[question_id]["options"]}
            if selected_option not in allowed or not isinstance(evidence_id, str) or not EVIDENCE_ID.fullmatch(evidence_id):
                raise IntakeError("A resolved intake Decision needs a valid selection receipt.", "INTAKE_RECOVERY_REQUIRED", 409)
        normalized_bindings.append(
            {
                "question_id": question_id,
                "decision_id": decision_id,
                "status": status,
                "selected_option": selected_option,
                "evidence_id": evidence_id,
            }
        )
    current_snapshot = normalized_snapshots.get(current) if isinstance(current, str) else None
    if current_snapshot and current_snapshot["answer_type"] == "choice":
        current_bindings = [item for item in normalized_bindings if item["question_id"] == current and item["status"] == "OPEN"]
        if len(current_bindings) != 1:
            raise IntakeError("The current choice must have exactly one open Decision binding.", "INTAKE_RECOVERY_REQUIRED", 409)

    fact_revalidations = value.get("fact_revalidations", [])
    if not isinstance(fact_revalidations, list) or len(fact_revalidations) > MAX_FACT_REVALIDATIONS:
        raise IntakeError("INTAKE.json fact revalidation history is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    answer_values = {item["question_id"]: item["value"] for item in normalized_answers}
    original_comparison_context: dict[str, Any] = {
        "answers": normalized_answers,
        "question_snapshots": normalized_snapshots,
        "fact_revalidations": [],
        "domain": {"selected": selected},
    }
    selected_by_comparison = {
        _comparison_id_for_domain(domain_id): answer_values.get(COMPARISON_QUESTION[domain_id])
        for domain_id in DOMAIN_IDS
    }
    prior_binding_by_comparison = {
        _comparison_id_for_domain(domain_id): next(
            (
                item for item in normalized_bindings
                if item["question_id"] == COMPARISON_QUESTION[domain_id] and item["status"] == "RESOLVED"
            ),
            None,
        )
        for domain_id in DOMAIN_IDS
    }
    normalized_fact_revalidations: list[dict[str, Any]] = []
    revalidated_questions: set[str] = set()
    for index, revalidation in enumerate(fact_revalidations, 1):
        if not isinstance(revalidation, Mapping) or set(revalidation) != {
            "id", "question_id", "supersedes_receipt_id", "receipt_id", "previous_answer", "replacement",
            "comparison_id", "prior_comparison", "prior_decision_id", "revalidation_question_id",
            "revalidation_decision_id",
        }:
            raise IntakeError("INTAKE.json fact revalidation history is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        revalidation_id = revalidation.get("id")
        question_id = revalidation.get("question_id")
        revalidation_question_id = revalidation.get("revalidation_question_id")
        if (
            revalidation_id != f"FRV-{index:04d}"
            or not isinstance(question_id, str)
            or question_id in revalidated_questions
            or question_id not in normalized_snapshots
            or normalized_snapshots[question_id]["answer_type"] != "fact"
            or revalidation_question_id != f"Q-RV-{index:03d}"
            or revalidation_question_id not in normalized_snapshots
            or revalidation_question_id not in order
        ):
            raise IntakeError("INTAKE.json fact revalidation history is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        revalidated_questions.add(question_id)
        previous_answer = next((item for item in normalized_answers if item["question_id"] == question_id), None)
        if (
            previous_answer is None
            or previous_answer.get("readiness") != "UNKNOWN"
            or not isinstance(revalidation.get("previous_answer"), Mapping)
            or dict(revalidation["previous_answer"]) != previous_answer
        ):
            raise IntakeError("A fact revalidation must preserve one original UNKNOWN answer exactly.", "INTAKE_RECOVERY_REQUIRED", 409)
        fact_key = normalized_snapshots[question_id]["key"]
        domain_id = FACT_COMPARISON_DOMAIN.get(fact_key)
        comparison_id = revalidation.get("comparison_id")
        if domain_id not in {"GENERAL_PROJECT", "FINANCE_REPORTING"} or comparison_id != _comparison_id_for_domain(domain_id):
            raise IntakeError("This fact revalidation route is unsupported or inconsistent.", "INTAKE_RECOVERY_REQUIRED", 409)
        prior_binding = prior_binding_by_comparison.get(comparison_id)
        if prior_binding is None or revalidation.get("prior_decision_id") != prior_binding["decision_id"]:
            raise IntakeError("A fact revalidation must identify the resolved route Decision it supersedes.", "INTAKE_RECOVERY_REQUIRED", 409)
        before_context = {
            **original_comparison_context,
            "fact_revalidations": deepcopy(normalized_fact_revalidations),
        }
        prior_comparison = _comparison_for(before_context, domain_id)
        prior_comparison["selected_option"] = selected_by_comparison.get(comparison_id)
        if not isinstance(revalidation.get("prior_comparison"), Mapping) or dict(revalidation["prior_comparison"]) != prior_comparison:
            raise IntakeError("A fact revalidation must preserve the prior route comparison exactly.", "INTAKE_RECOVERY_REQUIRED", 409)
        replacement = revalidation.get("replacement")
        if not isinstance(replacement, Mapping) or set(replacement) != {
            "value", "readiness", "detail", "support", "actor", "source", "recorded_at",
        }:
            raise IntakeError("INTAKE.json fact revalidation replacement is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        replacement_value = _bounded_text(
            replacement.get("value"), "Revalidated fact", normalized_snapshots[question_id]["max_length"]
        )
        replacement_fact = _validate_fact_answer(replacement_value, normalized_snapshots[question_id]["max_length"])
        replacement_actor = _bounded_text(replacement.get("actor"), "Fact revalidation actor", 120)
        replacement_source = replacement.get("source")
        replacement_timestamp = _iso_timestamp(replacement.get("recorded_at"))
        if (
            replacement_fact["readiness"] not in {"HUMAN_ANSWERED", "ESTABLISHED"}
            or replacement.get("readiness") != replacement_fact["readiness"]
            or replacement.get("detail") != replacement_fact["detail"]
            or replacement.get("support") != replacement_fact["support"]
            or replacement_source not in SOURCE_IDS
            or replacement_timestamp is None
        ):
            raise IntakeError("INTAKE.json fact revalidation replacement is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        supersedes_receipt_id = revalidation.get("supersedes_receipt_id")
        receipt_id = revalidation.get("receipt_id")
        if (
            not isinstance(supersedes_receipt_id, str)
            or not re.fullmatch(r"IR-\d{4}", supersedes_receipt_id)
            or not isinstance(receipt_id, str)
            or not re.fullmatch(r"IR-\d{4}", receipt_id)
        ):
            raise IntakeError("INTAKE.json fact revalidation receipt references are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        revalidation_decision_id = revalidation.get("revalidation_decision_id")
        new_binding = next(
            (item for item in normalized_bindings if item["question_id"] == revalidation_question_id),
            None,
        )
        if (
            new_binding is None
            or not isinstance(revalidation_decision_id, str)
            or new_binding["decision_id"] != revalidation_decision_id
            or normalized_snapshots[revalidation_question_id].get("revalidates") != prior_binding["decision_id"]
        ):
            raise IntakeError("A fact revalidation lacks its exact dependent Decision binding.", "INTAKE_RECOVERY_REQUIRED", 409)
        normalized_entry = {
            "id": revalidation_id,
            "question_id": question_id,
            "supersedes_receipt_id": supersedes_receipt_id,
            "receipt_id": receipt_id,
            "previous_answer": deepcopy(previous_answer),
            "replacement": {
                "value": replacement_value,
                **replacement_fact,
                "actor": replacement_actor,
                "source": replacement_source,
                "recorded_at": replacement_timestamp,
            },
            "comparison_id": comparison_id,
            "prior_comparison": deepcopy(prior_comparison),
            "prior_decision_id": prior_binding["decision_id"],
            "revalidation_question_id": revalidation_question_id,
            "revalidation_decision_id": revalidation_decision_id,
        }
        after_context = {
            **original_comparison_context,
            "fact_revalidations": [*normalized_fact_revalidations, normalized_entry],
        }
        recomputed = _comparison_for(after_context, domain_id)
        recomputed["selected_option"] = None
        expected_snapshot = _revalidation_question(
            revalidation_question_id,
            question_id,
            fact_key.replace("_", " "),
            recomputed,
            prior_binding["decision_id"],
        )
        if normalized_snapshots[revalidation_question_id] != expected_snapshot:
            raise IntakeError("A fact revalidation question conflicts with the recomputed comparison.", "INTAKE_RECOVERY_REQUIRED", 409)
        selected_by_comparison[comparison_id] = answer_values.get(revalidation_question_id)
        if new_binding["status"] == "RESOLVED":
            prior_binding_by_comparison[comparison_id] = new_binding
        normalized_fact_revalidations.append(normalized_entry)
    indexed_revalidation_questions = {
        item["revalidation_question_id"] for item in normalized_fact_revalidations
    }
    if {item for item in order if re.fullmatch(r"Q-RV-\d{3}", item)} != indexed_revalidation_questions:
        raise IntakeError("INTAKE.json contains an unbound fact-revalidation question.", "INTAKE_RECOVERY_REQUIRED", 409)

    comparisons = value.get("comparisons")
    comparison_history = value.get("comparison_history", [])
    receipts = value.get("receipts")
    if (
        not isinstance(comparisons, list)
        or len(comparisons) > 8
        or not isinstance(comparison_history, list)
        or len(comparison_history) > 16
        or not isinstance(receipts, list)
        or len(receipts) > MAX_RECEIPTS
    ):
        raise IntakeError("INTAKE.json comparison or receipt collections are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    comparison_domains = {
        "CMP-SW-001": "SOFTWARE",
        "CMP-GP-001": "GENERAL_PROJECT",
        "CMP-FR-001": "FINANCE_REPORTING",
        "CMP-OT-001": "OTHER",
    }
    normalized_comparisons: list[dict[str, Any]] = []
    seen_comparisons: set[str] = set()
    comparison_context = {
        "answers": normalized_answers,
        "question_snapshots": normalized_snapshots,
        "fact_revalidations": normalized_fact_revalidations,
        "domain": {"selected": selected},
    }
    canonical_answer_map = _answers_by_key(comparison_context)
    for comparison in comparisons:
        if not isinstance(comparison, Mapping) or not isinstance(comparison.get("id"), str):
            raise IntakeError("INTAKE.json comparison is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        comparison_id = comparison["id"]
        if comparison_id in seen_comparisons:
            raise IntakeError("INTAKE.json repeats a comparison.", "INTAKE_RECOVERY_REQUIRED", 409)
        seen_comparisons.add(comparison_id)
        if comparison_id == "CMP-TECH-001":
            proposer = _bounded_text(comparison.get("proposed_by"), "Technology proposal actor", 120)
            proposal_source = comparison.get("proposal_source")
            proposed_at = _iso_timestamp(comparison.get("proposed_at"))
            if proposal_source not in SOURCE_IDS or proposed_at is None:
                raise IntakeError("INTAKE.json technology proposal provenance is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
            options = comparison.get("options")
            if not isinstance(options, list):
                raise IntakeError("INTAKE.json technology options are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
            comparison_revision = comparison.get("revision")
            expected = _technology_comparison(
                options,
                manifest,
                proposer,
                proposal_source,
                proposed_at,
                comparison_revision,
                canonical_answer_map,
            )
            selected_option = comparison.get("selected_option")
            option_ids = {item["id"] for item in expected["options"]}
            if selected_option not in option_ids | {None}:
                raise IntakeError("INTAKE.json technology selection is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
            expected["selected_option"] = selected_option
            expected_answer = answer_values.get("Q-SW-012")
        elif comparison_id in comparison_domains:
            comparison_domain = comparison_domains[comparison_id]
            expected = _comparison_for(comparison_context, comparison_domain)
            selected_option = comparison.get("selected_option")
            option_ids = {item["id"] for item in expected["options"]}
            if selected_option not in option_ids | {None}:
                raise IntakeError("INTAKE.json comparison selection is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
            expected["selected_option"] = selected_option
            expected_answer = selected_by_comparison.get(comparison_id)
        else:
            raise IntakeError("INTAKE.json comparison ID is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        if selected_option != expected_answer or dict(comparison) != expected:
            raise IntakeError("INTAKE.json comparison conflicts with canonical intake facts.", "INTAKE_RECOVERY_REQUIRED", 409)
        normalized_comparisons.append(deepcopy(expected))

    normalized_comparison_history: list[dict[str, Any]] = []
    active_technology = next(
        (item for item in normalized_comparisons if item.get("id") == "CMP-TECH-001"),
        None,
    )
    for index, history_entry in enumerate(comparison_history, 1):
        if not isinstance(history_entry, Mapping) or set(history_entry) != {
            "comparison_id", "revision", "superseded_at", "superseded_by", "source", "snapshot",
        }:
            raise IntakeError("INTAKE.json comparison history is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        history_revision = history_entry.get("revision")
        superseded_at = _iso_timestamp(history_entry.get("superseded_at"))
        superseded_by = history_entry.get("superseded_by")
        history_source = history_entry.get("source")
        snapshot = history_entry.get("snapshot")
        if (
            history_entry.get("comparison_id") != "CMP-TECH-001"
            or history_revision != index
            or superseded_at is None
            or not isinstance(superseded_by, str)
            or superseded_by != f"CMP-TECH-001@{index + 1}"
            or history_source not in SOURCE_IDS
            or not isinstance(snapshot, Mapping)
            or snapshot.get("id") != "CMP-TECH-001"
            or snapshot.get("revision") != history_revision
            or snapshot.get("selected_option") is not None
        ):
            raise IntakeError("INTAKE.json comparison history is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        snapshot_proposer = _bounded_text(snapshot.get("proposed_by"), "Technology proposal actor", 120)
        snapshot_source = snapshot.get("proposal_source")
        snapshot_timestamp = _iso_timestamp(snapshot.get("proposed_at"))
        snapshot_options = snapshot.get("options")
        if snapshot_source not in SOURCE_IDS or snapshot_timestamp is None or not isinstance(snapshot_options, list):
            raise IntakeError("INTAKE.json comparison history is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        expected_snapshot = _technology_comparison(
            snapshot_options,
            manifest,
            snapshot_proposer,
            snapshot_source,
            snapshot_timestamp,
            history_revision,
            canonical_answer_map,
        )
        if dict(snapshot) != expected_snapshot:
            raise IntakeError("INTAKE.json comparison history is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        normalized_comparison_history.append(
            {
                "comparison_id": "CMP-TECH-001",
                "revision": history_revision,
                "superseded_at": superseded_at,
                "superseded_by": superseded_by,
                "source": history_source,
                "snapshot": deepcopy(expected_snapshot),
            }
        )
    if comparison_history:
        if active_technology is None or active_technology.get("revision") != len(normalized_comparison_history) + 1:
            raise IntakeError("INTAKE.json comparison history does not match the active revision.", "INTAKE_RECOVERY_REQUIRED", 409)
    elif active_technology is not None and active_technology.get("revision") != 1:
        raise IntakeError("INTAKE.json active technology comparison revision is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    normalized_receipts: list[dict[str, Any]] = []
    answer_receipts: dict[str, dict[str, Any]] = {}
    allowed_receipt_kinds = {
        "TEXT", "FACT", "EVIDENCE", "CHOICE", "PROPOSAL", "OPTION_PROPOSAL", "TECH_OPTIONS_REVISED",
        "FACT_REVALIDATED",
    }
    base_receipt_fields = {
        "receipt_id", "kind", "question_id", "old_revision", "new_revision", "actor", "source", "recorded_at",
    }
    for index, receipt in enumerate(receipts, 1):
        if not isinstance(receipt, Mapping):
            raise IntakeError("INTAKE.json receipts are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        receipt_id = receipt.get("receipt_id")
        kind = receipt.get("kind")
        question_id = receipt.get("question_id")
        old_revision = receipt.get("old_revision")
        new_revision = receipt.get("new_revision")
        actor = receipt.get("actor")
        source = receipt.get("source")
        timestamp = _iso_timestamp(receipt.get("recorded_at"))
        if (
            receipt_id != f"IR-{index:04d}"
            or kind not in allowed_receipt_kinds
            or question_id not in normalized_snapshots
            or not isinstance(old_revision, int) or isinstance(old_revision, bool)
            or not isinstance(new_revision, int) or isinstance(new_revision, bool)
            or old_revision != index
            or new_revision != old_revision + 1
            or not isinstance(actor, str)
            or not actor
            or len(actor) > 120
            or _contains_unsafe(actor)
            or source not in SOURCE_IDS
            or timestamp is None
        ):
            raise IntakeError("INTAKE.json receipts are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        normalized_receipt: dict[str, Any] = {
            "receipt_id": receipt_id,
            "kind": kind,
            "question_id": question_id,
            "old_revision": old_revision,
            "new_revision": new_revision,
            "actor": actor,
            "source": source,
            "recorded_at": timestamp,
        }
        if kind == "CHOICE":
            decision_id = receipt.get("decision_id")
            evidence_id = receipt.get("evidence_id")
            choice = receipt.get("choice")
            if (
                set(receipt) != base_receipt_fields | {"decision_id", "evidence_id", "choice"}
                or not isinstance(decision_id, str) or not DECISION_ID.fullmatch(decision_id)
                or not isinstance(evidence_id, str) or not EVIDENCE_ID.fullmatch(evidence_id)
                or not isinstance(choice, str) or not OPTION_ID.fullmatch(choice)
            ):
                raise IntakeError("INTAKE.json choice receipts are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
            normalized_receipt.update({"decision_id": decision_id, "evidence_id": evidence_id, "choice": choice})
        elif kind == "OPTION_PROPOSAL":
            decision_id = receipt.get("decision_id")
            option = receipt.get("option")
            if (
                set(receipt) != base_receipt_fields | {"decision_id", "option"}
                or not isinstance(decision_id, str)
                or not DECISION_ID.fullmatch(decision_id)
                or not isinstance(option, Mapping)
                or set(option) != {"id", "label", "description"}
            ):
                raise IntakeError("INTAKE.json option-proposal receipts are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
            normalized_option = {
                "id": option.get("id"),
                "label": _bounded_text(option.get("label"), "Option label", 200, allow_ambiguous=True),
                "description": _bounded_text(option.get("description"), "Option description", 1_000, allow_ambiguous=True),
            }
            if not isinstance(normalized_option["id"], str) or not OPTION_ID.fullmatch(normalized_option["id"]):
                raise IntakeError("INTAKE.json option-proposal receipts are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
            snapshot_options = normalized_snapshots[question_id]["options"]
            binding = next((item for item in normalized_bindings if item["question_id"] == question_id), None)
            if normalized_option not in snapshot_options or binding is None or binding["decision_id"] != decision_id:
                raise IntakeError("INTAKE.json option-proposal receipt conflicts with canonical choice state.", "INTAKE_RECOVERY_REQUIRED", 409)
            normalized_receipt.update({"decision_id": decision_id, "option": normalized_option})
        elif kind == "TECH_OPTIONS_REVISED":
            decision_id = receipt.get("decision_id")
            old_comparison_revision = receipt.get("old_comparison_revision")
            new_comparison_revision = receipt.get("new_comparison_revision")
            if (
                set(receipt) != base_receipt_fields
                | {"decision_id", "old_comparison_revision", "new_comparison_revision"}
                or question_id != "Q-SW-012"
                or not isinstance(decision_id, str)
                or not DECISION_ID.fullmatch(decision_id)
                or not isinstance(old_comparison_revision, int)
                or isinstance(old_comparison_revision, bool)
                or new_comparison_revision != old_comparison_revision + 1
            ):
                raise IntakeError("INTAKE.json technology-revision receipts are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
            normalized_receipt.update(
                {
                    "decision_id": decision_id,
                    "old_comparison_revision": old_comparison_revision,
                    "new_comparison_revision": new_comparison_revision,
                }
            )
        elif kind == "FACT_REVALIDATED":
            fact_revalidation_id = receipt.get("fact_revalidation_id")
            supersedes_receipt_id = receipt.get("supersedes_receipt_id")
            old_readiness = receipt.get("old_readiness")
            new_readiness = receipt.get("new_readiness")
            comparison_id = receipt.get("comparison_id")
            prior_decision_id = receipt.get("prior_decision_id")
            revalidation_decision_id = receipt.get("revalidation_decision_id")
            revalidation_question_id = receipt.get("revalidation_question_id")
            if (
                set(receipt) != base_receipt_fields
                | {
                    "fact_revalidation_id", "supersedes_receipt_id", "old_readiness", "new_readiness",
                    "comparison_id", "prior_decision_id", "revalidation_decision_id", "revalidation_question_id",
                }
                or not isinstance(fact_revalidation_id, str)
                or not re.fullmatch(r"FRV-\d{4}", fact_revalidation_id)
                or not isinstance(supersedes_receipt_id, str)
                or not re.fullmatch(r"IR-\d{4}", supersedes_receipt_id)
                or old_readiness != "UNKNOWN"
                or new_readiness not in {"HUMAN_ANSWERED", "ESTABLISHED"}
                or comparison_id not in {"CMP-GP-001", "CMP-FR-001"}
                or not isinstance(prior_decision_id, str)
                or not DECISION_ID.fullmatch(prior_decision_id)
                or not isinstance(revalidation_decision_id, str)
                or not DECISION_ID.fullmatch(revalidation_decision_id)
                or not isinstance(revalidation_question_id, str)
                or not re.fullmatch(r"Q-RV-\d{3}", revalidation_question_id)
            ):
                raise IntakeError("INTAKE.json fact-revalidation receipts are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
            normalized_receipt.update(
                {
                    "fact_revalidation_id": fact_revalidation_id,
                    "supersedes_receipt_id": supersedes_receipt_id,
                    "old_readiness": old_readiness,
                    "new_readiness": new_readiness,
                    "comparison_id": comparison_id,
                    "prior_decision_id": prior_decision_id,
                    "revalidation_decision_id": revalidation_decision_id,
                    "revalidation_question_id": revalidation_question_id,
                }
            )
        elif set(receipt) != base_receipt_fields:
            raise IntakeError("INTAKE.json answer receipts contain unsupported fields.", "INTAKE_RECOVERY_REQUIRED", 409)
        if kind in {"TEXT", "FACT", "EVIDENCE", "CHOICE"}:
            if question_id in answer_receipts:
                raise IntakeError("INTAKE.json repeats an answer receipt.", "INTAKE_RECOVERY_REQUIRED", 409)
            answer_receipts[question_id] = normalized_receipt
        normalized_receipts.append(normalized_receipt)
    technology_revision_receipts = [
        item for item in normalized_receipts if item["kind"] == "TECH_OPTIONS_REVISED"
    ]
    if len(technology_revision_receipts) != len(normalized_comparison_history):
        raise IntakeError("INTAKE.json technology history lacks exact mutation receipts.", "INTAKE_RECOVERY_REQUIRED", 409)
    for history_entry, receipt in zip(normalized_comparison_history, technology_revision_receipts):
        binding = next(
            (item for item in normalized_bindings if item["question_id"] == "Q-SW-012"),
            None,
        )
        if (
            binding is None
            or receipt["decision_id"] != binding["decision_id"]
            or receipt["old_comparison_revision"] != history_entry["revision"]
            or receipt["new_comparison_revision"] != history_entry["revision"] + 1
            or receipt["source"] != history_entry["source"]
            or receipt["recorded_at"] != history_entry["superseded_at"]
        ):
            raise IntakeError("INTAKE.json technology history conflicts with its receipt.", "INTAKE_RECOVERY_REQUIRED", 409)
    fact_revalidation_receipts = [
        item for item in normalized_receipts if item["kind"] == "FACT_REVALIDATED"
    ]
    if len(fact_revalidation_receipts) != len(normalized_fact_revalidations):
        raise IntakeError("INTAKE.json fact revalidation history lacks exact mutation receipts.", "INTAKE_RECOVERY_REQUIRED", 409)
    for entry, receipt in zip(normalized_fact_revalidations, fact_revalidation_receipts):
        prior_answer_receipt = next(
            (
                item for item in normalized_receipts
                if item["question_id"] == entry["question_id"] and item["kind"] in {"FACT", "EVIDENCE"}
            ),
            None,
        )
        replacement = entry["replacement"]
        if (
            prior_answer_receipt is None
            or prior_answer_receipt["receipt_id"] != entry["supersedes_receipt_id"]
            or receipt["receipt_id"] != entry["receipt_id"]
            or receipt["fact_revalidation_id"] != entry["id"]
            or receipt["question_id"] != entry["question_id"]
            or receipt["supersedes_receipt_id"] != entry["supersedes_receipt_id"]
            or receipt["old_readiness"] != entry["previous_answer"]["readiness"]
            or receipt["new_readiness"] != replacement["readiness"]
            or receipt["actor"] != replacement["actor"]
            or receipt["source"] != replacement["source"]
            or receipt["recorded_at"] != replacement["recorded_at"]
            or receipt["comparison_id"] != entry["comparison_id"]
            or receipt["prior_decision_id"] != entry["prior_decision_id"]
            or receipt["revalidation_decision_id"] != entry["revalidation_decision_id"]
            or receipt["revalidation_question_id"] != entry["revalidation_question_id"]
        ):
            raise IntakeError("INTAKE.json fact revalidation history conflicts with its receipt.", "INTAKE_RECOVERY_REQUIRED", 409)
    if revision != len(normalized_receipts) + 1:
        raise IntakeError("INTAKE.json revision does not match its mutation receipts.", "INTAKE_RECOVERY_REQUIRED", 409)
    for answer in normalized_answers:
        question_id = answer["question_id"]
        receipt = answer_receipts.get(question_id)
        snapshot = normalized_snapshots[question_id]
        expected_kinds = (
            {"CHOICE"}
            if snapshot["answer_type"] == "choice"
            else ({"TEXT"} if snapshot["answer_type"] == "text" else {"FACT", "EVIDENCE"})
        )
        if (
            receipt is None
            or receipt["kind"] not in expected_kinds
            or receipt["actor"] != answer["actor"]
            or receipt["source"] != answer["source"]
            or receipt["recorded_at"] != answer["answered_at"]
        ):
            raise IntakeError("INTAKE.json answer provenance does not match its receipt.", "INTAKE_RECOVERY_REQUIRED", 409)
        if receipt["kind"] == "EVIDENCE" and answer.get("readiness") != "ESTABLISHED":
            raise IntakeError("An evidence receipt must establish a readiness fact.", "INTAKE_RECOVERY_REQUIRED", 409)
        if receipt["kind"] == "CHOICE":
            binding = next((item for item in normalized_bindings if item["question_id"] == question_id), None)
            if (
                binding is None
                or binding["status"] != "RESOLVED"
                or receipt["decision_id"] != binding["decision_id"]
                or receipt["evidence_id"] != binding["evidence_id"]
                or receipt["choice"] != answer["value"]
                or receipt["choice"] != binding["selected_option"]
            ):
                raise IntakeError("INTAKE.json choice receipt conflicts with its Decision binding.", "INTAKE_RECOVERY_REQUIRED", 409)
    if set(answer_receipts) != {item["question_id"] for item in normalized_answers}:
        raise IntakeError("INTAKE.json contains an answer receipt without a canonical answer.", "INTAKE_RECOVERY_REQUIRED", 409)
    created_at = _iso_timestamp(value.get("created_at"))
    updated_at = _iso_timestamp(value.get("updated_at"))
    if created_at is None or updated_at is None:
        raise IntakeError("INTAKE.json timestamps are invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    return {
        "schema_version": INTAKE_SCHEMA_VERSION,
        "intake_id": INTAKE_ID,
        "effort_id": effort_id,
        "revision": revision,
        "flow_version": FLOW_VERSION,
        "status": value["status"],
        "intent": intent,
        "domain": {
            "proposed": domain["proposed"], "confidence": domain["confidence"], "ambiguous": domain["ambiguous"],
            "hybrid_candidate": domain["hybrid_candidate"],
            "signals": list(signals), "alternatives": list(alternatives),
            "suggested_secondary_domains": list(suggested_secondary), "selected": selected,
            "selected_option": domain_selected_option,
            "primary_domain": selected,
            "secondary_workstreams": normalized_workstreams,
            "selection_source": domain.get("selection_source"),
        },
        "question_order": list(order),
        "question_snapshots": normalized_snapshots,
        "answers": normalized_answers,
        "current_question_id": current,
        "decision_bindings": normalized_bindings,
        "comparisons": normalized_comparisons,
        "comparison_history": normalized_comparison_history,
        "fact_revalidations": normalized_fact_revalidations,
        "receipts": normalized_receipts,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _safe_comparison(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": value.get("id") if isinstance(value.get("id"), str) and re.fullmatch(r"CMP-(?:SW|GP|FR|OT|TECH)-\d{3,}", value["id"]) else "CMP-OT-001",
        "kind": value.get("kind") if value.get("kind") in {"architecture_strategy", "named_technology", "delivery_route", "reporting_route", "route"} else "route",
        "domain": value.get("domain") if value.get("domain") in DOMAIN_IDS else "OTHER",
        "title": value.get("title") if isinstance(value.get("title"), str) else "Route comparison",
        "criteria": [item for item in value.get("criteria", []) if isinstance(item, str) and len(item) <= 64][:32],
        "options": [],
        "recommended_option": value.get("recommended_option") if isinstance(value.get("recommended_option"), str) else None,
        "selected_option": value.get("selected_option") if isinstance(value.get("selected_option"), str) else None,
        "recommendation_rationale": value.get("recommendation_rationale") if isinstance(value.get("recommendation_rationale"), str) else "",
        "proposed_by": value.get("proposed_by") if isinstance(value.get("proposed_by"), str) else None,
        "proposal_source": value.get("proposal_source") if value.get("proposal_source") in SOURCE_IDS else None,
        "proposed_at": value.get("proposed_at") if _iso_timestamp(value.get("proposed_at")) else None,
        "revision": value.get("revision") if isinstance(value.get("revision"), int) and not isinstance(value.get("revision"), bool) and value.get("revision") > 0 else None,
        "recommendation_status": value.get("recommendation_status") if value.get("recommendation_status") in {"GROUNDED", "CONDITIONAL"} else None,
        "facts_digest": value.get("facts_digest") if isinstance(value.get("facts_digest"), str) and re.fullmatch(r"[0-9a-f]{64}", value["facts_digest"]) else None,
        "grounding": [],
    }
    raw_grounding = value.get("grounding")
    if isinstance(raw_grounding, list):
        for item in raw_grounding[:32]:
            if (
                isinstance(item, Mapping)
                and isinstance(item.get("key"), str)
                and re.fullmatch(r"[a-z][a-z0-9_]{1,63}", item["key"])
                and item.get("readiness") in {"ESTABLISHED", "HUMAN_ANSWERED", "NOT_APPLICABLE", "UNKNOWN", "MISSING", "INVALID"}
            ):
                result["grounding"].append({"key": item["key"], "readiness": item["readiness"]})
    for option in value.get("options", [])[:16] if isinstance(value.get("options"), list) else []:
        if not isinstance(option, Mapping) or not isinstance(option.get("id"), str) or not OPTION_ID.fullmatch(option["id"]):
            continue
        public_option: dict[str, Any] = {}
        for key, item in option.items():
            if key in {"id", "label", "name", "version_or_constraint", "summary", "mvp_speed", "scale_beyond_mvp", "reliability", "efficiency", "cost", "complexity", "lock_in", "security_privacy", "team_fit", "rationale", "schedule", "cost_certainty", "safety_quality", "coordination", "flexibility", "regulatory_dependency", "close_speed", "auditability", "controls", "scalability", "reconciliation_effort", "speed", "reversibility", "governance"} and isinstance(item, str) and len(item) <= 2_000 and not _contains_unsafe(item):
                public_option[key] = item
            elif key == "recommendation" and isinstance(item, bool):
                public_option[key] = item
            elif key in {"evidence_refs", "primary_sources"} and isinstance(item, list):
                public_option[key] = [value for value in item if isinstance(value, str) and len(value) <= 1_000 and not _contains_unsafe(value)][:32]
        result["options"].append(public_option)
    return result


def _intake_readiness(intake: Mapping[str, Any]) -> dict[str, Any]:
    answers = _answers_by_key(intake)
    regulatory = answers.get("reporting_need") == "REGULATORY"
    by_question = {
        item.get("question_id"): item
        for item in intake.get("answers", [])
        if isinstance(item, Mapping) and isinstance(item.get("question_id"), str)
    }
    pending_revalidation_questions: list[str] = []
    bindings = {
        item.get("question_id"): item
        for item in intake.get("decision_bindings", [])
        if isinstance(item, Mapping) and isinstance(item.get("question_id"), str)
    }
    for revision in intake.get("fact_revalidations", []):
        if not isinstance(revision, Mapping):
            continue
        question_id = revision.get("question_id")
        replacement = revision.get("replacement")
        if isinstance(question_id, str) and isinstance(replacement, Mapping):
            by_question[question_id] = replacement
        revalidation_question_id = revision.get("revalidation_question_id")
        binding = bindings.get(revalidation_question_id)
        if (
            isinstance(revalidation_question_id, str)
            and (not isinstance(binding, Mapping) or binding.get("status") != "RESOLVED")
        ):
            pending_revalidation_questions.append(revalidation_question_id)
    blocking: list[str] = []
    if regulatory:
        for question_id in sorted(REGULATORY_REQUIRED_FACTS):
            answer = by_question.get(question_id)
            if not isinstance(answer, Mapping) or answer.get("readiness") not in {"ESTABLISHED", "HUMAN_ANSWERED"}:
                blocking.append(question_id)
    blocking.extend(pending_revalidation_questions)
    blocking = sorted(set(blocking))
    return {
        "regulatory_reporting": regulatory,
        "exit_ready": not blocking,
        "blocking_questions": blocking,
        "pending_revalidation_questions": sorted(set(pending_revalidation_questions)),
        "reason": (
            "Record the required fact and explicitly revalidate every route Decision that depended on its earlier UNKNOWN premise."
            if pending_revalidation_questions
            else "Confirm the regulatory jurisdiction, reporting basis, and qualified sign-off authority before planning exit."
            if blocking
            else "No unresolved mandatory regulatory-reporting fact blocks planning exit."
        ),
    }


def _effective_fact_records(intake: Mapping[str, Any]) -> list[dict[str, Any]]:
    snapshots = intake.get("question_snapshots", {})
    receipts = intake.get("receipts", [])
    latest = {
        item.get("question_id"): item
        for item in intake.get("fact_revalidations", [])
        if isinstance(item, Mapping) and isinstance(item.get("question_id"), str)
    }
    result: list[dict[str, Any]] = []
    for answer in intake.get("answers", []):
        if not isinstance(answer, Mapping):
            continue
        question_id = answer.get("question_id")
        snapshot = snapshots.get(question_id) if isinstance(snapshots, Mapping) else None
        if not isinstance(snapshot, Mapping) or snapshot.get("answer_type") != "fact":
            continue
        original_receipt = next(
            (
                item for item in receipts
                if isinstance(item, Mapping)
                and item.get("question_id") == question_id
                and item.get("kind") in {"FACT", "EVIDENCE"}
            ),
            None,
        )
        revision = latest.get(question_id)
        effective = revision.get("replacement") if isinstance(revision, Mapping) else answer
        if not isinstance(effective, Mapping):
            continue
        result.append(
            {
                "question_id": question_id,
                "key": snapshot.get("key"),
                "value": effective.get("value"),
                "readiness": effective.get("readiness"),
                "detail": effective.get("detail"),
                "support": effective.get("support"),
                "actor": effective.get("actor"),
                "source": effective.get("source"),
                "recorded_at": effective.get("recorded_at", effective.get("answered_at")),
                "original_receipt_id": original_receipt.get("receipt_id") if isinstance(original_receipt, Mapping) else None,
                "effective_receipt_id": revision.get("receipt_id") if isinstance(revision, Mapping) else (
                    original_receipt.get("receipt_id") if isinstance(original_receipt, Mapping) else None
                ),
                "revalidated": isinstance(revision, Mapping),
            }
        )
    return result


def public_intake_state(
    effort_dir: Path,
    project_root: Path,
    manifest: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Return bounded intake data plus non-reflective state diagnostics."""
    missing = {
        "state": "NOT_STARTED", "status": "NOT_STARTED", "revision": 0, "flow_version": FLOW_VERSION,
        "progress": {"answered": 0, "total": None, "percent": 0}, "current_question": None,
        "domain": None, "secondary_confirmation": None,
        "answers": [], "effective_facts": [], "fact_revalidations": [], "decision_bindings": [],
        "comparisons": [], "comparison_history": [], "receipts": [],
        "readiness": {"regulatory_reporting": False, "exit_ready": True, "blocking_questions": [], "pending_revalidation_questions": [], "reason": "Intake has not started."},
    }
    transaction = effort_dir / ".INTAKE.transaction"
    if transaction.exists() or transaction.is_symlink():
        return ({**missing, "state": "RECOVERY_REQUIRED", "status": "RECOVERY_REQUIRED"}, [
            {"severity": "error", "code": "INTAKE_TRANSACTION_PENDING", "message": "An interrupted intake transaction requires recovery before intake state can be trusted.", "path": "[intake transaction]"}
        ])
    path = effort_dir / INTAKE_FILENAME
    if not path.exists() and not path.is_symlink():
        return missing, []
    relative = str(path.relative_to(project_root)) if _within(project_root, path) else "[unsafe intake path]"
    if path.is_symlink() or not _within(effort_dir, path) or not _within(project_root, path):
        return ({**missing, "state": "INVALID", "status": "INVALID"}, [
            {"severity": "error", "code": "INTAKE_PATH_ESCAPE", "message": "INTAKE.json must be a regular file inside the selected effort.", "path": "[unsafe intake path]"}
        ])
    try:
        raw = _parse_json(_read_regular(path, "INTAKE.json"), "INTAKE.json")
        effort_meta = manifest.get("effort") if isinstance(manifest.get("effort"), Mapping) else {}
        effort_id = effort_meta.get("id") if isinstance(effort_meta.get("id"), str) else effort_dir.name
        intake = _validate_intake(raw, effort_id, manifest)
        for entry in intake.get("fact_revalidations", []):
            replacement = entry.get("replacement") if isinstance(entry, Mapping) else None
            if isinstance(replacement, Mapping) and replacement.get("readiness") == "ESTABLISHED":
                _validate_evidence_pointer(project_root, manifest, replacement.get("support"))
    except IntakeError:
        return ({**missing, "state": "INVALID", "status": "INVALID"}, [
            {"severity": "error", "code": "INTAKE_INVALID", "message": "INTAKE.json is malformed, inconsistent, or unsafe.", "path": relative}
        ])
    current_id = intake["current_question_id"]
    current = deepcopy(intake["question_snapshots"].get(current_id)) if current_id else None
    if current:
        binding = next((item for item in intake["decision_bindings"] if item["question_id"] == current_id), None)
        current["decision_id"] = binding["decision_id"] if binding else None
        current["expected_revision"] = intake["revision"]
    total = len(intake["question_order"])
    answered = len(intake["answers"])
    suggested_secondary = list(intake["domain"].get("suggested_secondary_domains", []))
    secondary_confirmation = {
        "recommended": bool(suggested_secondary),
        "suggested_domains": suggested_secondary,
        "can_record_now": intake["domain"].get("primary_domain") in DOMAIN_IDS
        and intake["status"] != "AWAITING_TECH_OPTIONS",
        "prompt": (
            "Should Wayfinder record one of the signaled domains as a material secondary workstream?"
            if suggested_secondary
            else "No unconfirmed material secondary domain was detected."
        ),
        "action": "add_secondary_workstream",
    }
    return {
        "state": "AVAILABLE",
        "status": intake["status"],
        "revision": intake["revision"],
        "flow_version": intake["flow_version"],
        "progress": {"answered": answered, "total": total, "percent": int((answered * 100) / total) if total else 0},
        "current_question": current,
        "domain": deepcopy(intake["domain"]),
        "secondary_confirmation": secondary_confirmation,
        "answers": deepcopy(intake["answers"][:MAX_PUBLIC_ITEMS]),
        "effective_facts": _effective_fact_records(intake)[:MAX_PUBLIC_ITEMS],
        "fact_revalidations": deepcopy(intake["fact_revalidations"][:MAX_FACT_REVALIDATIONS]),
        "decision_bindings": deepcopy(intake["decision_bindings"][:MAX_PUBLIC_ITEMS]),
        "comparisons": [_safe_comparison(item) for item in intake["comparisons"][:8]],
        "comparison_history": [
            {
                "comparison_id": item["comparison_id"],
                "revision": item["revision"],
                "superseded_at": item["superseded_at"],
                "superseded_by": item["superseded_by"],
                "source": item["source"],
                "snapshot": _safe_comparison(item["snapshot"]),
            }
            for item in intake["comparison_history"][:16]
        ],
        "receipts": deepcopy(intake["receipts"][:MAX_PUBLIC_ITEMS]),
        "readiness": _intake_readiness(intake),
    }, []


def _safe_context(root: Path, effort: str | Path | None) -> tuple[Any, Path, Path, dict[str, Any]]:
    state_module = _load_state_module()
    try:
        project_root, effort_dir = state_module.resolve_effort(root, effort)
    except (OSError, ValueError, state_module.WayfinderError) as exc:
        raise IntakeError("The selected Wayfinder effort is unavailable or unsafe.", "INTAKE_NOT_READY", 409) from exc
    manifest_path = effort_dir / "EFFORT.json"
    if manifest_path.is_symlink() or not _within(effort_dir, manifest_path) or not _within(project_root, manifest_path):
        raise IntakeError("A safe schema-3 EFFORT.json is required for intake.", "INTAKE_NOT_READY", 409)
    try:
        manifest = _parse_json(_read_regular(manifest_path, "EFFORT.json", 4 * 1024 * 1024), "EFFORT.json")
    except (FileNotFoundError, IntakeError) as exc:
        raise IntakeError("A safe schema-3 EFFORT.json is required for intake.", "INTAKE_NOT_READY", 409) from exc
    effort_meta = manifest.get("effort")
    if (
        manifest.get("schema_version") != 3
        or not isinstance(effort_meta, Mapping)
        or effort_meta.get("id") != effort_dir.name
        or not SAFE_SLUG.fullmatch(effort_dir.name)
    ):
        raise IntakeError("A valid schema-3 effort identity is required for intake.", "INTAKE_NOT_READY", 409)
    return state_module, project_root, effort_dir, manifest


def _health_codes(state: Mapping[str, Any]) -> set[str]:
    health = state.get("health")
    issues = health.get("issues") if isinstance(health, Mapping) else None
    if not isinstance(issues, list):
        return {"STATE_UNAVAILABLE"}
    return {
        item.get("code")
        for item in issues
        if isinstance(item, Mapping) and isinstance(item.get("code"), str)
    }


def _require_intake_ready(state: Mapping[str, Any], *, framing_complete: bool, transaction_visible: bool = False) -> None:
    contract = state.get("manifest_contract")
    if not isinstance(contract, Mapping) or contract.get("state") != "schema-3":
        raise IntakeError("Intake requires a valid schema-3 manifest.", "INTAKE_NOT_READY", 409)
    allowed = set() if framing_complete else set(INTAKE_SCAFFOLD_ERROR_CODES)
    if transaction_visible:
        allowed.add("INTAKE_TRANSACTION_PENDING")
    unexpected = _health_codes(state) - allowed
    if unexpected:
        raise IntakeError("Wayfinder structural issues must be repaired before intake can continue.", "INTAKE_NOT_READY", 409)


def _load_intake(effort_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    path = effort_dir / INTAKE_FILENAME
    if path.is_symlink() or not _within(effort_dir, path):
        raise IntakeError("INTAKE.json is unavailable or unsafe.", "INTAKE_RECOVERY_REQUIRED", 409)
    try:
        raw = _parse_json(_read_regular(path, "INTAKE.json"), "INTAKE.json")
    except FileNotFoundError as exc:
        raise IntakeError("No intake exists for this effort. Start intake first.", "INTAKE_NOT_READY", 409) from exc
    effort_meta = manifest.get("effort") if isinstance(manifest.get("effort"), Mapping) else {}
    effort_id = effort_meta.get("id") if isinstance(effort_meta.get("id"), str) else effort_dir.name
    return _validate_intake(raw, effort_id, manifest)


def _safe_target(project_root: Path, effort_dir: Path, path: Path) -> str:
    if path.is_symlink() or not _within(project_root, path) or not _within(effort_dir, path):
        raise IntakeError("An intake transaction target is unsafe.", "INTAKE_RECOVERY_REQUIRED", 409)
    relative = path.relative_to(effort_dir).as_posix()
    if relative in {INTAKE_FILENAME, "EFFORT.json", "MAP.md"}:
        return relative
    if re.fullmatch(r"decisions/D-\d{3,}\.md", relative) or re.fullmatch(r"evidence/E-\d{3,}\.md", relative):
        return relative
    raise IntakeError("An intake transaction target is outside the canonical intake contract.", "INTAKE_RECOVERY_REQUIRED", 409)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_replace(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=False, exist_ok=True)
    temporary = path.parent / f".{path.name}.intake-{os.getpid()}-{secrets.token_hex(8)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def _intake_lock(effort_dir: Path) -> Iterator[None]:
    if fcntl is None:
        raise IntakeError("Atomic intake locking is unavailable on this host.", "INTAKE_NOT_READY", 409)
    path = effort_dir / ".INTAKE.lock"
    if path.is_symlink() or not _within(effort_dir, path):
        raise IntakeError("The intake lock is unsafe.", "INTAKE_RECOVERY_REQUIRED", 409)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise IntakeError("The intake lock is unavailable.", "INTAKE_BUSY", 409) from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise IntakeError("The intake lock is unsafe.", "INTAKE_RECOVERY_REQUIRED", 409)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise IntakeError("Another intake update is already in progress.", "INTAKE_BUSY", 409) from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(descriptor)


def _recover_transaction(project_root: Path, effort_dir: Path) -> None:
    journal_path = effort_dir / ".INTAKE.transaction"
    if not journal_path.exists() and not journal_path.is_symlink():
        return
    if journal_path.is_symlink() or not _within(effort_dir, journal_path):
        raise IntakeError("The intake recovery journal is unsafe.", "INTAKE_RECOVERY_REQUIRED", 409)
    try:
        journal = _parse_json(_read_regular(journal_path, "Intake transaction", 32 * 1024 * 1024), "Intake transaction")
    except (FileNotFoundError, IntakeError) as exc:
        raise IntakeError("The intake recovery journal cannot be trusted.", "INTAKE_RECOVERY_REQUIRED", 409) from exc
    targets = journal.get("targets")
    if journal.get("schema_version") != 1 or journal.get("effort_id") != effort_dir.name or not isinstance(targets, list) or len(targets) > 64:
        raise IntakeError("The intake recovery journal cannot be trusted.", "INTAKE_RECOVERY_REQUIRED", 409)
    restore: list[tuple[Path, bytes | None]] = []
    for item in targets:
        if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
            raise IntakeError("The intake recovery journal cannot be trusted.", "INTAKE_RECOVERY_REQUIRED", 409)
        path = effort_dir / item["path"]
        if _safe_target(project_root, effort_dir, path) != item["path"]:
            raise IntakeError("The intake recovery journal cannot be trusted.", "INTAKE_RECOVERY_REQUIRED", 409)
        existed = item.get("existed")
        original_b64 = item.get("original")
        original_hash = item.get("original_sha256")
        new_hash = item.get("new_sha256")
        if not isinstance(existed, bool) or not isinstance(new_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", new_hash):
            raise IntakeError("The intake recovery journal cannot be trusted.", "INTAKE_RECOVERY_REQUIRED", 409)
        original: bytes | None
        if existed:
            if not isinstance(original_b64, str) or not isinstance(original_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", original_hash):
                raise IntakeError("The intake recovery journal cannot be trusted.", "INTAKE_RECOVERY_REQUIRED", 409)
            try:
                original = base64.b64decode(original_b64.encode("ascii"), validate=True)
            except (ValueError, UnicodeError) as exc:
                raise IntakeError("The intake recovery journal cannot be trusted.", "INTAKE_RECOVERY_REQUIRED", 409) from exc
            if hashlib.sha256(original).hexdigest() != original_hash:
                raise IntakeError("The intake recovery journal cannot be trusted.", "INTAKE_RECOVERY_REQUIRED", 409)
        else:
            if original_b64 is not None or original_hash is not None:
                raise IntakeError("The intake recovery journal cannot be trusted.", "INTAKE_RECOVERY_REQUIRED", 409)
            original = None
        try:
            current = _read_regular(path, "Intake recovery target", 4 * 1024 * 1024)
        except FileNotFoundError:
            current = None
        current_hash = hashlib.sha256(current).hexdigest() if current is not None else None
        legal_hashes = {new_hash, original_hash if existed else None}
        if current_hash not in legal_hashes:
            raise IntakeError("An intake target changed outside the interrupted transaction; automatic recovery stopped.", "INTAKE_RECOVERY_REQUIRED", 409)
        restore.append((path, original))
    for path, original in restore:
        if original is None:
            try:
                path.unlink()
                _fsync_dir(path.parent)
            except FileNotFoundError:
                pass
        else:
            _atomic_replace(path, original)
    journal_path.unlink()
    _fsync_dir(effort_dir)


def _transactional_write(
    project_root: Path,
    effort_dir: Path,
    updates: Mapping[Path, bytes],
    validate: Callable[[], None],
) -> None:
    if not updates or len(updates) > 64:
        raise IntakeError("The intake transaction is empty or too large.", "INTAKE_VALIDATION", 422)
    journal_path = effort_dir / ".INTAKE.transaction"
    originals: dict[Path, bytes | None] = {}
    targets: list[dict[str, Any]] = []
    for path in sorted(updates, key=lambda item: item.as_posix()):
        relative = _safe_target(project_root, effort_dir, path)
        if path.parent.is_symlink() or not path.parent.is_dir() or not _within(effort_dir, path.parent):
            raise IntakeError("An intake artifact directory is unavailable or unsafe.", "INTAKE_RECOVERY_REQUIRED", 409)
        try:
            original = _read_regular(path, "Intake transaction target", 4 * 1024 * 1024)
        except FileNotFoundError:
            original = None
        originals[path] = original
        targets.append(
            {
                "path": relative,
                "existed": original is not None,
                "original": base64.b64encode(original).decode("ascii") if original is not None else None,
                "original_sha256": hashlib.sha256(original).hexdigest() if original is not None else None,
                "new_sha256": hashlib.sha256(updates[path]).hexdigest(),
            }
        )
    journal = {"schema_version": 1, "effort_id": effort_dir.name, "created_at": _now(), "targets": targets}
    _atomic_replace(journal_path, _json_bytes(journal))
    try:
        # Compare every target before the first visible replacement.
        for path, original in originals.items():
            try:
                current = _read_regular(path, "Intake CAS target", 4 * 1024 * 1024)
            except FileNotFoundError:
                current = None
            if current != original:
                raise IntakeError("An intake artifact changed concurrently; refresh and retry.", "INTAKE_REVISION_CONFLICT", 409)
        for path in sorted(updates, key=lambda item: item.as_posix()):
            _atomic_replace(path, updates[path])
        validate()
    except BaseException:
        rollback_failed = False
        for path, original in originals.items():
            try:
                if original is None:
                    try:
                        path.unlink()
                        _fsync_dir(path.parent)
                    except FileNotFoundError:
                        pass
                else:
                    _atomic_replace(path, original)
            except BaseException:
                rollback_failed = True
        if not rollback_failed:
            try:
                journal_path.unlink()
                _fsync_dir(effort_dir)
            except OSError:
                pass
        raise
    journal_path.unlink()
    _fsync_dir(effort_dir)


def _next_id(manifest: Mapping[str, Any], effort_dir: Path, kind: str) -> str:
    if kind == "decision":
        pattern, prefix, key, directory = DECISION_ID, "D", "decisions", "decisions"
    else:
        pattern, prefix, key, directory = EVIDENCE_ID, "E", "evidence", "evidence"
    values: set[int] = set()
    raw = manifest.get(key, [])
    if isinstance(raw, list):
        for item in raw:
            candidate = item.get("id") if isinstance(item, Mapping) else None
            if isinstance(candidate, str) and pattern.fullmatch(candidate):
                values.add(int(candidate.split("-")[1]))
    folder = effort_dir / directory
    if folder.is_symlink() or not folder.is_dir() or not _within(effort_dir, folder):
        raise IntakeError("A canonical intake artifact directory is unavailable or unsafe.", "INTAKE_RECOVERY_REQUIRED", 409)
    for item in folder.iterdir():
        if item.is_symlink():
            raise IntakeError("A canonical intake artifact directory contains an unsafe entry.", "INTAKE_RECOVERY_REQUIRED", 409)
        if item.is_file() and re.fullmatch(rf"{prefix}-\d{{3,}}\.md", item.name):
            values.add(int(item.stem.split("-")[1]))
    number = max(values, default=0) + 1
    if number > 999_999:
        raise IntakeError("The intake artifact ID space is exhausted.", "INTAKE_NOT_READY", 409)
    return f"{prefix}-{number:03d}"


def _table_cell(value: str) -> str:
    return value.replace("|", "&#124;").replace("\r", " ").replace("\n", " ")


def _render_decision(
    decision_id: str,
    question: Mapping[str, Any],
    authority: str,
    created_at: str,
    *,
    choice: str | None = None,
    evidence_id: str | None = None,
    resolved_at: str | None = None,
) -> bytes:
    title = _bounded_text(question.get("decision_title"), "Decision title", 300, allow_ambiguous=True)
    prompt = _bounded_text(question.get("prompt"), "Question prompt", 2_000, allow_ambiguous=True)
    status = "RESOLVED" if choice is not None else "OPEN"
    evidence = evidence_id or "none"
    options = "\n".join(
        f"| {_table_cell(item['id'])} | {_table_cell(item['label'])} | {_table_cell(item['description'])} |"
        for item in question.get("options", [])
    )
    selected_label = next(
        (item["label"] for item in question.get("options", []) if item.get("id") == choice),
        choice or "",
    )
    hypothesis = "No option is selected automatically; the recorded classifier or comparison is advisory only."
    recommendation = "Review the listed options and record the human authority's explicit selection."
    resolution = (
        f"The human authority explicitly selected {selected_label} ({choice}) for this intake choice."
        if choice is not None
        else ""
    )
    transitions = f"| — | OPEN | Wayfinder intake | {created_at} | Intake choice created for explicit human selection | none |"
    if choice is not None and resolved_at and evidence_id:
        transitions += f"\n| OPEN | RESOLVED | {_table_cell(authority)} | {resolved_at} | Explicit intake option selected | {evidence_id} |"
    revalidates = question.get("revalidates")
    revalidates_text = revalidates if isinstance(revalidates, str) and DECISION_ID.fullmatch(revalidates) else "none"
    content = f"""# {decision_id}: {title}

- **Kind:** DECISION
- **Phase:** p2-resolve
- **Type:** EXTERNAL-INPUT
- **Autonomy:** HITL
- **Responsible party:** {authority}
- **Decision authority:** {authority}
- **Next actor:** {authority}
- **Status:** {status}
- **Destination blocking:** {'true' if question.get('destination_blocking') is True else 'false'}
- **Requires:** none
- **Revalidates:** {revalidates_text}
- **Informs:** {evidence}
- **Evidence:** {evidence}
- **Claimed by:** none
- **Claimed at:** none
- **Claim expires at:** none
- **Revision:** 1
- **Blocks / affects:** {prompt}
- **Invalidation rule:** Reopen if the human authority changes this recorded choice or its framing changes materially.

## Options

| ID | Option | Meaning |
| --- | --- | --- |
{options}

## Current hypothesis

{hypothesis}

## Recommended direction

{recommendation}

## Evidence still required

An explicit selection by the named human decision authority.

## Resolution

{resolution}

## Dependent inspections

| Trigger | Dependent | Outcome (`STILL-VALID`, `REOPENED`, `SUPERSEDED`) | Evidence | Actor | Timestamp |
| --- | --- | --- | --- | --- | --- |

## Append-only transition history

| From | To | Actor | Timestamp | Reason | Evidence |
| --- | --- | --- | --- | --- | --- |
{transitions}
"""
    return content.encode("utf-8")


def _metadata_value(text: str, label: str) -> str:
    matches = re.findall(
        rf"^-\s*\*\*{re.escape(label)}:\*\*\s*(.*?)\s*$",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    if len(matches) != 1:
        raise IntakeError("The bound Decision metadata is missing or ambiguous.", "INTAKE_RECOVERY_REQUIRED", 409)
    return matches[0]


def _replace_metadata_value(text: str, label: str, value: str) -> str:
    pattern = re.compile(
        rf"^(-\s*\*\*{re.escape(label)}:\*\*\s*).*$",
        flags=re.MULTILINE | re.IGNORECASE,
    )
    if len(pattern.findall(text)) != 1:
        raise IntakeError("The bound Decision metadata is missing or ambiguous.", "INTAKE_RECOVERY_REQUIRED", 409)
    return pattern.sub(rf"\g<1>{value}", text, count=1)


def _replace_decision_section(text: str, heading: str, body: str) -> str:
    pattern = re.compile(
        rf"(^##\s+{re.escape(heading)}\s*$\n)(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        raise IntakeError("The bound Decision is missing a canonical section.", "INTAKE_RECOVERY_REQUIRED", 409)
    return text[: match.start(2)] + "\n" + body.strip() + "\n\n" + text[match.end(2) :]


def _options_table(question: Mapping[str, Any]) -> str:
    rows = "\n".join(
        f"| {_table_cell(item['id'])} | {_table_cell(item['label'])} | {_table_cell(item['description'])} |"
        for item in question.get("options", [])
    )
    return "| ID | Option | Meaning |\n| --- | --- | --- |\n" + rows


def _append_option_revision_history(
    text: str,
    old_revision: int,
    new_revision: int,
    actor: str,
    timestamp: str,
    reason: str,
) -> str:
    row = (
        f"| {old_revision} | {new_revision} | {_table_cell(actor)} | {timestamp} | "
        f"{_table_cell(reason)} |"
    )
    heading = "Option revision history"
    pattern = re.compile(
        rf"(^##\s+{re.escape(heading)}\s*$\n)(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(text)
    if match:
        body = match.group(2).strip() + "\n" + row
        return text[: match.start(2)] + "\n" + body + "\n\n" + text[match.end(2) :]
    marker = re.search(r"^##\s+Current hypothesis\s*$", text, flags=re.MULTILINE | re.IGNORECASE)
    if marker is None:
        raise IntakeError("The bound Decision is missing a canonical section.", "INTAKE_RECOVERY_REQUIRED", 409)
    section = (
        "## Option revision history\n\n"
        "| From revision | To revision | Actor | Timestamp | Reason |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"{row}\n\n"
    )
    return text[: marker.start()] + section + text[marker.start() :]


def _revise_open_decision_options(
    payload: bytes,
    question: Mapping[str, Any],
    actor: str,
    timestamp: str,
    reason: str,
) -> bytes:
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise IntakeError("The bound Decision is not valid UTF-8.", "INTAKE_RECOVERY_REQUIRED", 409) from exc
    if _metadata_value(text, "Status").upper() != "OPEN":
        raise IntakeError("Only an open current Decision may have its options revised.", "INTAKE_REVISION_CONFLICT", 409)
    raw_revision = _metadata_value(text, "Revision")
    if not raw_revision.isdigit() or int(raw_revision) < 1:
        raise IntakeError("The bound Decision revision is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
    old_revision = int(raw_revision)
    new_revision = old_revision + 1
    text = _replace_metadata_value(text, "Revision", str(new_revision))
    text = _replace_decision_section(text, "Options", _options_table(question))
    text = _append_option_revision_history(text, old_revision, new_revision, actor, timestamp, reason)
    return text.encode("utf-8")


def _resolve_open_decision(
    payload: bytes,
    question: Mapping[str, Any],
    choice: str,
    evidence_id: str,
    actor: str,
    timestamp: str,
) -> bytes:
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise IntakeError("The bound Decision is not valid UTF-8.", "INTAKE_RECOVERY_REQUIRED", 409) from exc
    if _metadata_value(text, "Status").upper() != "OPEN":
        raise IntakeError("The bound Decision is no longer open.", "INTAKE_REVISION_CONFLICT", 409)
    selected_label = next(
        (item["label"] for item in question.get("options", []) if item.get("id") == choice),
        choice,
    )
    text = _replace_metadata_value(text, "Status", "RESOLVED")
    text = _replace_metadata_value(text, "Informs", evidence_id)
    text = _replace_metadata_value(text, "Evidence", evidence_id)
    text = _replace_decision_section(
        text,
        "Resolution",
        f"The human authority explicitly selected {selected_label} ({choice}) for this intake choice.",
    )
    history_pattern = re.compile(
        r"(^##\s+Append-only transition history\s*$\n)(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    match = history_pattern.search(text)
    if match is None:
        raise IntakeError("The bound Decision is missing transition history.", "INTAKE_RECOVERY_REQUIRED", 409)
    row = (
        f"| OPEN | RESOLVED | {_table_cell(actor)} | {timestamp} | "
        f"Explicit intake option selected | {evidence_id} |"
    )
    body = match.group(2).strip() + "\n" + row
    text = text[: match.start(2)] + "\n" + body + "\n" + text[match.end(2) :]
    return text.encode("utf-8")


def _render_evidence(
    evidence_id: str,
    decision_id: str,
    question: Mapping[str, Any],
    choice: str,
    actor: str,
    source: str,
    timestamp: str,
    effort_id: str,
    subject_revision: int,
) -> bytes:
    option = next(item for item in question["options"] if item["id"] == choice)
    digest = hashlib.sha256(f"{effort_id}\n{decision_id}\n{choice}\n{actor}\n{timestamp}".encode("utf-8")).hexdigest()
    content = f"""# {evidence_id}: Explicit human selection for {decision_id}

- **Kind:** EVIDENCE
- **Method:** OBSERVATION
- **Observed at:** {timestamp}
- **Subject / revision:** {effort_id} / {subject_revision}
- **Source:** Local Wayfinder intake via {source}
- **Source type:** LOCAL-OBSERVATION
- **Collector:** {actor}
- **Basis:** OBSERVED
- **Confidence:** HIGH
- **Sensitivity:** INTERNAL
- **Content hash:** {digest}
- **Revalidate when:** The selected choice, destination framing, or named decision authority changes materially.
- **Assumptions affected:** none
- **Decisions affected:** {decision_id}
- **Gates affected:** none
- **Invariants affected:** none

## Conclusion

{actor} explicitly selected {_table_cell(option['label'])} ({choice}) for {decision_id}.

## Evidence

The validated local intake recorded the allowed option ID, actor, source, timestamp, effort identity, and destination revision.

## Confidence and limitations

This receipt proves the recorded human choice; it does not prove implementation feasibility or authorize execution.

## Observed facts versus inference

Observed: the named human actor selected the allowed option. Inference: none.

## What could change the conclusion

A later explicit choice by the authorized human, or a material change to the destination or authority boundary.
"""
    return content.encode("utf-8")


def _manifest_add_decision(manifest: dict[str, Any], decision_id: str, blocking: bool) -> None:
    decisions = manifest.setdefault("decisions", [])
    if not isinstance(decisions, list):
        raise IntakeError("EFFORT.json Decision index is invalid.", "INTAKE_NOT_READY", 409)
    decisions.append(
        {
            "id": decision_id,
            "path": f"decisions/{decision_id}.md",
            "status": "OPEN",
            "phase_id": "p2-resolve",
            "destination_blocking": blocking,
        }
    )


def _manifest_resolve_choice(
    manifest: dict[str, Any], decision_id: str, evidence_id: str, subject_revision: int, timestamp: str
) -> None:
    decisions = manifest.get("decisions")
    if not isinstance(decisions, list):
        raise IntakeError("EFFORT.json Decision index is invalid.", "INTAKE_NOT_READY", 409)
    matches = [item for item in decisions if isinstance(item, dict) and item.get("id") == decision_id]
    if len(matches) != 1 or matches[0].get("status") != "OPEN":
        raise IntakeError("The bound Decision no longer matches the intake choice.", "INTAKE_REVISION_CONFLICT", 409)
    matches[0]["status"] = "RESOLVED"
    evidence = manifest.setdefault("evidence", [])
    edges = manifest.setdefault("edges", [])
    if not isinstance(evidence, list) or not isinstance(edges, list):
        raise IntakeError("EFFORT.json evidence or edge index is invalid.", "INTAKE_NOT_READY", 409)
    evidence.append({"id": evidence_id, "path": f"evidence/{evidence_id}.md", "subject_revision": subject_revision})
    edges.append({"from": evidence_id, "type": "informs", "to": decision_id})
    effort_meta = manifest.get("effort")
    if not isinstance(effort_meta, dict):
        raise IntakeError("EFFORT.json effort metadata is invalid.", "INTAKE_NOT_READY", 409)
    effort_meta["updated_at"] = timestamp


def _manifest_add_activity(manifest: dict[str, Any], activity_id: str, node_id: str, message: str, actor: str, timestamp: str) -> None:
    activity = manifest.setdefault("activity", [])
    if not isinstance(activity, list) or len(activity) >= MAX_PUBLIC_ITEMS:
        raise IntakeError("EFFORT.json activity cannot safely accept another intake receipt.", "INTAKE_NOT_READY", 409)
    activity.append(
        {"id": activity_id, "type": "update", "timestamp": timestamp, "node_id": node_id, "message": message, "actor": actor}
    )


def _initial_intake(effort_id: str, intent: str, timestamp: str) -> dict[str, Any]:
    classification = classify_intent(intent)
    snapshot = _question_snapshot("Q-001", {})
    return {
        "schema_version": INTAKE_SCHEMA_VERSION,
        "intake_id": INTAKE_ID,
        "effort_id": effort_id,
        "revision": 1,
        "flow_version": FLOW_VERSION,
        "status": "AWAITING_HUMAN_CHOICE",
        "intent": _bounded_text(intent, "Intent", 2_000),
        "domain": classification,
        "question_order": ["Q-001"],
        "question_snapshots": {"Q-001": snapshot},
        "answers": [],
        "current_question_id": "Q-001",
        "decision_bindings": [],
        "comparisons": [],
        "comparison_history": [],
        "fact_revalidations": [],
        "receipts": [],
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _append_receipt(
    intake: dict[str, Any],
    *,
    kind: str,
    question_id: str,
    actor: str,
    source: str,
    timestamp: str,
    decision_id: str | None = None,
    evidence_id: str | None = None,
    choice: str | None = None,
    option: Mapping[str, str] | None = None,
    old_comparison_revision: int | None = None,
    new_comparison_revision: int | None = None,
    fact_revalidation_id: str | None = None,
    supersedes_receipt_id: str | None = None,
    old_readiness: str | None = None,
    new_readiness: str | None = None,
    comparison_id: str | None = None,
    prior_decision_id: str | None = None,
    revalidation_decision_id: str | None = None,
    revalidation_question_id: str | None = None,
) -> None:
    old_revision = intake["revision"]
    receipt: dict[str, Any] = {
        "receipt_id": f"IR-{len(intake['receipts']) + 1:04d}",
        "kind": kind,
        "question_id": question_id,
        "old_revision": old_revision,
        "new_revision": old_revision + 1,
        "actor": actor,
        "source": source,
        "recorded_at": timestamp,
    }
    if kind == "CHOICE":
        receipt.update({"decision_id": decision_id, "evidence_id": evidence_id, "choice": choice})
    elif kind == "OPTION_PROPOSAL":
        receipt.update({"decision_id": decision_id, "option": dict(option or {})})
    elif kind == "TECH_OPTIONS_REVISED":
        receipt.update(
            {
                "decision_id": decision_id,
                "old_comparison_revision": old_comparison_revision,
                "new_comparison_revision": new_comparison_revision,
            }
        )
    elif kind == "FACT_REVALIDATED":
        receipt.update(
            {
                "fact_revalidation_id": fact_revalidation_id,
                "supersedes_receipt_id": supersedes_receipt_id,
                "old_readiness": old_readiness,
                "new_readiness": new_readiness,
                "comparison_id": comparison_id,
                "prior_decision_id": prior_decision_id,
                "revalidation_decision_id": revalidation_decision_id,
                "revalidation_question_id": revalidation_question_id,
            }
        )
    intake["receipts"].append(receipt)


def _framing_complete(intake: Mapping[str, Any]) -> bool:
    return any(item.get("question_id") == "Q-007" for item in intake.get("answers", []) if isinstance(item, Mapping))


def _next_snapshot(intake: dict[str, Any]) -> dict[str, Any] | None:
    answered = len(intake["answers"])
    if answered >= len(intake["question_order"]):
        intake["current_question_id"] = None
        intake["status"] = "COMPLETE"
        return None
    question_id = intake["question_order"][answered]
    snapshot = intake["question_snapshots"].get(question_id)
    if snapshot is None:
        snapshot = _question_snapshot(question_id, intake)
        intake["question_snapshots"][question_id] = snapshot
    intake["current_question_id"] = question_id
    intake["status"] = "AWAITING_HUMAN_CHOICE" if snapshot["answer_type"] == "choice" else "IN_PROGRESS"
    if question_id in COMPARISON_QUESTION.values():
        comparison_domain = next(domain for domain, final_id in COMPARISON_QUESTION.items() if final_id == question_id)
        comparison = _comparison_for(intake, comparison_domain)
        intake["comparisons"] = [item for item in intake["comparisons"] if item.get("id") != comparison["id"]]
        intake["comparisons"].append(comparison)
    return snapshot


def _materialize_choice(
    intake: dict[str, Any], manifest: dict[str, Any], effort_dir: Path, question: Mapping[str, Any], authority: str, timestamp: str
) -> tuple[Path, bytes]:
    decision_id = _next_id(manifest, effort_dir, "decision")
    _manifest_add_decision(manifest, decision_id, question.get("destination_blocking") is True)
    revalidates = question.get("revalidates")
    if isinstance(revalidates, str) and DECISION_ID.fullmatch(revalidates):
        edges = manifest.setdefault("edges", [])
        if not isinstance(edges, list):
            raise IntakeError("EFFORT.json typed-edge index is invalid.", "INTAKE_NOT_READY", 409)
        edges.append({"from": decision_id, "type": "revalidates", "to": revalidates})
    intake["decision_bindings"].append(
        {"question_id": question["id"], "decision_id": decision_id, "status": "OPEN", "selected_option": None, "evidence_id": None}
    )
    domain = intake.get("domain")
    if isinstance(domain, dict):
        for workstream in domain.get("secondary_workstreams", []):
            if isinstance(workstream, dict) and question["id"] in workstream.get("required_questions", []):
                workstream.setdefault("decision_ids", []).append(decision_id)
    path = effort_dir / "decisions" / f"{decision_id}.md"
    return path, _render_decision(decision_id, question, authority, timestamp)


def _replace_section(text: str, heading: str, body: str) -> str:
    pattern = re.compile(rf"(^##\s+{re.escape(heading)}\s*$\n)(.*?)(?=^##\s+|\Z)", re.MULTILINE | re.DOTALL | re.IGNORECASE)
    match = pattern.search(text)
    if not match:
        raise IntakeError("MAP.md is missing a required framing section.", "INTAKE_NOT_READY", 409)
    return text[: match.start(2)] + "\n" + body.strip() + "\n\n" + text[match.end(2) :]


def _finalize_framing(map_text: str, intake: Mapping[str, Any], manifest: dict[str, Any], timestamp: str) -> str:
    answers = _answers_by_key(intake)
    required = ("desired_outcome", "success_condition", "success_evidence", "constraints", "out_of_scope", "decision_authority")
    if any(key not in answers for key in required):
        raise IntakeError("Destination framing is incomplete.", "INTAKE_VALIDATION", 422)
    effort_meta = manifest.get("effort")
    if not isinstance(effort_meta, dict):
        raise IntakeError("EFFORT.json effort metadata is invalid.", "INTAKE_NOT_READY", 409)
    revision = effort_meta.get("destination_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise IntakeError("EFFORT.json destination revision is invalid.", "INTAKE_NOT_READY", 409)
    revision += 1
    effort_meta["destination"] = answers["desired_outcome"]
    effort_meta["destination_revision"] = revision
    effort_meta["updated_at"] = timestamp
    manifest["current_phase_id"] = "p2-resolve"
    state_module = _load_state_module()
    phase_ids = [item["id"] for item in state_module.PHASES]
    for index, item in enumerate(manifest.get("phases", [])):
        if not isinstance(item, dict) or index >= len(phase_ids):
            raise IntakeError("EFFORT.json phase schema is invalid.", "INTAKE_NOT_READY", 409)
        item["state"] = "complete" if index == 0 else ("active" if index == 1 else "upcoming")
        contract = state_module.PHASES[index]
        item["label"] = contract["label"]
        item["description"] = contract["description"]
    checkpoint_statuses = ["COMPLETE", "DUE", "UPCOMING", "UPCOMING", "UPCOMING"]
    for index, item in enumerate(manifest.get("checkpoints", [])):
        if not isinstance(item, dict) or index >= len(checkpoint_statuses):
            raise IntakeError("EFFORT.json checkpoint schema is invalid.", "INTAKE_NOT_READY", 409)
        item["status"] = checkpoint_statuses[index]
        item["completed_at"] = timestamp if index == 0 else None
        contract = state_module.PHASES[index]["checkpoint"]
        item.update(
            {
                "phase_id": state_module.PHASES[index]["id"],
                "label": contract["label"],
                "due_when": contract["due_when"],
                "run_recommended": contract["recommended_run"],
                "reason": contract["reason"],
            }
        )
    for index, item in enumerate(manifest.get("milestones", [])):
        if not isinstance(item, dict):
            raise IntakeError("EFFORT.json milestone schema is invalid.", "INTAKE_NOT_READY", 409)
        item["status"] = "COMPLETE" if index == 0 else "PENDING"
    destination = _table_cell(answers["desired_outcome"])
    condition = _table_cell(answers["success_condition"])
    evidence = _table_cell(answers["success_evidence"])
    constraint = _table_cell(answers["constraints"])
    out_of_scope = _table_cell(answers["out_of_scope"])
    updated = re.sub(r"^- \*\*Current phase:\*\*.*$", "- **Current phase:** p2-resolve — Resolve route", map_text, flags=re.MULTILINE)
    updated = re.sub(r"^- \*\*Destination revision:\*\*.*$", f"- **Destination revision:** {revision}", updated, flags=re.MULTILINE)
    updated = re.sub(r"^- \*\*Last validated:\*\*.*$", f"- **Last validated:** {timestamp}", updated, flags=re.MULTILINE)
    updated = _replace_section(updated, "Destination", destination)
    updated = _replace_section(
        updated,
        "Success conditions",
        f"| ID | Observable condition | Evidence required | Status |\n| --- | --- | --- | --- |\n| SC-001 | {condition} | {evidence} | OPEN |",
    )
    updated = _replace_section(
        updated,
        "Constraints and authority boundaries",
        f"- {constraint}\n- Material route choices require approval by {_table_cell(answers['decision_authority'])}.\n- Planning artifacts do not authorize implementation, deployment, publication, spending, deletion, messaging, or external writes.",
    )
    updated = _replace_section(updated, "Explicit out of scope", f"- {out_of_scope}")
    updated = _replace_section(updated, "Fog / not yet formulated", "No unformulated fog remains.")
    return updated


def _validate_committed(state_module: Any, project_root: Path, effort_dir: Path, *, framing_complete: bool) -> None:
    manifest = _parse_json(
        _read_regular(effort_dir / "EFFORT.json", "EFFORT.json", 4 * 1024 * 1024),
        "EFFORT.json",
    )
    _load_intake(effort_dir, manifest)
    state = state_module.build_state(project_root, effort_dir)
    _require_intake_ready(state, framing_complete=framing_complete, transaction_visible=True)


def start_intake(root: Path, effort: str | Path | None, intent: str) -> dict[str, Any]:
    """Start a resumable intake and materialize only the first explicit choice."""
    state_module, project_root, effort_dir, manifest = _safe_context(root, effort)
    with _intake_lock(effort_dir):
        _recover_transaction(project_root, effort_dir)
        if (effort_dir / INTAKE_FILENAME).exists() or (effort_dir / INTAKE_FILENAME).is_symlink():
            raise IntakeError("Intake already exists; resume it instead of starting over.", "INTAKE_REVISION_CONFLICT", 409)
        state = state_module.build_state(project_root, effort_dir)
        _require_intake_ready(state, framing_complete=False)
        timestamp = _now()
        next_manifest = deepcopy(manifest)
        intake = _initial_intake(effort_dir.name, intent, timestamp)
        question = intake["question_snapshots"]["Q-001"]
        decision_path, decision_payload = _materialize_choice(
            intake, next_manifest, effort_dir, question, "Human decision authority", timestamp
        )
        next_manifest["effort"]["updated_at"] = timestamp
        decision_id = intake["decision_bindings"][0]["decision_id"]
        _manifest_add_activity(next_manifest, f"INTAKE-{intake['revision']:04d}", decision_id, "Intake started; explicit domain confirmation is awaiting a human choice.", "Wayfinder intake", timestamp)
        updates = {
            effort_dir / INTAKE_FILENAME: _json_bytes(intake),
            effort_dir / "EFFORT.json": _json_bytes(next_manifest),
            decision_path: decision_payload,
        }
        _transactional_write(
            project_root,
            effort_dir,
            updates,
            lambda: _validate_committed(state_module, project_root, effort_dir, framing_complete=False),
        )
    return state_module.build_state(project_root, effort_dir)


def _advance_after_answer(
    intake: dict[str, Any], manifest: dict[str, Any], effort_dir: Path, authority: str, timestamp: str
) -> tuple[Path, bytes] | None:
    snapshot = _next_snapshot(intake)
    if snapshot is None or snapshot["answer_type"] != "choice":
        return None
    return _materialize_choice(intake, manifest, effort_dir, snapshot, authority, timestamp)


def _validate_evidence_pointer(project_root: Path, manifest: Mapping[str, Any], value: str) -> str:
    pointer = _bounded_text(value, "Evidence pointer", 1_000)
    if EVIDENCE_ID.fullmatch(pointer):
        indexed = {
            item.get("id")
            for item in manifest.get("evidence", [])
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        } if isinstance(manifest.get("evidence"), list) else set()
        if pointer not in indexed:
            raise IntakeError("Evidence pointer must name indexed evidence.", "INTAKE_VALIDATION", 422)
        return pointer
    parsed = urlsplit(pointer)
    if parsed.scheme:
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise IntakeError("Evidence URL must be credential-free HTTPS without query or fragment.", "INTAKE_VALIDATION", 422)
        return pointer
    relative = Path(pointer)
    if relative.is_absolute() or "\\" in pointer or any(part in {"", ".", ".."} for part in relative.parts):
        raise IntakeError("Evidence file pointer must be a safe project-relative path.", "INTAKE_VALIDATION", 422)
    target = project_root / relative
    if target.is_symlink() or not _within(project_root, target):
        raise IntakeError("Evidence file pointer must remain inside the project folder.", "INTAKE_VALIDATION", 422)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(target, flags)
    except OSError as exc:
        raise IntakeError("Evidence file pointer must name a readable regular project file.", "INTAKE_VALIDATION", 422) from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise IntakeError("Evidence file pointer must name a regular project file.", "INTAKE_VALIDATION", 422)
    finally:
        os.close(descriptor)
    return relative.as_posix()


def _record_intake_answer(
    root: Path,
    effort: str | Path | None,
    question_id: str,
    expected_revision: int,
    actor: str,
    source: str,
    answer: str,
    *,
    evidence_mode: bool,
) -> dict[str, Any]:
    actor = _bounded_text(actor, "Evidence actor", 120) if evidence_mode else _human_actor(actor)
    source = _source(source)
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 1:
        raise IntakeError("Expected revision must be a positive integer.", "INTAKE_VALIDATION", 422)
    if not isinstance(question_id, str) or not QUESTION_ID.fullmatch(question_id):
        raise IntakeError("Question ID is invalid.", "INTAKE_VALIDATION", 422)
    state_module, project_root, effort_dir, manifest = _safe_context(root, effort)
    with _intake_lock(effort_dir):
        _recover_transaction(project_root, effort_dir)
        intake = _load_intake(effort_dir, manifest)
        if intake["revision"] != expected_revision:
            raise IntakeError("Intake revision changed; refresh and retry.", "INTAKE_REVISION_CONFLICT", 409)
        if intake["current_question_id"] != question_id:
            raise IntakeError("Only the current questionnaire item may be answered.", "INTAKE_REVISION_CONFLICT", 409)
        question = intake["question_snapshots"][question_id]
        if question["answer_type"] not in {"text", "fact"}:
            raise IntakeError("The current item requires an allowed option selection.", "INTAKE_VALIDATION", 422)
        if evidence_mode and question["answer_type"] != "fact":
            raise IntakeError("Evidence satisfaction is allowed only for a readiness-fact question.", "INTAKE_VALIDATION", 422)
        normalized_answer = _bounded_text(answer, "Answer", question["max_length"])
        if question["answer_type"] == "fact":
            fact = _validate_fact_answer(normalized_answer, question["max_length"])
            if (
                question_id in REGULATORY_REQUIRED_FACTS
                and _answers_by_key(intake).get("reporting_need") == "REGULATORY"
                and fact["readiness"] == "NOT_APPLICABLE"
            ):
                raise IntakeError(
                    "Regulatory reporting requires an explicit jurisdiction, reporting basis, and qualified sign-off authority.",
                    "INTAKE_REGULATORY_REQUIREMENT",
                    422,
                )
            if evidence_mode:
                if fact["readiness"] != "ESTABLISHED":
                    raise IntakeError("Evidence satisfaction must establish a fact with a cited pointer.", "INTAKE_VALIDATION", 422)
                _validate_evidence_pointer(project_root, manifest, fact["support"])
        state = state_module.build_state(project_root, effort_dir)
        _require_intake_ready(state, framing_complete=_framing_complete(intake))
        timestamp = _now()
        next_intake = deepcopy(intake)
        next_manifest = deepcopy(manifest)
        next_intake["answers"].append(
            {"question_id": question_id, "value": normalized_answer, "actor": actor, "source": source, "answered_at": timestamp}
        )
        _append_receipt(
            next_intake,
            kind="EVIDENCE" if evidence_mode else ("FACT" if question["answer_type"] == "fact" else "TEXT"),
            question_id=question_id,
            actor=actor,
            source=source,
            timestamp=timestamp,
        )
        next_intake["revision"] += 1
        next_intake["updated_at"] = timestamp
        updates: dict[Path, bytes] = {}
        if question_id == "Q-007":
            map_path = effort_dir / "MAP.md"
            map_text = _read_regular(map_path, "MAP.md", 4 * 1024 * 1024).decode("utf-8")
            framed_map = _finalize_framing(map_text, next_intake, next_manifest, timestamp)
            updates[map_path] = framed_map.encode("utf-8")
        created = _advance_after_answer(next_intake, next_manifest, effort_dir, actor, timestamp)
        if created:
            updates[created[0]] = created[1]
        next_manifest["effort"]["updated_at"] = timestamp
        _manifest_add_activity(next_manifest, f"INTAKE-{next_intake['revision']:04d}", "", "A validated intake framing answer was recorded.", actor, timestamp)
        updates[effort_dir / INTAKE_FILENAME] = _json_bytes(next_intake)
        updates[effort_dir / "EFFORT.json"] = _json_bytes(next_manifest)
        complete_framing = _framing_complete(next_intake)
        _transactional_write(
            project_root,
            effort_dir,
            updates,
            lambda: _validate_committed(state_module, project_root, effort_dir, framing_complete=complete_framing),
        )
    return state_module.build_state(project_root, effort_dir)


def record_intake_answer(
    root: Path,
    effort: str | Path | None,
    question_id: str,
    expected_revision: int,
    actor: str,
    source: str,
    answer: str,
) -> dict[str, Any]:
    """Record the current human text/fact answer under revision-CAS."""
    return _record_intake_answer(
        root, effort, question_id, expected_revision, actor, source, answer, evidence_mode=False
    )


def record_intake_evidence_answer(
    root: Path,
    effort: str | Path | None,
    question_id: str,
    expected_revision: int,
    actor: str,
    source: str,
    fact: str,
    evidence_pointer: str,
) -> dict[str, Any]:
    """Establish only the current fact from cited evidence; never records a human choice."""
    fact_text = _bounded_text(fact, "Established fact", 1_000)
    pointer = _bounded_text(evidence_pointer, "Evidence pointer", 1_000)
    answer = f"ESTABLISHED: {fact_text}; EVIDENCE: {pointer}"
    return _record_intake_answer(
        root, effort, question_id, expected_revision, actor, source, answer, evidence_mode=True
    )


def revalidate_intake_fact(
    root: Path,
    effort: str | Path | None,
    question_id: str,
    expected_revision: int,
    actor: str,
    source: str,
    answer: str,
    evidence_pointer: str | None = None,
) -> dict[str, Any]:
    """Replace only a prior UNKNOWN fact and require a new explicit route choice."""
    evidence_mode = evidence_pointer is not None
    actor = _bounded_text(actor, "Evidence actor", 120) if evidence_mode else _human_actor(actor)
    source = _source(source)
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 1:
        raise IntakeError("Expected revision must be a positive integer.", "INTAKE_VALIDATION", 422)
    if not isinstance(question_id, str) or not QUESTION_ID.fullmatch(question_id):
        raise IntakeError("Question ID is invalid.", "INTAKE_VALIDATION", 422)
    if evidence_mode:
        fact_text = _bounded_text(answer, "Established fact", 1_000)
        pointer = _bounded_text(evidence_pointer, "Evidence pointer", 1_000)
        normalized_answer = f"ESTABLISHED: {fact_text}; EVIDENCE: {pointer}"
    else:
        normalized_answer = _bounded_text(answer, "Revalidated fact", MAX_TEXT_ANSWER)
    parsed_fact = _validate_fact_answer(normalized_answer)
    if not evidence_mode and re.match(
        r"^\s*(?:unknown|not\s+known|unconfirmed|pending|tbd|tbc)\b",
        normalized_answer,
        flags=re.IGNORECASE,
    ):
        raise IntakeError("A human fact revalidation must state the now-known fact.", "INTAKE_VALIDATION", 422)
    if parsed_fact["readiness"] != ("ESTABLISHED" if evidence_mode else "HUMAN_ANSWERED"):
        raise IntakeError(
            "Fact revalidation must be either a normal human answer or an evidence-established fact.",
            "INTAKE_VALIDATION",
            422,
        )

    state_module, project_root, effort_dir, manifest = _safe_context(root, effort)
    with _intake_lock(effort_dir):
        _recover_transaction(project_root, effort_dir)
        intake = _load_intake(effort_dir, manifest)
        if intake["revision"] != expected_revision:
            raise IntakeError("Intake revision changed; refresh and retry.", "INTAKE_REVISION_CONFLICT", 409)
        if intake["status"] != "COMPLETE" or intake["current_question_id"] is not None:
            raise IntakeError(
                "Resolve the current intake question before revalidating an earlier UNKNOWN fact.",
                "INTAKE_NOT_READY",
                409,
            )
        if (
            len(intake["question_order"]) >= MAX_QUESTIONS
            or len(intake.get("fact_revalidations", [])) >= MAX_FACT_REVALIDATIONS
            or len(intake.get("receipts", [])) >= MAX_RECEIPTS
        ):
            raise IntakeError("The bounded fact-revalidation history is full.", "INTAKE_NOT_READY", 409)
        question = intake["question_snapshots"].get(question_id)
        previous_answer = next((item for item in intake["answers"] if item["question_id"] == question_id), None)
        if (
            not isinstance(question, Mapping)
            or question.get("answer_type") != "fact"
            or not isinstance(previous_answer, Mapping)
            or previous_answer.get("readiness") != "UNKNOWN"
            or any(item.get("question_id") == question_id for item in intake.get("fact_revalidations", []))
        ):
            raise IntakeError(
                "Only an unrevalidated original UNKNOWN readiness fact may use this workflow.",
                "INTAKE_NOT_READY",
                409,
            )
        fact_key = question["key"]
        domain_id = FACT_COMPARISON_DOMAIN.get(fact_key)
        if domain_id == "SOFTWARE":
            raise IntakeError(
                "Software fact changes require a grounded named-technology refresh before route revalidation.",
                "INTAKE_NOT_READY",
                409,
            )
        if domain_id not in {"GENERAL_PROJECT", "FINANCE_REPORTING"}:
            raise IntakeError("This readiness fact has no supported route comparison.", "INTAKE_NOT_READY", 409)
        comparison_id = _comparison_id_for_domain(domain_id)
        active_comparison = next(
            (item for item in intake["comparisons"] if item.get("id") == comparison_id),
            None,
        )
        previous_revalidation = next(
            (
                item for item in reversed(intake.get("fact_revalidations", []))
                if item.get("comparison_id") == comparison_id
            ),
            None,
        )
        prior_question_id = (
            previous_revalidation.get("revalidation_question_id")
            if isinstance(previous_revalidation, Mapping)
            else _comparison_question_for_domain(domain_id)
        )
        prior_binding = next(
            (
                item for item in intake["decision_bindings"]
                if item["question_id"] == prior_question_id and item["status"] == "RESOLVED"
            ),
            None,
        )
        if (
            not isinstance(active_comparison, Mapping)
            or not isinstance(active_comparison.get("selected_option"), str)
            or prior_binding is None
        ):
            raise IntakeError(
                "A completed explicit route choice is required before its UNKNOWN premise can be revalidated.",
                "INTAKE_NOT_READY",
                409,
            )
        if evidence_mode:
            _validate_evidence_pointer(project_root, manifest, parsed_fact["support"])
        state = state_module.build_state(project_root, effort_dir)
        _require_intake_ready(state, framing_complete=True)

        prior_receipt = next(
            (
                item for item in intake["receipts"]
                if item["question_id"] == question_id and item["kind"] in {"FACT", "EVIDENCE"}
            ),
            None,
        )
        if prior_receipt is None:
            raise IntakeError("The original UNKNOWN fact receipt is unavailable.", "INTAKE_RECOVERY_REQUIRED", 409)
        timestamp = _now()
        next_intake = deepcopy(intake)
        next_manifest = deepcopy(manifest)
        revalidation_number = len(next_intake["fact_revalidations"]) + 1
        revalidation_id = f"FRV-{revalidation_number:04d}"
        revalidation_question_id = f"Q-RV-{revalidation_number:03d}"
        if revalidation_question_id in next_intake["question_order"] or revalidation_question_id in next_intake["question_snapshots"]:
            raise IntakeError("The next fact-revalidation question ID is already occupied.", "INTAKE_RECOVERY_REQUIRED", 409)
        receipt_id = f"IR-{len(next_intake['receipts']) + 1:04d}"
        replacement = {
            "value": normalized_answer,
            **parsed_fact,
            "actor": actor,
            "source": source,
            "recorded_at": timestamp,
        }
        entry: dict[str, Any] = {
            "id": revalidation_id,
            "question_id": question_id,
            "supersedes_receipt_id": prior_receipt["receipt_id"],
            "receipt_id": receipt_id,
            "previous_answer": deepcopy(previous_answer),
            "replacement": replacement,
            "comparison_id": comparison_id,
            "prior_comparison": deepcopy(active_comparison),
            "prior_decision_id": prior_binding["decision_id"],
            "revalidation_question_id": revalidation_question_id,
            "revalidation_decision_id": "D-000",
        }
        next_intake["fact_revalidations"].append(entry)
        recomputed = _comparison_for(next_intake, domain_id)
        recomputed["selected_option"] = None
        next_intake["comparisons"] = [
            recomputed if item.get("id") == comparison_id else item
            for item in next_intake["comparisons"]
        ]
        revalidation_question = _revalidation_question(
            revalidation_question_id,
            question_id,
            fact_key.replace("_", " "),
            recomputed,
            prior_binding["decision_id"],
        )
        next_intake["question_order"].append(revalidation_question_id)
        next_intake["question_snapshots"][revalidation_question_id] = revalidation_question
        for workstream in next_intake["domain"].get("secondary_workstreams", []):
            if question_id in workstream.get("required_questions", []):
                workstream["required_questions"].append(revalidation_question_id)
        next_intake["current_question_id"] = revalidation_question_id
        next_intake["status"] = "AWAITING_HUMAN_CHOICE"
        decision_path, decision_payload = _materialize_choice(
            next_intake,
            next_manifest,
            effort_dir,
            revalidation_question,
            _human_actor(_answers_by_key(next_intake).get("decision_authority")),
            timestamp,
        )
        revalidation_decision_id = next_intake["decision_bindings"][-1]["decision_id"]
        entry["revalidation_decision_id"] = revalidation_decision_id
        _append_receipt(
            next_intake,
            kind="FACT_REVALIDATED",
            question_id=question_id,
            actor=actor,
            source=source,
            timestamp=timestamp,
            fact_revalidation_id=revalidation_id,
            supersedes_receipt_id=prior_receipt["receipt_id"],
            old_readiness="UNKNOWN",
            new_readiness=parsed_fact["readiness"],
            comparison_id=comparison_id,
            prior_decision_id=prior_binding["decision_id"],
            revalidation_decision_id=revalidation_decision_id,
            revalidation_question_id=revalidation_question_id,
        )
        next_intake["revision"] += 1
        next_intake["updated_at"] = timestamp
        next_manifest["effort"]["updated_at"] = timestamp
        _manifest_add_activity(
            next_manifest,
            f"INTAKE-{next_intake['revision']:04d}",
            revalidation_decision_id,
            "An owned UNKNOWN fact was replaced append-only; its dependent route awaits explicit human revalidation.",
            actor,
            timestamp,
        )
        updates = {
            effort_dir / INTAKE_FILENAME: _json_bytes(next_intake),
            effort_dir / "EFFORT.json": _json_bytes(next_manifest),
            decision_path: decision_payload,
        }
        _transactional_write(
            project_root,
            effort_dir,
            updates,
            lambda: _validate_committed(state_module, project_root, effort_dir, framing_complete=True),
        )
    return state_module.build_state(project_root, effort_dir)


def _normalize_proposed_option(option: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(option, Mapping) or set(option) != {"id", "label", "description"}:
        raise IntakeError(
            "A proposed option must contain exactly id, label, and description.",
            "INTAKE_INVALID_OPTION",
            422,
        )
    option_id = option.get("id")
    if not isinstance(option_id, str) or not OPTION_ID.fullmatch(option_id):
        raise IntakeError("Proposed option ID is invalid.", "INTAKE_INVALID_OPTION", 422)
    return {
        "id": option_id,
        "label": _bounded_text(option.get("label"), "Option label", 200, allow_ambiguous=True),
        "description": _bounded_text(
            option.get("description"),
            "Option description",
            1_000,
            allow_ambiguous=True,
        ),
    }


def propose_intake_alternative(
    root: Path,
    effort: str | Path | None,
    decision_id: str,
    expected_revision: int,
    actor: str,
    source: str,
    option: Mapping[str, Any],
) -> dict[str, Any]:
    """Append one human-proposed option to the current open Decision without selecting it."""
    actor = _human_actor(actor)
    source = _source(source)
    normalized_option = _normalize_proposed_option(option)
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 1:
        raise IntakeError("Expected revision must be a positive integer.", "INTAKE_VALIDATION", 422)
    if not isinstance(decision_id, str) or not DECISION_ID.fullmatch(decision_id):
        raise IntakeError("Decision ID is invalid.", "INTAKE_VALIDATION", 422)
    state_module, project_root, effort_dir, manifest = _safe_context(root, effort)
    with _intake_lock(effort_dir):
        _recover_transaction(project_root, effort_dir)
        intake = _load_intake(effort_dir, manifest)
        if intake["revision"] != expected_revision:
            raise IntakeError("Intake revision changed; refresh and retry.", "INTAKE_REVISION_CONFLICT", 409)
        current_id = intake.get("current_question_id")
        question = intake.get("question_snapshots", {}).get(current_id)
        binding = next(
            (
                item
                for item in intake.get("decision_bindings", [])
                if item.get("question_id") == current_id and item.get("status") == "OPEN"
            ),
            None,
        )
        if (
            not isinstance(current_id, str)
            or not isinstance(question, Mapping)
            or question.get("answer_type") != "choice"
            or binding is None
            or binding.get("decision_id") != decision_id
        ):
            raise IntakeError(
                "Decision does not identify the current open intake choice.",
                "INTAKE_REVISION_CONFLICT",
                409,
            )
        if current_id in COMPARISON_QUESTION.values() or current_id == "Q-SW-012":
            raise IntakeError(
                "Route-comparison alternatives require a complete grounded comparison revision.",
                "INTAKE_NOT_READY",
                409,
            )
        options = question.get("options")
        if not isinstance(options, list) or len(options) >= 32:
            raise IntakeError("The current choice cannot accept another bounded option.", "INTAKE_NOT_READY", 409)
        if any(item.get("id") == normalized_option["id"] for item in options if isinstance(item, Mapping)):
            raise IntakeError("That option ID already exists.", "INTAKE_INVALID_OPTION", 422)
        state = state_module.build_state(project_root, effort_dir)
        _require_intake_ready(state, framing_complete=_framing_complete(intake))
        timestamp = _now()
        next_intake = deepcopy(intake)
        next_manifest = deepcopy(manifest)
        next_question = next_intake["question_snapshots"][current_id]
        next_question["options"].append(deepcopy(normalized_option))
        _append_receipt(
            next_intake,
            kind="OPTION_PROPOSAL",
            question_id=current_id,
            actor=actor,
            source=source,
            timestamp=timestamp,
            decision_id=decision_id,
            option=normalized_option,
        )
        next_intake["revision"] += 1
        next_intake["updated_at"] = timestamp
        next_manifest["effort"]["updated_at"] = timestamp
        _manifest_add_activity(
            next_manifest,
            f"INTAKE-{next_intake['revision']:04d}",
            decision_id,
            "A human-proposed intake option was recorded; no option was selected.",
            actor,
            timestamp,
        )
        decision_path = effort_dir / "decisions" / f"{decision_id}.md"
        decision_payload = _read_regular(decision_path, "Bound intake Decision", 4 * 1024 * 1024)
        updates = {
            effort_dir / INTAKE_FILENAME: _json_bytes(next_intake),
            effort_dir / "EFFORT.json": _json_bytes(next_manifest),
            decision_path: _revise_open_decision_options(
                decision_payload,
                next_question,
                actor,
                timestamp,
                f"Added proposed option {normalized_option['id']} without selection",
            ),
        }
        _transactional_write(
            project_root,
            effort_dir,
            updates,
            lambda: _validate_committed(
                state_module,
                project_root,
                effort_dir,
                framing_complete=_framing_complete(next_intake),
            ),
        )
    return state_module.build_state(project_root, effort_dir)


def record_intake_choice(
    root: Path,
    effort: str | Path | None,
    decision_id: str,
    expected_revision: int,
    actor: str,
    source: str,
    choice: str,
) -> dict[str, Any]:
    """Resolve one bound intake Decision from its immutable allowed options."""
    actor = _human_actor(actor)
    source = _source(source)
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 1:
        raise IntakeError("Expected revision must be a positive integer.", "INTAKE_VALIDATION", 422)
    if not isinstance(decision_id, str) or not DECISION_ID.fullmatch(decision_id):
        raise IntakeError("Decision ID is invalid.", "INTAKE_VALIDATION", 422)
    if not isinstance(choice, str) or not OPTION_ID.fullmatch(choice):
        raise IntakeError("Choice ID is invalid.", "INTAKE_INVALID_OPTION", 422)
    state_module, project_root, effort_dir, manifest = _safe_context(root, effort)
    with _intake_lock(effort_dir):
        _recover_transaction(project_root, effort_dir)
        intake = _load_intake(effort_dir, manifest)
        if intake["revision"] != expected_revision:
            raise IntakeError("Intake revision changed; refresh and retry.", "INTAKE_REVISION_CONFLICT", 409)
        current_id = intake["current_question_id"]
        if current_id is None:
            raise IntakeError("Intake is already complete.", "INTAKE_REVISION_CONFLICT", 409)
        question = intake["question_snapshots"][current_id]
        binding = next(
            (item for item in intake["decision_bindings"] if item["question_id"] == current_id and item["status"] == "OPEN"),
            None,
        )
        if question["answer_type"] != "choice" or binding is None or binding["decision_id"] != decision_id:
            raise IntakeError("Decision does not identify the current intake choice.", "INTAKE_REVISION_CONFLICT", 409)
        allowed = {item["id"] for item in question["options"]}
        if choice not in allowed:
            raise IntakeError("Choice is not one of the current Decision's allowed options.", "INTAKE_INVALID_OPTION", 422)
        state = state_module.build_state(project_root, effort_dir)
        _require_intake_ready(state, framing_complete=_framing_complete(intake))
        timestamp = _now()
        next_intake = deepcopy(intake)
        next_manifest = deepcopy(manifest)
        current_binding = next(item for item in next_intake["decision_bindings"] if item["decision_id"] == decision_id)
        evidence_id = _next_id(next_manifest, effort_dir, "evidence")
        current_binding.update({"status": "RESOLVED", "selected_option": choice, "evidence_id": evidence_id})
        next_intake["answers"].append(
            {"question_id": current_id, "value": choice, "actor": actor, "source": source, "answered_at": timestamp}
        )
        _append_receipt(
            next_intake,
            kind="CHOICE",
            question_id=current_id,
            actor=actor,
            source=source,
            timestamp=timestamp,
            decision_id=decision_id,
            evidence_id=evidence_id,
            choice=choice,
        )
        if current_id == "Q-001":
            branch_domain = choice if choice in DOMAIN_IDS else "OTHER"
            next_intake["domain"]["selected"] = branch_domain
            next_intake["domain"]["selected_option"] = choice
            next_intake["domain"]["primary_domain"] = branch_domain
            next_intake["domain"]["selection_source"] = "HUMAN_EXPLICIT"
            suggestions = list(next_intake["domain"].get("suggested_secondary_domains", []))
            proposed = next_intake["domain"].get("proposed")
            if proposed in DOMAIN_IDS and proposed not in {"OTHER", branch_domain}:
                suggestions.append(proposed)
            suggestions = list(dict.fromkeys(item for item in suggestions if item != branch_domain))[:3]
            next_intake["domain"]["suggested_secondary_domains"] = suggestions
            next_intake["domain"]["hybrid_candidate"] = bool(suggestions)
            next_intake["domain"]["ambiguous"] = next_intake["domain"]["ambiguous"] or bool(suggestions)
            next_intake["question_order"] = ["Q-001", *COMMON_ORDER, *DOMAIN_ORDER[branch_domain]]
        next_intake["revision"] += 1
        next_intake["updated_at"] = timestamp
        revision = next_manifest["effort"].get("destination_revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise IntakeError("EFFORT.json destination revision is invalid.", "INTAKE_NOT_READY", 409)
        _manifest_resolve_choice(next_manifest, decision_id, evidence_id, revision, timestamp)
        if current_id in COMPARISON_QUESTION.values():
            for comparison in next_intake["comparisons"]:
                comparison_domain = next(domain for domain, final_id in COMPARISON_QUESTION.items() if final_id == current_id)
                if comparison.get("domain") == comparison_domain:
                    comparison["selected_option"] = choice
        elif current_id == "Q-SW-012":
            for comparison in next_intake["comparisons"]:
                if comparison.get("id") == "CMP-TECH-001":
                    comparison["selected_option"] = choice
        elif re.fullmatch(r"Q-RV-\d{3}", current_id):
            fact_revalidation = next(
                (
                    item for item in next_intake.get("fact_revalidations", [])
                    if item.get("revalidation_question_id") == current_id
                ),
                None,
            )
            if not isinstance(fact_revalidation, Mapping):
                raise IntakeError("The current revalidation choice lacks its fact history.", "INTAKE_RECOVERY_REQUIRED", 409)
            comparison_id = fact_revalidation.get("comparison_id")
            matched = False
            for comparison in next_intake["comparisons"]:
                if comparison.get("id") == comparison_id:
                    comparison["selected_option"] = choice
                    matched = True
            if not matched:
                raise IntakeError("The current revalidation choice lacks its recomputed comparison.", "INTAKE_RECOVERY_REQUIRED", 409)
        if current_id == "Q-SW-011":
            next_intake["current_question_id"] = None
            next_intake["status"] = "AWAITING_TECH_OPTIONS"
            created = None
        else:
            created = _advance_after_answer(next_intake, next_manifest, effort_dir, actor, timestamp)
        _manifest_add_activity(next_manifest, f"INTAKE-{next_intake['revision']:04d}", decision_id, "An explicit human intake choice was recorded with a local Evidence receipt.", actor, timestamp)
        decision_path = effort_dir / "decisions" / f"{decision_id}.md"
        evidence_path = effort_dir / "evidence" / f"{evidence_id}.md"
        decision_payload = _read_regular(decision_path, "Bound intake Decision", 4 * 1024 * 1024)
        updates: dict[Path, bytes] = {
            effort_dir / INTAKE_FILENAME: _json_bytes(next_intake),
            effort_dir / "EFFORT.json": _json_bytes(next_manifest),
            decision_path: _resolve_open_decision(
                decision_payload,
                question,
                choice,
                evidence_id,
                actor,
                timestamp,
            ),
            evidence_path: _render_evidence(evidence_id, decision_id, question, choice, actor, source, timestamp, effort_dir.name, revision),
        }
        if created:
            updates[created[0]] = created[1]
        _transactional_write(
            project_root,
            effort_dir,
            updates,
            lambda: _validate_committed(state_module, project_root, effort_dir, framing_complete=_framing_complete(next_intake)),
        )
    return state_module.build_state(project_root, effort_dir)


def _technology_comparison(
    options: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    proposer: str,
    source: str,
    timestamp: str,
    revision: int = 1,
    software_answers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise IntakeError("Technology comparison revision must be a positive integer.", "INTAKE_VALIDATION", 422)
    if isinstance(options, (str, bytes)) or not 2 <= len(options) <= 6:
        raise IntakeError("Named technology proposals require two to six alternatives.", "INTAKE_VALIDATION", 422)
    known_evidence = {
        item.get("id")
        for item in manifest.get("evidence", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    } if isinstance(manifest.get("evidence"), list) else set()
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    recommended: list[str] = []
    text_fields = (
        "name", "version_or_constraint", "summary", "mvp_speed", "scale_beyond_mvp", "reliability",
        "efficiency", "cost", "complexity", "lock_in", "security_privacy", "team_fit", "reversibility", "rationale",
    )
    for index, option in enumerate(options, 1):
        if not isinstance(option, Mapping):
            raise IntakeError("Every named technology alternative must be an object.", "INTAKE_VALIDATION", 422)
        option_id = option.get("id")
        if not isinstance(option_id, str) or not re.fullmatch(r"TECH-\d{3,}", option_id) or option_id in seen:
            raise IntakeError("Named technology option IDs must be unique canonical TECH-NNN values.", "INTAKE_VALIDATION", 422)
        seen.add(option_id)
        item = {"id": option_id}
        for field_name in text_fields:
            item[field_name] = _bounded_text(option.get(field_name), f"Technology {field_name}", 2_000)
        item["label"] = item["name"]
        recommendation = option.get("recommendation")
        if not isinstance(recommendation, bool):
            raise IntakeError("Each technology alternative needs a boolean recommendation marker.", "INTAKE_VALIDATION", 422)
        item["recommendation"] = recommendation
        if recommendation:
            recommended.append(option_id)
        raw_evidence = option.get("evidence_refs", [])
        raw_sources = option.get("primary_sources", [])
        if not isinstance(raw_evidence, list) or len(raw_evidence) > 32 or not isinstance(raw_sources, list) or len(raw_sources) > 32:
            raise IntakeError("Technology grounding references are invalid.", "INTAKE_VALIDATION", 422)
        evidence_refs = sorted(set(raw_evidence))
        if not all(isinstance(value, str) and EVIDENCE_ID.fullmatch(value) and value in known_evidence for value in evidence_refs):
            raise IntakeError("Technology evidence_refs must name indexed canonical E-NNN evidence.", "INTAKE_VALIDATION", 422)
        primary_sources: list[str] = []
        for raw_url in raw_sources:
            url = _bounded_text(raw_url, "Primary source", 1_000)
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise IntakeError("Primary sources must be bounded credential-free HTTPS document URLs.", "INTAKE_VALIDATION", 422)
            primary_sources.append(url)
        if not evidence_refs and not primary_sources:
            raise IntakeError("Every named technology alternative needs project evidence or a primary source.", "INTAKE_VALIDATION", 422)
        item["evidence_refs"] = evidence_refs
        item["primary_sources"] = sorted(set(primary_sources))
        normalized.append(item)
    grounding, facts_digest, recommendation_status = _comparison_grounding(
        software_answers or {},
        "SOFTWARE",
    )
    if recommendation_status == "GROUNDED" and len(recommended) != 1:
        raise IntakeError(
            "A grounded named technology comparison requires exactly one advisory recommendation.",
            "INTAKE_VALIDATION",
            422,
        )
    if recommendation_status == "CONDITIONAL" and recommended:
        raise IntakeError(
            "Named technology options cannot be recommended while material software facts remain unresolved.",
            "INTAKE_VALIDATION",
            422,
        )
    recommended_option = recommended[0] if recommended else None
    recommendation_rationale = (
        next(item["rationale"] for item in normalized if item["id"] == recommended_option)
        if recommended_option is not None
        else _grounding_clause("SOFTWARE", grounding, recommendation_status)
        + " Named alternatives remain available for human review, but none is preferred."
    )
    return {
        "id": "CMP-TECH-001",
        "kind": "named_technology",
        "domain": "SOFTWARE",
        "title": "Grounded named technology alternatives",
        "criteria": [
            "mvp_speed", "scale_beyond_mvp", "reliability", "efficiency", "cost", "complexity",
            "lock_in", "security_privacy", "team_fit", "reversibility",
        ],
        "options": normalized,
        "recommended_option": recommended_option,
        "selected_option": None,
        "recommendation_rationale": recommendation_rationale,
        "recommendation_status": recommendation_status,
        "grounding": grounding,
        "facts_digest": facts_digest,
        "proposed_by": proposer,
        "proposal_source": source,
        "proposed_at": timestamp,
        "revision": revision,
    }


def propose_technology_options(
    root: Path,
    effort: str | Path | None,
    expected_revision: int,
    actor: str,
    source: str,
    options: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Import grounded named alternatives without selecting one for the human."""
    proposer = _bounded_text(actor, "Proposal actor", 120)
    source = _source(source)
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 1:
        raise IntakeError("Expected revision must be a positive integer.", "INTAKE_VALIDATION", 422)
    state_module, project_root, effort_dir, manifest = _safe_context(root, effort)
    with _intake_lock(effort_dir):
        _recover_transaction(project_root, effort_dir)
        intake = _load_intake(effort_dir, manifest)
        if intake["revision"] != expected_revision:
            raise IntakeError("Intake revision changed; refresh and retry.", "INTAKE_REVISION_CONFLICT", 409)
        software_in_scope = intake["domain"]["primary_domain"] == "SOFTWARE" or any(
            item.get("domain") == "SOFTWARE" for item in intake["domain"].get("secondary_workstreams", [])
        )
        if not software_in_scope or intake["status"] != "AWAITING_TECH_OPTIONS":
            raise IntakeError("Named technology alternatives are accepted only at the software option-proposal boundary.", "INTAKE_NOT_READY", 409)
        if any(item.get("id") == "CMP-TECH-001" for item in intake["comparisons"]):
            raise IntakeError("Named technology alternatives already exist; start a new revisioned decision to replace them.", "INTAKE_REVISION_CONFLICT", 409)
        state = state_module.build_state(project_root, effort_dir)
        framing_complete = _framing_complete(intake)
        _require_intake_ready(state, framing_complete=framing_complete)
        timestamp = _now()
        comparison = _technology_comparison(
            options,
            manifest,
            proposer,
            source,
            timestamp,
            1,
            _answers_by_key(intake),
        )
        next_intake = deepcopy(intake)
        next_manifest = deepcopy(manifest)
        next_intake["comparisons"].append(comparison)
        next_intake["question_order"].append("Q-SW-012")
        for workstream in next_intake["domain"].get("secondary_workstreams", []):
            if workstream.get("domain") == "SOFTWARE" and "Q-SW-012" not in workstream["required_questions"]:
                workstream["required_questions"].append("Q-SW-012")
        snapshot = _question_snapshot("Q-SW-012", next_intake)
        next_intake["question_snapshots"]["Q-SW-012"] = snapshot
        next_intake["current_question_id"] = "Q-SW-012"
        next_intake["status"] = "AWAITING_HUMAN_CHOICE"
        _append_receipt(
            next_intake,
            kind="PROPOSAL",
            question_id="Q-SW-012",
            actor=proposer,
            source=source,
            timestamp=timestamp,
        )
        next_intake["revision"] += 1
        next_intake["updated_at"] = timestamp
        authority = _human_actor(_answers_by_key(next_intake).get("decision_authority"))
        created = _materialize_choice(next_intake, next_manifest, effort_dir, snapshot, authority, timestamp)
        next_manifest["effort"]["updated_at"] = timestamp
        _manifest_add_activity(
            next_manifest,
            f"INTAKE-{next_intake['revision']:04d}",
            next_intake["decision_bindings"][-1]["decision_id"],
            "Grounded named technology alternatives were proposed; human selection remains pending.",
            proposer,
            timestamp,
        )
        updates = {
            effort_dir / INTAKE_FILENAME: _json_bytes(next_intake),
            effort_dir / "EFFORT.json": _json_bytes(next_manifest),
            created[0]: created[1],
        }
        _transactional_write(
            project_root,
            effort_dir,
            updates,
            lambda: _validate_committed(
                state_module, project_root, effort_dir, framing_complete=_framing_complete(next_intake)
            ),
        )
    return state_module.build_state(project_root, effort_dir)


def replace_technology_options(
    root: Path,
    effort: str | Path | None,
    decision_id: str,
    expected_revision: int,
    actor: str,
    source: str,
    options: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Revision-CAS replacement of the open named-tech comparison; never selects an option."""
    proposer = _bounded_text(actor, "Proposal actor", 120)
    source = _source(source)
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 1:
        raise IntakeError("Expected revision must be a positive integer.", "INTAKE_VALIDATION", 422)
    if not isinstance(decision_id, str) or not DECISION_ID.fullmatch(decision_id):
        raise IntakeError("Decision ID is invalid.", "INTAKE_VALIDATION", 422)
    state_module, project_root, effort_dir, manifest = _safe_context(root, effort)
    with _intake_lock(effort_dir):
        _recover_transaction(project_root, effort_dir)
        intake = _load_intake(effort_dir, manifest)
        if intake["revision"] != expected_revision:
            raise IntakeError("Intake revision changed; refresh and retry.", "INTAKE_REVISION_CONFLICT", 409)
        binding = next(
            (
                item
                for item in intake["decision_bindings"]
                if item.get("question_id") == "Q-SW-012" and item.get("status") == "OPEN"
            ),
            None,
        )
        active = next(
            (item for item in intake["comparisons"] if item.get("id") == "CMP-TECH-001"),
            None,
        )
        if (
            intake.get("current_question_id") != "Q-SW-012"
            or intake.get("status") != "AWAITING_HUMAN_CHOICE"
            or binding is None
            or binding.get("decision_id") != decision_id
            or active is None
            or active.get("selected_option") is not None
        ):
            raise IntakeError(
                "Named technology alternatives may be replaced only while their current Decision remains open.",
                "INTAKE_NOT_READY",
                409,
            )
        old_revision = active.get("revision")
        if not isinstance(old_revision, int) or isinstance(old_revision, bool) or old_revision < 1:
            raise IntakeError("The active technology comparison revision is invalid.", "INTAKE_RECOVERY_REQUIRED", 409)
        state = state_module.build_state(project_root, effort_dir)
        _require_intake_ready(state, framing_complete=_framing_complete(intake))
        timestamp = _now()
        next_revision = old_revision + 1
        replacement = _technology_comparison(
            options,
            manifest,
            proposer,
            source,
            timestamp,
            next_revision,
            _answers_by_key(intake),
        )
        next_intake = deepcopy(intake)
        next_manifest = deepcopy(manifest)
        next_intake["comparison_history"].append(
            {
                "comparison_id": "CMP-TECH-001",
                "revision": old_revision,
                "superseded_at": timestamp,
                "superseded_by": f"CMP-TECH-001@{next_revision}",
                "source": source,
                "snapshot": deepcopy(active),
            }
        )
        next_intake["comparisons"] = [
            replacement if item.get("id") == "CMP-TECH-001" else item
            for item in next_intake["comparisons"]
        ]
        next_question = _question_snapshot("Q-SW-012", next_intake)
        next_intake["question_snapshots"]["Q-SW-012"] = next_question
        _append_receipt(
            next_intake,
            kind="TECH_OPTIONS_REVISED",
            question_id="Q-SW-012",
            actor=proposer,
            source=source,
            timestamp=timestamp,
            decision_id=decision_id,
            old_comparison_revision=old_revision,
            new_comparison_revision=next_revision,
        )
        next_intake["revision"] += 1
        next_intake["updated_at"] = timestamp
        next_manifest["effort"]["updated_at"] = timestamp
        _manifest_add_activity(
            next_manifest,
            f"INTAKE-{next_intake['revision']:04d}",
            decision_id,
            "Grounded named technology alternatives were revised; human selection remains pending.",
            proposer,
            timestamp,
        )
        decision_path = effort_dir / "decisions" / f"{decision_id}.md"
        decision_payload = _read_regular(decision_path, "Bound intake Decision", 4 * 1024 * 1024)
        updates = {
            effort_dir / INTAKE_FILENAME: _json_bytes(next_intake),
            effort_dir / "EFFORT.json": _json_bytes(next_manifest),
            decision_path: _revise_open_decision_options(
                decision_payload,
                next_question,
                proposer,
                timestamp,
                f"Replaced named technology comparison revision {old_revision} with {next_revision}",
            ),
        }
        _transactional_write(
            project_root,
            effort_dir,
            updates,
            lambda: _validate_committed(
                state_module,
                project_root,
                effort_dir,
                framing_complete=_framing_complete(next_intake),
            ),
        )
    return state_module.build_state(project_root, effort_dir)


def load_technology_options(path: Path) -> list[Mapping[str, Any]]:
    """Read a bounded no-follow `{\"options\": [...]}` proposal file."""
    if path.is_symlink():
        raise IntakeError("Technology option file must be a regular file.", "INTAKE_VALIDATION", 422)
    try:
        payload = _parse_json(_read_regular(path, "Technology option file"), "Technology option file")
    except FileNotFoundError as exc:
        raise IntakeError("Technology option file was not found.", "INTAKE_VALIDATION", 422) from exc
    options = payload.get("options")
    if not isinstance(options, list):
        raise IntakeError("Technology option file must contain an options array.", "INTAKE_VALIDATION", 422)
    return options


def add_secondary_workstream(
    root: Path,
    effort: str | Path | None,
    expected_revision: int,
    actor: str,
    source: str,
    domain: str,
    outcome: str,
    authority: str,
) -> dict[str, Any]:
    """Record an explicit hybrid workstream choice and resume its required branch."""
    actor = _human_actor(actor)
    authority = _human_actor(authority)
    source = _source(source)
    outcome = _bounded_text(outcome, "Secondary workstream outcome", 1_000)
    if domain not in DOMAIN_IDS:
        raise IntakeError("Secondary workstream domain is invalid.", "INTAKE_INVALID_OPTION", 422)
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 1:
        raise IntakeError("Expected revision must be a positive integer.", "INTAKE_VALIDATION", 422)
    state_module, project_root, effort_dir, manifest = _safe_context(root, effort)
    with _intake_lock(effort_dir):
        _recover_transaction(project_root, effort_dir)
        intake = _load_intake(effort_dir, manifest)
        if intake["revision"] != expected_revision:
            raise IntakeError("Intake revision changed; refresh and retry.", "INTAKE_REVISION_CONFLICT", 409)
        if intake["domain"].get("primary_domain") not in DOMAIN_IDS:
            raise IntakeError("Confirm the primary domain before adding a secondary workstream.", "INTAKE_NOT_READY", 409)
        if intake["status"] == "AWAITING_TECH_OPTIONS":
            raise IntakeError("Finish the pending named-technology proposal boundary before changing workstream order.", "INTAKE_NOT_READY", 409)
        existing_domains = {intake["domain"]["primary_domain"]} | {
            item["domain"] for item in intake["domain"]["secondary_workstreams"]
        }
        if domain in existing_domains:
            raise IntakeError("That domain already has a recorded workstream.", "INTAKE_REVISION_CONFLICT", 409)
        if len(intake["domain"]["secondary_workstreams"]) >= 3:
            raise IntakeError("The bounded secondary-workstream limit has been reached.", "INTAKE_NOT_READY", 409)
        state = state_module.build_state(project_root, effort_dir)
        _require_intake_ready(state, framing_complete=_framing_complete(intake))
        timestamp = _now()
        next_intake = deepcopy(intake)
        next_manifest = deepcopy(manifest)
        workstream_number = len(next_intake["domain"]["secondary_workstreams"]) + 1
        workstream_id = f"WS-{workstream_number:03d}"
        question_id = f"Q-HY-{workstream_number:03d}"
        options = [item for item in DOMAIN_OPTIONS if item["id"] not in existing_domains]
        question = _choice_question(
            question_id,
            f"secondary_domain_{workstream_number}",
            "Which material secondary workstream should Wayfinder add to this effort?",
            options,
            "Hybrid work must retain separate outcomes, authority, required questions, and stable Decisions.",
            "Confirm the secondary workstream domain",
            destination_blocking=False,
        )
        insertion_index = len(next_intake["answers"])
        next_intake["question_order"].insert(insertion_index, question_id)
        next_intake["question_order"].extend(DOMAIN_ORDER[domain])
        next_intake["question_snapshots"][question_id] = question
        workstream = {
            "id": workstream_id,
            "domain": domain,
            "outcome": outcome,
            "authority": authority,
            "required_questions": list(DOMAIN_ORDER[domain]),
            "decision_ids": [],
        }
        next_intake["domain"]["secondary_workstreams"].append(workstream)
        remaining_suggestions = [
            item for item in next_intake["domain"].get("suggested_secondary_domains", []) if item != domain
        ]
        next_intake["domain"]["suggested_secondary_domains"] = remaining_suggestions
        next_intake["domain"]["hybrid_candidate"] = bool(remaining_suggestions)
        decision_path, _open_decision = _materialize_choice(next_intake, next_manifest, effort_dir, question, authority, timestamp)
        binding = next_intake["decision_bindings"][-1]
        decision_id = binding["decision_id"]
        evidence_id = _next_id(next_manifest, effort_dir, "evidence")
        binding.update({"status": "RESOLVED", "selected_option": domain, "evidence_id": evidence_id})
        workstream["decision_ids"].append(decision_id)
        next_intake["answers"].append(
            {"question_id": question_id, "value": domain, "actor": actor, "source": source, "answered_at": timestamp}
        )
        _append_receipt(
            next_intake,
            kind="CHOICE",
            question_id=question_id,
            actor=actor,
            source=source,
            timestamp=timestamp,
            decision_id=decision_id,
            evidence_id=evidence_id,
            choice=domain,
        )
        next_intake["revision"] += 1
        next_intake["updated_at"] = timestamp
        revision = next_manifest["effort"].get("destination_revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise IntakeError("EFFORT.json destination revision is invalid.", "INTAKE_NOT_READY", 409)
        _manifest_resolve_choice(next_manifest, decision_id, evidence_id, revision, timestamp)
        next_snapshot = _next_snapshot(next_intake)
        created = None
        if next_snapshot is not None and next_snapshot["answer_type"] == "choice":
            existing_open = any(
                item.get("question_id") == next_snapshot["id"] and item.get("status") == "OPEN"
                for item in next_intake["decision_bindings"]
            )
            if not existing_open:
                created = _materialize_choice(next_intake, next_manifest, effort_dir, next_snapshot, authority, timestamp)
        _manifest_add_activity(
            next_manifest,
            f"INTAKE-{next_intake['revision']:04d}",
            decision_id,
            "An explicit secondary workstream was added; its domain-specific questions are now active.",
            actor,
            timestamp,
        )
        updates: dict[Path, bytes] = {
            effort_dir / INTAKE_FILENAME: _json_bytes(next_intake),
            effort_dir / "EFFORT.json": _json_bytes(next_manifest),
            decision_path: _render_decision(decision_id, question, authority, timestamp, choice=domain, evidence_id=evidence_id, resolved_at=timestamp),
            effort_dir / "evidence" / f"{evidence_id}.md": _render_evidence(
                evidence_id, decision_id, question, domain, actor, source, timestamp, effort_dir.name, revision
            ),
        }
        if created:
            updates[created[0]] = created[1]
        _transactional_write(
            project_root,
            effort_dir,
            updates,
            lambda: _validate_committed(
                state_module, project_root, effort_dir, framing_complete=_framing_complete(next_intake)
            ),
        )
    return state_module.build_state(project_root, effort_dir)
