# Contributing

Contributions should make complex planning clearer, safer, more truthful, or easier to verify without weakening the human decision boundary.

## Before opening a change

1. Open an issue for a material workflow, state-schema, dashboard, or host-contract change.
2. Keep `skills/wayfinder/` as the single authored source; generate Claude adapters through the packager.
3. Preserve explicit invocation and one-question-at-a-time human choice.
4. Keep runtime code dependency-free and compatible with Python 3.11+.
5. Put conditional detail in `references/`, templates in `assets/`, and deterministic mechanics in `scripts/`.
6. Use synthetic fixtures only. Never include private projects, transcripts, credentials, or personal data.
7. Add behavioral and adversarial tests for material changes.

## Required checks

```bash
python3 scripts/validate_repo.py
python3 -m unittest discover -s tests -v
python3 scripts/scan_secrets.py --all
python3 -m compileall -q scripts tests skills/wayfinder/scripts
node --check skills/wayfinder/assets/dashboard/app.js
python3 scripts/package_wayfinder.py build --output-dir build/wayfinder
python3 scripts/package_wayfinder.py verify build/wayfinder/*.zip
```

Also validate extracted OpenAI and Claude artifacts with the current host tooling when a host contract or package layout changes.

## Pull requests

- Keep one coherent change per pull request.
- Explain the user-visible behavior, authority impact, compatibility risk, and evidence.
- Do not weaken path, input, origin, capability, CSRF, CAS, atomicity, secret, or package-verification controls without dedicated adversarial proof.
- Preserve existing planning state or provide an explicit, reversible migration.
- Confirm wording is original and retain applicable licenses and notices.
