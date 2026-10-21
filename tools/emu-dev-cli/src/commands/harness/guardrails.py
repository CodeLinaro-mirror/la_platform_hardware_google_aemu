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

"""Lifecycle hook handlers and deterministic guardrails for the AEMU Harness.

Implements Jetski Web & CLI lifecycle hooks:
  - PreToolUse (`pre-tool`): Enforces Semantic Commit Messages on direct `git commit`
    invocations and `--notify=OWNER` on Gerrit mutations.
  - PostToolUse (`post-tool`): Tracks spawned/stopped emulator instances and
    workflow executions in the per-conversation Session Ledger.
  - PreInvocation (`pre-invocation`): Injects active `emu-dev-cli` workflow state
    and tracked emulator context as an `<ephemeralMessage>`.
  - SessionStart (`session-start`): Initializes the conversation ledger.
  - Stop (`stop`): Automatically continues active `RUNNING` workflows (bounded by
    `MAX_STOP_CONTINUATIONS`) and warns about leaked emulator instances.
"""

import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple
import yaml

from commands.harness.constants import (
    AVD_FLAG_PATTERN,
    EMU_LAUNCH_CMD_PATTERN,
    EMU_TEARDOWN_CMD_PATTERN,
    GERRIT_COMMENTS_PUBLISH_PATTERN,
    GERRIT_UNSAFE_CMD_PATTERN,
    GIT_COMMIT_AMEND_PATTERN,
    GIT_COMMIT_CMD_PATTERN,
    GIT_COMMIT_FILE_FLAG_PATTERN,
    GIT_COMMIT_MSG_FLAG_PATTERN,
    GIT_COMMIT_NO_EDIT_PATTERN,
    GOB_CURL_REVIEW_POST_PATTERN,
    HookDecision,
    HookEvent,
    LedgerEventKind,
    MAX_STOP_CONTINUATIONS,
    NOTIFY_OWNER_FLAG_PATTERN,
    NOTIFY_OWNER_JSON_PATTERN,
    WORKFLOW_STATE_FLAG_PATTERN,
)
from commands.harness.ledger import SessionLedgerStore


def _load_commit_verifier():
    """Dynamically loads commit_verifier from workflows/common/git_commit."""
    try:
        from workflows.common.git_commit import commit_verifier

        return commit_verifier
    except ImportError:
        repo_root = Path(__file__).resolve().parents[3]
        wf_parent = str(repo_root)
        if wf_parent not in sys.path:
            sys.path.append(wf_parent)
        from workflows.common.git_commit import commit_verifier

        return commit_verifier


