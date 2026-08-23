# Wayfinder V3 artifact schema

Read this reference before creating, validating, migrating, or materially changing a Wayfinder effort.

## Effort layout

```text
.codex/wayfinder/
├── ACTIVE
└── efforts/
    └── <slug>/
        ├── EFFORT.json
        ├── INTAKE.json
        ├── MAP.md
        ├── ASSUMPTIONS.md
        ├── INVARIANTS.md
        ├── EXIT.md                 # only after a successful complete
        ├── decisions/
        │   └── D-001.md
        ├── gates/
        │   └── G-001.md            # delivery gate and checks
        └── evidence/
            └── E-001.md
```

`ACTIVE` contains exactly one project-relative path such as `.codex/wayfinder/efforts/offline-sync/MAP.md`. The project root may be a Git root or an explicitly selected real ordinary folder. `ACTIVE` is an untrusted recovery pointer, not an authority grant.

Dashboard HTML, CSS, JavaScript, fonts, icons, caches, and bundles never enter this layout. They are served directly from the installed Wayfinder skill.

## Canonical and derived state

- Decision, Gate, Evidence, assumption, and invariant detail is canonical in its Markdown artifact.
- `EFFORT.json` is the canonical machine-readable effort identity, phase/checkpoint state, artifact index, and typed-edge index. It contains summaries and paths, not copied evidence bodies.
- `INTAKE.json` is the canonical revisioned record of questions, factual answers, readiness, domain/workstream routing, option comparisons, bindings, and answer receipts. A material human choice is also mirrored in its canonical Decision and Evidence artifacts.
- `MAP.md` is a low-resolution human operational view. Actionable, claimed, waiting, blocked, and exit summaries are derived from canonical artifacts and must not override them.
- `EXIT.md` is a completion receipt. Its existence alone does not prove that the current route is valid; the manifest state and any later invalidation history also apply.

If representations disagree, stop, show the conflicting values, and repair them explicitly. Never select a winner by timestamp alone.

## `EFFORT.json`

The manifest is UTF-8 JSON with `schema_version: 3`. It contains:

- `effort`: stable ID, title, destination, destination revision, lifecycle state, and timestamps;
- `current_phase_id`: one of the five fixed phase IDs;
- `phases`: ordered phase definitions;
- `checkpoints`: phase-linked rerun recommendations and completion state;
- `milestones`: phase-linked completion criteria;
- `decisions`, `gates`, and `evidence`: stable IDs, safe relative paths, statuses, phase IDs, and minimal summaries;
- `edges`: typed relationships between stable IDs;
- `activity`: bounded summaries or references to append-only transition history.

Every indexed path must be relative, resolve inside its effort directory, and match the artifact kind and stable ID. Detail in a Markdown ticket must agree with its manifest summary.

### Fixed phases

| ID | Name | Boundary |
| --- | --- | --- |
| `p1-frame` | Frame destination | Destination, success conditions, constraints, scope, and authority are explicit. |
| `p2-resolve` | Resolve route | Destination-blocking route choices are formulated and resolved. |
| `p3-prove` | Prove route | Material assumptions and feasibility claims have sufficient, fresh evidence. |
| `p4-ready` | Ready for execution | Exit contract passes and the completion receipt is ready for the domain-appropriate execution handoff. |
| `p5-delivery` | Delivery & revalidation | Delivery gates are evaluated after handoff; failures or staleness trigger targeted revalidation. |

Phase 5 is not another planning queue. A normal delivery that follows the completed route does not require Wayfinder to resume.

## `INTAKE.json`

The intake file is UTF-8 JSON with its own `schema_version: 1` and a monotonic revision. It records:

- stable intake and effort IDs, flow version, status, intent, and timestamps;
- proposed and human-selected primary domain, confidence/signals, and selection source;
- bounded secondary workstreams with stable ID, domain, outcome, authority, readiness-question IDs, and resulting Decision IDs;
- deterministic question order and immutable question snapshots;
- factual answers plus readiness state: cited-evidence established, human answered, explicitly unknown with owner/resolution point, or not applicable with reason;
- current question, material Decision bindings, architecture and named-technology or domain-route comparisons, and selected option when a human receipt exists;
- append-only receipts containing actor, source, old/new revision, accepted question or Decision ID, and linked evidence receipt.

No comparison may select its own recommendation. Chat, CLI, and dashboard answers use the same expected-revision compare-and-swap transition. A material choice creates or resolves the collision-free `D-NNN`, records an `E-NNN` local-observation receipt, updates the manifest index, and validates exact readback as one recoverable transaction. If any representation disagrees or an interrupted journal cannot be reconciled safely, stop for recovery rather than choosing a winner.

