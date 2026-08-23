# Portable package contract

Wayfinder has one authored source tree at [`skills/wayfinder/`](../skills/wayfinder/). The deterministic packager derives host-specific frontmatter and manifests without maintaining a second implementation.

## Why four artifacts exist

OpenAI and Claude Code use compatible Agent Skill structures but different explicit-only metadata. The canonical OpenAI entrypoint uses `agents/openai.yaml` with `allow_implicit_invocation: false`. Generated Claude artifacts add `disable-model-invocation: true` to their `SKILL.md`.

Keeping the adapters separate allows both ecosystems' validators to accept their own artifact while every runtime, reference, asset, and instruction body still comes from the same canonical tree.

## Artifact layouts

```text
wayfinder-openai-skill-0.2.0.zip
├── wayfinder/
│   ├── SKILL.md
│   ├── LICENSE
│   ├── NOTICE.md
│   └── ...canonical skill files
└── WAYFINDER-PACKAGE.json

wayfinder-claude-skill-0.2.0.zip
├── wayfinder/
│   ├── SKILL.md              generated Claude explicit-only frontmatter
│   ├── LICENSE
│   ├── NOTICE.md
│   └── ...canonical skill files
└── WAYFINDER-PACKAGE.json

wayfinder-openai-plugin-0.2.0.zip
├── .agents/plugins/marketplace.json
├── .codex-plugin/plugin.json
├── LICENSE
├── NOTICE.md
├── skills/wayfinder/...
└── WAYFINDER-PACKAGE.json

wayfinder-claude-plugin-0.2.0.zip
├── .claude-plugin/plugin.json
├── LICENSE
├── NOTICE.md
├── skills/wayfinder/...
└── WAYFINDER-PACKAGE.json
```

Each `WAYFINDER-PACKAGE.json` records the format, version, install layout, canonical tree digest, and a sorted inventory containing path, mode, byte count, and SHA-256 for every canonical and generated file.

## Determinism and verification

```bash
python3 scripts/package_wayfinder.py build --output-dir build/wayfinder
python3 scripts/package_wayfinder.py verify build/wayfinder/*.zip
```

Unchanged source under the same Python implementation produces byte-identical archives. ZIP timestamps, compression method, entry ordering, permissions, manifests, and JSON rendering are fixed.

Verification rejects:

- missing, extra, duplicate, absolute, traversal, drive-letter, or ambiguous paths;
- symlinks and non-regular archive entries;
- content, size, mode, inventory, or tree-digest drift;
- non-deterministic ZIP metadata;
- malformed or duplicate-key package manifests;
- private state, credential/key filenames, secret-like content, ambiguous binaries, and oversized source files;
- unsafe diagnostics that could reflect attacker-controlled archive labels or secret values.

Every archive contains the repository's exact `LICENSE` and `NOTICE.md` bytes.

## Source boundaries

The package contains only the Wayfinder skill and required host adapter files. It excludes:

- `.git`, `.codex`, `.claude`, `.cursor`, and runtime session state;
- tests, contributor tooling, repository workflows, and generated build output;
- caches, logs, transcripts, package-manager state, and unrelated skills;
- hooks, MCP servers, apps, network dependencies, and install scripts.

Dashboard HTML, CSS, JavaScript, and icons remain installed skill assets. They are served over a temporary loopback origin and are never copied into the target project.

## Release rule

Release artifacts must be built from the exact tagged commit after all repository, test, security, host-validation, reproducibility, and clean-install gates pass. Publish the four ZIPs with `SHA256SUMS.txt`; never hand-edit an archive after build.
