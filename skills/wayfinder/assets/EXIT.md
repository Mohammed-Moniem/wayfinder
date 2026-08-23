# Wayfinder Completion Receipt: {{TITLE}}

- **Effort:** {{SLUG}}
- **Schema:** 3
- **Receipt status:** CURRENT
- **Destination revision:** {{DESTINATION_REVISION}}
- **Completed at:** {{COMPLETED_AT}}
- **Completed by:** {{COMPLETED_BY}}
- **Manifest hash:** {{MANIFEST_HASH}}

## Destination accepted for execution planning

{{DESTINATION}}

## Success conditions

| ID | Observable condition | Route evidence | Status |
| --- | --- | --- | --- |

## Resolved destination-blocking Decisions

| ID | Resolution | Decision authority | Evidence | Revision |
| --- | --- | --- | --- | --- |

## Validated assumptions and accepted risks

| Assumption | Status | Evidence or accepted-risk receipt | Revalidate when |
| --- | --- | --- | --- |

## Active invariants

| ID | Invariant | Enforcement | Evidence | Revalidate when |
| --- | --- | --- | --- | --- |

## Delivery Gates defined for later evaluation

| ID | Delivery condition | Responsible party | Revalidates Decisions | Gates milestone | Freshness rule |
| --- | --- | --- | --- | --- | --- |

These Gates are evaluated during delivery. They were not required to pass before this planning receipt was written.

## Remaining non-blocking unknowns

- None.

## Revalidation triggers

- {{REVALIDATION_TRIGGER}}

## Execution baseline and handoff

- **Primary domain:** {{PRIMARY_DOMAIN}}
- **Recommended next workflow:** {{NEXT_WORKFLOW}}
- **Effort ID:** {{SLUG}}
- **Manifest hash:** {{MANIFEST_HASH}}
- **Destination revision:** {{DESTINATION_REVISION}}
- **Intake revision:** {{INTAKE_REVISION}}
- **Applicable Decision revisions:** {{APPLICABLE_DECISION_REVISIONS}}
- **Primary map:** {{MAP_PATH}}
- **Decision index:** {{DECISION_INDEX}}
- **Evidence index:** {{EVIDENCE_INDEX}}

## Completion validation

- [ ] No destination-blocking Decision remains `OPEN`, `CLAIMED`, `BLOCKED`, or `REOPENED`.
- [ ] No relevant true Fog remains.
- [ ] High/critical assumptions are settled or have valid accepted-risk receipts.
- [ ] Changed Decisions have dependent-inspection receipts.
- [ ] Route evidence is fresh for the recorded subject revision.
- [ ] Required delivery Gates are defined with checks and typed links.
- [ ] The domain-appropriate execution plan can proceed without another major route choice.

This receipt captures an accepted route, not authority to implement or release it. A later material invalidation marks it stale in append-only history and triggers targeted revalidation; it does not erase the prior decision record.
