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

"""Crash Investigation Subcommand Group for emu-dev-cli.

Integrates CrashAdvisor to provide:
- Intelligent Buganizer deduplication & stack analysis (`find-bug`)
- Fixed bug verification & older repository build version checking
- Automated RCA & Buganizer issue filing (`file-bug`)
- Closed-loop autonomous engineer fixing (`autofix`) with older build guardrails
- Unified crash analysis (`analyze`)
- Local debugger crash reproduction (`reproduce`)
"""

from commands.crash.advisor import (
    AdvisorLaunch,
    CrashAdvisorRunner,
    create_crash_bug_tracker,
    ensure_crashadvisor_imports,
    load_crashadvisor_modules,
    resolve_advisor_launch,
)
from commands.crash.analyze import (
    CrashAnalyzeCommand,
    register_analyze_parser,
)
from commands.crash.autofix import (
    CrashAutofixCommand,
    register_autofix_parser,
)
from commands.crash.file_bug import (
    CrashFileBugCommand,
    register_file_bug_parser,
)
from commands.crash.find_bug import (
    CrashFindBugCommand,
    register_find_bug_parser,
)
from commands.crash.fix_checker import (
    FixStatusResult,
    check_git_history_fixes,
    evaluate_crash_fix_status,
    extract_crash_version_info,
    format_agent_version_guardrail_prompt,
)
from commands.crash.link_bug import (
    CrashLinkBugCommand,
    register_link_bug_parser,
)
from commands.crash.list_crashes import (
    CrashListCrashesCommand,
    register_list_crashes_parser,
)
from commands.crash.parser import CrashCommand, register_parser
from commands.crash.reproduce import (
    CrashReproduceCommand,
    register_reproduce_parser,
)
from commands.crash.utils import (
    OAuthTokenManager,
    acquire_auth_token,
    create_secure_sandbox_dir,
    extract_top_fault_frame,
    get_crashadvisor_sandbox_dir,
    is_path_secure_user_owned,
    parse_crash_id,
    resolve_crash_work_dir,
)

__all__ = [
    "AdvisorLaunch",
    "CrashAdvisorRunner",
    "CrashAnalyzeCommand",
    "CrashAutofixCommand",
    "CrashCommand",
    "CrashFileBugCommand",
    "CrashFindBugCommand",
    "CrashLinkBugCommand",
    "CrashListCrashesCommand",
    "CrashReproduceCommand",
    "FixStatusResult",
    "OAuthTokenManager",
    "acquire_auth_token",
    "check_git_history_fixes",
    "create_crash_bug_tracker",
    "create_secure_sandbox_dir",
    "ensure_crashadvisor_imports",
    "evaluate_crash_fix_status",
    "extract_crash_version_info",
    "extract_top_fault_frame",
    "format_agent_version_guardrail_prompt",
    "get_crashadvisor_sandbox_dir",
    "is_path_secure_user_owned",
    "load_crashadvisor_modules",
    "parse_crash_id",
    "register_analyze_parser",
    "register_autofix_parser",
    "register_file_bug_parser",
    "register_find_bug_parser",
    "register_link_bug_parser",
    "register_list_crashes_parser",
    "register_parser",
    "register_reproduce_parser",
    "resolve_advisor_launch",
    "resolve_crash_work_dir",
]
