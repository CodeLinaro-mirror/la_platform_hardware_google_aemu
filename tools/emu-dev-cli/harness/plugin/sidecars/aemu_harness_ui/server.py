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
import re
import shutil
import subprocess
import urllib.parse
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


PORT: int = int(os.environ.get("ANTIGRAVITY_SIDECAR_WEB_PORT", "8765"))
UI_TOKEN: str = os.environ.get("ANTIGRAVITY_SIDECAR_UI_TOKEN", "")

SEMANTIC_SUBJECT_RE = re.compile(
    r"^(feat|fix|build|docs|chore|test|refactor|perf|style)(?:\(([^)]+)\))?:\s+(.+)$"
)
BUG_FOOTER_RE = re.compile(r"(?im)^\s*Bug:\s*(?:b/)?(\d+|N/A|None)\s*$")
TEST_FOOTER_RE = re.compile(r"(?im)^\s*Test:\s*(\S.*?)\s*$")

SIDE_CAR_LIBS_CATALOG: List[Dict[str, str]] = [
    {
        "name": "file",
        "bazel_target": "//emulator/libs/file",
        "header": "goldfish/file/file.h",
        "namespace": "android::base::file",
        "summary": "Cross-platform UTF-8 file I/O helpers safe on Windows non-ASCII paths.",
    },
    {
        "name": "ini_file",
        "bazel_target": "//emulator/libs/ini_file",
        "header": "android/goldfish/ini_file.h",
        "namespace": "android::goldfish::IniFile",
        "summary": "INI configuration parser and writer for AVD and hardware profiles.",
    },
    {
        "name": "http",
        "bazel_target": "//emulator/libs/http",
        "header": "goldfish/http/web_server.h",
        "namespace": "goldfish::http::WebServer",
        "summary": "Embedded HTTP WebServer (`CreateWebServer`) for production and unit tests.",
    },
    {
        "name": "http_client",
        "bazel_target": "//emulator/libs/network:http_client",
        "header": "goldfish/network/http_client.h",
        "namespace": "goldfish::network::HttpClient",
        "summary": "HTTP/HTTPS client with StatusOr error propagation.",
    },
    {
        "name": "network",
        "bazel_target": "//emulator/libs/network",
        "header": "goldfish/network/resolve.h",
        "namespace": "goldfish::network::ResolveEndpoints",
        "summary": "DNS and socket endpoint resolution helpers.",
    },
    {
        "name": "async",
        "bazel_target": "//emulator/libs/async",
        "header": "goldfish/async/threaded_event_loop.h",
        "namespace": "goldfish::async",
        "summary": "LibuvEventLoop, ThreadedEventLoop, AsyncSocket, and UvErrToAbslStatus.",
    },
    {
        "name": "system",
        "bazel_target": "//emulator/libs/system",
        "header": "android/base/system.h",
        "namespace": "android::base::System",
        "summary": "Environment variable queries (`EnvGet`) and `TestSystem` (`:test-headers`).",
    },
]


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


def validate_commit_message_live(message: str) -> Dict[str, Any]:
    """Validates a commit message against Erwin's Semantic Commit Message rules."""
    raw = (message or "").strip()
    if not raw:
        return {
            "valid": False,
            "subject_length": 0,
            "max_body_line_length": 0,
            "errors": ["Commit message is empty."],
        }

    lines = raw.splitlines()
    subject = lines[0].strip()
    errors: List[str] = []

    if len(subject) > 50:
        errors.append(f"Subject length ({len(subject)}) exceeds 50-char limit.")

    match = SEMANTIC_SUBJECT_RE.match(subject)
    if not match:
        errors.append(
            "Subject must match '<type>[optional scope]: <Description>' "
            "(feat|fix|build|docs|chore|test|refactor|perf|style)."
        )
    else:
        desc = match.group(3).strip()
        if not desc or not desc[0].isupper():
            errors.append("Subject <Description> must start with a capital letter.")
        if desc.endswith("."):
            errors.append("Subject must not end with a period '.'.")

    if len(lines) > 1 and lines[1].strip():
        errors.append("Line 2 must be a blank line separating subject and body.")

    body_lines = [
        ln
        for ln in lines[2:]
        if ln.strip()
        and not re.match(r"^(Bug|Test|TEST|Change-Id|BREAKING CHANGE):", ln.strip())
    ]
    if not body_lines:
        errors.append(
            "Missing explanatory body paragraph between subject and footers (what & why)."
        )

    max_body_len = max((len(ln) for ln in body_lines), default=0)
    if max_body_len > 72:
        errors.append(f"Body line length ({max_body_len}) exceeds 72-char limit.")

    if not BUG_FOOTER_RE.search(raw):
        errors.append("Missing required 'Bug: <id>' or 'Bug: N/A' footer.")
    if not TEST_FOOTER_RE.search(raw):
        errors.append("Missing required 'Test: <command>' footer.")
    if re.search(r"(?i)\b(TAG|CONV)\s*=", raw):
        errors.append("Forbidden 'Tag=' or 'CONV=' tag present.")

    return {
        "valid": len(errors) == 0,
        "subject_length": len(subject),
        "max_body_line_length": max_body_len,
        "errors": errors,
    }


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

    if not conversation_id and sessions_dir.is_dir():
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
            "commands_rewritten": 0,
            "diagnostics_injected": 0,
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


