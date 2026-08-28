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

"""Crash Investigation Subcommand Group for emu-dev-cli."""

from commands.crash.advisor import (
    ensure_crashadvisor_imports,
    run_crashadvisor_bazel,
)
from commands.crash.analyze import register_analyze_parser, run_analyze
from commands.crash.autofix import register_autofix_parser, run_autofix
from commands.crash.file_bug import register_file_bug_parser, run_file_bug
from commands.crash.find_bug import register_find_bug_parser, run_find_bug
from commands.crash.fix_checker import (
    CrashFixChecker,
    FixStatusResult,
    check_buganizer_fixed_status,
    check_git_history_fixes,
    evaluate_crash_fix_status,
    extract_crash_version_info,
    format_agent_version_guardrail_prompt,
)
from commands.crash.parser import register_parser
from commands.crash.reproduce import register_reproduce_parser, run_reproduce
from commands.crash.utils import (
    OAuthTokenManager,
    acquire_auth_token,
    create_secure_sandbox_dir,
    extract_top_fault_frame,
    get_crashadvisor_sandbox_dir,
    is_path_secure_user_owned,
    parse_crash_id,
)

__all__ = [
    "CrashFixChecker",
    "FixStatusResult",
    "OAuthTokenManager",
    "acquire_auth_token",
    "check_buganizer_fixed_status",
    "check_git_history_fixes",
    "create_secure_sandbox_dir",
    "ensure_crashadvisor_imports",
    "evaluate_crash_fix_status",
    "extract_crash_version_info",
    "extract_top_fault_frame",
    "format_agent_version_guardrail_prompt",
    "get_crashadvisor_sandbox_dir",
    "is_path_secure_user_owned",
    "parse_crash_id",
    "register_analyze_parser",
    "register_autofix_parser",
    "register_file_bug_parser",
    "register_find_bug_parser",
    "register_parser",
    "register_reproduce_parser",
    "run_analyze",
    "run_autofix",
    "run_crashadvisor_bazel",
    "run_file_bug",
    "run_find_bug",
    "run_reproduce",
]
