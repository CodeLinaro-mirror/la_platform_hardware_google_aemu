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

"""Closed-loop autonomous engineer fixing subcommand module for emu-dev-cli (`crash autofix`)."""

import argparse
from pathlib import Path
import sys

from commands.crash.advisor import run_crashadvisor_bazel
from commands.crash.utils import (
    acquire_auth_token,
    get_crashadvisor_sandbox_dir,
    parse_crash_id,
)
from commands.source_directory import get_source_directory
from lib.agent import AgentApiClient
from lib.markdown import extract_yaml_block


def run_autofix(args: argparse.Namespace) -> None:
    """Parses RCA actionability and dispatches emu_main_next_engineer for autonomous fix.

    Args:
        args: Parsed command line arguments containing crash_id, token, dry_run flags.
    """
    target_id = parse_crash_id(args.crash_id)
    token = acquire_auth_token(getattr(args, "token", None))
    dry_run = getattr(args, "dry_run", False)

    print(f"🔧 Initiating autonomous autofix pipeline for Target: {target_id}...")

    sandbox_dir = get_crashadvisor_sandbox_dir(target_id, create=True)

    # Run CrashAdvisor in auto-run mode to ensure RCA summary exists
    cmd_args = [target_id, "--auto-run", "--work-dir", sandbox_dir]
    if token:
        cmd_args.extend(["--token", token])

    try:
        run_crashadvisor_bazel(cmd_args)
    except Exception as e:
        sys.stderr.write(f"⚠️ CrashAdvisor execution notice: {e}\n")

    rca_path = Path(sandbox_dir) / "rca_summary.md"

    if not rca_path.exists():
        sys.stderr.write(f"❌ ERROR: RCA summary file not found at {rca_path}.\n")
        sys.exit(1)

    rca_content = rca_path.read_text(encoding="utf-8")
    action_data = extract_yaml_block(rca_content, "actionability:")

    if not action_data:
        print(
            "🟡 RCA analysis completed, but no structured actionability block was generated. Halting autofix."
        )
        sys.exit(0)

    is_fixable = action_data.get("fixable", "false").lower() == "true"
    target_file = action_data.get("target_file", "unknown")
    target_func = action_data.get("target_function", "unknown")
    remediation = action_data.get("remediation_summary", "Refer to rca_summary.md")

    # Resolve local file path via source_directory registry
    local_src_dir = get_source_directory("emu-main-next")
    local_file_path = None
    if local_src_dir and target_file != "unknown":
        cand = Path(local_src_dir) / target_file
        if cand.exists():
            local_file_path = str(cand)

    print(f"\n📋 Actionability Analysis Results:")
    print(f"  • Fixable: {'YES ✅' if is_fixable else 'NO ❌'}")
    print(f"  • Target File: {target_file}")
    if local_file_path:
        print(f"  • Local File Path: file://{local_file_path}")
    print(f"  • Target Function: {target_func}")
    print(f"  • Remediation: {remediation}\n")

    if not is_fixable:
        print(
            "🛑 Issue is marked as not fixable autonomously. Halting for developer review."
        )
        sys.exit(0)

    if dry_run:
        print("🔍 Dry-run specified. Skipping agentapi dispatch.")
        sys.exit(0)

    # Dispatch emu_main_next_engineer subagent
    prompt = f"""Implement the remediation plan detailed in {rca_path} for Crash Target {target_id}.
Target File: {target_file} (Local Path: {local_file_path or 'Search workspace'})
Target Function: {target_func}
Remediation Summary: {remediation}

Follow the TDD loop (Red/Green/Refactor) coordinating with test_enforcer.
Upon successful verification, hand off to reviewer.md for audit and committer.md to execute repo upload.
"""
    print("🤖 Dispatching emu_main_next_engineer subagent via agentapi...")
    try:
        agent_client = AgentApiClient()
        res = agent_client.start_conversation(
            prompt=prompt,
            agent=".gemini/agents/emu_main_next_engineer.md",
            model="pro",
        )
        print(f"✅ Autonomous engineer successfully dispatched!\n{res.stdout.strip()}")
    except Exception as e:
        sys.stderr.write(f"❌ Failed to dispatch agentapi: {e}\n")
        sys.exit(1)


def register_autofix_parser(crash_subparsers) -> None:
    """Registers the `autofix` subcommand parser under `crash`.

    Args:
        crash_subparsers: Subparser group instance from `crash` parser.
    """
    autofix_parser = crash_subparsers.add_parser(
        "autofix",
        help="Perform RCA and dispatch emu_main_next_engineer to implement and verify a local fix",
        description="Parses CrashAdvisor RCA actionability data for a Crash ID, maps faulting code locations to local source repository paths, and dispatches the autonomous AI engineer to write unit tests and fix the code.",
    )
    autofix_parser.add_argument(
        "crash_id", help="Crash ID (e.g. 05d8356e2f800000) or go/crash URL"
    )
    autofix_parser.add_argument("--token", help="OAuth2 token")
    autofix_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform analysis and file mapping without launching agent",
    )
    autofix_parser.set_defaults(func=run_autofix)
