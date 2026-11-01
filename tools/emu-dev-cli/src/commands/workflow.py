"""Workflow Subcommand Group for emu-dev-cli.

Dispatches and manages automated workflows to guide agents.
"""

import argparse
import logging
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from commands.source_directory import get_source_directory
from core.command import (
    Command,
    CommandGroup,
    CommandResult,
    META_KEY_ALREADY_RENDERED,
    bind_command,
)
from core.context import ExecutionContext
from lib.output import print_result
from workflows.core.yaml_runner import (
    _build_subprocess_env,
    approve_workflow_session,
    get_state_base_dir,
    load_yaml,
    resume_workflow_session,
    run_yaml_workflow,
)

logger = logging.getLogger(__name__)


def clean_description(desc: str) -> str:
    """Strips markdown syntax and normalizes whitespace for clean CLI help text."""
    desc = re.sub(r"[*`_]", "", desc)
    desc = re.sub(r"\s+", " ", desc).strip()
    return desc


def discover_workflow_directories() -> List[str]:
    """Finds all candidate workflow directories across registered sources and sys.path."""
    candidates = []

    # 1. Search up from current working directory
    cur = os.path.abspath(os.getcwd())
    while cur and cur != "/":
        wf_dir = os.path.join(
            cur, "hardware", "google", "aemu", "tools", "emu-dev-cli", "workflows"
        )
        if os.path.isdir(wf_dir) and wf_dir not in candidates:
            candidates.append(wf_dir)
        cur = os.path.dirname(cur)

    # 2. Check registered source directories
    for branch in ("emu-main-dev", "emu-main-next", "git_main"):
        src = get_source_directory(branch)
        if src:
            wf_dir = os.path.join(
                src, "hardware", "google", "aemu", "tools", "emu-dev-cli", "workflows"
        )
            if os.path.isdir(wf_dir) and wf_dir not in candidates:
                candidates.append(wf_dir)

    # 3. Check relative to this file
    rel_wf = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "workflows")
    )
    if os.path.isdir(rel_wf) and rel_wf not in candidates:
        candidates.append(rel_wf)

    # 4. Check sys.path (e.g. installed ~/.android/emu-dev-cli/lib/workflows)
    for p in sys.path:
        wf_dir = os.path.join(p, "workflows")
        if os.path.isdir(wf_dir) and wf_dir not in candidates:
            candidates.append(wf_dir)

    # 5. Also check state base dir for dynamically created custom workflows
    try:
        state_base = str(get_state_base_dir())
        if os.path.isdir(state_base) and state_base not in candidates:
            candidates.append(state_base)
    except OSError as exc:
        logger.debug("Failed to resolve workflow state base directory: %s", exc)

    return candidates


