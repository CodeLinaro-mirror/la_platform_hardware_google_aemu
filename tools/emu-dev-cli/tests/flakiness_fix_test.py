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

"""Unit tests for 'emu-dev-cli flakiness fix'."""

import argparse
import unittest
from clients.buganizer_service import BuganizerIssue
from commands import flakiness
from core.context import ExecutionContext
from core.errors import ExecutionError


class FlakinessFixTest(unittest.TestCase):

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        self.parser.add_argument("--json", action="store_true")
        subparsers = self.parser.add_subparsers(dest="subcommand")
        flakiness.register_parser(subparsers)
        self.ctx = ExecutionContext.for_testing()

    def test_handle_flakiness_fix_resolved_from_bug(self):
        self.ctx.fake_buganizer_service.seed_issue(
            BuganizerIssue(
                issue_id="123456789",
                title="[Flaky Test] @goldfish//emulator/libs/async:loop_handoff_test failing on emulator_linux_x64_tsan (20.0% flake rate)",
                component_id=1016880,
                status="ASSIGNED",
            )
        )
        args = self.parser.parse_args(
            [
                "flakiness",
                "fix",
                "--bug",
                "123456789",
                "--iterations",
                "50",
                "--dry-run",
            ]
        )
        cmd = flakiness.FlakinessFixCommand()
        res = cmd.execute(args, self.ctx)
        self.assertTrue(res.ok)
        data = res.data
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["bug_id"], "123456789")
        self.assertEqual(
            data["test_target"],
            "@goldfish//emulator/libs/async:loop_handoff_test",
        )

    def test_handle_flakiness_fix_unresolved_target_error(self):
        self.ctx.fake_buganizer_service.seed_issue(
            BuganizerIssue(
                issue_id="999999",
                title="Some other unrelated bug without bazel target",
                component_id=1016880,
                status="ASSIGNED",
            )
        )
        args = self.parser.parse_args(
            [
                "flakiness",
                "fix",
                "--bug",
                "999999",
                "--dry-run",
            ]
        )
        cmd = flakiness.FlakinessFixCommand()
        with self.assertRaises(ExecutionError) as ctx_err:
            cmd.execute(args, self.ctx)
        self.assertEqual(ctx_err.exception.error_code, "UNRESOLVED_TEST_TARGET")

    def test_handle_flakiness_fix_dry_run_explicit_test(self):
        args = self.parser.parse_args(
            [
                "flakiness",
                "fix",
                "--bug",
                "123456789",
                "--test",
                "//emulator/videobridge:in_process_media_provider_test",
                "--iterations",
                "50",
                "--dry-run",
            ]
        )
        cmd = flakiness.FlakinessFixCommand()
        res = cmd.execute(args, self.ctx)
        self.assertTrue(res.ok)
        data = res.data
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["bug_id"], "123456789")
        self.assertEqual(
            data["test_target"],
            "//emulator/videobridge:in_process_media_provider_test",
        )

    def test_handle_flakiness_fix_success_upload(self):
        self.ctx.fake_buganizer_service.seed_issue(
            BuganizerIssue(
                issue_id="123456789",
                title="[Flaky Test] @goldfish//emulator/libs/async:loop_handoff_test",
                component_id=1016880,
                status="ASSIGNED",
            )
        )
        self.ctx.fake_process_runner.queue_response(
            returncode=0, stdout="PASSED 50/50 iterations"
        )
        args = self.parser.parse_args(
            [
                "flakiness",
                "fix",
                "--bug",
                "b/123456789",
                "--iterations",
                "50",
                "--upload",
            ]
        )
        cmd = flakiness.FlakinessFixCommand()
        res = cmd.execute(args, self.ctx)
        self.assertTrue(res.ok)
        data = res.data
        self.assertTrue(data["test_passed"])
        self.assertEqual(data["bug_id"], "123456789")
        self.assertEqual(
            data["test_target"],
            "@goldfish//emulator/libs/async:loop_handoff_test",
        )
        self.assertTrue(data["upload"])

    def test_handle_flakiness_fix_failure(self):
        self.ctx.fake_buganizer_service.seed_issue(
            BuganizerIssue(
                issue_id="123456789",
                title="[Flaky Test] @goldfish//emulator/libs/async:loop_handoff_test",
                component_id=1016880,
                status="ASSIGNED",
            )
        )
        self.ctx.fake_process_runner.queue_response(
            returncode=1, stdout="FAILED on iteration 12"
        )
        args = self.parser.parse_args(
            [
                "flakiness",
                "fix",
                "--bug",
                "b/123456789",
                "--iterations",
                "50",
                "--upload",
            ]
        )
        cmd = flakiness.FlakinessFixCommand()
        res = cmd.execute(args, self.ctx)
        self.assertFalse(res.ok)
        data = res.data
        self.assertFalse(data["test_passed"])


if __name__ == "__main__":
    unittest.main()
