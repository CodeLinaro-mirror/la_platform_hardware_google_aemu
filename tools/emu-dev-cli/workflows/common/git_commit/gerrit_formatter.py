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

"""Gerrit Commit Message Formatter and Linter.

Implements the Gerrit format protocol for commit messages, providing parsing,
validation, lint error detection, and formatting.
"""

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import List, Optional


class ErrorType(str, Enum):
    SUBJECT_TOO_LONG = "SUBJECT_TOO_LONG"
    LINE_TOO_LONG = "LINE_TOO_LONG"
    MISSING_BLANK_LINE = "MISSING_BLANK_LINE"
    EXTRA_BLANK_LINE = "EXTRA_BLANK_LINE"
    INVALID_INDENTATION = "INVALID_INDENTATION"
    TRAILING_SPACES = "TRAILING_SPACES"
    COMMENT_LINE = "COMMENT_LINE"
    LEADING_SPACES = "LEADING_SPACES"


@dataclass
class FormattingError:
    type: ErrorType
    message: str
    line: Optional[int] = None


@dataclass
class CommitMessage:
    subject: str = ""
    body: List[str] = field(default_factory=list)
    footer: List[str] = field(default_factory=list)
    has_trailing_blank_line: bool = False


MAX_SUBJECT_LENGTH: int = 72
MAX_LINE_LENGTH: int = 72
INDENTATION_THRESHOLD: int = 4
BULLET_POINT_REGEX: re.Pattern = re.compile(r"^\s*[-+*#]\s")
FOOTER_REGEX: re.Pattern = re.compile(r"^([\w-]+)(?::[ \t]+|=)(.*)$")


def port_footers_from_body(message: CommitMessage) -> CommitMessage:
    """Check if last line of 'Body' follows the footer format and transfer it to the footer section.

    So if the commit message is:
    Foo

    Footer1: val1

    Footer2: val2

    Current message.footer will only contain val2 and the rest will be considered in the body.
    Note: This does not support multi-line footers.
    """
    footers_to_port_over: List[str] = []
    for i in range(len(message.body) - 1, -1, -1):
        line = message.body[i]
        match = FOOTER_REGEX.match(line)
        # If it's a footer line or an empty blank line, then we remove it from the body
        if match or line.strip() == "":
            if line.strip() != "":
                footers_to_port_over.append(line)
            message.body.pop()
        else:
            break

    if footers_to_port_over:
        footers_to_port_over.reverse()
        message.footer = footers_to_port_over + message.footer

    return message


def format_subject(subject: str) -> str:
    """Formats the commit subject line by stripping whitespace."""
    return subject.strip()


def is_untouched_line(line: str, previous_was_empty: bool) -> bool:
    """Returns true if the line will not be modified by the formatter.

    For example, quotes, bullet points, and indented lines are untouched.
    """
    return bool(
        line.lstrip().startswith("> ")
        or (
            len(line) >= INDENTATION_THRESHOLD
            and line[:INDENTATION_THRESHOLD].strip() == ""
        )
        or BULLET_POINT_REGEX.search(line)
        or (
            not previous_was_empty
            and len(line.lstrip()) > 0
            and line != line.lstrip()
        )
    )


def split_paragraph(paragraph: str) -> List[str]:
    """Splits a single-line paragraph into wrapped lines of at most MAX_LINE_LENGTH."""
    words = re.split(r"\s+", paragraph.strip())
    if not words or words == [""]:
        return []

    lines: List[str] = []
    current_line = ""

    for word in words:
        if len(word) > MAX_LINE_LENGTH:
            if current_line:
                lines.append(current_line)
                current_line = ""
            lines.append(word)
        elif current_line and len(current_line) + len(word) + 1 > MAX_LINE_LENGTH:
            lines.append(current_line)
            current_line = word
        else:
            current_line += (" " if current_line else "") + word

    if current_line:
        lines.append(current_line)

    return lines


def remove_consecutive_blank_lines(lines: List[str]) -> List[str]:
    """Removes consecutive blank lines as well as leading and trailing blank lines."""
    filtered_lines: List[str] = []
    previous_line_blank = False

    for line in lines:
        is_blank = line.strip() == ""
        if not is_blank or not previous_line_blank:
            filtered_lines.append(line)
        previous_line_blank = is_blank

    # Remove leading and trailing blank lines
    while filtered_lines and filtered_lines[0].strip() == "":
        filtered_lines.pop(0)
    while filtered_lines and filtered_lines[-1].strip() == "":
        filtered_lines.pop()

    return filtered_lines


