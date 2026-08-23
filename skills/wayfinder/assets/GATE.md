# G-NNN: {{GATE_NAME}}

- **Kind:** GATE
- **Phase:** p5-delivery
- **Post build:** true
- **Status:** DEFINED
- **Responsible party:** {{RESPONSIBLE_PARTY}}
- **Next actor:** {{NEXT_ACTOR}}
- **Waiver authority:** {{WAIVER_AUTHORITY}}
- **Requires:** none
- **Revalidates:** none
- **Informs:** none
- **Gates:** M-005
- **Subject revision:** {{SUBJECT_REVISION}}
- **Defined at:** {{CREATED_AT}}
- **Last evaluated at:** never
- **Revalidate when:** {{FRESHNESS_TRIGGER}}

## Delivery condition

{{OBSERVABLE_GATE_CONDITION}}

This Gate is defined before the Wayfinder handoff and evaluated during delivery. Its unevaluated state does not block the planning exit.

## Checks

| ID | Method (`COMMAND`, `PROBE`, `REVIEW`, `HUMAN-APPROVAL`) | Expected result | Evidence required | Status |
| --- | --- | --- | --- | --- |
| C-001 | {{METHOD}} | {{EXPECTED_RESULT}} | {{EVIDENCE_REQUIRED}} | PENDING |

## Result

## Evaluation receipt

Required when the Gate is `PASSED`, `FAILED`, or `STALE`.

| Evaluated by | Timestamp | Outcome | Evidence | Subject revision | Rationale |
| --- | --- | --- | --- | --- | --- |

## Failure or staleness handling

Inspect only the Decisions listed by `revalidates`. Record each as still valid with evidence, `REOPENED`, or `SUPERSEDED`; do not reopen the entire effort by default.

## Waiver receipt

| Waived by | Authority source | Timestamp | Scope | Expiry / revalidate when | Rationale |
| --- | --- | --- | --- | --- | --- |

## Append-only transition history

| From | To | Actor | Timestamp | Reason | Evidence |
| --- | --- | --- | --- | --- | --- |
| — | DEFINED | {{CREATED_BY}} | {{CREATED_AT}} | Gate defined | none |
