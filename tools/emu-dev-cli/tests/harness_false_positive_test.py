#!/usr/bin/env python3
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

"""Regression tests for AEMU Harness hook false positives."""

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from commands.harness.constants import HookDecision, HookEvent
from commands.harness.diagnostics import diagnose_command_failure, enrich_post_tool_result
from commands.harness.guardrails import (
    dispatch_hook_event,
    evaluate_git_commit_command,
    rewrite_command_if_needed,
)
from commands.harness.ledger import SessionLedgerStore
from commands.harness.shell_segments import iter_command_segments

VALID_COMMIT_MESSAGE = (
    "fix(harness): Resolve commit message paths 测试_🚀_é\n\n"
    "Expand shell variables before validating the commit message file so the\n"
    "guardrail checks the file that git actually reads.\n\n"
    "Bug: N/A\n"
    "Test: bazel test @aemu//tools/emu-dev-cli:harness_false_positive_test\n"
    "Change-Id: I1234567890abcdef1234567890abcdef12345678\n"
)
INVALID_COMMIT_MESSAGE = "fixed stuff\n"
TSAN_REPORT_LINE = "WARNING: ThreadSanitizer: data race (pid=1234)"
MISSING_HEADER_LINE = (
    "emulator/net/client.cc:12:10: fatal error: 'goldfish/http/web_server.h' file not found"
)
UNSET_VAR_NAME = "AEMU_HARNESS_TEST_UNSET_COMMIT_FILE"


class ShellSegmentTest(unittest.TestCase):
    """Tests quote-aware splitting of command lines into top-level segments."""

    def _texts(self, command: str, posix: bool = True) -> list[str]:
        segments = list(iter_command_segments(command, posix=posix))
        for start, text in segments:
            self.assertEqual(command[start : start + len(text)], text)
        return [text.strip() for _, text in segments]

    def test_splits_on_unquoted_separators_only(self) -> None:
        command = "cd /src && bazel test //a:b | tee log; echo \"x; y\" 'p | q' \\; done"
        self.assertEqual(
            self._texts(command),
            ["cd /src", "bazel test //a:b", "tee log", "echo \"x; y\" 'p | q' \\; done"],
        )

    def test_backslash_is_literal_on_windows(self) -> None:
        self.assertEqual(
            self._texts('cd "C:\\src\\" && git commit -m x', posix=False),
            ['cd "C:\\src\\"', "git commit -m x"],
        )

    def test_skips_heredoc_bodies_and_comments(self) -> None:
        self.assertEqual(
            self._texts("cat > notes.txt <<'EOF'\nDon't forget; bazel test //x:y\nEOF\ngit commit -m wip"),
            ["cat > notes.txt <<'EOF'", "git commit -m wip"],
        )
        self.assertEqual(
            self._texts("cat <<-END <<\"B\"\n\tit's a\n\tEND\nb | c\nB\necho done"),
            ['cat <<-END <<"B"', "echo done"],
        )
        self.assertEqual(
            self._texts("echo hi # don't split; here\ngit commit -m wip"),
            ["echo hi", "git commit -m wip"],
        )
        self.assertEqual(
            self._texts("cat <<< \"it's\"; echo ${#ARR} $#"),
            ['cat <<< "it\'s"', "echo ${#ARR} $#"],
        )


class BazelTestRewriteTest(unittest.TestCase):
    """Tests that `--test_output=errors` is injected only into real `bazel test` commands."""

    def test_rewrites_bazel_test_at_command_positions(self) -> None:
        cases = {
            "cd /src && bazel test //emulator/libs/file:all": (
                "cd /src && bazel test --test_output=errors //emulator/libs/file:all"
            ),
            "FOO=1 bazel test //x:y": "FOO=1 bazel test --test_output=errors //x:y",
            "bazel --output_base=/tmp/ob test //x:y": (
                "bazel --output_base=/tmp/ob test --test_output=errors //x:y"
            ),
            "C:\\tools\\bazelisk.exe test //x:y": (
                "C:\\tools\\bazelisk.exe test --test_output=errors //x:y"
            ),
        }
        for command, expected in cases.items():
            rewritten = rewrite_command_if_needed(command)
            self.assertIsNotNone(rewritten, msg=command)
            self.assertEqual(rewritten[0], expected)  # type: ignore[index]

    def test_leaves_quoted_and_non_command_mentions_untouched(self) -> None:
        for command in (
            'grep -rnoE "bazel test[^`]*" hardware/generic/goldfish --include=AGENTS.md',
            "echo 'run: bazel test //x:y'",
            'git commit -m "docs: Explain how to run bazel test"',
            "xbazel test //x:y",
            "cat > run.sh <<'EOF'\nbazel test //x:y\nEOF",
        ):
            self.assertIsNone(rewrite_command_if_needed(command), msg=command)

        with tempfile.TemporaryDirectory() as tmp:
            out = dispatch_hook_event(
                HookEvent.PRE_TOOL.value,
                {
                    "conversationId": "conv-grep",
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": 'grep -rn "bazel test //a:b" docs/'},
                    },
                },
                store=SessionLedgerStore(base_dir=Path(tmp)),
            )
        self.assertEqual(out["decision"], HookDecision.ALLOW.value)
        self.assertNotIn("overwrite", out)


