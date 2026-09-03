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


class WorkflowError(Exception):
    """Base exception for workflow runtime and composition errors."""
    pass


class WorkflowOutputError(WorkflowError):
    """Raised when a sub-workflow output key mapping fails."""
    pass


class WorkflowRecursionError(WorkflowError):
    """Raised when circular workflow dependencies or max depth is exceeded."""
    pass


class WorkflowTargetNotFoundError(WorkflowError):
    """Raised when a referenced sub-workflow cannot be found."""
    pass


def get_state_base_dir() -> Path:
    """Returns the base state directory for workflow runtime sessions."""
    custom_state_dir = os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR")
    if custom_state_dir:
        base = Path(custom_state_dir).expanduser().resolve()
    else:
        cache_home = os.environ.get("XDG_CACHE_HOME")
        base = (Path(cache_home) if cache_home else Path.home() / ".cache") / "emu-dev-cli" / "workflows" / "state"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base


def get_workflow_cache_dir(workflow_name: str) -> Path:
    """Returns the isolated, generic cache directory for a given workflow name."""
    custom_cache_dir = os.environ.get("EMU_DEV_CLI_WORKFLOW_CACHE_DIR")
    if custom_cache_dir:
        base = Path(custom_cache_dir).expanduser().resolve() / workflow_name
    else:
        custom_state_dir = os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR")
        if custom_state_dir:
            base = Path(custom_state_dir).expanduser().resolve() / workflow_name
        else:
            xdg_cache = os.environ.get("XDG_CACHE_HOME")
            if xdg_cache:
                base = Path(xdg_cache).expanduser().resolve() / "emu-dev-cli" / "workflows" / workflow_name
            else:
                base = Path.home() / ".cache" / "emu-dev-cli" / "workflows" / workflow_name
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base


def resolve_state_file(state_dir: Path, state_id: str) -> Tuple[str, Path]:
    """Resolves a state file given a state_id or a direct path to a state file."""
    if not state_id:
        return "", state_dir / "STATE.yaml"

    path_candidate = Path(state_id).expanduser()
    if path_candidate.is_file():
        stem = path_candidate.stem
        if stem.startswith("STATE-"):
            clean_id = stem[len("STATE-"):]
        else:
            clean_id = stem
        return clean_id, path_candidate.resolve()

    clean_id = state_id.strip()
    if clean_id.startswith("STATE-"):
        clean_id = clean_id[len("STATE-"):]
    if clean_id.endswith(".yaml"):
        clean_id = clean_id[:-len(".yaml")]
    elif clean_id.endswith(".yml"):
        clean_id = clean_id[:-len(".yml")]

    candidate_yaml = state_dir / f"STATE-{clean_id}.yaml"
    if candidate_yaml.is_file():
        return clean_id, candidate_yaml

    candidate_yml = state_dir / f"STATE-{clean_id}.yml"
    if candidate_yml.is_file():
        return clean_id, candidate_yml

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


def get_nested_context_val(ctx: Dict[str, Any], path: str) -> Any:
    """Resolves a variable path against a context dict, supporting dot notation and nested workflow namespaces."""
    if path in ctx:
        return ctx[path]
    if "." in path:
        parts = path.split(".")
        curr = ctx
        found = True
        for p in parts:
            if isinstance(curr, dict) and p in curr:
                curr = curr[p]
            else:
                found = False
                break
        if found:
            return curr
    # Fallback: search across nested workflow metadata dicts
    for k, v in ctx.items():
        if isinstance(v, dict) and not k.startswith("_") and path in v:
            return v[path]
    return None


def interpolate(template: str, context: Dict[str, Any]) -> str:
    """Standard single-pass string interpolation for human-readable prompts and messages."""
    pattern = re.compile(r"\{([a-zA-Z0-9_.-]+)\}")
    return pattern.sub(
        lambda m: str(val) if (val := get_nested_context_val(context, m.group(1))) is not None else m.group(0),
        template,
    )