def list_available_workflows() -> Dict[str, Dict[str, str]]:
    """Discovers all available workflows and returns a dict mapping name to metadata."""
    workflows = {}
    for base_dir in discover_workflow_directories():
        if not os.path.isdir(base_dir):
            continue
        for entry in os.listdir(base_dir):
            if entry in ("core", "tests", "__pycache__", "ui"):
                continue
            entry_path = os.path.join(base_dir, entry)
            if not os.path.isdir(entry_path):
                continue

            target_path = None
            target_type = None
            desc = "Automated development workflow"

            # Check for declarative YAML workflow first
            yaml_path = os.path.join(entry_path, "workflow.yaml")
            yml_path = os.path.join(entry_path, "workflow.yml")
            main_py = os.path.join(entry_path, "main.py")

            if os.path.isfile(yaml_path):
                target_path = yaml_path
                target_type = "yaml"
            elif os.path.isfile(yml_path):
                target_path = yml_path
                target_type = "yaml"
            elif os.path.isfile(main_py):
                target_path = main_py
                target_type = "script"
            else:
                continue

            wf_name = entry.replace("_", "-")
            if target_type == "yaml":
                try:
                    spec = load_yaml(target_path)
                    if "description" in spec:
                        desc = clean_description(spec["description"])
                    if "name" in spec:
                        wf_name = spec["name"]
                except Exception as exc:
                    logger.warning(
                        "Failed to parse workflow YAML '%s': %s", target_path, exc
                    )
            elif target_type == "script":
                readme_path = os.path.join(entry_path, "README.md")
                if os.path.isfile(readme_path):
                    try:
                        with open(readme_path, "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line and not line.startswith("#"):
                                    desc = clean_description(line)
                                    break
                    except OSError as exc:
                        logger.debug(
                            "Failed to read workflow README '%s': %s",
                            readme_path,
                            exc,
                        )

            if wf_name not in workflows:
                workflows[wf_name] = {
                    "name": wf_name,
                    "description": desc,
                    "target_path": target_path,
                    "target_type": target_type,
                    "main_script": target_path,
                    "directory": entry_path,
                }

    return workflows


def find_workflow_target(workflow_name: str) -> Optional[Tuple[str, str]]:
    """Resolves (target_type, target_path) for an exact workflow name."""
    workflows = list_available_workflows()
    if workflow_name in workflows:
        wf = workflows[workflow_name]
        return wf.get("target_type", "script"), wf.get("target_path", wf.get("main_script", ""))
    return None


def find_workflow_main(workflow_name: str) -> Optional[str]:
    """Resolves target path for a given workflow name (backwards-compatible)."""
    target = find_workflow_target(workflow_name)
    return target[1] if target else None


def run_workflow_main(main_script: str, args: List[str]) -> int:
    """Executes workflow script or YAML with forwarded arguments."""
    if main_script.endswith((".yaml", ".yml")):
        return run_yaml_workflow(main_script, args)
    cmd = [sys.executable, main_script] + args
    result = subprocess.run(cmd, env=_build_subprocess_env())
    return result.returncode


def register_parser(subparsers):
    # Insert '--' before option arguments if needed
    for i, a in enumerate(sys.argv):
        if a == "workflow" and i + 1 < len(sys.argv):
            if sys.argv[i + 1].startswith("-") or sys.argv[i + 1] in ("list", "ui", "approve", "resume"):
                break
            if (
                i + 2 < len(sys.argv)
                and sys.argv[i + 2] != "--"
                and sys.argv[i + 2].startswith("-")
                and sys.argv[i + 2] not in ("-h", "--help")
            ):
                sys.argv.insert(i + 2, "--")
            break

    WorkflowCommand().register(subparsers)


class WorkflowListCommand(Command[argparse.Namespace, Any]):
    """Lists available development workflows."""

    name = "list"
    help_text = "List available development workflows"

    def execute(
        self, args: argparse.Namespace, ctx: Optional[ExecutionContext] = None
    ) -> CommandResult:
        return run_workflow_help(args)


class WorkflowUiCommand(Command[argparse.Namespace, Any]):
    """Launches the workflow web visualizer UI."""

    name = "ui"
    help_text = (
        "Launch the web visualizer to inspect running and past workflow sessions"
    )
    description = (
        "Launch the single-page web visualizer for emu-dev-cli workflows."
    )

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--port",
            type=int,
            default=8080,
            help="Port to run the web visualizer server on (default: 8080, auto-finds next available)",
        )
        parser.add_argument(
            "--host",
            default="127.0.0.1",
            help="Host to bind the web server to (default: 127.0.0.1)",
        )
        parser.add_argument(
            "--no-browser",
            action="store_true",
            help="Do not automatically open the web browser upon startup",
        )

    def execute(
        self, args: argparse.Namespace, ctx: Optional[ExecutionContext] = None
    ) -> CommandResult:
        return run_workflow_ui(args)


class WorkflowApproveCommand(Command[argparse.Namespace, Any]):
    """Approves a workflow session paused at a human verification barrier."""

    name = "approve"
    help_text = (
        "Approve a workflow session paused at a human verification barrier"
    )
    description = (
        "Approve a workflow session paused at a human verification barrier."
    )

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--state",
            dest="state_id",
            required=True,
            help="State ID of the workflow session to approve",
        )
        parser.add_argument(
            "--workflow",
            dest="workflow_name",
            default=None,
            help="Optional workflow name if disambiguating state ID",
        )
        parser.add_argument(
            "--feedback",
            default=None,
            help="Optional feedback message to pass to the agent for the next step",
        )

    def execute(
        self, args: argparse.Namespace, ctx: Optional[ExecutionContext] = None
    ) -> CommandResult:
        return run_workflow_approve(args)


class WorkflowResumeCommand(Command[argparse.Namespace, Any]):
    """Resumes a workflow session and optionally wakes its linked Jetski conversation."""

    name = "resume"
    help_text = (
        "Resume a completed, paused, or stuck workflow session and wake its Jetski conversation"
    )
    description = (
        "Reset a workflow session to RUNNING and wake its linked Jetski conversation."
    )

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--state",
            dest="state_id",
            required=True,
            help="State ID of the workflow session to resume",
        )
        parser.add_argument(
            "--workflow",
            dest="workflow_name",
            default=None,
            help="Optional workflow name if disambiguating state ID",
        )
        parser.add_argument(
            "--step",
            type=int,
            default=0,
            help="Step index to resume from (default: 0)",
        )
        parser.add_argument(
            "--prompt",
            default=None,
            help="Optional custom wake prompt to send to the linked Jetski conversation",
        )
        parser.add_argument(
            "--no-wake",
            action="store_true",
            help="Only reset workflow state without waking the background Jetski conversation",
        )

    def execute(
        self, args: argparse.Namespace, ctx: Optional[ExecutionContext] = None
    ) -> CommandResult:
        return run_workflow_resume(args)


