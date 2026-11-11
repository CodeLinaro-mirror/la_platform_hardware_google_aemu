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

"""Thread-safe repository and atomic persistence for workflow session state (`STATE-<id>.yaml`)."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Callable, Dict, List, Mapping, Optional, Union
import yaml

_SAFE_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")

_SESSION_LOCKS_GUARD = threading.Lock()
_SESSION_LOCKS: Dict[str, threading.RLock] = {}


class StateYamlDumper(yaml.SafeDumper):
    """YAML dumper preserving literal block style (`|`) for multiline strings."""


def _multiline_str_presenter(dumper: yaml.Dumper, data: str) -> Any:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


StateYamlDumper.add_representer(str, _multiline_str_presenter)


def is_safe_state_token(token: str) -> bool:
    """Validates that a workflow name or session ID contains only safe path characters."""
    return bool(token and _SAFE_TOKEN_RE.match(str(token)))


def resolve_default_state_base_dir(
    explicit_dir: Optional[Union[str, Path]] = None,
) -> Path:
    """Resolves the root directory storing workflow session state subdirectories."""
    if explicit_dir is not None and str(explicit_dir).strip():
        base = Path(explicit_dir).expanduser().resolve()
    else:
        override = os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR")
        if override:
            base = Path(override).expanduser().resolve()
        else:
            cache_home = os.environ.get("XDG_CACHE_HOME")
            base = (
                (Path(cache_home) if cache_home else Path.home() / ".cache")
                / "emu-dev-cli"
                / "workflows"
                / "state"
            )
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base


def get_session_lock(lock_key: Union[str, Path]) -> threading.RLock:
    """Returns a process-wide reentrant lock for `lock_key` (path or `workflow/session_id`)."""
    norm_key = str(lock_key)
    with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(norm_key)
        if lock is None:
            lock = threading.RLock()
            _SESSION_LOCKS[norm_key] = lock
        return lock


class WorkflowState(dict):
    """Typed dictionary wrapper representing a workflow session's persisted YAML state."""

    def __init__(self, data: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> None:
        super().__init__()
        if data:
            self.update(dict(data))
        if kwargs:
            self.update(kwargs)
        self.setdefault("state", "INIT")
        self.setdefault("step", 0)
        self.setdefault("retries", 0)
        self.setdefault("stuck_reason", "")
        if not isinstance(self.get("metadata"), dict):
            self["metadata"] = {}

    @property
    def state(self) -> str:
        return str(self.get("state") or "UNKNOWN")

    @state.setter
    def state(self, value: str) -> None:
        self["state"] = str(value)

    @property
    def step(self) -> int:
        return int(self.get("step", 0) or 0)

    @step.setter
    def step(self, value: int) -> None:
        self["step"] = int(value)

    @property
    def retries(self) -> int:
        return int(self.get("retries", 0) or 0)

    @retries.setter
    def retries(self, value: int) -> None:
        self["retries"] = int(value)

    @property
    def iteration(self) -> int:
        return int(self.get("iteration", 0) or 0)

    @iteration.setter
    def iteration(self, value: int) -> None:
        self["iteration"] = int(value)

    @property
    def stuck_reason(self) -> str:
        return str(self.get("stuck_reason") or "")

    @stuck_reason.setter
    def stuck_reason(self, value: str) -> None:
        self["stuck_reason"] = str(value)

    @property
    def metadata(self) -> Dict[str, Any]:
        meta = self.get("metadata")
        if not isinstance(meta, dict):
            meta = {}
            self["metadata"] = meta
        return meta

    @metadata.setter
    def metadata(self, value: Dict[str, Any]) -> None:
        self["metadata"] = dict(value)

    def to_dict(self) -> Dict[str, Any]:
        """Returns a plain `dict` copy suitable for YAML or JSON serialization."""
        out = dict(self)
        if isinstance(out.get("metadata"), dict):
            out["metadata"] = dict(out["metadata"])
        return out


class SessionMetadata(dict):
    """Typed dictionary wrapper representing a discovered workflow session summary."""

    @property
    def state_id(self) -> str:
        return str(self.get("state_id") or "")

    @property
    def workflow_name(self) -> str:
        return str(self.get("workflow_name") or "")

    @property
    def custom_title(self) -> str:
        return str(self.get("custom_title") or "")

    @property
    def state(self) -> str:
        return str(self.get("state") or "UNKNOWN")

    @property
    def step(self) -> int:
        return int(self.get("step", 0) or 0)

    @property
    def mtime(self) -> float:
        return float(self.get("mtime", 0.0) or 0.0)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self)