def build_cmd_args(cmd_spec: Union[str, List[str]], context: Dict[str, Any]) -> List[str]:
    """Constructs a parameterized list of command arguments for safe shell=False execution."""
    if isinstance(cmd_spec, list):
        raw_tokens = list(cmd_spec)
    else:
        raw_tokens = shlex.split(str(cmd_spec))

    pattern = re.compile(r"\{([a-zA-Z0-9_.-]+)\}")
    return [
        pattern.sub(
            lambda m: str(val) if (val := get_nested_context_val(context, m.group(1))) is not None else m.group(0),
            tok,
        )
        for tok in raw_tokens
    ]


def execute_init(init_spec: Dict[str, Any], init_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Evaluates the init section of the workflow specification using parameterized execution."""
    metadata = {}
    ctx = dict(init_context or {})
    for key, val in init_spec.items():
        if key.startswith("_") or key == "args":
            continue
        if isinstance(val, dict) and "cmd" in val:
            cmd_args = build_cmd_args(val["cmd"], ctx)
            output = subprocess.check_output(cmd_args, shell=False, text=True).strip()
            metadata[key] = output
            ctx[key] = output
        elif isinstance(val, str):
            interpolated_val = interpolate(val, ctx)
            metadata[key] = interpolated_val
            ctx[key] = interpolated_val
        else:
            metadata[key] = val
            ctx[key] = val
    return metadata


def _parse_args_spec(raw_args: Any) -> List[Dict[str, Any]]:
    """Parses raw arguments specification into a standardized list of dicts."""
    workflow_args: List[Dict[str, Any]] = []
    if isinstance(raw_args, dict):
        for k, v in raw_args.items():
            entry = dict(v) if isinstance(v, dict) else {}
            entry["name"] = k
            workflow_args.append(entry)
    elif isinstance(raw_args, list):
        for item in raw_args:
            if isinstance(item, str):
                workflow_args.append({"name": item, "required": True})
            elif isinstance(item, dict):
                workflow_args.append(item)
    return workflow_args


def build_init_argparser(prog_name: str, workflow_args: List[Dict[str, Any]]) -> argparse.ArgumentParser:
    """Builds an ArgumentParser for workflow init arguments."""
    parser = argparse.ArgumentParser(prog=prog_name, add_help=False)
    for arg_def in workflow_args:
        arg_name = arg_def.get("name")
        if not arg_name:
            continue
        arg_help = arg_def.get("help") or arg_def.get("description", f"{arg_name} parameter")
        required = arg_def.get("required", True)
        metavar = arg_def.get("metavar")
        kwargs: Dict[str, Any] = {"help": arg_help}
        if metavar:
            kwargs["metavar"] = metavar
        if required:
            parser.add_argument(arg_name, **kwargs)
        else:
            default_val = arg_def.get("default", None)
            kwargs["default"] = default_val
            kwargs["required"] = False
            kwargs["dest"] = arg_name.replace("-", "_")
            parser.add_argument(f"--{arg_name}", **kwargs)
    return parser


def resolve_workflow_file(target_name: str, base_hint: Optional[Path] = None) -> Optional[Path]:
    """Resolves a workflow definition file by name or path."""
    target_path = Path(target_name).expanduser()
    if target_path.is_file():
        return target_path.resolve()
    if target_path.is_dir() and (target_path / "workflow.yaml").is_file():
        return (target_path / "workflow.yaml").resolve()

    try:
        from commands.workflow import find_workflow_target
        _, found = find_workflow_target(target_name)
        if found and os.path.isfile(found):
            return Path(found).resolve()
    except Exception:
        pass

    if base_hint:
        base_parent = base_hint.parent if base_hint.is_file() else base_hint
        candidates = [
            base_parent.parent / target_name.replace("-", "_") / "workflow.yaml",
            base_parent.parent / target_name / "workflow.yaml",
            base_parent / target_name / "workflow.yaml",
            base_parent / target_name.replace("-", "_") / "workflow.yaml",
        ]
        for c in candidates:
            if c.is_file():
                return c.resolve()

    return None


def check_workflow_cycles(spec_path: Path, visited: Optional[List[str]] = None) -> None:
    """Performs a static DFS check to ensure no circular workflow dependencies exist."""
    visited = visited or []
    spec = load_yaml(spec_path)
    wf_name = spec.get("name", spec_path.stem).replace("_", "-")
    if wf_name in visited:
        cycle = " -> ".join(visited + [wf_name])
        raise WorkflowRecursionError(f"Circular workflow dependency detected: {cycle}")
    new_visited = visited + [wf_name]
    for step in spec.get("steps", []):
        sub_wf = step.get("workflow")
        if not sub_wf:
            continue
        sub_name = sub_wf if isinstance(sub_wf, str) else sub_wf.get("name")
        if not sub_name:
            continue
        sub_file = resolve_workflow_file(sub_name, spec_path)
        if sub_file and sub_file.is_file():
            check_workflow_cycles(sub_file, new_visited)


def init_workflow_session(
    spec_path: Union[str, Path],
    args_input: Optional[Union[List[str], Dict[str, Any]]] = None,
    parent_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Initializes a workflow session programmatically and returns its state_id."""
    path = Path(spec_path).resolve()
    check_workflow_cycles(path)
    spec = load_yaml(path)
    wf_name = spec.get("name", path.stem).replace("_", "-")
    init_spec = spec.get("init", {})
    raw_args = spec.get("args") or init_spec.get("args") or []
    workflow_args = _parse_args_spec(raw_args)

    unique_id = uuid.uuid4().hex[:8]
    workflow_cache_dir = get_workflow_cache_dir(wf_name)
    workflow_cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    init_ctx: Dict[str, Any] = {
        "workflow_name": wf_name,
        "workflow_dir": str(path.parent),
        "state_id": unique_id,
        "cache-dir": str(workflow_cache_dir),
        "cache_dir": str(workflow_cache_dir),
        "output-dir": str(workflow_cache_dir),
        "output_dir": str(workflow_cache_dir),
    }
    initial_metadata: Dict[str, Any] = {
        "cache-dir": str(workflow_cache_dir),
    }

    if isinstance(args_input, dict):
        for arg_def in workflow_args:
            arg_name = arg_def.get("name")
            if not arg_name:
                continue
            val = args_input.get(arg_name)
            if val is None:
                val = args_input.get(arg_name.replace("-", "_"))
            if val is None and arg_def.get("default") is not None:
                val = arg_def.get("default")
            if val is None and arg_def.get("required", True):
                raise ValueError(f"Workflow '{wf_name}' requires missing argument: {arg_name}")
            if val is not None:
                if arg_name in ("bug", "bug_id", "bug_number", "bug-number", "bug-id") and isinstance(val, str):
                    clean_val = re.sub(r"^(?:https?://)?b(?:/)?", "", val.strip(), flags=re.I)
                else:
                    clean_val = str(val).strip()
                init_ctx[arg_name] = clean_val
                initial_metadata[arg_name] = clean_val
    elif isinstance(args_input, list):
        parser = build_init_argparser(f"emu-dev-cli workflow {wf_name} init", workflow_args)
        parsed_args = parser.parse_args(args_input)
        for arg_def in workflow_args:
            arg_name = arg_def.get("name")
            if arg_name:
                val = getattr(parsed_args, arg_name, None)
                if val is None:
                    val = getattr(parsed_args, arg_name.replace("-", "_"), None)
                if val is not None:
                    if arg_name in ("bug", "bug_id", "bug_number", "bug-number", "bug-id") and isinstance(val, str):
                        clean_val = re.sub(r"^(?:https?://)?b(?:/)?", "", val.strip(), flags=re.I)
                    else:
                        clean_val = str(val).strip()
                    init_ctx[arg_name] = clean_val
                    initial_metadata[arg_name] = clean_val

    evaluated_metadata = execute_init(init_spec, init_ctx)
    initial_metadata.update(evaluated_metadata)

    if parent_context and "_call_stack" in parent_context:
        initial_metadata["_call_stack"] = list(parent_context["_call_stack"]) + [wf_name]
    else:
        initial_metadata["_call_stack"] = [wf_name]

    initial_state = {
        "state": "INIT",
        "step": 0,
        "retries": 0,
        "stuck_reason": "",
        "metadata": initial_metadata,
    }
    state_dir = get_state_base_dir() / wf_name
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_file = state_dir / f"STATE-{unique_id}.yaml"
    save_state_yaml(state_file, initial_state)
    return unique_id


def execute_workflow_step(
    spec_path: Union[str, Path],
    state_arg: str,
    continuation_cmd: Optional[str] = None,
) -> int:
    """Executes a single step of a workflow, delegating sub-workflows when encountered."""
    path = Path(spec_path).resolve()
    spec = load_yaml(path)
    wf_name = spec.get("name", path.stem).replace("_", "-")
    steps = spec.get("steps", [])
    state_dir = get_state_base_dir() / wf_name

    clean_id, state_file = resolve_state_file(state_dir, state_arg)
    if not state_file or not state_file.is_file():
        print(f"Error: State file '{state_file}' does not exist.", file=sys.stderr)
        return 1

    state_data = load_state_yaml(state_file)
    current_step_index = state_data.get("step", 0)
    current_state = state_data.get("state", "INIT")
    retries = state_data.get("retries", 0)
    metadata = state_data.get("metadata", {})

    active_continuation = continuation_cmd or f'emu-dev-cli workflow {wf_name} --state={clean_id}'

    # Build context for template interpolation
    context = dict(metadata)
    context["workflow_name"] = wf_name
    context["workflow_dir"] = str(path.parent)
    context["state_id"] = clean_id
    context["step"] = current_step_index
    context["retries"] = retries
    context["verifier_error"] = ""
    context["continuation_cmd"] = active_continuation
    if "cache-dir" not in context:
        context["cache-dir"] = str(get_workflow_cache_dir(wf_name))
    if "cache_dir" not in context:
        context["cache_dir"] = context["cache-dir"]
    if "output-dir" not in context:
        context["output-dir"] = context["cache-dir"]
    if "output_dir" not in context:
        context["output_dir"] = context["cache-dir"]

    # Check terminal condition
    if current_step_index >= len(steps):
        state_data["state"] = "DONE"
        save_state_yaml(state_file, state_data)
        print(f"Workflow '{wf_name}' completed successfully!")
        return 0

    step_spec = steps[current_step_index]

    # -------------------------------------------------------------
    # Case 1: Sub-Workflow Step ('workflow' key present in step_spec)
    # -------------------------------------------------------------
    if "workflow" in step_spec:
        sub_wf_def = step_spec["workflow"]
        if isinstance(sub_wf_def, str):
            sub_name = sub_wf_def
            sub_args_raw = {}
            sub_outputs = {}
        elif isinstance(sub_wf_def, dict):
            sub_name = sub_wf_def.get("name", "")
            sub_args_raw = sub_wf_def.get("args", {})
            sub_outputs = sub_wf_def.get("outputs", {})
        else:
            print(f"Error: Invalid workflow step definition: {sub_wf_def}", file=sys.stderr)
            return 1

        sub_wf_meta = metadata.get("_sub_workflow")
        if not sub_wf_meta or sub_wf_meta.get("parent_step") != current_step_index:
            call_stack = list(metadata.get("_call_stack", [wf_name]))
            if sub_name in call_stack:
                raise WorkflowRecursionError(
                    f"Circular workflow dependency detected: {' -> '.join(call_stack)} -> {sub_name}"
                )
            if len(call_stack) >= 5:
                raise WorkflowRecursionError(
                    f"Maximum workflow composition depth (5) exceeded: {' -> '.join(call_stack)}"
                )

            child_file = resolve_workflow_file(sub_name, path)
            if not child_file:
                raise WorkflowTargetNotFoundError(f"Sub-workflow '{sub_name}' not found.")

            if isinstance(sub_args_raw, dict):
                child_args = {k: interpolate(str(v), context) for k, v in sub_args_raw.items()}
            elif isinstance(sub_args_raw, list):
                child_args = [interpolate(str(a), context) for a in sub_args_raw]
            else:
                child_args = {}

            child_spec = load_yaml(child_file)
            child_wf_name = child_spec.get("name", child_file.stem).replace("_", "-")
            child_state_id = init_workflow_session(child_file, child_args, parent_context=context)
            sub_wf_meta = {
                "name": child_wf_name,
                "state_id": child_state_id,
                "parent_step": current_step_index,
            }
            metadata["_sub_workflow"] = sub_wf_meta
            save_state_yaml(state_file, state_data)

        child_wf_name = sub_wf_meta.get("name", sub_name)
        child_state_id = sub_wf_meta["state_id"]
        child_file = resolve_workflow_file(sub_name, path)
        if not child_file:
            raise WorkflowTargetNotFoundError(f"Sub-workflow '{sub_name}' not found.")

        # Delegate execution to child step
        child_rc = execute_workflow_step(child_file, child_state_id, continuation_cmd=active_continuation)

        # Inspect child state
        child_state_dir = get_state_base_dir() / child_wf_name
        _, child_state_path = resolve_state_file(child_state_dir, child_state_id)
        child_state = load_state_yaml(child_state_path)

        if child_state.get("state") == "DONE":
            # Extract outputs strictly and nest under workflow name
            child_metadata = child_state.get("metadata", {})
            wf_meta = metadata.setdefault(child_wf_name, {})
            # Copy all child non-private metadata into workflow namespace
            for k, v in child_metadata.items():
                if not k.startswith("_"):
                    wf_meta[k] = v

            for child_k, parent_k in sub_outputs.items():
                if child_k not in child_metadata:
                    available = ", ".join(child_metadata.keys())
                    raise WorkflowOutputError(
                        f"Sub-workflow '{child_wf_name}' completed but output key '{child_k}' was not found in metadata. "
                        f"Available keys: [{available}]"
                    )
                wf_meta[parent_k] = child_metadata[child_k]

            context[child_wf_name] = wf_meta

            # Archive completed sub-workflow
            history = metadata.setdefault("_sub_workflow_history", [])
            history.append({
                "name": sub_name,
                "state_id": child_state_id,
                "parent_step": current_step_index,
                "status": "DONE",
            })
            metadata.pop("_sub_workflow", None)

            # Advance parent step
            next_step = current_step_index + 1
            state_data["step"] = next_step
            state_data["retries"] = 0
            state_data["stuck_reason"] = ""
            state_data["state"] = "RUNNING"
            save_state_yaml(state_file, state_data)

            # If parent finished
            if next_step >= len(steps):
                state_data["state"] = "DONE"
                save_state_yaml(state_file, state_data)
                print(f"Workflow '{wf_name}' completed successfully!")
                return 0

            next_spec = steps[next_step]
            # Passive step
            if not next_spec.get("verifier_cmd") and "workflow" not in next_spec:
                state_data["state"] = "DONE"
                save_state_yaml(state_file, state_data)
                raw_prompt = " ".join(next_spec.get("prompt", [])) if isinstance(next_spec.get("prompt"), list) else next_spec.get("prompt", "")
                print(interpolate(raw_prompt, context))
                return 0

            # If next step is another sub-workflow, automatically execute its first step
            if "workflow" in next_spec:
                return execute_workflow_step(path, clean_id, continuation_cmd=active_continuation)

            # Next step is active atomic step
            raw_prompt = " ".join(next_spec.get("prompt", [])) if isinstance(next_spec.get("prompt"), list) else next_spec.get("prompt", "")
            prompt = interpolate(raw_prompt, context)
            suffix = f', and run "{active_continuation}" for next steps.'
            print(f"{prompt}{suffix}")
            return 0

        elif child_state.get("state") == "STUCK":
            state_data["state"] = "STUCK"
            state_data["stuck_reason"] = f"Sub-workflow '{sub_name}' is STUCK: {child_state.get('stuck_reason', '')}"
            save_state_yaml(state_file, state_data)
            return 1
        else:
            state_data["state"] = "RUNNING"
            state_data["stuck_reason"] = ""
            save_state_yaml(state_file, state_data)
            return child_rc

    # -------------------------------------------------------------
    # Case 2: Atomic Step
    # -------------------------------------------------------------
    verifier_cmd = step_spec.get("verifier_cmd")
    max_retries = step_spec.get("max_retries", 3)
    context["max_retries"] = max_retries

    # Case 2A: Passive Step (verifier_cmd is null/empty)
    if not verifier_cmd:
        state_data["state"] = "DONE"
        state_data["stuck_reason"] = ""
        state_data["retries"] = 0
        save_state_yaml(state_file, state_data)

        raw_prompt = " ".join(step_spec.get("prompt", [])) if isinstance(step_spec.get("prompt"), list) else step_spec.get("prompt", "")
        prompt = interpolate(raw_prompt, context)
        print(prompt)
        return 0

    # Case 2B: Step 0 freshly started from INIT with active verifier
    if current_step_index == 0 and current_state == "INIT":
        state_data["state"] = "RUNNING"
        save_state_yaml(state_file, state_data)

        raw_prompt = " ".join(step_spec.get("prompt", [])) if isinstance(step_spec.get("prompt"), list) else step_spec.get("prompt", "")
        prompt = interpolate(raw_prompt, context)
        suffix = f', and run "{active_continuation}" for next steps.'
        print(f"{prompt}{suffix}")
        return 0

    # Case 2C: Active Step with verifier_cmd executed safely with shell=False
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
            suffix = f', and run "{active_continuation}" to resume.'
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
            suffix = f', and run "{active_continuation}" for next steps.'
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

    if next_step_index >= len(steps):
        state_data["state"] = "DONE"
        save_state_yaml(state_file, state_data)
        print(f"Workflow '{wf_name}' completed successfully!")
        return 0

    next_spec = steps[next_step_index]

    if "workflow" in next_spec:
        return execute_workflow_step(path, clean_id, continuation_cmd=active_continuation)

    if not next_spec.get("verifier_cmd"):
        state_data["state"] = "DONE"
        save_state_yaml(state_file, state_data)
        raw_prompt = " ".join(next_spec.get("prompt", [])) if isinstance(next_spec.get("prompt"), list) else next_spec.get("prompt", "")
        print(interpolate(raw_prompt, context))
        return 0

    raw_prompt = " ".join(next_spec.get("prompt", [])) if isinstance(next_spec.get("prompt"), list) else next_spec.get("prompt", "")
    prompt = interpolate(raw_prompt, context)
    suffix = f', and run "{active_continuation}" for next steps.'
    print(f"{prompt}{suffix}")
    return 0


def run_yaml_workflow(spec_path: Union[str, Path], argv: Optional[List[str]] = None) -> int:
    """Executes a declarative YAML workflow given its specification file path."""
    if argv is None:
        argv = sys.argv[1:]

    path = Path(spec_path).resolve()
    spec = load_yaml(path)
    wf_name = spec.get("name", path.stem).replace("_", "-")
    description = spec.get("description", "Automated workflow")
    init_spec = spec.get("init", {})
    raw_args = spec.get("args") or init_spec.get("args") or []
    workflow_args = _parse_args_spec(raw_args)

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
    init_parser = subparsers.add_parser(
        "init",
        help=f"Initialize a new {wf_name} session",
    )
    for arg_def in workflow_args:
        arg_name = arg_def.get("name")
        if not arg_name:
            continue
        arg_help = arg_def.get("help") or arg_def.get("description", f"{arg_name} parameter")
        required = arg_def.get("required", True)
        metavar = arg_def.get("metavar")
        kwargs: Dict[str, Any] = {"help": arg_help}
        if metavar:
            kwargs["metavar"] = metavar
        if required:
            init_parser.add_argument(arg_name, **kwargs)
        else:
            default_val = arg_def.get("default", None)
            kwargs["default"] = default_val
            kwargs["required"] = False
            kwargs["dest"] = arg_name.replace("-", "_")
            init_parser.add_argument(f"--{arg_name}", **kwargs)

    if not argv:
        parser.print_help()
        return 0

    args, extra_sub_args = parser.parse_known_args(argv)

    if args.subcommand == "init":
        # Extract init arguments from argv after "init"
        init_idx = argv.index("init")
        init_argv = argv[init_idx + 1:]
        unique_id = init_workflow_session(path, init_argv)
        print(
            f"Please run 'emu-dev-cli workflow {wf_name} --state={unique_id}' for the next instructions"
        )
        return 0

    if not args.state_id:
        parser.print_help()
        return 0

    return execute_workflow_step(path, args.state_id)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 -m workflows.core.yaml_runner <workflow.yaml> [args...]", file=sys.stderr)
        sys.exit(1)
    spec_arg = sys.argv[1]
    extra_args = sys.argv[2:]
    sys.exit(run_yaml_workflow(spec_arg, extra_args))