SIDECAR_PRELOAD_JS: str = """(function() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token') || '';
  const conversationId = params.get('conversationId') || '';

  let currentWorkspaceUris = [];
  let hasInitialWorkspace = false;
  const workspaceListeners = new Set();
  let resolveInitialWorkspace;
  const initialWorkspacePromise = new Promise((resolve) => {
    resolveInitialWorkspace = resolve;
  });

  window.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'theme-change') {
      const vars = (event.data.payload && event.data.payload.cssVariables) || {};
      for (const [key, value] of Object.entries(vars)) {
        document.documentElement.style.setProperty(key, value);
      }
    } else if (event.data && event.data.type === 'workspace-change') {
      const payload = event.data.payload || {};
      currentWorkspaceUris = Array.isArray(payload.workspaceUris)
        ? payload.workspaceUris.slice()
        : [];
      hasInitialWorkspace = true;
      resolveInitialWorkspace();
      for (const listener of workspaceListeners) {
        try {
          listener(currentWorkspaceUris.slice());
        } catch (err) {
          console.error('workspace-change listener failed:', err);
        }
      }
    }
  });

  function secureFetch(url, options = {}) {
    let targetUrl = url;
    if (token && !/[?&]token=/.test(targetUrl)) {
      const sep = targetUrl.includes('?') ? '&' : '?';
      targetUrl = `${targetUrl}${sep}token=${encodeURIComponent(token)}`;
    }
    const headers = new Headers(options.headers || {});
    headers.set('Content-Type', 'application/json');
    if (token) {
      headers.set('X-Sidecar-Token', token);
    }
    return fetch(targetUrl, Object.assign({}, options, { headers }));
  }

  const projectIdPromise = (async () => {
    if (!conversationId) return '';
    try {
      const res = await secureFetch('/_sidecar/get-conversation-metadata', {
        method: 'POST',
        body: JSON.stringify({ conversationId })
      });
      if (!res.ok) return '';
      const data = await res.json();
      const metaEvent = (data && data.response && (data.response.conversationMetadata || data.response.conversation_metadata)) || {};
      const meta = metaEvent.metadata || {};
      return meta.projectId || meta.project_id || '';
    } catch (e) {
      return '';
    }
  })();

  async function getWorkspaceUris() {
    if (window.parent === window) {
      return [];
    }
    await initialWorkspacePromise;
    return currentWorkspaceUris.slice();
  }

  window.sidecar = {
    conversationId,
    fetch: secureFetch,
    getWorkspaceUris,
    onWorkspaceChange(listener) {
      if (typeof listener !== 'function') return () => {};
      workspaceListeners.add(listener);
      if (hasInitialWorkspace) {
        queueMicrotask(() => {
          if (workspaceListeners.has(listener)) {
            listener(currentWorkspaceUris.slice());
          }
        });
      }
      return () => { workspaceListeners.delete(listener); };
    },
    agent: {
      async sendMessage(message, convId) {
        const resolvedProjectId = await projectIdPromise;
        const res = await secureFetch('/_sidecar/send-message', {
          method: 'POST',
          body: JSON.stringify({
            conversationId: convId || conversationId,
            message,
            projectId: resolvedProjectId
          })
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
      },
      async startConversation(message, title) {
        const resolvedProjectId = await projectIdPromise;
        const res = await secureFetch('/_sidecar/new-conversation', {
          method: 'POST',
          body: JSON.stringify({ message, title, projectId: resolvedProjectId })
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
      },
      async getConversationMetadata(convId) {
        const res = await secureFetch('/_sidecar/get-conversation-metadata', {
          method: 'POST',
          body: JSON.stringify({ conversationId: convId || conversationId })
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
      }
    },
    ui: {
      toggleAuxPane(request) {
        window.parent.postMessage({ type: 'toggle-aux-pane', payload: request }, '*');
      },
      toggleConversation(convId) {
        window.parent.postMessage({ type: 'toggle-conversation', payload: { conversationId: convId } }, '*');
      }
    }
  };
})();
"""

