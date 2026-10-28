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
"""Polymorphic WorkflowStep classes implementing State and Composite patterns."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Union

from .yaml_runner import (
    WorkflowOutputError,
    WorkflowRecursionError,
    WorkflowTargetNotFoundError,
    _build_subprocess_env,
    _format_human_verify_prompt,
    _is_human_verify_step,
    _parse_args_spec,
    build_cmd_args,
    format_prompt_with_suffix,
    format_step_prompt,
    get_nested_context_val,
    get_state_base_dir,
    init_workflow_session,
    interpolate,
    load_state_yaml,
    load_yaml,
    resolve_state_file,
    resolve_workflow_file,
    save_state_yaml,
)

MAX_SUBWORKFLOW_DEPTH = 5
DEFAULT_STEP_TIMEOUT_SEC = 600.0
COMMAND_NOT_FOUND_EXIT_CODE = 127
DEFAULT_MAX_RETRIES = 3
DEFAULT_LOOP_EXIT_CODE = 2
DEFAULT_MAX_LOOP_ITERATIONS = 5


@dataclass
class StepExecutionContext:
    """Encapsulates runtime state and context for executing a workflow step."""

    wf_name: str
    spec_path: Path
    state_id: str
    state_file: Path
    state_data: Dict[str, Any]
    metadata: Dict[str, Any]
    steps: List[Dict[str, Any]]
    current_step_index: int
    current_state: str
    retries: int
    iteration: int
    active_continuation: str
    context: Dict[str, Any]
    execute_step_fn: Callable[[Union[str, Path], str, Optional[str]], int]

    def save_state(self) -> None:
        """Persists the current workflow state dictionary to disk."""
        save_state_yaml(self.state_file, self.state_data)

    def advance_to_step(self, next_step_idx: int) -> int:
        """Transitions the workflow session to the given step index and runs its entry action."""
        self.state_data["step"] = next_step_idx
        self.state_data["retries"] = 0
        self.state_data["stuck_reason"] = ""
        self.state_data["state"] = "RUNNING"
        self.save_state()

        self.context["step"] = next_step_idx
        self.context["retries"] = 0

        if next_step_idx >= len(self.steps):
            self.state_data["state"] = "DONE"
            self.save_state()
            print(f"Workflow '{self.wf_name}' completed successfully!")
            return 0

        next_step = WorkflowStepFactory.create_step(self.steps[next_step_idx])
        return next_step.on_enter(self)


class WorkflowStep(ABC):
    """Abstract base class for polymorphic workflow step states."""

    def __init__(self, step_spec: Dict[str, Any]):
        self.step_spec = step_spec

    @abstractmethod
    def on_enter(self, ctx: StepExecutionContext) -> int:
        """Handles entry into this step when advancing from a preceding step."""

    @abstractmethod
    def execute(self, ctx: StepExecutionContext) -> int:
        """Executes or verifies this step when invoked by the workflow runner."""


class HumanVerifyStep(WorkflowStep):
    """Pauses execution at a human verification barrier until explicitly approved."""

    def on_enter(self, ctx: StepExecutionContext) -> int:
        ctx.state_data["state"] = "WAITING_HUMAN"
        ctx.save_state()
        prompt = format_step_prompt(self.step_spec, ctx.context)
        print(_format_human_verify_prompt(prompt, ctx.active_continuation, ctx.state_id))
        return 0

    def execute(self, ctx: StepExecutionContext) -> int:
        approved_steps = ctx.metadata.get("_approved_steps", [])
        if ctx.current_step_index in approved_steps:
            return ctx.advance_to_step(ctx.current_step_index + 1)
        return self.on_enter(ctx)


class SubWorkflowStep(WorkflowStep):
    """Composite workflow step that delegates execution to a child workflow."""

    def on_enter(self, ctx: StepExecutionContext) -> int:
        return ctx.execute_step_fn(ctx.spec_path, ctx.state_id, ctx.active_continuation)

    def _parse_sub_workflow_def(
        self,
    ) -> Optional[ tuple[str, Union[Dict[str, Any], List[Any]], Dict[str, Any], str] ]:
        sub_wf_def = self.step_spec["workflow"]
        if isinstance(sub_wf_def, str):
            sub_name = sub_wf_def
            sub_args_raw: Union[Dict[str, Any], List[Any]] = {}
            sub_outputs: Dict[str, Any] = {}
        elif isinstance(sub_wf_def, dict):
            sub_name = sub_wf_def.get("name", "")
            sub_args_raw = sub_wf_def.get("args", {})
            sub_outputs = sub_wf_def.get("outputs", {})
        else:
            print(
                f"Error: Invalid workflow step definition '{sub_wf_def}' in workflow.yaml: "
                "expected a sub-workflow name string or a mapping with '{name, args, outputs}'.",
                file=sys.stderr,
            )
            return None

        step_custom_instr = self.step_spec.get("custom_instructions", "")
        if not step_custom_instr and isinstance(sub_wf_def, dict):
            step_custom_instr = sub_wf_def.get("custom_instructions", "")
        return sub_name, sub_args_raw, sub_outputs, step_custom_instr

    def _ensure_child_session(
        self,
        ctx: StepExecutionContext,
        sub_name: str,
        sub_args_raw: Union[Dict[str, Any], List[Any]],
        step_custom_instr: str,
    ) -> Dict[str, Any]:
        sub_wf_meta = ctx.metadata.get("_sub_workflow")
        if sub_wf_meta and sub_wf_meta.get("parent_step") == ctx.current_step_index:
            return sub_wf_meta

        call_stack = list(ctx.metadata.get("_call_stack", [ctx.wf_name]))
        if sub_name in call_stack:
            raise WorkflowRecursionError(
                f"Circular workflow dependency detected: {' -> '.join(call_stack)} -> {sub_name}"
            )
        if len(call_stack) >= MAX_SUBWORKFLOW_DEPTH:
            raise WorkflowRecursionError(
                f"Maximum workflow composition depth ({MAX_SUBWORKFLOW_DEPTH}) exceeded: {' -> '.join(call_stack)}"
            )

        child_file = resolve_workflow_file(sub_name, ctx.spec_path)
        if not child_file:
            raise WorkflowTargetNotFoundError(
                f"Sub-workflow '{sub_name}' referenced by '{ctx.wf_name}' ({ctx.spec_path}) not found. "
                "Verify the sub-workflow name under workflows/."
            )

        if isinstance(sub_args_raw, dict):
            child_args: Union[Dict[str, Any], List[str]] = {
                k: interpolate(str(v), ctx.context) for k, v in sub_args_raw.items()
            }
        elif isinstance(sub_args_raw, list):
            child_args = [interpolate(str(a), ctx.context) for a in sub_args_raw]
        else:
            child_args = {}

        child_spec = load_yaml(child_file)
        if isinstance(child_args, dict):
            child_arg_defs = _parse_args_spec(
                child_spec.get("args") or child_spec.get("init", {}).get("args") or []
            )
            for c_arg in child_arg_defs:
                c_name = c_arg.get("name")
                if c_name and c_name not in child_args:
                    inherited_val = get_nested_context_val(ctx.context, c_name)
                    if inherited_val is None:
                        inherited_val = get_nested_context_val(
                            ctx.context, c_name.replace("-", "_")
                        )
                    if inherited_val is not None and str(inherited_val).strip() != "":
                        child_args[c_name] = str(inherited_val)

        child_parent_ctx = dict(ctx.context)
        if step_custom_instr:
            child_parent_ctx["_custom_instructions"] = interpolate(
                str(step_custom_instr), ctx.context
            )

        child_wf_name = child_spec.get("name", child_file.stem).replace("_", "-")
        child_state_id = init_workflow_session(
            child_file, child_args, parent_context=child_parent_ctx
        )
        sub_wf_meta = {
            "name": child_wf_name,
            "state_id": child_state_id,
            "parent_step": ctx.current_step_index,
        }
        ctx.metadata["_sub_workflow"] = sub_wf_meta
        ctx.save_state()
        if ctx.metadata.pop("_human_feedback", None) is not None:
            ctx.save_state()
        return sub_wf_meta

    def execute(self, ctx: StepExecutionContext) -> int:
        parsed = self._parse_sub_workflow_def()
        if parsed is None:
            return 1
        sub_name, sub_args_raw, sub_outputs, step_custom_instr = parsed

        sub_wf_meta = self._ensure_child_session(
            ctx, sub_name, sub_args_raw, step_custom_instr
        )
        child_wf_name = sub_wf_meta.get("name", sub_name)
        child_state_id = sub_wf_meta["state_id"]
        child_file = resolve_workflow_file(sub_name, ctx.spec_path)
        if not child_file:
            raise WorkflowTargetNotFoundError(
                f"Sub-workflow '{sub_name}' referenced by '{ctx.wf_name}' ({ctx.spec_path}) not found. "
                "Verify the sub-workflow name under workflows/."
            )

        child_rc = ctx.execute_step_fn(
            child_file, child_state_id, ctx.active_continuation
        )

        child_state_dir = get_state_base_dir() / child_wf_name
        _, child_state_path = resolve_state_file(child_state_dir, child_state_id)
        child_state = load_state_yaml(child_state_path)
        child_status = child_state.get("state")

        if child_status == "DONE":
            child_metadata = child_state.get("metadata", {})
            wf_meta = ctx.metadata.setdefault(child_wf_name, {})
            for k, v in child_metadata.items():
                if not k.startswith("_"):
                    wf_meta[k] = v

            if child_metadata.get("_human_feedback") and not ctx.metadata.get("_human_feedback"):
                ctx.metadata["_human_feedback"] = child_metadata["_human_feedback"]
                ctx.context["_human_feedback"] = child_metadata["_human_feedback"]

            for child_k, parent_k in sub_outputs.items():
                if child_k not in child_metadata:
                    available = ", ".join(child_metadata.keys())
                    raise WorkflowOutputError(
                        f"Sub-workflow '{child_wf_name}' completed but output key '{child_k}' was not found in metadata. "
                        f"Available keys: [{available}]"
                    )
                wf_meta[parent_k] = child_metadata[child_k]

            ctx.context[child_wf_name] = wf_meta

            history = ctx.metadata.setdefault("_sub_workflow_history", [])
            history.append({
                "name": sub_name,
                "state_id": child_state_id,
                "parent_step": ctx.current_step_index,
                "status": "DONE",
            })
            ctx.metadata.pop("_sub_workflow", None)
            return ctx.advance_to_step(ctx.current_step_index + 1)

        if child_status == "WAITING_HUMAN":
            ctx.state_data["state"] = "WAITING_HUMAN"
            ctx.state_data["stuck_reason"] = ""
            ctx.save_state()
            return child_rc

        if child_status == "STUCK":
            ctx.state_data["state"] = "STUCK"
            ctx.state_data["stuck_reason"] = (
                f"Sub-workflow '{sub_name}' is STUCK: {child_state.get('stuck_reason', '')}"
            )
            ctx.save_state()
            return 1

        ctx.state_data["state"] = "RUNNING"
        ctx.state_data["stuck_reason"] = ""
        ctx.save_state()
        return child_rc


class PassiveStep(WorkflowStep):
    """Step without a verifier_cmd that marks the workflow DONE and outputs its prompt."""

    def on_enter(self, ctx: StepExecutionContext) -> int:
        ctx.state_data["state"] = "DONE"
        ctx.save_state()
        print(format_step_prompt(self.step_spec, ctx.context))
        return 0

    def execute(self, ctx: StepExecutionContext) -> int:
        ctx.state_data["state"] = "DONE"
        ctx.state_data["stuck_reason"] = ""
        ctx.state_data["retries"] = 0
        ctx.save_state()
        print(format_step_prompt(self.step_spec, ctx.context))
        return 0


class AtomicVerifierStep(WorkflowStep):
    """Step guarded by a parameterized verifier_cmd with bounded retry handling."""

    def on_enter(self, ctx: StepExecutionContext) -> int:
        prompt = format_step_prompt(self.step_spec, ctx.context)
        if ctx.metadata.pop("_human_feedback", None) is not None:
            ctx.context.pop("_human_feedback", None)
            ctx.save_state()
        print(format_prompt_with_suffix(prompt, ctx.active_continuation, "for next steps."))
        return 0

    def _run_verifier(self, ctx: StepExecutionContext) -> subprocess.CompletedProcess:
        verifier_cmd = self.step_spec.get("verifier_cmd")
        try:
            eval_cmd = build_cmd_args(verifier_cmd, ctx.context)
            step_timeout = float(self.step_spec.get("timeout", DEFAULT_STEP_TIMEOUT_SEC))
            return subprocess.run(
                eval_cmd,
                shell=False,
                text=True,
                capture_output=True,
                env=_build_subprocess_env(),
                timeout=step_timeout,
            )
        except FileNotFoundError as exc:
            return subprocess.CompletedProcess(
                args=verifier_cmd,
                returncode=COMMAND_NOT_FOUND_EXIT_CODE,
                stdout="",
                stderr=str(exc),
            )

    def _handle_loop_outcome(
        self,
        ctx: StepExecutionContext,
        check_res: subprocess.CompletedProcess,
        verifier_out: str,
    ) -> Optional[int]:
        return None

    def _handle_verification_failure(
        self,
        ctx: StepExecutionContext,
        check_res: subprocess.CompletedProcess,
        max_retries: int,
    ) -> int:
        retries = ctx.retries + 1
        ctx.retries = retries
        ctx.state_data["retries"] = retries
        ctx.context["retries"] = retries
        verifier_msg = check_res.stderr.strip() or check_res.stdout.strip()
        ctx.context["verifier_error"] = verifier_msg

        if retries >= max_retries:
            ctx.state_data["state"] = "STUCK"
            if "stuck_reason" in self.step_spec:
                reason = interpolate(self.step_spec["stuck_reason"], ctx.context)
            else:
                reason = (
                    f"Failed to find required input in step {ctx.current_step_index} "
                    f"after {retries} retries."
                )
            ctx.state_data["stuck_reason"] = reason
            ctx.save_state()

            if "stuck_prompt" in self.step_spec:
                raw_prompt = (
                    " ".join(self.step_spec["stuck_prompt"])
                    if isinstance(self.step_spec["stuck_prompt"], list)
                    else self.step_spec["stuck_prompt"]
                )
            else:
                raw_prompt = (
                    f"Error: Step {ctx.current_step_index} verification failed. "
                    f"Workflow is now STUCK (retries reached {retries})."
                )
            prompt = interpolate(raw_prompt, ctx.context)
            print(
                format_prompt_with_suffix(prompt, ctx.active_continuation, "to resume."),
                file=sys.stderr,
            )
            return 1

        ctx.state_data["state"] = "RUNNING"
        ctx.state_data["stuck_reason"] = ""
        ctx.save_state()

        if "error_prompt" in self.step_spec:
            raw_prompt = (
                " ".join(self.step_spec["error_prompt"])
                if isinstance(self.step_spec["error_prompt"], list)
                else self.step_spec["error_prompt"]
            )
        else:
            raw_prompt = (
                f"Error: Verification failed for step {ctx.current_step_index} "
                f"(attempt {retries} of {max_retries})."
            )
        prompt = interpolate(raw_prompt, ctx.context)
        print(
            format_prompt_with_suffix(prompt, ctx.active_continuation, "for next steps."),
            file=sys.stderr,
        )
        return 1

    def execute(self, ctx: StepExecutionContext) -> int:
        max_retries = self.step_spec.get("max_retries", DEFAULT_MAX_RETRIES)
        ctx.context["max_retries"] = max_retries

        if ctx.current_step_index == 0 and ctx.current_state == "INIT":
            ctx.state_data["state"] = "RUNNING"
            prompt = format_step_prompt(self.step_spec, ctx.context)
            if ctx.metadata.pop("_human_feedback", None) is not None:
                ctx.context.pop("_human_feedback", None)
            ctx.save_state()
            print(format_prompt_with_suffix(prompt, ctx.active_continuation, "for next steps."))
            return 0

        check_res = self._run_verifier(ctx)
        verifier_out = check_res.stdout.strip() or check_res.stderr.strip()
        ctx.context["verifier_output"] = verifier_out

        loop_rc = self._handle_loop_outcome(ctx, check_res, verifier_out)
        if loop_rc is not None:
            return loop_rc

        if check_res.returncode != 0:
            return self._handle_verification_failure(ctx, check_res, max_retries)

        return ctx.advance_to_step(ctx.current_step_index + 1)


class LoopStep(AtomicVerifierStep):
    """Verifier step that loops back to an earlier step on a configured loop_exit_code."""

    def _handle_loop_outcome(
        self,
        ctx: StepExecutionContext,
        check_res: subprocess.CompletedProcess,
        verifier_out: str,
    ) -> Optional[int]:
        loop_exit_code = int(self.step_spec.get("loop_exit_code", DEFAULT_LOOP_EXIT_CODE))
        max_iterations = int(self.step_spec.get("max_iterations", DEFAULT_MAX_LOOP_ITERATIONS))
        if check_res.returncode != loop_exit_code:
            return None

        next_iter = ctx.iteration + 1
        if next_iter >= max_iterations:
            ctx.state_data["state"] = "STUCK"
            ctx.state_data["stuck_reason"] = (
                f"Reached maximum loop iterations ({max_iterations}) "
                f"at step {ctx.current_step_index}: {verifier_out}"
            )
            ctx.save_state()
            print(
                format_prompt_with_suffix(
                    ctx.state_data["stuck_reason"], ctx.active_continuation, "to resume."
                ),
                file=sys.stderr,
            )
            return 1

        target_step_idx = int(self.step_spec["loop_to_step"])
        ctx.state_data["iteration"] = next_iter
        ctx.state_data["step"] = target_step_idx
        ctx.state_data["retries"] = 0
        ctx.state_data["stuck_reason"] = ""
        ctx.state_data["state"] = "RUNNING"
        ctx.save_state()

        ctx.context["iteration"] = next_iter
        ctx.context["step"] = target_step_idx
        ctx.context["retries"] = 0

        loop_header = ""
        if "loop_prompt" in self.step_spec:
            raw_lp = (
                " ".join(self.step_spec["loop_prompt"])
                if isinstance(self.step_spec["loop_prompt"], list)
                else str(self.step_spec["loop_prompt"])
            )
            loop_header = interpolate(raw_lp, ctx.context).strip()

        target_spec = (
            ctx.steps[target_step_idx]
            if 0 <= target_step_idx < len(ctx.steps)
            else self.step_spec
        )
        target_prompt = format_step_prompt(target_spec, ctx.context)
        combined_prompt = (
            f"{loop_header}\n\n{target_prompt}".strip()
            if loop_header
            else target_prompt
        )
        print(
            format_prompt_with_suffix(
                combined_prompt, ctx.active_continuation, "for next steps."
            )
        )
        return 0


class WorkflowStepFactory:
    """Factory that instantiates the appropriate polymorphic WorkflowStep for a step spec."""

    @staticmethod
    def create_step(step_spec: Dict[str, Any]) -> WorkflowStep:
        if _is_human_verify_step(step_spec):
            return HumanVerifyStep(step_spec)
        if "workflow" in step_spec:
            return SubWorkflowStep(step_spec)
        if not step_spec.get("verifier_cmd"):
            return PassiveStep(step_spec)
        if step_spec.get("loop_to_step") is not None:
            return LoopStep(step_spec)
        return AtomicVerifierStep(step_spec)
