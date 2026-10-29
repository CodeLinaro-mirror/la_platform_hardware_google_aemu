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

"""Hermetic zero-patch DI unit tests for `commands.flakiness` Command classes."""

import argparse
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import unittest

_SRC_DIR = str(Path(__file__).resolve().parents[2] / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from commands.flakiness import (
    FlakinessCommand,
    FlakinessFetchLogsCommand,
    FlakinessFixCommand,
    FlakinessHistoryCommand,
    FlakinessListCommand,
    FlakinessReproduceCommand,
    FlakinessSpongeCommand,
    FlakinessTriageCommand,
    register_parser,
)
from core.command import CommandValidationError
from core.context import (
    ExecutionContext,
    FakeServiceFactory,
    SERVICE_F1_SQL,
)
from core.dispatcher import CliDispatcher


class _FakeStudioF1Client:
    """In-memory fake F1SqlClient returning Studio TestActions JSONL rows."""

    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None) -> None:
        self.rows = list(rows or [])
        self.queries: List[str] = []

    def query_jsonl(
        self,
        sql: str,
        *,
        accounting_group: Optional[str] = None,
        timeout_sec: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        del accounting_group, timeout_sec
        self.queries.append(sql)
        return list(self.rows)


class FlakinessCommandsTest(unittest.TestCase):
    """Tests Command-pattern flakiness subcommands with FakeServiceFactory."""

    def test_flakiness_list_validate_rejects_invalid_days_and_limit(self) -> None:
        ctx = ExecutionContext.for_testing()
        cmd = FlakinessListCommand()
        with self.assertRaises(CommandValidationError):
            cmd.validate(argparse.Namespace(days=0, limit=10), ctx)
        with self.assertRaises(CommandValidationError):
            cmd.validate(argparse.Namespace(days=7, min_flake_rate=150.0), ctx)

    def test_flakiness_list_studio_executes_with_injected_f1_and_unicode(
        self,
    ) -> None:
        fake_f1 = _FakeStudioF1Client(
            rows=[
                {
                    "test_target": "//tools/vendor/google/cli/android-device-hub/electron:test_测试_🚀_é",
                    "ab_target": "studio-linux",
                    "l100_runs": 100,
                    "l100_failed": 3,
                    "l100_flake_pct": 3.0,
                    "l250_runs": 250,
                    "l250_failed": 5,
                    "l250_flake_pct": 2.0,
                    "sample_failed_invocation_id": "inv-unicode-123",
                    "latest_failure_date": "2026-10-28 12:00:00",
                }
            ]
        )
        factory = FakeServiceFactory(services={SERVICE_F1_SQL: fake_f1})
        ctx = ExecutionContext.for_testing(factory=factory)
        cmd = FlakinessListCommand()
        args = argparse.Namespace(
            target="studio-linux",
            config="all",
            mode="postsubmit",
            days=14,
            limit=20,
            min_runs=1,
            directory="//tools/vendor/google/cli/android-device-hub/electron",
            window=100,
            accounting_group="android-devtools-emulator-leads",
            print_sql=False,
            include_non_flaky=False,
            token=None,
            json=True,
        )
        validated = cmd.validate(args, ctx)
        res = cmd.execute(validated, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(len(fake_f1.queries), 1)
        self.assertIn("android_devtools_infra.TestActions", fake_f1.queries[0])

    def test_flakiness_history_studio_executes_with_injected_f1_and_unicode(
        self,
    ) -> None:
        fake_f1 = _FakeStudioF1Client(
            rows=[
                {
                    "invocation_id": "inv-测试_🚀_é-001",
                    "test_target": "//tools/vendor/google/cli/android-device-hub/electron:unit_测试_🚀_é",
                    "status": "FLAKY",
                    "ab_target": "studio-linux",
                    "branch": "git_studio-main",
                    "build_id": "16600001",
                    "start_time": "2026-10-28 10:00:00",
                    "duration_s": 4.2,
                    "attempt_count": 2,
                }
            ]
        )
        factory = FakeServiceFactory(services={SERVICE_F1_SQL: fake_f1})
        ctx = ExecutionContext.for_testing(factory=factory)
        cmd = FlakinessHistoryCommand()
        args = argparse.Namespace(
            test="//tools/vendor/google/cli/android-device-hub/electron:unit_测试_🚀_é",
            target="studio-linux",
            config="all",
            mode="postsubmit",
            days=10,
            limit=15,
            accounting_group=None,
            print_sql=False,
            token=None,
            json=True,
        )
        validated = cmd.validate(args, ctx)
        res = cmd.execute(validated, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(len(fake_f1.queries), 1)
        self.assertIn("unit_测试_🚀_é", fake_f1.queries[0])

    def test_flakiness_subcommands_validation_guards(self) -> None:
        ctx = ExecutionContext.for_testing()
        with self.assertRaises(CommandValidationError):
            FlakinessFetchLogsCommand().validate(
                argparse.Namespace(invocation_id="   "), ctx
            )
        with self.assertRaises(CommandValidationError):
            FlakinessFixCommand().validate(
                argparse.Namespace(bug_id="not-a-bug", runs=10), ctx
            )
        with self.assertRaises(CommandValidationError):
            FlakinessReproduceCommand().validate(
                argparse.Namespace(test="   ", runs=10), ctx
            )
        with self.assertRaises(CommandValidationError):
            FlakinessSpongeCommand().validate(
                argparse.Namespace(
                    invocation_id=None, test=None, target_filter=None
                ),
                ctx,
            )
        with self.assertRaises(CommandValidationError):
            FlakinessTriageCommand().validate(
                argparse.Namespace(days=0, limit=5), ctx
            )

    def test_flakiness_parser_binds_command_instances_for_dispatcher(self) -> None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--json", action="store_true")
        subparsers = parser.add_subparsers(dest="subcommand")
        register_parser(subparsers)

        parsed = parser.parse_args(
            ["--json", "flakiness", "list", "--target", "studio-linux"]
        )
        dispatcher = CliDispatcher()
        resolved = dispatcher.resolve_command(parsed)
        self.assertIsInstance(resolved, FlakinessListCommand)
        self.assertEqual(FlakinessCommand().name, "flakiness")


if __name__ == "__main__":
    unittest.main()
