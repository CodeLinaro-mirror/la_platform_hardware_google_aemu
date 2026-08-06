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

"""Subcommand handler for 'emu-dev-cli flakiness reproduce'."""

import logging
import subprocess
import sys
from lib.ath_api import calculate_stress_test_timeout, get_bazel_flags_for_target
from lib.output import format_markdown_table, print_result
from lib.workspace import WorkspacePathResolver

logger = logging.getLogger(__name__)


def handle_flakiness_reproduce(args):
    """Handles 'emu-dev-cli flakiness reproduce' subcommand.

    Executes local Bazel stress testing with target sanitizer and iterations.
    """
    logger.info("Executing flakiness reproduce command (test=%s, iterations=%s)", args.test, args.iterations)
    json_mode = getattr(args, "json", False)

    # Resolve Bazel binary path dynamically using WorkspacePathResolver
    resolver = WorkspacePathResolver("emu-main-next")
    bazel_bin = resolver.find_tool_binary("bazel") or "bazel"

    _, flags = get_bazel_flags_for_target(
        test_name=args.test, target=args.target, iterations=args.iterations
    )
    cmd = [str(bazel_bin), "test"] + flags

    timeout_seconds = calculate_stress_test_timeout(
        target=args.target,
        iterations=args.iterations,
        explicit_timeout=getattr(args, "timeout", None),
    )

    sys.stderr.write(
        f"🚀 Executing Bazel test reproduction (timeout: {timeout_seconds}s):\n  {' '.join(cmd)}\n"
    )
    sys.stderr.flush()

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_seconds,
        )

        success = proc.returncode == 0
        raw_output = proc.stdout
    except Exception as e:
        success = False
        raw_output = str(e)

    status_str = (
        "PASSED (No Flake Observed)" if success else "FAILED (Flake Reproduced)"
    )

    headers = ["Property", "Value"]
    rows = [
        ["Test Target", f"`{args.test}`"],
        ["Target Platform", f"`{args.target}`"],
        ["Iterations Requested", f"`{args.iterations}`"],
        ["Reproduction Status", f"**{status_str}**"],
        ["Bazel Command", f"`{' '.join(cmd)}`"],
    ]

    data = {
        "summary": f"Reproduction run complete for '{args.test}' on '{args.target}' -> {status_str}",
        "test": args.test,
        "target": args.target,
        "iterations": args.iterations,
        "status": status_str,
        "success": success,
        "markdown_table": format_markdown_table(headers, rows),
        "log_snippet": raw_output[-1500:] if raw_output else "",
    }
    print_result(data, json_mode=json_mode, is_error=not success)
