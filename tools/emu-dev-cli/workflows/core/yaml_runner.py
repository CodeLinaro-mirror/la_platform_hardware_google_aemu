"""Generic declarative YAML workflow engine for emu-dev-cli."""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

import yaml


def get_state_base_dir() -> Path:
    """Returns user-specific secure cache directory for workflow state files."""
    if "EMU_DEV_CLI_WORKFLOW_STATE_DIR" in os.environ:
        return Path(os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"])
    cache_home = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache_home) if cache_home else Path.home() / ".cache"
    return base / "emu-dev-cli" / "workflows" / "state"


STATE_BASE_DIR = get_state_base_dir()


def resolve_state_file(state_dir: Path, state_arg: Optional[str]) -> Tuple[Optional[str], Optional[Path]]:
    """Resolves state argument into (clean_id, state_file_path)."""
    if not state_arg:
        return None, None
    raw = Path(state_arg.strip()).name
    if raw.startswith("STATE-"):
        raw = raw[len("STATE-") :]
    for ext in (".yaml", ".yml", ".json"):
        if raw.endswith(ext):
            raw = raw[: -len(ext)]
            break
    clean_id = raw

    # Look for existing file (.yaml, .yml, or legacy .json)
    for ext in (".yaml", ".yml", ".json"):
        candidate = state_dir / f"STATE-{clean_id}{ext}"
        if candidate.is_file():
            return clean_id, candidate

    # Default to .yaml for new or uncreated paths
    return clean_id, state_dir / f"STATE-{clean_id}.yaml"


class StateYamlDumper(yaml.SafeDumper):
    pass


def _multiline_str_presenter(dumper: yaml.Dumper, data: str):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


StateYamlDumper.add_representer(str, _multiline_str_presenter)


def load_yaml(content_or_path: Union[str, Path]) -> Dict[str, Any]:
    """Loads workflow definition or state from a YAML string or file path."""
    p = Path(content_or_path) if isinstance(content_or_path, str) else content_or_path
    if p.is_file():
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    elif isinstance(content_or_path, str):
        data = yaml.safe_load(content_or_path)
    else:
        data = {}
    return data or {}


def load_state_yaml(filepath: Union[str, Path]) -> Dict[str, Any]:
    return load_yaml(filepath)


def save_state_yaml(filepath: Union[str, Path], data: Dict[str, Any]) -> None:
    path = Path(filepath)
    parent_dir = path.parent
    parent_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_file = tempfile.mkstemp(dir=str(parent_dir), prefix="state_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(
                data,
                f,
                Dumper=StateYamlDumper,
                sort_keys=False,
                default_flow_style=False,
            )
        os.chmod(tmp_file, 0o600)
        os.replace(tmp_file, path)
        os.chmod(path, 0o600)
    except Exception:
        if os.path.isfile(tmp_file):
            os.remove(tmp_file)
        raise


def interpolate(template: str, context: Dict[str, Any]) -> str:
    """Standard single-pass string interpolation for human-readable prompts and messages."""
    pattern = re.compile(r"\{([a-zA-Z0-9_.-]+)\}")
    return pattern.sub(
        lambda m: str(context[m.group(1)]) if m.group(1) in context else m.group(0),
        template,
    )


def build_cmd_args(cmd_spec: Union[str, List[str]], context: Dict[str, Any]) -> List[str]:
    """Constructs a parameterized list of command arguments for safe shell=False execution.

    Accepts either a list of argument tokens or a command string which is safely tokenized
    via shlex.split. Each argument token is interpolated with context variables in a single
    pass without shell interpretation, preventing command injection vulnerabilities.
    """
    if isinstance(cmd_spec, list):
        raw_tokens = list(cmd_spec)
    else:
        raw_tokens = shlex.split(str(cmd_spec))

    pattern = re.compile(r"\{([a-zA-Z0-9_.-]+)\}")
    return [
        pattern.sub(
            lambda m: str(context[m.group(1)]) if m.group(1) in context else m.group(0),
            tok,
        )
        for tok in raw_tokens
    ]


