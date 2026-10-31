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

from http.server import ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import ModuleType
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


PORT: int = int(os.environ.get("ANTIGRAVITY_SIDECAR_WEB_PORT", "8765"))
UI_TOKEN: str = os.environ.get("ANTIGRAVITY_SIDECAR_UI_TOKEN", "")
UI_TOKEN_HINT: str = "Open this panel from Jetski, which supplies ANTIGRAVITY_SIDECAR_UI_TOKEN."
WEB_SECURITY_FILENAME: str = "web_security.py"
CONFIGURED_SRC_DIR: str = "__EMU_DEV_CLI_SRC_DIR__"


def _resolve_cli_src_dir() -> Optional[Path]:
    """Locates the `emu-dev-cli/src` directory from configured or source-tree paths."""
    candidates: List[Path] = []
    if CONFIGURED_SRC_DIR and not CONFIGURED_SRC_DIR.startswith("__"):
        candidates.append(Path(CONFIGURED_SRC_DIR))
    here = Path(__file__).resolve()
    if len(here.parents) > 4:
        candidates.append(here.parents[4] / "src")
    home = Path.home()
    candidates.extend([
        home / ".android" / "emu-dev-cli" / "lib",
        home / ".android" / "emu-dev-cli" / "lib" / "src",
        home / ".android" / "emu-dev-cli" / "src",
        home / "tools" / "aemu" / "tools" / "emu-dev-cli" / "src",
    ])
    for cand in candidates:
        if (cand / "core" / "web" / "__init__.py").is_file():
            return cand
    return None


