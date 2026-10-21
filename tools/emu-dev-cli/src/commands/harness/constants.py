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

"""Named constants and enums for the Android Emulator Jetski Harness."""

from enum import Enum
import re


class HookEvent(str, Enum):
    """Supported Jetski lifecycle hook events."""

    SESSION_START = "session-start"
    PRE_TOOL = "pre-tool"
    POST_TOOL = "post-tool"
    PRE_INVOCATION = "pre-invocation"
    STOP = "stop"


class HookDecision(str, Enum):
    """PreToolUse and Stop hook decision values."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    CONTINUE = "continue"
    STOP = "stop"


class LedgerEventKind(str, Enum):
    """Types of events recorded in the per-conversation harness ledger."""

    GUARDRAIL_BLOCKED = "guardrail_blocked"
    GUARDRAIL_ALLOWED = "guardrail_allowed"
    CODE_GUARDRAIL_BLOCKED = "code_guardrail_blocked"
    COMMAND_REWRITTEN = "command_rewritten"
    DIAGNOSTIC_INJECTED = "diagnostic_injected"
    EMULATOR_LAUNCHED = "emulator_launched"
    EMULATOR_STOPPED = "emulator_stopped"
    WORKFLOW_UPDATED = "workflow_updated"
    STOP_CONTINUED = "stop_continued"


PLUGIN_DIR_NAME: str = "aemu-harness"
SIDE_CAR_DIR_NAME: str = "aemu_harness_ui"
DEFAULT_LS_ADDRESS: str = "localhost:5387"

# Maximum number of automatic Stop-hook continuations for an active workflow
# before yielding control back to the user to prevent infinite loops.
MAX_STOP_CONTINUATIONS: int = 3

# Maximum number of recent hook events retained per conversation ledger.
MAX_LEDGER_EVENTS: int = 50

# Regex patterns for command inspection in PreToolUse / PostToolUse
GIT_COMMIT_CMD_PATTERN: re.Pattern[str] = re.compile(
    r"(?:^|[;&|]\s*|\b)git\s+(?:-C\s+\S+\s+)*commit\b"
)
GIT_COMMIT_AMEND_PATTERN: re.Pattern[str] = re.compile(r"(?:^|\s)--amend\b")
GIT_COMMIT_NO_EDIT_PATTERN: re.Pattern[str] = re.compile(r"(?:^|\s)--no-edit\b")
GIT_COMMIT_MSG_FLAG_PATTERN: re.Pattern[str] = re.compile(
    r"(?:^|\s)-m\s+(?:\$'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"|'([^']*)'|(\S+))"
)
_BATCH_OR_EXE_EXT_RE: str = r"(?:\.(?:cmd|bat|exe))?"
_PY_BIN_NAME_RE: str = r"py(?:thon3?)?"
_EMU_CLI_NAME_RE: str = r"(?:emu-dev-cli|__main__\.py)"
_QUOTED_ARG_RE: str = r"(?:\"([^\"]+)\"|'([^']+)'|(\S+))"


def _cli_exe_re(name_re: str, ext_re: str = r"(?:\.exe)?") -> str:
    """Builds a linear-time pattern matching quoted or unquoted CLI binary paths."""
    return (
        rf"(?:\"[^\"]*{name_re}{ext_re}\""
        rf"|'[^']*{name_re}{ext_re}'"
        rf"|[^\s\"';&|]*{name_re}{ext_re})"
    )


_PY_LAUNCHER_RE: str = (
    rf"(?:{_cli_exe_re(_PY_BIN_NAME_RE)}\s+(?:-\d(?:\.\d+)?\s+)?)?"
)
_REPO_UPLOAD_CMD_RE: str = (
    rf"{_PY_LAUNCHER_RE}{_cli_exe_re('repo', _BATCH_OR_EXE_EXT_RE)}\s+upload\b"
)
_EMU_CLI_CMD_RE: str = (
    rf"{_PY_LAUNCHER_RE}"
    rf"{_cli_exe_re(_EMU_CLI_NAME_RE, _BATCH_OR_EXE_EXT_RE)}"
)

GIT_COMMIT_FILE_FLAG_PATTERN: re.Pattern[str] = re.compile(
    rf"(?:^|\s)(?:-F|--file)(?:=|\s+){_QUOTED_ARG_RE}"
)
HELP_FLAG_PATTERN: re.Pattern[str] = re.compile(r"(?:^|\s)(?:-h|--help)\b")
REPO_UPLOAD_BRANCH_FLAG_PATTERN: re.Pattern[str] = re.compile(
    rf"(?:^|\s)--br(?:anch)?(?:=|\s+){_QUOTED_ARG_RE}"
)
REPO_UPLOAD_TAIL_PATTERN: re.Pattern[str] = re.compile(
    rf"(?m)(?:^|[;&|]\s*){_REPO_UPLOAD_CMD_RE}([^;&|]*)"
)
MAX_REPO_SUBPROJECT_SCAN_DEPTH: int = 3

GERRIT_UNSAFE_CMD_PATTERN: re.Pattern[str] = re.compile(
    r"\bgerrit\s+(?:vote|messages\s+post)\b"
)
GERRIT_COMMENTS_PUBLISH_PATTERN: re.Pattern[str] = re.compile(
    r"\bgerrit\s+comments\s+publish\b"
)
GOB_CURL_REVIEW_POST_PATTERN: re.Pattern[str] = re.compile(
    r"\bgob-curl\b.*\/revisions\/[^\/\s]+\/review\b"
)
GERRIT_UPLOAD_CMD_PATTERN: re.Pattern[str] = re.compile(
    rf"(?m)(?:^|[;&|]\s*)(?:{_REPO_UPLOAD_CMD_RE}"
    rf"|{_EMU_CLI_CMD_RE}\s+(?:gerrit|cr)\s+upload\b"
    rf"|{_cli_exe_re('git')}\s+(?:-C\s+(?:\"[^\"]+\"|'[^']+'|[^\s\"']+)\s+)*push\s+\S+\s+\S*refs/for/)"
)
SKIP_REVIEW_SWARM_PATTERN: re.Pattern[str] = re.compile(
    r"(?:--skip-review-swarm\b|AEMU_SKIP_REVIEW_SWARM=1\b)"
)
REPO_DIR_CD_PATTERN: re.Pattern[str] = re.compile(
    r"(?m)(?:^|[;&|]\s*)cd\s+(?:/[dD]\s+)?(?:\"([^\"]+)\"|'([^']+)'|([^;&|<>\r\n]+?)(?=\s*(?:[;&|]|\d*[<>]|$)))"
)
GIT_C_FLAG_PATTERN: re.Pattern[str] = re.compile(rf"(?:^|\s)-C\s+{_QUOTED_ARG_RE}")
REPO_FLAG_PATTERN: re.Pattern[str] = re.compile(
    rf"(?:^|\s)--repo(?:-dir)?(?:=|\s+){_QUOTED_ARG_RE}"
)
NOTIFY_OWNER_FLAG_PATTERN: re.Pattern[str] = re.compile(
    r"--notify(?:=|\s+)OWNER\b"
)
NOTIFY_OWNER_JSON_PATTERN: re.Pattern[str] = re.compile(
    r"\"notify\"\s*:\s*\"OWNER\""
)
BAZEL_TEST_CMD_PATTERN: re.Pattern[str] = re.compile(
    r"(?:^|[;&|]\s*|\b)bazel\s+test\b"
)
BAZEL_TEST_OUTPUT_FLAG_PATTERN: re.Pattern[str] = re.compile(
    r"--test_output(?:=|\s+)\S+"
)

EMU_LAUNCH_CMD_PATTERN: re.Pattern[str] = re.compile(
    r"\bemu-dev-cli\s+launch\s+(emulator|mesh)\b"
)
EMU_TEARDOWN_CMD_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:emu-dev-cli\s+mesh\s+teardown|adb\s+.*\bemu\s+kill|pkill\s+.*qemu)\b"
)
AVD_FLAG_PATTERN: re.Pattern[str] = re.compile(
    r"(?:-avd|--name|--prefix)\s+([a-zA-Z0-9_.-]+)"
)
WORKFLOW_STATE_FLAG_PATTERN: re.Pattern[str] = re.compile(
    r"--state(?:=|\s+)([a-zA-Z0-9_-]+)"
)

# Source-code guardrail patterns for write_file / edit_file
RAW_CC_BINARY_BLOCK_PATTERN: re.Pattern[str] = re.compile(
    r"(?<!with_manifest\s)\bcc_binary\s*\(([^)]*)\)", re.DOTALL
)
LINKSHARED_TRUE_PATTERN: re.Pattern[str] = re.compile(
    r"\blinkshared\s*=\s*True\b"
)
RAW_FSTREAM_PATTERN: re.Pattern[str] = re.compile(
    r"\bstd::(?:i|o)?fstream\b"
)
STATUSOR_HUNGARIAN_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:absl::StatusOr\s*<[^>]+>|auto)\s+([a-zA-Z0-9_]+_or|maybe_[a-zA-Z0-9_]+)\s*="
)
