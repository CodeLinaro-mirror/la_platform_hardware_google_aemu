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

"""Unit tests for commands.crash.advisor module."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)

from commands.crash.advisor import (
    AdvisorLaunch,
    CrashAdvisorRunner,
    ensure_crashadvisor_imports,
    resolve_advisor_launch,
)
from core.context import ExecutionContext


class CrashAdvisorTest(unittest.TestCase):
    """Tests for CrashAdvisor imports setup and execution."""

    def test_ensure_crashadvisor_imports(self):
        """Tests setting up sys.path and importing CrashAdvisor modules."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            advisor_dir = Path(tmp_dir) / "advisor"
            advisor_dir.mkdir()
            for mod in ["api", "buganizer", "client", "context", "dump", "metadata", "rca", "symbols", "advisor"]:
                (advisor_dir / f"{mod}.py").write_text(f"# fake {mod}", encoding="utf-8")
            with patch("lib.workspace.WorkspacePathResolver.find_directory", return_value=advisor_dir):
                modules = ensure_crashadvisor_imports()
                self.assertIn("advisor", modules)
                self.assertIn("symbols", modules)
                self.assertIn("buganizer", modules)

    def test_tqdm_stub_context_manager(self):
        """Tests that the fallback tqdm stub supports both context manager and iteration."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            advisor_dir = Path(tmp_dir) / "advisor"
            advisor_dir.mkdir()
            for mod in ["api", "buganizer", "client", "context", "dump", "metadata", "rca", "symbols", "advisor"]:
                (advisor_dir / f"{mod}.py").write_text(f"# fake {mod}", encoding="utf-8")
            with patch("lib.workspace.WorkspacePathResolver.find_directory", return_value=advisor_dir), \
                 patch.dict(sys.modules, {"tqdm": None}):
                sys.modules.pop("tqdm", None)
                ensure_crashadvisor_imports()
                import tqdm

                with tqdm.tqdm(total=10, unit="B") as bar:
                    bar.update(5)
                self.assertEqual(list(tqdm.tqdm([1, 2, 3])), [1, 2, 3])

    @patch("lib.workspace.WorkspacePathResolver.find_tool_binary")
    def test_resolve_advisor_launch_executable_found(self, mock_find_bin):
        """Tests resolving CrashAdvisor via precompiled binary executable."""
        mock_find_bin.return_value = Path("/path/to/advisor")
        launch = resolve_advisor_launch()
        self.assertEqual(launch.command, ("/path/to/advisor",))
        self.assertIsNone(launch.cwd)

    @patch("lib.workspace.WorkspacePathResolver.find_tool_binary")
    @patch("lib.workspace.WorkspacePathResolver.find_directory")
    @patch("lib.bazel.find_bazel_cmd")
    def test_resolve_advisor_launch_fallback(
        self, mock_find_bazel, mock_find_dir, mock_find_bin
    ):
        """Tests fallback execution via Bazel when executable binary is absent."""
        mock_find_bin.return_value = None
        mock_find_dir.return_value = Path("/work/emu-main-next/hardware/google/aemu")
        mock_find_bazel.return_value = "bazel"
        launch = resolve_advisor_launch()
        self.assertEqual(
            launch.command,
            ("bazel", "run", "@goldfish//emulator/crashreport/tool/advisor", "--"),
        )
        self.assertEqual(launch.cwd, Path("/work/emu-main-next"))

    def test_crash_advisor_runner_run(self):
        """Tests executing CrashAdvisor via CrashAdvisorRunner."""
        ctx = ExecutionContext.for_testing()
        runner = CrashAdvisorRunner(
            resolve_launch=lambda: AdvisorLaunch(command=("fake_advisor",), cwd=Path("/tmp"))
        )
        res = runner.run(
            ctx,
            "05d8356e2f800000",
            Path("/tmp/work"),
            flags=["--auto-run"],
            token="tok123",
        )
        self.assertTrue(res.ok)
        calls = ctx.process_runner.calls
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0].args,
            ("fake_advisor", "05d8356e2f800000", "--auto-run", "--work-dir", "/tmp/work", "--token", "tok123"),
        )
        self.assertEqual(str(calls[0].cwd), "/tmp")


if __name__ == "__main__":
    unittest.main()
