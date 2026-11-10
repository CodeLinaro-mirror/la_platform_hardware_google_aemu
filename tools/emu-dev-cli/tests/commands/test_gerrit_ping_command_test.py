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

"""Hermetic unit tests for GerritPingForReviewCommand and emu_core exports (zero @patch decorators)."""

import argparse
import io
import json
import os
import sys
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import clients
from commands.gerrit import (
    GerritPingForReviewCommand,
    PingArgs,
    PingResult,
    register_parser,
)
import core
from core.context import ExecutionContext
from core.dispatcher import CliDispatcher, OutputRenderer
from core.errors import (
    ExecutionError,
    ExitCode,
    RetryableError,
    UsageError,
)
from core.process import FakeProcessCall, ProcessResult


def _build_gerrit_detail_xssi(
    *,
    cl_number: int = 43386500,
    change_id: str = "I1234567890abcdef1234567890abcdef12345678",
    subject: str = "feat(gerrit): Add ping-for-review_测试_🚀_é",
    project: str = "platform/hardware/google/aemu",
    patch_set: int = 3,
    presubmit_score: int = 2,
) -> str:
    """Builds a Gerrit XSSI-prefixed `/changes/<id>/detail` JSON payload."""
    label_entry: dict[str, object] = {"all": [{"value": presubmit_score}]}
    if presubmit_score >= 2:
        label_entry["approved"] = {"_account_id": 1000}
    elif presubmit_score == 1:
        label_entry["recommended"] = {"_account_id": 1000}
    elif presubmit_score == -1:
        label_entry["disliked"] = {"_account_id": 1000}
    elif presubmit_score <= -2:
        label_entry["rejected"] = {"_account_id": 1000}
    payload = {
        "_number": cl_number,
        "change_id": change_id,
        "project": project,
        "subject": subject,
        "current_revision": f"rev_ps_{patch_set}",
        "revisions": {
            f"rev_ps_{patch_set}": {"_number": patch_set},
        },
        "labels": {
            "Presubmit-Verified": label_entry,
        },
    }
    return ")]}'\n" + json.dumps(payload)


