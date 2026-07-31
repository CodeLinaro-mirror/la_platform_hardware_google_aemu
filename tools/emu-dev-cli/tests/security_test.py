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

"""Unit tests for lib.security module."""

import os
from pathlib import Path
import sys
import tempfile
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from lib.security import is_path_secure_user_owned, ensure_secure_permissions


class SecurityTest(unittest.TestCase):
    """Tests for security and filesystem validation helpers."""

    def test_is_path_secure_user_owned_dir(self):
        """Tests directory permission and ownership validation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.chmod(tmp_dir, 0o700)
            self.assertTrue(is_path_secure_user_owned(tmp_dir, is_dir=True))
            self.assertFalse(is_path_secure_user_owned(tmp_dir, is_dir=False))

            os.chmod(tmp_dir, 0o777)
            self.assertFalse(is_path_secure_user_owned(tmp_dir, is_dir=True))

    def test_is_path_secure_user_owned_file(self):
        """Tests file permission and ownership validation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_file = Path(tmp_dir) / "test.sh"
            test_file.write_text("#!/bin/sh\necho test\n", encoding="utf-8")
            test_file.chmod(0o700)

            self.assertTrue(is_path_secure_user_owned(test_file, is_dir=False))
            self.assertFalse(is_path_secure_user_owned(test_file, is_dir=True))

            test_file.chmod(0o777)
            self.assertFalse(is_path_secure_user_owned(test_file, is_dir=False))

    def test_ensure_secure_permissions(self):
        """Tests setting safe user-only permissions."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_file = Path(tmp_dir) / "script.sh"
            test_file.write_text("#!/bin/sh\n", encoding="utf-8")
            test_file.chmod(0o777)

            ensure_secure_permissions(test_file, mode=0o700)
            self.assertTrue(is_path_secure_user_owned(test_file, is_dir=False))

    def test_parent_symlink_rejection(self):
        """Tests that having a symlink anywhere in ancestor hierarchy is rejected."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            real_target_dir = Path(tmp_dir) / "real_target"
            real_target_dir.mkdir(mode=0o700)

            symlink_parent = Path(tmp_dir) / "symlink_dir"
            os.symlink(real_target_dir, symlink_parent)

            child_file = symlink_parent / "script.sh"
            (real_target_dir / "script.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (real_target_dir / "script.sh").chmod(0o700)

            # Accessing through the symlink parent must be rejected
            self.assertFalse(is_path_secure_user_owned(child_file, is_dir=False))


if __name__ == "__main__":
    unittest.main()
