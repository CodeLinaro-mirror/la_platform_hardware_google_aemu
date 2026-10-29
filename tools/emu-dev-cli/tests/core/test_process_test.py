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

"""Hermetic unit tests for core.process (ProcessResult, FakeProcessRunner, SubprocessRunner)."""

import os
from pathlib import Path
import sys
import tempfile
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from core.errors import SubprocessError, UsageError
from core.events import EventBus, SubprocessInvoked
from core.process import (
    COMMAND_NOT_FOUND_EXIT_CODE,
    FakeProcessRunner,
    ProcessResult,
    SubprocessRunner,
    TIMEOUT_EXIT_CODE,
)


class ProcessTest(unittest.TestCase):
    """Tests FakeProcessRunner and SubprocessRunner without @patch decorators."""

    def test_fake_process_runner_queues_prefix_rules_and_emits_events(self) -> None:
        bus = EventBus(record_history=True)
        runner = FakeProcessRunner(event_bus=bus)

        runner.queue_response(returncode=0, stdout="queued_output_测试_🚀\n")
        runner.register_prefix(
            ["git", "status"],
            ProcessResult(args=(), returncode=0, stdout="clean\n"),
        )
        runner.register_prefix(
            ["bazel", "test"],
            lambda call: ProcessResult(
                args=call.args,
                returncode=1,
                stderr=f"failed in {call.cwd}",
            ),
        )

        res1 = runner.run(["echo", "hello"], cwd=Path("/tmp/测试_🚀_é"))
        self.assertTrue(res1.ok)
        self.assertEqual(res1.stdout, "queued_output_测试_🚀\n")
        self.assertEqual(runner.calls[0].cwd, "/tmp/测试_🚀_é")

        res2 = runner.run(["git", "status", "--short"])
        self.assertTrue(res2.ok)
        self.assertEqual(res2.stdout, "clean\n")

        with self.assertRaises(SubprocessError) as ctx:
            runner.run(
                ["bazel", "test", "//emulator/..."],
                cwd="/work/aemu",
                check=True,
                remediation="Run bazel clean and retry.",
            )
        self.assertEqual(ctx.exception.returncode, 1)
        self.assertIn("failed in /work/aemu", ctx.exception.message)
        self.assertEqual(ctx.exception.remediation, "Run bazel clean and retry.")

        invoked = bus.recorded_events(SubprocessInvoked)
        self.assertEqual(len(invoked), 3)
        self.assertEqual(invoked[0].cwd, "/tmp/测试_🚀_é")

    def test_rejects_raw_string_or_empty_command(self) -> None:
        runner = FakeProcessRunner()
        with self.assertRaises(UsageError):
            runner.run("git status")  # type: ignore[arg-type]
        with self.assertRaises(UsageError):
            runner.run([])
        with self.assertRaises(UsageError):
            runner.run(["   "])

    def test_real_subprocess_runner_utf8_timeout_and_missing_binary(self) -> None:
        bus = EventBus(record_history=True)
        runner = SubprocessRunner(event_bus=bus)

        with tempfile.TemporaryDirectory(prefix="emu_proc_测试_🚀_") as tmp_dir:
            res = runner.run(
                [
                    sys.executable,
                    "-c",
                    "import sys; print('你好_🚀_' + sys.stdin.read().strip())",
                ],
                cwd=Path(tmp_dir),
                input_text="世界_é",
                check=True,
            )
            self.assertTrue(res.ok)
            self.assertEqual(res.stdout.strip(), "你好_🚀_世界_é")

            timeout_res = runner.run(
                [sys.executable, "-c", "import time; time.sleep(2.0)"],
                cwd=tmp_dir,
                timeout_sec=0.05,
                check=False,
            )
            self.assertFalse(timeout_res.ok)
            self.assertTrue(timeout_res.timed_out)
            self.assertEqual(timeout_res.returncode, TIMEOUT_EXIT_CODE)

            with self.assertRaises(SubprocessError) as timeout_err:
                timeout_res.check_returncode()
            self.assertTrue(timeout_err.exception.timed_out)

            missing_res = runner.run(
                ["/nonexistent_binary_测试_🚀_12345"],
                cwd=tmp_dir,
                check=False,
            )
            self.assertEqual(missing_res.returncode, COMMAND_NOT_FOUND_EXIT_CODE)
            with self.assertRaises(SubprocessError):
                runner.run(
                    ["/nonexistent_binary_测试_🚀_12345"],
                    cwd=tmp_dir,
                    check=True,
                )


if __name__ == "__main__":
    unittest.main()
