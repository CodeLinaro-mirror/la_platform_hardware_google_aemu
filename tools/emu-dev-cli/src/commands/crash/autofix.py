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
from typing import Any

from commands.crash.advisor import CrashAdvisorRunner
from commands.crash.fix_checker import (
    evaluate_crash_fix_status,
    format_agent_version_guardrail_prompt,
)
from commands.crash.utils import (
    acquire_auth_token,
    parse_crash_id,
    resolve_crash_work_dir,
)
from commands.source_directory import get_source_directory
from core.command import Command, CommandResult
from core.context import ExecutionContext, SERVICE_AGENT_RUNTIME
from core.errors import CommandValidationError, ExecutionError
from lib.agent import AgentApiClient
from lib.markdown import extract_yaml_block


class CrashAutofixCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Command implementation for `emu-dev-cli crash autofix`."""

    name = "autofix"
    help_text = (
        "Perform RCA and dispatch emu_main_next_engineer to implement and verify a local fix"
    )
    help = help_text
    description = (
        "Parses CrashAdvisor RCA actionability data for a Crash ID, maps faulting code "
        "locations to local source repository paths, verifies older build revisions and "
        "existing fixes, and dispatches the autonomous AI engineer to write unit tests "
        "and fix the code."
    )

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.description = self.description
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

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> argparse.Namespace:
        del ctx
        raw_id = str(getattr(args, "crash_id", "") or "").strip()
        target_id = parse_crash_id(raw_id) if raw_id else ""
        if not target_id:
            raise CommandValidationError(
                "Crash ID cannot be empty for 'crash autofix'.",
                remediation="Pass a 16-hex Crash ID (e.g. '05d8356e2f800000') or go/crash URL.",
            )
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        target_id = parse_crash_id(str(args.crash_id))
        dry_run = bool(getattr(args, "dry_run", False))
        force = bool(getattr(args, "force", False))

        explicit_work_dir = getattr(args, "work_dir", None)
        sandbox_path = resolve_crash_work_dir(
            ctx.config.cache_dir, target_id, explicit_work_dir
        )

        rca_path = sandbox_path / "rca_summary.md"
        if not rca_path.is_file():
            token = acquire_auth_token(getattr(args, "token", None))
            advisor_runner = CrashAdvisorRunner()
            advisor_runner.run(
                ctx,
                target_id,
                sandbox_path,
                flags=["--auto-run"],
                token=token,
                check=False,
            )

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
        agent_def = ".gemini/agents/emu_main_next_engineer.md"
        agent_model = "pro"
        if agent_runtime and hasattr(agent_runtime, "start_conversation"):
            try:
                from clients.agent_runtime_service import AgentDispatchRequest

                request = AgentDispatchRequest(
                    prompt=prompt,
                    agent_definition=agent_def,
                    model=agent_model,
                )
                session = agent_runtime.start_conversation(request)
            except (TypeError, ValueError):
                session = agent_runtime.start_conversation(
                    prompt=prompt,
                    agent=agent_def,
                    model=agent_model,
                )
            conv_id = getattr(session, "conversation_id", "")
            agent_output = getattr(session, "stdout", "").strip()
        elif agent_runtime and hasattr(agent_runtime, "send_prompt"):
            res = agent_runtime.send_prompt(prompt)
            conv_id = ""
            agent_output = getattr(res, "stdout", "").strip()
        else:
            agent_client = AgentApiClient()
            res = agent_client.start_conversation(
                prompt=prompt,
                agent=agent_def,
                model=agent_model,
            )
            conv_id = ""
            agent_output = res.stdout.strip()

        payload = {
            "status": "dispatched",
            "action": "crash autofix",
            "crash_id": target_id,
            "conversation_id": conv_id,
            "agent_output": agent_output,
        }
        return CommandResult(
            data=payload,
            action="crash autofix",
            summary=f"Dispatched autonomous engineer for crash {target_id}.",
            status="dispatched",
        )


def register_autofix_parser(crash_subparsers) -> argparse.ArgumentParser:
    """Registers the `autofix` subcommand parser under `crash`.

    Args:
        crash_subparsers: Subparser group instance from `crash` parser.
    """
    return CrashAutofixCommand().register(crash_subparsers)
