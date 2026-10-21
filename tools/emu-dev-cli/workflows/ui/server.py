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

"""HTTP Server and API backend for the Workflow Visualizer Web UI."""

from datetime import datetime, timezone
import http.server
import io
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import webbrowser

_CUR_DIR = Path(__file__).resolve().parent
_WORKFLOWS_DIR = _CUR_DIR.parent
_CORE_DIR = _WORKFLOWS_DIR / "core"
_EMU_DEV_CLI_DIR = _WORKFLOWS_DIR.parent
_SRC_DIR = _EMU_DEV_CLI_DIR / "src"

for d in (str(_SRC_DIR), str(_EMU_DEV_CLI_DIR), str(_WORKFLOWS_DIR)):
    if d not in sys.path:
        sys.path.insert(0, d)

import shutil
import sqlite3
import subprocess
import time
import uuid

try:
    from workflows.core.yaml_runner import (
        _parse_args_spec,
        approve_workflow_session,
        execute_workflow_step,
        format_step_prompt,
        get_state_base_dir,
        init_workflow_session,
        interpolate,
        load_state_yaml,
        load_yaml,
        resolve_workflow_file,
        save_state_yaml,
        StateYamlDumper,
    )
except ImportError:
    from core.yaml_runner import (
        _parse_args_spec,
        approve_workflow_session,
        execute_workflow_step,
        format_step_prompt,
        get_state_base_dir,
        init_workflow_session,
        interpolate,
        load_state_yaml,
        load_yaml,
        resolve_workflow_file,
        save_state_yaml,
        StateYamlDumper,
    )

import yaml

try:
    from commands.workflow import find_workflow_target, list_available_workflows
except ImportError:
    def list_available_workflows():
        return {}
    def find_workflow_target(name):
        return None


def get_ui_html_path() -> Path:
    """Returns the path to index.html."""
    candidates = [
        _CUR_DIR / "index.html",
        _CUR_DIR / "static" / "index.html",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return _CUR_DIR / "index.html"


def resolve_jetski_cli() -> Optional[str]:
    """Resolves the executable path to standalone jetski_cli / jetski binary."""
    env_bin = os.environ.get("JETSKI_CLI_BIN")
    if env_bin and os.path.exists(env_bin) and os.access(env_bin, os.X_OK):
        return env_bin
    for name in ("jetski_cli", "jetski"):
        found = shutil.which(name)
        if found:
            return found
    paths = [
        "/google/bin/releases/jetski-devs/tools/cli",
        "/google/bin/releases/gemini-cli/tools/gemini",
    ]
    for path in paths:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    return shutil.which("gemini")


def get_workflow_catalog() -> List[Dict[str, Any]]:
    """Returns enriched workflow catalog with argument specs for the UI builder."""
    wfs = list_available_workflows()
    result = []
    for name, info in sorted(wfs.items(), key=lambda x: x[0]):
        if name == "custom-workflow":
            continue
        target_path = info.get("target_path")
        args_spec = []
        is_barrier = name == "human-verify"
        if target_path and os.path.isfile(target_path) and target_path.endswith((".yaml", ".yml")):
            try:
                spec = load_yaml(target_path)
                raw_args = spec.get("args") or spec.get("init", {}).get("args") or []
                args_spec = _parse_args_spec(raw_args)
                for s in spec.get("steps", []):
                    if s.get("human_verify"):
                        is_barrier = True
            except Exception:
                pass
        result.append({
            "name": name,
            "description": info.get("description", ""),
            "target_type": info.get("target_type", "yaml"),
            "args": args_spec,
            "is_barrier": is_barrier,
        })
    return result


def get_known_source_directories() -> List[Dict[str, Any]]:
    """Returns known source directories from ~/.android/emu-dev-cli.json (or EMU_DEV_CLI_CONFIG_PATH)."""
    cfg_path = os.environ.get(
        "EMU_DEV_CLI_CONFIG_PATH",
        os.path.expanduser("~/.android/emu-dev-cli.json"),
    )
    source_dirs: Dict[str, str] = {}
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("source_directories"), dict):
                    source_dirs = data["source_directories"]
        except Exception:
            pass

    if not source_dirs:
        try:
            from commands.source_directory import load_config as load_src_config
            cfg = load_src_config()
            if isinstance(cfg.get("source_directories"), dict):
                source_dirs = cfg["source_directories"]
        except Exception:
            pass

    cwd_resolved = Path(os.getcwd()).resolve()
    items: List[Dict[str, Any]] = []
    for branch, raw_path in source_dirs.items():
        if not raw_path or not str(raw_path).strip():
            continue
        norm_path = os.path.abspath(os.path.expanduser(str(raw_path).strip()))
        is_current = False
        try:
            cwd_resolved.relative_to(Path(norm_path).resolve())
            is_current = True
        except ValueError:
            pass
        items.append({
            "branch": branch,
            "path": norm_path,
            "exists": os.path.isdir(norm_path),
            "is_current": is_current,
        })

    if not items:
        items.append({
            "branch": "current",
            "path": str(cwd_resolved),
            "exists": True,
            "is_current": True,
        })

    return items


# In-memory registry of background jetski_cli processes spawned by this UI server
_ACTIVE_AGENT_PROCS: Dict[str, subprocess.Popen] = {}
_STARTING_AGENTS: set = set()
_ACTIVE_CONV_PROCS: Dict[str, subprocess.Popen] = {}
_STARTING_CONVS: set = set()


def _is_conv_agent_alive(conversation_id: str) -> bool:
    """Determines if a background jetski_cli process for conversation_id is actively running."""
    if not conversation_id:
        return False
    if conversation_id in _STARTING_CONVS:
        return True
    proc = _ACTIVE_CONV_PROCS.get(conversation_id)
    if proc is not None:
        if proc.poll() is None:
            return True
        _ACTIVE_CONV_PROCS.pop(conversation_id, None)
    return False


def _is_agent_pid_alive(pid: Any) -> bool:
    """Checks whether a background jetski_cli stream-json PID is still running."""
    try:
        pid_int = int(pid)
        if pid_int <= 0:
            return False
    except Exception:
        return False

    proc_cmdline = Path(f"/proc/{pid_int}/cmdline")
    if proc_cmdline.is_file():
        try:
            raw = proc_cmdline.read_bytes().decode("utf-8", errors="replace")
            if "stream-json" in raw or "jetski" in raw or "/cli" in raw:
                return True
            return False
        except Exception:
            return False

    try:
        os.kill(pid_int, 0)
        return True
    except OSError:
        return False


def _is_session_agent_alive(state_id: str, metadata: Dict[str, Any]) -> bool:
    """Determines if a background jetski_cli process for state_id is actively running."""
    if state_id in _STARTING_AGENTS:
        return True

    proc = _ACTIVE_AGENT_PROCS.get(state_id)
    if proc is not None:
        if proc.poll() is None:
            return True
        _ACTIVE_AGENT_PROCS.pop(state_id, None)

    agent_pid = metadata.get("_agent_pid")
    if agent_pid and _is_agent_pid_alive(agent_pid):
        return True

    return False


def _inspect_transcript_interruption(conversation_id: Optional[str]) -> Tuple[bool, str]:
    """Inspects the tail of transcript.jsonl to see if the conversation stopped mid-turn."""
    if not conversation_id or not str(conversation_id).strip():
        return False, ""
    cid = str(conversation_id).strip()
    if "/" in cid or "\\" in cid or ".." in cid:
        return False, ""

    t_path = get_jetski_brain_dir() / cid / ".system_generated" / "logs" / "transcript.jsonl"
    if not t_path.is_file():
        return False, ""

    try:
        size = t_path.stat().st_size
        if size == 0:
            return False, ""
        with open(t_path, "rb") as f:
            seek_pos = max(0, size - 65536)
            f.seek(seek_pos)
            tail_text = f.read().decode("utf-8", errors="replace")
        lines = [ln.strip() for ln in tail_text.splitlines() if ln.strip()]
        if not lines:
            return False, ""
        last_obj = json.loads(lines[-1])
        if not isinstance(last_obj, dict):
            return False, ""

        step_idx = last_obj.get("step_index", "?")
        step_type = str(last_obj.get("type", "")).upper()
        step_status = str(last_obj.get("status", "")).upper()
        tool_calls = last_obj.get("tool_calls") or []

        if step_status in ("INTERRUPTED", "CANCELED", "CANCELLED", "ERROR", "FAILED"):
            return True, f"Conversation interrupted at step #{step_idx} ({step_type}: {step_status})."
        if step_status and step_status != "DONE":
            return True, f"Conversation stopped mid-step at step #{step_idx} ({step_type}: {step_status})."
        if step_type == "GENERIC":
            return (
                True,
                f"Conversation interrupted mid-turn after tool execution (step #{step_idx}) — waiting for agent continuation.",
            )
        if step_type == "PLANNER_RESPONSE" and isinstance(tool_calls, list) and len(tool_calls) > 0:
            tc_name = tool_calls[0].get("name", "tool") if isinstance(tool_calls[0], dict) else "tool"
            return True, f"Conversation interrupted during tool call '{tc_name}' (step #{step_idx})."
        if step_type == "USER_INPUT":
            return True, f"Conversation interrupted after prompt (step #{step_idx}) before agent responded."
    except Exception:
        return False, ""

    return False, ""


