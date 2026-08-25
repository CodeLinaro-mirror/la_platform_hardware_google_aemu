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

"""Unit tests for patch_verifier.py."""

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from lib.bazel import BazelRunner
from lib.patch_verifier import TransactionalPatchContext


class PatchVerifierTest(unittest.TestCase):

    def test_snapshot_restore_commit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            f1 = Path(tmpdir) / "test.txt"
            f1.write_text("original content", encoding="utf-8")

            ctx = TransactionalPatchContext(workspace_root=Path(tmpdir))
            ctx.snapshot([f1])

            # Modify file
            f1.write_text("modified content", encoding="utf-8")
            self.assertEqual(f1.read_text(encoding="utf-8"), "modified content")

            # Restore
            ctx.restore()
            self.assertEqual(f1.read_text(encoding="utf-8"), "original content")

    @patch("lib.patch_verifier.shutil.which", return_value=None)
    def test_verify_build_fallback_bazel(self, mock_which):
        mock_bazel = MagicMock(spec=BazelRunner)
        mock_bazel.build.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr=""
        )

        ctx = TransactionalPatchContext(bazel_runner=mock_bazel)
        success = ctx.verify_build(target=None)

        self.assertTrue(success)
        mock_bazel.build.assert_called_once_with(["@goldfish//..."])

    @patch("lib.patch_verifier.shutil.which", return_value="/usr/bin/mise")
    @patch("lib.patch_verifier.subprocess.run")
    def test_verify_build_mise_available(self, mock_run, mock_which):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_run.return_value = mock_proc

        ctx = TransactionalPatchContext()
        success = ctx.verify_build(target=None)

        self.assertTrue(success)
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args[0][0], ["mise", "run", "fastbuild"])

    @patch("lib.patch_verifier.shutil.which", return_value=None)
    def test_verify_test_fallback_bazel(self, mock_which):
        mock_bazel = MagicMock(spec=BazelRunner)
        mock_bazel.test.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr=""
        )

        ctx = TransactionalPatchContext(bazel_runner=mock_bazel)
        success = ctx.verify_test(test_target=None)

        self.assertTrue(success)
        mock_bazel.test.assert_called_once_with(["@goldfish//..."])


if __name__ == "__main__":
    unittest.main()
