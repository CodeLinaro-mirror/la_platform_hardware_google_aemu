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

try:
    from workflows.core.yaml_runner import (
        get_state_base_dir,
        interpolate,
        load_state_yaml,
        load_yaml,
        resolve_workflow_file,
        save_state_yaml,
    )
except ImportError:
    from core.yaml_runner import (
        get_state_base_dir,
        interpolate,
        load_state_yaml,
        load_yaml,
        resolve_workflow_file,
        save_state_yaml,
    )

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
            if k in metadata:
                key_args[k] = metadata[k]

        sub_wf = metadata.get("_sub_workflow")
        sub_history = metadata.get("_sub_workflow_history", [])

        sessions.append({
            "workflow_name": wf_name,
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
                if k.startswith("_sub_workflow"):
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

    # Load workflow specification
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
            elif current_state == "RUNNING":
                status = "running"
            elif current_state == "INIT":
                status = "init"
            else:
                status = current_state.lower()
        else:
            status = "pending"

        # Format prompt
        raw_prompt = raw_s.get("prompt", "")
        if isinstance(raw_prompt, list):
            raw_prompt = "\n".join(raw_prompt)
        interpolated_prompt = interpolate(raw_prompt, metadata) if raw_prompt else ""

        step_info = {
            "index": idx,
            "title": s_title,
            "description": s_desc,
            "status": status,
            "prompt": interpolated_prompt,
            "verifier_cmd": raw_s.get("verifier_cmd"),
            "max_retries": raw_s.get("max_retries", 3),
            "is_sub_workflow": "workflow" in raw_s,
        }

        # Sub-workflow inspection
        if "workflow" in raw_s:
            sub_spec = raw_s["workflow"]
            child_name = sub_spec.get("name")
            step_info["sub_workflow_spec"] = {
                "name": child_name,
                "args": sub_spec.get("args", {}),
                "outputs": sub_spec.get("outputs", {}),
            }

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

    return {
        "workflow_name": workflow_name,
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
    }


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
    return deleted


def clear_sessions(filter_type: str, state_base_dir: Optional[Path] = None) -> Dict[str, int]:
    """
    Performs bulk operations on workflow sessions.
    Supported filter_type values:
      - 'done': delete all DONE sessions
      - 'stopped': delete all STOPPED or CANCELLED sessions
      - 'obsolete': delete all DONE, STOPPED, or CANCELLED sessions
      - 'stop_all_active': set all RUNNING, INIT, or STUCK sessions to STOPPED
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
                elif filter_type in ("stop_all_active", "stop_running") and current_state in ("RUNNING", "INIT", "STUCK"):
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
        else:
            self.send_error(404, "Endpoint not found")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path == "/api/sessions":
            self.handle_api_sessions(params)
        elif path == "/api/session":
            wf = params.get("workflow", [None])[0]
            sid = params.get("id", [None])[0]
            self.handle_api_session_detail(wf, sid)
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

    def handle_api_session_detail(self, wf: Optional[str], sid: Optional[str]):
        if not wf or not sid:
            self.send_json({"error": "Missing workflow or id parameter"}, status=400)
            return
        detail = get_session_detail(wf, sid)
        if not detail:
            self.send_json({"error": f"Session not found for workflow {wf} with ID {sid}"}, status=404)
            return
        self.send_json(detail)

    def handle_api_workflows(self):
        wfs = list_available_workflows()
        result = []
        for name, info in wfs.items():
            result.append({
                "name": name,
                "description": info.get("description", ""),
                "target_type": info.get("target_type", "yaml"),
            })
        self.send_json({"workflows": result})

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