def load_state_file_atomic(state_file: Union[str, Path]) -> WorkflowState:
    """Loads a `STATE-<id>.yaml` file under its per-file reentrant lock."""
    path = Path(state_file)
    lock = get_session_lock(path.resolve())
    with lock:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        data = {}
    return WorkflowState(data)


def save_state_file_atomic(
    state_file: Union[str, Path],
    state_data: Union[WorkflowState, Mapping[str, Any]],
) -> Path:
    """Atomically writes `state_data` to `state_file` under its per-file reentrant lock."""
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = get_session_lock(path.resolve())
    plain_dict = (
        state_data.to_dict()
        if isinstance(state_data, WorkflowState)
        else dict(state_data)
    )
    with lock:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_f:
                yaml.dump(
                    plain_dict,
                    tmp_f,
                    Dumper=StateYamlDumper,
                    sort_keys=False,
                    default_flow_style=False,
                )
                tmp_f.flush()
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, str(path))
            os.chmod(path, 0o600)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            raise
    return path


class WorkflowStateStore:
    """Repository providing atomic, locked CRUD operations over `STATE-<id>.yaml` files."""

    def __init__(self, state_base_dir: Optional[Union[str, Path]] = None) -> None:
        self._explicit_base_dir = Path(state_base_dir) if state_base_dir is not None else None

    @property
    def base_dir(self) -> Path:
        """Returns the effective base directory for workflow state files."""
        return resolve_default_state_base_dir(self._explicit_base_dir)

    def get_state_file_path(self, workflow_name: str, session_id: str) -> Path:
        """Constructs `<base_dir>/<workflow_name>/STATE-<session_id>.yaml` after token validation."""
        if not is_safe_state_token(workflow_name):
            raise ValueError(
                f"Invalid workflow name '{workflow_name}': expected alphanumeric, '.', '_', or '-'."
            )
        if not is_safe_state_token(session_id):
            raise ValueError(
                f"Invalid session state ID '{session_id}': expected alphanumeric, '.', '_', or '-'."
            )
        return self.base_dir / workflow_name / f"STATE-{session_id}.yaml"

    def resolve_state_file(
        self,
        session_id: str,
        workflow_name: Optional[str] = None,
    ) -> Optional[Path]:
        """Locates `STATE-<session_id>.yaml` across `workflow_name` or all workflow directories."""
        if not is_safe_state_token(session_id):
            return None
        base = self.base_dir
        if workflow_name:
            if not is_safe_state_token(workflow_name):
                return None
            for ext in (".yaml", ".yml"):
                candidate = base / workflow_name / f"STATE-{session_id}{ext}"
                if candidate.is_file():
                    return candidate
            return None
        if not base.is_dir():
            return None
        matches = sorted(
            list(base.glob(f"*/STATE-{session_id}.yaml"))
            + list(base.glob(f"*/STATE-{session_id}.yml"))
        )
        return matches[0] if matches else None

    def lock_for_session(
        self,
        session_id: str,
        workflow_name: Optional[str] = None,
    ) -> threading.RLock:
        """Returns the reentrant lock protecting `(workflow_name, session_id)`."""
        state_file = self.resolve_state_file(session_id, workflow_name=workflow_name)
        if state_file is not None:
            return get_session_lock(state_file.resolve())
        if workflow_name and is_safe_state_token(workflow_name) and is_safe_state_token(session_id):
            target = self.get_state_file_path(workflow_name, session_id)
            return get_session_lock(target.resolve())
        return get_session_lock(f"{self.base_dir.resolve()}:{workflow_name or '*'}:{session_id}")

    def load_session_state(
        self,
        session_id: str,
        workflow_name: Optional[str] = None,
    ) -> Optional[WorkflowState]:
        """Loads and returns `WorkflowState` for `session_id`, or `None` if not found."""
        state_file = self.resolve_state_file(session_id, workflow_name=workflow_name)
        if state_file is None or not state_file.is_file():
            return None
        try:
            return load_state_file_atomic(state_file)
        except Exception:
            return None

    def save_session_state(
        self,
        session_id: str,
        state: Union[WorkflowState, Mapping[str, Any]],
        workflow_name: Optional[str] = None,
    ) -> Path:
        """Atomically persists `state` to `STATE-<session_id>.yaml` under the per-session lock."""
        if workflow_name:
            target = self.get_state_file_path(workflow_name, session_id)
        else:
            resolved = self.resolve_state_file(session_id)
            if resolved is None:
                raise FileNotFoundError(
                    f"Cannot save session '{session_id}' without workflow_name when state file does not exist."
                )
            target = resolved
        return save_state_file_atomic(target, state)

    def update_session_state(
        self,
        session_id: str,
        mutator: Callable[[WorkflowState], Any],
        workflow_name: Optional[str] = None,
    ) -> Optional[WorkflowState]:
        """Executes an atomic read-modify-write cycle under the session's reentrant lock.

        If `mutator(state)` explicitly returns `False`, the state file is not rewritten.
        """
        state_file = self.resolve_state_file(session_id, workflow_name=workflow_name)
        if state_file is None or not state_file.is_file():
            return None
        lock = get_session_lock(state_file.resolve())
        with lock:
            try:
                state = load_state_file_atomic(state_file)
            except Exception:
                return None
            should_save = mutator(state)
            if should_save is not False:
                save_state_file_atomic(state_file, state)
            return state

    def delete_session(
        self,
        session_id: str,
        workflow_name: Optional[str] = None,
    ) -> bool:
        """Deletes `STATE-<session_id>.yaml` (and any companion `WORKFLOW-<session_id>`) under the session lock."""
        state_file = self.resolve_state_file(session_id, workflow_name=workflow_name)
        if state_file is None:
            return False
        lock = get_session_lock(state_file.resolve())
        with lock:
            deleted = False
            wf_dir = state_file.parent
            for ext in (".yaml", ".yml"):
                sf = wf_dir / f"STATE-{session_id}{ext}"
                if sf.is_file():
                    try:
                        sf.unlink(missing_ok=True)
                        deleted = True
                    except OSError:
                        pass
                spec_f = wf_dir / f"WORKFLOW-{session_id}{ext}"
                if spec_f.is_file():
                    try:
                        spec_f.unlink(missing_ok=True)
                    except OSError:
                        pass
            return deleted

    def list_sessions(
        self,
        filter_fn: Optional[Callable[[SessionMetadata], bool]] = None,
        *,
        include_sub_workflows: bool = False,
        status_resolver: Optional[
            Callable[[Path, Dict[str, Any], str, str], tuple[str, str]]
        ] = None,
    ) -> List[SessionMetadata]:
        """Scans all `STATE-*.yaml` files under `base_dir` and returns sorted `SessionMetadata`."""
        base_dir = self.base_dir
        if not base_dir.is_dir():
            return []

        raw_records: List[Dict[str, Any]] = []
        child_session_keys: set[tuple[str, str]] = set()

        for wf_dir in sorted(base_dir.iterdir()):
            if not wf_dir.is_dir():
                continue
            wf_name = wf_dir.name
            for state_file in sorted(wf_dir.glob("STATE-*.yaml")):
                try:
                    stat = state_file.stat()
                    data = load_state_file_atomic(state_file)
                except Exception:
                    continue

                stem = state_file.stem
                state_id = stem[len("STATE-") :] if stem.startswith("STATE-") else stem
                metadata = data.metadata

                call_stack = metadata.get("_call_stack")
                if isinstance(call_stack, list) and len(call_stack) > 1:
                    child_session_keys.add((wf_name, state_id))

                active_sub = metadata.get("_sub_workflow")
                if isinstance(active_sub, dict) and active_sub.get("state_id"):
                    c_name = str(active_sub.get("name", ""))
                    c_id = str(active_sub.get("state_id", ""))
                    child_session_keys.add((c_name, c_id))
                    child_session_keys.add(("*", c_id))

                sub_history = metadata.get("_sub_workflow_history")
                if isinstance(sub_history, list):
                    for item in sub_history:
                        if isinstance(item, dict) and item.get("state_id"):
                            c_name = str(item.get("name", ""))
                            c_id = str(item.get("state_id", ""))
                            child_session_keys.add((c_name, c_id))
                            child_session_keys.add(("*", c_id))

                raw_records.append(
                    {
                        "wf_name": wf_name,
                        "state_id": state_id,
                        "state_file": state_file,
                        "mtime": stat.st_mtime,
                        "data": data,
                        "metadata": metadata,
                    }
                )

        sessions: List[SessionMetadata] = []
        for rec in raw_records:
            wf_name = rec["wf_name"]
            state_id = rec["state_id"]
            state_file = rec["state_file"]
            stat_mtime = rec["mtime"]
            data: WorkflowState = rec["data"]
            metadata = rec["metadata"]

            is_sub_workflow = (
                (wf_name, state_id) in child_session_keys
                or ("*", state_id) in child_session_keys
            )
            if is_sub_workflow and not include_sub_workflows:
                continue

            key_args: Dict[str, Any] = {}
            for k in (
                "bug-number",
                "bug",
                "cl",
                "repo",
                "branch",
                "target",
                "hello-file",
                "joke-file",
            ):
                if k in metadata and isinstance(metadata[k], (str, int, float, bool)):
                    key_args[k] = metadata[k]

            sub_wf = metadata.get("_sub_workflow")
            has_sub = isinstance(sub_wf, dict) and bool(sub_wf.get("name"))
            conv_id = str(metadata.get("conversation_id") or "")
            state_str = str(data.get("state", "UNKNOWN"))

            if status_resolver is not None:
                agent_status, agent_status_reason = status_resolver(
                    state_file, data, state_id, state_str
                )
            else:
                agent_status = str(metadata.get("_agent_status") or "")
                agent_status_reason = str(metadata.get("_agent_status_reason") or "")

            entry = SessionMetadata(
                {
                    "state_id": state_id,
                    "workflow_name": wf_name,
                    "custom_title": metadata.get("_custom_title") or "",
                    "state": state_str,
                    "step": int(data.get("step", 0) or 0),
                    "retries": int(data.get("retries", 0) or 0),
                    "iteration": int(data.get("iteration", 0) or 0),
                    "stuck_reason": str(data.get("stuck_reason", "") or ""),
                    "has_sub_workflow": has_sub,
                    "sub_workflow_name": sub_wf.get("name") if has_sub else None,
                    "is_sub_workflow": is_sub_workflow,
                    "key_args": key_args,
                    "conversation_id": conv_id,
                    "agent_status": agent_status,
                    "agent_status_reason": agent_status_reason,
                    "state_file": str(state_file),
                    "mtime": stat_mtime,
                    "updated_at": datetime.fromtimestamp(
                        stat_mtime, tz=timezone.utc
                    ).isoformat(),
                }
            )
            if filter_fn is None or filter_fn(entry):
                sessions.append(entry)

        sessions.sort(key=lambda s: s["mtime"], reverse=True)
        return sessions


__all__ = [
    "SessionMetadata",
    "WorkflowState",
    "WorkflowStateStore",
    "get_session_lock",
    "is_safe_state_token",
    "load_state_file_atomic",
    "resolve_default_state_base_dir",
    "save_state_file_atomic",
]
