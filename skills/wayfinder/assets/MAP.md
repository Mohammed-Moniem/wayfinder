# Wayfinder Map: {{TITLE}}

- **Effort:** {{SLUG}}
- **Schema:** 3
- **State:** ACTIVE
- **Current phase:** p1-frame — Frame destination
- **Destination revision:** 1
- **Last validated:** {{CREATED_AT}}
- **Next recommended mode:** resume

## Destination

{{DESTINATION}}

## Success conditions

| ID | Observable condition | Evidence required | Status |
| --- | --- | --- | --- |
| SC-001 | {{OBSERVABLE_SUCCESS}} | {{SUCCESS_EVIDENCE}} | OPEN |

## Constraints and authority boundaries

- {{CONSTRAINT}}
- Planning artifacts do not authorize implementation, deployment, publication, spending, deletion, messaging, or external writes.

## Explicit out of scope

- {{OUT_OF_SCOPE}}

## Phase route

| Phase | Milestone | Status | Recommended checkpoint |
| --- | --- | --- | --- |
| p1-frame — Frame destination | Destination baseline | CURRENT | Run now to frame or materially revise the destination. |
| p2-resolve — Resolve route | Decision-complete route | UPCOMING | Resume when a blocking route choice is actionable. |
| p3-prove — Prove route | Evidence-sufficient route | UPCOMING | Resume before an expensive/irreversible commitment or when evidence expires. |
| p4-ready — Ready for execution | Completion handoff | UPCOMING | Run `complete` immediately before the execution handoff. |
| p5-delivery — Delivery & revalidation | Delivery milestone | UPCOMING | Run `revalidate` only after a material trigger or failed/stale Gate. |

## Actionable now

| ID | Question | Type | Autonomy | Responsible party | Decision authority | Next actor |
| --- | --- | --- | --- | --- | --- | --- |

## Claimed / in progress

| ID | Question | Claimed by | Claim expiry | Next actor |
| --- | --- | --- | --- | --- |

## Waiting for prerequisites

| ID | Question | Requires | Next actor |
| --- | --- | --- | --- |

## Externally blocked

| ID | Question | Blocker | Responsible party | Decision authority | Next actor |
| --- | --- | --- | --- | --- | --- |

## Known unknowns

- None yet. Formulated unknowns belong here, in a Decision, or in the assumption ledger—not in Fog.

## Fog / not yet formulated

- {{IN_SCOPE_BUT_NOT_FORMULATED}}

## Delivery Gates defined for handoff

| ID | Gate | Checks | Revalidates Decisions | Gates milestone | Status |
| --- | --- | --- | --- | --- | --- |

## Typed decision graph

```text
{{TYPED_EDGES}}
```

Allowed edge types: `requires`, `revalidates`, `informs`, `gates`.

## Recent transitions and invalidations

- None.

## Revalidation triggers

- Destination, scope, authority, or a success condition changes materially.
- Evidence freshness condition fires.
- The execution plan exposes a missing major route choice.
- A linked delivery Gate fails or becomes stale.

## Computed exit contract

- [ ] No destination-blocking Decision is `OPEN`, `CLAIMED`, `BLOCKED`, or `REOPENED`.
- [ ] No relevant true Fog remains.
- [ ] High/critical assumptions are settled or have valid accepted-risk receipts.
- [ ] Changed Decisions have dependent-inspection receipts.
- [ ] Route evidence is fresh for the recorded subject revision.
- [ ] Delivery Gates are defined with checks, owners, typed links, and freshness rules; they do not need to have run.
- [ ] The domain-appropriate execution plan can proceed without another major route choice.

Do not mark this view by hand as proof of completion. The deterministic `complete` command validates canonical artifacts without mutation; only after it passes may the agent explicitly write and validate `EXIT.md`.
