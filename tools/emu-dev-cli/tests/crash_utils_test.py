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

"""Unit tests for commands.crash.utils module."""

import os
import sys
import tempfile
import unittest

# Ensure src/ is in sys.path
SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from commands.crash.utils import (
    parse_crash_id,
    acquire_auth_token,
    extract_top_fault_frame,
    is_path_secure_user_owned,
    get_crashadvisor_sandbox_dir,
)


class CrashUtilsTest(unittest.TestCase):
    """Tests for crash investigation utility functions."""

    def test_parse_crash_id(self):
        """Tests parsing crash ID from raw strings and URLs."""
        self.assertEqual(parse_crash_id("05d8356e2f800000"), "05d8356e2f800000")
        self.assertEqual(
            parse_crash_id("https://crash.corp.google.com/05d8356e2f800000"),
            "05d8356e2f800000",
        )
        self.assertEqual(
            parse_crash_id("go/crash/05d8356e2f800000"), "05d8356e2f800000"
        )
        self.assertEqual(parse_crash_id("  05d8356e2f800000 / "), "05d8356e2f800000")

    def test_acquire_auth_token(self):
        """Tests acquiring and cleaning auth tokens."""
        self.assertEqual(
            acquire_auth_token("Bearer explicit_token_123\n"), "explicit_token_123"
        )
        self.assertEqual(acquire_auth_token("explicit_token_123"), "explicit_token_123")

    def test_extract_top_fault_frame(self):
        """Tests extracting top faulting function and file from crash dump."""
        sample_dump = """
Crashing Thread:
#0 0x00007f123456 in abort () from /lib64/libc.so.6
#1 0x00007f123457 in android::FrameBuffer::post() at FrameBuffer.cpp:142
#2 0x00007f123458 in RenderThread::main() at RenderThread.cpp:88
"""
        func, file_info = extract_top_fault_frame(sample_dump)
        self.assertEqual(func, "android::FrameBuffer::post")
        self.assertEqual(file_info, "FrameBuffer.cpp:142")

    def test_get_crashadvisor_sandbox_dir(self):
        """Tests resolving CrashAdvisor sandbox directory path."""
        sandbox = get_crashadvisor_sandbox_dir("05d8356e2f800000")
        self.assertIn("crashadvisor_05d8356e2f800000_", sandbox)

    def test_create_secure_sandbox_dir(self):
        """Tests creating atomic user-exclusive sandbox directory."""
        from commands.crash.utils import create_secure_sandbox_dir

        sandbox = create_secure_sandbox_dir("05d8356e2f800000")
        try:
            self.assertTrue(os.path.exists(sandbox))
            self.assertTrue(is_path_secure_user_owned(sandbox, is_dir=True))
        finally:
            if os.path.exists(sandbox):
                os.rmdir(sandbox)

    def test_is_path_secure_user_owned(self):
        """Tests security and permissions verification for files and directories."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.chmod(tmp_dir, 0o700)
            self.assertTrue(is_path_secure_user_owned(tmp_dir, is_dir=True))
            self.assertFalse(is_path_secure_user_owned(tmp_dir, is_dir=False))

            os.chmod(tmp_dir, 0o777)
            self.assertFalse(is_path_secure_user_owned(tmp_dir, is_dir=True))
            os.chmod(tmp_dir, 0o700)

            test_file = os.path.join(tmp_dir, "test.sh")
            with open(test_file, "w") as f:
                f.write("#!/bin/sh\necho test\n")
            os.chmod(test_file, 0o700)
            self.assertTrue(is_path_secure_user_owned(test_file, is_dir=False))
            self.assertFalse(is_path_secure_user_owned(test_file, is_dir=True))


if __name__ == "__main__":
    unittest.main()
