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

"""Unit tests for commands.crash.parser module."""

import argparse
import os
import sys
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from commands.crash.parser import register_parser
from commands.crash.find_bug import run_find_bug
from commands.crash.file_bug import run_file_bug
from commands.crash.autofix import run_autofix
from commands.crash.reproduce import run_reproduce
from commands.crash.analyze import run_analyze


class CrashParserTest(unittest.TestCase):
    """Tests for full `crash` command group parser registration."""

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.subparsers = self.parser.add_subparsers(dest="subcommand")
        register_parser(self.subparsers)

    def test_find_bug_subcommand(self):
        """Tests parsing `crash find-bug` arguments."""
        args = self.parser.parse_args(
            ["crash", "find-bug", "123456", "--component-id", "999"]
        )
        self.assertEqual(args.subcommand, "crash")
        self.assertEqual(args.crash_cmd, "find-bug")
        self.assertEqual(args.crash_id, "123456")
        self.assertEqual(args.component_id, 999)
        self.assertEqual(args.func, run_find_bug)

    def test_file_bug_subcommand(self):
        """Tests parsing `crash file-bug` arguments."""
        args = self.parser.parse_args(["crash", "file-bug", "123456", "--qa"])
        self.assertEqual(args.subcommand, "crash")
        self.assertEqual(args.crash_cmd, "file-bug")
        self.assertTrue(args.qa)
        self.assertEqual(args.func, run_file_bug)

    def test_autofix_subcommand(self):
        """Tests parsing `crash autofix` arguments."""
        args = self.parser.parse_args(["crash", "autofix", "123456", "--dry-run"])
        self.assertEqual(args.subcommand, "crash")
        self.assertEqual(args.crash_cmd, "autofix")
        self.assertTrue(args.dry_run)
        self.assertEqual(args.func, run_autofix)

    def test_reproduce_subcommand(self):
        """Tests parsing `crash reproduce` arguments."""
        args = self.parser.parse_args(
            ["crash", "reproduce", "123456", "--lldb", "--dry-run"]
        )
        self.assertEqual(args.subcommand, "crash")
        self.assertEqual(args.crash_cmd, "reproduce")
        self.assertTrue(args.lldb)
        self.assertTrue(args.dry_run)
        self.assertEqual(args.func, run_reproduce)

    def test_analyze_subcommand(self):
        """Tests parsing `crash analyze` arguments."""
        args = self.parser.parse_args(["crash", "analyze", "123456", "--auto-run"])
        self.assertEqual(args.subcommand, "crash")
        self.assertEqual(args.crash_cmd, "analyze")
        self.assertTrue(args.auto_run)
        self.assertEqual(args.func, run_analyze)


if __name__ == "__main__":
    unittest.main()
