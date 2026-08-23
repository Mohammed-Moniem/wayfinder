# Security policy

## Supported versions

Security fixes are applied to the latest release and the default branch.

## Report a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/Mohammed-Moniem/wayfinder/security/advisories/new). Do not open a public issue for a suspected vulnerability or include live credentials, private repository content, transcripts, or personal data in a report.

Include the affected component, attacker-controlled input, expected invariant, observed impact, and a minimal synthetic reproduction when possible.

## Useful report areas

- project-root containment, traversal, symlink, FIFO, and atomic-write behavior;
- dashboard loopback, Host, Origin, capability, CSRF, method, body, schema, or revision enforcement;
- unsafe DOM rendering or remote-request behavior;
- manifest/index conflicts, transition validation, evidence freshness, or authorization expansion;
- package inventory, ZIP metadata, secret scanning, diagnostic reflection, or release provenance;
- host metadata that could make an explicit-only skill activate implicitly.

## Security boundary

Wayfinder is a planning tool, not a sandbox. It validates its own project-local state and dashboard mutation surface, but the surrounding AI host and operating system retain their own permissions and security boundaries. Planning artifacts never grant authority for implementation or external action.
