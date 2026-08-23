from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "wayfinder"


class StandaloneSkillContractTests(unittest.TestCase):
    def test_repository_contains_one_canonical_explicit_only_skill(self) -> None:
        self.assertEqual(
            ["wayfinder"],
            sorted(path.name for path in (ROOT / "skills").iterdir() if path.is_dir()),
        )
        entrypoint = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = entrypoint.split("\n---\n", 1)[0]
        self.assertIn("name: wayfinder", frontmatter)
        self.assertIn("explicitly invoked", frontmatter)
        self.assertNotIn("disable-model-invocation", frontmatter)
        openai = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("allow_implicit_invocation: false", openai)
        self.assertFalse((ROOT / ".claude-plugin").exists())

    def test_intake_and_handoff_contracts_are_domain_neutral_and_durable(self) -> None:
        entrypoint = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        intake = (SKILL / "references" / "intake.md").read_text(encoding="utf-8")
        exit_template = (SKILL / "assets" / "EXIT.md").read_text(encoding="utf-8")
        combined = "\n".join((entrypoint, intake))
        for domain in ("SOFTWARE", "GENERAL_PROJECT", "FINANCE_REPORTING", "OTHER"):
            self.assertIn(domain, combined)
        self.assertIn("Ask one material question at a time", entrypoint)
        self.assertIn("implementation baseline", combined.casefold())
        self.assertIn("## Execution baseline and handoff", exit_template)
        self.assertIn("Manifest hash", exit_template)
        self.assertIn("Applicable Decision revisions", exit_template)

    def test_dashboard_assets_stay_installed_and_out_of_target_projects(self) -> None:
        cli = SKILL / "scripts" / "wayfinder.py"
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            result = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "init",
                    "--root",
                    str(project),
                    "--slug",
                    "privacy-check",
                    "--destination",
                    "Create a bounded synthetic plan",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            state = project / ".codex" / "wayfinder"
            self.assertTrue((state / "ACTIVE").is_file())
            copied_web_assets = [
                path
                for path in state.rglob("*")
                if path.is_file() and path.suffix.casefold() in {".css", ".html", ".js", ".svg"}
            ]
            self.assertEqual([], copied_web_assets)

    def test_public_plugin_and_marketplace_identify_only_wayfinder(self) -> None:
        plugin = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual("wayfinder", plugin["name"])
        self.assertEqual("./skills/", plugin["skills"])
        self.assertEqual(["wayfinder"], [entry["name"] for entry in marketplace["plugins"]])
        self.assertEqual({"source": "local", "path": "."}, marketplace["plugins"][0]["source"])

    def test_source_repository_ignores_local_runtime_state(self) -> None:
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".codex/", ignore)
        self.assertNotIn("!.codex/wayfinder/", ignore)


if __name__ == "__main__":
    unittest.main()
