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

"""Quote-aware splitting of shell command lines into top-level command segments.

Hooks match command patterns per segment so that text inside quoted arguments
(for example a `grep "bazel test"` pattern or a `git commit -m` message), heredoc
bodies, and `#` comments is never mistaken for a command.
"""

import os
import re
from typing import Iterator, List, Optional, Tuple

_SEGMENT_SEPARATORS = frozenset(";&|\n(")
_QUOTES = frozenset("'\"")
# `<<WORD`, `<<-WORD`, `<<'WORD'` or `<<"WORD"`, but not a `<<<` here-string.
_HEREDOC_START_PATTERN = re.compile(r"(?<!<)<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def _starts_comment(command: str, index: int) -> bool:
    """Returns True if the unquoted `#` at `index` begins a word, i.e. a shell comment."""
    if index == 0:
        return True
    previous = command[index - 1]
    return previous.isspace() or previous in _SEGMENT_SEPARATORS


def _heredoc_body_end(command: str, delimiter: str, body_start: int) -> int:
    """Returns the offset just past the line that closes a heredoc body.

    An unterminated heredoc runs to the end of the command, as in bash.
    """
    closing_line = re.compile(rf"(?m)^\t*{re.escape(delimiter)}[ \t]*$")
    match = closing_line.search(command, body_start)
    return len(command) if match is None else match.end()


def iter_command_segments(
    command: str,
    posix: Optional[bool] = None,
) -> Iterator[Tuple[int, str]]:
    """Yields `(start_offset, text)` for each top-level command in `command`.

    Splits on `;`, `&`, `|` (and therefore `&&` / `||`), newlines, and `(` when they
    appear outside single or double quotes. Heredoc bodies and `#` comments are
    skipped, so an apostrophe or a command inside them neither opens a quote nor
    yields a segment. Backslash escapes are honored outside single quotes when
    `posix` is true, which is the default everywhere except Windows: cmd.exe and
    PowerShell treat `\\` as a path separator (the same rule as
    `shlex.split(..., posix=(os.name != "nt"))`). Whitespace-only segments are
    skipped; `command[start:start + len(text)] == text` holds for every segment.
    """
    if posix is None:
        posix = os.name != "nt"
    start = 0
    index = 0
    quote = ""
    pending_heredocs: List[str] = []
    length = len(command)
    while index < length:
        char = command[index]
        if posix and char == "\\" and quote != "'" and index + 1 < length:
            index += 2
            continue
        if quote:
            if char == quote:
                quote = ""
        elif char in _QUOTES:
            quote = char
        elif char == "#" and _starts_comment(command, index):
            if command[start:index].strip():
                yield start, command[start:index]
            newline = command.find("\n", index)
            index = length if newline < 0 else newline
            start = index
            continue
        elif char == "<" and (heredoc := _HEREDOC_START_PATTERN.match(command, index)):
            pending_heredocs.append(heredoc.group(2))
            index = heredoc.end()
            continue
        elif char in _SEGMENT_SEPARATORS:
            if command[start:index].strip():
                yield start, command[start:index]
            start = index + 1
            if char == "\n" and pending_heredocs:
                for delimiter in pending_heredocs:
                    start = _heredoc_body_end(command, delimiter, start)
                pending_heredocs.clear()
                index = start
                continue
        index += 1
    if command[start:].strip():
        yield start, command[start:]


def find_command_segment(
    command: str,
    pattern: "re.Pattern[str]",
    posix: Optional[bool] = None,
) -> Optional[Tuple[int, str, "re.Match[str]"]]:
    """Returns `(start_offset, text, match)` for the first segment that `pattern` matches.

    `pattern` is matched (anchored) against each top-level segment from
    `iter_command_segments`. Returns None if no segment matches.
    """
    for start, text in iter_command_segments(command, posix=posix):
        match = pattern.match(text)
        if match:
            return start, text, match
    return None