def execute_init(init_spec: Dict[str, Any], init_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Evaluates the init section of the workflow specification using parameterized execution."""
    metadata = {}
    ctx = dict(init_context or {})
    for key, val in init_spec.items():
        if key.startswith("_"):
            continue
        if isinstance(val, dict) and "cmd" in val:
            cmd_args = build_cmd_args(val["cmd"], ctx)
            output = subprocess.check_output(cmd_args, shell=False, text=True).strip()
            metadata[key] = output
            ctx[key] = output
        else:
            metadata[key] = val
            ctx[key] = val
    return metadata


def run_yaml_workflow(spec_path: Union[str, Path], argv: Optional[List[str]] = None) -> int:
    """Executes a declarative YAML workflow given its specification file path."""
    if argv is None:
        argv = sys.argv[1:]

    path = Path(spec_path).resolve()
    spec = load_yaml(path)
    wf_name = spec.get("name", path.stem)
    description = spec.get("description", "Automated workflow")
    init_spec = spec.get("init", {})
    steps = spec.get("steps", [])

    state_dir = get_state_base_dir() / wf_name

    parser = argparse.ArgumentParser(
        prog=f"emu-dev-cli workflow {wf_name}",
        description=description,
    )
    parser.add_argument(
        "--state",
        dest="state_id",
        default=None,
        help="Unique state identifier or path to state file",
    )
    subparsers = parser.add_subparsers(dest="subcommand", metavar="<command>")
    subparsers.add_parser(
        "init",
        help=f"Initialize a new {wf_name} session",
    )

    # If invoked with no arguments at all, print help and exit
    if not argv:
        parser.print_help()
        return 0

    args = parser.parse_args(argv)

    # 1. Initialization via 'init' subcommand
    if args.subcommand == "init":
        unique_id = uuid.uuid4().hex[:8]
        init_ctx = {
            "workflow_name": wf_name,
            "workflow_dir": str(path.parent),
            "state_id": unique_id,
        }
        metadata = execute_init(init_spec, init_ctx)

        initial_state = {
            "state": "INIT",
            "step": 0,
            "retries": 0,
            "stuck_reason": "",
            "metadata": metadata,
        }
        state_file = state_dir / f"STATE-{unique_id}.yaml"
        save_state_yaml(state_file, initial_state)

        print(
            f"Please run 'emu-dev-cli workflow {wf_name} --state={unique_id}' for the next instructions"
        )
        return 0

    # If --state is missing
    if not args.state_id:
        parser.print_help()
        return 0

    # 2. Step Progression / Verification
    clean_id, state_file = resolve_state_file(state_dir, args.state_id)
    if not state_file or not state_file.is_file():
        print(f"Error: State file '{state_file}' does not exist.", file=sys.stderr)
        return 1

    state_data = load_state_yaml(state_file)
    current_step_index = state_data.get("step", 0)
    current_state = state_data.get("state", "INIT")
    retries = state_data.get("retries", 0)
    metadata = state_data.get("metadata", {})

    # Build context for template interpolation
    context = dict(metadata)
    context["workflow_name"] = wf_name
    context["workflow_dir"] = str(path.parent)
    context["state_id"] = clean_id
    context["step"] = current_step_index
    context["retries"] = retries
    context["verifier_error"] = ""

    # Find step specification
    step_spec = None
    for s in steps:
        if s.get("step") == current_step_index:
            step_spec = s
            break

    if not step_spec:
        print(f"Workflow '{wf_name}' completed successfully!")
        state_data["state"] = "DONE"
        save_state_yaml(state_file, state_data)
        return 0

    verifier_cmd = step_spec.get("verifier_cmd")
    max_retries = step_spec.get("max_retries", 3)
    context["max_retries"] = max_retries

    # Case A: Step 0 freshly started from INIT
    if current_step_index == 0 and current_state == "INIT":
        state_data["state"] = "RUNNING"
        save_state_yaml(state_file, state_data)

        raw_prompt = " ".join(step_spec.get("prompt", [])) if isinstance(step_spec.get("prompt"), list) else step_spec.get("prompt", "")
        prompt = interpolate(raw_prompt, context)
        suffix = f', and run "emu-dev-cli workflow {wf_name} --state={clean_id}" for next steps.'
        print(f"{prompt}{suffix}")
        return 0

    # Case B: Passive Step (verifier_cmd is null/empty)
    if not verifier_cmd:
        state_data["state"] = "DONE"
        state_data["stuck_reason"] = ""
        state_data["retries"] = 0
        save_state_yaml(state_file, state_data)

        raw_prompt = " ".join(step_spec.get("prompt", [])) if isinstance(step_spec.get("prompt"), list) else step_spec.get("prompt", "")
        prompt = interpolate(raw_prompt, context)
        print(prompt)
        return 0

    # Case C: Active Step with verifier_cmd executed safely with shell=False
    try:
        eval_cmd = build_cmd_args(verifier_cmd, context)
        check_res = subprocess.run(eval_cmd, shell=False, text=True, capture_output=True)
    except FileNotFoundError as e:
        check_res = subprocess.CompletedProcess(
            args=verifier_cmd,
            returncode=127,
            stdout="",
            stderr=str(e),
        )

    if check_res.returncode != 0:
        retries += 1
        state_data["retries"] = retries
        context["retries"] = retries
        verifier_msg = (check_res.stderr.strip() or check_res.stdout.strip())
        context["verifier_error"] = verifier_msg

        if retries >= max_retries:
            state_data["state"] = "STUCK"
            if "stuck_reason" in step_spec:
                reason = interpolate(step_spec["stuck_reason"], context)
            else:
                reason = f"Failed to find required input in step {current_step_index} after {retries} retries."
            state_data["stuck_reason"] = reason
            save_state_yaml(state_file, state_data)

            if "stuck_prompt" in step_spec:
                raw_prompt = " ".join(step_spec["stuck_prompt"]) if isinstance(step_spec["stuck_prompt"], list) else step_spec["stuck_prompt"]
            else:
                raw_prompt = f"Error: Step {current_step_index} verification failed. Workflow is now STUCK (retries reached {retries})."
            prompt = interpolate(raw_prompt, context)
            suffix = f', and run "emu-dev-cli workflow {wf_name} --state={clean_id}" to resume.'
            print(f"{prompt}{suffix}", file=sys.stderr)
            return 1
        else:
            state_data["state"] = "RUNNING"
            state_data["stuck_reason"] = ""
            save_state_yaml(state_file, state_data)

            if "error_prompt" in step_spec:
                raw_prompt = " ".join(step_spec["error_prompt"]) if isinstance(step_spec["error_prompt"], list) else step_spec["error_prompt"]
            else:
                raw_prompt = f"Error: Verification failed for step {current_step_index} (attempt {retries} of {max_retries})."
            prompt = interpolate(raw_prompt, context)
            suffix = f', and run "emu-dev-cli workflow {wf_name} --state={clean_id}" for next steps.'
            print(f"{prompt}{suffix}", file=sys.stderr)
            return 1

    # Step Passed -> Advance to next step
    next_step_index = current_step_index + 1
    state_data["step"] = next_step_index
    state_data["retries"] = 0
    state_data["stuck_reason"] = ""
    state_data["state"] = "RUNNING"
    save_state_yaml(state_file, state_data)

    context["step"] = next_step_index
    context["retries"] = 0

    next_spec = None
    for s in steps:
        if s.get("step") == next_step_index:
            next_spec = s
            break

    if not next_spec:
        state_data["state"] = "DONE"
        save_state_yaml(state_file, state_data)
        return 0

    if not next_spec.get("verifier_cmd"):
        state_data["state"] = "DONE"
        save_state_yaml(state_file, state_data)
        raw_prompt = " ".join(next_spec.get("prompt", [])) if isinstance(next_spec.get("prompt"), list) else next_spec.get("prompt", "")
        print(interpolate(raw_prompt, context))
        return 0

    raw_prompt = " ".join(next_spec.get("prompt", [])) if isinstance(next_spec.get("prompt"), list) else next_spec.get("prompt", "")
    prompt = interpolate(raw_prompt, context)
    suffix = f', and run "emu-dev-cli workflow {wf_name} --state={clean_id}" for next steps.'
    print(f"{prompt}{suffix}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 -m workflows.core.yaml_runner <workflow.yaml> [args...]", file=sys.stderr)
        sys.exit(1)
    spec_arg = sys.argv[1]
    extra_args = sys.argv[2:]
    sys.exit(run_yaml_workflow(spec_arg, extra_args))