def _extract_command_line(payload: Dict[str, Any]) -> str:
    """Extracts the shell command string from a Jetski PreToolUse/PostToolUse payload."""
    tool_call = payload.get("toolCall") or payload.get("tool_call") or {}
    args = tool_call.get("args") or payload.get("tool_input") or {}
    for key in ("CommandLine", "command", "cmd"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _extract_tool_name(payload: Dict[str, Any]) -> str:
    """Extracts the tool name from a Jetski hook payload."""
    tool_call = payload.get("toolCall") or payload.get("tool_call") or {}
    name = tool_call.get("name") or payload.get("tool_name") or ""
    return str(name).strip()


def evaluate_git_commit_command(command: str) -> Optional[Tuple[bool, str]]:
    """Checks a shell command for direct `git commit` compliance with Semantic Commit rules.

    Returns:
        None if the command is not a `git commit` invocation, or
        (is_allowed, explanation) if it is a `git commit` command.
    """
    if not GIT_COMMIT_CMD_PATTERN.search(command):
        return None

    has_amend = bool(GIT_COMMIT_AMEND_PATTERN.search(command))
    has_no_edit = bool(GIT_COMMIT_NO_EDIT_PATTERN.search(command))
    file_match = GIT_COMMIT_FILE_FLAG_PATTERN.search(command)
    msg_matches = GIT_COMMIT_MSG_FLAG_PATTERN.findall(command)

    if has_amend and has_no_edit and not file_match and not msg_matches:
        return True, "Allowed git commit --amend --no-edit"

    verifier = _load_commit_verifier()

    if file_match:
        file_path_str = file_match.group(1) or file_match.group(2) or file_match.group(3)
        errors = verifier.validate_commit_message_file(Path(file_path_str))
        if errors:
            bullet_errors = "\n".join(f"  - {err}" for err in errors)
            return (
                False,
                "Blocked by AEMU Harness Semantic Commit Guardrail:\n"
                f"{bullet_errors}\n"
                "Tip: Format the commit message as '<type>[optional scope]: <Description>' "
                "with an explanatory body and 'Bug:' / 'Test:' footers, or use "
                "'emu-dev-cli workflow create-commit' / 'emu-dev-cli workflow amend-commit'.",
            )
        return True, f"Verified Semantic Commit message file '{file_path_str}'"

    if msg_matches:
        paragraphs = [m[0] or m[1] or m[2] for m in msg_matches]
        combined_msg = "\n\n".join(paragraphs).replace("\\n", "\n")
        errors = verifier.validate_commit_message(combined_msg)
        if errors:
            bullet_errors = "\n".join(f"  - {err}" for err in errors)
            return (
                False,
                "Blocked by AEMU Harness Semantic Commit Guardrail:\n"
                f"{bullet_errors}\n"
                "Tip: Provide a multi-line message (subject, explanatory body, Bug:, Test:) "
                "or run 'emu-dev-cli workflow create-commit' / 'emu-dev-cli workflow amend-commit'.",
            )
        return True, "Verified inline Semantic Commit message"

    return None


def evaluate_gerrit_command(command: str) -> Optional[Tuple[bool, str]]:
    """Checks a shell command for Gerrit notification safety (`--notify=OWNER`).

    Returns:
        None if the command is not a Gerrit mutation, or
        (is_allowed, explanation) if it is a Gerrit mutation.
    """
    if GERRIT_UNSAFE_CMD_PATTERN.search(command):
        return (
            False,
            "Blocked by AEMU Harness Gerrit Guardrail: 'gerrit vote' and 'gerrit messages post' "
            "do not support '--notify=OWNER' and trigger GMR Result: 6 email rejections "
            "(b/567559614). Use 'emu-dev-cli gerrit reply' or 'gob-curl' with "
            "'\"notify\": \"OWNER\"' instead.",
        )

    if GERRIT_COMMENTS_PUBLISH_PATTERN.search(command):
        if not NOTIFY_OWNER_FLAG_PATTERN.search(command):
            return (
                False,
                "Blocked by AEMU Harness Gerrit Guardrail: 'gerrit comments publish' must "
                "include '--notify=OWNER' so bot service accounts do not cause GMR email "
                "rejections (b/567559614).",
            )
        return True, "Verified '--notify=OWNER' on gerrit comments publish"

    if GOB_CURL_REVIEW_POST_PATTERN.search(command):
        is_post = any(flag in command for flag in ("-X POST", "-XPOST", "-d ", "--data"))
        if is_post and not NOTIFY_OWNER_JSON_PATTERN.search(command):
            return (
                False,
                "Blocked by AEMU Harness Gerrit Guardrail: 'gob-curl' POST to Gerrit /review "
                "must include '\"notify\": \"OWNER\"' in the top-level JSON payload (b/567559614).",
            )
        if is_post:
            return True, "Verified '\"notify\": \"OWNER\"' on gob-curl review POST"

    return None


def get_workflow_state_root() -> Path:
    """Returns the root directory where `emu-dev-cli workflow` stores state files."""
    env_override = os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR")
    if env_override:
        return Path(env_override)
    try:
        from workflows.core.yaml_runner import get_state_base_dir

        return Path(get_state_base_dir())
    except Exception:
        return Path.home() / ".cache" / "emu-dev-cli" / "workflows" / "state"


def discover_active_workflows(
    conversation_id: Optional[str] = None,
    state_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Discovers active (`INIT`, `RUNNING`, `RETRY`, `WAITING_HUMAN`) workflow sessions."""
    root = state_root or get_workflow_state_root()
    if not root.is_dir():
        return []

    active_statuses = {"INIT", "RUNNING", "RETRY", "WAITING_HUMAN", "STUCK"}
    results: List[Dict[str, Any]] = []

    state_files = sorted(
        root.rglob("STATE-*.yaml"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    for state_file in state_files:
        try:
            data = yaml.safe_load(state_file.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if not isinstance(data, dict):
            continue

        meta = data.get("metadata") or {}
        if meta.get("_parent_state_id"):
            # Skip child sub-workflows; surface the top-level parent workflow.
            continue

        status = str(data.get("status", "")).upper()
        if status not in active_statuses:
            continue

        wf_conv = str(meta.get("conversation_id") or "")
        if conversation_id and wf_conv and wf_conv != conversation_id:
            continue

        results.append({
            "workflow_name": data.get("workflow_name") or state_file.parent.name,
            "state_id": data.get("state_id") or state_file.stem.replace("STATE-", ""),
            "status": status,
            "current_step": int(data.get("current_step", 0)),
            "retries": int(data.get("retries", 0)),
            "conversation_id": wf_conv,
            "updated_at": data.get("updated_at", ""),
            "state_file": str(state_file),
        })
    return results


def handle_pre_tool_hook(
    payload: Dict[str, Any],
    store: Optional[SessionLedgerStore] = None,
) -> Dict[str, Any]:
    """Handles Jetski `PreToolUse` hook events."""
    ledger_store = store or SessionLedgerStore()
    conversation_id = payload.get("conversationId") or payload.get("conversation_id")
    tool_name = _extract_tool_name(payload)
    if tool_name and tool_name not in ("run_command", "run_shell_command", "bash"):
        return {"decision": HookDecision.ALLOW.value}

    command = _extract_command_line(payload)
    if not command:
        return {"decision": HookDecision.ALLOW.value}

    for evaluator in (evaluate_git_commit_command, evaluate_gerrit_command):
        verdict = evaluator(command)
        if verdict is None:
            continue
        allowed, reason = verdict
        if not allowed:
            ledger_store.record_event(
                conversation_id,
                LedgerEventKind.GUARDRAIL_BLOCKED,
                reason.splitlines()[0],
                {"command": command, "reason": reason},
            )
            return {
                "decision": HookDecision.DENY.value,
                "reason": reason,
            }
        ledger_store.record_event(
            conversation_id,
            LedgerEventKind.GUARDRAIL_ALLOWED,
            reason,
            {"command": command},
        )

    return {"decision": HookDecision.ALLOW.value}


def handle_post_tool_hook(
    payload: Dict[str, Any],
    store: Optional[SessionLedgerStore] = None,
) -> Dict[str, Any]:
    """Handles Jetski `PostToolUse` hook events to track emulator and workflow state."""
    ledger_store = store or SessionLedgerStore()
    conversation_id = payload.get("conversationId") or payload.get("conversation_id")
    command = _extract_command_line(payload)
    if not command:
        return {}

    if EMU_LAUNCH_CMD_PATTERN.search(command) and "--dry-run" not in command:
        avd_match = AVD_FLAG_PATTERN.search(command)
        avd_name = avd_match.group(1) if avd_match else "default-avd"
        ledger_store.register_emulator_launch(conversation_id, avd_name, command)

    elif EMU_TEARDOWN_CMD_PATTERN.search(command):
        ledger_store.register_emulator_teardown(conversation_id, command)

    if "emu-dev-cli workflow" in command:
        state_match = WORKFLOW_STATE_FLAG_PATTERN.search(command)
        state_id = state_match.group(1) if state_match else None
        ledger_store.record_event(
            conversation_id,
            LedgerEventKind.WORKFLOW_UPDATED,
            f"Executed workflow command: {command[:80]}",
            {"command": command, "state_id": state_id},
        )

    return {}


def handle_session_start_hook(
    payload: Dict[str, Any],
    store: Optional[SessionLedgerStore] = None,
) -> Dict[str, Any]:
    """Handles Jetski `SessionStart` hook events."""
    ledger_store = store or SessionLedgerStore()
    conversation_id = payload.get("conversationId") or payload.get("conversation_id")
    ledger = ledger_store.load(conversation_id)
    ledger["stop_continuations"] = 0
    ledger_store.save(conversation_id, ledger)
    return {}


def handle_pre_invocation_hook(
    payload: Dict[str, Any],
    store: Optional[SessionLedgerStore] = None,
    state_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Handles Jetski `PreInvocation` hook events by injecting active workflow/emulator context."""
    ledger_store = store or SessionLedgerStore()
    conversation_id = payload.get("conversationId") or payload.get("conversation_id")
    ledger = ledger_store.load(conversation_id)

    active_workflows = discover_active_workflows(
        conversation_id=conversation_id,
        state_root=state_root,
    )
    active_emulators = ledger.get("active_emulators", [])

    if not active_workflows and not active_emulators:
        return {}

    lines: List[str] = ["[AEMU Custom Agent Harness Context]"]
    if active_workflows:
        lines.append("Active emu-dev-cli Workflows:")
        for wf in active_workflows[:3]:
            wf_name = wf["workflow_name"]
            state_id = wf["state_id"]
            status = wf["status"]
            step = wf["current_step"]
            lines.append(
                f"  - {wf_name} (state={state_id}, status={status}, step={step}): "
                f"resume via `emu-dev-cli workflow {wf_name} run --state {state_id}`"
            )

    if active_emulators:
        avd_list = ", ".join(str(e.get("avd", "unknown")) for e in active_emulators)
        lines.append(
            f"Tracked Running Emulator Instances: {avd_list} "
            "(remember to shut down spawned emulators when testing finishes)."
        )

    return {
        "injectSteps": [
            {
                "ephemeralMessage": "\n".join(lines),
            }
        ]
    }


def handle_stop_hook(
    payload: Dict[str, Any],
    store: Optional[SessionLedgerStore] = None,
    state_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Handles Jetski `Stop` hook events.

    If a bound `emu-dev-cli` workflow is still in `RUNNING` or `RETRY` state for this
    conversation, automatically requests continuation up to `MAX_STOP_CONTINUATIONS` times.
    """
    ledger_store = store or SessionLedgerStore()
    conversation_id = payload.get("conversationId") or payload.get("conversation_id")
    ledger = ledger_store.load(conversation_id)

    active_workflows = discover_active_workflows(
        conversation_id=conversation_id,
        state_root=state_root,
    )
    running_workflows = [
        wf
        for wf in active_workflows
        if wf.get("status") in ("RUNNING", "RETRY")
        and (not conversation_id or wf.get("conversation_id") == conversation_id)
    ]

    continuations = int(ledger.get("stop_continuations", 0))
    if running_workflows and continuations < MAX_STOP_CONTINUATIONS:
        wf = running_workflows[0]
        ledger["stop_continuations"] = continuations + 1
        ledger_store.save(conversation_id, ledger)
        reason = (
            f"Active emu-dev-cli workflow '{wf['workflow_name']}' (state {wf['state_id']}) "
            f"is still {wf['status']} at step {wf['current_step']} "
            f"(auto-continuation {continuations + 1}/{MAX_STOP_CONTINUATIONS}). "
            f"Run `emu-dev-cli workflow {wf['workflow_name']} run --state {wf['state_id']}` "
            "to verify and advance the workflow."
        )
        ledger_store.record_event(
            conversation_id,
            LedgerEventKind.STOP_CONTINUED,
            reason,
            {"workflow": wf},
        )
        return {
            "decision": HookDecision.CONTINUE.value,
            "reason": reason,
        }

    # Reset continuation counter once the agent stops cleanly
    if continuations > 0:
        ledger["stop_continuations"] = 0
        ledger_store.save(conversation_id, ledger)

    return {"decision": HookDecision.STOP.value}


def dispatch_hook_event(
    event: str,
    payload: Dict[str, Any],
    store: Optional[SessionLedgerStore] = None,
    state_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Dispatches a Jetski hook event name to its corresponding handler."""
    normalized = event.strip().lower()
    if normalized == HookEvent.PRE_TOOL.value:
        return handle_pre_tool_hook(payload, store=store)
    if normalized == HookEvent.POST_TOOL.value:
        return handle_post_tool_hook(payload, store=store)
    if normalized == HookEvent.PRE_INVOCATION.value:
        return handle_pre_invocation_hook(payload, store=store, state_root=state_root)
    if normalized == HookEvent.SESSION_START.value:
        return handle_session_start_hook(payload, store=store)
    if normalized == HookEvent.STOP.value:
        return handle_stop_hook(payload, store=store, state_root=state_root)
    return {}
