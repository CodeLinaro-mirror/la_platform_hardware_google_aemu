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

"""HTTP REST endpoint handlers and static HTML rendering for AEMU Fast Gerrit UI."""

from __future__ import annotations

import hashlib
import http.server
import json
import os
from pathlib import Path
import socket
from typing import Any, Callable
import urllib.parse
import webbrowser

from commands.gerrit.services.diff_parser import (
    _candidate_gerrit_ui_template_paths,
    get_gerrit_ui_template_path,
    get_gerrit_ui_template_version,
    register_ui_hook,
    resolve_ui_hook,
)
from commands.gerrit.services.inline_qa_service import _run_sidecar_agentapi
from commands.gerrit.services.queue_service import (
    TEAM_ROSTER_EMAILS,
    _normalize_viewer_email,
)
from lib.gerrit_api import DEFAULT_GERRIT_HOST
from lib.web_security import (
    SIDECAR_TOKEN_ENV,
    RequestRejected,
    mint_ui_token,
    read_json_body,
    require_ui_token,
)

DEFAULT_GERRIT_UI_PORT = 8085
MAX_PORT_PROBES = 50
# Only the sidecar bridge script is public. The page itself embeds the viewer's email, and
# both the Jetski iframe URL and the standalone URL carry `?token=`.
PUBLIC_GET_ROUTES: frozenset[str] = frozenset({"/preload.js"})
_UI_TOKEN_HINT = (
    "Open the Gerrit UI from the Jetski side panel, or through the URL printed by "
    "'emu-dev-cli gerrit ui' (it ends with ?token=...)."
)


SIDECAR_PRELOAD_SCRIPT_TAG: str = '<script src="/preload.js"></script>'

