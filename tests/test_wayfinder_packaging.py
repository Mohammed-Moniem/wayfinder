from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "package_wayfinder.py"
RELEASE_VERSION = "0.2.0"


def load_packager():
    spec = importlib.util.spec_from_file_location("package_wayfinder", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load Wayfinder packager")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


packager = load_packager()


class WayfinderPackagingTests(unittest.TestCase):
    def test_install_contract_uses_current_paths_and_release_identity(self) -> None:
        portable = (ROOT / "docs" / "PORTABLE-WAYFINDER.md").read_text(encoding="utf-8")
        installation = (ROOT / "docs" / "INSTALLATION.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        combined = "\n".join((portable, installation, readme))

        self.assertIn('$HOME/.agents/skills', combined)
        self.assertIn('$HOME/.claude/skills', combined)
        self.assertIn("codex plugin add wayfinder@wayfinder-local", combined)
        self.assertIn("claude --plugin-dir ./wayfinder-claude-plugin-0.2.0.zip", combined)
        self.assertIn("wayfinder-openai-skill-0.2.0.zip", combined)
        self.assertIn("wayfinder-claude-skill-0.2.0.zip", combined)
        self.assertIn("wayfinder-openai-plugin-0.2.0.zip", combined)
        self.assertIn("wayfinder-claude-plugin-0.2.0.zip", combined)
        self.assertIn("Claude Code 2.1.128 or newer", combined)
        self.assertIn("Python 3.11 or newer", combined)
        self.assertIn("Codex IDE extension", combined)
        self.assertIn("does not list plugin installs", combined)
        self.assertIn("Mohammed-Moniem/wayfinder", combined)
        self.assertIn(RELEASE_VERSION, combined)
        self.assertNotIn("$HOME/.codex/skills", combined)
        self.assertNotIn("~/.codex/skills", combined)
        self.assertNotIn("0.2.0-" + "dev.1", combined)
        self.assertNotIn("codex-" + "engineering-system", combined)

    def test_release_identity_is_consistent_and_valid_semver(self) -> None:
        codex = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(RELEASE_VERSION, codex["version"])
        self.assertEqual("wayfinder", codex["name"])
        self.assertEqual("https://github.com/Mohammed-Moniem/wayfinder", codex["repository"])
        self.assertIsNotNone(packager.SEMVER.fullmatch(RELEASE_VERSION))

    def test_python_311_floor_is_enforced_before_runtime_work(self) -> None:
        with self.assertRaisesRegex(packager.PackageError, "Python 3.11 or newer"):
            packager.require_supported_python((3, 10))
        packager.require_supported_python((3, 11))

        for filename in ("wayfinder.py", "init_wayfinder.py"):
            with self.subTest(runtime=filename):
                runtime = ROOT / "skills" / "wayfinder" / "scripts" / filename
                probe = (
                    "import runpy, sys; "
                    "sys.version_info = (3, 10, 0); "
                    f"runpy.run_path({str(runtime)!r}, run_name='wayfinder_version_probe')"
                )
                result = subprocess.run(
                    [sys.executable, "-c", probe],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("Python 3.11 or newer is required", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_generated_claude_manifest_uses_explicit_only_adapter_without_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = packager.build_packages(Path(directory), ("claude-plugin",))[0]
            with zipfile.ZipFile(archive_path) as archive:
                manifest = json.loads(archive.read(".claude-plugin/plugin.json"))
                entrypoint = archive.read("skills/wayfinder/SKILL.md").decode("utf-8")
                self.assertEqual("wayfinder", manifest["name"])
                self.assertEqual(RELEASE_VERSION, manifest["version"])
                self.assertEqual("./skills/", manifest["skills"])
                self.assertNotIn("hooks", manifest)
                self.assertIn("disable-model-invocation: true", entrypoint)

    def test_build_is_byte_deterministic_and_verifiable(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_paths = packager.build_packages(Path(first), packager.PACKAGE_FORMATS)
            second_paths = packager.build_packages(Path(second), packager.PACKAGE_FORMATS)
            self.assertEqual([path.name for path in first_paths], [path.name for path in second_paths])
            for left, right in zip(first_paths, second_paths):
                with self.subTest(archive=left.name):
                    self.assertEqual(left.read_bytes(), right.read_bytes())
                    verified = packager.verify_archive(left)
                    self.assertEqual(64, len(verified["sha256"]))
                    self.assertEqual(64, len(verified["tree_sha256"]))

    def test_host_specific_skill_and_plugin_archives_are_validly_separated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            openai_archive, claude_archive, openai_plugin, claude_plugin = packager.build_packages(
                Path(directory), packager.PACKAGE_FORMATS
            )
            with zipfile.ZipFile(openai_archive) as archive:
                names = set(archive.namelist())
                self.assertIn("wayfinder/SKILL.md", names)
                self.assertIn("wayfinder/LICENSE", names)
                self.assertIn("wayfinder/NOTICE.md", names)
                self.assertNotIn(
                    "disable-model-invocation",
                    archive.read("wayfinder/SKILL.md").decode("utf-8"),
                )
                self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))
            with zipfile.ZipFile(claude_archive) as archive:
                names = set(archive.namelist())
                self.assertIn("wayfinder/SKILL.md", names)
                self.assertIn("wayfinder/LICENSE", names)
                self.assertIn("wayfinder/NOTICE.md", names)
                self.assertIn(
                    "disable-model-invocation: true",
                    archive.read("wayfinder/SKILL.md").decode("utf-8"),
                )
            with zipfile.ZipFile(openai_plugin) as archive:
                names = set(archive.namelist())
                self.assertIn("skills/wayfinder/SKILL.md", names)
                self.assertIn(".codex-plugin/plugin.json", names)
                self.assertNotIn(".claude-plugin/plugin.json", names)
                self.assertIn(".agents/plugins/marketplace.json", names)
                self.assertIn("LICENSE", names)
                self.assertIn("NOTICE.md", names)
                codex = json.loads(archive.read(".codex-plugin/plugin.json"))
                marketplace = json.loads(archive.read(".agents/plugins/marketplace.json"))
                package = json.loads(archive.read(packager.PACKAGE_MANIFEST_NAME))
                self.assertEqual("./skills/", codex["skills"])
                self.assertEqual(RELEASE_VERSION, codex["version"])
                self.assertEqual("wayfinder-local", marketplace["name"])
                self.assertEqual("wayfinder", marketplace["plugins"][0]["name"])
                self.assertEqual(
                    {"source": "local", "path": "."},
                    marketplace["plugins"][0]["source"],
                )
                self.assertEqual(
                    {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                    marketplace["plugins"][0]["policy"],
                )
                self.assertEqual("Developer Tools", marketplace["plugins"][0]["category"])
                self.assertEqual(RELEASE_VERSION, package["version"])
                self.assertEqual("wayfinder-local", package["codex_marketplace"])
                self.assertEqual(
                    [
                        ".agents/plugins/marketplace.json",
                        ".codex-plugin/plugin.json",
                        "LICENSE",
                        "NOTICE.md",
                    ],
                    [item["path"] for item in package["generated_files"]],
                )
                for item in package["generated_files"]:
                    with self.subTest(generated=item["path"]):
                        info = archive.getinfo(item["path"])
                        self.assertEqual(item["bytes"], info.file_size)
                        self.assertEqual(item["sha256"], packager._sha256(archive.read(info)))
                        self.assertEqual(item["mode"], f"{(info.external_attr >> 16) & 0o777:04o}")
            with zipfile.ZipFile(claude_plugin) as archive:
                names = set(archive.namelist())
                self.assertIn("skills/wayfinder/SKILL.md", names)
                self.assertIn(".claude-plugin/plugin.json", names)
                self.assertNotIn(".codex-plugin/plugin.json", names)
                self.assertNotIn(".agents/plugins/marketplace.json", names)
                self.assertIn("LICENSE", names)
                self.assertIn("NOTICE.md", names)
                entrypoint = archive.read("skills/wayfinder/SKILL.md").decode("utf-8")
                manifest = json.loads(archive.read(".claude-plugin/plugin.json"))
                package = json.loads(archive.read(packager.PACKAGE_MANIFEST_NAME))
                self.assertIn("disable-model-invocation: true", entrypoint)
                self.assertEqual("./skills/", manifest["skills"])
                self.assertNotIn("codex_marketplace", package)
                self.assertEqual(
                    [
                        ".claude-plugin/plugin.json",
                        "LICENSE",
                        "NOTICE.md",
                        "skills/wayfinder/SKILL.md",
                    ],
                    [item["path"] for item in package["generated_files"]],
                )

    def test_every_archive_contains_exact_public_license_and_notice(self) -> None:
        license_bytes = (ROOT / "LICENSE").read_bytes()
        notice_bytes = (ROOT / "NOTICE.md").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            for archive_path in packager.build_packages(Path(directory), packager.PACKAGE_FORMATS):
                with self.subTest(archive=archive_path.name), zipfile.ZipFile(archive_path) as archive:
                    prefix = "wayfinder/" if "-skill-" in archive_path.name else ""
                    self.assertEqual(license_bytes, archive.read(prefix + "LICENSE"))
                    self.assertEqual(notice_bytes, archive.read(prefix + "NOTICE.md"))

    def test_extracted_archives_have_installable_host_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            extracted_openai = Path(directory) / "openai"
            extracted_claude = Path(directory) / "claude"
            extracted_openai_plugin = Path(directory) / "openai-plugin"
            extracted_claude_plugin = Path(directory) / "claude-plugin"
            openai_archive, claude_archive, openai_plugin, claude_plugin = packager.build_packages(
                output, packager.PACKAGE_FORMATS
            )
            with zipfile.ZipFile(openai_archive) as archive:
                archive.extractall(extracted_openai)
            with zipfile.ZipFile(claude_archive) as archive:
                archive.extractall(extracted_claude)
            with zipfile.ZipFile(openai_plugin) as archive:
                archive.extractall(extracted_openai_plugin)
            with zipfile.ZipFile(claude_plugin) as archive:
                archive.extractall(extracted_claude_plugin)

            self.assertTrue((extracted_openai / "wayfinder" / "SKILL.md").is_file())
            self.assertTrue((extracted_claude / "wayfinder" / "SKILL.md").is_file())
            self.assertTrue((extracted_openai_plugin / "skills" / "wayfinder" / "SKILL.md").is_file())
            self.assertTrue((extracted_claude_plugin / "skills" / "wayfinder" / "SKILL.md").is_file())
            self.assertTrue((extracted_openai_plugin / ".codex-plugin" / "plugin.json").is_file())
            self.assertTrue((extracted_claude_plugin / ".claude-plugin" / "plugin.json").is_file())
            marketplace_path = extracted_openai_plugin / ".agents" / "plugins" / "marketplace.json"
            marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
            source = marketplace["plugins"][0]["source"]
            self.assertEqual("local", source["source"])
            self.assertEqual(
                extracted_openai_plugin.resolve(),
                (extracted_openai_plugin / source["path"]).resolve(),
            )

    def test_archive_contains_only_real_files_with_canonical_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = packager.build_packages(Path(directory), ("openai-plugin",))[0]
            with zipfile.ZipFile(archive_path) as archive:
                package = json.loads(archive.read(packager.PACKAGE_MANIFEST_NAME))
                inventory = package["canonical"]["files"]
                for item in inventory:
                    name = "skills/wayfinder/" + item["path"]
                    info = archive.getinfo(name)
                    file_type = (info.external_attr >> 16) & 0o170000
                    self.assertEqual(stat.S_IFREG, file_type)
                    self.assertEqual(item["sha256"], packager._sha256(archive.read(name)))

    @unittest.skipIf(sys.platform == "win32", "symlink construction differs on Windows")
    def test_packager_rejects_symlinked_source_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            (root / "SKILL.md").write_text("---\nname: wayfinder\ndescription: Safe\n---\nSafe\n", encoding="utf-8")
            secret = Path(outside) / "secret.txt"
            secret.write_text("not packaged", encoding="utf-8")
            (root / "escape.txt").symlink_to(secret)
            with self.assertRaises(packager.PackageError):
                packager.collect_skill_files(root)

    def test_packager_omits_runtime_cache_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SKILL.md").write_text("---\nname: wayfinder\ndescription: Safe\n---\nSafe\n", encoding="utf-8")
            cache = root / "scripts" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "helper.cpython-311.pyc").write_bytes(b"cache")
            self.assertEqual(["SKILL.md"], [item.path.as_posix() for item in packager.collect_skill_files(root)])

    def test_packager_rejects_private_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SKILL.md").write_text("---\nname: wayfinder\ndescription: Safe\n---\nSafe\n", encoding="utf-8")
            (root / ".env").write_text("TOKEN=not-packaged\n", encoding="utf-8")
            with self.assertRaises(packager.PackageError):
                packager.collect_skill_files(root)

    def test_packager_rejects_keys_sessions_and_secrets_without_reflection(self) -> None:
        private_key = "-----BEGIN " + "OPENSSH " + "PRIVATE KEY-----\n" + "A" * 48
        pgp_key = "-----BEGIN " + "PGP " + "PRIVATE KEY BLOCK-----\n" + "B" * 48
        encrypted_key = "-----BEGIN " + "ENCRYPTED " + "PRIVATE KEY-----\n" + "C" * 48
        github_token = "gh" + "p_" + "Ab3dEf5h" + "J7kLm9Np" + "2Qr4St6V" + "w8Xy0Za1"
        assigned_secret = "".join(("aB3dE5fG", "7hJ9kL2m", "N4pQ6rS8", "tV0xY1zC"))
        opaque_name = "credentials-" + "".join(("aB3dE5fG", "7hJ9kL2m", "N4pQ6rS8")) + ".txt"
        cases = (
            ("identity file", Path("id_rsa"), "harmless fixture text", None),
            ("opaque credential filename", Path(opaque_name), "harmless fixture text", opaque_name),
            ("session state", Path("sessions/state.json"), "{}", None),
            ("private key content", Path("notes.txt"), private_key, private_key),
            ("PGP private key content", Path("pgp-notes.txt"), pgp_key, pgp_key),
            ("encrypted private key content", Path("encrypted-notes.txt"), encrypted_key, encrypted_key),
            ("token content", Path("release-notes.txt"), github_token, github_token),
            (
                "credential assignment",
                Path("configuration.txt"),
                f"api_key = {assigned_secret}\n",
                assigned_secret,
            ),
            ("secret path", Path(github_token), "harmless fixture text", github_token),
        )
        for label, relative, content, prohibited in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "SKILL.md").write_text(
                    "---\nname: wayfinder\ndescription: Safe\n---\nSafe\n",
                    encoding="utf-8",
                )
                candidate = root / relative
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_text(content, encoding="utf-8")
                with self.assertRaises(packager.PackageError) as raised:
                    packager.collect_skill_files(root)
                if prohibited is not None:
                    self.assertNotIn(prohibited, str(raised.exception))

    def test_packager_allows_documented_placeholders_but_rejects_binary_and_oversize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SKILL.md").write_text(
                "---\nname: wayfinder\ndescription: Safe\n---\nSafe\n",
                encoding="utf-8",
            )
            (root / "example.txt").write_text(
                "api_key = YOUR_EXAMPLE_CREDENTIAL_VALUE\n",
                encoding="utf-8",
            )
            self.assertEqual(
                ["SKILL.md", "example.txt"],
                [item.path.as_posix() for item in packager.collect_skill_files(root)],
            )

            (root / "ambiguous.bin").write_bytes(b"safe-prefix\x00hidden")
            with self.assertRaisesRegex(packager.PackageError, "ambiguous binary content"):
                packager.collect_skill_files(root)
            (root / "ambiguous.bin").unlink()

            (root / "oversize.txt").write_bytes(b"A" * (packager.MAX_SOURCE_FILE_BYTES + 1))
            with self.assertRaisesRegex(packager.PackageError, "exceeds the .* package limit"):
                packager.collect_skill_files(root)

    def test_verifier_rejects_tampering_and_unsafe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = list(packager.expected_entries("openai-skill"))
            original = expected[0]
            expected[0] = packager.ArchiveEntry(original.path, original.data + b"tampered", original.mode)
            tampered = root / "tampered.zip"
            packager._write_archive(tampered, expected, force=False)
            with self.assertRaises(packager.PackageError):
                packager.verify_archive(tampered)

            unsafe = root / "unsafe.zip"
            info = zipfile.ZipInfo("../escape.txt", packager.ARCHIVE_TIMESTAMP)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            with zipfile.ZipFile(unsafe, "w") as archive:
                archive.writestr(info, b"escape")
            with self.assertRaises(packager.PackageError):
                packager.verify_archive(unsafe)

            secret_name = "gh" + "p_" + "Ab3dEf5h" + "J7kLm9Np" + "2Qr4St6V" + "w8Xy0Za1"
            hostile = root / "hostile.zip"
            info = zipfile.ZipInfo(secret_name, packager.ARCHIVE_TIMESTAMP)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            with zipfile.ZipFile(hostile, "w") as archive:
                archive.writestr(info, b"not reflected")
            with self.assertRaises(packager.PackageError) as raised:
                packager.verify_archive(hostile)
            self.assertNotIn(secret_name, str(raised.exception))

            opaque_name = "extra-" + "".join(("aB3dE5fG", "7hJ9kL2m", "N4pQ6rS8")) + ".txt"
            opaque = root / "opaque.zip"
            info = zipfile.ZipInfo(opaque_name, packager.ARCHIVE_TIMESTAMP)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            with zipfile.ZipFile(opaque, "w") as archive:
                archive.writestr(info, b"not reflected")
            with self.assertRaises(packager.PackageError) as raised:
                packager.verify_archive(opaque)
            self.assertNotIn(opaque_name, str(raised.exception))

    def test_verifier_rejects_hostile_json_and_zip_errors_without_reflection(self) -> None:
        secret_key = "gh" + "p_" + "Ab3dEf5h" + "J7kLm9Np" + "2Qr4St6V" + "w8Xy0Za1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = list(packager.expected_entries("openai-skill"))
            for index, entry in enumerate(entries):
                if entry.path.as_posix() == packager.PACKAGE_MANIFEST_NAME:
                    hostile_json = (
                        '{"package_format":"openai-skill",'
                        f'"{secret_key}":1,"{secret_key}":2}}\n'
                    ).encode("utf-8")
                    entries[index] = packager.ArchiveEntry(entry.path, hostile_json, entry.mode)
                    break
            else:  # pragma: no cover - the package contract always emits the manifest
                self.fail("package manifest entry was not generated")

            hostile_manifest = root / "hostile-manifest.zip"
            packager._write_archive(hostile_manifest, entries, force=False)
            with self.assertRaises(packager.PackageError) as raised:
                packager.verify_archive(hostile_manifest)
            self.assertNotIn(secret_key, str(raised.exception))
            self.assertIn("duplicate JSON key", str(raised.exception))

            hostile_filename = root / f"{secret_key}.zip"
            hostile_filename.write_bytes(b"not a zip archive")
            with self.assertRaises(packager.PackageError) as raised:
                packager.verify_archive(hostile_filename)
            self.assertNotIn(secret_key, str(raised.exception))
            self.assertEqual(
                "invalid archive: unreadable or malformed ZIP",
                str(raised.exception),
            )

    def test_packager_rejects_secret_like_release_metadata_without_reflection(self) -> None:
        marker = "gh" + "p_" + "Ab3dEf5h" + "J7kLm9Np" + "2Qr4St6V" + "w8Xy0Za1"
        with tempfile.TemporaryDirectory() as directory:
            manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
            manifest["author"]["url"] = "https://example.invalid/" + marker
            path = Path(directory) / "plugin.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            original = packager.CODEX_MANIFEST
            packager.CODEX_MANIFEST = path
            try:
                with self.assertRaises(packager.PackageError) as raised:
                    packager.expected_entries("openai-plugin")
            finally:
                packager.CODEX_MANIFEST = original
            self.assertNotIn(marker, str(raised.exception))

    def test_cli_build_and_verify(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            build = subprocess.run(
                [sys.executable, str(SCRIPT), "build", "--output-dir", directory],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
            self.assertEqual(0, build.returncode, build.stdout + build.stderr)
            archives = sorted(Path(directory).glob("*.zip"))
            self.assertEqual(4, len(archives))
            verify = subprocess.run(
                [sys.executable, str(SCRIPT), "verify", *(str(path) for path in archives)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
            self.assertEqual(0, verify.returncode, verify.stdout + verify.stderr)
            self.assertEqual(4, len(verify.stdout.strip().splitlines()))


if __name__ == "__main__":
    unittest.main()
