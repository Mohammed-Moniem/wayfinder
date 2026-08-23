# Conversational intake

Use this workflow for a new Wayfinder destination or an incomplete `INTAKE.json`. The conversation gathers decisions; the deterministic intake commands preserve them. Never make the human's choice just to finish intake.

Command names below are subcommands of `python3 <installed-skill-root>/scripts/wayfinder.py`. Resolve `<installed-skill-root>` from this skill's own location; do not assume a `wayfinder` executable is on `PATH` or copy the runtime into the user's project.

## First invocation

1. Inspect the project and validated active pointer read-only. Reuse a matching incomplete effort; never replace an unrelated active effort silently. A real explicitly selected project folder is valid even when it is not a Git repository; do not make construction, finance, or operations users initialize Git merely to plan.
2. If the user has not stated the intended outcome, ask only: “What are you trying to achieve?” Do not create a nameless effort.
3. Once an outcome is known, initialize a new schema-3 effort with the current `ACTIVE` compare-and-swap contract, then start intake with the user's wording as `intent`. A fresh scaffold may be incomplete by design; use the intake-specific readiness check rather than bypassing validation.
4. Resume from `intake status --json` after interruption or compaction. Treat its revision and `current_question` as canonical.

## Ask like a planning conversation

- Ask one material question at a time. If the host has a structured question control, use it; otherwise ask in normal chat.
- Start with the question, add one sentence explaining why it matters, and use ordinary language.
- When a real choice exists, show two to four distinct options, a short consequence for each, and a recommended option only when evidence supports one. Always allow the user to supply a different answer.
- State what was inferred from project evidence and what still needs confirmation. If domain confidence is low or the work is hybrid, confirm the dominant outcome before branching.
- Do not ask for information already established by reliable project evidence. Do not overwhelm the user with the entire questionnaire.
- Never auto-select an option, convert silence into approval, or let an agent-authored recommendation masquerade as a human receipt.

After each reply, run `intake answer` with the exact current question or bound `D-NNN`, the expected intake revision, actor `User`, and source `CHAT`. Read the updated state back before asking the next question. If the revision is stale, refresh and reconcile; never overwrite a newer chat or dashboard answer.

If the offered answers do not cover the user's meaning at a non-comparison choice, use `intake propose-option --decision-id D-NNN --expect-revision ... --actor ... --source CHAT --option-id ... --label ... --description ...`. This appends one bounded option, increments both the intake and Decision revisions, and writes an `OPTION_PROPOSAL` receipt while leaving the Decision open and unselected. Read the new revision back, show the revised option set, and ask for the explicit choice. Never treat proposing an option as selecting it.

If `secondary_confirmation.can_record_now` is true, ask whether the suggested secondary workstream is materially part of the destination as soon as the primary domain is confirmed. On explicit agreement, use `intake add-workstream` with its domain, observable outcome, decision authority, and exact expected revision. This creates its own stable question, Decision, Evidence, and receipt without consuming or replacing the current primary-branch question. Do not add a workstream from the classifier's suggestion alone.

For a current readiness fact that is already established by inspected evidence, use `intake establish-fact` with the exact current question, revision, bounded fact, and safe evidence pointer. The pointer must be an indexed `E-NNN`, a safe project-relative regular file, or a credential-free HTTPS primary-document URL. This records evidence provenance and advances readiness; it never records a human choice. Ordinary human answers still use `intake answer`.

If a completed route still contains an explicitly owned `UNKNOWN` general-project or finance/reporting readiness fact, resolve it in the same effort with `intake revalidate-fact`. Use exactly one mode: `--answer` for a normal human answer, or `--fact` plus `--evidence-pointer` for an evidence-established fact. Include the original `Q-*`, exact expected intake revision, actor, and source. Wayfinder never edits the original answer or receipt. It appends an `FRV-NNNN` history entry and `FACT_REVALIDATED` receipt, recomputes the affected comparison from the effective facts, preserves the prior selected comparison, and opens a new destination-blocking `Q-RV-NNN` / `D-NNN` choice linked by `revalidates` to the earlier route Decision. Ask that new current question normally and record the answer through the existing `intake answer --decision-id ... --choice ...` boundary. Planning exit remains false until both the fact replacement and the new explicit human route selection are complete. A stale revision, unsafe or changed evidence pointer, unsupported fact, or already-revalidated fact fails without mutation.

This command is deliberately not a past-answer editor. It accepts only an original readiness fact whose effective state is still `UNKNOWN`, only after the current questionnaire is complete, and never rewrites or silently blesses an earlier route choice. A software fact that already informed named technology alternatives requires a grounded technology-refresh workflow and is rejected by this bounded command until that multi-comparison refresh is available.

## Minimum shared framing

Adapt the wording, but settle these facts before route decisions:

- observable destination and who benefits;
- proof of success and acceptance authority;
- time, budget, resource, policy, privacy, and permission boundaries;
- concrete constraints and non-negotiables;
- explicit out-of-scope boundary;
- material unknowns that could change the route.

The last framing answer must produce a concrete `MAP.md` destination, success row and evidence requirement, constraints, out-of-scope boundary, and bounded Fog or known-unknown record. Require a passing doctor before continuing into route-choice questions.

## Branch questions