SIDECAR_PRELOAD_JS: str = """(function() {
  const searchStr = (typeof window !== 'undefined' && window.location && window.location.search) || '';
  const params = typeof URLSearchParams !== 'undefined' ? new URLSearchParams(searchStr) : { get: () => '' };
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
    const headers = typeof Headers !== 'undefined' ? new Headers(options.headers || {}) : Object.assign({}, options.headers || {});
    if (typeof headers.set === 'function') {
      headers.set('Content-Type', 'application/json');
      if (token) {
        headers.set('X-Sidecar-Token', token);
      }
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


def render_static_gerrit_ui_html(initial_payload: dict[str, Any] | None = None) -> str:
    """Renders `gerrit_ui.html` with inlined `window.sidecar` bridge and optional JSON state."""
    tpl_path = get_gerrit_ui_template_path()
    html = tpl_path.read_text(encoding="utf-8")
    inline_preload = f"<script>\n{SIDECAR_PRELOAD_JS}\n</script>"
    html = html.replace(SIDECAR_PRELOAD_SCRIPT_TAG, inline_preload)
    html = html.replace('"__GERRIT_UI_VERSION__"', json.dumps(get_gerrit_ui_template_version()))
    bootstrap_json = json.dumps(initial_payload or {}, ensure_ascii=False).replace("</", "<\\/")
    return html.replace("__GERRIT_UI_BOOTSTRAP_JSON__", bootstrap_json)


def _is_truthy_query_flag(qs: dict[str, list[str]], key: str) -> bool:
    """Returns `True` if query string parameter `key` is `"1"`, `"true"`, or `"yes"`."""
    return (qs.get(key) or [""])[0].strip().lower() in ("1", "true", "yes")


def make_gerrit_ui_handler(
    service: Any, ui_token: str | None = None
) -> type[http.server.BaseHTTPRequestHandler]:
    """Creates the HTTP request handler bound to `service`.

    Every route outside `PUBLIC_GET_ROUTES` requires `ui_token`, which defaults to
    `ANTIGRAVITY_SIDECAR_UI_TOKEN`. With no token configured, those routes reject
    every request.
    """
    expected_token = os.environ.get(SIDECAR_TOKEN_ENV, "") if ui_token is None else ui_token

    class GerritUIHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _require_token(self, parsed: urllib.parse.ParseResult) -> None:
            require_ui_token(expected_token, self.headers, parsed.query, _UI_TOKEN_HINT)

        def _send_raw_bytes(self, content: bytes, content_type: str, status: int = 200) -> None:
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _send_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self._send_raw_bytes(body, "application/json; charset=utf-8", status=status)

        def _read_json_body(self) -> dict[str, Any]:
            return read_json_body(self.headers, self.rfile)

        def _handle_get_preload(self, _qs: dict[str, list[str]]) -> None:
            self._send_raw_bytes(
                SIDECAR_PRELOAD_JS.encode("utf-8"),
                "application/javascript; charset=utf-8",
            )

        def _handle_get_index(self, qs: dict[str, list[str]]) -> None:
            req_viewer = _normalize_viewer_email((qs.get("viewer") or [""])[0])
            initial_state: dict[str, Any] = {
                "viewer": req_viewer or service.default_viewer,
                "roster": list(TEAM_ROSTER_EMAILS),
            }
            html_bytes = render_static_gerrit_ui_html(initial_payload=initial_state).encode("utf-8")
            self._send_raw_bytes(html_bytes, "text/html; charset=utf-8")

        def _handle_get_queue(self, qs: dict[str, list[str]]) -> None:
            viewer = (qs.get("viewer") or [""])[0]
            if _is_truthy_query_flag(qs, "prewarm_only"):
                scheduled = service.schedule_background_prewarm(viewer or None)
                self._send_json({"status": "ok", "prewarm_scheduled": scheduled})
                return
            query = (qs.get("query") or [""])[0]
            scope = (qs.get("scope") or ["reviewer"])[0]
            author = (qs.get("author") or [""])[0]
            fast_flag = _is_truthy_query_flag(qs, "fast")
            refresh_flag = _is_truthy_query_flag(qs, "refresh")
            data = service.fetch_queue(
                viewer_email=viewer or None,
                query=query or None,
                scope=scope,
                author=author or None,
                fast=fast_flag,
                use_cache=not refresh_flag,
            )
            if _is_truthy_query_flag(qs, "prewarm") or not fast_flag:
                service.schedule_background_prewarm(data.get("viewer") or viewer or None)
            self._send_json(data)

        def _handle_get_diffs(self, qs: dict[str, list[str]]) -> None:
            raw_cls = (qs.get("cls") or [""])[0]
            cl_list = [x.strip() for x in raw_cls.split(",") if x.strip()]
            diffs = service.fetch_multi_commit_diffs(cl_list)
            self._send_json({"diffs": diffs, "ui_version": get_gerrit_ui_template_version()})

        def _handle_get_swarm_status(self, qs: dict[str, list[str]]) -> None:
            raw_cls = (qs.get("cls") or [""])[0]
            cl_list = [x.strip() for x in raw_cls.split(",") if x.strip()]
            self._send_json(
                {
                    "statuses": service.get_swarm_statuses(cl_list),
                    "ui_version": get_gerrit_ui_template_version(),
                }
            )

        def _handle_get_code_questions(self, qs: dict[str, list[str]]) -> None:
            raw_cls = (qs.get("cls") or [""])[0]
            conv_id = (qs.get("conversationId") or [""])[0].strip() or None
            cl_list = [x.strip() for x in raw_cls.split(",") if x.strip()]
            self._send_json(
                {"questions": service.get_code_questions(cl_list, conversation_id=conv_id)}
            )

        def _handle_get_suggest_reviewers(self, qs: dict[str, list[str]]) -> None:
            cl_num = (qs.get("cl") or [""])[0]
            query_str = (qs.get("q") or [""])[0]
            self._send_json({"suggestions": service.suggest_reviewers(cl_num, query=query_str)})

        def _handle_get_presubmit_targets(self, qs: dict[str, list[str]]) -> None:
            cl_num = (qs.get("cl") or [""])[0].strip()
            if not cl_num:
                self._send_json(
                    {"error": "Missing required `cl` query parameter for `/api/gerrit/presubmit-targets`."},
                    status=400,
                )
                return
            raw_ps = (qs.get("ps") or [""])[0].strip()
            ps_val = int(raw_ps) if raw_ps.isdigit() else None
            proj = (qs.get("project") or [""])[0].strip()
            refresh_flag = _is_truthy_query_flag(qs, "refresh")
            self._send_json(
                service.get_presubmit_targets(
                    cl_num,
                    patchset=ps_val,
                    project=proj,
                    force_refresh=refresh_flag,
                )
            )

        def _handle_get_ai_analysis(self, qs: dict[str, list[str]]) -> None:
            cl_num = (qs.get("cl") or [""])[0].strip()
            if not cl_num:
                self._send_json(
                    {
                        "error": "Missing required `cl` query parameter for `/api/gerrit/ai-analysis` "
                        "(pass e.g. `?cl=43256612&ps=1`)."
                    },
                    status=400,
                )
                return
            raw_ps = (qs.get("ps") or [""])[0].strip()
            ps_val = int(raw_ps) if raw_ps.isdigit() else None
            self._send_json(service.get_ai_analysis(cl_num, patchset=ps_val))

        def _handle_get_update_status(self, qs: dict[str, list[str]]) -> None:
            force_flag = _is_truthy_query_flag(qs, "force") or _is_truthy_query_flag(qs, "refresh")
            self._send_json(service.get_sidecar_update_status(force_fetch=force_flag))

        def _handle_post_sidecar_metadata(self, body: dict[str, Any]) -> None:
            conv_id = str(
                body.get("conversationId")
                or os.environ.get("ANTIGRAVITY_SIDECAR_CONVERSATION_ID")
                or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
                or ""
            ).strip()
            if not conv_id:
                self._send_json(
                    {
                        "error": (
                            "Missing conversationId for /api/sidecar/metadata: provide "
                            "'conversationId' in the JSON body or set ANTIGRAVITY_CONVERSATION_ID."
                        )
                    },
                    status=400,
                )
                return
            runner = resolve_ui_hook("_run_sidecar_agentapi", _run_sidecar_agentapi)
            self._send_json(runner(["get-conversation-metadata", conv_id]))

        def _handle_post_sidecar_send_message(self, body: dict[str, Any]) -> None:
            msg = str(body.get("message") or "").strip()
            conv_id = str(
                body.get("conversationId")
                or os.environ.get("ANTIGRAVITY_SIDECAR_CONVERSATION_ID")
                or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
                or ""
            ).strip()
            if not msg or not conv_id:
                self._send_json(
                    {
                        "error": (
                            "Missing message or conversationId for /api/sidecar/send-message: "
                            "provide non-empty 'message' and 'conversationId' fields in the JSON body."
                        )
                    },
                    status=400,
                )
                return
            args = ["send-message"]
            if body.get("title"):
                args.extend(["--title", str(body["title"])])
            args.extend([conv_id, msg])
            runner = resolve_ui_hook("_run_sidecar_agentapi", _run_sidecar_agentapi)
            self._send_json(runner(args, project_id=body.get("projectId")))

        def _handle_post_sidecar_new_conversation(self, body: dict[str, Any]) -> None:
            msg = str(body.get("message") or "").strip()
            if not msg:
                self._send_json(
                    {
                        "error": (
                            "Missing message for /api/sidecar/new-conversation: provide "
                            "a non-empty 'message' field in the JSON body."
                        )
                    },
                    status=400,
                )
                return
            args = ["new-conversation"]
            if body.get("title"):
                args.extend(["--title", str(body["title"])])
            args.append(msg)
            runner = resolve_ui_hook("_run_sidecar_agentapi", _run_sidecar_agentapi)
            self._send_json(runner(args, project_id=body.get("projectId")))

        def _handle_post_vote(self, body: dict[str, Any]) -> None:
            if "code_review" in body:
                cr_arg = int(body["code_review"]) if body["code_review"] is not None else None
            elif (
                "autosubmit" in body
                or "presubmit_ready" in body
                or str(body.get("message") or "").strip()
            ):
                cr_arg = None
            else:
                cr_arg = 2
            raw_as = body.get("autosubmit", False)
            as_arg: bool | int | None = (
                raw_as if isinstance(raw_as, bool) or raw_as is None else int(raw_as)
            )
            raw_pr = body.get("presubmit_ready", False)
            pr_arg: bool | int | None = (
                raw_pr if isinstance(raw_pr, bool) or raw_pr is None else int(raw_pr)
            )
            res = service.cast_batch_vote(
                cl_numbers=body.get("cls") or [],
                code_review=cr_arg,
                autosubmit=as_arg,
                presubmit_ready=pr_arg,
                message=str(body.get("message", "")),
            )
            self._send_json(res)

        def _handle_post_submit(self, body: dict[str, Any]) -> None:
            raw_cls = body.get("cls")
            if not isinstance(raw_cls, list):
                single_cl = body.get("cl")
                raw_cls = [single_cl] if single_cl is not None and str(single_cl).strip() else []
            res = service.submit_changes(cl_numbers=raw_cls)
            self._send_json(res, status=200 if res.get("status") == "ok" else 409)

        def _handle_post_assign_reviewer(self, body: dict[str, Any]) -> None:
            res = service.assign_second_human_reviewer(
                cl_number=body.get("cl", ""),
                reviewer_email=body.get("reviewer", ""),
            )
            self._send_json(res)

        def _handle_post_reviewer(self, body: dict[str, Any]) -> None:
            batch_cls = body.get("cls")
            if isinstance(batch_cls, list) and len(batch_cls) > 0:
                res = service.modify_reviewers_batch(
                    cl_numbers=batch_cls,
                    action=str(body.get("action") or "add"),
                    account=body.get("account") or body.get("reviewer") or "",
                    state=str(body.get("state") or "REVIEWER"),
                    viewer_email=str(body.get("viewer") or "").strip() or None,
                )
            else:
                res = service.modify_reviewer(
                    cl_number=body.get("cl", ""),
                    action=str(body.get("action") or "add"),
                    account=body.get("account") or body.get("reviewer") or "",
                    state=str(body.get("state") or "REVIEWER"),
                    viewer_email=str(body.get("viewer") or "").strip() or None,
                )
            self._send_json(res)

        def _handle_post_chat_dm(self, body: dict[str, Any]) -> None:
            res = service.send_reviewer_chat_dm(
                cl_number=body.get("cl", ""),
                recipient=str(body.get("recipient") or body.get("reviewer") or ""),
                message=str(body.get("message") or ""),
            )
            self._send_json(res)

        def _handle_post_harness(self, body: dict[str, Any]) -> None:
            res = service.run_harness_action(
                cl_number=body.get("cl", ""),
                action=body.get("action", "review-swarm"),
            )
            self._send_json(res)

        def _handle_post_swarm_review(self, body: dict[str, Any]) -> None:
            self._send_json(service.post_swarm_review(cl_number=body.get("cl", "")))

        def _handle_post_inline_comment(self, body: dict[str, Any]) -> None:
            res = service.post_inline_comment(
                cl_number=body.get("cl", ""),
                file_path=str(body.get("file_path") or body.get("file") or ""),
                line=int(body.get("line") or body.get("end_line") or 1),
                message=str(body.get("message") or ""),
                start_line=(
                    int(body["start_line"]) if body.get("start_line") is not None else None
                ),
                end_line=(int(body["end_line"]) if body.get("end_line") is not None else None),
                side=str(body.get("side") or "REVISION"),
                unresolved=bool(body.get("unresolved", True)),
            )
            self._send_json(res)

        def _handle_post_reply_comment(self, body: dict[str, Any]) -> None:
            raw_line = body.get("line")
            raw_ps = body.get("patch_set") if body.get("patch_set") is not None else body.get("ps")
            res = service.reply_inline_comment(
                cl_number=body.get("cl", ""),
                comment_id=str(body.get("comment_id") or body.get("in_reply_to") or ""),
                file_path=str(body.get("file_path") or body.get("file") or ""),
                message=str(body.get("message") or ""),
                line=(int(raw_line) if raw_line is not None and str(raw_line).isdigit() else None),
                patch_set=(int(raw_ps) if raw_ps is not None and str(raw_ps).isdigit() else None),
                side=str(body.get("side") or "REVISION"),
                unresolved=bool(body.get("unresolved", False)),
            )
            self._send_json(res)

        def _handle_post_ask_code(self, body: dict[str, Any]) -> None:
            res = service.ask_agent_about_code(
                cl_number=body.get("cl", ""),
                file_path=str(body.get("file_path") or body.get("file") or ""),
                start_line=int(body.get("start_line") or body.get("line") or 1),
                end_line=int(body.get("end_line") or body.get("line") or 1),
                code_snippet=str(body.get("code_snippet") or body.get("code") or ""),
                question=str(body.get("question") or ""),
                side=str(body.get("side") or "REVISION"),
                conversation_id=str(body.get("conversationId") or "").strip() or None,
                project_id=str(body.get("projectId") or "").strip() or None,
                spawn_new=bool(body.get("spawnNew", False)),
                thread_id=str(body.get("threadId") or "").strip() or None,
                function_name=str(
                    body.get("function_name") or body.get("functionName") or ""
                ).strip(),
            )
            self._send_json(res)

        def _handle_post_code_question_action(self, body: dict[str, Any]) -> None:
            res = service.modify_or_delete_code_question(
                action=str(body.get("action") or ""),
                cl_number=body.get("cl", ""),
                conversation_id=str(body.get("conversationId") or "").strip() or None,
                thread_id=str(body.get("threadId") or "").strip() or None,
                question=str(body.get("question") or ""),
                file_path=str(body.get("file_path") or body.get("file") or ""),
                start_line=int(body.get("start_line") or body.get("line") or 1),
                end_line=int(body.get("end_line") or body.get("line") or 1),
                code_snippet=str(body.get("code_snippet") or body.get("code") or ""),
                side=str(body.get("side") or "REVISION"),
                function_name=str(
                    body.get("function_name") or body.get("functionName") or ""
                ).strip(),
                project_id=str(body.get("projectId") or "").strip() or None,
            )
            self._send_json(res)

        def _handle_post_ai_analysis(self, body: dict[str, Any]) -> None:
            raw_ps = body.get("ps")
            ps_val = int(raw_ps) if raw_ps is not None and str(raw_ps).isdigit() else None
            res = service.trigger_or_cancel_ai_analysis(
                cl_number=body.get("cl", ""),
                action=str(body.get("action") or "analyze"),
                patchset=ps_val,
                project=str(body.get("project") or ""),
                subject=str(body.get("subject") or ""),
                project_id=str(body.get("projectId") or "").strip() or None,
                force=bool(body.get("force", False)),
            )
            self._send_json(res)

        def _handle_post_perform_update(self, body: dict[str, Any]) -> None:
            force_discard = bool(body.get("force_discard_local", False))
            res = service.perform_sidecar_update(force_discard_local=force_discard)
            http_status = 200 if res.get("status") == "ok" else 409
            self._send_json(res, status=http_status)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            get_routes: dict[str, Callable[[dict[str, list[str]]], None]] = {
                "/preload.js": self._handle_get_preload,
                "/": self._handle_get_index,
                "/index.html": self._handle_get_index,
                "/api/gerrit/queue": self._handle_get_queue,
                "/api/gerrit/diffs": self._handle_get_diffs,
                "/api/gerrit/swarm-status": self._handle_get_swarm_status,
                "/api/gerrit/code-questions": self._handle_get_code_questions,
                "/api/gerrit/suggest-reviewers": self._handle_get_suggest_reviewers,
                "/api/gerrit/presubmit-targets": self._handle_get_presubmit_targets,
                "/api/gerrit/ai-analysis": self._handle_get_ai_analysis,
                "/api/gerrit/update-status": self._handle_get_update_status,
            }
            try:
                if parsed.path not in PUBLIC_GET_ROUTES:
                    self._require_token(parsed)
                handler_fn = get_routes.get(parsed.path)
                if handler_fn is None:
                    self._send_json({"error": f"Unknown endpoint '{parsed.path}'"}, status=404)
                    return
                handler_fn(qs)
            except RequestRejected as exc:
                self._send_json({"error": str(exc)}, status=exc.status)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            post_routes: dict[str, Callable[[dict[str, Any]], None]] = {
                "/_sidecar/get-conversation-metadata": self._handle_post_sidecar_metadata,
                "/_sidecar/send-message": self._handle_post_sidecar_send_message,
                "/_sidecar/new-conversation": self._handle_post_sidecar_new_conversation,
                "/api/gerrit/vote": self._handle_post_vote,
                "/api/gerrit/submit": self._handle_post_submit,
                "/api/gerrit/assign-reviewer": self._handle_post_assign_reviewer,
                "/api/gerrit/reviewer": self._handle_post_reviewer,
                "/api/gerrit/chat-dm": self._handle_post_chat_dm,
                "/api/gerrit/harness": self._handle_post_harness,
                "/api/gerrit/post-swarm-review": self._handle_post_swarm_review,
                "/api/gerrit/comment": self._handle_post_inline_comment,
                "/api/gerrit/reply": self._handle_post_reply_comment,
                "/api/gerrit/ask-code": self._handle_post_ask_code,
                "/api/gerrit/code-question-action": self._handle_post_code_question_action,
                "/api/gerrit/ai-analysis": self._handle_post_ai_analysis,
                "/api/gerrit/perform-update": self._handle_post_perform_update,
            }
            try:
                self._require_token(parsed)
                body = self._read_json_body()
                handler_fn = post_routes.get(parsed.path)
                if handler_fn is None:
                    self._send_json({"error": f"Unknown POST endpoint '{parsed.path}'"}, status=404)
                    return
                handler_fn(body)
            except RequestRejected as exc:
                self._send_json({"error": str(exc)}, status=exc.status)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)

    return GerritUIHandler


def find_available_port(host: str, start_port: int) -> int:
    """Finds the first available TCP port starting at `start_port`."""
    for offset in range(MAX_PORT_PROBES):
        candidate = start_port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
                return candidate
            except OSError:
                continue
    raise RuntimeError(
        f"Could not bind Gerrit UI server on '{host}' across ports {start_port}..{start_port + MAX_PORT_PROBES - 1}."
    )


register_ui_hook("find_available_port", find_available_port)


def serve_gerrit_ui(
    host_addr: str = "127.0.0.1",
    port: int = DEFAULT_GERRIT_UI_PORT,
    gerrit_host: str = DEFAULT_GERRIT_HOST,
    viewer: str = "",
    query: str = "",
    open_browser: bool = True,
    export_static_path: str | None = None,
) -> int:
    """Starts the local Gerrit UI server or exports a pre-populated static snapshot HTML."""
    from commands.gerrit.ui_server import GerritUIService

    service = GerritUIService(host=gerrit_host, default_viewer=viewer, default_query=query)
    if export_static_path:
        queue_data = service.fetch_queue(viewer_email=viewer or None, query=query or None)
        vetted_stacks = [
            st
            for st in (queue_data.get("stacks") or [])
            if any(
                c.get("presubmit_vetted") and not c.get("approved_by_viewer")
                for c in (st.get("commits") or [])
            )
        ]
        target_cls = [
            c["number"] for st in vetted_stacks[:3] for c in (st.get("commits") or [])
        ][:8]
        if not target_cls:
            target_cls = [c["number"] for c in (queue_data.get("changes") or [])[:6]]
        queue_data["prefetched_diffs"] = service.fetch_multi_commit_diffs(target_cls)
        html_out = render_static_gerrit_ui_html(initial_payload=queue_data)
        out_file = Path(export_static_path).resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(html_out, encoding="utf-8")
        print(f"Exported static Gerrit UI snapshot ({len(queue_data['changes'])} CLs) to {out_file}")
        return 0

    port_finder = resolve_ui_hook("find_available_port", find_available_port)
    bound_port = port_finder(host_addr, port)
    # Standalone servers mint their own token; the browser picks it up from `?token=`.
    ui_token = mint_ui_token()
    url = f"http://{host_addr}:{bound_port}/?token={ui_token}"
    server = http.server.ThreadingHTTPServer(
        (host_addr, bound_port), make_gerrit_ui_handler(service, ui_token=ui_token)
    )
    print(f"AEMU Fast Gerrit UI running at {url} (viewer: {service.default_viewer})")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Gerrit UI server.")
    finally:
        server.server_close()
    return 0