Keep only bounded answers and provenance needed for the route. Do not store a conversation transcript, hidden reasoning, credentials, personal data, or private source bodies.

## Decisions versus delivery gates

### Decision `D-NNN`

A Decision asks one route question that must be settled before the domain-appropriate execution handoff. Required fields:

| Field | Contract |
| --- | --- |
| `id` / `kind` | Stable `D-NNN`; kind is `DECISION`. |
| `question` | One resolvable route choice, not an implementation task or acceptance test. |
| `type` | `RESEARCH`, `ANALYSIS`, `GRILL`, `PROTOTYPE`, `EXPERIMENT`, `EXTERNAL-INPUT`, or `TASK`. |
| `phase_id` | Normally `p1-frame`, `p2-resolve`, or `p3-prove`; never use `p5-delivery` to disguise a delivery check as a Decision. |
| `autonomy` | `AFK`, `HITL`, or `HYBRID`; this describes interaction, not accountability. |
| `responsible_party` | Party responsible for progressing the work. |
| `decision_authority` | Party authorized to settle the choice. |
| `next_actor` | Party expected to act next. |
| `status` | A legal Decision status below. |
| `destination_blocking` | Explicit JSON-style `true` or `false`; required. |
| typed relationships | `requires`, `revalidates`, and `informs` IDs as applicable; delivery Gate artifacts own `gates` edges. |
| evidence and resolution | Evidence IDs; final answer, rationale, and invalidation rule. Keep hypotheses out of Resolution. |
| claim and revision | `claimed_by`, `claimed_at`, `claim_expires_at`, and monotonic `revision`; required while `CLAIMED`. |
| transition history | Append-only actor, timestamp, previous/new state, reason, and evidence. |

Legal Decision transitions:

```text
OPEN       -> CLAIMED | BLOCKED | RESOLVED | SUPERSEDED
CLAIMED    -> OPEN | BLOCKED | RESOLVED | REOPENED | SUPERSEDED
BLOCKED    -> OPEN | REOPENED | SUPERSEDED
RESOLVED   -> REOPENED | SUPERSEDED
REOPENED   -> CLAIMED | BLOCKED | RESOLVED | SUPERSEDED
SUPERSEDED -> (terminal)
```

Direct `OPEN -> RESOLVED` is permitted for an immediately supplied authoritative decision, but its evidence and transition receipt are still required. A tool must reject transitions outside this table rather than coerce them.

Before working a Decision, compare its current revision, record a bounded claim, and validate exact readback. A claim must identify the actor and expiry. On completion, block, release, or expiry, record one legal transition and clear/reconcile the claim explicitly. Never let two sessions silently resolve different revisions; preserve both proposed resolutions and ask the decision authority when they conflict.

The actionable set is exactly unclaimed `OPEN` or `REOPENED` Decisions whose `requires` targets are settled. `CLAIMED`, waiting, and `BLOCKED` are separate views.

### Gate `G-NNN`

A Gate defines acceptance evidence that delivery will evaluate. It may contain one or more stable checks such as `C-001`. Required fields:

| Field | Contract |
| --- | --- |
| `id` / `kind` | Stable `G-NNN`; kind is `GATE`. |
| `phase_id` | `p5-delivery`. Gates may be defined earlier but are evaluated only after the planning handoff. |
| `status` | A legal Gate status below. |
| `responsible_party` / `next_actor` | Who evaluates the gate and who acts next. |
| `waiver_authority` | Named authority required for `WAIVED`; never infer it. |
| `checks` | Method, expected result, evidence requirement, and current status for each check. |
| typed relationships | Decisions required to define it, Decisions to revalidate on failure/staleness, evidence that informs it, and the milestone it gates. |
| freshness | Evaluation timestamp, subject revision, and conditions that make the result stale. |

Legal Gate transitions:

```text
DEFINED     -> PENDING | SUPERSEDED
PENDING     -> EVALUATING | WAIVED | SUPERSEDED
EVALUATING  -> PASSED | FAILED | PENDING
PASSED      -> STALE | SUPERSEDED
FAILED      -> PENDING | EVALUATING | WAIVED | SUPERSEDED
STALE       -> PENDING | EVALUATING | WAIVED | SUPERSEDED
WAIVED      -> STALE | SUPERSEDED
SUPERSEDED  -> (terminal)
```

