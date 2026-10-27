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

"""Session Ledger store for the Android Emulator Jetski Harness.

Tracks per-conversation active workflows, spawned emulator instances,
stop-hook continuation budgets, and guardrail audit events.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from commands.harness.constants import LedgerEventKind, MAX_LEDGER_EVENTS


def get_harness_base_dir() -> Path:
    """Returns the base directory for harness session ledgers."""
    env_dir = os.environ.get("EMU_DEV_CLI_HARNESS_DIR")
    if env_dir:
        base = Path(env_dir)
    else:
        base = Path.home() / ".cache" / "emu-dev-cli" / "harness"
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def _sanitize_conversation_id(conversation_id: Optional[str]) -> str:
    """Sanitizes a conversation ID into a safe filename token."""
    raw = (conversation_id or "").strip()
    if not raw:
        return "default"
    safe = "".join(c for c in raw if c.isalnum() or c in ("-", "_"))
    return safe or "default"


def _utc_now_iso() -> str:
    """Returns current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SessionLedgerStore:
    """Reads and writes per-conversation harness state ledgers on disk."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.base_dir = base_dir or get_harness_base_dir()
        self.sessions_dir = self.base_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def ledger_path(self, conversation_id: Optional[str]) -> Path:
        safe_id = _sanitize_conversation_id(conversation_id)
        return self.sessions_dir / f"{safe_id}.json"

    def load(self, conversation_id: Optional[str]) -> Dict[str, Any]:
        """Loads the session ledger for conversation_id, initializing defaults if absent."""
        safe_id = _sanitize_conversation_id(conversation_id)
        path = self.ledger_path(safe_id)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {
            "conversation_id": safe_id,
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "active_emulators": [],
            "linked_workflows": [],
            "stop_continuations": 0,
            "stats": {
                "commits_verified": 0,
                "guardrails_blocked": 0,
                "emulators_launched": 0,
            },
            "events": [],
        }

    def save(self, conversation_id: Optional[str], data: Dict[str, Any]) -> Path:
        """Persists the session ledger to disk."""
        safe_id = _sanitize_conversation_id(conversation_id)
        data["conversation_id"] = safe_id
        data["updated_at"] = _utc_now_iso()
        events = data.get("events", [])
        if len(events) > MAX_LEDGER_EVENTS:
            data["events"] = events[-MAX_LEDGER_EVENTS:]

        path = self.ledger_path(safe_id)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    def record_event(
        self,
        conversation_id: Optional[str],
        kind: LedgerEventKind,
        summary: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Appends a timestamped event to the conversation's ledger."""
        ledger = self.load(conversation_id)
        event_entry: Dict[str, Any] = {
            "timestamp": _utc_now_iso(),
            "kind": kind.value,
            "summary": summary,
        }
        if details:
            event_entry["details"] = details

        ledger.setdefault("events", []).append(event_entry)
        stats = ledger.setdefault(
            "stats",
            {"commits_verified": 0, "guardrails_blocked": 0, "emulators_launched": 0},
        )
        if kind in (
            LedgerEventKind.GUARDRAIL_BLOCKED,
            LedgerEventKind.CODE_GUARDRAIL_BLOCKED,
        ):
            stats["guardrails_blocked"] = int(stats.get("guardrails_blocked", 0)) + 1
        elif kind == LedgerEventKind.GUARDRAIL_ALLOWED:
            stats["commits_verified"] = int(stats.get("commits_verified", 0)) + 1
        elif kind == LedgerEventKind.EMULATOR_LAUNCHED:
            stats["emulators_launched"] = int(stats.get("emulators_launched", 0)) + 1
        elif kind == LedgerEventKind.COMMAND_REWRITTEN:
            stats["commands_rewritten"] = int(stats.get("commands_rewritten", 0)) + 1
        elif kind == LedgerEventKind.DIAGNOSTIC_INJECTED:
            stats["diagnostics_injected"] = int(stats.get("diagnostics_injected", 0)) + 1
        elif kind == LedgerEventKind.CONCURRENCY_TEST_WARNING:
            stats["concurrency_test_warnings"] = int(stats.get("concurrency_test_warnings", 0)) + 1

        self.save(conversation_id, ledger)
        return ledger

    def register_emulator_launch(
        self,
        conversation_id: Optional[str],
        avd_name: str,
        command: str,
    ) -> Dict[str, Any]:
        """Records an emulator instance launched during this conversation."""
        ledger = self.load(conversation_id)
        active = ledger.setdefault("active_emulators", [])
        if not any(item.get("avd") == avd_name for item in active):
            active.append({
                "avd": avd_name,
                "command": command,
                "started_at": _utc_now_iso(),
            })
        self.save(conversation_id, ledger)
        return self.record_event(
            conversation_id,
            LedgerEventKind.EMULATOR_LAUNCHED,
            f"Launched emulator target '{avd_name}'",
            {"avd": avd_name, "command": command},
        )

    def register_emulator_teardown(
        self,
        conversation_id: Optional[str],
        command: str,
    ) -> Dict[str, Any]:
        """Clears active emulator records when teardown is executed."""
        ledger = self.load(conversation_id)
        count = len(ledger.get("active_emulators", []))
        ledger["active_emulators"] = []
        self.save(conversation_id, ledger)
        return self.record_event(
            conversation_id,
            LedgerEventKind.EMULATOR_STOPPED,
            f"Tore down {count} tracked emulator instance(s)",
            {"command": command},
        )

    def list_all_ledgers(self) -> List[Dict[str, Any]]:
        """Lists all persisted conversation ledgers sorted by updated_at descending."""
        items: List[Dict[str, Any]] = []
        if not self.sessions_dir.is_dir():
            return items
        for file_path in self.sessions_dir.glob("*.json"):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    items.append(data)
            except Exception:
                continue
        items.sort(key=lambda d: str(d.get("updated_at", "")), reverse=True)
        return items

    def clear(self, conversation_id: Optional[str] = None) -> int:
        """Deletes either a specific conversation ledger or all ledgers."""
        if conversation_id:
            path = self.ledger_path(conversation_id)
            if path.is_file():
                path.unlink()
                return 1
            return 0

        removed = 0
        for file_path in self.sessions_dir.glob("*.json"):
            try:
                file_path.unlink()
                removed += 1
            except OSError:
                pass
        return removed