def format_body(body: List[str]) -> List[str]:
    """Formats the commit body, wrapping regular paragraphs and preserving code/quotes/lists."""
    in_code_block = False
    paragraph_lines: List[str] = []
    formatted_body: List[str] = []
    previous_was_bullet_point = False
    previous_was_empty = True  # Track if previous line was empty

    for line in body:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            formatted_body.append(line.rstrip())
            previous_was_empty = False
            continue

        if in_code_block or is_untouched_line(line, previous_was_empty):
            if not in_code_block:
                previous_was_bullet_point = bool(BULLET_POINT_REGEX.search(line))
            if paragraph_lines:
                formatted_body.extend(split_paragraph(" ".join(paragraph_lines)))
                paragraph_lines = []
            formatted_body.append(line.rstrip())
            previous_was_empty = False
            continue

        if previous_was_bullet_point and line.startswith("  "):
            formatted_body.append(line.rstrip())
            previous_was_empty = False
            continue

        if line.strip() == "":
            if paragraph_lines:
                formatted_body.extend(split_paragraph(" ".join(paragraph_lines)))
                paragraph_lines = []
            formatted_body.append("")
            previous_was_bullet_point = False
            previous_was_empty = True
        else:
            paragraph_lines.append(line.strip())
            previous_was_empty = False

    if paragraph_lines:
        formatted_body.extend(split_paragraph(" ".join(paragraph_lines)))

    return remove_consecutive_blank_lines(formatted_body)


def format_footer(footer: List[str]) -> List[str]:
    """Formats the commit footer lines."""
    formatted_footer = [line.strip() for line in footer]
    return remove_consecutive_blank_lines(formatted_footer)


def format_commit_message(message: CommitMessage) -> CommitMessage:
    """Formats a CommitMessage object according to Gerrit standards."""
    formatted_subject = format_subject(message.subject)
    msg_copy = CommitMessage(
        subject=message.subject,
        body=list(message.body),
        footer=list(message.footer),
        has_trailing_blank_line=message.has_trailing_blank_line,
    )
    msg_copy = port_footers_from_body(msg_copy)
    formatted_body = format_body(msg_copy.body)
    formatted_footer = format_footer(msg_copy.footer)

    return CommitMessage(
        subject=formatted_subject,
        body=formatted_body,
        footer=formatted_footer,
        has_trailing_blank_line=msg_copy.has_trailing_blank_line,
    )


def find_start_of_paragraph(lines: List[str], last_line_in_paragraph: int) -> int:
    """Finds the start index of the paragraph ending at last_line_in_paragraph."""
    for i in range(last_line_in_paragraph, -1, -1):
        if lines[i].strip() == "":
            return i + 1
    # On line 0 is subject
    return 1


def first_non_empty_line_index(lines: List[str], index: int, direction: int) -> int:
    """Returns the index of the first non-empty line in the given direction (1 or -1)."""
    while 0 <= index < len(lines) and lines[index].strip() == "":
        index += direction
    return index


def parse_commit_message_string(message_string: str) -> CommitMessage:
    """Parses a raw commit message string into structured CommitMessage."""
    lines = message_string.split("\n")
    # Remove leading blank lines
    while lines and lines[0].strip() == "":
        lines.pop(0)

    subject = ""
    body: List[str] = []
    footer: List[str] = []
    has_trailing_blank_line = False

    if len(lines) == 0:
        return CommitMessage(subject, body, footer, has_trailing_blank_line)

    if len(lines) == 1:
        subject = lines[0]
        return CommitMessage(subject, body, footer, has_trailing_blank_line)

    subject = lines[0]  # Subject is always the first line
    has_trailing_blank_line = len(lines) > 0 and lines[-1].strip() == ""

    last_line = len(lines) - 2 if has_trailing_blank_line else len(lines) - 1
    footer_start_index = find_start_of_paragraph(lines, last_line)

    footer = list(lines[footer_start_index:])
    if has_trailing_blank_line and footer:
        footer.pop()

    # Extract body lines, removing all leading/trailing blank lines
    start_body = first_non_empty_line_index(lines, 1, 1)
    end_body = first_non_empty_line_index(lines, footer_start_index - 1, -1)
    if start_body <= end_body and start_body < len(lines) and end_body >= 0:
        body = list(lines[start_body : end_body + 1])
    else:
        body = []

    # Check if footer contains any lines in the format "key: value"
    # If not, move footer lines to body and make footer empty
    has_formatted_footer_line = any(bool(FOOTER_REGEX.search(line.strip())) for line in footer)
    if not has_formatted_footer_line and footer:
        if body:
            body.append("")
        body.extend(footer)
        footer = []

    return CommitMessage(
        subject=subject,
        body=body,
        footer=footer,
        has_trailing_blank_line=has_trailing_blank_line,
    )