def resolve_live_agent_status(
    state_id: str,
    state_data: Dict[str, Any],
    state_file: Optional[Path] = None,
) -> Tuple[Optional[str], str]:
    """Computes the real-time agent execution status (RUNNING, INTERRUPTED, IDLE, etc.)."""
    if not isinstance(state_data, dict):
        return None, ""
    wf_state = str(state_data.get("state", "INIT")).upper()
    metadata = state_data.get("metadata")
    if not isinstance(metadata, dict):
        return None, ""

    raw_status = metadata.get("_agent_status")
    conv_id = metadata.get("conversation_id")

    if _is_session_agent_alive(state_id, metadata):
        return "RUNNING", "Agent process is actively running."

    # Background agent process is not running. Check if transcript is mid-turn / interrupted.
    is_interrupted, interrupt_reason = _inspect_transcript_interruption(conv_id)
    if is_interrupted:
        if raw_status == "RUNNING" and state_file and state_file.is_file():
            try:
                metadata["_agent_status"] = "INTERRUPTED"
                metadata.pop("_agent_pid", None)
                save_state_yaml(state_file, state_data)
            except Exception:
                pass
        return "INTERRUPTED", interrupt_reason

    # If state file still claims RUNNING but no background process is alive
    if raw_status == "RUNNING":
        if wf_state == "RUNNING":
            reason = "Agent process is no longer running while workflow is still active. Poke the agent to continue."
            if state_file and state_file.is_file():
                try:
                    metadata["_agent_status"] = "INTERRUPTED"
                    metadata.pop("_agent_pid", None)
                    save_state_yaml(state_file, state_data)
                except Exception:
                    pass
            return "INTERRUPTED", reason
        else:
            if state_file and state_file.is_file():
                try:
                    metadata["_agent_status"] = "IDLE"
                    metadata.pop("_agent_pid", None)
                    save_state_yaml(state_file, state_data)
                except Exception:
                    pass
            return "IDLE", ""

    if raw_status == "INTERRUPTED":
        return (
            "INTERRUPTED",
            interrupt_reason or "Agent conversation was interrupted. Poke the agent to continue.",
        )

    if isinstance(raw_status, str) and (raw_status.startswith("EXITED") or raw_status == "ERROR"):
        return (
            raw_status,
            interrupt_reason or f"Agent process exited ({raw_status}). Poke the agent to continue.",
        )

    if raw_status == "IDLE" and wf_state == "RUNNING":
        return "IDLE", "Agent finished its turn while workflow is still RUNNING. Poke the agent to continue."

    return raw_status, ""


def scan_all_sessions(
    state_base_dir: Optional[Path] = None,
    include_sub_workflows: bool = False,
) -> List[Dict[str, Any]]:
    """Scans state directory and returns summaries of workflow sessions.

    By default, excludes nested sub-workflows (child sessions) so only top-level
    parent workflows appear in the root session list.
    """
    base_dir = state_base_dir or get_state_base_dir()
    sessions = []

    if not base_dir.is_dir():
        return sessions

    available_wfs = list_available_workflows()

    # Pass 1: Parse all sessions, and collect child IDs from parent sessions
    raw_sessions = []
    child_ids = set()

    for wf_dir in base_dir.iterdir():
        if not wf_dir.is_dir() or wf_dir.name.startswith("."):
            continue
        wf_name = wf_dir.name

        for state_file in list(wf_dir.glob("STATE-*.yaml")) + list(wf_dir.glob("STATE-*.yml")):
            try:
                mtime = state_file.stat().st_mtime
                dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
                iso_time = dt.isoformat()

                state_id = state_file.stem.replace("STATE-", "")
                data = load_state_yaml(state_file)
                metadata = data.get("metadata", {})

                # Check if this session is marked with a call stack indicating sub-workflow
                call_stack = metadata.get("_call_stack", [])
                is_sub = isinstance(call_stack, list) and len(call_stack) > 1

                # Collect child IDs declared in parent
                sub_wf = metadata.get("_sub_workflow")
                if isinstance(sub_wf, dict) and sub_wf.get("state_id"):
                    child_ids.add(str(sub_wf["state_id"]))

                sub_history = metadata.get("_sub_workflow_history", [])
                if isinstance(sub_history, list):
                    for h in sub_history:
                        if isinstance(h, dict) and h.get("state_id"):
                            child_ids.add(str(h["state_id"]))

                raw_sessions.append((wf_name, state_id, state_file, data, metadata, mtime, iso_time, is_sub))
            except Exception:
                continue

    # Pass 2: Filter and build summaries
    for wf_name, state_id, state_file, data, metadata, mtime, iso_time, is_sub in raw_sessions:
        is_child = is_sub or (state_id in child_ids)
        if not include_sub_workflows and is_child:
            continue

        # Count total steps from workflow definition if available
        total_steps = 0
        custom_spec_path = metadata.get("_workflow_spec_path")
        if custom_spec_path and Path(custom_spec_path).is_file():
            try:
                spec = load_yaml(custom_spec_path)
                total_steps = len(spec.get("steps", []))
            except Exception:
                pass
        else:
            wf_def = available_wfs.get(wf_name)
            if wf_def and wf_def.get("target_path"):
                try:
                    spec = load_yaml(wf_def["target_path"])
                    total_steps = len(spec.get("steps", []))
                except Exception:
                    pass

        # Extract key user-facing arguments
        key_args = {}
        for k in ("bug-number", "bug_number", "repo", "joke-file"):
            if k in metadata and str(metadata[k]).strip():
                key_args[k] = metadata[k]

        sub_wf = metadata.get("_sub_workflow")
        sub_history = metadata.get("_sub_workflow_history", [])
        agent_status, agent_reason = resolve_live_agent_status(state_id, data, state_file)

        sessions.append({
            "workflow_name": wf_name,
            "custom_title": metadata.get("_custom_title", ""),
            "state_id": state_id,
            "state": data.get("state", "UNKNOWN"),
            "step": data.get("step", 0),
            "total_steps": total_steps,
            "retries": data.get("retries", 0),
            "stuck_reason": data.get("stuck_reason", ""),
            "mtime": mtime,
            "updated_at": iso_time,
            "key_args": key_args,
            "is_sub_workflow": is_child,
            "has_sub_workflow": bool(sub_wf),
            "sub_workflow_name": sub_wf.get("name") if isinstance(sub_wf, dict) else None,
            "sub_workflow_history_count": len(sub_history) if isinstance(sub_history, list) else 0,
            "conversation_id": metadata.get("conversation_id"),
            "agent_status": agent_status,
            "agent_status_reason": agent_reason,
        })

    # Sort most recent first
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


