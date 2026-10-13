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


def format_prompt_with_suffix(prompt: str, active_continuation: str, action: str = "for next steps.") -> str:
    """Formats prompt with continuation command, placing it on a new line for multiline prompts."""
    cleaned = prompt.strip()
    if "\n" in cleaned:
        return f'{cleaned}\n\nand run "{active_continuation}" {action}'
    if cleaned.endswith("."):
        cleaned = cleaned[:-1]
    return f'{cleaned}, and run "{active_continuation}" {action}'


def format_step_prompt(step_spec: Dict[str, Any], context: Dict[str, Any]) -> str:
    """Formats a step's prompt, appending custom_instructions and any pending human feedback."""
    raw_prompt = (
        " ".join(step_spec.get("prompt", []))
        if isinstance(step_spec.get("prompt"), list)
        else step_spec.get("prompt", "")
    )
    prompt = interpolate(str(raw_prompt), context).strip() if raw_prompt else ""

    custom_parts: List[str] = []
    inherited_custom = context.get("_custom_instructions", "")
    if inherited_custom and str(inherited_custom).strip():
        custom_parts.append(interpolate(str(inherited_custom).strip(), context))

    step_custom = step_spec.get("custom_instructions", "")
    if not step_custom and isinstance(step_spec.get("workflow"), dict):
        step_custom = step_spec["workflow"].get("custom_instructions", "")
    if step_custom and str(step_custom).strip():
        interpolated_sc = interpolate(str(step_custom).strip(), context)
        if interpolated_sc not in custom_parts:
            custom_parts.append(interpolated_sc)

    if custom_parts:
        joined_custom = "\n".join(custom_parts)
        if prompt:
            prompt = f"{prompt}\n\nAdditional Instructions:\n{joined_custom}"
        else:
            prompt = f"Additional Instructions:\n{joined_custom}"

    human_feedback = context.get("_human_feedback", "")
    if human_feedback and str(human_feedback).strip():
        if prompt:
            prompt = f"{prompt}\n\nHuman Verifier Feedback:\n{str(human_feedback).strip()}"
        else:
            prompt = f"Human Verifier Feedback:\n{str(human_feedback).strip()}"

    return prompt


def _is_human_verify_step(step_spec: Dict[str, Any]) -> bool:
    """Returns True if the step specification represents a human verification barrier."""
    return bool(step_spec.get("human_verify"))


