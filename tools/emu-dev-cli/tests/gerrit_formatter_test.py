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

import os
import sys
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_EMU_DEV_CLI_DIR = os.path.dirname(_TESTS_DIR)
_WORKFLOWS_DIR = os.path.join(_EMU_DEV_CLI_DIR, "workflows")
_SRC_DIR = os.path.join(_EMU_DEV_CLI_DIR, "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
if _EMU_DEV_CLI_DIR not in sys.path:
    sys.path.insert(0, _EMU_DEV_CLI_DIR)
if _WORKFLOWS_DIR not in sys.path:
    sys.path.insert(0, _WORKFLOWS_DIR)

from workflows.common.git_commit import (
    CommitMessage,
    ErrorType,
    FormattingError,
    detect_formatting_errors,
    detect_formatting_errors_in_string,
    format_body,
    format_commit_message,
    format_commit_message_string,
    format_commit_message_to_string,
    format_footer,
    format_subject,
    is_untouched_line,
    parse_commit_message_string,
    port_footers_from_body,
    remove_consecutive_blank_lines,
    split_paragraph,
    validate_commit_message,
)


class TestGerritFormatter(unittest.TestCase):

    def test_parse_commit_message_empty(self):
        msg = parse_commit_message_string("")
        self.assertEqual(msg.subject, "")
        self.assertEqual(msg.body, [])
        self.assertEqual(msg.footer, [])
        self.assertFalse(msg.has_trailing_blank_line)

    def test_parse_commit_message_single_line(self):
        msg = parse_commit_message_string("feat: Single subject line")
        self.assertEqual(msg.subject, "feat: Single subject line")
        self.assertEqual(msg.body, [])
        self.assertEqual(msg.footer, [])
        self.assertFalse(msg.has_trailing_blank_line)

    def test_parse_commit_message_subject_and_body_no_footer(self):
        raw = "feat: Subject\n\nThis is paragraph one.\n\nThis is paragraph two."
        msg = parse_commit_message_string(raw)
        self.assertEqual(msg.subject, "feat: Subject")
        # Since the last paragraph has no FOOTER_REGEX lines, it is ported to body
        self.assertEqual(msg.body, ["This is paragraph one.", "", "This is paragraph two."])
        self.assertEqual(msg.footer, [])
        self.assertFalse(msg.has_trailing_blank_line)

    def test_parse_commit_message_with_footer_and_trailing_newline(self):
        raw = (
            "feat: Subject\n\n"
            "Body line 1\n"
            "Body line 2\n\n"
            "Bug: 12345678\n"
            "Test: bazel test //...\n"
        )
        msg = parse_commit_message_string(raw)
        self.assertEqual(msg.subject, "feat: Subject")
        self.assertEqual(msg.body, ["Body line 1", "Body line 2"])
        self.assertEqual(msg.footer, ["Bug: 12345678", "Test: bazel test //..."])
        self.assertTrue(msg.has_trailing_blank_line)

    def test_port_footers_from_body(self):
        cm = CommitMessage(
            subject="feat: Title",
            body=["Description line", "", "Bug: 12345", "Test: N/A"],
            footer=["Change-Id: Iabcdef"],
            has_trailing_blank_line=True,
        )
        ported = port_footers_from_body(cm)
        self.assertEqual(ported.body, ["Description line"])
        self.assertEqual(
            ported.footer,
            ["Bug: 12345", "Test: N/A", "Change-Id: Iabcdef"],
        )

    def test_is_untouched_line(self):
        # Quotes
        self.assertTrue(is_untouched_line("> Quoted text", False))
        self.assertTrue(is_untouched_line("  > Indented quote", False))

        # Indented blocks (>= 4 spaces)
        self.assertTrue(is_untouched_line("    code indentation", False))
        self.assertTrue(is_untouched_line("\t\tcode tabs", False))

        # Bullet points
        self.assertTrue(is_untouched_line("- Item 1", False))
        self.assertTrue(is_untouched_line("* Item 2", False))
        self.assertTrue(is_untouched_line("+ Item 3", False))
        self.assertTrue(is_untouched_line("# Item 4", False))
        self.assertTrue(is_untouched_line("  - Nested bullet", False))

        # List continuation (indentation when previousWasEmpty=False)
        self.assertTrue(is_untouched_line("  continuation of bullet", previous_was_empty=False))
        self.assertFalse(is_untouched_line("  continuation after blank", previous_was_empty=True))

        # Regular lines
        self.assertFalse(is_untouched_line("Normal text line", False))
        self.assertFalse(is_untouched_line("Another normal line", True))

    def test_split_paragraph(self):
        short = "This is a short line under 72 chars."
        self.assertEqual(split_paragraph(short), [short])

        long_para = (
            "This is a very long line that definitely exceeds seventy-two characters "
            "and should be wrapped cleanly across multiple lines without breaking words."
        )
        wrapped = split_paragraph(long_para)
        self.assertGreater(len(wrapped), 1)
        for line in wrapped:
            self.assertLessEqual(len(line), 72)

        # Word longer than 72 characters
        huge_word = "A" * 80
        mixed = f"Intro text {huge_word} trailing text"
        mixed_wrapped = split_paragraph(mixed)
        self.assertEqual(mixed_wrapped[0], "Intro text")
        self.assertEqual(mixed_wrapped[1], huge_word)
        self.assertEqual(mixed_wrapped[2], "trailing text")

    def test_remove_consecutive_blank_lines(self):
        lines = ["", "  ", "First", "", "", "Second", "", "", "", "Third", "", " "]
        cleaned = remove_consecutive_blank_lines(lines)
        self.assertEqual(cleaned, ["First", "", "Second", "", "Third"])

    def test_format_body_with_code_blocks_and_bullets(self):
        body = [
            "This is a long introductory paragraph that should be wrapped cleanly by the formatter "
            "into multiple lines of maximum length 72 characters each.",
            "",
            "- Bullet point 1",
            "  Continuation of bullet point 1",
            "- Bullet point 2",
            "",
            "```",
            "def foo():",
            "    long_code_line_that_should_not_be_wrapped_at_all_because_it_is_in_code_block = 123",
            "```",
            "",
            "Final concluding sentence.",
        ]
        formatted = format_body(body)
        # Verify code block was untouched
        self.assertIn("```", formatted)
        self.assertIn(
            "    long_code_line_that_should_not_be_wrapped_at_all_because_it_is_in_code_block = 123",
            formatted,
        )
        # Verify bullet points were untouched
        self.assertIn("- Bullet point 1", formatted)
        self.assertIn("  Continuation of bullet point 1", formatted)
        self.assertIn("- Bullet point 2", formatted)
        # Verify consecutive blanks are removed
        for i in range(len(formatted) - 1):
            self.assertFalse(formatted[i] == "" and formatted[i + 1] == "")

    def test_format_commit_message_full(self):
        raw = (
            "  feat: Clean commit subject  \n\n"
            "This is a paragraph that needs wrapping because it is quite long and extends beyond the 72 character limit.\n\n"
            "Bug: 12345678  \n"
            "Test: bazel test //...  \n\n"
        )
        formatted = format_commit_message_string(raw)
        self.assertTrue(formatted.startswith("feat: Clean commit subject\n\n"))
        self.assertIn("Bug: 12345678", formatted)
        self.assertIn("Test: bazel test //...", formatted)
        self.assertTrue(formatted.endswith("\n"))

    def test_detect_formatting_errors_subject(self):
        # Subject too long
        long_subject = "feat: " + "a" * 70
        errors = detect_formatting_errors_in_string(long_subject + "\n\nBug: 1\nTest: N/A\n")
        self.assertTrue(any(e.type == ErrorType.SUBJECT_TOO_LONG for e in errors))

        # Leading spaces in subject
        errors = detect_formatting_errors_in_string(" feat: Title\n\nBug: 1\nTest: N/A\n")
        self.assertTrue(any(e.type == ErrorType.LEADING_SPACES for e in errors))

        # Trailing spaces in subject
        errors = detect_formatting_errors_in_string("feat: Title \n\nBug: 1\nTest: N/A\n")
        self.assertTrue(any(e.type == ErrorType.TRAILING_SPACES for e in errors))

    def test_detect_formatting_errors_comments(self):
        raw = "feat: Title\n\n# This is a git comment line\n\nBug: 1\nTest: N/A\n"
        errors = detect_formatting_errors_in_string(raw)
        self.assertTrue(any(e.type == ErrorType.COMMENT_LINE for e in errors))

    def test_detect_formatting_errors_consecutive_blank_lines(self):
        raw = (
            "feat: Title\n\n\n"
            "Body line 1\n"
            "Body line 2\n\n"
            "Bug: 1\n"
            "Test: N/A\n"
        )
        errors = detect_formatting_errors_in_string(raw)
        self.assertTrue(any(e.type == ErrorType.EXTRA_BLANK_LINE for e in errors))

    def test_detect_formatting_errors_line_too_long(self):
        long_line = "b" * 75
        raw = f"feat: Title\n\n{long_line}\n\nBug: 1\nTest: N/A\n"
        errors = detect_formatting_errors_in_string(raw)
        self.assertTrue(any(e.type == ErrorType.LINE_TOO_LONG for e in errors))

        # URL longer than 72 chars should NOT trigger LINE_TOO_LONG
        long_url = "See https://android.googlesource.com/platform/hardware/google/aemu/+/refs/heads/emu-main-next"
        raw_url = f"feat: Title\n\n{long_url}\n\nBug: 1\nTest: N/A\n"
        errors_url = detect_formatting_errors_in_string(raw_url)
        self.assertFalse(any(e.type == ErrorType.LINE_TOO_LONG for e in errors_url))

    def test_detect_formatting_errors_trailing_and_leading_spaces(self):
        raw_body_trailing = "feat: Title\n\nBody line with trailing space \n\nBug: 1\nTest: N/A\n"
        errors = detect_formatting_errors_in_string(raw_body_trailing)
        self.assertTrue(any(e.type == ErrorType.TRAILING_SPACES for e in errors))

        raw_footer_leading = "feat: Title\n\nBody.\n\n Bug: 1\nTest: N/A\n"
        errors_f = detect_formatting_errors_in_string(raw_footer_leading)
        self.assertTrue(any(e.type == ErrorType.LEADING_SPACES for e in errors_f))

    def test_validate_commit_message_integration(self):
        # Valid message passes both Gerrit and emu-dev-cli rules
        valid = (
            "feat: Implement feature\n\n"
            "This is a proper description.\n\n"
            "Bug: 12345678\n"
            "Test: bazel test //...\n"
        )
        self.assertEqual(validate_commit_message(valid), [])

        # Message with subject > 72 chars rejected by validate_commit_message
        invalid_subject = ("x" * 75) + "\n\nBug: 1\nTest: N/A\n"
        errors = validate_commit_message(invalid_subject)
        self.assertTrue(any("Subject exceeds 72 characters" in e for e in errors))

        # Message with trailing spaces rejected
        trailing_msg = "feat: Title \n\nBody.\n\nBug: 1\nTest: N/A\n"
        errors = validate_commit_message(trailing_msg)
        self.assertTrue(any("Subject should not end with spaces" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
