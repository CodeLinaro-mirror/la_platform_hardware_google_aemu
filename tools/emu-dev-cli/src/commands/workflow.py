"""Workflow Subcommand Group for emu-dev-cli.

Dispatches and manages automated workflows to guide agents.
"""

import argparse
import os
import re
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

from commands.source_directory import get_source_directory
from lib.output import print_result



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

    return candidates


def list_available_workflows() -> Dict[str, Dict[str, str]]:
    """Discovers all available workflows and returns a dict mapping name to metadata."""
    workflows = {}
    for base_dir in discover_workflow_directories():
        if not os.path.isdir(base_dir):
            continue
        for entry in os.listdir(base_dir):
            if entry in ("core", "tests", "__pycache__"):
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

            if target_type == "yaml":
                try:
                    from workflows.core.yaml_runner import load_yaml
                    spec = load_yaml(target_path)
                    if "description" in spec:
                        desc = clean_description(spec["description"])
                except Exception:
                    try:
                        from core.yaml_runner import load_yaml
                        spec = load_yaml(target_path)
                        if "description" in spec:
                            desc = clean_description(spec["description"])
                    except Exception:
                        pass
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
                    except Exception:
                        pass

            wf_name = entry.replace("_", "-")
            if target_type == "yaml":
                try:
                    from workflows.core.yaml_runner import load_yaml
                    spec = load_yaml(target_path)
                    if "name" in spec:
                        wf_name = spec["name"]
                except Exception:
                    pass

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
        try:
            from workflows.core.yaml_runner import run_yaml_workflow
        except ImportError:
            from core.yaml_runner import run_yaml_workflow
        return run_yaml_workflow(main_script, args)
    cmd = [sys.executable, main_script] + args
    result = subprocess.run(cmd)
    return result.returncode


def register_parser(subparsers):
    # Insert '--' before option arguments if needed
    for i, a in enumerate(sys.argv):
        if a == "workflow" and i + 1 < len(sys.argv):
            if sys.argv[i + 1].startswith("-"):
                break
            if (
                i + 2 < len(sys.argv)
                and sys.argv[i + 2] != "--"
                and sys.argv[i + 2].startswith("-")
                and sys.argv[i + 2] not in ("-h", "--help")
            ):
                sys.argv.insert(i + 2, "--")
            break

    workflow_parser = subparsers.add_parser(
        "workflow",
        help="Hook-enforced automated workflows to guide agents",
        description="Run and manage hook-enforced automated workflows to guide agents.",
    )
    workflow_parser.set_defaults(
        parser=workflow_parser,
        func=run_workflow_help,
    )

    workflow_subparsers = workflow_parser.add_subparsers(
        dest="workflow_cmd",
        metavar="<workflow>",
        help="Available workflows",
    )

    list_parser = workflow_subparsers.add_parser(
        "list",
        help="List available development workflows",
    )
    list_parser.set_defaults(
        func=run_workflow_help,
    )

    # Discovered workflows registered directly as subcommands (single canonical name with '-')
    workflows = list_available_workflows()
    for name, wf in workflows.items():
        desc = wf.get("description", "Automated development workflow")
        target_path = wf.get("target_path", wf.get("main_script"))
        target_type = wf.get("target_type", "script")

        wf_p = workflow_subparsers.add_parser(
            name,
            help=desc,
        )
        if target_path:
            def make_help_func(path, ttype):
                def _help(file=None):
                    if ttype == "yaml" or path.endswith((".yaml", ".yml")):
                        try:
                            from workflows.core.yaml_runner import run_yaml_workflow
                        except ImportError:
                            from core.yaml_runner import run_yaml_workflow
                        run_yaml_workflow(path, ["--help"])
                    else:
                        subprocess.run([sys.executable, path, "--help"])
                return _help
            wf_p.print_help = make_help_func(target_path, target_type)

        wf_p.add_argument(
            "workflow_args",
            nargs=argparse.REMAINDER,
            help=argparse.SUPPRESS,
        )
        wf_p.set_defaults(
            func=run_workflow_direct,
            workflow_target_name=name,
        )


def run_workflow_help(args):
    json_mode = getattr(args, "json", False)
    if json_mode:
        workflows = list_available_workflows()
        print_result(
            {
                "status": "success",
                "workflows": [
                    {"name": wf["name"], "description": wf["description"]}
                    for wf in workflows.values()
                ],
            },
            json_mode=True,
        )
        return
    if hasattr(args, "parser"):
        args.parser.print_help()
        sys.exit(0)
    workflows = list_available_workflows()
    print("Available workflows:")
    max_len = max((len(w) for w in workflows.keys()), default=10)
    for wf in sorted(workflows.values(), key=lambda w: w["name"]):
        print(f"  {wf['name'].ljust(max_len + 4)}{wf['description']}")


def run_workflow_direct(args):
    wf_name = getattr(args, "workflow_target_name", None) or getattr(
        args, "workflow_cmd", ""
    )
    target_args = [a for a in (getattr(args, "workflow_args", []) or []) if a != "--"]
    main_script = find_workflow_main(wf_name)
    if not main_script:
        available = ", ".join(list_available_workflows().keys()) or "none"
        msg = f"Unknown workflow '{wf_name}'. Available workflows: {available}"
        print_result(
            {
                "status": "error",
                "error_message": msg,
                "exit_code": 1,
            },
            json_mode=getattr(args, "json", False),
            is_error=True,
        )
        sys.exit(1)
    target = find_workflow_target(wf_name)
    if target and target[0] == "yaml":
        try:
            from workflows.core.yaml_runner import run_yaml_workflow
        except ImportError:
            from core.yaml_runner import run_yaml_workflow
        code = run_yaml_workflow(target[1], target_args)
    else:
        code = run_workflow_main(main_script, target_args)
    sys.exit(code)


def run_workflow_command(args):
    """Backwards-compatible dispatcher for unit tests and direct callers."""
    cmd = getattr(args, "workflow_cmd", None)
    if not cmd or cmd == "list":
        run_workflow_help(args)
    elif cmd == "run":
        wf_name = getattr(args, "target_workflow", "")
        if not wf_name and getattr(args, "workflow_args", []):
            wf_name = args.workflow_args[0]
            args.workflow_args = args.workflow_args[1:]
        args.workflow_target_name = wf_name
        run_workflow_direct(args)
    else:
        run_workflow_direct(args)