def _format_human_verify_prompt(prompt: str, active_continuation: str, state_id: str) -> str:
    """Formats the message displayed when a workflow pauses at a human verification barrier."""
    base = prompt.strip() if prompt else "Human verification barrier reached."
    return (
        f"⏸️ HUMAN VERIFICATION REQUIRED:\n{base}\n\n"
        f"Do NOT proceed automatically. Please summarize your progress for the user and wait "
        f"for the human to verify and approve this step in the Workflow UI "
        f"(or via `emu-dev-cli workflow approve --state={state_id}`).\n"
        f'Once approved, run "{active_continuation}" to continue.'
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
            try:
                output = subprocess.check_output(cmd_args, shell=False, text=True).strip()
            except subprocess.CalledProcessError as e:
                raise RuntimeError(
                    f"Init command for '{key}' failed with exit code {e.returncode}."
                ) from None
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
        elif arg_def.get("positional", False):
            default_val = arg_def.get("default", None)
            kwargs["default"] = default_val
            kwargs["nargs"] = "?"
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

    # Check state base dir for dynamically created custom workflows
    state_wf_dir = get_state_base_dir() / target_name
    if state_wf_dir.is_dir():
        wf_yaml = state_wf_dir / "workflow.yaml"
        if wf_yaml.is_file():
            return wf_yaml.resolve()

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


def _normalize_arg_val(arg_name: str, val: Any) -> str:
    """Normalizes common CLI argument values such as Buganizer IDs and repository paths."""
    if arg_name in ("bug", "bug_id", "bug_number", "bug-number", "bug-id") and isinstance(val, str):
        return re.sub(r"^(?:https?://)?b(?:/)?", "", val.strip(), flags=re.I)
    if arg_name in ("repo", "repository", "repo-dir", "repo_dir") and isinstance(val, str):
        if val.strip():
            return str(Path(val.strip()).expanduser().resolve())
        return str(Path(".").resolve())
    return str(val).strip()


def init_workflow_session(
    spec_path: Union[str, Path],
    args_input: Optional[Union[List[str], Dict[str, Any]]] = None,
    parent_context: Optional[Dict[str, Any]] = None,
    state_id_override: Optional[str] = None,
) -> str:
    """Initializes a workflow session programmatically and returns its state_id."""
    path = Path(spec_path).resolve()
    check_workflow_cycles(path)
    spec = load_yaml(path)
    wf_name = spec.get("name", path.stem).replace("_", "-")
    init_spec = spec.get("init", {})
    raw_args = spec.get("args") or init_spec.get("args") or []
    workflow_args = _parse_args_spec(raw_args)

    unique_id = state_id_override or uuid.uuid4().hex[:8]
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

    declared_names = set()
    if isinstance(args_input, dict):
        for arg_def in workflow_args:
            arg_name = arg_def.get("name")
            if not arg_name:
                continue
            declared_names.add(arg_name)
            declared_names.add(arg_name.replace("-", "_"))
            val = args_input.get(arg_name)
            if val is None:
                val = args_input.get(arg_name.replace("-", "_"))
            if val is None and arg_def.get("default") is not None:
                val = arg_def.get("default")
            if val is None and arg_def.get("required", True):
                raise ValueError(f"Workflow '{wf_name}' requires missing argument: {arg_name}")
            if val is not None:
                clean_val = _normalize_arg_val(arg_name, val)
                init_ctx[arg_name] = clean_val
                initial_metadata[arg_name] = clean_val

        # Also preserve any additional shared arguments or internal metadata keys passed in dict
        for k, v in args_input.items():
            if k not in declared_names and v is not None:
                if k.startswith("_"):
                    initial_metadata[k] = v
                else:
                    clean_val = _normalize_arg_val(k, v)
                    init_ctx[k] = clean_val
                    initial_metadata[k] = clean_val
    elif isinstance(args_input, list):
        parser = build_init_argparser(f"emu-dev-cli workflow {wf_name} init", workflow_args)
        parsed_args = parser.parse_args(args_input)

        # Smart positional swap: if workflow expects repo and commit
        arg_names = [a.get("name") for a in workflow_args if a.get("name")]
        if "repo" in arg_names and "commit" in arg_names:
            repo_val = getattr(parsed_args, "repo", None)
            commit_val = getattr(parsed_args, "commit", None)
            if repo_val and commit_val and isinstance(repo_val, str) and isinstance(commit_val, str):
                if len(args_input) == 1:
                    first_arg = args_input[0]
                    if os.path.isdir(first_arg) or Path(first_arg).is_dir():
                        setattr(parsed_args, "repo", first_arg)
                        setattr(parsed_args, "commit", "HEAD")
                    else:
                        setattr(parsed_args, "commit", first_arg)
                        setattr(parsed_args, "repo", ".")
                elif not os.path.exists(repo_val) and (os.path.isdir(commit_val) or Path(commit_val).is_dir()):
                    setattr(parsed_args, "repo", commit_val)
                    setattr(parsed_args, "commit", repo_val)

        for arg_def in workflow_args:
            arg_name = arg_def.get("name")
            if arg_name:
                val = getattr(parsed_args, arg_name, None)
                if val is None:
                    val = getattr(parsed_args, arg_name.replace("-", "_"), None)
                if val is not None:
                    clean_val = _normalize_arg_val(arg_name, val)
                    init_ctx[arg_name] = clean_val
                    initial_metadata[arg_name] = clean_val

    evaluated_metadata = execute_init(init_spec, init_ctx)
    initial_metadata.update(evaluated_metadata)

    detected_cid = detect_active_conversation_id()
    if detected_cid and not initial_metadata.get("conversation_id"):
        initial_metadata["conversation_id"] = detected_cid

    if parent_context and "_call_stack" in parent_context:
        initial_metadata["_call_stack"] = list(parent_context["_call_stack"]) + [wf_name]
        if "workflow_name" in parent_context:
            initial_metadata["_parent_workflow"] = parent_context["workflow_name"]
        if "state_id" in parent_context:
            initial_metadata["_parent_state_id"] = parent_context["state_id"]
        if parent_context.get("_custom_instructions"):
            initial_metadata["_custom_instructions"] = parent_context["_custom_instructions"]
        if parent_context.get("_human_feedback"):
            initial_metadata["_human_feedback"] = parent_context["_human_feedback"]
        if parent_context.get("conversation_id") and not initial_metadata.get("conversation_id"):
            initial_metadata["conversation_id"] = parent_context["conversation_id"]
    else:
        initial_metadata["_call_stack"] = [wf_name]

    initial_state = {
        "state": "INIT",
        "step": 0,
        "iteration": 0,
        "retries": 0,
        "stuck_reason": "",
        "metadata": initial_metadata,
    }
    state_dir = get_state_base_dir() / wf_name
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_file = state_dir / f"STATE-{unique_id}.yaml"
    save_state_yaml(state_file, initial_state)
    return unique_id


def detect_active_conversation_id() -> Optional[str]:
    """Detects the active Jetski/Antigravity conversation ID from environment variables."""
    for env_key in ("ANTIGRAVITY_CONVERSATION_ID", "JETSKI_CONVERSATION_ID"):
        val = os.environ.get(env_key, "").strip()
        if val:
            return val
    src_meta = os.environ.get("ANTIGRAVITY_SOURCE_METADATA", "").strip()
    if src_meta:
        try:
            import json
            parsed = json.loads(src_meta)
            cid = (parsed.get("tool") or {}).get("conversationId")
            if cid and str(cid).strip():
                return str(cid).strip()
        except Exception:
            pass
    return None


def resume_workflow_session(
    state_id: str,
    workflow_name: Optional[str] = None,
    target_step: int = 0,
    custom_prompt: Optional[str] = None,
    wake_conversation: bool = True,
    state_base_dir: Optional[Path] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Resets a completed, paused, or stuck workflow session back to RUNNING and optionally wakes its Jetski conversation."""
    base_dir = state_base_dir or get_state_base_dir()
    target_state_file: Optional[Path] = None
    resolved_wf_name = workflow_name
    clean_id = state_id.strip()
    if clean_id.startswith("STATE-"):
        clean_id = clean_id[len("STATE-"):]

    if workflow_name:
        clean_id, cand = resolve_state_file(base_dir / workflow_name, clean_id)
        if cand.is_file():
            target_state_file = cand
    else:
        for wf_dir in base_dir.iterdir() if base_dir.is_dir() else []:
            if not wf_dir.is_dir():
                continue
            cid, cand = resolve_state_file(wf_dir, clean_id)
            if cand.is_file():
                clean_id = cid
                target_state_file = cand
                resolved_wf_name = wf_dir.name
                break

    if not target_state_file or not target_state_file.is_file():
        return False, f"Session '{state_id}' not found.", {}

    state_data = load_state_yaml(target_state_file)
    metadata = state_data.setdefault("metadata", {})
    detected_cid = detect_active_conversation_id()
    if detected_cid and not metadata.get("conversation_id"):
        metadata["conversation_id"] = detected_cid

    next_iteration = int(state_data.get("iteration", 0) or 0) + 1
    state_data["state"] = "RUNNING"
    state_data["step"] = int(target_step)
    state_data["retries"] = 0
    state_data["iteration"] = next_iteration
    state_data["stuck_reason"] = ""
    metadata.pop("_sub_workflow", None)

    continuation_cmd = f"emu-dev-cli workflow {resolved_wf_name} --state={clean_id}"
    spec_path_str = metadata.get("_workflow_spec_path")
    spec_file = (
        Path(spec_path_str)
        if spec_path_str and Path(spec_path_str).is_file()
        else (resolve_workflow_file(resolved_wf_name) if resolved_wf_name else None)
    )
    step_prompt = ""
    if spec_file and spec_file.is_file():
        spec = load_yaml(spec_file)
        steps = spec.get("steps", [])
        if 0 <= target_step < len(steps):
            ctx = dict(metadata)
            ctx["workflow_name"] = resolved_wf_name or ""
            ctx["workflow_dir"] = str(spec_file.parent)
            ctx["state_id"] = clean_id
            ctx["step"] = target_step
            ctx["iteration"] = next_iteration
            ctx["retries"] = 0
            ctx["continuation_cmd"] = continuation_cmd
            step_prompt = format_Prompt_for_resume(steps[target_step], ctx, continuation_cmd)
            metadata["_last_step_prompt"] = step_prompt

    save_state_yaml(target_state_file, state_data)

    conv_id = str(metadata.get("conversation_id", "") or "").strip()
    wake_status = "not_requested"
    wake_detail = ""
    if wake_conversation and conv_id:
        effective_msg = (
            custom_prompt.strip()
            if custom_prompt and custom_prompt.strip()
            else (
                f"Workflow session '{clean_id}' ({resolved_wf_name}, iteration {next_iteration}) has been resumed with new work.\n\n"
                f"{step_prompt or f'Please run `{continuation_cmd}` to continue.'}"
            )
        )
        woke = False
        if os.environ.get("ANTIGRAVITY_LS_ADDRESS") and conv_id != detected_cid:
            try:
                from lib.agent import AgentApiClient
                AgentApiClient().send_message(
                    recipient_id=conv_id,
                    message=effective_msg,
                    title=f"Resume {resolved_wf_name} ({clean_id})",
                )
                woke = True
                wake_status = "agentapi_sent"
                wake_detail = f"Sent wake message via agentapi to conversation {conv_id}"
            except Exception as exc:
                wake_detail = f"agentapi send_message note: {exc}"

        if not woke:
            try:
                from workflows.ui.server import launch_jetski_for_session
                ok_launch, launch_msg, _ = launch_jetski_for_session(
                    workflow_name=resolved_wf_name or "",
                    state_id=clean_id,
                    resume=True,
                    poke=True,
                    custom_prompt=effective_msg,
                    state_base_dir=base_dir,
                )
                if ok_launch:
                    woke = True
                    wake_status = "jetski_cli_launched"
                    wake_detail = launch_msg
                else:
                    wake_status = "manual_required"
                    wake_detail = launch_msg
            except Exception as exc:
                wake_status = "manual_required"
                wake_detail = str(exc)

    info = {
        "workflow_name": resolved_wf_name,
        "state_id": clean_id,
        "state_file": str(target_state_file),
        "step": target_step,
        "iteration": next_iteration,
        "conversation_id": conv_id,
        "continuation_cmd": continuation_cmd,
        "wake_status": wake_status,
        "wake_detail": wake_detail,
        "step_prompt": step_prompt,
    }
    msg = (
        f"Resumed workflow '{resolved_wf_name}' (session {clean_id}, iteration {next_iteration}) at step {target_step}."
        + (f" Conversation: {conv_id} ({wake_status})." if conv_id else "")
    )
    return True, msg, info


def format_Prompt_for_resume(
    step_spec: Dict[str, Any],
    context: Dict[str, Any],
    continuation_cmd: str,
) -> str:
    """Formats a step prompt with continuation instructions for session resumption."""
    prompt = format_step_prompt(step_spec, context)
    if not prompt:
        return f'Run "{continuation_cmd}" for next steps.'
    return format_prompt_with_suffix(prompt, continuation_cmd, "for next steps.")


def approve_workflow_session(
    state_id: str,
    workflow_name: Optional[str] = None,
    feedback: Optional[str] = None,
    state_base_dir: Optional[Path] = None,
    advance: bool = False,
) -> Tuple[bool, str]:
    """Approves a workflow session (and any active child sub-workflow) paused at a human verification barrier."""
    base_dir = state_base_dir or get_state_base_dir()
    target_state_file: Optional[Path] = None
    resolved_wf_name = workflow_name

    if workflow_name:
        clean_id, cand = resolve_state_file(base_dir / workflow_name, state_id)
        if cand.is_file():
            target_state_file = cand
    else:
        clean_id = state_id.strip()
        if clean_id.startswith("STATE-"):
            clean_id = clean_id[len("STATE-"):]
        for wf_dir in base_dir.iterdir() if base_dir.is_dir() else []:
            if not wf_dir.is_dir():
                continue
            _, cand = resolve_state_file(wf_dir, clean_id)
            if cand.is_file():
                target_state_file = cand
                resolved_wf_name = wf_dir.name
                break

    if not target_state_file or not target_state_file.is_file():
        return False, f"Session '{state_id}' not found."

    state_data = load_state_yaml(target_state_file)
    metadata = state_data.setdefault("metadata", {})
    current_step = state_data.get("step", 0)

    # If there is an active child sub-workflow, approve it recursively as well
    sub_wf = metadata.get("_sub_workflow")
    if isinstance(sub_wf, dict) and sub_wf.get("state_id") and sub_wf.get("name"):
        approve_workflow_session(
            state_id=str(sub_wf["state_id"]),
            workflow_name=str(sub_wf["name"]),
            feedback=feedback,
            state_base_dir=base_dir,
            advance=False,
        )

    approved_steps = metadata.setdefault("_approved_steps", [])
    if current_step not in approved_steps:
        approved_steps.append(current_step)

    if feedback and str(feedback).strip():
        metadata["_human_feedback"] = str(feedback).strip()

    if state_data.get("state") in ("WAITING_HUMAN", "STUCK"):
        state_data["state"] = "RUNNING"
        state_data["stuck_reason"] = ""

    save_state_yaml(target_state_file, state_data)

    if advance and resolved_wf_name:
        spec_path_str = metadata.get("_workflow_spec_path")
        spec_file = Path(spec_path_str) if spec_path_str and Path(spec_path_str).is_file() else resolve_workflow_file(resolved_wf_name)
        if spec_file and spec_file.is_file():
            old_env = os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR")
            if state_base_dir is not None:
                os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = str(base_dir)
            try:
                execute_workflow_step(spec_file, clean_id)
            finally:
                if state_base_dir is not None:
                    if old_env is not None:
                        os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = old_env
                    else:
                        os.environ.pop("EMU_DEV_CLI_WORKFLOW_STATE_DIR", None)

    return True, f"Approved workflow '{resolved_wf_name}' (session {state_id}) at step {current_step}."


def execute_workflow_step(
    spec_path: Union[str, Path],
    state_arg: str,
    continuation_cmd: Optional[str] = None,
) -> int:
    """Executes a single step of a workflow, delegating sub-workflows when encountered."""
    path = Path(spec_path).resolve()
    spec = load_yaml(path)
    wf_name = spec.get("name", path.stem).replace("_", "-")
    state_dir = get_state_base_dir() / wf_name

    clean_id, state_file = resolve_state_file(state_dir, state_arg)
    if not state_file or not state_file.is_file():
        print(f"Error: State file '{state_file}' does not exist.", file=sys.stderr)
        return 1

    state_data = load_state_yaml(state_file)
    metadata = state_data.setdefault("metadata", {})
    detected_cid = detect_active_conversation_id()
    if detected_cid and not metadata.get("conversation_id"):
        metadata["conversation_id"] = detected_cid
        save_state_yaml(state_file, state_data)

    # Support session-specific composed workflow YAML override
    custom_spec_path = metadata.get("_workflow_spec_path")
    if custom_spec_path and Path(custom_spec_path).is_file():
        path = Path(custom_spec_path).resolve()
        spec = load_yaml(path)
        wf_name = spec.get("name", wf_name).replace("_", "-")

    steps = spec.get("steps", [])
    current_step_index = state_data.get("step", 0)
    current_state = state_data.get("state", "INIT")
    retries = state_data.get("retries", 0)
    iteration = int(state_data.get("iteration", 0) or 0)

    active_continuation = continuation_cmd or f'emu-dev-cli workflow {wf_name} --state={clean_id}'

    # Build context for template interpolation
    context = dict(metadata)
    context["workflow_name"] = wf_name
    context["workflow_dir"] = str(path.parent)
    context["state_id"] = clean_id
    context["step"] = current_step_index
    context["iteration"] = iteration
    context["retries"] = retries
    context["verifier_error"] = ""
    context["verifier_output"] = ""
    context["continuation_cmd"] = active_continuation
    if "cache-dir" not in context:
        context["cache-dir"] = str(get_workflow_cache_dir(wf_name))
    if "cache_dir" not in context:
        context["cache_dir"] = context["cache-dir"]
    if "output-dir" not in context:
        context["output-dir"] = context["cache-dir"]
    if "output_dir" not in context:
        context["output_dir"] = context["cache-dir"]

    def _advance_to_step(next_step_idx: int) -> int:
        state_data["step"] = next_step_idx
        state_data["retries"] = 0
        state_data["stuck_reason"] = ""
        state_data["state"] = "RUNNING"
        save_state_yaml(state_file, state_data)

        context["step"] = next_step_idx
        context["retries"] = 0

        if next_step_idx >= len(steps):
            state_data["state"] = "DONE"
            save_state_yaml(state_file, state_data)
            print(f"Workflow '{wf_name}' completed successfully!")
            return 0

        next_spec = steps[next_step_idx]

        # If next step is a sub-workflow, automatically execute its first step
        if "workflow" in next_spec:
            return execute_workflow_step(path, clean_id, continuation_cmd=active_continuation)

        # If next step is a human verification barrier, enter WAITING_HUMAN
        if _is_human_verify_step(next_spec):
            state_data["state"] = "WAITING_HUMAN"
            save_state_yaml(state_file, state_data)
            prompt = format_step_prompt(next_spec, context)
            print(_format_human_verify_prompt(prompt, active_continuation, clean_id))
            return 0

        # Passive completion step
        if not next_spec.get("verifier_cmd"):
            state_data["state"] = "DONE"
            save_state_yaml(state_file, state_data)
            print(format_step_prompt(next_spec, context))
            return 0

        # Active atomic step
        prompt = format_step_prompt(next_spec, context)
        if metadata.pop("_human_feedback", None) is not None:
            context.pop("_human_feedback", None)
            save_state_yaml(state_file, state_data)
        print(format_prompt_with_suffix(prompt, active_continuation, "for next steps."))
        return 0

    # Check terminal condition
    if current_step_index >= len(steps):
        state_data["state"] = "DONE"
        save_state_yaml(state_file, state_data)
        print(f"Workflow '{wf_name}' completed successfully!")
        return 0

    step_spec = steps[current_step_index]

    # -------------------------------------------------------------
    # Case 0: Human Verification Barrier Step ('human_verify: true')
    # -------------------------------------------------------------
    if _is_human_verify_step(step_spec):
        approved_steps = metadata.get("_approved_steps", [])
        if current_step_index in approved_steps:
            return _advance_to_step(current_step_index + 1)

        state_data["state"] = "WAITING_HUMAN"
        save_state_yaml(state_file, state_data)
        prompt = format_step_prompt(step_spec, context)
        print(_format_human_verify_prompt(prompt, active_continuation, clean_id))
        return 0

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

        step_custom_instr = step_spec.get("custom_instructions", "")
        if not step_custom_instr and isinstance(sub_wf_def, dict):
            step_custom_instr = sub_wf_def.get("custom_instructions", "")

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
            # Auto-forward shared arguments from parent context if child declares them
            if isinstance(child_args, dict):
                child_arg_defs = _parse_args_spec(
                    child_spec.get("args") or child_spec.get("init", {}).get("args") or []
                )
                for c_arg in child_arg_defs:
                    c_name = c_arg.get("name")
                    if c_name and c_name not in child_args:
                        inherited_val = get_nested_context_val(context, c_name)
                        if inherited_val is None:
                            inherited_val = get_nested_context_val(context, c_name.replace("-", "_"))
                        if inherited_val is not None and str(inherited_val).strip() != "":
                            child_args[c_name] = str(inherited_val)

            child_parent_ctx = dict(context)
            if step_custom_instr:
                child_parent_ctx["_custom_instructions"] = interpolate(str(step_custom_instr), context)

            child_wf_name = child_spec.get("name", child_file.stem).replace("_", "-")
            child_state_id = init_workflow_session(child_file, child_args, parent_context=child_parent_ctx)
            sub_wf_meta = {
                "name": child_wf_name,
                "state_id": child_state_id,
                "parent_step": current_step_index,
            }
            metadata["_sub_workflow"] = sub_wf_meta
            save_state_yaml(state_file, state_data)
            if metadata.pop("_human_feedback", None) is not None:
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

            if child_metadata.get("_human_feedback") and not metadata.get("_human_feedback"):
                metadata["_human_feedback"] = child_metadata["_human_feedback"]
                context["_human_feedback"] = child_metadata["_human_feedback"]

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

            return _advance_to_step(current_step_index + 1)

        elif child_state.get("state") == "WAITING_HUMAN":
            state_data["state"] = "WAITING_HUMAN"
            state_data["stuck_reason"] = ""
            save_state_yaml(state_file, state_data)
            return child_rc
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

        prompt = format_step_prompt(step_spec, context)
        print(prompt)
        return 0

    # Case 2B: Step 0 freshly started from INIT with active verifier
    if current_step_index == 0 and current_state == "INIT":
        state_data["state"] = "RUNNING"
        prompt = format_step_prompt(step_spec, context)
        if metadata.pop("_human_feedback", None) is not None:
            context.pop("_human_feedback", None)
        save_state_yaml(state_file, state_data)

        print(format_prompt_with_suffix(prompt, active_continuation, "for next steps."))
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

    verifier_out = (check_res.stdout.strip() or check_res.stderr.strip())
    context["verifier_output"] = verifier_out

    if step_spec.get("loop_to_step") is not None:
        loop_exit_code = int(step_spec.get("loop_exit_code", 2))
        max_iterations = int(step_spec.get("max_iterations", 5))
        if check_res.returncode == loop_exit_code:
            next_iter = iteration + 1
            if next_iter >= max_iterations:
                state_data["state"] = "STUCK"
                state_data["stuck_reason"] = (
                    f"Reached maximum loop iterations ({max_iterations}) at step {current_step_index}: {verifier_out}"
                )
                save_state_yaml(state_file, state_data)
                print(
                    format_prompt_with_suffix(
                        state_data["stuck_reason"], active_continuation, "to resume."
                    ),
                    file=sys.stderr,
                )
                return 1

            target_step_idx = int(step_spec["loop_to_step"])
            state_data["iteration"] = next_iter
            state_data["step"] = target_step_idx
            state_data["retries"] = 0
            state_data["stuck_reason"] = ""
            state_data["state"] = "RUNNING"
            save_state_yaml(state_file, state_data)

            context["iteration"] = next_iter
            context["step"] = target_step_idx
            context["retries"] = 0

            loop_header = ""
            if "loop_prompt" in step_spec:
                raw_lp = (
                    " ".join(step_spec["loop_prompt"])
                    if isinstance(step_spec["loop_prompt"], list)
                    else str(step_spec["loop_prompt"])
                )
                loop_header = interpolate(raw_lp, context).strip()

            target_spec = steps[target_step_idx] if 0 <= target_step_idx < len(steps) else step_spec
            target_prompt = format_step_prompt(target_spec, context)
            combined_prompt = (
                f"{loop_header}\n\n{target_prompt}".strip()
                if loop_header
                else target_prompt
            )
            print(format_prompt_with_suffix(combined_prompt, active_continuation, "for next steps."))
            return 0

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
            print(format_prompt_with_suffix(prompt, active_continuation, "to resume."), file=sys.stderr)
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
            print(format_prompt_with_suffix(prompt, active_continuation, "for next steps."), file=sys.stderr)
            return 1

    # Step Passed -> Advance to next step
    return _advance_to_step(current_step_index + 1)


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
        try:
            unique_id = init_workflow_session(path, init_argv)
        except Exception as e:
            print(f"Error initializing workflow '{wf_name}': {e}", file=sys.stderr)
            return 1
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
