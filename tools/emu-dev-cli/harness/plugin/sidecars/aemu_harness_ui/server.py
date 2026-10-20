#!/usr/bin/env python3
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

"""Jetski Web AuxPane UI Sidecar for the Android Emulator Custom Agent Harness."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import urllib.parse
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


PORT: int = int(os.environ.get("ANTIGRAVITY_SIDECAR_WEB_PORT", "8765"))
UI_TOKEN: str = os.environ.get("ANTIGRAVITY_SIDECAR_UI_TOKEN", "")


def _get_harness_dir() -> Path:
    env_dir = os.environ.get("EMU_DEV_CLI_HARNESS_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".cache" / "emu-dev-cli" / "harness"


def _get_workflow_state_dir() -> Path:
    env_dir = os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".cache" / "emu-dev-cli" / "workflows" / "state"


def _sanitize_conv_id(conv_id: Optional[str]) -> str:
    raw = (conv_id or "").strip()
    safe = "".join(c for c in raw if c.isalnum() or c in ("-", "_"))
    return safe or "default"


def load_ledger_data(conversation_id: Optional[str]) -> Dict[str, Any]:
    """Reads the session ledger for conversation_id or the most recent active ledger."""
    sessions_dir = _get_harness_dir() / "sessions"
    if conversation_id:
        safe_id = _sanitize_conv_id(conversation_id)
        target = sessions_dir / f"{safe_id}.json"
        if target.is_file():
            try:
                return json.loads(target.read_text(encoding="utf-8"))
            except Exception:
                pass

    if sessions_dir.is_dir():
        files = sorted(
            sessions_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )
        for file_path in files:
            try:
                return json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                continue

    return {
        "conversation_id": _sanitize_conv_id(conversation_id),
        "active_emulators": [],
        "stop_continuations": 0,
        "stats": {
            "commits_verified": 0,
            "guardrails_blocked": 0,
            "emulators_launched": 0,
        },
        "events": [],
    }


def list_workflow_sessions(conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Reads recent `emu-dev-cli` workflow state files."""
    state_root = _get_workflow_state_dir()
    if not state_root.is_dir() or yaml is None:
        return []

    sessions: List[Dict[str, Any]] = []
    state_files = sorted(
        state_root.rglob("STATE-*.yaml"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    for sf in state_files[:15]:
        try:
            data = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        meta = data.get("metadata") or {}
        if meta.get("_parent_state_id"):
            continue

        wf_conv = str(meta.get("conversation_id") or "")
        sessions.append({
            "workflow_name": data.get("workflow_name") or sf.parent.name,
            "state_id": data.get("state_id") or sf.stem.replace("STATE-", ""),
            "status": str(data.get("status", "UNKNOWN")).upper(),
            "current_step": int(data.get("current_step", 0)),
            "retries": int(data.get("retries", 0)),
            "conversation_id": wf_conv,
            "matches_conversation": bool(conversation_id and wf_conv == conversation_id),
            "updated_at": str(data.get("updated_at", "")),
        })
    return sessions


INDEX_HTML: str = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AEMU Custom Agent Harness</title>
  <script src="/preload.js"></script>
  <style>
    :root {
      --bg: var(--background, #0f172a);
      --fg: var(--foreground, #e2e8f0);
      --card-bg: var(--card, #1e293b);
      --border-col: var(--border, #334155);
      --accent: var(--primary, #3ddc84);
      --muted: var(--muted-foreground, #94a3b8);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 14px;
      background: var(--bg);
      color: var(--fg);
      font-family: var(--font-sans, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif);
      font-size: 13px;
      line-height: 1.45;
    }
    .header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 12px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--border-col);
    }
    .title {
      font-weight: 700;
      font-size: 14px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .badge {
      font-size: 11px;
      padding: 2px 7px;
      border-radius: 999px;
      background: rgba(61, 220, 132, 0.15);
      color: #3ddc84;
      font-weight: 600;
    }
    .grid-3 {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
      margin-bottom: 12px;
    }
    .stat-card {
      background: var(--card-bg);
      border: 1px solid var(--border-col);
      border-radius: 8px;
      padding: 8px 10px;
      text-align: center;
    }
    .stat-val {
      font-size: 18px;
      font-weight: 700;
      color: #3ddc84;
    }
    .stat-lbl {
      font-size: 11px;
      color: var(--muted);
    }
    .section {
      background: var(--card-bg);
      border: 1px solid var(--border-col);
      border-radius: 8px;
      padding: 10px 12px;
      margin-bottom: 12px;
    }
    .section-title {
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--muted);
      margin-bottom: 8px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .btn-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 6px;
    }
    button {
      background: rgba(56, 189, 248, 0.12);
      color: var(--fg);
      border: 1px solid var(--border-col);
      border-radius: 6px;
      padding: 7px 9px;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      text-align: left;
      transition: border-color 0.15s, background 0.15s;
    }
    button:hover {
      border-color: #3ddc84;
      background: rgba(61, 220, 132, 0.14);
    }
    .wf-item, .ev-item {
      padding: 6px 0;
      border-bottom: 1px solid var(--border-col);
      font-size: 12px;
    }
    .wf-item:last-child, .ev-item:last-child {
      border-bottom: none;
    }
    .status-pill {
      display: inline-block;
      padding: 1px 6px;
      border-radius: 4px;
      font-size: 10px;
      font-weight: 700;
    }
    .status-RUNNING, .status-INIT { background: rgba(56, 189, 248, 0.2); color: #38bdf8; }
    .status-WAITING_HUMAN { background: rgba(251, 191, 36, 0.2); color: #fbbf24; }
    .status-DONE { background: rgba(61, 220, 132, 0.2); color: #3ddc84; }
    .status-STUCK, .status-RETRY { background: rgba(248, 113, 113, 0.2); color: #f87171; }
    .muted { color: var(--muted); font-size: 11px; }
  </style>
</head>
<body>
  <div class="header">
    <div class="title">
      <span>🤖 AEMU Agent Harness</span>
    </div>
    <span class="badge" id="conv-badge">Active</span>
  </div>

  <div class="grid-3">
    <div class="stat-card">
      <div class="stat-val" id="stat-commits">0</div>
      <div class="stat-lbl">Commits Verified</div>
    </div>
    <div class="stat-card">
      <div class="stat-val" id="stat-blocked">0</div>
      <div class="stat-lbl">Guardrails Fired</div>
    </div>
    <div class="stat-card">
      <div class="stat-val" id="stat-emus">0</div>
      <div class="stat-lbl">Running AVDs</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">⚡ One-Click Workflow Dispatch</div>
    <div class="btn-grid">
      <button onclick="dispatchPrompt('Run `emu-dev-cli workflow create-commit init` to create a Semantic Commit for my current changes.')">
        📝 Create Semantic Commit
      </button>
      <button onclick="dispatchPrompt('Run `emu-dev-cli workflow amend-commit init` to amend HEAD with a verified Semantic Commit.')">
        ✏️ Amend HEAD Commit
      </button>
      <button onclick="dispatchPrompt('Invoke the `aemu-code-reviewer` subagent to audit my current commit for //emulator/libs/* reuse, go/error-messages, 5-way code complexity, and Semantic Commit compliance.')">
        🔍 5-Way Code Review
      </button>
      <button onclick="dispatchPrompt('Run `emu-dev-cli workflow address-review init` to fetch and resolve open Gerrit review comments on my CL.')">
        💬 Address Gerrit Review
      </button>
      <button onclick="dispatchPrompt('Invoke `aemu-flake-hunter` to inspect recent flake history and reproduce the flaky test locally with 50x stress runs.')">
        🧪 Hunt & Repro Flake
      </button>
      <button onclick="dispatchPrompt('Run `emu-dev-cli crash list-crashes --unassigned-only` and summarize top unassigned production crashes.')">
        💥 Triage Crashpad
      </button>
    </div>
  </div>

  <div class="section">
    <div class="section-title">
      <span>🔄 Active emu-dev-cli Workflows</span>
    </div>
    <div id="workflows-list" class="muted">No active workflows detected.</div>
  </div>

  <div class="section">
    <div class="section-title">🛡️ Harness Guardrail & Hook Ledger</div>
    <div id="events-list" class="muted">No hook events recorded in this session yet.</div>
  </div>

  <script>
    const urlParams = new URLSearchParams(window.location.search);
    const uiToken = urlParams.get("token") || "";
    let currentConvId = urlParams.get("conversationId") || "";

    if (window.sidecar) {
      if (window.sidecar.conversationId) {
        currentConvId = window.sidecar.conversationId;
      }
      if (typeof window.sidecar.onConversationChange === "function") {
        window.sidecar.onConversationChange((newId) => {
          currentConvId = newId || "";
          refreshState();
        });
      }
    }

    function dispatchPrompt(text) {
      if (window.sidecar && window.sidecar.agent && typeof window.sidecar.agent.sendMessage === "function") {
        window.sidecar.agent.sendMessage(text);
      } else {
        navigator.clipboard.writeText(text);
        alert("Copied command prompt to clipboard:\\n\\n" + text);
      }
    }

    async function approveWorkflow(stateId) {
      await fetch("/api/workflow/approve?token=" + encodeURIComponent(uiToken), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Sidecar-Token": uiToken
        },
        body: JSON.stringify({ state_id: stateId })
      });
      refreshState();
    }

    async function refreshState() {
      try {
        const res = await fetch(
          "/api/state?conversationId=" + encodeURIComponent(currentConvId) + "&token=" + encodeURIComponent(uiToken),
          { headers: { "X-Sidecar-Token": uiToken } }
        );
        if (!res.ok) return;
        const data = await res.json();
        const ledger = data.ledger || {};
        const stats = ledger.stats || {};
        const activeEmus = ledger.active_emulators || [];

        document.getElementById("conv-badge").textContent =
          (ledger.conversation_id || currentConvId || "global").slice(0, 12);
        document.getElementById("stat-commits").textContent = stats.commits_verified || 0;
        document.getElementById("stat-blocked").textContent = stats.guardrails_blocked || 0;
        document.getElementById("stat-emus").textContent = activeEmus.length;

        const wfList = document.getElementById("workflows-list");
        const workflows = data.workflows || [];
        if (workflows.length === 0) {
          wfList.innerHTML = '<div class="muted">No workflows found in ~/.cache/emu-dev-cli/workflows/state.</div>';
        } else {
          wfList.innerHTML = workflows.slice(0, 6).map(wf => {
            const approveBtn = wf.status === "WAITING_HUMAN"
              ? ` <button style="padding:2px 6px;font-size:11px;" onclick="approveWorkflow('${wf.state_id}')">Approve</button>`
              : "";
            return `<div class="wf-item">
              <div>
                <strong>${wf.workflow_name}</strong>
                <span class="status-pill status-${wf.status}">${wf.status}</span>
                <span class="muted">step ${wf.current_step} · ${wf.state_id}</span>
                ${approveBtn}
              </div>
            </div>`;
          }).join("");
        }

        const evList = document.getElementById("events-list");
        const events = (ledger.events || []).slice().reverse();
        if (events.length === 0) {
          evList.innerHTML = '<div class="muted">No hook events recorded yet.</div>';
        } else {
          evList.innerHTML = events.slice(0, 8).map(ev => `
            <div class="ev-item">
              <div><strong>${ev.kind}</strong>: ${ev.summary}</div>
              <div class="muted">${ev.timestamp || ""}</div>
            </div>
          `).join("");
        }
      } catch (e) {
        // Ignore transient polling errors
      }
    }

    refreshState();
    setInterval(refreshState, 2500);
  </script>
</body>
</html>
"""


class HarnessSidecarHandler(BaseHTTPRequestHandler):
    """HTTP handler for the AEMU Harness Jetski Web AuxPane UI sidecar."""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _check_token(self, parsed_url: urllib.parse.ParseResult) -> bool:
        if not UI_TOKEN:
            return True
        header_token = self.headers.get("X-Sidecar-Token", "")
        if header_token == UI_TOKEN:
            return True
        query = urllib.parse.parse_qs(parsed_url.query)
        if query.get("token", [""])[0] == UI_TOKEN:
            return True
        return False

    def _send_json(self, status_code: int, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path

        if route == "/preload.js":
            js = b"window.sidecar = window.sidecar || {};\n"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(js)))
            self.end_headers()
            self.wfile.write(js)
            return

        if route in ("/", "/index.html"):
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if route == "/api/state":
            if not self._check_token(parsed):
                self._send_json(403, {"error": "Invalid sidecar UI token"})
                return
            query = urllib.parse.parse_qs(parsed.query)
            conv_id = query.get("conversationId", [None])[0]
            self._send_json(
                200,
                {
                    "ledger": load_ledger_data(conv_id),
                    "workflows": list_workflow_sessions(conv_id),
                },
            )
            return

        self._send_json(404, {"error": f"Unknown route: {route}"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if not self._check_token(parsed):
            self._send_json(403, {"error": "Invalid sidecar UI token"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            body = json.loads(raw_body)
        except Exception:
            body = {}

        if parsed.path == "/api/workflow/approve":
            state_id = str(body.get("state_id", "")).strip()
            if not state_id:
                self._send_json(400, {"error": "Missing state_id"})
                return
            subprocess.run(
                ["emu-dev-cli", "workflow", "approve", "--state", state_id],
                check=False,
                capture_output=True,
            )
            self._send_json(200, {"status": "approved", "state_id": state_id})
            return

        self._send_json(404, {"error": f"Unknown POST route: {parsed.path}"})


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), HarnessSidecarHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
