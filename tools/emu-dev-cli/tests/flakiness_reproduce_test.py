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

"""Unit tests for 'emu-dev-cli flakiness reproduce'."""

import argparse
import unittest
from commands import flakiness
from core.context import ExecutionContext


class FlakinessReproduceTest(unittest.TestCase):

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--json", action="store_true")
        subparsers = self.parser.add_subparsers(dest="subcommand")
        flakiness.register_parser(subparsers)
        self.ctx = ExecutionContext.for_testing()

    def test_handle_flakiness_reproduce_passed(self):
        self.ctx.fake_process_runner.queue_response(
            returncode=0, stdout="PASSED in 0.5s"
        )
        args = self.parser.parse_args(
            [
                "flakiness",
                "reproduce",
                "--test",
                "//emulator/videobridge:in_process_media_provider_test",
                "--target",
                "emulator_linux_x64",
                "--iterations",
                "10",
            ]
        )
        cmd = flakiness.FlakinessReproduceCommand()
        res = cmd.execute(args, self.ctx)
        self.assertTrue(res.ok)
        data = res.data
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], "PASSED (No Flake Observed)")

    def test_handle_flakiness_reproduce_failed(self):
        self.ctx.fake_process_runner.queue_response(
            returncode=1, stdout="FAILED (TSAN race detected)"
        )
        args = self.parser.parse_args(
            [
                "flakiness",
                "reproduce",
                "--test",
                "//emulator/videobridge:in_process_media_provider_test",
                "--target",
                "emulator_linux_x64_tsan",
                "--iterations",
                "20",
            ]
        )
        cmd = flakiness.FlakinessReproduceCommand()
        res = cmd.execute(args, self.ctx)
        self.assertFalse(res.ok)
        data = res.data
        self.assertFalse(data["success"])
        self.assertEqual(data["status"], "FAILED (Flake Reproduced)")


if __name__ == "__main__":
    unittest.main()
