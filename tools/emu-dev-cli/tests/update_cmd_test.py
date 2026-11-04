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

"""Unit tests for `emu-dev-cli update` version detection, logging, and --force."""

import argparse
import io
import json
from pathlib import Path
import platform
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from commands.update_cmd import register_parser, run_update_cmd
from lib.version import read_installed_version, write_installed_version

COMMIT_A = "1111111122222222333333334444444455555555"
COMMIT_A_SHORT = "11111111"
SUBJECT_A = "feat(cli): Initial versioned release"

COMMIT_B = "9999999988888888777777776666666655555555"
COMMIT_B_SHORT = "99999999"
SUBJECT_B = "feat(cli): Next update 测试_🚀_é"


def _make_source_version(
    source_dir: str,
    commit: str = COMMIT_A,
    short: str = COMMIT_A_SHORT,
    subject: str = SUBJECT_A,
    dirty: bool = False,
) -> dict:
    return {
        "commit": commit,
        "commit_short": short,
        "commit_date": "2026-10-26T22:00:00+00:00",
        "commit_subject": subject,
        "dirty": dirty,
        "source_dir": source_dir,
    }


def _create_fake_executable(root: Path) -> Path:
    fake_bin = root / "built" / "emu-dev-cli"
    fake_bin.parent.mkdir(parents=True, exist_ok=True)
    fake_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    if platform.system().lower() != "windows":
        fake_bin.chmod(0o755)
    return fake_bin


