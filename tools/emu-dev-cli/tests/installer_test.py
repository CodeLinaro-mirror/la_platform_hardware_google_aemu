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

"""Unit tests for installer and update logic."""

import argparse
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure src/ is in sys.path
SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from install.installer import (
    install_launcher_wrapper,
    install_skill,
    resolve_built_artifact,
)
from commands.update_cmd import BAZEL_TARGET, run_update_cmd


class InstallerTest(unittest.TestCase):
    """Tests for emu-dev-cli installer and update command."""

    @patch("install.installer.BazelRunner")
    def test_resolve_built_artifact_success(self, mock_runner_cls):
        """Verifies resolve_built_artifact returns the matching executable from cquery."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir).resolve()
            fake_bin = tmp_path / "emu-dev-cli"
            fake_bin.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
            if platform.system().lower() != "windows":
                fake_bin.chmod(0o755)

            mock_runner = MagicMock()
            mock_runner.query_artifacts.return_value = [fake_bin]
            mock_runner_cls.return_value = mock_runner

            result = resolve_built_artifact(
                str(tmp_path), "@aemu//tools/emu-dev-cli:emu-dev-cli", "emu-dev-cli"
            )
            self.assertEqual(result, str(fake_bin))
            mock_runner.query_artifacts.assert_called_once_with(
                ["@aemu//tools/emu-dev-cli:emu-dev-cli"], cwd=tmp_path
            )

    @patch("install.installer.BazelRunner")
    def test_resolve_built_artifact_not_executable(self, mock_runner_cls):
        """Verifies resolve_built_artifact skips non-executable files on POSIX."""
        if platform.system().lower() == "windows":
            self.skipTest("Permission test not applicable on Windows")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir).resolve()
            fake_bin = tmp_path / "emu-dev-cli"
            fake_bin.write_text("not executable", encoding="utf-8")
            fake_bin.chmod(0o644)

            mock_runner = MagicMock()
            mock_runner.query_artifacts.return_value = [fake_bin]
            mock_runner_cls.return_value = mock_runner

            result = resolve_built_artifact(
                str(tmp_path), "@aemu//tools/emu-dev-cli:emu-dev-cli", "emu-dev-cli"
            )
            self.assertIsNone(result)

    @patch("install.installer.BazelRunner")
    def test_resolve_built_artifact_cquery_failure(self, mock_runner_cls):
        """Verifies resolve_built_artifact handles cquery exceptions gracefully."""
        mock_runner = MagicMock()
        mock_runner.query_artifacts.side_effect = RuntimeError("cquery failed")
        mock_runner_cls.return_value = mock_runner

        with tempfile.TemporaryDirectory() as tmpdir:
            result = resolve_built_artifact(
                tmpdir, "@aemu//tools/emu-dev-cli:emu-dev-cli", "emu-dev-cli"
            )
            self.assertIsNone(result)

    @patch("install.installer.BazelRunner")
    def test_resolve_built_artifact_empty(self, mock_runner_cls):
        """Verifies resolve_built_artifact returns None when cquery yields no files."""
        mock_runner = MagicMock()
        mock_runner.query_artifacts.return_value = []
        mock_runner_cls.return_value = mock_runner

        with tempfile.TemporaryDirectory() as tmpdir:
            result = resolve_built_artifact(
                tmpdir, "@aemu//tools/emu-dev-cli:emu-dev-cli", "emu-dev-cli"
            )
            self.assertIsNone(result)

    def test_install_launcher_wrapper(self):
        """Verifies install_launcher_wrapper installs binary and python libraries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir).resolve()

            fake_built_bin = tmp_path / "built_emu_dev_cli"
            fake_built_bin.write_text("#!/bin/sh\necho built\n", encoding="utf-8")
            if platform.system().lower() != "windows":
                fake_built_bin.chmod(0o755)

            fake_release_dir = tmp_path / "release"
            dest_launcher = tmp_path / "bin" / "emu-dev-cli"

            fake_src = tmp_path / "src"
            fake_src.mkdir()
            (fake_src / "__main__.py").write_text("print('hello')\n")

            with patch(
                "install.installer.get_release_package_directory",
                return_value=str(fake_release_dir),
            ), patch(
                "install.installer.resolve_built_artifact",
                return_value=None,
            ):
                install_launcher_wrapper(
                    str(fake_built_bin),
                    str(dest_launcher),
                    source_dir=str(fake_src),
                )

            # Check that release binary exists
            release_bin_name = "emu-dev-cli.exe" if platform.system().lower() == "windows" else "emu-dev-cli"
            installed_release_bin = fake_release_dir / release_bin_name
            self.assertTrue(installed_release_bin.exists())

            # Check that python files were copied to lib/
            installed_main = fake_release_dir / "lib" / "__main__.py"
            self.assertTrue(installed_main.exists())

            # Check that launcher wrapper destination exists
            self.assertTrue(dest_launcher.exists())

    def test_install_skill(self):
        """Verifies install_skill writes SKILL.md to the gemini skills directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir).resolve()
            with patch("os.path.expanduser", return_value=str(tmp_path)):
                installed_files = install_skill(source_dir=None)

            self.assertTrue(len(installed_files) > 0)
            for skill_file in installed_files:
                self.assertTrue(os.path.exists(skill_file))
                self.assertTrue(skill_file.endswith("SKILL.md"))
                with open(skill_file, "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("emu-dev-cli", content)

    @patch("commands.update_cmd.ensure_codesearch_urls_config")
    @patch("commands.update_cmd.install_skill")
    @patch("commands.update_cmd.install_launcher_wrapper")
    @patch("commands.update_cmd.resolve_built_artifact")
    @patch("commands.update_cmd.subprocess.run")
    @patch("commands.update_cmd.find_bazel_cmd", return_value="bazel")
    @patch("commands.update_cmd.get_source_directory")
    def test_run_update_cmd_success(
        self,
        mock_get_source,
        mock_find_bazel,
        mock_subproc,
        mock_resolve,
        mock_install_wrapper,
        mock_install_skill,
        mock_ensure_config,
    ):
        """Verifies successful run_update_cmd flow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir).resolve()
            mock_get_source.return_value = str(tmp_path)
            mock_subproc.return_value = MagicMock(returncode=0)
            mock_install_skill.return_value = ["/mock/SKILL.md"]

            fake_bin = tmp_path / "bin" / "emu-dev-cli"
            fake_bin.parent.mkdir(parents=True, exist_ok=True)
            fake_bin.write_text("#!/bin/sh\n")
            if platform.system().lower() != "windows":
                fake_bin.chmod(0o755)
            mock_resolve.return_value = str(fake_bin)

            args = argparse.Namespace(
                path=str(tmp_path / "installed_bin"),
                json=True,
                verbose=False,
            )

            # Should complete with successful exit code 0
            with self.assertRaises(SystemExit) as cm:
                run_update_cmd(args)
            self.assertEqual(cm.exception.code, 0)

            # Verify Bazel build was executed
            mock_subproc.assert_called_once()
            call_cmd = mock_subproc.call_args[0][0]
            self.assertEqual(call_cmd[0], "bazel")
            self.assertEqual(call_cmd[1], "build")
            self.assertIn(BAZEL_TARGET, call_cmd)

            # Verify resolve and install were invoked
            mock_resolve.assert_called_once_with(
                str(tmp_path), BAZEL_TARGET, "emu-dev-cli"
            )
            mock_install_wrapper.assert_called_once_with(
                str(tmp_path / "bin" / "emu-dev-cli"),
                str(tmp_path / "installed_bin"),
                source_dir=str(tmp_path),
            )
            mock_install_skill.assert_called_once()
            mock_ensure_config.assert_called_once()

    @patch("commands.update_cmd.subprocess.run")
    @patch("commands.update_cmd.find_bazel_cmd", return_value="bazel")
    @patch("commands.update_cmd.get_source_directory")
    def test_run_update_cmd_build_failure(
        self, mock_get_source, mock_find_bazel, mock_subproc
    ):
        """Verifies run_update_cmd exits with error when Bazel build fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_get_source.return_value = tmpdir
            mock_subproc.return_value = MagicMock(returncode=2, stderr="Build failed")

            args = argparse.Namespace(path=None, json=True, verbose=False)
            with self.assertRaises(SystemExit) as cm:
                run_update_cmd(args)

            self.assertEqual(cm.exception.code, 2)

    @patch("commands.update_cmd.resolve_built_artifact", return_value=None)
    @patch("commands.update_cmd.subprocess.run")
    @patch("commands.update_cmd.find_bazel_cmd", return_value="bazel")
    @patch("commands.update_cmd.get_source_directory")
    def test_run_update_cmd_resolution_failure(
        self, mock_get_source, mock_find_bazel, mock_subproc, mock_resolve
    ):
        """Verifies run_update_cmd exits with error when artifact cannot be resolved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_get_source.return_value = tmpdir
            mock_subproc.return_value = MagicMock(returncode=0)

            args = argparse.Namespace(path=None, json=True, verbose=False)
            with self.assertRaises(SystemExit) as cm:
                run_update_cmd(args)

            self.assertEqual(cm.exception.code, 1)

    @patch("commands.update_cmd.os.getcwd", return_value="/nonexistent")
    @patch("commands.update_cmd.get_source_directory", return_value=None)
    def test_run_update_cmd_missing_source_directory(
        self, mock_get_source, mock_getcwd
    ):
        """Verifies run_update_cmd exits when no valid source directory is found."""
        args = argparse.Namespace(path=None, json=True, verbose=False)
        with self.assertRaises(SystemExit) as cm:
            run_update_cmd(args)

        self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