class CommitMessageFileTest(unittest.TestCase):
    """Tests that `git commit -F` paths are resolved the way the shell would."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.valid = self.base / "valid_测试.txt"
        self.valid.write_text(VALID_COMMIT_MESSAGE, encoding="utf-8")
        self.invalid = self.base / "invalid.txt"
        self.invalid.write_text(INVALID_COMMIT_MESSAGE, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _verdict(self, command: str, default_cwd: Path | None = None) -> tuple[bool, str]:
        verdict = evaluate_git_commit_command(command, default_cwd=default_cwd)
        self.assertIsNotNone(verdict, msg=command)
        return verdict  # type: ignore[return-value]

    def test_expands_variables_assigned_earlier_in_the_command(self) -> None:
        allowed, reason = self._verdict(f'MSG="{self.valid}"; git commit -F $MSG')
        self.assertTrue(allowed, msg=reason)

        allowed, reason = self._verdict(f'MSG="{self.invalid}" && git commit -F "${{MSG}}"')
        self.assertFalse(allowed)
        self.assertIn("Semantic Commit", reason)

    def test_expands_environment_variables_and_relative_paths(self) -> None:
        with mock.patch.dict(os.environ, {"AEMU_HARNESS_TEST_COMMIT_FILE": str(self.valid)}):
            allowed, reason = self._verdict('git commit -F "$AEMU_HARNESS_TEST_COMMIT_FILE"')
        self.assertTrue(allowed, msg=reason)

        allowed, reason = self._verdict(f"cd {self.base} && git commit -F {self.valid.name}")
        self.assertTrue(allowed, msg=reason)

        allowed, _ = self._verdict("git commit -F invalid.txt", default_cwd=self.base)
        self.assertFalse(allowed)

    def test_skips_unresolvable_paths_and_stdin(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(UNSET_VAR_NAME, None)
            allowed, reason = self._verdict(f"git commit -F ${UNSET_VAR_NAME}")
        self.assertTrue(allowed)
        self.assertIn("Skipped", reason)

        allowed, reason = self._verdict("git commit -F - < message.txt")
        self.assertTrue(allowed)
        self.assertIn("stdin", reason)

    def test_resolves_git_dash_c_directory_with_unicode(self) -> None:
        sub = self.base / "子目录_🚀_é"
        sub.mkdir()
        (sub / "msg_测试.txt").write_text(VALID_COMMIT_MESSAGE, encoding="utf-8")
        (sub / "bad_é.txt").write_text(INVALID_COMMIT_MESSAGE, encoding="utf-8")

        allowed, reason = self._verdict(
            "grep -C 3 x log.txt; git -C 子目录_🚀_é commit -F msg_测试.txt",
            default_cwd=self.base,
        )
        self.assertTrue(allowed, msg=reason)

        allowed, _ = self._verdict('git -C "子目录_🚀_é" commit -F bad_é.txt', default_cwd=self.base)
        self.assertFalse(allowed)

    def test_blocks_message_file_written_by_the_same_command(self) -> None:
        # A stale valid file must not be verified when the command overwrites it first.
        for command in (
            f'MSG="{self.valid}"; echo "fixed stuff" > "$MSG" && git commit -F "$MSG"',
            f"cat > {self.valid} <<'EOF'\nfixed stuff\nEOF\ngit commit -F {self.valid}",
            f"cd {self.base} && printf 'fixed stuff' | tee {self.valid.name} && "
            f"git commit -F {self.valid}",
        ):
            allowed, reason = self._verdict(command)
            self.assertFalse(allowed, msg=command)
            self.assertIn("written by this same command", reason)
            self.assertIn("separate command", reason)

    def test_reports_missing_message_file_without_format_tip(self) -> None:
        missing = self.base / "missing_é.txt"
        allowed, reason = self._verdict(f"git commit -F {missing}")
        self.assertFalse(allowed)
        self.assertIn("does not exist", reason)
        self.assertIn(str(missing), reason)
        self.assertNotIn("Format the commit message", reason)


class CommitSegmentDetectionTest(unittest.TestCase):
    """Tests which command lines count as a top-level `git commit`."""

    def test_blocks_windows_git_binary_forms(self) -> None:
        for command in (
            'git.exe commit -m "fixed stuff"',
            'C:\\Git\\cmd\\git commit -m "fixed stuff"',
            '"C:\\Program Files\\Git\\cmd\\git.exe" commit -m "fixed stuff"',
        ):
            verdict = evaluate_git_commit_command(command)
            self.assertIsNotNone(verdict, msg=command)
            self.assertFalse(verdict[0], msg=command)  # type: ignore[index]

        self.assertIsNone(evaluate_git_commit_command('legit commit -m "fixed stuff"'))

    def test_heredoc_and_comment_apostrophes_do_not_hide_the_commit(self) -> None:
        for command in (
            "cat > notes.txt <<'EOF'\nDon't forget\nEOF\ngit commit -m \"wip\"",
            "echo hi  # don't forget\ngit commit -m \"wip\"",
        ):
            verdict = evaluate_git_commit_command(command)
            self.assertIsNotNone(verdict, msg=command)
            self.assertFalse(verdict[0], msg=command)  # type: ignore[index]


class DiagnosticsFalsePositiveTest(unittest.TestCase):
    """Tests that diagnostics only fire for failed commands and name real targets."""

    def test_successful_commands_are_not_diagnosed(self) -> None:
        payload = {"result": f"{TSAN_REPORT_LINE}\n{MISSING_HEADER_LINE}\n"}
        self.assertIsNone(enrich_post_tool_result("sed -n '1,40p' tests/harness_test.py", payload))

    def test_failed_commands_are_still_diagnosed(self) -> None:
        failed = {"error": "exit status 1", "result": f"{TSAN_REPORT_LINE}\n"}
        enriched = enrich_post_tool_result("bazel test //emulator/libs/async:all", failed)
        self.assertIsNotNone(enriched)
        self.assertIn("--config=tsan", enriched[0])  # type: ignore[index]

        exit_code_only = {"result": f"Command exited with code 66.\n\n{TSAN_REPORT_LINE}\n"}
        self.assertIsNotNone(
            enrich_post_tool_result("bazel test //emulator/libs/async:all", exit_code_only)
        )

    def test_missing_header_hints_name_existing_targets(self) -> None:
        log = (
            f"{MISSING_HEADER_LINE}\n"
            "emulator/x/y_test.cc:3:10: fatal error: "
            "'goldfish/async/testing/test_event_loop.h' file not found\n"
            "ERROR: undeclared inclusion(s) in rule '//emulator/x:y': "
            "'external/goldfish+/emulator/libs/async/include/goldfish/async/libuv_event_loop.h'\n"
        )
        diagnostic = diagnose_command_failure("bazel build //emulator/x:y", log)
        self.assertIsNotNone(diagnostic)
        self.assertIn("`//emulator/libs/http:web_server`", diagnostic)
        self.assertIn("`//emulator/libs/async:async_testing`", diagnostic)
        self.assertIn("`//emulator/libs/async:libuv_event_loop`", diagnostic)
        self.assertNotIn("`//emulator/libs/http`", diagnostic)
        self.assertNotIn("async_api", diagnostic)

    def test_include_frames_are_not_reported_as_missing_headers(self) -> None:
        frame = "external/goldfish+/emulator/libs/async/include/goldfish/async/libuv_event_loop.h"
        log = f"In file included from {frame}:22:\n{frame}:22:10: fatal error: 'uv.h' file not found\n"
        self.assertIsNone(diagnose_command_failure("bazel build //emulator/x:y", log))

    def test_long_tokens_and_blank_lines_are_scanned_in_linear_time(self) -> None:
        # Both inputs took several seconds with the previous backtracking patterns,
        # close to the 10s PostToolUse hook timeout.
        started = time.monotonic()
        self.assertIsNone(
            diagnose_command_failure("bazel build //x:y", "a" * 40000 + " No such file or directory")
        )
        self.assertIsNone(enrich_post_tool_result("ls", {"result": " \n" * 20000}))
        self.assertLess(time.monotonic() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