def _ensure_package_loaded(pkg_name: str, pkg_dir: Path) -> ModuleType:
    """Loads a package into `sys.modules` via `importlib.util` without mutating `sys.path`."""
    existing = sys.modules.get(pkg_name)
    if existing is not None:
        return existing
    init_py = pkg_dir / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        pkg_name,
        init_py,
        submodule_search_locations=[str(pkg_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load package {pkg_name} from {pkg_dir}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[pkg_name] = module
    spec.loader.exec_module(module)
    return module


def _load_core_web() -> ModuleType:
    """Imports `core.web` directly or via `importlib.util` package bootstrap."""
    try:
        import core.web as core_web_mod

        return core_web_mod
    except ImportError:
        src_dir = _resolve_cli_src_dir()
        if src_dir is None:
            raise
        _ensure_package_loaded("core", src_dir / "core")
        _ensure_package_loaded("clients", src_dir / "clients")
        return _ensure_package_loaded("core.web", src_dir / "core" / "web")


def _load_web_security() -> ModuleType:
    """Loads the CLI's request checks without importing the CLI.

    `emu-dev-cli harness install` copies `src/core/web/security.py` next to this file.
    When running from the source tree, the module is loaded from `src/core/web` instead.
    """
    here = Path(__file__).resolve()
    candidates = [here.with_name(WEB_SECURITY_FILENAME)]
    src_dir = _resolve_cli_src_dir()
    if src_dir is not None:
        candidates.append(src_dir / "core" / "web" / "security.py")
        candidates.append(src_dir / "lib" / WEB_SECURITY_FILENAME)
    for candidate in candidates:
        if not candidate.is_file():
            continue
        spec = importlib.util.spec_from_file_location("aemu_harness_web_security", candidate)
        if spec is not None and spec.loader is not None:
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
    raise ImportError(
        f"aemu-harness sidecar cannot find {WEB_SECURITY_FILENAME} (looked in "
        f"{', '.join(str(c) for c in candidates)}). Re-run 'emu-dev-cli harness install'."
    )


core_web = _load_core_web()
web_security = _load_web_security()
AgentProxy = core_web.AgentProxy
JsonRequestHandler = core_web.JsonRequestHandler
Router = core_web.Router
SecurityPolicy = core_web.SecurityPolicy
SIDECAR_PRELOAD_JS: str = core_web.SIDECAR_PRELOAD_JS
SIDECAR_PRELOAD_SCRIPT_TAG: str = core_web.SIDECAR_PRELOAD_SCRIPT_TAG
WebRequest = core_web.WebRequest

SEMANTIC_SUBJECT_RE = re.compile(
    r"^(feat|fix|build|docs|chore|test|refactor|perf|style)(?:\(([^)]+)\))?:\s+(.+)$"
)
BUG_FOOTER_RE = re.compile(r"(?im)^\s*Bug:\s*(?:b/)?(\d+|N/A|None)\s*$")
TEST_FOOTER_RE = re.compile(r"(?im)^\s*Test:\s*(\S.*?)\s*$")

LIBS_CATALOG_PATH: Path = Path(__file__).with_name("libs_catalog.json")


def _load_libs_catalog() -> List[Dict[str, str]]:
    """Loads the reusable-library catalog rendered next to this file.

    The sidecar runs standalone and cannot import the CLI, so `emu-dev-cli harness install`
    writes `libs_catalog.json` from `commands.harness.goldfish_libs.REUSABLE_LIBS_CATALOG`.
    Returns an empty list, with a warning, if the file is missing or malformed.
    """
    try:
        catalog = json.loads(LIBS_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        catalog = None
        reason = str(exc)
    else:
        reason = f"expected a JSON list, got {type(catalog).__name__}"
    if isinstance(catalog, list):
        return catalog
    print(
        f"warning: aemu-harness sidecar could not load the library catalog "
        f"'{LIBS_CATALOG_PATH}' ({reason}), so the Libraries panel is empty. "
        "Re-run 'emu-dev-cli harness install' to regenerate it.",
        file=sys.stderr,
    )
    return []


SIDE_CAR_LIBS_CATALOG: List[Dict[str, str]] = _load_libs_catalog()


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
        raw_state = data.get("state") or data.get("status", "UNKNOWN")
        raw_step = data.get("step") if data.get("step") is not None else data.get("current_step", 0)
        sessions.append({
            "workflow_name": data.get("workflow_name") or sf.parent.name,
            "state_id": data.get("state_id") or sf.stem.replace("STATE-", ""),
            "status": str(raw_state).upper(),
            "current_step": int(raw_step),
            "retries": int(data.get("retries", 0)),
            "conversation_id": wf_conv,
            "matches_conversation": bool(conversation_id and wf_conv == conversation_id),
            "updated_at": str(data.get("updated_at", "")),
        })
    return sessions


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
        encoding="utf-8",
        errors="replace",
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


_HARNESS_ROUTER = Router()
_HARNESS_AGENT_PROXY = AgentProxy(invoker=_run_sidecar_agentapi)
_HARNESS_AGENT_PROXY.register_routes(_HARNESS_ROUTER)


class HarnessSidecarHandler(JsonRequestHandler):
    """HTTP handler for the AEMU Harness Jetski Web AuxPane UI sidecar (`core.web`)."""

    security_policy = SecurityPolicy(
        expected_token="",
        token_hint=UI_TOKEN_HINT,
        public_get_routes=frozenset({"", "/", "/index.html", "/preload.js"}),
        protected_get_prefixes=("/api/",),
        enable_cors=False,
    )
    router = _HARNESS_ROUTER

    def _resolve_expected_token(self) -> str:
        """Returns the configured UI token from the handler, server, or module global."""
        if self.ui_token is not None:
            return str(self.ui_token)
        server_token = getattr(self.server, "ui_token", None)
        if server_token is not None:
            return str(server_token)
        return UI_TOKEN

    def handle_get_preload_js(self, _request: WebRequest) -> None:
        """Serves `/preload.js`."""
        self.send_javascript(SIDECAR_PRELOAD_JS)

    def handle_get_index(self, _request: WebRequest) -> None:
        """Serves `/` and `/index.html`."""
        self.send_html(_render_harness_index_html())

    def handle_get_state(self, request: WebRequest) -> None:
        """Serves `GET /api/state`."""
        conv_id = request.query_opt_str("conversationId")
        self.send_json(
            {
                "ledger": load_ledger_data(conv_id),
                "workflows": list_workflow_sessions(conv_id),
                "libraries": SIDE_CAR_LIBS_CATALOG,
            }
        )

    def handle_post_commit_validate(self, request: WebRequest) -> None:
        """Serves `POST /api/commit/validate`."""
        msg = request.body_str("message")
        self.send_json(validate_commit_message_live(msg))

    def handle_post_workflow_approve(self, request: WebRequest) -> None:
        """Serves `POST /api/workflow/approve`."""
        state_id = request.body_str("state_id", strip=True)
        if not state_id:
            self.send_json({"error": "Missing state_id"}, status=400)
            return
        try:
            res = subprocess.run(
                ["emu-dev-cli", "workflow", "approve", "--state", state_id],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            self.send_json(
                {
                    "error": f"Failed to execute 'emu-dev-cli workflow approve --state {state_id}': {exc}",
                    "state_id": state_id,
                },
                status=500,
            )
            return
        if res.returncode != 0:
            detail = (res.stderr or res.stdout or f"exit code {res.returncode}").strip()
            self.send_json(
                {
                    "error": (
                        f"Failed to approve workflow state '{state_id}' "
                        f"(exit code {res.returncode}): {detail}"
                    ),
                    "state_id": state_id,
                    "exit_code": res.returncode,
                },
                status=500,
            )
            return
        self.send_json({"status": "approved", "state_id": state_id})


_HARNESS_ROUTER.add_get("/preload.js", HarnessSidecarHandler.handle_get_preload_js)
_HARNESS_ROUTER.add_get("/", HarnessSidecarHandler.handle_get_index)
_HARNESS_ROUTER.add_get("/index.html", HarnessSidecarHandler.handle_get_index)
_HARNESS_ROUTER.add_get("/api/state", HarnessSidecarHandler.handle_get_state)
_HARNESS_ROUTER.add_post(
    "/api/commit/validate", HarnessSidecarHandler.handle_post_commit_validate
)
_HARNESS_ROUTER.add_post(
    "/api/workflow/approve", HarnessSidecarHandler.handle_post_workflow_approve
)


def main() -> None:
    if not UI_TOKEN:
        print(
            "warning: ANTIGRAVITY_SIDECAR_UI_TOKEN is unset, so the aemu-harness sidecar rejects "
            "every API request. Start it from Jetski, which sets the token.",
            file=sys.stderr,
        )
    server = ThreadingHTTPServer(("127.0.0.1", PORT), HarnessSidecarHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
