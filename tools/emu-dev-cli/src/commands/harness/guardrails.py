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
    workflow executions in the per-conversation Session Ledger, appends failure
    diagnostics, warns when a goldfish commit or upload changes
    concurrency-sensitive code without touching a test, and appends non-blocking
    `[AEMU Harness Code Advisory]` suggestions to applied file edits.
  - PreInvocation (`pre-invocation`): Injects active `emu-dev-cli` workflow state
    and tracked emulator context as an `<ephemeralMessage>`.
  - SessionStart (`session-start`): Initializes the conversation ledger.
  - Stop (`stop`): Automatically continues active `RUNNING` workflows (bounded by
    `MAX_STOP_CONTINUATIONS`) and warns about leaked emulator instances.
"""

import os
from pathlib import Path
import re
import string
import sys
from typing import Any, Dict, List, Optional, Tuple
try:
    import yaml
except ImportError:
    yaml = None

from commands import docs
from commands.harness.code_guardrails import (
    build_file_edit_advisory,
    evaluate_file_edit_payload,
    find_goldfish_repo_root,
)
from commands.harness.concurrency_test_check import build_concurrency_test_warning
from commands.harness.constants import (
    AVD_FLAG_PATTERN,
    BAZEL_TEST_CMD_PATTERN,
    BAZEL_TEST_OUTPUT_FLAG_PATTERN,
    EMU_LAUNCH_CMD_PATTERN,
    EMU_TEARDOWN_CMD_PATTERN,
    GERRIT_COMMENTS_PUBLISH_PATTERN,
    GERRIT_UNSAFE_CMD_PATTERN,
    GIT_COMMIT_AMEND_PATTERN,
    GIT_COMMIT_FILE_FLAG_PATTERN,
    GIT_COMMIT_MSG_FLAG_PATTERN,
    GIT_COMMIT_NO_EDIT_PATTERN,
    GIT_COMMIT_SEGMENT_PATTERN,
    GOB_CURL_REVIEW_POST_PATTERN,
    HookDecision,
    HookEvent,
    LedgerEventKind,
    MAX_STOP_CONTINUATIONS,
    NOTIFY_OWNER_FLAG_PATTERN,
    NOTIFY_OWNER_JSON_PATTERN,
    SHELL_ASSIGNMENT_PATTERN,
    SHELL_WRITE_TARGET_PATTERN,
    WORKFLOW_STATE_FLAG_PATTERN,
)
from commands.harness.diagnostics import enrich_post_tool_result
from commands.harness.ledger import SessionLedgerStore
from commands.harness.shell_segments import find_command_segment
from commands.harness.upload_guardrails import (
    evaluate_upload_command,
    first_regex_group,
    resolve_command_cwd,
)

# Jetski tools that write or edit files; code guardrails and advisories apply to them.
FILE_EDIT_TOOL_NAMES = frozenset({
    "write_file",
    "edit_file",
    "replace_file_content",
    "multi_replace_file_content",
    "write_to_file",
})


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


def _extract_workspace_dir(payload: Dict[str, Any]) -> Optional[Path]:
    """Extracts the active workspace directory from a Jetski hook payload."""
    tool_call = payload.get("toolCall") or payload.get("tool_call") or {}
    args = tool_call.get("args") or payload.get("tool_input") or {}
    for key in ("Cwd", "cwd"):
        arg_val = args.get(key)
        if isinstance(arg_val, str) and arg_val.strip():
            return Path(arg_val.strip()).expanduser()

    ws_paths = payload.get("workspacePaths") or payload.get("workspace_paths")
    if isinstance(ws_paths, list):
        for entry in ws_paths:
            if isinstance(entry, str) and entry.strip():
                cand = Path(entry.strip()).expanduser()
                if cand.exists():
                    return cand
        if ws_paths and isinstance(ws_paths[0], str) and ws_paths[0].strip():
            return Path(ws_paths[0].strip()).expanduser()

    for key in ("cwd", "workspacePath", "workspace_path"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return Path(val.strip()).expanduser()
    return None


def _unescape_inline_commit_paragraph(paragraph: str) -> str:
    """Unescapes shell-escaped quotes and newlines from inline `-m` arguments."""
    return (
        paragraph.replace("\\n", "\n")
        .replace("\\'", "'")
        .replace('\\"', '"')
    )


_GIT_DIR_FLAG_RE = re.compile(
    r'(?:git\s+-C\s+|cd\s+)(?:"([^"]+)"|\'([^\']+)\'|([^\s;&|]+))'
)


def _evaluate_goldfish_doc_drift_for_commit(
    command: str,
) -> Optional[Tuple[bool, str]]:
    """Checks for AGENTS.md / ARCHITECTURE.md drift when committing inside goldfish."""
    match = _GIT_DIR_FLAG_RE.search(command)
    if not match:
        return None
    raw_dir = match.group(1) or match.group(2) or match.group(3) or ""
    if not raw_dir:
        return None
    target_dir = Path(raw_dir)
    if not target_dir.is_dir():
        return None

    goldfish_root = find_goldfish_repo_root(target_dir)
    if goldfish_root is None:
        return None

    scan_root = goldfish_root
    if (
        goldfish_root.parent.name == "generic"
        and goldfish_root.parent.parent.name == "hardware"
    ):
        scan_root = goldfish_root.parent.parent.parent

    report = docs.lint_wiki_documents([str(scan_root)])
    goldfish_issues = [
        iss
        for iss in report.get("issues", [])
        if "goldfish" in iss.get("file", "") or scan_root == goldfish_root
    ]
    if goldfish_issues:
        bullets = "\n".join(
            f"  - [{iss['kind']}] {iss['file']}: {iss['message']}"
            for iss in goldfish_issues[:5]
        )
        return (
            False,
            "Blocked by AEMU Harness Documentation Drift Guardrail:\n"
            f"{bullets}\n"
            "Tip: Update out-of-sync AGENTS.md / ARCHITECTURE.md references or run "
            "'emu-dev-cli docs lint --fix' before committing.",
        )
    return None


_SEMANTIC_COMMIT_BLOCK_PREFIX = "Blocked by AEMU Harness Semantic Commit Guardrail"


def _expand_shell_word(word: str, command: str) -> Optional[str]:
    """Expands `$VAR` / `${VAR}` and `~` in `word` the way the shell would.

    Variables come from assignments in the same command line, which take precedence,
    or from the environment. Returns None if a substitution remains unresolved.
    """
    assignments = {
        name: value.strip("\"'") for name, value in SHELL_ASSIGNMENT_PATTERN.findall(command)
    }
    expanded = os.path.expanduser(
        string.Template(word).safe_substitute(os.environ, **assignments)
    )
    if "$" in expanded or "`" in expanded:
        return None
    return expanded


def _resolve_command_path(
    raw_path: str,
    command: str,
    git_c_text: str,
    default_cwd: Optional[Path],
) -> Optional[Path]:
    """Resolves a path argument of `command` to the absolute path the shell and git use.

    Relative paths resolve against the working directory, a preceding `cd`, and any
    `git -C` in `git_c_text`. Returns None if a shell variable cannot be resolved, so
    the caller can skip validation instead of blocking on a path it cannot see.
    """
    expanded = _expand_shell_word(raw_path, command)
    if expanded is None:
        return None
    return (resolve_command_cwd(command, git_c_text, default_cwd) / expanded).resolve()


def _is_written_by_command(
    message_path: Path,
    command: str,
    default_cwd: Optional[Path],
) -> bool:
    """Returns True if `command` redirects or tees output into `message_path`."""
    for match in SHELL_WRITE_TARGET_PATTERN.finditer(command):
        target = first_regex_group(match)
        if target and _resolve_command_path(target, command, "", default_cwd) == message_path:
            return True
    return False


def _find_git_commit_segment(command: str) -> Optional[str]:
    """Returns the first top-level `git commit` segment of `command`, or None."""
    found = find_command_segment(command, GIT_COMMIT_SEGMENT_PATTERN)
    return None if found is None else found[1]


def _evaluate_commit_message_file(
    raw_path: str,
    command: str,
    commit_segment: str,
    default_cwd: Optional[Path],
    verifier: Any,
) -> Tuple[bool, str]:
    """Validates the `git commit -F` message file as git will read it.

    Skips stdin (`-F -`) and paths with unresolvable shell variables. Blocks files that
    the same command writes, because the PreToolUse hook runs before the command and
    would otherwise validate a stale or missing file.
    """
    if raw_path == "-":
        return True, "Skipped Semantic Commit validation: message is read from stdin (-F -)"
    message_path = _resolve_command_path(raw_path, command, commit_segment, default_cwd)
    if message_path is None:
        return (
            True,
            "Skipped Semantic Commit validation: could not resolve shell variables in "
            f"commit message path '{raw_path}'",
        )
    if _is_written_by_command(message_path, command, default_cwd):
        return (
            False,
            f"{_SEMANTIC_COMMIT_BLOCK_PREFIX}: commit message file '{message_path}' (from -F "
            f"'{raw_path}') is written by this same command, so the PreToolUse hook cannot "
            "validate it before git reads it. Write the file in a separate command first, "
            f"then run 'git commit -F {raw_path}' on its own.",
        )
    if not message_path.is_file():
        return (
            False,
            f"{_SEMANTIC_COMMIT_BLOCK_PREFIX}: commit message file '{message_path}' (from -F "
            f"'{raw_path}') does not exist when the PreToolUse hook runs. Create it in a "
            "separate command before running 'git commit -F', or use "
            "'emu-dev-cli workflow create-commit'.",
        )
    errors = verifier.validate_commit_message_file(message_path)
    if errors:
        bullet_errors = "\n".join(f"  - {err}" for err in errors)
        return (
            False,
            f"{_SEMANTIC_COMMIT_BLOCK_PREFIX}:\n"
            f"{bullet_errors}\n"
            "Tip: Format the commit message as '<type>[optional scope]: <Description>' "
            "with an explanatory body and 'Bug:' / 'Test:' footers, or use "
            "'emu-dev-cli workflow create-commit' / 'emu-dev-cli workflow amend-commit'.",
        )
    return True, f"Verified Semantic Commit message file '{raw_path}'"


def evaluate_git_commit_command(
    command: str,
    default_cwd: Optional[Path] = None,
) -> Optional[Tuple[bool, str]]:
    """Checks a shell command for direct `git commit` compliance with Semantic Commit rules.

    Only a top-level `git commit` segment counts, so quoted mentions (for example in
    a `grep` pattern), heredoc bodies, and comments are ignored, and flags of other
    segments (such as `grep -m 5`) are not mistaken for commit options.

    Returns:
        None if the command is not a `git commit` invocation, or
        (is_allowed, explanation) if it is a `git commit` command.
    """
    commit_segment = _find_git_commit_segment(command)
    if commit_segment is None:
        return None

    drift_verdict = _evaluate_goldfish_doc_drift_for_commit(command)
    if drift_verdict is not None and not drift_verdict[0]:
        return drift_verdict

    has_amend = bool(GIT_COMMIT_AMEND_PATTERN.search(commit_segment))
    has_no_edit = bool(GIT_COMMIT_NO_EDIT_PATTERN.search(commit_segment))
    file_match = GIT_COMMIT_FILE_FLAG_PATTERN.search(commit_segment)
    msg_matches = GIT_COMMIT_MSG_FLAG_PATTERN.findall(commit_segment)

    if has_amend and has_no_edit and not file_match and not msg_matches:
        return True, "Allowed git commit --amend --no-edit"

    verifier = _load_commit_verifier()

    if file_match:
        return _evaluate_commit_message_file(
            first_regex_group(file_match) or "",
            command,
            commit_segment,
            default_cwd,
            verifier,
        )

    if msg_matches:
        paragraphs = [
            _unescape_inline_commit_paragraph(m[0] or m[1] or m[2] or m[3])
            for m in msg_matches
        ]
        combined_msg = "\n\n".join(paragraphs)
        errors = verifier.validate_commit_message(combined_msg)
        if errors:
            bullet_errors = "\n".join(f"  - {err}" for err in errors)
            return (
                False,
                f"{_SEMANTIC_COMMIT_BLOCK_PREFIX}:\n"
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


def _inject_bazel_test_output_flag(command: str) -> Optional[str]:
    """Adds `--test_output=errors` after the first top-level `bazel test`, if any."""
    if BAZEL_TEST_OUTPUT_FLAG_PATTERN.search(command):
        return None
    found = find_command_segment(command, BAZEL_TEST_CMD_PATTERN)
    if found is None:
        return None
    start, _, match = found
    insert_at = start + match.end()
    return f"{command[:insert_at]} --test_output=errors{command[insert_at:]}"


def rewrite_command_if_needed(command: str) -> Optional[Tuple[str, str]]:
    """Transparently rewrites shell commands via PreToolUse `overwrite` when safe.

    Returns:
        None if no rewrite applies, or (rewritten_command, reason).
    """
    if GERRIT_COMMENTS_PUBLISH_PATTERN.search(command) and "--notify" not in command:
        rewritten = GERRIT_COMMENTS_PUBLISH_PATTERN.sub(
            "gerrit comments publish --notify=OWNER",
            command,
            count=1,
        )
        return (
            rewritten,
            "Auto-injected '--notify=OWNER' on gerrit comments publish (b/567559614)",
        )

    rewritten_bazel = _inject_bazel_test_output_flag(command)
    if rewritten_bazel is not None:
        return (
            rewritten_bazel,
            "Auto-injected '--test_output=errors' on bazel test for immediate failure diagnostics",
        )

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
    if yaml is None or not root.is_dir():
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

    if tool_name in FILE_EDIT_TOOL_NAMES:
        verdict = evaluate_file_edit_payload(payload)
        if verdict is not None:
            allowed, reason = verdict
            if not allowed:
                ledger_store.record_event(
                    conversation_id,
                    LedgerEventKind.CODE_GUARDRAIL_BLOCKED,
                    reason.splitlines()[0],
                    {"tool": tool_name, "reason": reason},
                )
                return {
                    "decision": HookDecision.DENY.value,
                    "reason": reason,
                }
        return {"decision": HookDecision.ALLOW.value}

    if tool_name and tool_name not in ("run_command", "run_shell_command", "bash"):
        return {"decision": HookDecision.ALLOW.value}

    command = _extract_command_line(payload)
    if not command:
        return {"decision": HookDecision.ALLOW.value}

    rewrite_result = rewrite_command_if_needed(command)
    effective_command = rewrite_result[0] if rewrite_result else command

    default_cwd = _extract_workspace_dir(payload)
    for evaluator in (
        lambda cmd: evaluate_git_commit_command(cmd, default_cwd=default_cwd),
        evaluate_gerrit_command,
        lambda cmd: evaluate_upload_command(cmd, default_cwd=default_cwd),
    ):
        verdict = evaluator(effective_command)
        if verdict is None:
            continue
        allowed, reason = verdict
        if not allowed:
            ledger_store.record_event(
                conversation_id,
                LedgerEventKind.GUARDRAIL_BLOCKED,
                reason.splitlines()[0],
                {"command": effective_command, "reason": reason},
            )
            return {
                "decision": HookDecision.DENY.value,
                "reason": reason,
            }
        ledger_store.record_event(
            conversation_id,
            LedgerEventKind.GUARDRAIL_ALLOWED,
            reason,
            {"command": effective_command},
        )

    if rewrite_result is not None:
        rewritten_cmd, rewrite_reason = rewrite_result
        ledger_store.record_event(
            conversation_id,
            LedgerEventKind.COMMAND_REWRITTEN,
            rewrite_reason,
            {"original_command": command, "rewritten_command": rewritten_cmd},
        )
        return {
            "decision": HookDecision.ALLOW.value,
            "reason": rewrite_reason,
            "overwrite": {
                "CommandLine": rewritten_cmd,
            },
        }

    return {"decision": HookDecision.ALLOW.value}


def _append_file_edit_advisory(
    payload: Dict[str, Any],
    ledger_store: SessionLedgerStore,
    conversation_id: Optional[str],
    tool_name: str,
) -> Dict[str, Any]:
    """Appends non-blocking code advisories to the result of an applied file edit."""
    if str(payload.get("error") or "").strip():
        return {}
    advisory = build_file_edit_advisory(payload)
    if advisory is None:
        return {}
    ledger_store.record_event(
        conversation_id,
        LedgerEventKind.CODE_ADVISORY_ISSUED,
        advisory.splitlines()[1].strip()[:160],
        {"tool": tool_name, "advisory": advisory},
    )
    result = str(payload.get("result") or "")
    return {
        "overwriteResult": (
            f"{result.rstrip()}\n\n{advisory}\n" if result.strip() else f"{advisory}\n"
        )
    }


def handle_post_tool_hook(
    payload: Dict[str, Any],
    store: Optional[SessionLedgerStore] = None,
) -> Dict[str, Any]:
    """Handles Jetski `PostToolUse` hook events to track emulator and workflow state."""
    ledger_store = store or SessionLedgerStore()
    conversation_id = payload.get("conversationId") or payload.get("conversation_id")
    tool_name = _extract_tool_name(payload)
    if tool_name in FILE_EDIT_TOOL_NAMES:
        return _append_file_edit_advisory(payload, ledger_store, conversation_id, tool_name)

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

    overwritten_result: Optional[str] = None
    enriched = enrich_post_tool_result(command, payload)
    if enriched is not None:
        overwritten_result, diag_summary = enriched
        ledger_store.record_event(
            conversation_id,
            LedgerEventKind.DIAGNOSTIC_INJECTED,
            diag_summary,
            {"command": command},
        )

    concurrency_warning = build_concurrency_test_warning(
        command, payload, default_cwd=_extract_workspace_dir(payload)
    )
    if concurrency_warning is not None:
        base_result = (
            overwritten_result
            if overwritten_result is not None
            else str(payload.get("result") or "")
        )
        overwritten_result = (
            f"{base_result.rstrip()}\n\n{concurrency_warning}\n"
            if base_result.strip()
            else f"{concurrency_warning}\n"
        )
        ledger_store.record_event(
            conversation_id,
            LedgerEventKind.CONCURRENCY_TEST_WARNING,
            concurrency_warning.splitlines()[1].strip(),
            {"command": command},
        )

    if overwritten_result is not None:
        return {"overwriteResult": overwritten_result}
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


GRPC_PLAYBOOK_EPHEMERAL_CONTEXT: str = (
    "Goldfish gRPC Plugin Playbook (`emulator/plugin/grpc/AGENTS.md` & `//emulator/libs/grpc_utils`):\n"
    "  - Prefer the `//emulator/libs/grpc_utils` wrappers below over subclassing raw `grpc::ServerWriteReactor` / `grpc::ServerReadReactor`; deviate only when they cannot express the RPC.\n"
    "  - Server event/state streams (`//emulator/libs/grpc_utils:event-stream`, `#include \"android/emulation/control/grpc_event_stream_support.h\"`):\n"
    "    * `android::emulation::control::StateStreamWriter<T, Event, Source, max_q, recycle, Reactor>`: First choice when RPC streams current device state (sends initial snapshot via `populate_fn(T*)`, then fresh snapshots when `source` fires).\n"
    "    * `android::emulation::control::UniqueEventStreamWriter<T, Source>`: Deduplicates consecutive identical proto messages (`MessageDifferencer::Equals`).\n"
    "    * `android::emulation::control::GenericEventStreamWriter<T>`: Forwards discrete events without an initial snapshot.\n"
    "    * `android::emulation::control::BaseEventStreamWriter<T, Event, Source>`: Base class for custom event writers (call `this->Subscribe()` at the end of the subclass constructor).\n"
    "  - Client/Server async streams (`//emulator/libs/grpc_utils:async`, `#include \"android/emulation/control/simple_async_grpc.h\"`):\n"
    "    * `::SimpleServerLambdaReader<R>` / `::SimpleServerReader<R>`: First choice for client-streaming RPCs (`stream Req`).\n"
    "    * `::SimpleServerLambdaWriter<W>` / `::SimpleServerWriter<W>`: Callback-driven server stream without an `EventChangeSupport` source.\n"
    "    * `::ErrorServerWriter<W>`: Immediately finishes a server stream with a non-OK `grpc::Status` and self-deletes in `OnDone()`.\n"
    "  - Status translation (`//emulator/libs/grpc_utils:absl_translate`, `#include \"android/emulation/control/absl_status_translate.h\"`):\n"
    "    * Prefer `android::emulation::control::AbslStatusToGrpcStatus` and `GrpcStatusToAbslStatus` over hand-rolled `absl::Status` <-> `grpc::Status` switch blocks.\n"
    "  - Lock ordering & unit testing: Acquire `EventChangeSupport` listener mutex before `reactor_lock_`, never perform blocking I/O under `reactor_lock_`, and test streaming reactors hermetically with `FakeWriteReactor<T>` (`//emulator/plugin/grpc/services/v2/test:grpc_v2_service_test`)."
)


def _contains_grpc_plugin_reference(value: Any) -> bool:
    """Returns True if `value` (recursively) references `emulator/plugin/grpc`."""
    if isinstance(value, str):
        norm = value.replace("\\", "/")
        return "emulator/plugin/grpc" in norm
    if isinstance(value, dict):
        return any(_contains_grpc_plugin_reference(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_grpc_plugin_reference(item) for item in value)
    return False


def _should_inject_grpc_playbook_context(
    payload: Dict[str, Any],
    ledger: Dict[str, Any],
) -> bool:
    """Returns True when invocation payload or session ledger targets `emulator/plugin/grpc`."""
    if _contains_grpc_plugin_reference(payload):
        return True
    events = ledger.get("events")
    if isinstance(events, list) and _contains_grpc_plugin_reference(events[-10:]):
        return True
    return False


def handle_pre_invocation_hook(
    payload: Dict[str, Any],
    store: Optional[SessionLedgerStore] = None,
    state_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Handles Jetski `PreInvocation` hook events by injecting active workflow/emulator/gRPC context."""
    ledger_store = store or SessionLedgerStore()
    conversation_id = payload.get("conversationId") or payload.get("conversation_id")
    ledger = ledger_store.load(conversation_id)

    active_workflows = discover_active_workflows(
        conversation_id=conversation_id,
        state_root=state_root,
    )
    active_emulators = ledger.get("active_emulators", [])
    inject_grpc_playbook = _should_inject_grpc_playbook_context(payload, ledger)

    if not active_workflows and not active_emulators and not inject_grpc_playbook:
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

    if inject_grpc_playbook:
        lines.append(GRPC_PLAYBOOK_EPHEMERAL_CONTEXT)

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