class UpdateCmdVersioningTest(unittest.TestCase):
    """Tests update-needed detection, log output, JSON payloads, and --force."""

    def _invoke_update(
        self,
        root: Path,
        source_dir: Path,
        release_dir: Path,
        src_ver: dict,
        *,
        fake_bin: Path | None = None,
        force: bool = False,
        json_mode: bool = False,
    ) -> tuple[int, str, MagicMock, MagicMock]:
        """Runs `run_update_cmd` with isolated mocks and captures exit code + stdout."""
        stdout_buf = io.StringIO()
        with patch(
            "commands.update_cmd.get_source_directory",
            return_value=str(source_dir),
        ), patch(
            "commands.update_cmd.get_release_package_directory",
            return_value=str(release_dir),
        ), patch(
            "commands.update_cmd.inspect_source_version",
            return_value=src_ver,
        ), patch(
            "commands.update_cmd.find_bazel_cmd", return_value="bazel"
        ), patch(
            "commands.update_cmd.subprocess.run",
            return_value=MagicMock(returncode=0),
        ) as mock_bazel_run, patch(
            "commands.update_cmd.resolve_built_artifact",
            return_value=str(fake_bin) if fake_bin else None,
        ), patch(
            "commands.update_cmd.install_launcher_wrapper"
        ) as mock_install, patch(
            "commands.update_cmd.install_skill", return_value=[]
        ), patch(
            "commands.update_cmd.ensure_codesearch_urls_config"
        ), patch(
            "sys.stdout", stdout_buf
        ), self.assertRaises(
            SystemExit
        ) as cm:
            run_update_cmd(
                argparse.Namespace(
                    path=str(root / "bin" / "emu-dev-cli"),
                    force=force,
                    json=json_mode,
                    verbose=False,
                )
            )
        return cm.exception.code, stdout_buf.getvalue(), mock_bazel_run, mock_install

    def test_register_parser_supports_force_flags(self):
        """Verifies `update` registers both `--force` and `-f`."""
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register_parser(subparsers)

        parsed_default = parser.parse_args(["update"])
        self.assertFalse(parsed_default.force)

        parsed_short = parser.parse_args(["update", "-f"])
        self.assertTrue(parsed_short.force)

        parsed_long = parser.parse_args(["update", "--force"])
        self.assertTrue(parsed_long.force)

    def test_skips_build_when_clean_and_identical_commit(self):
        """Verifies clean identical commit logs 'No update was needed' and skips Bazel."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            source_dir = root / "src_repo"
            source_dir.mkdir()
            release_dir = root / "release"

            src_ver = _make_source_version(str(source_dir))
            write_installed_version(release_dir, source_version=src_ver)

            code, output, mock_bazel_run, mock_install = self._invoke_update(
                root, source_dir, release_dir, src_ver
            )
            self.assertEqual(code, 0)
            mock_bazel_run.assert_not_called()
            mock_install.assert_not_called()
            self.assertIn(
                f"ℹ️ No update was needed: emu-dev-cli is already at commit "
                f"{COMMIT_A_SHORT} ({SUBJECT_A}).",
                output,
            )
            self.assertIn("Use --force to rebuild anyway.", output)

    def test_skips_build_in_json_mode_with_update_needed_false(self):
        """Verifies JSON mode returns pure JSON with `update_needed: False` when up to date."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            source_dir = root / "src_repo"
            source_dir.mkdir()
            release_dir = root / "release"

            src_ver = _make_source_version(str(source_dir))
            write_installed_version(release_dir, source_version=src_ver)

            code, output, mock_bazel_run, _ = self._invoke_update(
                root, source_dir, release_dir, src_ver, json_mode=True
            )
            self.assertEqual(code, 0)
            mock_bazel_run.assert_not_called()
            payload = json.loads(output)
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["action"], "update")
            self.assertFalse(payload["update_needed"])
            self.assertFalse(payload["updated"])

    def test_force_rebuilds_even_when_already_up_to_date(self):
        """Verifies `--force` runs Bazel build and re-install when commit is unchanged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            source_dir = root / "src_repo"
            source_dir.mkdir()
            release_dir = root / "release"
            fake_bin = _create_fake_executable(root)

            src_ver = _make_source_version(str(source_dir))
            write_installed_version(
                release_dir,
                source_version=src_ver,
                installed_at="2026-10-20T00:00:00+00:00",
            )

            code, output, mock_bazel_run, mock_install = self._invoke_update(
                root,
                source_dir,
                release_dir,
                src_ver,
                fake_bin=fake_bin,
                force=True,
            )
            self.assertEqual(code, 0)
            mock_bazel_run.assert_called_once()
            mock_install.assert_called_once()
            self.assertIn("ℹ️ No update was needed:", output)
            self.assertIn("--force", output)
            self.assertIn(
                f"✅ emu-dev-cli updated to {COMMIT_A_SHORT} ({SUBJECT_A}).",
                output,
            )

    def test_updates_and_logs_reasons_when_missing_different_or_dirty(self):
        """Verifies update logs reason and updates version.json across missing, new, and dirty states."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            source_dir = root / "src_repo"
            source_dir.mkdir()
            release_dir = root / "release"
            fake_bin = _create_fake_executable(root)

            scenarios = [
                (
                    _make_source_version(str(source_dir), COMMIT_A, COMMIT_A_SHORT, SUBJECT_A),
                    "ℹ️ Update is needed: no previous version record found.",
                    f"✅ emu-dev-cli updated to {COMMIT_A_SHORT} ({SUBJECT_A}).",
                ),
                (
                    _make_source_version(str(source_dir), COMMIT_B, COMMIT_B_SHORT, SUBJECT_B),
                    f"ℹ️ Update is needed: updating from {COMMIT_A_SHORT} to {COMMIT_B_SHORT} ({SUBJECT_B}).",
                    f"✅ emu-dev-cli updated to {COMMIT_B_SHORT} ({SUBJECT_B}).",
                ),
                (
                    _make_source_version(
                        str(source_dir), COMMIT_B, COMMIT_B_SHORT, SUBJECT_B, dirty=True
                    ),
                    "ℹ️ Update is needed: local changes detected in source directory.",
                    f"✅ emu-dev-cli updated to {COMMIT_B_SHORT} ({SUBJECT_B}).",
                ),
            ]

            for src_ver, expected_needed_msg, expected_done_msg in scenarios:
                code, output, mock_bazel_run, _ = self._invoke_update(
                    root,
                    source_dir,
                    release_dir,
                    src_ver,
                    fake_bin=fake_bin,
                )
                self.assertEqual(code, 0)
                mock_bazel_run.assert_called_once()
                self.assertIn(expected_needed_msg, output)
                self.assertIn(expected_done_msg, output)

                persisted = read_installed_version(release_dir)
                self.assertIsNotNone(persisted)
                self.assertEqual(persisted["commit"], src_ver["commit"])
                self.assertEqual(persisted["dirty"], src_ver["dirty"])


if __name__ == "__main__":
    unittest.main()
