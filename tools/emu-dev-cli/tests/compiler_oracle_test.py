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

"""Unit tests for CompilerOracle in emu-dev-cli."""

from pathlib import Path
import unittest
from unittest.mock import MagicMock

from lib.compiler_oracle import CompilerError, CompilerOracle, CompilerOracleReport


class CompilerOracleTest(unittest.TestCase):

    def test_parse_clang_errors(self):
        oracle = CompilerOracle()
        sample_output = """
INFO: Build starting...
emulator/libs/sockets/socket_utils.cc:471:15: error: use of undeclared identifier 'socketSendAll'
    return socketSendAll(sock, buf, len);
           ^
emulator/launcher/launcher.cc:88:5: error: no member named 'socketTcpBindAndListen' in namespace 'android::sockets'
    android::sockets::socketTcpBindAndListen(port);
    ~~~~~~~~~~~~~~~~~~^
Target //emulator/libs/sockets:sockets failed to build
"""
        errors = oracle.parse_errors(sample_output)
        self.assertEqual(len(errors), 2)

        self.assertEqual(errors[0].file_path, "emulator/libs/sockets/socket_utils.cc")
        self.assertEqual(errors[0].line, 471)
        self.assertEqual(errors[0].column, 15)
        self.assertIn("use of undeclared identifier 'socketSendAll'", errors[0].message)

        self.assertEqual(errors[1].file_path, "emulator/launcher/launcher.cc")
        self.assertEqual(errors[1].line, 88)
        self.assertEqual(errors[1].column, 5)

    def test_parse_clang_errors_with_notes_and_context(self):
        oracle = CompilerOracle()
        sample_output = """
emulator/libs/sockets/socket_utils.cc:215:10: error: no matching function for call to 'InitFromBsd'
    return InitFromBsd(from, fromLen);
           ^~~~~~~~~~~
emulator/libs/sockets/include/android/sockets/socket_utils.h:50:6: note: candidate function not viable: no known conversion
bool InitFromBsd(const void* from, size_t from_len);
     ^
"""
        errors = oracle.parse_errors(sample_output)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].file_path, "emulator/libs/sockets/socket_utils.cc")
        self.assertEqual(errors[0].line, 215)
        self.assertIn("no matching function", errors[0].message)
        self.assertTrue(
            any("candidate function not viable" in n for n in errors[0].notes)
        )
        self.assertIn("InitFromBsd", errors[0].context)

    def test_parse_linker_errors(self):
        oracle = CompilerOracle()
        sample_output = """
ld.lld: error: undefined symbol: android::sockets::InitLoopbackFor(int, int)
>>> referenced by avd.cc:100
"""
        errors = oracle.parse_errors(sample_output, returncode=1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].file_path, "[linker]")
        self.assertIn("undefined symbol", errors[0].message)

    def test_fallback_raw_stderr_when_returncode_nonzero(self):
        oracle = CompilerOracle()
        sample_output = """
ninja: build stopped: subcommand failed.
Unknown fatal compiler crash occurred.
"""
        errors = oracle.parse_errors(sample_output, returncode=1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].file_path, "[build]")

        report = CompilerOracleReport(
            success=False, errors=[], raw_output=sample_output
        )
        formatted = report.format_for_prompt()
        self.assertIn("Compilation or linking failed", formatted)
        self.assertIn("ninja: build stopped", formatted)


if __name__ == "__main__":
    unittest.main()
