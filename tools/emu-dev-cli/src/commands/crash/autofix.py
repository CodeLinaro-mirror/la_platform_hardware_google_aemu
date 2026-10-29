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
from typing import Any

from commands.crash.advisor import run_crashadvisor_bazel
from commands.crash.fix_checker import (
    evaluate_crash_fix_status,
    format_agent_version_guardrail_prompt,
)
from commands.crash.utils import (
    acquire_auth_token,
    get_crashadvisor_sandbox_dir,
    parse_crash_id,
)
from commands.source_directory import get_source_directory
from core.command import Command, CommandResult, bind_command
from core.context import (
    ExecutionContext,
    SERVICE_AGENT_RUNTIME,
    SERVICE_PROCESS_RUNNER,
)
from core.errors import ExecutionError, UsageError
from lib.agent import AgentApiClient
from lib.markdown import extract_yaml_block
from lib.output import print_result, print_status


def run_autofix(args: argparse.Namespace) -> None:
    """Parses RCA actionability and dispatches emu_main_next_engineer for autonomous fix.

    Checks whether the crash was built against an older repository version and whether
    the bug has already been resolved or fixed before dispatching the autonomous agent.

    Args:
        args: Parsed command line arguments containing crash_id, token, dry_run, force flags.
    """
    json_mode = getattr(args, "json", False)
    target_id = parse_crash_id(args.crash_id)
    token = acquire_auth_token(getattr(args, "token", None))
    dry_run = getattr(args, "dry_run", False)
    force = getattr(args, "force", False)

    print_status(
        f"🔧 Initiating autonomous autofix pipeline for Target: {target_id}...",
        json_mode=json_mode,
    )

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
        if json_mode:
            print_result(
                {
                    "status": "error",
                    "action": "crash autofix",
                    "crash_id": target_id,
                    "message": f"RCA summary file not found at {rca_path}.",
                },
                json_mode=True,
                is_error=True,
            )
        else:
            sys.stderr.write(f"❌ ERROR: RCA summary file not found at {rca_path}.\n")
        sys.exit(1)

    rca_content = rca_path.read_text(encoding="utf-8")
    action_data = extract_yaml_block(rca_content, "actionability:")

    if not action_data:
        if json_mode:
            print_result(
                {
                    "status": "halted",
                    "action": "crash autofix",
                    "crash_id": target_id,
                    "reason": "no_actionability_block",
                },
                json_mode=True,
            )
        else:
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

    meta_path = Path(sandbox_dir) / "metadata.json"
    fix_res = evaluate_crash_fix_status(
        crash_id=target_id,
        metadata_path=meta_path if meta_path.exists() else None,
        action_data=action_data,
        source_dir=local_src_dir,
    )

    fixed_badge = (
        "ALREADY FIXED / RESOLVED ✅"
        if fix_res.is_already_fixed
        else "NEEDS VERIFICATION / FIX ⚠️"
    )

    if not json_mode:
        print(f"\n📋 Actionability Analysis Results:")
        print(f"  • Fixable: {'YES ✅' if is_fixable else 'NO ❌'}")
        print(f"  • Target File: {target_file}")
        if local_file_path:
            print(f"  • Local File Path: file://{local_file_path}")
        print(f"  • Target Function: {target_func}")
        print(f"  • Remediation: {remediation}")
        print(f"  • Crash Build ID: {fix_res.build_id} (Version: {fix_res.version_str})")
        print(f"  • Fix Status Assessment: {fixed_badge} ({fix_res.fix_reason})")
        print(f"  • Repository Version Warning: {fix_res.older_version_warning}\n")

    if not is_fixable:
        if json_mode:
            print_result(
                {
                    "status": "halted",
                    "action": "crash autofix",
                    "crash_id": target_id,
                    "fixable": False,
                    "target_file": target_file,
                    "target_function": target_func,
                },
                json_mode=True,
            )
        else:
            print(
                "🛑 Issue is marked as not fixable autonomously. Halting for developer review."
            )
        sys.exit(0)

    if fix_res.is_already_fixed and not force and not dry_run:
        if json_mode:
            print_result(
                {
                    "status": "already_fixed",
                    "action": "crash autofix",
                    "crash_id": target_id,
                    "fix_reason": fix_res.fix_reason,
                    "build_id": fix_res.build_id,
                },
                json_mode=True,
            )
        else:
            print(
                f"🟢 Fix Assessment Notice: The bug associated with Crash {target_id} appears to ALREADY BE FIXED.\n"
                f"   Reason: {fix_res.fix_reason}\n"
                f"   Note: This crash occurred on Build ID {fix_res.build_id}, which is an older build than current repository HEAD.\n"
                f"   Halting automated dispatch to prevent redundant CLs. To dispatch the AI engineer anyway, pass --force."
            )
        sys.exit(0)

    if dry_run:
        if json_mode:
            print_result(
                {
                    "status": "dry_run",
                    "action": "crash autofix",
                    "crash_id": target_id,
                    "fixable": is_fixable,
                    "target_file": target_file,
                    "local_file_path": local_file_path,
                    "target_function": target_func,
                    "remediation": remediation,
                },
                json_mode=True,
            )
        else:
            print("🔍 Dry-run specified. Skipping agentapi dispatch.")
        sys.exit(0)

    # Construct prompt with older build version guardrails
    prompt = format_agent_version_guardrail_prompt(
        crash_id=target_id,
        rca_path=str(rca_path),
        target_file=target_file,
        local_file_path=local_file_path,
        target_func=target_func,
        remediation=remediation,
        fix_status=fix_res,
    )

    print_status(
        "🤖 Dispatching emu_main_next_engineer subagent via agentapi...",
        json_mode=json_mode,
    )
    try:
        agent_client = AgentApiClient()
        res = agent_client.start_conversation(
            prompt=prompt,
            agent=".gemini/agents/emu_main_next_engineer.md",
            model="pro",
        )
        if json_mode:
            print_result(
                {
                    "status": "dispatched",
                    "action": "crash autofix",
                    "crash_id": target_id,
                    "agent_output": res.stdout.strip(),
                },
                json_mode=True,
            )
        else:
            print(f"✅ Autonomous engineer successfully dispatched!\n{res.stdout.strip()}")
    except Exception as e:
        if json_mode:
            print_result(
                {
                    "status": "error",
                    "action": "crash autofix",
                    "crash_id": target_id,
                    "message": f"Failed to dispatch agentapi: {e}",
                },
                json_mode=True,
                is_error=True,
            )
        else:
            sys.stderr.write(f"❌ Failed to dispatch agentapi: {e}\n")
        sys.exit(1)


class CrashAutofixCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Command implementation for `emu-dev-cli crash autofix`."""

    name = "autofix"
    help_text = "Perform RCA and dispatch emu_main_next_engineer to implement and verify a local fix"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.description = (
            "Parses CrashAdvisor RCA actionability data for a Crash ID, maps faulting code "
            "locations to local source repository paths, verifies older build revisions and "
            "existing fixes, and dispatches the autonomous AI engineer to write unit tests "
            "and fix the code."
        )
        parser.add_argument(
            "crash_id", help="Crash ID (e.g. 05d8356e2f800000) or go/crash URL"
        )
        parser.add_argument("--token", help="OAuth2 token")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Perform analysis and file mapping without launching agent",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force dispatching autonomous engineer even if bug is already marked fixed in Buganizer or codebase",
        )
        parser.add_argument(
            "--work-dir",
            dest="work_dir",
            help="Optional explicit work directory containing rca_summary.md and metadata.json",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Output machine-readable JSON result",
        )

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> None:
        del ctx
        raw_id = str(getattr(args, "crash_id", "") or "").strip()
        target_id = parse_crash_id(raw_id) if raw_id else ""
        if not target_id:
            raise UsageError(
                "Crash ID cannot be empty for 'crash autofix'.",
                remediation="Pass a 16-hex Crash ID (e.g. '05d8356e2f800000') or go/crash URL.",
            )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        target_id = parse_crash_id(str(args.crash_id))
        dry_run = bool(getattr(args, "dry_run", False))
        force = bool(getattr(args, "force", False))

        explicit_work_dir = getattr(args, "work_dir", None)
        if explicit_work_dir:
            sandbox_path = Path(str(explicit_work_dir)).expanduser()
        else:
            sandbox_path = ctx.config.cache_dir / "crashadvisor" / target_id
            if not sandbox_path.exists():
                sandbox_path.mkdir(parents=True, exist_ok=True)

        rca_path = sandbox_path / "rca_summary.md"
        if not rca_path.is_file():
            runner = ctx.services.get(SERVICE_PROCESS_RUNNER)
            cmd_args = [
                "bazel",
                "run",
                "//emulator/crashreport/tool/advisor:crashadvisor",
                "--",
                target_id,
                "--auto-run",
                "--work-dir",
                str(sandbox_path),
            ]
            token = getattr(args, "token", None)
            if token:
                cmd_args.extend(["--token", str(token)])
            runner.run(cmd_args, check=False)

        if not rca_path.is_file():
            raise ExecutionError(
                f"RCA summary file not found at '{rca_path}'.",
                remediation="Run CrashAdvisor first or pass a directory containing rca_summary.md via --work-dir.",
            )

        rca_content = rca_path.read_text(encoding="utf-8")
        action_data = extract_yaml_block(rca_content, "actionability:")
        if not action_data:
            payload: dict[str, Any] = {
                "status": "halted",
                "action": "crash autofix",
                "crash_id": target_id,
                "reason": "no_actionability_block",
            }
            return CommandResult(
                data=payload,
                action="crash autofix",
                summary=f"Halted autofix for {target_id}: no actionability block found.",
                status="halted",
            )

        is_fixable = str(action_data.get("fixable", "false")).lower() == "true"
        target_file = str(action_data.get("target_file", "unknown"))
        target_func = str(action_data.get("target_function", "unknown"))
        remediation = str(
            action_data.get("remediation_summary", "Refer to rca_summary.md")
        )

        local_src_dir = ctx.config.get_source_directory(
            "emu-main-next"
        ) or get_source_directory("emu-main-next")
        local_file_path: str | None = None
        if local_src_dir and target_file != "unknown":
            cand = Path(local_src_dir) / target_file
            if cand.exists():
                local_file_path = str(cand)

        meta_path = sandbox_path / "metadata.json"
        fix_res = evaluate_crash_fix_status(
            crash_id=target_id,
            metadata_path=meta_path if meta_path.is_file() else None,
            action_data=action_data,
            source_dir=local_src_dir,
        )

        if not is_fixable:
            payload = {
                "status": "halted",
                "action": "crash autofix",
                "crash_id": target_id,
                "fixable": False,
                "target_file": target_file,
                "target_function": target_func,
            }
            return CommandResult(
                data=payload,
                action="crash autofix",
                summary=f"Halted autofix for {target_id}: crash marked not fixable.",
                status="halted",
            )

        if fix_res.is_already_fixed and not force and not dry_run:
            payload = {
                "status": "already_fixed",
                "action": "crash autofix",
                "crash_id": target_id,
                "fix_reason": fix_res.fix_reason,
                "build_id": fix_res.build_id,
            }
            return CommandResult(
                data=payload,
                action="crash autofix",
                summary=f"Skipped autofix for {target_id}: already fixed ({fix_res.fix_reason}).",
                status="already_fixed",
            )

        if dry_run:
            payload = {
                "status": "dry_run",
                "action": "crash autofix",
                "crash_id": target_id,
                "fixable": is_fixable,
                "target_file": target_file,
                "local_file_path": local_file_path,
                "target_function": target_func,
                "remediation": remediation,
            }
            return CommandResult(
                data=payload,
                action="crash autofix",
                summary=f"Dry-run autofix plan ready for {target_id} ({target_file}).",
                status="dry_run",
            )

        prompt = format_agent_version_guardrail_prompt(
            crash_id=target_id,
            rca_path=str(rca_path),
            target_file=target_file,
            local_file_path=local_file_path,
            target_func=target_func,
            remediation=remediation,
            fix_status=fix_res,
        )
        agent_runtime = ctx.services.get(SERVICE_AGENT_RUNTIME)
        session = agent_runtime.start_conversation(
            prompt=prompt,
            agent=".gemini/agents/emu_main_next_engineer.md",
            model="pro",
        )
        payload = {
            "status": "dispatched",
            "action": "crash autofix",
            "crash_id": target_id,
            "conversation_id": session.conversation_id,
            "agent_output": session.stdout.strip(),
        }
        return CommandResult(
            data=payload,
            action="crash autofix",
            summary=f"Dispatched autonomous engineer for crash {target_id} ({session.conversation_id}).",
            status="dispatched",
        )


def register_autofix_parser(crash_subparsers) -> None:
    """Registers the `autofix` subcommand parser under `crash`.

    Args:
        crash_subparsers: Subparser group instance from `crash` parser.
    """
    bind_command(
        crash_subparsers,
        CrashAutofixCommand(),
        legacy_func=run_autofix,
    )