def format_commit_message_to_string(message: CommitMessage) -> str:
    """Serializes a CommitMessage back into a standard formatted string."""
    result = message.subject
    if message.body:
        result += "\n\n" + "\n".join(message.body)
    if message.footer:
        result += "\n\n" + "\n".join(message.footer)
    if message.has_trailing_blank_line:
        result += "\n"
    return result


def format_commit_message_string(message_string: str) -> str:
    """Convenience helper to parse and format a commit message string in one pass."""
    commit_message = parse_commit_message_string(message_string)
    formatted_message = format_commit_message(commit_message)
    return format_commit_message_to_string(formatted_message)


def detect_formatting_errors(
    message: CommitMessage,
    message_string: str,
) -> List[FormattingError]:
    """Detects formatting errors in a CommitMessage according to Gerrit protocol."""
    errors: List[FormattingError] = []

    # Check subject
    if len(message.subject) > MAX_SUBJECT_LENGTH:
        errors.append(
            FormattingError(
                type=ErrorType.SUBJECT_TOO_LONG,
                line=1,
                message=f"Subject exceeds {MAX_SUBJECT_LENGTH} characters",
            )
        )

    if message.subject.startswith(" "):
        errors.append(
            FormattingError(
                type=ErrorType.LEADING_SPACES,
                line=1,
                message="Subject should not start with spaces",
            )
        )

    if message.subject.endswith(" "):
        errors.append(
            FormattingError(
                type=ErrorType.TRAILING_SPACES,
                line=1,
                message="Subject should not end with spaces",
            )
        )

    lines = message_string.split("\n")
    for i in range(len(lines)):
        if lines[i].strip().startswith("#"):
            errors.append(
                FormattingError(
                    type=ErrorType.COMMENT_LINE,
                    line=i + 1,  # Line numbers are 1-based
                    message="'#' at line start is a comment marker in Git. Line will be ignored",
                )
            )

    # Check for extra blank lines using the raw message_string
    for i in range(len(lines)):
        if i > 0 and lines[i].strip() == "" and lines[i - 1].strip() == "":
            is_between_subject_and_body = i == 1 and len(message.body) > 0
            is_between_body_and_footer = (
                i == len(message.body) + (1 if message.subject else 0)
                and len(message.footer) > 0
            )
            is_at_end_of_footer = i == len(lines) - 1 and len(message.footer) > 0

            if (
                not is_between_subject_and_body
                and not is_between_body_and_footer
                and not is_at_end_of_footer
            ):
                errors.append(
                    FormattingError(
                        type=ErrorType.EXTRA_BLANK_LINE,
                        line=i + 1,  # Line numbers are 1-based
                        message="Consecutive blank lines are not allowed",
                    )
                )

    # Check body
    line_number = 3
    in_code_block = False

    for line in message.body:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block

        if (
            not in_code_block
            and not is_untouched_line(line, False)
            and len(line) > MAX_LINE_LENGTH
            and "://" not in line  # Don't flag long URLs
        ):
            errors.append(
                FormattingError(
                    type=ErrorType.LINE_TOO_LONG,
                    line=line_number,
                    message=f"Line exceeds {MAX_LINE_LENGTH} characters",
                )
            )
        if line.endswith(" "):
            errors.append(
                FormattingError(
                    type=ErrorType.TRAILING_SPACES,
                    line=line_number,
                    message="Line should not end with spaces",
                )
            )
        line_number += 1

    # Check footer
    line_number = len(message.body) + 4
    for line in message.footer:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block

        if (
            not in_code_block
            and not is_untouched_line(line, False)
            and len(line) > MAX_LINE_LENGTH
            and "://" not in line  # Don't flag long URLs
        ):
            errors.append(
                FormattingError(
                    type=ErrorType.LINE_TOO_LONG,
                    line=line_number,
                    message=f"Line exceeds {MAX_LINE_LENGTH} characters",
                )
            )
        if line.endswith(" "):
            errors.append(
                FormattingError(
                    type=ErrorType.TRAILING_SPACES,
                    line=line_number,
                    message="Line should not end with spaces",
                )
            )
        if line.startswith(" "):
            errors.append(
                FormattingError(
                    type=ErrorType.LEADING_SPACES,
                    line=line_number,
                    message="Line should not start with spaces",
                )
            )
        line_number += 1

    return errors


def detect_formatting_errors_in_string(message_string: str) -> List[FormattingError]:
    """Detects formatting errors in a raw commit message string according to Gerrit protocol."""
    commit_message = parse_commit_message_string(message_string)
    return detect_formatting_errors(commit_message, message_string)