def extract_artifacts_from_metadata(
    metadata: Dict[str, Any],
    source_workflow: Optional[str] = None,
    seen_paths: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Recursively traverses a metadata dictionary to discover referenced files on disk."""
    if seen_paths is None:
        seen_paths = set()
    artifacts: List[Dict[str, Any]] = []

    def _walk(data: Any, prefix: str = "", current_source: Optional[str] = None):
        if isinstance(data, dict):
            for k, v in data.items():
                if k in ("_sub_workflow", "_sub_workflow_history", "_workflow_spec_path"):
                    continue
                next_prefix = f"{prefix}.{k}" if prefix else k
                # If entering a namespace dictionary (e.g. metadata["hello-world-example"])
                sub_source = current_source or (k if isinstance(v, dict) and not k.startswith("_") else None)
                _walk(v, next_prefix, sub_source)
        elif isinstance(data, (list, tuple)):
            for idx, item in enumerate(data):
                _walk(item, f"{prefix}[{idx}]", current_source)
        elif isinstance(data, str) and (data.startswith("/") or data.startswith("~")):
            p = Path(os.path.expanduser(data))
            if p.is_file():
                canonical_path = str(p.resolve())
                if canonical_path not in seen_paths:
                    seen_paths.add(canonical_path)
                    try:
                        sz = p.stat().st_size
                    except Exception:
                        sz = 0
                    artifacts.append({
                        "key": prefix,
                        "path": canonical_path,
                        "filename": p.name,
                        "size": sz,
                        "source_workflow": current_source or source_workflow,
                    })

    _walk(metadata, current_source=source_workflow)
    return artifacts


def get_session_detail(
    workflow_name: str,
    state_id: str,
    state_base_dir: Optional[Path] = None,
    visited: Optional[set] = None,
) -> Optional[Dict[str, Any]]:
    """Loads detailed state and specification for a specific workflow session."""
    if visited is None:
        visited = set()
    session_key = f"{workflow_name}:{state_id}"
    if session_key in visited:
        return None
    visited.add(session_key)

    base_dir = state_base_dir or get_state_base_dir()
    state_file = base_dir / workflow_name / f"STATE-{state_id}.yaml"
    if not state_file.is_file():
        # Fallback to .yml
        state_file = base_dir / workflow_name / f"STATE-{state_id}.yml"
        if not state_file.is_file():
            return None

    state_data = load_state_yaml(state_file)
    current_state = state_data.get("state", "INIT")
    current_step_idx = state_data.get("step", 0)
    metadata = state_data.get("metadata", {})

    # Load workflow specification (check custom session spec override first)
    custom_spec_path = metadata.get("_workflow_spec_path")
    if custom_spec_path and Path(custom_spec_path).is_file():
        wf_file = Path(custom_spec_path).resolve()
    else:
        wf_file = resolve_workflow_file(workflow_name)
    spec = load_yaml(wf_file) if wf_file else {}
    raw_steps = spec.get("steps", [])

    active_sub_wf = metadata.get("_sub_workflow", {})
    sub_history = metadata.get("_sub_workflow_history", [])

    steps_detail = []
    for idx, raw_s in enumerate(raw_steps):
        s_title = raw_s.get("title") or raw_s.get("name") or f"Step {idx}"
        s_desc = raw_s.get("description", "")

        # Determine step status
        if current_state == "DONE":
            status = "completed"
        elif current_state in ("STOPPED", "CANCELLED"):
            if idx < current_step_idx:
                status = "completed"
            elif idx == current_step_idx:
                status = "stopped"
            else:
                status = "pending"
        elif idx < current_step_idx:
            status = "completed"
        elif idx == current_step_idx:
            if current_state == "STUCK":
                status = "stuck"
            elif current_state == "WAITING_HUMAN":
                status = "waiting_human"
            elif current_state == "RUNNING":
                status = "running"
            elif current_state == "INIT":
                status = "init"
            else:
                status = current_state.lower()
        else:
            status = "pending"

        # Format prompt including custom_instructions
        interpolated_prompt = format_step_prompt(raw_s, metadata)

        step_custom = raw_s.get("custom_instructions", "")
        if not step_custom and isinstance(raw_s.get("workflow"), dict):
            step_custom = raw_s["workflow"].get("custom_instructions", "")

        step_info = {
            "index": idx,
            "title": s_title,
            "description": s_desc,
            "status": status,
            "prompt": interpolated_prompt,
            "custom_instructions": interpolate(str(step_custom), metadata) if step_custom else "",
            "human_verify": bool(raw_s.get("human_verify")),
            "verifier_cmd": raw_s.get("verifier_cmd"),
            "max_retries": raw_s.get("max_retries", 3),
            "is_sub_workflow": "workflow" in raw_s,
        }

        # Sub-workflow inspection
        if "workflow" in raw_s:
            sub_spec = raw_s["workflow"]
            if isinstance(sub_spec, str):
                child_name = sub_spec
                child_args = {}
                child_outputs = {}
            else:
                child_name = sub_spec.get("name")
                child_args = sub_spec.get("args", {})
                child_outputs = sub_spec.get("outputs", {})
            step_info["sub_workflow_spec"] = {
                "name": child_name,
                "args": child_args,
                "outputs": child_outputs,
            }
            if child_name == "human-verify":
                step_info["human_verify"] = True

            # Check if this sub-workflow is currently active
            if active_sub_wf and active_sub_wf.get("parent_step") == idx:
                child_id = active_sub_wf.get("state_id")
                step_info["active_child"] = get_session_detail(child_name, child_id, base_dir, visited.copy())

            # Check if archived in history
            matching_history = [h for h in sub_history if h.get("parent_step") == idx]
            if matching_history:
                step_info["child_history"] = []
                for h in matching_history:
                    c_id = h.get("state_id")
                    h_detail = get_session_detail(h.get("name", child_name), c_id, base_dir, visited.copy())
                    if h_detail:
                        step_info["child_history"].append(h_detail)

        steps_detail.append(step_info)

    # Discover referenced artifact files in metadata (recursive)
    seen_paths = set()
    artifacts = extract_artifacts_from_metadata(metadata, seen_paths=seen_paths)

    # Aggregate artifacts from child executions into parent list
    for step_info in steps_detail:
        if step_info.get("active_child"):
            c_arts = step_info["active_child"].get("artifacts", [])
            for c_art in c_arts:
                if c_art["path"] not in seen_paths:
                    seen_paths.add(c_art["path"])
                    artifacts.append(dict(c_art))
        if step_info.get("child_history"):
            for ch in step_info["child_history"]:
                c_arts = ch.get("artifacts", [])
                for c_art in c_arts:
                    if c_art["path"] not in seen_paths:
                        seen_paths.add(c_art["path"])
                        artifacts.append(dict(c_art))

    # Parent workflow resolution for sub-workflows
    parent_info = None
    if metadata.get("_parent_workflow") and metadata.get("_parent_state_id"):
        parent_info = {
            "workflow_name": metadata["_parent_workflow"],
            "state_id": metadata["_parent_state_id"],
        }
    else:
        call_stack = metadata.get("_call_stack", [])
        if isinstance(call_stack, list) and len(call_stack) > 1:
            parent_wf = call_stack[-2]
            parent_dir = base_dir / parent_wf
            if parent_dir.is_dir():
                for sf in list(parent_dir.glob("STATE-*.yaml")) + list(parent_dir.glob("STATE-*.yml")):
                    try:
                        pdata = load_state_yaml(sf)
                        pmeta = pdata.get("metadata", {})
                        p_sub = pmeta.get("_sub_workflow", {})
                        if isinstance(p_sub, dict) and p_sub.get("state_id") == state_id:
                            parent_info = {
                                "workflow_name": parent_wf,
                                "state_id": sf.stem.replace("STATE-", ""),
                            }
                            break
                        for h in pmeta.get("_sub_workflow_history", []):
                            if isinstance(h, dict) and h.get("state_id") == state_id:
                                parent_info = {
                                    "workflow_name": parent_wf,
                                    "state_id": sf.stem.replace("STATE-", ""),
                                }
                                break
                        if parent_info:
                            break
                    except Exception:
                        pass

    agent_status, agent_reason = resolve_live_agent_status(state_id, state_data, state_file)

    return {
        "workflow_name": workflow_name,
        "custom_title": metadata.get("_custom_title", ""),
        "workflow_description": spec.get("description", ""),
        "state_id": state_id,
        "state": current_state,
        "step": current_step_idx,
        "total_steps": len(raw_steps),
        "retries": state_data.get("retries", 0),
        "stuck_reason": state_data.get("stuck_reason", ""),
        "metadata": metadata,
        "steps": steps_detail,
        "artifacts": artifacts,
        "parent": parent_info,
        "continuation_cmd": f"emu-dev-cli workflow {workflow_name} --state={state_id}",
        "conversation_id": metadata.get("conversation_id"),
        "agent_status": agent_status,
        "agent_status_reason": agent_reason,
    }


def create_composed_session(
    steps: Optional[List[Dict[str, Any]]] = None,
    args: Optional[Dict[str, Any]] = None,
    title: Optional[str] = None,
    state_base_dir: Optional[Path] = None,
    steps_payload: Optional[List[Dict[str, Any]]] = None,
    shared_args: Optional[Dict[str, Any]] = None,
    launch_jetski: bool = False,
) -> Dict[str, Any]:
    """Creates a new composed workflow YAML and initializes its session state."""
    effective_steps = steps if steps is not None else (steps_payload or [])
    effective_args = args if args is not None else (shared_args or {})
    if not effective_steps:
        raise ValueError("At least one workflow step is required.")

    base_dir = state_base_dir or get_state_base_dir()
    workflow_name = "custom-workflow"
    state_id = uuid.uuid4().hex[:8]

    wf_state_dir = base_dir / workflow_name
    wf_state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    step_names = [
        s.get("title") or s.get("name") or s.get("workflow_name") or f"Step {i}"
        for i, s in enumerate(effective_steps)
    ]
    desc = title.strip() if title and title.strip() else f"Composed workflow: {' → '.join(step_names)}"

    yaml_steps: List[Dict[str, Any]] = []
    for idx, item in enumerate(effective_steps):
        s_type = item.get("type", "workflow")
        s_name = (item.get("name") or item.get("workflow_name") or "").strip()
        s_title = (item.get("title") or "").strip()
        s_custom = (item.get("custom_instructions") or "").strip()

        if s_type == "human_verify" or s_name == "human-verify":
            yaml_steps.append({
                "step": idx,
                "title": s_title or "Await Human Verification",
                "description": "Pause workflow execution until approved by human verifier",
                "human_verify": True,
                "prompt": (
                    "Please pause execution and summarize what you have accomplished and verified so far. "
                    "Wait for the human to verify the results and approve continuation in the Workflow UI."
                ),
                "custom_instructions": s_custom,
                "verifier_cmd": None,
            })
        elif s_type == "custom_step" or s_name == "custom-step":
            yaml_steps.append({
                "step": idx,
                "title": s_title or f"Custom Step {idx + 1}",
                "description": "Custom instruction step",
                "prompt": s_custom or "Follow the custom instructions for this step.",
                "verifier_cmd": item.get("verifier_cmd") or 'python3 -c "import sys; sys.exit(0)"',
            })
        else:
            step_obj: Dict[str, Any] = {
                "step": idx,
                "title": s_title or s_name,
                "description": f"Execute {s_name} sub-workflow",
                "workflow": {
                    "name": s_name,
                    "args": item.get("args", {}),
                },
            }
            if s_custom:
                step_obj["custom_instructions"] = s_custom
            yaml_steps.append(step_obj)

    # Append final passive completion step
    yaml_steps.append({
        "step": len(yaml_steps),
        "title": "Completion",
        "description": "Pipeline completed",
        "prompt": "Composed workflow pipeline completed successfully!",
        "verifier_cmd": None,
    })

    composed_spec = {
        "name": workflow_name,
        "description": desc,
        "steps": yaml_steps,
    }

    spec_file = wf_state_dir / f"WORKFLOW-{state_id}.yaml"
    with open(spec_file, "w", encoding="utf-8") as f:
        yaml.dump(composed_spec, f, Dumper=StateYamlDumper, sort_keys=False, default_flow_style=False)

    init_args = dict(effective_args)
    init_args["_workflow_spec_path"] = str(spec_file.resolve())
    if title and title.strip():
        init_args["_custom_title"] = title.strip()

    old_env = os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR")
    if state_base_dir is not None:
        os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = str(base_dir)
    try:
        created_id = init_workflow_session(spec_file, init_args, state_id_override=state_id)
        # Immediately enter Step 0 so the session starts active (RUNNING or WAITING_HUMAN)
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            execute_workflow_step(spec_file, created_id)
            initial_prompt = buf.getvalue().strip()
        finally:
            sys.stdout = old_stdout

        state_file = wf_state_dir / f"STATE-{created_id}.yaml"
        if state_file.is_file() and initial_prompt:
            st = load_state_yaml(state_file)
            st.setdefault("metadata", {})["_last_step_prompt"] = initial_prompt
            save_state_yaml(state_file, st)
    finally:
        if state_base_dir is not None:
            if old_env is not None:
                os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = old_env
            else:
                os.environ.pop("EMU_DEV_CLI_WORKFLOW_STATE_DIR", None)

    result = {
        "workflow_name": workflow_name,
        "state_id": created_id,
        "spec_path": str(spec_file.resolve()),
        "continuation_cmd": f"emu-dev-cli workflow {workflow_name} --state={created_id}",
    }
    if launch_jetski:
        ok, msg, _ = launch_jetski_for_session(
            workflow_name=workflow_name,
            state_id=created_id,
            resume=False,
            state_base_dir=base_dir,
        )
        result["agent_launched"] = ok
        result["agent_message"] = msg
    return result


def get_jetski_brain_dir() -> Path:
    """Returns the base directory where Jetski stores conversation brain logs."""
    env_dir = os.environ.get("JETSKI_BRAIN_DIR")
    if env_dir:
        return Path(os.path.expanduser(env_dir)).resolve()
    return Path.home() / ".gemini" / "jetski" / "brain"


def _discover_newest_brain_conversation(before_ids: set) -> Optional[str]:
    """Checks ~/.gemini/jetski/brain for a newly created conversation ID directory."""
    brain_dir = get_jetski_brain_dir()
    if not brain_dir.is_dir():
        return None
    candidates = []
    for d in brain_dir.iterdir():
        if d.is_dir() and d.name not in before_ids and not d.name.startswith("."):
            try:
                candidates.append((d.stat().st_mtime, d.name))
            except Exception:
                pass
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    return None


def get_conversation_transcript(
    conversation_id: Optional[str] = None,
    log_file: Optional[str] = None,
    limit: int = 250,
) -> Dict[str, Any]:
    """Reads and parses the live transcript.jsonl for a Jetski conversation."""
    steps: List[Dict[str, Any]] = []
    transcript_path_str = ""
    total_steps = 0

    if conversation_id and str(conversation_id).strip():
        cid = str(conversation_id).strip()
        # Prevent path traversal
        if "/" not in cid and "\\" not in cid and ".." not in cid:
            t_path = get_jetski_brain_dir() / cid / ".system_generated" / "logs" / "transcript.jsonl"
            if t_path.is_file():
                transcript_path_str = str(t_path)
                try:
                    with open(t_path, "r", encoding="utf-8", errors="replace") as f:
                        raw_lines = f.readlines()
                    non_empty_lines = [
                        (idx, line.strip())
                        for idx, line in enumerate(raw_lines)
                        if line.strip()
                    ]
                    total_steps = len(non_empty_lines)
                    tail_lines = (
                        non_empty_lines[-limit:]
                        if limit and total_steps > limit
                        else non_empty_lines
                    )
                    parsed_steps: List[Dict[str, Any]] = []
                    for idx, stripped in tail_lines:
                        try:
                            obj = json.loads(stripped)
                            if not isinstance(obj, dict):
                                continue
                            tool_calls_raw = obj.get("tool_calls") or []
                            normalized_tools = []
                            if isinstance(tool_calls_raw, list):
                                for tc in tool_calls_raw:
                                    if isinstance(tc, dict):
                                        normalized_tools.append({
                                            "id": tc.get("id", ""),
                                            "name": tc.get("name", ""),
                                            "args": tc.get("args", {}),
                                        })
                            parsed_steps.append({
                                "step_index": obj.get("step_index", idx),
                                "source": obj.get("source", ""),
                                "type": obj.get("type", ""),
                                "status": obj.get("status", ""),
                                "created_at": obj.get("created_at", ""),
                                "content": obj.get("content", ""),
                                "thinking": obj.get("thinking", ""),
                                "tool_calls": normalized_tools,
                            })
                        except Exception:
                            continue
                    steps = parsed_steps
                except Exception:
                    pass

    raw_log = ""
    if log_file and str(log_file).strip():
        lp = Path(os.path.expanduser(str(log_file).strip()))
        if lp.is_file():
            try:
                text = lp.read_text(encoding="utf-8", errors="replace")
                raw_log = text[-32768:]
            except Exception:
                pass

    return {
        "conversation_id": conversation_id or "",
        "transcript_path": transcript_path_str,
        "total_steps": total_steps,
        "steps": steps,
        "raw_log": raw_log,
    }


def get_session_transcript(
    workflow_name: Optional[str] = None,
    state_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    state_base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Resolves a workflow session's conversation_id/log_file and returns its live transcript."""
    base_dir = state_base_dir or get_state_base_dir()
    resolved_cid = conversation_id
    log_file = None
    agent_status = None
    agent_status_reason = ""

    if workflow_name and state_id:
        for ext in (".yaml", ".yml"):
            sf = base_dir / workflow_name / f"STATE-{state_id}{ext}"
            if sf.is_file():
                try:
                    data = load_state_yaml(sf)
                    meta = data.get("metadata", {})
                    if not resolved_cid:
                        resolved_cid = meta.get("conversation_id")
                    log_file = meta.get("agent-log-file")
                    agent_status, agent_status_reason = resolve_live_agent_status(state_id, data, sf)
                    # Fallback to parent workflow if this is a sub-workflow
                    if not resolved_cid and meta.get("_parent_workflow") and meta.get("_parent_state_id"):
                        pwf = meta["_parent_workflow"]
                        psid = meta["_parent_state_id"]
                        for pext in (".yaml", ".yml"):
                            psf = base_dir / pwf / f"STATE-{psid}{pext}"
                            if psf.is_file():
                                pdata = load_state_yaml(psf)
                                pmeta = pdata.get("metadata", {})
                                resolved_cid = pmeta.get("conversation_id")
                                log_file = log_file or pmeta.get("agent-log-file")
                                if not agent_status:
                                    agent_status, agent_status_reason = resolve_live_agent_status(
                                        psid, pdata, psf
                                    )
                                break
                except Exception:
                    pass
                break
    elif resolved_cid:
        db_map = _query_conversation_summaries_db()
        db_row = db_map.get(resolved_cid)
        agent_status, agent_status_reason = resolve_conversation_status(resolved_cid, db_row=db_row)
        # Check if there is a poke log file for standalone conversation
        poke_log = get_jetski_brain_dir() / resolved_cid / ".system_generated" / "logs" / "ui_poke.log"
        if poke_log.is_file():
            log_file = str(poke_log)

    result = get_conversation_transcript(conversation_id=resolved_cid, log_file=log_file)
    result["workflow_name"] = workflow_name or ""
    result["state_id"] = state_id or ""
    result["agent_status"] = agent_status or ""
    result["agent_status_reason"] = agent_status_reason or ""
    return result


def _clean_conversation_text(text: str, max_len: int = 140) -> str:
    """Cleans up raw user prompt or response text for concise conversation titles/previews."""
    if not text:
        return ""
    cleaned = re.sub(r"</?USER_REQUEST>", "", str(text))
    cleaned = re.sub(r"<CONTEXT_SUMMARY>[\s\S]*?(?:</CONTEXT_SUMMARY>|$)", "", cleaned)
    cleaned = re.sub(r"<ADDITIONAL_METADATA>[\s\S]*?(?:</ADDITIONAL_METADATA>|$)", "", cleaned)
    cleaned = " ".join(cleaned.strip().split())
    if len(cleaned) > max_len:
        return cleaned[: max_len - 1] + "…"
    return cleaned


def _query_conversation_summaries_db(limit: int = 100) -> Dict[str, Dict[str, Any]]:
    """Queries ~/.gemini/jetski/conversation_summaries.db for live conversation metadata."""
    db_path = get_jetski_brain_dir().parent / "conversation_summaries.db"
    if not db_path.is_file():
        return {}
    rows_by_id: Dict[str, Dict[str, Any]] = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.5)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT conversation_id, title, preview, step_count, last_modified_time,
                       workspace_uris, status, not_fully_idle, parent_conversation_id,
                       nesting_depth, killed
                FROM conversation_summaries
                ORDER BY last_modified_time DESC
                LIMIT ?
                """,
                (max(50, limit * 2),),
            )
            for row in cur.fetchall():
                cid = str(row["conversation_id"] or "").strip()
                if not cid:
                    continue
                ws_raw = row["workspace_uris"] or "[]"
                workspace_path = ""
                try:
                    uris = json.loads(ws_raw)
                    if isinstance(uris, list) and uris:
                        first_uri = str(uris[0])
                        if first_uri.startswith("file://"):
                            workspace_path = urllib.parse.unquote(first_uri[len("file://") :])
                        else:
                            workspace_path = first_uri
                except Exception:
                    pass
                rows_by_id[cid] = {
                    "conversation_id": cid,
                    "title": str(row["title"] or ""),
                    "preview": str(row["preview"] or ""),
                    "step_count": int(row["step_count"] or 0),
                    "last_modified_time": str(row["last_modified_time"] or ""),
                    "workspace": workspace_path,
                    "status": str(row["status"] or ""),
                    "not_fully_idle": int(row["not_fully_idle"] or 0),
                    "parent_conversation_id": str(row["parent_conversation_id"] or ""),
                    "nesting_depth": int(row["nesting_depth"] or 0),
                    "killed": int(row["killed"] or 0),
                }
        finally:
            conn.close()
    except Exception:
        return {}
    return rows_by_id


def _inspect_conversation_transcript_meta(
    conversation_id: str,
    need_title: bool = True,
    need_preview: bool = True,
) -> Dict[str, Any]:
    """Reads tail (and optional header) of transcript.jsonl to determine turn state."""
    info: Dict[str, Any] = {
        "exists": False,
        "mtime": 0.0,
        "step_count": 0,
        "first_user_prompt": "",
        "last_preview": "",
        "last_type": "",
        "last_status": "",
        "last_tool_calls": [],
        "is_mid_turn": False,
        "mid_turn_reason": "",
    }
    if not conversation_id or "/" in conversation_id or "\\" in conversation_id or ".." in conversation_id:
        return info

    t_path = get_jetski_brain_dir() / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"
    if not t_path.is_file():
        return info

    try:
        st = t_path.stat()
        info["exists"] = True
        info["mtime"] = st.st_mtime
        size = st.st_size
        if size == 0:
            return info

        with open(t_path, "rb") as f:
            if need_title:
                head_text = f.read(min(size, 8192)).decode("utf-8", errors="replace")
                for ln in head_text.splitlines():
                    ln_s = ln.strip()
                    if not ln_s:
                        continue
                    try:
                        obj = json.loads(ln_s)
                        if isinstance(obj, dict) and str(obj.get("type", "")).upper() == "USER_INPUT":
                            c = str(obj.get("content", "")).strip()
                            if c:
                                info["first_user_prompt"] = _clean_conversation_text(c, 100)
                                break
                    except Exception:
                        continue

            # Read tail 16KB to inspect latest step
            seek_pos = max(0, size - 16384)
            f.seek(seek_pos)
            tail_text = f.read().decode("utf-8", errors="replace")

        lines = [ln.strip() for ln in tail_text.splitlines() if ln.strip()]
        if not lines:
            return info

        last_obj = None
        for ln in reversed(lines):
            try:
                candidate = json.loads(ln)
                if isinstance(candidate, dict):
                    if last_obj is None:
                        last_obj = candidate
                    if need_preview and not info["last_preview"]:
                        c_text = str(candidate.get("content", "")).strip()
                        if c_text:
                            info["last_preview"] = _clean_conversation_text(c_text, 140)
                    if last_obj is not None and (not need_preview or info["last_preview"]):
                        break
            except Exception:
                continue

        if not isinstance(last_obj, dict):
            return info

        step_idx = last_obj.get("step_index", 0)
        try:
            info["step_count"] = int(step_idx) + 1
        except Exception:
            info["step_count"] = len(lines)

        step_type = str(last_obj.get("type", "")).upper()
        step_status = str(last_obj.get("status", "")).upper()
        tool_calls = last_obj.get("tool_calls") or []
        info["last_type"] = step_type
        info["last_status"] = step_status
        info["last_tool_calls"] = tool_calls if isinstance(tool_calls, list) else []

        if step_status in ("INTERRUPTED", "CANCELED", "CANCELLED", "ERROR", "FAILED"):
            info["is_mid_turn"] = True
            info["mid_turn_reason"] = f"Conversation interrupted at step #{step_idx} ({step_type}: {step_status})."
        elif step_status and step_status != "DONE":
            info["is_mid_turn"] = True
            info["mid_turn_reason"] = f"Conversation stopped mid-step at step #{step_idx} ({step_type}: {step_status})."
        elif step_type == "GENERIC":
            info["is_mid_turn"] = True
            info["mid_turn_reason"] = (
                f"Conversation interrupted mid-turn after tool execution (step #{step_idx}) — waiting for agent continuation."
            )
        elif step_type == "PLANNER_RESPONSE" and isinstance(tool_calls, list) and len(tool_calls) > 0:
            tc_name = tool_calls[0].get("name", "tool") if isinstance(tool_calls[0], dict) else "tool"
            info["is_mid_turn"] = True
            info["mid_turn_reason"] = f"Conversation interrupted during tool call '{tc_name}' (step #{step_idx})."
        elif step_type == "USER_INPUT":
            info["is_mid_turn"] = True
            info["mid_turn_reason"] = f"Conversation interrupted after prompt (step #{step_idx}) before agent responded."
    except Exception:
        pass

    return info


def resolve_conversation_status(
    conversation_id: str,
    db_row: Optional[Dict[str, Any]] = None,
    t_meta: Optional[Dict[str, Any]] = None,
    linked_wf: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """Determines whether a Jetski conversation is RUNNING, WAITING_INPUT, or INTERRUPTED."""
    if _is_conv_agent_alive(conversation_id):
        return "RUNNING", "Agent process is actively running."
    if linked_wf and linked_wf.get("agent_status") == "RUNNING":
        return "RUNNING", "Workflow agent process is actively running."

    if t_meta is None:
        t_meta = _inspect_conversation_transcript_meta(conversation_id, need_title=False, need_preview=False)

    db_status = str((db_row or {}).get("status", ""))
    not_fully_idle = bool((db_row or {}).get("not_fully_idle", 0))
    is_mid_turn = bool(t_meta.get("is_mid_turn", False))
    mid_turn_reason = str(t_meta.get("mid_turn_reason", ""))
    last_status = str(t_meta.get("last_status", "")).upper()
    last_type = str(t_meta.get("last_type", "")).upper()
    mtime = float(t_meta.get("mtime", 0.0) or 0.0)
    age_sec = max(0.0, time.time() - mtime) if mtime > 0 else 999999.0

    if last_status in ("INTERRUPTED", "CANCELED", "CANCELLED", "ERROR", "FAILED"):
        return "INTERRUPTED", mid_turn_reason or f"Conversation stopped with status {last_status}."

    if is_mid_turn:
        if not_fully_idle or db_status == "CASCADE_RUN_STATUS_RUNNING":
            if last_type in ("GENERIC", "USER_INPUT") and age_sec > 180:
                return "INTERRUPTED", mid_turn_reason
            return "RUNNING", "Agent is actively executing a turn."
        return "INTERRUPTED", mid_turn_reason or "Conversation stopped mid-turn and needs a poke to continue."

    if not_fully_idle or db_status == "CASCADE_RUN_STATUS_RUNNING":
        return "RUNNING", "Agent is actively executing a turn."

    if linked_wf and linked_wf.get("state") == "RUNNING" and linked_wf.get("agent_status") == "INTERRUPTED":
        return (
            "INTERRUPTED",
            linked_wf.get("agent_status_reason") or "Workflow is still RUNNING while conversation stopped.",
        )

    return "WAITING_INPUT", "Agent finished its turn and is waiting for your input to continue."


def scan_all_conversations(
    limit: int = 50,
    include_subagents: bool = False,
    state_base_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Scans all Jetski conversations on the system and returns their live status and metadata."""
    db_rows = _query_conversation_summaries_db(limit=limit)
    brain_dir = get_jetski_brain_dir()

    # Map conversation_id -> linked workflow session
    wf_sessions = scan_all_sessions(state_base_dir=state_base_dir, include_sub_workflows=True)
    conv_to_wf: Dict[str, Dict[str, Any]] = {}
    for s in wf_sessions:
        cid = s.get("conversation_id")
        if cid and str(cid).strip():
            cid_str = str(cid).strip()
            existing = conv_to_wf.get(cid_str)
            if not existing or (existing.get("is_sub_workflow") and not s.get("is_sub_workflow")):
                conv_to_wf[cid_str] = {
                    "workflow_name": s.get("workflow_name", ""),
                    "state_id": s.get("state_id", ""),
                    "state": s.get("state", ""),
                    "custom_title": s.get("custom_title", ""),
                    "agent_status": s.get("agent_status", ""),
                    "agent_status_reason": s.get("agent_status_reason", ""),
                    "is_sub_workflow": s.get("is_sub_workflow", False),
                }

    # Combine conversation IDs from DB, workflow sessions, and fallback brain_dir
    candidate_ids: List[str] = list(db_rows.keys())
    seen_ids = set(candidate_ids)

    for cid in conv_to_wf:
        if cid not in seen_ids:
            candidate_ids.append(cid)
            seen_ids.add(cid)

    # Only scan brain_dir on disk if DB had fewer entries than limit (e.g. in tests or standalone CLI)
    if len(candidate_ids) < limit and brain_dir.is_dir():
        brain_entries = []
        for d in brain_dir.iterdir():
            if d.is_dir() and not d.name.startswith(".") and d.name not in seen_ids:
                t_file = d / ".system_generated" / "logs" / "transcript.jsonl"
                if t_file.is_file():
                    try:
                        brain_entries.append((t_file.stat().st_mtime, d.name))
                    except Exception:
                        pass
        brain_entries.sort(reverse=True)
        for _, cid in brain_entries[:limit]:
            candidate_ids.append(cid)
            seen_ids.add(cid)

    conversations: List[Dict[str, Any]] = []
    for cid in candidate_ids:
        if limit and len(conversations) >= limit:
            break
        db_row = db_rows.get(cid)
        parent_cid = str((db_row or {}).get("parent_conversation_id", "")).strip()
        nesting = int((db_row or {}).get("nesting_depth", 0) or 0)
        is_subagent = bool(parent_cid or nesting > 0)
        if is_subagent and not include_subagents:
            continue

        has_db_title = bool((db_row or {}).get("title"))
        t_meta = _inspect_conversation_transcript_meta(
            cid,
            need_title=not has_db_title,
            need_preview=True,
        )
        if not db_row and not t_meta.get("exists") and cid not in conv_to_wf:
            continue

        linked_wf = conv_to_wf.get(cid)
        status, status_reason = resolve_conversation_status(
            cid, db_row=db_row, t_meta=t_meta, linked_wf=linked_wf
        )

        raw_title = (db_row or {}).get("title") or t_meta.get("first_user_prompt") or ""
        if not raw_title and linked_wf:
            raw_title = linked_wf.get("custom_title") or linked_wf.get("workflow_name") or ""
        title = _clean_conversation_text(raw_title, 100) or f"Conversation {cid[:8]}"

        db_preview = (db_row or {}).get("preview") or ""
        raw_preview = (
            t_meta.get("last_preview")
            if (t_meta.get("last_preview") and db_preview == (db_row or {}).get("title"))
            else (db_preview or t_meta.get("last_preview") or "")
        )
        preview = _clean_conversation_text(raw_preview, 140)

        step_count = max(
            int((db_row or {}).get("step_count", 0) or 0),
            int(t_meta.get("step_count", 0) or 0),
        )

        mtime = float(t_meta.get("mtime", 0.0) or 0.0)
        db_iso = str((db_row or {}).get("last_modified_time", "")).strip()
        if db_iso:
            try:
                parsed_dt = datetime.fromisoformat(db_iso.replace("Z", "+00:00"))
                mtime = max(mtime, parsed_dt.timestamp())
            except Exception:
                pass
        if mtime <= 0:
            mtime = time.time()
        iso_time = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

        workspace = str((db_row or {}).get("workspace", "")).strip()
        workspace_name = Path(workspace).name if workspace else ""

        conversations.append({
            "conversation_id": cid,
            "title": title,
            "preview": preview,
            "step_count": step_count,
            "mtime": mtime,
            "updated_at": iso_time,
            "workspace": workspace,
            "workspace_name": workspace_name,
            "status": status,
            "status_reason": status_reason,
            "is_subagent": is_subagent,
            "parent_conversation_id": parent_cid,
            "linked_workflow": linked_wf,
        })

    conversations.sort(key=lambda c: c["mtime"], reverse=True)
    return conversations