class GerritPingForReviewCommandTest(unittest.TestCase):
    """Tests GerritPingForReviewCommand polling, Chat notification, error paths, and emu_core exports."""

    def test_ping_immediately_when_presubmit_verified_plus_two(self) -> None:
        ctx = ExecutionContext.for_testing("/tmp/emu-ping-test")
        ctx.fake_process_runner.register_prefix(
            ["/usr/bin/gob-curl", "-s", "-f", "-S", "--compressed", "-X", "GET"],
            ProcessResult(
                args=("/usr/bin/gob-curl",),
                returncode=0,
                stdout=_build_gerrit_detail_xssi(
                    cl_number=43386500,
                    subject="feat(gerrit): Add ping-for-review_测试_🚀_é",
                    patch_set=2,
                    presubmit_score=2,
                ),
            ),
        )
        cmd = GerritPingForReviewCommand()
        result = cmd.execute(
            PingArgs(cl="43386500", to="joshuaduong", no_wait=True),
            ctx,
        )
        self.assertTrue(result.ok)
        self.assertIsInstance(result.data, PingResult)
        self.assertEqual(result.data.cl_number, 43386500)
        self.assertEqual(result.data.patch_set, 2)
        self.assertEqual(result.data.presubmit_score, 2)
        self.assertEqual(result.data.recipient, "joshuaduong@google.com")
        self.assertEqual(result.data.polls, 1)
        self.assertEqual(len(ctx.fake_chat_service.sent_dms), 1)
        sent = ctx.fake_chat_service.sent_dms[0]
        self.assertEqual(sent.recipient, "joshuaduong@google.com")
        self.assertIn("ag/43386500", sent.message)
        self.assertIn("feat(gerrit): Add ping-for-review_测试_🚀_é", sent.message)
        self.assertIn("Presubmit-Verified: +2", sent.message)

    def test_polls_until_presubmit_reaches_plus_two_then_pings(self) -> None:
        ctx = ExecutionContext.for_testing("/tmp/emu-ping-poll-test")
        scores = [0, 1, 2]

        def _detail_responder(call: FakeProcessCall) -> ProcessResult:
            score = scores.pop(0) if scores else 2
            return ProcessResult(
                args=call.args,
                returncode=0,
                stdout=_build_gerrit_detail_xssi(
                    cl_number=43386500,
                    patch_set=4,
                    presubmit_score=score,
                ),
            )

        ctx.fake_process_runner.register_prefix(
            ["/usr/bin/gob-curl", "-s", "-f", "-S", "--compressed", "-X", "GET"],
            _detail_responder,
        )
        clock_now = [100.0]
        slept_intervals: list[float] = []

        def _fake_clock() -> float:
            return clock_now[0]

        def _fake_sleep(sec: float) -> None:
            slept_intervals.append(sec)
            clock_now[0] += sec

        cmd = GerritPingForReviewCommand(
            sleep_fn=_fake_sleep,
            clock_fn=_fake_clock,
        )
        res = cmd.execute(
            PingArgs(
                cl="ag/43386500",
                to="jansene",
                timeout=120,
                poll_interval=15,
            ),
            ctx,
        )
        self.assertEqual(res.data.polls, 3)
        self.assertEqual(res.data.presubmit_score, 2)
        self.assertEqual(slept_intervals, [15.0, 15.0])
        self.assertEqual(len(ctx.fake_chat_service.sent_dms), 1)
        self.assertEqual(
            ctx.fake_chat_service.sent_dms[0].recipient, "jansene@google.com"
        )

    def test_presubmit_failure_raises_execution_error_without_pinging(self) -> None:
        ctx = ExecutionContext.for_testing("/tmp/emu-ping-fail-test")
        ctx.fake_process_runner.register_prefix(
            ["/usr/bin/gob-curl", "-s", "-f", "-S", "--compressed", "-X", "GET"],
            ProcessResult(
                args=("/usr/bin/gob-curl",),
                returncode=0,
                stdout=_build_gerrit_detail_xssi(
                    cl_number=43386500,
                    patch_set=1,
                    presubmit_score=-1,
                ),
            ),
        )
        cmd = GerritPingForReviewCommand()
        with self.assertRaisesRegex(
            ExecutionError, "Presubmit-Verified is -1 on PS1"
        ):
            cmd.execute(
                PingArgs(cl="43386500", to="joshuaduong", no_wait=True),
                ctx,
            )
        self.assertEqual(len(ctx.fake_chat_service.sent_dms), 0)

    def test_timeout_when_presubmit_not_yet_plus_two_does_not_ping(self) -> None:
        ctx = ExecutionContext.for_testing("/tmp/emu-ping-timeout-test")
        ctx.fake_process_runner.register_prefix(
            ["/usr/bin/gob-curl", "-s", "-f", "-S", "--compressed", "-X", "GET"],
            ProcessResult(
                args=("/usr/bin/gob-curl",),
                returncode=0,
                stdout=_build_gerrit_detail_xssi(
                    cl_number=43386500,
                    patch_set=1,
                    presubmit_score=1,
                ),
            ),
        )
        clock_now = [0.0]

        def _fake_clock() -> float:
            return clock_now[0]

        def _fake_sleep(sec: float) -> None:
            clock_now[0] += sec

        cmd = GerritPingForReviewCommand(
            sleep_fn=_fake_sleep,
            clock_fn=_fake_clock,
        )
        with self.assertRaisesRegex(
            RetryableError, "Presubmit-Verified is \\+1 after 2 poll\\(s\\)"
        ):
            cmd.execute(
                PingArgs(
                    cl="43386500",
                    to="corakwue",
                    timeout=10,
                    poll_interval=10,
                ),
                ctx,
            )
        self.assertEqual(len(ctx.fake_chat_service.sent_dms), 0)

    def test_head_resolution_and_cli_dispatcher_json_integration(self) -> None:
        ctx = ExecutionContext.for_testing(
            "/tmp/emu-ping-head-test", json_mode=True
        )
        change_id = "Iabcdef1234567890abcdef1234567890abcdef12"
        ctx.fake_process_runner.register_prefix(
            ["git", "log", "-1", "--format=%B", "HEAD"],
            ProcessResult(
                args=("git", "log"),
                returncode=0,
                stdout=(
                    "feat(gerrit): Add ping-for-review\n\n"
                    f"Change-Id: {change_id}\n"
                ),
            ),
        )
        ctx.fake_process_runner.register_prefix(
            ["/usr/bin/gob-curl", "-s", "-f", "-S", "--compressed", "-X", "GET"],
            ProcessResult(
                args=("/usr/bin/gob-curl",),
                returncode=0,
                stdout=_build_gerrit_detail_xssi(
                    cl_number=43386501,
                    change_id=change_id,
                    subject="feat(gerrit): Add ping-for-review",
                    patch_set=1,
                    presubmit_score=2,
                ),
            ),
        )
        root_parser = argparse.ArgumentParser(prog="emu-dev-cli")
        subparsers = root_parser.add_subparsers(dest="command")
        register_parser(subparsers)

        parsed = root_parser.parse_args(
            [
                "gerrit",
                "ping-for-review",
                "HEAD",
                "--to",
                "joshuaduong",
                "--no-wait",
                "--message",
                "Custom review ping_测试_🚀_é",
            ]
        )
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        dispatcher = CliDispatcher(
            context=ctx,
            renderer=OutputRenderer(stdout=stdout_buf, stderr=stderr_buf),
        )
        exit_code = dispatcher.dispatch(parsed)
        self.assertEqual(exit_code, ExitCode.SUCCESS)
        payload = json.loads(stdout_buf.getvalue())
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["cl_number"], 43386501)
        self.assertEqual(payload["recipient"], "joshuaduong@google.com")
        self.assertEqual(payload["message"], "Custom review ping_测试_🚀_é")

        bad_parsed = root_parser.parse_args(
            [
                "gerrit",
                "ping-for-review",
                "43386501",
                "--to",
                "joshuaduong, jansene",
            ]
        )
        err_out = io.StringIO()
        err_dispatcher = CliDispatcher(
            context=ctx,
            renderer=OutputRenderer(stdout=err_out, stderr=io.StringIO()),
        )
        self.assertEqual(
            err_dispatcher.dispatch(bad_parsed), ExitCode.USAGE_ERROR
        )

    def test_parse_args_validates_timeout_and_poll_interval(self) -> None:
        cmd = GerritPingForReviewCommand()
        parser = argparse.ArgumentParser()
        cmd.configure_parser(parser)
        with self.assertRaisesRegex(UsageError, "timeout must be >= 0"):
            cmd.parse_args(
                parser.parse_args(["43386500", "--to", "joshuaduong", "--timeout", "-1"])
            )
        with self.assertRaisesRegex(UsageError, "poll interval must be >= 1"):
            cmd.parse_args(
                parser.parse_args(
                    ["43386500", "--to", "joshuaduong", "--poll-interval", "0"]
                )
            )

    def test_emu_core_foundation_exports_in_core_and_clients(self) -> None:
        expected_core_exports = (
            "AppConfig",
            "CliDispatcher",
            "Command",
            "CommandGroup",
            "CommandResult",
            "ExecutionContext",
            "FakeProcessRunner",
            "FakeServiceFactory",
            "ProcessResult",
            "ProcessRunner",
            "ProdServiceFactory",
            "SERVICE_AGENT_RUNTIME",
            "SERVICE_ARTIFACT",
            "SERVICE_BUGANIZER",
            "SERVICE_CHAT",
            "SERVICE_EVENT_BUS",
            "SERVICE_F1_SQL",
            "SERVICE_PROCESS_RUNNER",
            "ServiceContainer",
            "ServiceFactory",
            "SubprocessRunner",
        )
        for symbol in expected_core_exports:
            self.assertIn(symbol, core.__all__)
            self.assertTrue(hasattr(core, symbol), f"core missing {symbol}")

        expected_client_exports = (
            "AgentRuntimeService",
            "AppConfig",
            "ArtifactService",
            "BuganizerService",
            "ChatDmRequest",
            "ChatDmResult",
            "ChatNotificationService",
            "ExecutionContext",
            "F1SqlClient",
            "FakeAgentRuntimeService",
            "FakeArtifactService",
            "FakeBuganizerService",
            "FakeChatNotificationService",
            "FakeF1SqlClient",
            "FakeProcessRunner",
            "FakeServiceFactory",
            "ProcessRunner",
            "ProdAgentRuntimeService",
            "ProdArtifactService",
            "ProdBuganizerService",
            "ProdChatNotificationService",
            "ProdF1SqlClient",
            "ProdServiceFactory",
            "ServiceContainer",
            "SubprocessRunner",
        )
        for symbol in expected_client_exports:
            self.assertIn(symbol, clients.__all__)
            self.assertTrue(
                hasattr(clients, symbol), f"clients missing {symbol}"
            )


if __name__ == "__main__":
    unittest.main()
