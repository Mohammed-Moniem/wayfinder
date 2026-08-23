# Domain routing and option comparisons

Route by the primary outcome, not by isolated keywords. Treat the classification as a proposal until evidence is strong or the user confirms it.

## Domain model

| Domain | Primary outcome | Typical route choices |
| --- | --- | --- |
| `SOFTWARE` | A running application, service, integration, automation, data system, or technical platform | architecture, technology, data, hosting, security, reliability, delivery |
| `GENERAL_PROJECT` | A physical, operational, organizational, construction, event, or service-delivery result | phases, owners, schedule, permits, vendors, materials, safety, quality, contingency |
| `FINANCE_REPORTING` | Accounting records, reconciliations, management reports, financial statements, audit support, or filing preparation | basis, period, sources, mappings, controls, review, evidence, format, sign-off |
| `OTHER` | A goal not well represented above | outcome-specific workstreams, evidence, owners, dependencies, acceptance |

“Build a construction project dashboard” is usually `SOFTWARE`; “deliver a construction project” is usually `GENERAL_PROJECT`. “Automate the monthly close” may be hybrid: finance/reporting owns correctness and sign-off, while software is a supporting workstream. Record `primary_domain` plus each material secondary workstream's stable ID, domain, outcome, authority, required-question IDs, and resulting decision IDs. A secondary workstream is not a label only; it keeps its own readiness and route choices.

## Confidence and ambiguity

- Use high confidence only when the primary deliverable, success proof, and authority all point to one domain.
- Use medium confidence when the wording is clear but the operating context or deliverable is incomplete.
- Mark the result ambiguous when two domains would produce materially different decisions. Ask the user to choose the dominant outcome; do not route by majority keyword count.
- Reclassify only with a receipt. A domain change invalidates branch-specific comparisons and requires their dependents to be inspected.

## Software technology comparison

First compare architecture strategies only when that is still an open choice. Before the software route is complete, compare at least two named, viable technology stacks grounded in the current environment, team, integrations, operating owner, and current primary evidence; generic labels such as “managed platform” are not a final technology decision. Include a “stay with the existing approach” option when migration itself is a material cost. Do not make every option look equivalent.

Explain each criterion in plain language:

| Criterion | Plain meaning |
| --- | --- |
| MVP speed | How quickly a small useful first version can be delivered |
| Scale beyond MVP | How comfortably it can grow in users, data, regions, and features |
| Reliability | How well it avoids, contains, and recovers from failures |
| Efficiency | The computing, network, storage, and operator effort it consumes |
| Cost | Build cost plus realistic ongoing infrastructure and maintenance cost |
| Complexity | How difficult it is to build, test, operate, hire for, and change |
| Lock-in | How expensive it would be to move away later |
| Security and privacy | How well it supports the project's actual data and access obligations |
| Team fit | Whether the available team can deliver and operate it safely |
| Reversibility | Whether an early choice can be changed without rebuilding the project |

Use `Strong`, `Workable`, `Weak`, or `Unknown` with a short reason unless trustworthy measurements justify numbers. Separate evidence from inference and show what would change the recommendation. Prefer the simplest option that satisfies the destination and near-term proof while retaining a credible growth path; “scalable” does not justify premature distributed complexity.

## General-project comparisons

Compare route strategies such as phased versus single cutover, self-performed versus vendor-delivered, or schedule/material/vendor alternatives. Include schedule confidence, total cost, resource availability, permits/approvals, safety and quality exposure, dependency risk, reversibility, contingency, and who accepts the result. Technology belongs here only when it materially supports the project.

## Finance/reporting comparisons

Compare source/mapping, reconciliation, close, control, and delivery approaches. Include auditability, data lineage, cut-off correctness, repeatability, exception handling, reviewer effort, access segregation, change control, and filing or management deadline risk. Never fabricate an accounting rule or imply professional sign-off. For legal, tax, filing, or accounting-standard conclusions, use current authoritative sources when available and record the qualified reviewer as decision authority.

## Plain-language presentation

Lead with the decision and consequence, then show the comparison. Define unavoidable technical or accounting terms at first use. Give the recommendation and its reason in one short paragraph, followed by the strongest alternative and the condition under which it would become better. Preserve a technical appendix in canonical evidence when detail is needed; do not force nontechnical users to interpret raw infrastructure or ledger jargon to make a choice.
