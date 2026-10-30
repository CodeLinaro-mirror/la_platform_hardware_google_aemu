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

"""Unit tests for lib.workspace module."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)

from lib.workspace import WorkspacePathResolver


class WorkspacePathResolverTest(unittest.TestCase):
    """Tests for unified workspace path resolution."""

    def test_find_directory_custom_root(self):
        """Tests resolving directory relative to custom root."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_dir = Path(tmp_dir) / "hardware/generic/goldfish/tool"
            target_dir.mkdir(parents=True)

            resolver = WorkspacePathResolver(custom_roots=[tmp_dir])
            resolved = resolver.find_directory("hardware/generic/goldfish/tool")

            self.assertIsNotNone(resolved)
            self.assertEqual(resolved, target_dir)

    def test_find_file_across_registered_sources(self):
        """Tests resolving file relative to registered source directories."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "hardware/google/aemu/tools/libs_python/ab.py"
            file_path.parent.mkdir(parents=True)
            file_path.write_text("# module\n", encoding="utf-8")

            with patch("lib.workspace.get_source_directory", return_value=tmp_dir):
                resolver = WorkspacePathResolver("emu-main-next")
                resolved = resolver.find_file(
                    "hardware/google/aemu/tools/libs_python/ab.py"
                )

                self.assertIsNotNone(resolved)
                self.assertEqual(resolved, file_path)

    def test_find_directory_ancestor_fallback(self):
        """Tests fallback resolution walking up ancestor trees."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir) / "repo"
            sub_dir = repo_root / "nested/deep/child"
            target_dir = repo_root / "hardware/generic/tool"

            sub_dir.mkdir(parents=True)
            target_dir.mkdir(parents=True)

            resolver = WorkspacePathResolver(custom_roots=[sub_dir])
            resolved = resolver.find_directory("hardware/generic/tool")

            self.assertIsNotNone(resolved)
            self.assertEqual(resolved, target_dir)

    def test_find_tool_binary_bazel_bin(self):
        """Tests locating executable inside bazel-bin."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            bin_path = Path(tmp_dir) / "bazel-bin/tool/bin/my_tool"
            bin_path.parent.mkdir(parents=True)
            bin_path.write_text("#!/bin/sh\n", encoding="utf-8")
            bin_path.chmod(0o755)

            with patch("lib.workspace.get_source_directory", return_value=tmp_dir):
                resolver = WorkspacePathResolver("emu-main-next")
                resolved = resolver.find_tool_binary(
                    "my_tool", bazel_bin_rel="bazel-bin/tool/bin/my_tool"
                )

                self.assertIsNotNone(resolved)
                self.assertEqual(resolved, bin_path)

    def test_find_tool_binary_bazel_target_cquery(self):
        """Tests locating executable via BazelRunner cquery resolution."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            bin_path = Path(tmp_dir) / "bazel-out/k8-opt/bin/external/goldfish+/advisor"
            bin_path.parent.mkdir(parents=True)
            bin_path.write_text("#!/bin/sh\n", encoding="utf-8")
            bin_path.chmod(0o755)

            with patch(
                "lib.workspace.get_source_directory", return_value=tmp_dir
            ), patch("lib.bazel.BazelRunner.query_artifacts", return_value=[bin_path]):
                (Path(tmp_dir) / "WORKSPACE").write_text("", encoding="utf-8")
                resolver = WorkspacePathResolver(
                    "emu-main-next", custom_roots=[tmp_dir]
                )
                resolved = resolver.find_tool_binary(
                    "advisor",
                    bazel_target="@goldfish//emulator/crashreport/tool/advisor:advisor",
                )

                self.assertIsNotNone(resolved)
                self.assertEqual(resolved, bin_path)

    def test_find_tool_binary_glob_fallback(self):
        """Tests locating executable via resilient bazel-bin globbing."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            bin_path = Path(tmp_dir) / "bazel-bin/any_mangled_dir/advisor/advisor"
            bin_path.parent.mkdir(parents=True)
            bin_path.write_text("#!/bin/sh\n", encoding="utf-8")
            bin_path.chmod(0o755)

            resolver = WorkspacePathResolver(custom_roots=[tmp_dir])
            resolved = resolver.find_tool_binary("advisor")

            self.assertIsNotNone(resolved)
            self.assertEqual(resolved, bin_path)

    def test_find_non_existent(self):
        """Tests returning None when path cannot be resolved."""

        resolver = WorkspacePathResolver(custom_roots=[])
        self.assertIsNone(resolver.find_file("non/existent/file.txt"))
        self.assertIsNone(resolver.find_directory("non/existent/dir"))
        self.assertIsNone(resolver.find_tool_binary("non_existent_binary"))

    def test_source_directory_atomic_save_backup_recovery_and_corrupt_protection(self):
        """Verifies `source_directory` never wipes corrupt configs and recovers from `.bak`."""
        from commands import source_directory
        from core.errors import ExecutionError

        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg_path = os.path.join(tmp_dir, "测试_🚀_é", "emu-dev-cli.json")
            with patch.object(source_directory, "CONFIG_FILE_PATH", cfg_path):
                saved = source_directory.set_source_directory(
                    "emu-main-next", os.path.join(tmp_dir, "work_测试_🚀_é")
                )
                self.assertTrue(os.path.isfile(cfg_path))
                self.assertEqual(
                    source_directory.get_source_directory("emu-main-next"), saved
                )

                # Second save creates .bak of the valid previous config
                source_directory.set_source_directory(
                    "emu-main-dev", os.path.join(tmp_dir, "dev_测试_🚀_é")
                )
                self.assertTrue(os.path.isfile(f"{cfg_path}.bak"))

                # Corrupt primary config; load_config recovers from .bak
                Path(cfg_path).write_text("{corrupt json", encoding="utf-8")
                recovered = source_directory.load_config(strict=True)
                self.assertEqual(
                    recovered["source_directories"].get("emu-main-next"), saved
                )

                # Remove .bak while primary config is still corrupt:
                # ensure_codesearch_urls_config must NOT overwrite the corrupt file,
                # and set_source_directory must raise CORRUPT_CONFIG_FILE.
                os.unlink(f"{cfg_path}.bak")
                source_directory.ensure_codesearch_urls_config()
                self.assertEqual(
                    Path(cfg_path).read_text(encoding="utf-8"), "{corrupt json"
                )
                with self.assertRaises(ExecutionError) as err_ctx:
                    source_directory.set_source_directory("git_main", tmp_dir)
                self.assertEqual(
                    err_ctx.exception.error_code, "CORRUPT_CONFIG_FILE"
                )


if __name__ == "__main__":
    unittest.main()