def poke_conversation(
    conversation_id: str,
    prompt: Optional[str] = None,
    cwd: Optional[str] = None,
    state_base_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Sends a prompt or continuation poke to any Jetski conversation on the system."""
    if not conversation_id or not str(conversation_id).strip():
        return False, "Missing conversation_id."
    cid = str(conversation_id).strip()
    if "/" in cid or "\\" in cid or ".." in cid:
        return False, "Invalid conversation_id."

    # Check if this conversation is attached to an active workflow session
    wf_sessions = scan_all_sessions(state_base_dir=state_base_dir, include_sub_workflows=True)
    for s in wf_sessions:
        if s.get("conversation_id") == cid and s.get("state") in ("RUNNING", "STUCK", "INIT", "STOPPED"):
            ok, msg, _ = launch_jetski_for_session(
                workflow_name=s["workflow_name"],
                state_id=s["state_id"],
                resume=True,
                poke=True,
                custom_prompt=prompt,
                state_base_dir=state_base_dir,
            )
            return ok, msg

    jetski_bin = resolve_jetski_cli()
    if not jetski_bin:
        return False, "Could not locate jetski_cli (/google/bin/releases/jetski-devs/tools/cli)."

    effective_prompt = (
        prompt.strip()
        if prompt and str(prompt).strip()
        else "Your previous turn was interrupted or paused. Please continue from where you left off."
    )

    # Resolve working directory from explicit cwd, DB workspace_uris, or current working directory
    effective_cwd = os.getcwd()
    if cwd and os.path.isdir(str(cwd)):
        effective_cwd = str(Path(str(cwd)).resolve())
    else:
        db_row = _query_conversation_summaries_db().get(cid)
        ws = (db_row or {}).get("workspace", "")
        if ws and os.path.isdir(ws):
            effective_cwd = str(Path(ws).resolve())

    log_dir = get_jetski_brain_dir() / cid / ".system_generated" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = log_dir / "ui_poke.log"

    cmd = [
        jetski_bin,
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--conversation",
        cid,
        "--prompt",
        effective_prompt,
    ]

    prev_proc = _ACTIVE_CONV_PROCS.pop(cid, None)
    if prev_proc is not None and prev_proc.poll() is None:
        try:
            prev_proc.terminate()
        except Exception:
            pass

    _STARTING_CONVS.add(cid)

    def _run_conv():
        proc = None
        try:
            with open(log_path, "a", encoding="utf-8") as log_f:
                ts = datetime.now(timezone.utc).isoformat()
                log_f.write(f"\n=== [{ts}] Poking conversation {cid} ===\n")
                log_f.write(f"Command: {' '.join(cmd)}\nCwd: {effective_cwd}\n\n")
                log_f.flush()

                proc = subprocess.Popen(
                    cmd,
                    cwd=effective_cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                _ACTIVE_CONV_PROCS[cid] = proc
                _STARTING_CONVS.discard(cid)

                if proc.stdout:
                    for line in proc.stdout:
                        log_f.write(line)
                        log_f.flush()
                proc.wait()
                log_f.write(f"\n=== jetski_cli finished (exit code {proc.returncode}) ===\n")
                log_f.flush()
        except Exception as exc:
            try:
                with open(log_path, "a", encoding="utf-8") as log_f:
                    log_f.write(f"\n=== Error poking conversation {cid}: {exc} ===\n")
            except Exception:
                pass
        finally:
            _STARTING_CONVS.discard(cid)
            if proc is not None and _ACTIVE_CONV_PROCS.get(cid) is proc:
                _ACTIVE_CONV_PROCS.pop(cid, None)

    t = threading.Thread(target=_run_conv, daemon=True)
    t.start()
    return True, f"Sent message to conversation {cid[:8]} in {effective_cwd}"


def launch_jetski_for_session(
    workflow_name: str,
    state_id: str,
    resume: bool = False,
    feedback: Optional[str] = None,
    poke: bool = False,
    custom_prompt: Optional[str] = None,
    state_base_dir: Optional[Path] = None,
) -> Tuple[bool, str, Optional[str]]:
    """Spawns, resumes, or pokes a jetski_cli conversation in the background for a workflow session."""
    base_dir = state_base_dir or get_state_base_dir()
    state_file = base_dir / workflow_name / f"STATE-{state_id}.yaml"
    if not state_file.is_file():
        state_file = base_dir / workflow_name / f"STATE-{state_id}.yml"
        if not state_file.is_file():
            return False, f"Session {state_id} not found.", None

    jetski_bin = resolve_jetski_cli()
    if not jetski_bin:
        return False, "Could not locate jetski_cli (/google/bin/releases/jetski-devs/tools/cli).", None

    state_data = load_state_yaml(state_file)
    metadata = state_data.setdefault("metadata", {})
    continuation_cmd = f"emu-dev-cli workflow {workflow_name} --state={state_id}"
    last_step_prompt = metadata.get("_last_step_prompt", "").strip()
    existing_conv_id = metadata.get("conversation_id")

    if poke:
        if custom_prompt and custom_prompt.strip():
            prompt = (
                f"{custom_prompt.strip()}\n\n"
                f"(Workflow context: You are executing workflow session {state_id} ({workflow_name}). "
                f'When the current step is complete, run "{continuation_cmd}" and follow its instructions '
                f"until the workflow completes or reaches a human verification barrier.)"
            )
        else:
            step_reminder = (
                f"\n\nCurrent workflow step instructions:\n{last_step_prompt}"
                if last_step_prompt
                else ""
            )
            prompt = (
                f"Your previous turn in workflow session {state_id} ({workflow_name}) was interrupted or paused. "
                f"Please continue from where you left off. When the current step is completed, "
                f'run "{continuation_cmd}" and follow its instructions until the workflow completes '
                f"or reaches a human verification barrier.{step_reminder}"
            )
    elif resume:
        fb_note = f"\nHuman feedback: {feedback.strip()}\n" if feedback and feedback.strip() else ""
        if last_step_prompt:
            prompt = (
                f"Human verification has been approved!{fb_note}\n"
                f"Here are the instructions for the next workflow step:\n\n{last_step_prompt}"
            )
        else:
            prompt = (
                f"Human verification has been approved!{fb_note} "
                f'Please continue executing the workflow by running "{continuation_cmd}" '
                f"and following its instructions until the workflow completes or reaches another human verification barrier."
            )
    else:
        if last_step_prompt:
            prompt = (
                f"You are executing workflow session {state_id} ({workflow_name}). "
                f"Follow the current step instructions below, and when completed run `{continuation_cmd}` "
                f"until the workflow completes or reaches a human verification barrier:\n\n{last_step_prompt}"
            )
        else:
            prompt = (
                f'Please start and execute the workflow by running "{continuation_cmd}" '
                f"and following its step-by-step instructions until the workflow completes or reaches a human verification barrier."
            )

    cwd = os.getcwd()
    repo_val = metadata.get("repo")
    if repo_val and os.path.isdir(str(repo_val)):
        cwd = str(Path(str(repo_val)).resolve())

    cache_dir = Path(metadata.get("cache-dir") or (base_dir / workflow_name))
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = cache_dir / f"jetski_agent_{state_id}.log"

    # Always attach to existing_conv_id if this session already has a conversation
    cmd = [jetski_bin, "--dangerously-skip-permissions", "--output-format", "stream-json"]
    if existing_conv_id:
        cmd.extend(["--conversation", str(existing_conv_id)])
    cmd.extend(["--prompt", prompt])

    # Terminate any previous hung background stream-json process for this session
    prev_proc = _ACTIVE_AGENT_PROCS.pop(state_id, None)
    if prev_proc is not None and prev_proc.poll() is None:
        try:
            prev_proc.terminate()
        except Exception:
            pass

    brain_dir = get_jetski_brain_dir()
    before_brain_ids = set()
    if brain_dir.is_dir():
        try:
            before_brain_ids = {d.name for d in brain_dir.iterdir() if d.is_dir()}
        except Exception:
            pass

    if poke and state_data.get("state") in ("STOPPED", "STUCK"):
        state_data["state"] = "RUNNING"
        state_data["stuck_reason"] = ""
    metadata["agent-log-file"] = str(log_path.resolve())
    metadata["_agent_status"] = "RUNNING"
    save_state_yaml(state_file, state_data)
    _STARTING_AGENTS.add(state_id)

    def _run_and_monitor():
        detected_conv_id = existing_conv_id
        proc = None
        try:
            with open(log_path, "a", encoding="utf-8") as log_f:
                ts = datetime.now(timezone.utc).isoformat()
                mode_label = "poke" if poke else ("resume" if (resume or existing_conv_id) else "new")
                log_f.write(f"\n=== [{ts}] Launching jetski_cli ({mode_label}) ===\n")
                log_f.write(f"Command: {' '.join(cmd)}\nCwd: {cwd}\n\n")
                log_f.flush()

                proc = subprocess.Popen(
                    cmd,
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                _ACTIVE_AGENT_PROCS[state_id] = proc
                try:
                    st = load_state_yaml(state_file)
                    st.setdefault("metadata", {})["_agent_pid"] = proc.pid
                    save_state_yaml(state_file, st)
                except Exception:
                    pass
                _STARTING_AGENTS.discard(state_id)

                if proc.stdout:
                    for line in proc.stdout:
                        stripped = line.strip()
                        if not stripped:
                            continue
                        # Attempt to parse stream-json for conversation_id and readable content
                        try:
                            obj = json.loads(stripped)
                            if isinstance(obj, dict):
                                cid = (
                                    obj.get("conversation_id")
                                    or obj.get("conversationId")
                                    or obj.get("session_id")
                                )
                                if cid and not detected_conv_id:
                                    detected_conv_id = str(cid)
                                    st = load_state_yaml(state_file)
                                    st.setdefault("metadata", {})["conversation_id"] = detected_conv_id
                                    save_state_yaml(state_file, st)
                        except Exception:
                            pass

                        if not detected_conv_id:
                            new_cid = _discover_newest_brain_conversation(before_brain_ids)
                            if new_cid:
                                detected_conv_id = new_cid
                                try:
                                    st = load_state_yaml(state_file)
                                    st.setdefault("metadata", {})["conversation_id"] = detected_conv_id
                                    save_state_yaml(state_file, st)
                                except Exception:
                                    pass

                        log_f.write(line)
                        log_f.flush()

                proc.wait()
                if not detected_conv_id:
                    new_cid = _discover_newest_brain_conversation(before_brain_ids)
                    if new_cid:
                        detected_conv_id = new_cid

                st = load_state_yaml(state_file)
                meta = st.setdefault("metadata", {})
                meta.pop("_agent_pid", None)
                if detected_conv_id:
                    meta["conversation_id"] = detected_conv_id
                is_interrupted, _ = _inspect_transcript_interruption(detected_conv_id)
                if is_interrupted:
                    meta["_agent_status"] = "INTERRUPTED"
                elif proc.returncode == 0:
                    meta["_agent_status"] = "IDLE"
                else:
                    meta["_agent_status"] = f"EXITED ({proc.returncode})"
                save_state_yaml(state_file, st)
                log_f.write(f"\n=== jetski_cli finished (exit code {proc.returncode}) ===\n")
                log_f.flush()
        except Exception as exc:
            try:
                with open(log_path, "a", encoding="utf-8") as log_f:
                    log_f.write(f"\n=== Error running jetski_cli: {exc} ===\n")
                st = load_state_yaml(state_file)
                meta = st.setdefault("metadata", {})
                meta.pop("_agent_pid", None)
                meta["_agent_status"] = "ERROR"
                save_state_yaml(state_file, st)
            except Exception:
                pass
        finally:
            _STARTING_AGENTS.discard(state_id)
            if proc is not None and _ACTIVE_AGENT_PROCS.get(state_id) is proc:
                _ACTIVE_AGENT_PROCS.pop(state_id, None)

    t = threading.Thread(target=_run_and_monitor, daemon=True)
    t.start()

    action_verb = "Poked" if poke else ("Resumed" if existing_conv_id else "Launched")
    return True, f"{action_verb} jetski_cli in {cwd}", existing_conv_id


def stop_session(workflow_name: str, state_id: str, state_base_dir: Optional[Path] = None) -> bool:
    """Marks an active or stuck workflow session as STOPPED."""
    base_dir = state_base_dir or get_state_base_dir()
    for ext in (".yaml", ".yml"):
        state_file = base_dir / workflow_name / f"STATE-{state_id}{ext}"
        if state_file.is_file():
            try:
                state_data = load_state_yaml(state_file)
                state_data["state"] = "STOPPED"
                state_data["stuck_reason"] = "Workflow stopped by user via Web UI"
                save_state_yaml(state_file, state_data)
                return True
            except Exception:
                return False
    return False


def delete_session(workflow_name: str, state_id: str, state_base_dir: Optional[Path] = None) -> bool:
    """Permanently removes a workflow session state file."""
    base_dir = state_base_dir or get_state_base_dir()
    deleted = False
    for ext in (".yaml", ".yml"):
        state_file = base_dir / workflow_name / f"STATE-{state_id}{ext}"
        if state_file.is_file():
            try:
                state_file.unlink(missing_ok=True)
                deleted = True
            except Exception:
                pass
        spec_file = base_dir / workflow_name / f"WORKFLOW-{state_id}{ext}"
        if spec_file.is_file():
            try:
                spec_file.unlink(missing_ok=True)
            except Exception:
                pass
    return deleted


def clear_sessions(filter_type: str, state_base_dir: Optional[Path] = None) -> Dict[str, int]:
    """
    Performs bulk operations on workflow sessions.
    Supported filter_type values:
      - 'done': delete all DONE sessions
      - 'stopped': delete all STOPPED or CANCELLED sessions
      - 'obsolete': delete all DONE, STOPPED, or CANCELLED sessions
      - 'stop_all_active': set all RUNNING, INIT, WAITING_HUMAN, or STUCK sessions to STOPPED
      - 'all': delete ALL sessions
    """
    base_dir = state_base_dir or get_state_base_dir()
    count_deleted = 0
    count_updated = 0

    if not base_dir.is_dir():
        return {"deleted": 0, "updated": 0}

    for wf_dir in base_dir.iterdir():
        if not wf_dir.is_dir() or wf_dir.name.startswith("."):
            continue

        for state_file in list(wf_dir.glob("STATE-*.yaml")) + list(wf_dir.glob("STATE-*.yml")):
            try:
                state_data = load_state_yaml(state_file)
                current_state = state_data.get("state", "UNKNOWN").upper()

                if filter_type == "all":
                    state_file.unlink(missing_ok=True)
                    count_deleted += 1
                elif filter_type == "done" and current_state == "DONE":
                    state_file.unlink(missing_ok=True)
                    count_deleted += 1
                elif filter_type == "stopped" and current_state in ("STOPPED", "CANCELLED"):
                    state_file.unlink(missing_ok=True)
                    count_deleted += 1
                elif filter_type == "obsolete" and current_state in ("DONE", "STOPPED", "CANCELLED"):
                    state_file.unlink(missing_ok=True)
                    count_deleted += 1
                elif filter_type in ("stop_all_active", "stop_running") and current_state in (
                    "RUNNING",
                    "INIT",
                    "STUCK",
                    "WAITING_HUMAN",
                ):
                    state_data["state"] = "STOPPED"
                    state_data["stuck_reason"] = "Workflow stopped by user via Web UI"
                    save_state_yaml(state_file, state_data)
                    count_updated += 1
            except Exception:
                continue

    return {"deleted": count_deleted, "updated": count_updated}


def find_available_port(host: str = "127.0.0.1", start_port: int = 8080, max_attempts: int = 50) -> int:
    """Finds the first available TCP port starting from start_port."""
    for p in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    return start_port


class WorkflowUIHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler serving REST API endpoints and single-page web visualizer."""

    def log_message(self, format, *args):
        # Suppress verbose HTTP polling logs in CLI console
        pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def read_post_json(self) -> Dict[str, Any]:
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len > 0:
            raw_body = self.rfile.read(content_len).decode("utf-8")
            try:
                return json.loads(raw_body)
            except Exception:
                return {}
        return {}

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self.read_post_json()

        if path == "/api/session/stop":
            self.handle_api_session_stop(body)
        elif path == "/api/session/delete":
            self.handle_api_session_delete(body)
        elif path == "/api/sessions/clear":
            self.handle_api_sessions_clear(body)
        elif path == "/api/session/create":
            self.handle_api_session_create(body)
        elif path == "/api/session/approve":
            self.handle_api_session_approve(body)
        elif path in ("/api/session/launch_agent", "/api/session/poke"):
            if path == "/api/session/poke":
                body["poke"] = True
                body["resume"] = True
            self.handle_api_session_launch_agent(body)
        elif path == "/api/conversation/poke":
            self.handle_api_conversation_poke(body)
        else:
            self.send_error(404, "Endpoint not found")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path == "/api/sessions":
            self.handle_api_sessions(params)
        elif path == "/api/conversations":
            self.handle_api_conversations(params)
        elif path == "/api/session":
            wf = params.get("workflow", [None])[0]
            sid = params.get("id", [None])[0]
            self.handle_api_session_detail(wf, sid)
        elif path == "/api/transcript":
            wf = params.get("workflow", [None])[0]
            sid = params.get("id", [None])[0]
            cid = params.get("conversation_id", [None])[0]
            self.handle_api_transcript(wf, sid, cid)
        elif path == "/api/workflows":
            self.handle_api_workflows()
        elif path == "/api/artifact":
            file_path = params.get("path", [None])[0]
            self.handle_api_artifact(file_path)
        elif path in ("", "/", "/index.html"):
            self.handle_serve_index()
        else:
            self.send_error(404, "Endpoint not found")

    def handle_api_sessions(self, params: Optional[Dict[str, List[str]]] = None):
        include_subs = False
        if params and "include_sub_workflows" in params:
            include_subs = params["include_sub_workflows"][0].lower() in ("true", "1", "yes")
        sessions = scan_all_sessions(include_sub_workflows=include_subs)
        self.send_json({"sessions": sessions, "count": len(sessions)})

    def handle_api_conversations(self, params: Optional[Dict[str, List[str]]] = None):
        limit = 50
        include_subagents = False
        if params:
            if "limit" in params:
                try:
                    limit = max(1, min(200, int(params["limit"][0])))
                except Exception:
                    pass
            if "include_subagents" in params:
                include_subagents = params["include_subagents"][0].lower() in ("true", "1", "yes")
        conversations = scan_all_conversations(
            limit=limit,
            include_subagents=include_subagents,
            state_base_dir=get_state_base_dir(),
        )
        self.send_json({"conversations": conversations, "count": len(conversations)})

    def handle_api_conversation_poke(self, body: Dict[str, Any]):
        cid = body.get("conversation_id") or body.get("id")
        prompt = body.get("prompt") or body.get("message")
        cwd = body.get("cwd") or body.get("workspace")
        if not cid:
            self.send_json({"error": "Missing conversation_id parameter"}, status=400)
            return
        ok, msg = poke_conversation(
            conversation_id=str(cid),
            prompt=prompt,
            cwd=cwd,
            state_base_dir=get_state_base_dir(),
        )
        if ok:
            self.send_json({
                "status": "success",
                "message": msg,
                "conversation_id": str(cid),
            })
        else:
            self.send_json({"error": msg}, status=400)

    def handle_api_session_detail(self, wf: Optional[str], sid: Optional[str]):
        if not wf or not sid:
            self.send_json({"error": "Missing workflow or id parameter"}, status=400)
            return
        detail = get_session_detail(wf, sid)
        if not detail:
            self.send_json({"error": f"Session not found for workflow {wf} with ID {sid}"}, status=404)
            return
        self.send_json(detail)

    def handle_api_transcript(self, wf: Optional[str], sid: Optional[str], cid: Optional[str]):
        if not wf and not sid and not cid:
            self.send_json({"error": "Missing workflow/id or conversation_id parameter"}, status=400)
            return
        transcript = get_session_transcript(workflow_name=wf, state_id=sid, conversation_id=cid)
        self.send_json(transcript)

    def handle_api_workflows(self):
        catalog = get_workflow_catalog()
        source_dirs = get_known_source_directories()
        jetski_bin = resolve_jetski_cli()
        self.send_json({
            "workflows": catalog,
            "source_directories": source_dirs,
            "default_cwd": os.getcwd(),
            "jetski_available": bool(jetski_bin),
            "jetski_bin": jetski_bin or "",
        })

    def handle_api_artifact(self, file_path: Optional[str]):
        if not file_path:
            self.send_json({"error": "Missing path parameter"}, status=400)
            return
        p = Path(os.path.expanduser(file_path)).resolve()
        if not p.is_file():
            self.send_json({"error": "File not found"}, status=404)
            return
        try:
            # Read first 128KB safely
            content = p.read_text(encoding="utf-8", errors="replace")[:131072]
            self.send_json({
                "path": str(p),
                "filename": p.name,
                "size": p.stat().st_size,
                "content": content,
            })
        except Exception as e:
            self.send_json({"error": f"Failed to read file: {e}"}, status=500)

    def handle_api_session_create(self, body: Dict[str, Any]):
        steps = body.get("steps", [])
        args = body.get("args", {})
        title = body.get("title", "")
        launch_agent = bool(body.get("launch_agent") or body.get("launch_jetski"))

        if not isinstance(steps, list) or len(steps) == 0:
            self.send_json({"error": "Please add at least one step to the workflow."}, status=400)
            return

        try:
            created = create_composed_session(
                steps=steps,
                args=args,
                title=title,
                state_base_dir=get_state_base_dir(),
                launch_jetski=launch_agent,
            )
            created["status"] = "success"
            created.setdefault("agent_launched", False)
            created.setdefault("agent_message", "")
            self.send_json(created)
        except Exception as e:
            self.send_json({"error": str(e)}, status=400)

    def handle_api_session_approve(self, body: Dict[str, Any]):
        wf = body.get("workflow")
        sid = body.get("id")
        feedback = body.get("feedback")
        resume_agent = bool(body.get("resume_agent") or body.get("resume_jetski"))
        if not sid:
            self.send_json({"error": "Missing id parameter"}, status=400)
            return

        base_dir = get_state_base_dir()
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            ok, msg = approve_workflow_session(
                state_id=sid,
                workflow_name=wf,
                feedback=feedback,
                state_base_dir=base_dir,
                advance=True,
            )
            next_prompt = buf.getvalue().strip()
        finally:
            sys.stdout = old_stdout

        if not ok:
            self.send_json({"error": msg}, status=404)
            return

        if wf and next_prompt:
            state_file = base_dir / wf / f"STATE-{sid}.yaml"
            if state_file.is_file():
                st = load_state_yaml(state_file)
                st.setdefault("metadata", {})["_last_step_prompt"] = next_prompt
                save_state_yaml(state_file, st)

        agent_resumed = False
        agent_msg = ""
        if resume_agent and wf:
            agent_resumed, agent_msg, _ = launch_jetski_for_session(
                workflow_name=wf,
                state_id=sid,
                resume=True,
                feedback=feedback,
                state_base_dir=base_dir,
            )

        self.send_json({
            "status": "success",
            "message": msg,
            "agent_resumed": agent_resumed,
            "agent_message": agent_msg,
        })

    def handle_api_session_launch_agent(self, body: Dict[str, Any]):
        wf = body.get("workflow")
        sid = body.get("id")
        resume = bool(body.get("resume", False))
        poke = bool(body.get("poke", False))
        custom_prompt = body.get("prompt") or body.get("message") or body.get("feedback")
        if not wf or not sid:
            self.send_json({"error": "Missing workflow or id parameter"}, status=400)
            return
        ok, msg, conv_id = launch_jetski_for_session(
            workflow_name=wf,
            state_id=sid,
            resume=resume or poke,
            feedback=custom_prompt if not poke else None,
            poke=poke,
            custom_prompt=custom_prompt if poke else None,
            state_base_dir=get_state_base_dir(),
        )
        if ok:
            self.send_json({
                "status": "success",
                "message": msg,
                "conversation_id": conv_id,
            })
        else:
            self.send_json({"error": msg}, status=400)

    def handle_api_session_stop(self, body: Dict[str, Any]):
        wf = body.get("workflow")
        sid = body.get("id")
        if not wf or not sid:
            self.send_json({"error": "Missing workflow or id parameter"}, status=400)
            return
        if stop_session(wf, sid):
            self.send_json({"status": "success", "message": f"Session {sid} stopped"})
        else:
            self.send_json({"error": f"Session {sid} not found"}, status=404)

    def handle_api_session_delete(self, body: Dict[str, Any]):
        wf = body.get("workflow")
        sid = body.get("id")
        if not wf or not sid:
            self.send_json({"error": "Missing workflow or id parameter"}, status=400)
            return
        if delete_session(wf, sid):
            self.send_json({"status": "success", "message": f"Session {sid} deleted"})
        else:
            self.send_json({"error": f"Session {sid} not found"}, status=404)

    def handle_api_sessions_clear(self, body: Dict[str, Any]):
        filter_type = body.get("filter", "obsolete")
        res = clear_sessions(filter_type)
        self.send_json({
            "status": "success",
            "filter": filter_type,
            "deleted": res["deleted"],
            "updated": res["updated"],
        })

    def handle_serve_index(self):
        html_path = get_ui_html_path()
        if html_path.is_file():
            try:
                content = html_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            except Exception:
                pass

        # Fallback minimal HTML if template file is missing
        fallback = b"""<!DOCTYPE html><html><head><title>Workflow Visualizer</title></head><body><h1>Workflow Visualizer</h1><p>UI template not found.</p></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(fallback)))
        self.end_headers()
        self.wfile.write(fallback)

    def send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def start_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    open_browser: bool = True,
    block: bool = True,
) -> Tuple[http.server.HTTPServer, int]:
    """Starts the visualizer HTTP server."""
    actual_port = find_available_port(host=host, start_port=port)
    server_address = (host, actual_port)
    httpd = http.server.ThreadingHTTPServer(server_address, WorkflowUIHandler)

    url = f"http://{host}:{actual_port}"
    print(f"🚀 emu-dev-cli Workflow Visualizer running at:\n   {url}\n")
    print("Press Ctrl+C to stop the server.")

    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open_new_tab(url)).start()

    if block:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")
        finally:
            httpd.server_close()

    return httpd, actual_port