SIDECAR_PRELOAD_SCRIPT_TAG: str = '<script src="/preload.js"></script>'


def _run_sidecar_agentapi(args: List[str], project_id: Optional[str] = None) -> Dict[str, Any]:
    """Invokes `agentapi` for Jetski Web sidecar conversation integration."""
    agentapi_bin = shutil.which("agentapi")
    if not agentapi_bin:
        cand = Path.home() / ".gemini" / "jetski" / "bin" / "agentapi"
        agentapi_bin = str(cand) if cand.is_file() else "agentapi"
    env = os.environ.copy()
    if project_id:
        env["ANTIGRAVITY_PROJECT_ID"] = project_id
    res = subprocess.run(
        [agentapi_bin, *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=30,
    )
    if res.returncode != 0:
        raise RuntimeError(f"agentapi failed: {(res.stderr or '').strip()}")
    out = (res.stdout or "").strip()
    return json.loads(out) if out else {}


def _render_harness_index_html() -> str:
    """Inlines `SIDECAR_PRELOAD_JS` so cross-site Cloudtop gateways never block `/preload.js`."""
    return INDEX_HTML.replace(
        SIDECAR_PRELOAD_SCRIPT_TAG, f"<script>\n{SIDECAR_PRELOAD_JS}\n</script>"
    )


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
      margin-bottom: 10px;
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
    .tabs {
      display: flex;
      gap: 6px;
      margin-bottom: 12px;
      border-bottom: 1px solid var(--border-col);
      padding-bottom: 8px;
    }
    .tab-btn {
      background: transparent;
      border: 1px solid transparent;
      padding: 5px 10px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 600;
      color: var(--muted);
      cursor: pointer;
    }
    .tab-btn.active {
      background: rgba(61, 220, 132, 0.14);
      border-color: #3ddc84;
      color: var(--fg);
    }
    .tab-pane { display: none; }
    .tab-pane.active { display: block; }
    .grid-4 {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 6px;
      margin-bottom: 12px;
    }
    .stat-card {
      background: var(--card-bg);
      border: 1px solid var(--border-col);
      border-radius: 8px;
      padding: 7px 6px;
      text-align: center;
    }
    .stat-val {
      font-size: 16px;
      font-weight: 700;
      color: #3ddc84;
    }
    .stat-lbl {
      font-size: 10px;
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
      font-size: 11px;
      font-weight: 700;
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
    textarea, input[type="text"] {
      width: 100%;
      background: rgba(15, 23, 42, 0.65);
      color: var(--fg);
      border: 1px solid var(--border-col);
      border-radius: 6px;
      padding: 8px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
      margin-bottom: 8px;
    }
    .wf-item, .ev-item, .lib-item {
      padding: 6px 0;
      border-bottom: 1px solid var(--border-col);
      font-size: 12px;
    }
    .wf-item:last-child, .ev-item:last-child, .lib-item:last-child {
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
    .ok-text { color: #3ddc84; font-weight: 600; }
    .err-text { color: #f87171; }
  </style>
</head>
<body>
  <div class="header">
    <div class="title">
      <span>🤖 AEMU Agent Harness</span>
    </div>
    <span class="badge" id="conv-badge">Active</span>
  </div>

  <div class="tabs">
    <button class="tab-btn active" onclick="switchTab('control', this)">Control Center</button>
    <button class="tab-btn" onclick="switchTab('commit', this)">Commit Sandbox</button>
    <button class="tab-btn" onclick="switchTab('libs', this)">//emulator/libs/*</button>
    <button class="tab-btn" onclick="switchTab('swarm', this)">Review Swarm</button>
  </div>

  <div id="tab-control" class="tab-pane active">
    <div class="grid-4">
      <div class="stat-card">
        <div class="stat-val" id="stat-commits">0</div>
        <div class="stat-lbl">Commits OK</div>
      </div>
      <div class="stat-card">
        <div class="stat-val" id="stat-blocked">0</div>
        <div class="stat-lbl">Blocked</div>
      </div>
      <div class="stat-card">
        <div class="stat-val" id="stat-rewrites">0</div>
        <div class="stat-lbl">Rewrites</div>
      </div>
      <div class="stat-card">
        <div class="stat-val" id="stat-emus">0</div>
        <div class="stat-lbl">Running AVDs</div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">⚡ One-Click Workflow & Swarm Dispatch</div>
      <div class="btn-grid">
        <button onclick="dispatchPrompt('Run `emu-dev-cli workflow create-commit init` to create a Semantic Commit for my current changes.')">
          📝 Create Semantic Commit
        </button>
        <button onclick="dispatchPrompt('Run `emu-dev-cli workflow amend-commit init` to amend HEAD with a verified Semantic Commit.')">
          ✏️ Amend HEAD Commit
        </button>
        <button onclick="dispatchPrompt('Run `emu-dev-cli harness review-swarm` to launch the 8-Specialist Single-Wave Parallel Code-Review Swarm on my current commit/CL.')">
          🐝 8-Agent Review Swarm
        </button>
        <button onclick="dispatchPrompt('Run `emu-dev-cli workflow address-review init` to fetch and resolve open Gerrit review comments on my CL.')">
          💬 Address Gerrit Review
        </button>
        <button onclick="dispatchPrompt('Invoke `aemu-flake-hunter` to inspect recent flake history and reproduce the flaky test locally with 50x TSAN/ASAN stress runs.')">
          🧪 Hunt & Repro Flake
        </button>
        <button onclick="dispatchPrompt('Run `emu-dev-cli crash list-crashes --unassigned-only` and summarize top unassigned production crashes.')">
          💥 Triage Crashpad
        </button>
      </div>
    </div>

    <div class="section">
      <div class="section-title">🔄 Active emu-dev-cli Workflows</div>
      <div id="workflows-list" class="muted">No active workflows detected.</div>
    </div>

    <div class="section">
      <div class="section-title">🛡️ Harness Guardrail & Hook Ledger</div>
      <div id="events-list" class="muted">No hook events recorded in this session yet.</div>
    </div>
  </div>

  <div id="tab-commit" class="tab-pane">
    <div class="section">
      <div class="section-title">
        <span>📝 Live Semantic Commit Validator</span>
        <span id="commit-rulers" class="muted">Subject: 0/50 · Body: 0/72</span>
      </div>
      <textarea id="commit-input" rows="8" oninput="checkCommitLive()" placeholder="feat(scope): Capitalized imperative summary&#10;&#10;Explain what changed and why (wrapped at <= 72 chars).&#10;&#10;Bug: 485291270&#10;Test: bazel test //emulator/..."></textarea>
      <div id="commit-feedback" class="muted" style="margin-bottom:8px;">Type a commit message above to validate against Semantic Commit rules.</div>
      <button onclick="sendVerifiedCommit()">🚀 Send Verified Commit Message to Agent</button>
    </div>
  </div>

  <div id="tab-libs" class="tab-pane">
    <div class="section">
      <div class="section-title">📚 //emulator/libs/* Reuse Explorer</div>
      <input type="text" id="libs-search" oninput="renderLibs()" placeholder="Filter by keyword (e.g. http, file, ini, async, system)..." />
      <div id="libs-list"></div>
    </div>
  </div>

  <div id="tab-swarm" class="tab-pane">
    <div class="section">
      <div class="section-title">🐝 8-Specialist Parallel Code-Review Swarm</div>
      <input type="text" id="swarm-target" value="HEAD" placeholder="Gerrit CL (e.g. 43137255, ag/43137255) or git ref (HEAD)" />
      <div class="btn-grid" style="margin-bottom:8px;">
        <button onclick="launchReviewSwarm('workflow')">⚡ Launch 8-Agent run_workflow</button>
        <button onclick="launchReviewSwarm('swarm')">🐝 Launch 8-Agent Native Swarm</button>
      </div>
      <div class="muted">
        Specialists: (1) Commit &amp; Bug, (2) Duplication &amp; Reuse, (3) Modularity, (4) Control Flow,
        (5) Named Constants, (6) Unicode &amp; Platform, (7) Error Messages &amp; StatusOr, (8) Concurrency &amp; Safety.
      </div>
    </div>
  </div>

  <script>
    const urlParams = new URLSearchParams(window.location.search);
    const uiToken = urlParams.get("token") || "";
    let currentConvId = urlParams.get("conversationId") || "";
    let cachedLibs = [];

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

    function switchTab(name, btnEl) {
      document.querySelectorAll(".tab-pane").forEach(el => el.classList.remove("active"));
      document.querySelectorAll(".tab-btn").forEach(el => el.classList.remove("active"));
      document.getElementById("tab-" + name).classList.add("active");
      btnEl.classList.add("active");
    }

    function dispatchPrompt(text) {
      if (window.sidecar && window.sidecar.agent && typeof window.sidecar.agent.sendMessage === "function") {
        window.sidecar.agent.sendMessage(text);
      } else {
        navigator.clipboard.writeText(text);
        alert("Copied prompt to clipboard:\\n\\n" + text);
      }
    }

    async function checkCommitLive() {
      const msg = document.getElementById("commit-input").value;
      const res = await fetch("/api/commit/validate?token=" + encodeURIComponent(uiToken), {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Sidecar-Token": uiToken },
        body: JSON.stringify({ message: msg })
      });
      if (!res.ok) return;
      const data = await res.json();
      document.getElementById("commit-rulers").textContent =
        `Subject: ${data.subject_length}/50 · Max Body Line: ${data.max_body_line_length}/72`;
      const fb = document.getElementById("commit-feedback");
      if (data.valid) {
        fb.innerHTML = '<span class="ok-text">✓ Valid Semantic Commit message!</span>';
      } else {
        fb.innerHTML = (data.errors || []).map(e => `<div class="err-text">• ${e}</div>`).join("");
      }
    }

    function sendVerifiedCommit() {
      const msg = document.getElementById("commit-input").value.trim();
      if (!msg) return;
      dispatchPrompt("Commit the current staged changes using this exact Semantic Commit message:\\n\\n```\\n" + msg + "\\n```");
    }

    function launchReviewSwarm(mode) {
      const target = (document.getElementById("swarm-target").value || "HEAD").trim();
      dispatchPrompt(
        `Run \\`emu-dev-cli harness review-swarm --target "${target}" --mode ${mode}\\` to prepare the 8-Specialist Single-Wave Parallel Code-Review Swarm, then execute the generated workflow script via \\`run_workflow\\` and present the diff-bounded Markdown report and Gerrit JSON payload.`
      );
    }

    function renderLibs() {
      const q = (document.getElementById("libs-search").value || "").toLowerCase().trim();
      const container = document.getElementById("libs-list");
      const filtered = cachedLibs.filter(item =>
        !q || JSON.stringify(item).toLowerCase().includes(q)
      );
      container.innerHTML = filtered.map(lib => `
        <div class="lib-item">
          <div><strong>${lib.bazel_target}</strong> · <code>${lib.header}</code></div>
          <div class="muted">${lib.namespace} — ${lib.summary}</div>
        </div>
      `).join("");
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

        if (data.libraries && cachedLibs.length === 0) {
          cachedLibs = data.libraries;
          renderLibs();
        }

        document.getElementById("conv-badge").textContent =
          (ledger.conversation_id || currentConvId || "global").slice(0, 12);
        document.getElementById("stat-commits").textContent = stats.commits_verified || 0;
        document.getElementById("stat-blocked").textContent = stats.guardrails_blocked || 0;
        document.getElementById("stat-rewrites").textContent = stats.commands_rewritten || 0;
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
            js = SIDECAR_PRELOAD_JS.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(js)))
            self.end_headers()
            self.wfile.write(js)
            return

        if route in ("/", "/index.html"):
            body = _render_harness_index_html().encode("utf-8")
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
                    "libraries": SIDE_CAR_LIBS_CATALOG,
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

        try:
            if parsed.path == "/_sidecar/get-conversation-metadata":
                conv_id = str(
                    body.get("conversationId")
                    or os.environ.get("ANTIGRAVITY_SIDECAR_CONVERSATION_ID")
                    or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
                    or ""
                ).strip()
                if not conv_id:
                    self._send_json(400, {"error": "Missing conversationId"})
                    return
                self._send_json(200, _run_sidecar_agentapi(["get-conversation-metadata", conv_id]))
                return

            if parsed.path == "/_sidecar/send-message":
                msg = str(body.get("message") or "").strip()
                conv_id = str(
                    body.get("conversationId")
                    or os.environ.get("ANTIGRAVITY_SIDECAR_CONVERSATION_ID")
                    or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
                    or ""
                ).strip()
                if not msg or not conv_id:
                    self._send_json(400, {"error": "Missing message or conversationId"})
                    return
                args = ["send-message"]
                if body.get("title"):
                    args.extend(["--title", str(body["title"])])
                args.extend([conv_id, msg])
                self._send_json(200, _run_sidecar_agentapi(args, project_id=body.get("projectId")))
                return

            if parsed.path == "/_sidecar/new-conversation":
                msg = str(body.get("message") or "").strip()
                args = ["new-conversation"]
                if body.get("title"):
                    args.extend(["--title", str(body["title"])])
                if msg:
                    args.append(msg)
                self._send_json(200, _run_sidecar_agentapi(args, project_id=body.get("projectId")))
                return
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})
            return

        if parsed.path == "/api/commit/validate":
            msg = str(body.get("message") or "")
            self._send_json(200, validate_commit_message_live(msg))
            return

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
