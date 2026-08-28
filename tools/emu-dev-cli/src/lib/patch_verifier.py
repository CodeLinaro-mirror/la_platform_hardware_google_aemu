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

"""Transactional patch application, verification, and rollback engine for emu-dev-cli."""

import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import Dict, List, Optional

from lib.bazel import BazelRunner, BuildExitCode


class TransactionalPatchContext:
    """Provides snapshot backup, fast build/test verification, and automatic rollback."""

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        bazel_runner: Optional[BazelRunner] = None,
    ):
        self.workspace_root = workspace_root or Path.cwd()
        self.bazel_runner = bazel_runner or BazelRunner(source_dir=self.workspace_root)
        self._snapshots: Dict[Path, bytes] = {}

    def snapshot(self, files: List[Path]) -> None:
        """Captures in-memory byte snapshots of the specified files."""
        for f in files:
            path = Path(f)
            if not path.is_absolute() and self.workspace_root:
                path = self.workspace_root / path
            if path.exists() and path not in self._snapshots:
                with open(path, "rb") as fp:
                    self._snapshots[path] = fp.read()

    def restore(self) -> None:
        """Restores all snapshot files to their initial pristine state."""
        for path, content in self._snapshots.items():
            try:
                with open(path, "wb") as fp:
                    fp.write(content)
            except Exception as e:
                logging.error("Failed to restore file %s: %s", path, e)
        self._snapshots.clear()

    def commit(self) -> None:
        """Discards snapshots and commits changes as the new baseline."""
        self._snapshots.clear()

    def verify_build(self, target: Optional[str] = None) -> bool:
        """Runs fast build verification using mise fastbuild or bazel build."""
        try:
            if target:
                res = self.bazel_runner.build([target])
                return res.returncode == 0
            elif shutil.which("mise"):
                # Try mise fastbuild if mise executable is available on PATH
                res = subprocess.run(
                    ["mise", "run", "fastbuild"],
                    cwd=str(self.workspace_root),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                return res.returncode == 0
            else:
                # Fallback to Bazel build across the goldfish workspace
                res = self.bazel_runner.build(["@goldfish//..."])
                return res.returncode == 0
        except Exception as e:
            logging.error("Build verification encountered error: %s", e)
            return False

    def verify_test(self, test_target: Optional[str] = None) -> bool:
        """Runs test verification using mise fasttest or bazel test."""
        try:
            if test_target:
                res = self.bazel_runner.test([test_target])
                return res.returncode == 0
            elif shutil.which("mise"):
                res = subprocess.run(
                    ["mise", "run", "fasttest"],
                    cwd=str(self.workspace_root),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                return res.returncode == 0
            else:
                res = self.bazel_runner.test(["@goldfish//..."])
                return res.returncode == 0
        except Exception as e:
            logging.error("Test verification encountered error: %s", e)
            return False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.restore()
        return False