class WorkflowTargetCommand(Command[argparse.Namespace, Any]):
    """Executes a discovered development workflow by name."""

    def __init__(
        self,
        workflow_name: str,
        description_text: str,
        target_path: Optional[str],
        target_type: str,
    ) -> None:
        self.name = workflow_name
        self.help_text = description_text
        self._target_path = target_path
        self._target_type = target_type

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        if self._target_path:
            path = self._target_path
            ttype = self._target_type

            def _help(file=None):
                if ttype == "yaml" or path.endswith((".yaml", ".yml")):
                    run_yaml_workflow(path, ["--help"])
                else:
                    subprocess.run(
                        [sys.executable, path, "--help"],
                        env=_build_subprocess_env(),
                    )

            parser.print_help = _help

        parser.add_argument(
            "workflow_args",
            nargs=argparse.REMAINDER,
            help=argparse.SUPPRESS,
        )
        parser.set_defaults(workflow_target_name=self.name)

    def execute(
        self, args: argparse.Namespace, ctx: Optional[ExecutionContext] = None
    ) -> CommandResult:
        args.workflow_target_name = self.name
        return run_workflow_direct(args)


class WorkflowCommand(CommandGroup):
    """Top-level command group for automated development workflows."""

    name = "workflow"
    help_text = "Hook-enforced automated workflows to guide agents"
    description = "Run and manage hook-enforced automated workflows to guide agents."
    subcommand_dest = "workflow_cmd"

    def register(
        self,
        subparsers: argparse._SubParsersAction,
    ) -> argparse.ArgumentParser:
        parser = subparsers.add_parser(
            self.name,
            help=self.help_text,
            description=self.description or self.help_text,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.set_defaults(parser=parser)
        self._parser = parser
        self.bind_parser(parser)
        child_subparsers = parser.add_subparsers(
            dest=self.subcommand_dest,
            metavar="<workflow>",
            help="Available workflows",
        )
        static_commands: List[Command[Any, Any]] = [
            WorkflowListCommand(),
            WorkflowUiCommand(),
            WorkflowApproveCommand(),
            WorkflowResumeCommand(),
        ]
        all_children: List[Command[Any, Any]] = []
        for child in static_commands:
            child.register(child_subparsers)
            all_children.append(child)
            self._by_name[child.name] = child
        for wf_name, wf in list_available_workflows().items():
            desc = wf.get("description", "Automated development workflow")
            target_path = wf.get("target_path", wf.get("main_script"))
            target_type = wf.get("target_type", "script")
            wf_cmd = WorkflowTargetCommand(
                wf_name, desc, target_path, target_type
            )
            wf_cmd.register(child_subparsers)
            all_children.append(wf_cmd)
            self._by_name[wf_cmd.name] = wf_cmd
        self._subcommands = tuple(all_children)
        self.subcommands = self._subcommands
        return parser

    def execute(
        self, args: argparse.Namespace, ctx: Optional[ExecutionContext] = None
    ) -> CommandResult:
        sub_cmd = self.resolve_subcommand(args)
        if sub_cmd is not None:
            return sub_cmd.execute(args, ctx)
        return run_workflow_help(args)


def run_workflow_help(args: argparse.Namespace) -> CommandResult:
    json_mode = getattr(args, "json", False)
    if json_mode:
        workflows = list_available_workflows()
        return print_result(
            {
                "status": "success",
                "workflows": [
                    {"name": wf["name"], "description": wf["description"]}
                    for wf in workflows.values()
                ],
            },
            json_mode=True,
        )
    if hasattr(args, "parser"):
        args.parser.print_help()
        return CommandResult.success(
            action="workflow",
            metadata={META_KEY_ALREADY_RENDERED: True},
        )
    workflows = list_available_workflows()
    print("Available workflows:")
    max_len = max((len(w) for w in workflows.keys()), default=10)
    for wf in sorted(workflows.values(), key=lambda w: w["name"]):
        print(f"  {wf['name'].ljust(max_len + 4)}{wf['description']}")
    return CommandResult.success(
        action="workflow list",
        metadata={META_KEY_ALREADY_RENDERED: True},
    )


def run_workflow_ui(args: argparse.Namespace) -> CommandResult:
    """Launches the workflow web UI visualizer."""
    from workflows.ui.server import start_server

    port = getattr(args, "port", 8080)
    host = getattr(args, "host", "127.0.0.1")
    no_browser = getattr(args, "no_browser", False)
    start_server(host=host, port=port, open_browser=not no_browser, block=True)
    return CommandResult.success(
        action="workflow ui",
        metadata={META_KEY_ALREADY_RENDERED: True},
    )


def run_workflow_approve(args: argparse.Namespace) -> CommandResult:
    """Approves a workflow session paused at a human verification barrier."""
    state_id = getattr(args, "state_id", "")
    wf_name = getattr(args, "workflow_name", None)
    feedback = getattr(args, "feedback", None)
    ok, msg = approve_workflow_session(
        state_id=state_id, workflow_name=wf_name, feedback=feedback
    )
    if ok:
        print(msg)
        return CommandResult.success(
            summary=msg,
            action="workflow approve",
            metadata={META_KEY_ALREADY_RENDERED: True},
        )
    print(f"Error: {msg}", file=sys.stderr)
    return CommandResult.failure(
        msg,
        action="workflow approve",
        metadata={META_KEY_ALREADY_RENDERED: True},
    )


def run_workflow_resume(args: argparse.Namespace) -> CommandResult:
    """Resumes a workflow session and optionally wakes its linked Jetski conversation."""
    state_id = getattr(args, "state_id", "")
    wf_name = getattr(args, "workflow_name", None)
    step = int(getattr(args, "step", 0) or 0)
    prompt = getattr(args, "prompt", None)
    no_wake = bool(getattr(args, "no_wake", False))
    ok, msg, info = resume_workflow_session(
        state_id=state_id,
        workflow_name=wf_name,
        target_step=step,
        custom_prompt=prompt,
        wake_conversation=not no_wake,
    )
    if getattr(args, "json", False):
        return print_result(
            {"status": "success" if ok else "error", "message": msg, "session": info},
            json_mode=True,
            is_error=not ok,
        )
    if ok:
        print(msg)
        if info.get("continuation_cmd"):
            print(f"Continuation command: {info['continuation_cmd']}")
        return CommandResult.success(
            summary=msg,
            action="workflow resume",
            metadata={META_KEY_ALREADY_RENDERED: True},
        )
    print(f"Error: {msg}", file=sys.stderr)
    return CommandResult.failure(
        msg,
        action="workflow resume",
        metadata={META_KEY_ALREADY_RENDERED: True},
    )


def run_workflow_direct(args: argparse.Namespace) -> CommandResult:
    wf_name = getattr(args, "workflow_target_name", None) or getattr(
        args, "workflow_cmd", ""
    )
    target_args = [
        a for a in (getattr(args, "workflow_args", []) or []) if a != "--"
    ]
    main_script = find_workflow_main(wf_name)
    if not main_script:
        available = ", ".join(list_available_workflows().keys()) or "none"
        msg = f"Unknown workflow '{wf_name}'. Available workflows: {available}"
        return print_result(
            {
                "status": "error",
                "error_message": msg,
                "exit_code": 1,
            },
            json_mode=getattr(args, "json", False),
            is_error=True,
        )
    target = find_workflow_target(wf_name)
    if target and target[0] == "yaml":
        code = run_yaml_workflow(target[1], target_args)
    else:
        code = run_workflow_main(main_script, target_args)
    return CommandResult(
        status="success" if code == 0 else "error",
        action=f"workflow {wf_name}",
        exit_code=int(code),
        metadata={META_KEY_ALREADY_RENDERED: True},
    )


def run_workflow_command(args: argparse.Namespace) -> CommandResult:
    """Backwards-compatible dispatcher for unit tests and direct callers."""
    cmd = getattr(args, "workflow_cmd", None)
    if not cmd or cmd == "list":
        return run_workflow_help(args)
    if cmd == "ui":
        return run_workflow_ui(args)
    if cmd == "approve":
        return run_workflow_approve(args)
    if cmd == "resume":
        return run_workflow_resume(args)
    if cmd == "run":
        wf_name = getattr(args, "target_workflow", "")
        if not wf_name and getattr(args, "workflow_args", []):
            wf_name = args.workflow_args[0]
            args.workflow_args = args.workflow_args[1:]
        args.workflow_target_name = wf_name
        return run_workflow_direct(args)
    return run_workflow_direct(args)