Read [domain-routing.md](domain-routing.md) and ask only the branch-relevant questions.

Treat each required branch fact as a readiness item. Its state must be exactly one of: established by cited evidence; answered by the user; explicitly unknown with an owner and resolution point; or not applicable with a reason. Skip only evidence-established or justified-not-applicable items. Human choices still require a human receipt even when evidence suggests an answer.

For regulatory or statutory reporting, jurisdiction, reporting basis, and qualified sign-off authority are mandatory. Do not accept `N/A` for those three facts. An explicitly unknown value with a human owner may be recorded, but it remains a visible blocker and planning exit stays false until the fact is established or answered.

- **Software:** users and expected load, data sensitivity, integrations, current environment, team capability, delivery constraints, operational ownership, and acceptable cost/lock-in. Present a technology comparison before choosing a stack.
- **General project:** site or operating context, stakeholders and approval authority, schedule, resources, vendors, permits/regulation, safety/quality constraints, dependencies, acceptance, and contingency ownership.
- **Finance/reporting:** report purpose and audience, entity and jurisdiction, applicable accounting basis, period/cut-off, currency/materiality, source systems, reconciliation and data quality, controls, reviewer/sign-off, delivery format, and deadline. Record uncertainty and require qualified approval for compliance or filing conclusions.
- **Other or hybrid:** define workstreams, select the dominant outcome, and use the relevant questions for each material branch without forcing a software stack.

## Comparisons and decisions

Material options become stable `D-NNN` Decisions. Keep recommendations separate from selections. A comparison must explain why the options are viable, their important trade-offs, the consequence of waiting, what evidence could change the recommendation, and whether the choice is reversible.

Ground every branch comparison in the complete recorded fact set. Expose readable factor and readiness summaries; keep the facts digest only as machine traceability. If any material comparison fact is missing or explicitly unknown, mark the comparison `CONDITIONAL`, set `recommended_option` to `null`, mark every option `recommendation: false`, and explain which fact labels must be confirmed. The human may still inspect and choose an option, but the agent must not present an ungrounded preference.

For software comparisons, include MVP speed, scale beyond MVP, reliability, runtime/resource efficiency, cost, implementation and operating complexity, lock-in, security/privacy, team fit, recommendation, and rationale. Use current primary documentation for change-prone claims when available; never invent prices, limits, certifications, or benchmarks.

The first software choice selects an architecture or operating strategy. When status becomes `AWAITING_TECH_OPTIONS`, inspect the project's current constraints and gather current primary documentation, then prepare two to six grounded named alternatives in a bounded temporary JSON file outside the target project. Each `TECH-NNN` object must include `name`, `version_or_constraint`, `summary`, `mvp_speed`, `scale_beyond_mvp`, `reliability`, `efficiency`, `cost`, `complexity`, `lock_in`, `security_privacy`, `team_fit`, `reversibility`, `rationale`, a boolean `recommendation`, and either indexed `evidence_refs` or credential-free HTTPS `primary_sources`. When all material software facts are grounded, exactly one option must be advisory-recommended. When any material software fact is missing or `UNKNOWN`, every option must use `recommendation: false`, `recommended_option` remains `null`, and the comparison explains what must be confirmed. In either case, no option is selected. Import it with `intake propose-tech --expect-revision ... --actor ... --source ... --options-file ...`, read back the resulting `PROPOSAL` receipt, then ask the new `Q-SW-012` / `D-NNN` human choice. Never turn a generic strategy label into a pretend named technology.

If named alternatives must change before the human chooses, prepare the complete replacement comparison and use `intake revise-tech --decision-id D-NNN --expect-revision ... --actor ... --source ... --options-file ...`. Wayfinder snapshots the prior `CMP-TECH-001` revision in `comparison_history`, increments the active comparison and Decision revisions, appends a `TECH_OPTIONS_REVISED` receipt, and keeps the Decision open with no selected option. A stale revision fails without mutation. Do not use `propose-tech` twice and do not erase the earlier comparison.

The selected option, actor, source, revision, linked `E-NNN` observation receipt, and append-only transition belong to canonical state. The execution handoff must freeze an implementation baseline containing the effort ID, current manifest hash, destination revision, intake revision, and every applicable Decision ID and revision. Applicable means every terminal destination-blocking Decision plus every terminal Decision bound to an explicit resolved intake choice, so domain, route, secondary-workstream, and named-technology choices cannot disappear from implementation traceability. Later specification, tickets, and implementation must carry and revalidate that baseline. If it is stale or delivery contradicts a recorded premise, stop and run targeted revalidation instead of silently changing the route.

## Dashboard handoff

When intake has enough concrete detail to render truthfully:

1. Run doctor and fix structural problems.
2. Start the installed script's `dashboard --interactive` command as a persistent loopback process.
3. Verify that framing readiness passed, a current question or complete route exists, the state endpoint returns `200`, and session metadata reports interactive mode.
4. Only then give the user the exact capability-bearing local URL. Explain that guided interactive mode can record the currently offered decision options; arbitrary editing is not enabled.
5. Refresh canonical state before continuing chat. A dashboard receipt is already an answer—do not ask for or write it again.

Manual `dashboard` remains read-only. Dashboard assets stay in the installed skill and never enter the target project. Stopping the process invalidates the temporary URL.
