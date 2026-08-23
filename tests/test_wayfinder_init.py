from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "wayfinder" / "scripts" / "init_wayfinder.py"


class WayfinderInitTests(unittest.TestCase):
    def test_creates_effort_and_active_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, timeout=5)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(repo), "--slug", "offline-sync", "--destination", "Clients converge without data loss."],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            pointer = ".codex/wayfinder/efforts/offline-sync/MAP.md"
            self.assertEqual(pointer + "\n", (repo / ".codex" / "wayfinder" / "ACTIVE").read_text(encoding="utf-8"))
            map_text = (repo / pointer).read_text(encoding="utf-8")
            self.assertIn("Clients converge without data loss.", map_text)
            self.assertTrue((repo / ".codex" / "wayfinder" / "efforts" / "offline-sync" / "decisions").is_dir())

    def test_rejects_path_like_slug_and_existing_effort(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, timeout=5)
            bad = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(repo), "--slug", "../escape", "--destination", "Safe destination"],
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertNotEqual(0, bad.returncode)
            command = [sys.executable, str(SCRIPT), "--root", str(repo), "--slug", "safe", "--destination", "Safe destination"]
            first = subprocess.run(command, capture_output=True, check=False, timeout=5)
            second = subprocess.run(command, capture_output=True, check=False, timeout=5)
            self.assertEqual(0, first.returncode)
            self.assertNotEqual(0, second.returncode)

    @unittest.skipIf(sys.platform == "win32", "secure directory descriptors are unavailable on Windows")
    def test_rejects_symlinked_state_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, timeout=5)
            (repo / ".codex").symlink_to(Path(outside), target_is_directory=True)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(repo), "--slug", "safe", "--destination", "Safe destination"],
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertEqual([], list(Path(outside).iterdir()))


if __name__ == "__main__":
    unittest.main()
