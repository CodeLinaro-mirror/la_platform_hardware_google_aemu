# Copyright 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for emu-dev-cli version inspection, persistence, and CLI --version."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lib.version import (
    SHORT_COMMIT_LEN,
    UNKNOWN_COMMIT,
    VERSION_FILENAME,
    compare_versions,
    format_version_display,
    inspect_source_version,
    read_installed_version,
    resolve_emu_dev_cli_git_dir,
    write_installed_version,
)


def _init_git_repo(repo_dir: Path, subject: str = "feat: Initial commit") -> str:
    """Initializes a local git repository with one commit and returns its SHA."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    subprocess.run(
        ["git", "init"],
        cwd=repo_dir,
        env=env,
        check=True,
        capture_output=True,
    )
    (repo_dir / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=repo_dir,
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", subject],
        cwd=repo_dir,
        env=env,
        check=True,
        capture_output=True,
    )
    res = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return res.stdout.strip()


class VersionTest(unittest.TestCase):
    """Tests for `src/lib/version.py` and `emu-dev-cli --version`."""

    def test_write_and_read_version_json_in_git_repo(self):
        """Verifies creating and reading version.json with clean and dirty states."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            repo_dir = root / "workspace_测试_🚀_é" / "hardware" / "google" / "aemu"
            cli_dir = repo_dir / "tools" / "emu-dev-cli"
            cli_dir.mkdir(parents=True, exist_ok=True)
            commit_sha = _init_git_repo(
                repo_dir, subject="feat(cli): Support 测试_🚀_é paths"
            )

            workspace_root = root / "workspace_测试_🚀_é"
            self.assertEqual(resolve_emu_dev_cli_git_dir(workspace_root), cli_dir)

            release_dir = root / "release"
            written = write_installed_version(
                release_dir,
                source_dir=workspace_root,
                installed_at="2026-10-26T22:00:00+00:00",
            )
            self.assertEqual(written["commit"], commit_sha)
            self.assertEqual(written["commit_short"], commit_sha[:SHORT_COMMIT_LEN])
            self.assertEqual(
                written["commit_subject"], "feat(cli): Support 测试_🚀_é paths"
            )
            self.assertTrue(written["commit_date"])
            self.assertFalse(written["dirty"])
            self.assertEqual(written["source_dir"], str(workspace_root))
            self.assertEqual(written["installed_at"], "2026-10-26T22:00:00+00:00")

            version_file = release_dir / VERSION_FILENAME
            self.assertTrue(version_file.is_file())

            loaded = read_installed_version(release_dir)
            self.assertEqual(loaded, written)

            # Modify working tree and verify dirty detection
            (repo_dir / "untracked.txt").write_text("dirty\n", encoding="utf-8")
            dirty_info = inspect_source_version(workspace_root)
            self.assertTrue(dirty_info["dirty"])
            self.assertEqual(dirty_info["commit"], commit_sha)

    def test_non_git_directory_and_corrupt_version_json(self):
        """Verifies fallback to 'unknown' outside git repos and resilience to corrupt JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            non_git = root / "plain_dir"
            non_git.mkdir()

            inspected = inspect_source_version(non_git)
            self.assertEqual(inspected["commit"], UNKNOWN_COMMIT)
            self.assertEqual(inspected["commit_short"], UNKNOWN_COMMIT)
            self.assertFalse(inspected["dirty"])

            release_dir = root / "release"
            self.assertIsNone(read_installed_version(release_dir))

            release_dir.mkdir(parents=True)
            (release_dir / VERSION_FILENAME).write_text("{corrupt", encoding="utf-8")
            self.assertIsNone(read_installed_version(release_dir))

    def test_compare_versions_matrix(self):
        """Verifies compare_versions across identical, different, dirty, and missing records."""
        clean_v1 = {
            "commit": "1111111122222222333333334444444455555555",
            "commit_short": "11111111",
            "commit_subject": "feat: First commit",
            "dirty": False,
        }
        clean_v2 = {
            "commit": "aaaaaaaa99999999888888887777777766666666",
            "commit_short": "aaaaaaaa",
            "commit_subject": "feat: Second commit",
            "dirty": False,
        }

        # 1. Missing installed version -> needed
        needed, reason = compare_versions(None, clean_v1)
        self.assertTrue(needed)
        self.assertEqual(reason, "no previous version record found")

        # 2. Clean identical commit -> NOT needed
        needed, reason = compare_versions(clean_v1, dict(clean_v1))
        self.assertFalse(needed)
        self.assertEqual(
            reason, "emu-dev-cli is already at commit 11111111 (feat: First commit)"
        )

        # 3. Different commit -> needed
        needed, reason = compare_versions(clean_v1, clean_v2)
        self.assertTrue(needed)
        self.assertEqual(
            reason, "updating from 11111111 to aaaaaaaa (feat: Second commit)"
        )

        # 4. Same commit, dirty source -> needed
        dirty_source = {**clean_v1, "dirty": True}
        needed, reason = compare_versions(clean_v1, dirty_source)
        self.assertTrue(needed)
        self.assertEqual(reason, "local changes detected in source directory")

        # 5. Same commit, previously dirty install -> needed
        dirty_installed = {**clean_v1, "dirty": True}
        needed, reason = compare_versions(dirty_installed, clean_v1)
        self.assertTrue(needed)
        self.assertIn("uncommitted local changes", reason)

        # 6. Unknown commit -> needed
        unknown = {"commit": UNKNOWN_COMMIT, "commit_short": UNKNOWN_COMMIT, "dirty": False}
        needed, _ = compare_versions(unknown, unknown)
        self.assertTrue(needed)

    def test_main_version_flag_text_and_json(self):
        """Verifies `emu-dev-cli --version` and `emu-dev-cli --json --version`."""
        import importlib.util

        main_path = Path(__file__).resolve().parent.parent / "src" / "__main__.py"
        spec = importlib.util.spec_from_file_location("emu_dev_cli_main", main_path)
        self.assertIsNotNone(spec)
        cli_main = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cli_main)

        with tempfile.TemporaryDirectory() as tmpdir:
            release_dir = Path(tmpdir).resolve() / "release"
            # Missing version.json
            out_missing = io.StringIO()
            with patch(
                "install.installer.get_release_package_directory",
                return_value=str(release_dir),
            ), patch.object(sys, "argv", ["emu-dev-cli", "--version"]), patch(
                "sys.stdout", out_missing
            ), self.assertRaises(
                SystemExit
            ) as cm:
                cli_main.main()
            self.assertEqual(cm.exception.code, 0)
            self.assertIn("version unknown", out_missing.getvalue())

            # Populate version.json
            write_installed_version(
                release_dir,
                source_version={
                    "commit": "abcdef0123456789abcdef0123456789abcdef01",
                    "commit_short": "abcdef01",
                    "commit_date": "2026-10-26T20:00:00+00:00",
                    "commit_subject": "feat(emu-dev-cli): Add update version check",
                    "dirty": False,
                    "source_dir": "/work/emu-main-next",
                },
                installed_at="2026-10-26T21:00:00+00:00",
            )

            out_text = io.StringIO()
            with patch(
                "install.installer.get_release_package_directory",
                return_value=str(release_dir),
            ), patch.object(sys, "argv", ["emu-dev-cli", "--version"]), patch(
                "sys.stdout", out_text
            ), self.assertRaises(
                SystemExit
            ) as cm:
                cli_main.main()
            self.assertEqual(cm.exception.code, 0)
            rendered = out_text.getvalue()
            self.assertIn(
                "emu-dev-cli abcdef01 (feat(emu-dev-cli): Add update version check)",
                rendered,
            )
            self.assertIn("abcdef0123456789abcdef0123456789abcdef01", rendered)

            out_json = io.StringIO()
            with patch(
                "install.installer.get_release_package_directory",
                return_value=str(release_dir),
            ), patch.object(
                sys, "argv", ["emu-dev-cli", "--json", "--version"]
            ), patch(
                "sys.stdout", out_json
            ), self.assertRaises(
                SystemExit
            ) as cm:
                cli_main.main()
            self.assertEqual(cm.exception.code, 0)
            parsed = json.loads(out_json.getvalue())
            self.assertEqual(parsed["status"], "success")
            self.assertEqual(parsed["action"], "version")
            self.assertTrue(parsed["installed"])
            self.assertEqual(parsed["commit_short"], "abcdef01")
            self.assertIn(
                "[dirty]", format_version_display({**parsed, "dirty": True})
            )


if __name__ == "__main__":
    unittest.main()
