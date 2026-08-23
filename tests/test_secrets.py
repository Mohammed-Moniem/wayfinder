from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


def load_scanner():
    path = ROOT / "scripts" / "scan_secrets.py"
    spec = importlib.util.spec_from_file_location("scan_secrets", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load secret scanner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scanner = load_scanner()


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=5)


class SecretScannerTests(unittest.TestCase):
    def test_detects_dynamic_fake_token_and_redacts_report(self) -> None:
        token = "gh" + "p_" + "A1b2" * 9
        found = scanner.scan_records([("fixture.txt", token.encode())], "test")
        self.assertEqual(1, len(found))
        self.assertNotIn(token, found[0])

    def test_detects_fine_grained_github_token_and_redacts_report(self) -> None:
        token = "github" + "_pat_" + "A1b2" * 20
        found = scanner.scan_records([("fixture.txt", token.encode())], "test")
        self.assertEqual(1, len(found))
        self.assertNotIn(token, found[0])

    def test_allows_explicit_placeholder(self) -> None:
        text = 'api_key="YOUR_TOKEN_VALUE_PLACEHOLDER"'
        self.assertEqual([], scanner.findings(text))

    def test_detects_private_key_header_variants(self) -> None:
        headers = (
            "-----BEGIN " + "DSA " + "PRIVATE KEY-----",
            "-----BEGIN " + "PGP " + "PRIVATE KEY BLOCK-----",
            "-----BEGIN " + "ENCRYPTED " + "PRIVATE KEY-----",
        )
        for header in headers:
            with self.subTest(header=header):
                found = scanner.findings(header)
                self.assertTrue(any(kind == "private-key" for kind, _ in found))

    def test_scans_mixed_bytes_and_unquoted_assignments(self) -> None:
        token = "gh" + "p_" + "Q7r6" * 9
        records = [("mixed.bin", b"\x00\xffprefix" + token.encode())]
        reports = scanner.scan_records(records, "test")
        self.assertTrue(reports)
        self.assertNotIn(token, "\n".join(reports))
        self.assertTrue(scanner.findings("api_key=" + "A1b2C3d4E5f6G7h8" * 2))

    def test_scans_common_provider_and_utf16_forms(self) -> None:
        aws_id = "ASIA" + "A1B2C3D4E5F6G7H8"
        stripe = "sk" + "_live_" + "A1b2C3d4E5f6G7h8I9j0"
        compound = "AWS_SECRET_ACCESS_KEY=" + "A1b2C3d4E5f6G7h8" * 2
        text = f"{aws_id}\n{stripe}\n{compound}\n"
        reports = scanner.scan_records([("windows.txt", text.encode("utf-16-le"))], "test")
        self.assertGreaterEqual(len(reports), 3)
        self.assertNotIn(aws_id, "\n".join(reports))
        self.assertNotIn(stripe, "\n".join(reports))

    def test_fallback_does_not_follow_external_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            token = "gh" + "p_" + "S3t4" * 9
            external = Path(outside) / "secret.txt"
            external.write_text(token, encoding="utf-8")
            (root / "linked.txt").symlink_to(external)
            reports = scanner.scan_records(scanner.worktree_files(root), "worktree")
            self.assertEqual([], reports)

    def test_oversized_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "large.txt"
            path.write_bytes(b"x" * 17)
            previous = scanner.MAX_FILE
            scanner.MAX_FILE = 16
            try:
                with self.assertRaises(RuntimeError):
                    list(scanner.worktree_files(root))
            finally:
                scanner.MAX_FILE = previous

    def test_oversized_error_redacts_secret_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = "gh" + "p_" + "R7s8" * 9
            (root / f"{token}.txt").write_bytes(b"x" * 17)
            previous = scanner.MAX_FILE
            scanner.MAX_FILE = 16
            try:
                with self.assertRaises(RuntimeError) as caught:
                    list(scanner.worktree_files(root))
                self.assertNotIn(token, str(caught.exception))
                self.assertIn("<redacted-path:", str(caught.exception))
            finally:
                scanner.MAX_FILE = previous

    def test_scans_staged_blob_when_worktree_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git(repo, "init", "-q", "-b", "main")
            token = "gh" + "p_" + "I5j6" * 9
            path = repo / "staged.txt"
            path.write_text(token + "\n", encoding="utf-8")
            git(repo, "add", "staged.txt")
            path.write_text("safe worktree content\n", encoding="utf-8")
            reports = scanner.scan_records(scanner.index_records(repo), "index")
            self.assertTrue(reports)
            self.assertNotIn(token, "\n".join(reports))

    def test_detects_and_redacts_secret_in_path_label(self) -> None:
        token = "gh" + "p_" + "P3q4" * 9
        reports = scanner.scan_records([(f"fixtures/{token}.txt", b"safe")], "test")
        self.assertTrue(reports)
        self.assertNotIn(token, "\n".join(reports))
        self.assertIn("<redacted-path:", reports[0])

    def test_history_finds_deleted_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.name", "Example User")
            git(repo, "config", "user.email", "example@example.com")
            token = "gh" + "p_" + "Z9y8" * 9
            path = repo / "temporary.txt"
            path.write_text(token + "\n", encoding="utf-8")
            git(repo, "add", "temporary.txt")
            git(repo, "commit", "-q", "-m", "add fixture")
            path.unlink()
            git(repo, "add", "-u")
            git(repo, "commit", "-q", "-m", "remove fixture")

            current = scanner.scan_records(scanner.worktree_files(repo), "worktree")
            history = scanner.scan_records(scanner.history_records(repo), "history")
            self.assertEqual([], current)
            self.assertTrue(history)
            self.assertNotIn(token, "\n".join(history))

    def test_history_scans_commit_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.name", "Example User")
            git(repo, "config", "user.email", "example@example.com")
            (repo / "file.txt").write_text("safe\n", encoding="utf-8")
            git(repo, "add", "file.txt")
            token = "gh" + "p_" + "C5d6" * 9
            git(repo, "commit", "-q", "-m", token)
            history = scanner.scan_records(scanner.history_records(repo), "history")
            self.assertTrue(history)
            self.assertNotIn(token, "\n".join(history))

    def test_history_scans_every_path_for_shared_blobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.name", "Example User")
            git(repo, "config", "user.email", "example@example.com")
            token = "gh" + "p_" + "H7j8" * 9
            safe = repo / "safe.txt"
            sensitive = repo / f"{token}.txt"
            safe.write_text("identical\n", encoding="utf-8")
            sensitive.write_text("identical\n", encoding="utf-8")
            git(repo, "add", "safe.txt", sensitive.name)
            git(repo, "commit", "-q", "-m", "add shared fixture")
            sensitive.unlink()
            git(repo, "add", "-u")
            git(repo, "commit", "-q", "-m", "remove sensitive alias")

            reports = scanner.scan_records(scanner.history_records(repo), "history")
            self.assertTrue(reports)
            self.assertNotIn(token, "\n".join(reports))
            self.assertTrue(any("path-github-token" in report for report in reports))

    def test_history_scans_reference_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.name", "Example User")
            git(repo, "config", "user.email", "example@example.com")
            (repo / "safe.txt").write_text("safe\n", encoding="utf-8")
            git(repo, "add", "safe.txt")
            git(repo, "commit", "-q", "-m", "safe commit")
            token = "gh" + "p_" + "K3m4" * 9
            git(repo, "tag", token)

            reports = scanner.scan_records(scanner.history_records(repo), "history")
            self.assertTrue(any("path-github-token" in report for report in reports))
            self.assertNotIn(token, "\n".join(reports))

    def test_history_detects_fine_grained_token_in_deleted_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.name", "Example User")
            git(repo, "config", "user.email", "example@example.com")
            token = "github" + "_pat_" + "B2c3" * 20
            path = repo / f"{token}.txt"
            path.write_text("safe\n", encoding="utf-8")
            git(repo, "add", path.name)
            git(repo, "commit", "-q", "-m", "add fixture")
            path.unlink()
            git(repo, "add", "-u")
            git(repo, "commit", "-q", "-m", "remove fixture")

            reports = scanner.scan_records(scanner.history_records(repo), "history")
            self.assertTrue(any("path-github-fine-grained-token" in report for report in reports))
            self.assertNotIn(token, "\n".join(reports))

    def test_history_refuses_replacement_refs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.name", "Example User")
            git(repo, "config", "user.email", "example@example.com")
            path = repo / "file.txt"
            path.write_text("first\n", encoding="utf-8")
            git(repo, "add", "file.txt")
            git(repo, "commit", "-q", "-m", "first")
            first = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True, timeout=5
            ).stdout.strip()
            path.write_text("second\n", encoding="utf-8")
            git(repo, "add", "file.txt")
            git(repo, "commit", "-q", "-m", "second")
            second = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True, timeout=5
            ).stdout.strip()
            git(repo, "replace", first, second)

            with self.assertRaisesRegex(RuntimeError, "replacement references"):
                list(scanner.history_records(repo))

    def test_history_refuses_grafts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.name", "Example User")
            git(repo, "config", "user.email", "example@example.com")
            (repo / "file.txt").write_text("safe\n", encoding="utf-8")
            git(repo, "add", "file.txt")
            git(repo, "commit", "-q", "-m", "safe")
            graft = repo / ".git" / "info" / "grafts"
            graft.parent.mkdir(parents=True, exist_ok=True)
            graft.write_text("fixture\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "grafts"):
                list(scanner.history_records(repo))

    def test_oversized_history_object_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.name", "Example User")
            git(repo, "config", "user.email", "example@example.com")
            (repo / "large.txt").write_bytes(b"x" * 512)
            git(repo, "add", "large.txt")
            git(repo, "commit", "-q", "-m", "add large fixture")
            previous = scanner.MAX_FILE
            scanner.MAX_FILE = 256
            try:
                with self.assertRaises(RuntimeError):
                    list(scanner.history_records(repo))
            finally:
                scanner.MAX_FILE = previous


if __name__ == "__main__":
    unittest.main()
