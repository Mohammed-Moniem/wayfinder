# Local Wayfinder dashboard

Read this reference when the user asks to view, launch, explain, record an intake answer from, or troubleshoot the visual Wayfinder map.

## Boundary

Dashboard application code belongs to the installed Wayfinder skill, never the target project:

```text
installed Wayfinder skill
├── scripts/wayfinder_server.py
├── scripts/wayfinder_state.py
├── scripts/wayfinder_intake.py
└── assets/dashboard/     dependency-free HTML, CSS, JavaScript
             │
             │ fresh capability URL on loopback
             ▼
local browser ── GET state/session ──> validated project artifacts
             └─ optional bounded POST ─> canonical intake CAS seam
```

- Serve assets directly from the installed skill. Never copy, scaffold, bundle, cache, or generate dashboard code inside the target project.
- Bind only to `127.0.0.1`. Require the exact printed Host and a fresh per-launch capability path on every page, asset, API, and method request.
- Treat the full printed URL as a temporary local secret. Do not share or log it, expose a LAN/public listener, create a tunnel, or leave the server running after use.
- Use no runtime install, CDN, external font, analytics, telemetry, package dependency, or automatic external request. A cited credential-free HTTPS source may open only after the user deliberately activates its evidence link.
- Treat project text and state as untrusted. Render with text nodes, never executable HTML, and never expose rejected raw values or unsafe paths.
- Project artifacts remain canonical. The dashboard does not maintain a shadow copy or write dashboard files into the project.

## Modes

The server is read-only by default. A manual diagnostic launch can inspect the route without granting a mutation surface.

Interactive mode is deliberate opt-in for the guided intake journey. It can perform exactly two operations:

1. select one option already present on the exact current intake Decision;
2. record one bounded answer for the exact current text or fact question.

It cannot accept a browser-supplied actor, source, path, filename, command, arbitrary field, Decision title, or free-form option. The server fixes the identity to `User` and the source to `DASHBOARD`, then delegates to the canonical intake engine. The engine revalidates current-question identity, allowed option, optimistic revision, structure, containment, and recovery state under a lock. A successful choice atomically resolves its Decision and appends the Evidence and activity receipts; a framing answer atomically advances one question. A refreshed state payload is returned only after the canonical transaction succeeds.

Every write additionally requires:

- the capability-bearing path;
- the exact loopback Host and Origin;
- the per-launch CSRF token from same-origin session metadata;
- exact `application/json` content type and a bounded body;
- a unique-key, finite JSON object;
- an exact `If-Match` value equal to the body revision;
- the exact narrow field set for that endpoint.

Only POST may reach a recorder. PUT, PATCH, DELETE, OPTIONS, CONNECT, TRACE, unknown verbs, query variants, missing proofs, and read-only launches are non-mutating. There are no CORS permissions.

## Launch

From the target project directory, validate before serving:

```bash
python3 /path/to/installed/wayfinder/scripts/wayfinder.py doctor --root .
python3 /path/to/installed/wayfinder/scripts/wayfinder.py dashboard --root .
```

Use interactive mode only when the user is intentionally continuing intake:

```bash
python3 /path/to/installed/wayfinder/scripts/wayfinder.py dashboard --root . --interactive
```

The server chooses a fresh OS-assigned port by default. Fixed ports are explicit opt-in because predictable browser-origin reuse weakens isolation. Open only the exact capability URL printed by the current process.

Before a guided workflow presents the URL, verify all of these:

- framing is ready enough to expose the current intake state;
- there is a current question;
- the server started successfully;
- the capability-bearing state endpoint returns 200;
- session metadata reports interactive mode and a recordable current question.

Do not guess an installed-skill path or add a project dependency. The server reports the selected project root and active effort before serving. Stop if ACTIVE, a manifest path, an indexed artifact, or intake recovery state is unsafe or inconsistent. A project directory need not be a Git repository when it was explicitly selected and all Wayfinder containment checks pass.

## Routes and interaction

The sidebar contains seven real hash routes. They are deep-linkable and participate in browser back/forward history:

| Route | Purpose |
| --- | --- |
| `#/overview` | destination, next move, route health, workstreams, comparison level, and implementation baseline |
| `#/map` | keyboard-accessible typed dependency graph |
| `#/decisions` | filterable Decision and Gate ledger |
| `#/decisions/D-NNN` | stable deep link to one exact Decision inspector |
| `#/evidence` | Evidence receipts, planning-exit proof, activity, and invalidations |
| `#/assumptions` | open, settled, blocking, and non-blocking assumptions |
| `#/invariants` | active route constraints and their enforcement |
| `#/checkpoints` | five phases, milestones, and explicit rerun recommendations |

Route changes update `aria-current`, the document title, and heading focus. The mobile navigation and Decision inspector trap focus only while acting as modal overlays. Opening mobile navigation moves focus to its explicit in-drawer close button and makes the page background inert. Its close button or Escape closes the drawer and restores focus to the menu opener; choosing a route closes the drawer and routing moves focus to the destination heading. The slash shortcut routes to the Decision ledger before focusing search when necessary.

The Overview explicitly distinguishes:

- the primary workstream from secondary workstreams;
- an architecture or operating-strategy comparison from a comparison of named technologies or vendors;
- the live route from its implementation baseline of effort ID, destination revision, intake revision, exact manifest hash, and applicable Decision revisions.

All route comparisons remain visible. Strategy and named-technology comparisons are labeled separately; each option keeps its recommendation rationale and offers a collapsed, scannable tradeoff-and-evidence detail rather than a raw data dump. An HTTPS source link is inert until the user deliberately opens it and receives no dashboard credential or capability value.

User-facing lifecycle copy is domain-neutral: phase four is Ready for execution, and the planning exit leads to an execution handoff. Legacy artifact labels may still be accepted by the state engine without forcing software terminology into general-project or finance views.

## Rerun guidance

The Checkpoints view is the answer to “when should I run Wayfinder again for this project?” It shows the current recommendation and why, then distinguishes:

- **Run now** — canonical state currently requires attention or an explicit human answer;
- **Run at checkpoint** — the next lifecycle boundary recommends a fresh pass;
- **Rerun if changed** — a completed checkpoint should be revisited only after its recorded assumptions or inputs materially change.

Five milestones show the execution route without claiming that delivery has happened. Planning readiness, execution readiness, delivery evidence, and revalidation remain separate facts.

## Troubleshooting

- Run `status` or `doctor` before blaming the UI. Degraded diagnostics stay visible instead of hiding route nodes.
- If state cannot load, reopen the exact URL from the same running process. A prior capability is intentionally invalid.
- If a write returns 409, refresh and review the new current question; the optimistic revision lost a race and nothing from the stale submission was applied.
- If a write returns 422, the current answer or option failed the canonical bounded intake contract; do not bypass validation in the browser.
- If session is read-only, restart deliberately with interactive mode rather than adding a generic mutation endpoint.
- If counts differ from artifacts, resolve the canonical conflict explicitly; do not patch the payload or UI.
- Stop the server when viewing or intake continuation ends.