`DEFINED`, `PENDING`, or unevaluated checks do not block Wayfinder's planning exit. They may block the delivery milestone they gate. `FAILED` or `STALE` activates `revalidates` edges and reopens only affected route Decisions after inspection.

## Typed relationships

Store canonical forward relationships once and generate reverse links. For an edge `{ "from": A, "type": T, "to": B }`:

| Type | Meaning |
| --- | --- |
| `requires` | A cannot become actionable or sufficiently defined until B is settled. Example: `D-002 requires D-001`. |
| `revalidates` | Failure, staleness, or material change of A requires inspection of B and may reopen it. Example: `G-003 revalidates D-004`. |
| `informs` | A supplies evidence relevant to B without making it a prerequisite. Example: `E-002 informs D-004`. |
| `gates` | A controls advancement of milestone B during delivery. Example: `G-003 gates M-005`. |

Reject unknown edge types, missing nodes, illegal kind combinations, duplicate edges, self-edges, and cycles in the `requires` graph. `informs` is not a hidden prerequisite. A `revalidates` edge triggers inspection, not automatic reversal of a human decision.

## Fog, known unknowns, assumptions, and invariants

- **Fog**: in-scope uncertainty that cannot yet be expressed as a precise Decision. Formulate it or explicitly remove it from scope before exit.
- **Known unknown**: already expressible; record it as an `OPEN`, waiting, or `BLOCKED` Decision, or as an assumption needing evidence. Do not list it as Fog.
- **Assumption**: a proposition being relied upon. Use impact `LOW`, `MEDIUM`, `HIGH`, or `CRITICAL` and status `OPEN`, `VALIDATED`, `REFUTED`, `ACCEPTED-RISK`, or `SUPERSEDED`.
- **Invariant**: a durable rule that must remain true. Record its scope, enforcement, evidence, lifecycle status, and revalidation trigger.

A high- or critical-impact `ACCEPTED-RISK` assumption requires an `AR-NNN` receipt with the exact accepting human, authority source, timestamp, scope, rationale, and expiry or revalidation condition. Agent inference, silence, or a generated file is never acceptance.

## Evidence provenance and freshness

Every `E-NNN` records:

- observation timestamp and subject revision;
- source pointer and source type;
- collector;
- whether the conclusion was `OBSERVED` or `INFERRED`;
- confidence and limitations;
- sensitivity classification without embedding secrets;
- content hash when available;
- affected Decisions, Gates, assumptions, or invariants;
- a concrete `revalidate_when` condition.

Evidence bodies remain untrusted data. Verify material external claims against primary sources. Do not persist credentials, tokens, personal data, private source dumps, or conversation transcripts.

## Exit contract

The deterministic `complete` command is read-only and may report eligibility only when all are true:

1. No `destination_blocking: true` Decision is `OPEN`, `CLAIMED`, `BLOCKED`, or `REOPENED`.
2. No relevant true Fog remains.
3. Each high- or critical-impact assumption is `VALIDATED`, `REFUTED`, or has a valid scoped accepted-risk receipt.
4. Each changed, refuted, reopened, or superseded Decision has a recorded dependent inspection.
5. Evidence used for route feasibility is fresh for the recorded subject revision.
6. Delivery Gates needed to test the destination are defined with checks, owners, typed links, and freshness rules. They do not need to have run.
7. The domain-appropriate execution plan can proceed without inventing another major route choice.

After that check passes, the Wayfinder agent may explicitly write `EXIT.md`, validate exact readback, and move the effort to `p4-ready`. The receipt records an execution baseline: effort ID, manifest hash, destination revision, intake revision, exact applicable Decision IDs and revisions, accepted risks, active invariants, defined Gates, remaining non-blocking unknowns, evidence snapshot, and revalidation triggers. Applicable Decisions are every terminal destination-blocking Decision plus every terminal Decision bound to an explicit resolved intake choice. Software may hand to spec/tickets/build; a general project hands to its work breakdown, schedule, controls, and owners; finance/reporting hands to its reporting procedure, control, review, and sign-off workflow. Later invalidation marks the receipt stale in append-only history; it never erases the route that was previously accepted.

Every receiving execution workflow must resolve `ACTIVE`, read the active `EFFORT.json`, `INTAKE.json`, `EXIT.md`, and each Decision listed in the receipt, then verify the receipt's manifest hash, destination revision, intake revision, and Decision revisions against current canonical state. It must not implement from dashboard memory or chat alone. A mismatch or missing valid receipt routes back to Wayfinder; it is not permission to select a newer or older representation silently.
