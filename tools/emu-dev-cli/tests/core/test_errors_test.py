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

"""Hermetic unit tests for core.errors exception hierarchy and secret redaction."""

import os
import sys
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from core.errors import (
    AuthError,
    ERROR_CODE_AUTH,
    ERROR_CODE_EXECUTION,
    ERROR_CODE_RETRYABLE,
    ERROR_CODE_SUBPROCESS,
    ERROR_CODE_USAGE,
    EmuCliError,
    ExecutionError,
    ExitCode,
    REDACTED_PLACEHOLDER,
    RetryableError,
    SubprocessError,
    UsageError,
    redact_command_args,
    redact_sensitive_text,
    sanitize_details,
)


class ErrorsTest(unittest.TestCase):
    """Tests typed EmuCliError subclasses, canonical exit codes, and secret redaction."""

    def test_canonical_exit_codes_and_error_codes(self) -> None:
        self.assertEqual(int(ExitCode.SUCCESS), 0)
        self.assertEqual(int(ExitCode.FAILURE), 1)
        self.assertEqual(int(ExitCode.RETRY_OR_LOOP), 2)
        self.assertEqual(int(ExitCode.AUTH_ERROR), 3)
        self.assertEqual(int(ExitCode.USAGE_ERROR), 4)

        usage_err = UsageError("Invalid flag '--foo'")
        self.assertEqual(usage_err.exit_code, 4)
        self.assertEqual(usage_err.error_code, ERROR_CODE_USAGE)

        exec_err = ExecutionError("Build failed")
        self.assertEqual(exec_err.exit_code, 1)
        self.assertEqual(exec_err.error_code, ERROR_CODE_EXECUTION)

        auth_err = AuthError("LOAS certificate expired")
        self.assertEqual(auth_err.exit_code, 3)
        self.assertEqual(auth_err.error_code, ERROR_CODE_AUTH)

        retry_err = RetryableError("New review comments arrived")
        self.assertEqual(retry_err.exit_code, 2)
        self.assertEqual(retry_err.error_code, ERROR_CODE_RETRYABLE)

    def test_cause_preservation_and_human_formatting(self) -> None:
        root_cause = OSError("No space left on device: /tmp/测试_🚀_é")
        err = ExecutionError(
            "Failed to stage artifact '/tmp/测试_🚀_é/emulator.zip'.",
            remediation="Free disk space in /tmp and retry.",
            details={"path": "/tmp/测试_🚀_é/emulator.zip"},
            cause=root_cause,
        )
        self.assertIs(err.__cause__, root_cause)
        formatted = err.format_human()
        self.assertIn("[EXECUTION_ERROR]", formatted)
        self.assertIn("/tmp/测试_🚀_é/emulator.zip", formatted)
        self.assertIn("Next step: Free disk space in /tmp and retry.", formatted)

        payload = err.to_dict(action="fetch-build")
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["action"], "fetch-build")
        self.assertEqual(payload["error_code"], ERROR_CODE_EXECUTION)
        self.assertEqual(payload["exit_code"], 1)
        self.assertEqual(payload["details"]["path"], "/tmp/测试_🚀_é/emulator.zip")

    def test_secret_redaction_in_messages_commands_and_details(self) -> None:
        raw_text = "Request failed with Authorization: Bearer fake_bearer_token_123 and --token=my_secret_abc"
        redacted = redact_sensitive_text(raw_text)
        self.assertNotIn("fake_bearer_token_123", redacted)
        self.assertNotIn("my_secret_abc", redacted)
        self.assertIn(REDACTED_PLACEHOLDER, redacted)

        cmd = [
            "gob-curl",
            "--token",
            "super-secret-value",
            "--api-key=key_98765",
            "https://example.com/路径_🚀",
        ]
        safe_cmd = redact_command_args(cmd)
        self.assertEqual(
            safe_cmd,
            (
                "gob-curl",
                "--token",
                REDACTED_PLACEHOLDER,
                f"--api-key={REDACTED_PLACEHOLDER}",
                "https://example.com/路径_🚀",
            ),
        )

        details = sanitize_details(
            {
                "access_token": "tok_secret",
                "nested": {"cookie": "SID=12345", "target": "emulator_linux_x64"},
                "notes": ["Bearer secret_jwt_val", "normal_note_测试"],
            }
        )
        self.assertEqual(details["access_token"], REDACTED_PLACEHOLDER)
        self.assertEqual(details["nested"]["cookie"], REDACTED_PLACEHOLDER)
        self.assertEqual(details["nested"]["target"], "emulator_linux_x64")
        self.assertNotIn("secret_jwt_val", details["notes"][0])
        self.assertEqual(details["notes"][1], "normal_note_测试")

    def test_subprocess_error_captures_and_redacts_command_context(self) -> None:
        sub_err = SubprocessError(
            "Subprocess exited with code 128",
            cmd=["git", "--password", "hunter2", "fetch"],
            returncode=128,
            stdout="fetching...",
            stderr="fatal: Authentication failed for --token=xyz987",
            timed_out=False,
            remediation="Run gcert to refresh SSO credentials.",
        )
        self.assertIsInstance(sub_err, EmuCliError)
        self.assertEqual(sub_err.error_code, ERROR_CODE_SUBPROCESS)
        self.assertEqual(sub_err.cmd, ("git", "--password", REDACTED_PLACEHOLDER, "fetch"))
        self.assertNotIn("xyz987", sub_err.stderr)
        self.assertEqual(sub_err.details["returncode"], 128)
        self.assertFalse(sub_err.details["timed_out"])


if __name__ == "__main__":
    unittest.main()
