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

import http.server
import os
from pathlib import Path
from typing import Any
import webbrowser

from commands.gerrit.services import inline_qa_service
from commands.gerrit.services.diff_parser import (
    _candidate_gerrit_ui_template_paths,
    get_gerrit_ui_template_path,
    get_gerrit_ui_template_version,
)
from commands.gerrit.services.queue_service import (
    TEAM_ROSTER_EMAILS,
    _normalize_viewer_email,
)
from core.web.agent_proxy import AgentProxy
from core.web.assets import (
    SIDECAR_PRELOAD_JS,
    SIDECAR_PRELOAD_SCRIPT_TAG,
    render_html_shell,
)
from core.web.handler import JsonRequestHandler
from core.web.router import Router, WebRequest, is_truthy_query_flag
from core.web.security import (
    SIDECAR_TOKEN_ENV,
    RequestRejected,
    SecurityPolicy,
    mint_ui_token,
)
from core.web.server import (
    DEFAULT_MAX_PORT_PROBES,
    find_available_port as _core_find_available_port,
    serve_http,
)
from lib.gerrit_api import DEFAULT_GERRIT_HOST

DEFAULT_GERRIT_UI_PORT = 8085
MAX_PORT_PROBES = DEFAULT_MAX_PORT_PROBES
# Only the sidecar bridge script is public. The page itself embeds the viewer's email, and
# both the Jetski iframe URL and the standalone URL carry `?token=`.
PUBLIC_GET_ROUTES: frozenset[str] = frozenset({"/preload.js"})
_UI_TOKEN_HINT = (
    "Open the Gerrit UI from the Jetski side panel, or through the URL printed by "
    "'emu-dev-cli gerrit ui' (it ends with ?token=...)."
)

_is_truthy_query_flag = is_truthy_query_flag


def render_static_gerrit_ui_html(
    initial_payload: dict[str, Any] | None = None,
    variant: str = "classic",
) -> str:
    """Renders `gerrit_ui.html` or `gerrit_ui_polygerrit.html` with inlined `window.sidecar` bridge."""
    return render_html_shell(
        get_gerrit_ui_template_path(variant),
        inline_preload=True,
        version_placeholder='"__GERRIT_UI_VERSION__"',
        version=get_gerrit_ui_template_version(),
        bootstrap_placeholder="__GERRIT_UI_BOOTSTRAP_JSON__",
        bootstrap_payload=initial_payload,
    )


def _extract_code_question_kwargs(req: WebRequest) -> dict[str, Any]:
    """Extracts shared inline code question coordinates and metadata from `req`."""
    body = req.body
    default_line = int(body.get("line") or 1)
    return {
        "cl_number": body.get("cl", ""),
        "file_path": str(body.get("file_path") or body.get("file") or ""),
        "start_line": int(body.get("start_line") or default_line),
        "end_line": int(body.get("end_line") or default_line),
        "code_snippet": str(body.get("code_snippet") or body.get("code") or ""),
        "question": str(body.get("question") or ""),
        "side": str(body.get("side") or "REVISION"),
        "conversation_id": req.body_opt_str("conversationId"),
        "project_id": req.body_opt_str("projectId"),
        "thread_id": req.body_opt_str("threadId"),
        "function_name": str(
            body.get("function_name") or body.get("functionName") or ""
        ).strip(),
    }


def _coerce_vote_flag(raw_val: Any) -> bool | int | None:
    """Coerces a vote flag (`autosubmit` / `presubmit_ready`) to `bool | int | None`."""
    if isinstance(raw_val, bool) or raw_val is None:
        return raw_val
    return int(raw_val)


class _GerritRouteEndpoints:
    """Cohesive Gerrit UI endpoint controller bound to a `GerritUIService` instance."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def get_preload(self, handler: JsonRequestHandler, _req: WebRequest) -> None:
        handler.send_javascript(SIDECAR_PRELOAD_JS)

    def get_index(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        req_viewer = _normalize_viewer_email(req.query_str("viewer"))
        req_ui = req.query_str("ui", strip=True).lower()
        ui_variant = (
            "polygerrit"
            if req.path == "/polygerrit" or req_ui in ("polygerrit", "pg")
            else "classic"
        )
        initial_state: dict[str, Any] = {
            "viewer": req_viewer or self._service.default_viewer,
            "roster": list(TEAM_ROSTER_EMAILS),
        }
        handler.send_html(
            render_static_gerrit_ui_html(initial_payload=initial_state, variant=ui_variant)
        )

    def get_queue(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        viewer = req.query_str("viewer")
        if req.query_flag("prewarm_only"):
            scheduled = self._service.schedule_background_prewarm(viewer or None)
            handler.send_json({"status": "ok", "prewarm_scheduled": scheduled})
            return
        fast_flag = req.query_flag("fast")
        data = self._service.fetch_queue(
            viewer_email=viewer or None,
            query=req.query_str("query") or None,
            scope=req.query_str("scope", "reviewer") or "reviewer",
            author=req.query_str("author") or None,
            fast=fast_flag,
            use_cache=not req.query_flag("refresh"),
        )
        if req.query_flag("prewarm") or not fast_flag:
            self._service.schedule_background_prewarm(data.get("viewer") or viewer or None)
        handler.send_json(data)

    def get_diffs(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        cl_list = req.query_csv("cls")
        ps = req.query_opt_str("ps")
        base = req.query_opt_str("base")
        if len(cl_list) == 1 and (ps or base):
            diffs = [self._service.fetch_commit_diff_bundle(cl_list[0], patch_set=ps, base=base)]
        else:
            diffs = self._service.fetch_multi_commit_diffs(cl_list)
        handler.send_json({"diffs": diffs, "ui_version": get_gerrit_ui_template_version()})

    def get_file_content(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        cl = req.query_str("cl", strip=True)
        file_path = req.query_str("file", strip=True)
        ps = req.query_opt_str("ps")
        parent_val = req.query_opt_str("parent")
        is_parent = parent_val in ("1", "true") or ps in ("0", "base", "parent")
        if not cl or not file_path:
            handler.send_json(
                {"error": "Missing required 'cl' or 'file' query parameter"},
                status=400,
            )
            return
        if is_parent:
            res = self._service.fetch_file_content(cl, file_path, patch_set=ps, parent=True)
        else:
            res = self._service.fetch_file_content(cl, file_path, patch_set=ps)
        res["ui_version"] = get_gerrit_ui_template_version()
        handler.send_json(res)

    def get_swarm_status(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        handler.send_json(
            {
                "statuses": self._service.get_swarm_statuses(req.query_csv("cls")),
                "ui_version": get_gerrit_ui_template_version(),
            }
        )

    def get_code_questions(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        conv_id = req.query_opt_str("conversationId")
        handler.send_json(
            {"questions": self._service.get_code_questions(req.query_csv("cls"), conversation_id=conv_id)}
        )

    def get_suggest_reviewers(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        handler.send_json(
            {
                "suggestions": self._service.suggest_reviewers(
                    req.query_str("cl"), query=req.query_str("q")
                )
            }
        )

    def get_presubmit_targets(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        cl_num = req.query_str("cl", strip=True)
        if not cl_num:
            handler.send_json(
                {"error": "Missing required `cl` query parameter for `/api/gerrit/presubmit-targets`."},
                status=400,
            )
            return
        handler.send_json(
            self._service.get_presubmit_targets(
                cl_num,
                patchset=req.query_int("ps", digits_only=True),
                project=req.query_str("project", strip=True),
                force_refresh=req.query_flag("refresh"),
            )
        )

    def post_checks_rerun(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
        cl_num = str(body.get("cl", "")).strip()
        if not cl_num:
            handler.send_json(
                {"ok": False, "error": "Missing required `cl` parameter for checks rerun."},
                status=400,
            )
            return
        raw_targets = body.get("targets")
        if isinstance(raw_targets, str):
            targets = [t.strip() for t in raw_targets.split(",") if t.strip()]
        elif isinstance(raw_targets, list):
            targets = [str(t).strip() for t in raw_targets if str(t).strip()]
        else:
            single = str(body.get("target") or "").strip()
            targets = [single] if single else []

        res = self._service.rerun_presubmit_checks(
            cl_number=cl_num,
            targets=targets,
            patchset=req.body_int("ps", "patchset"),
            reason=str(body.get("reason", "")).strip(),
            bypass_cache=bool(body.get("bypass_cache", False)),
            stage_presubmit_ready=bool(body.get("stage_presubmit_ready", True)),
        )
        handler.send_json(res, status=200 if res.get("ok", True) else 400)

    def get_included_in(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        cl_num = req.query_str("cl", strip=True)
        if not cl_num:
            handler.send_json(
                {"error": "Missing required `cl` query parameter for `/api/gerrit/included-in`."},
                status=400,
            )
            return
        force_refresh = req.query_flag("refresh", "force")
        res = self._service.get_included_in(cl_num, force_refresh=force_refresh)
        res["ui_version"] = get_gerrit_ui_template_version()
        handler.send_json(res)

    def get_change_edit(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        cl_num = req.query_str("cl", strip=True)
        if not cl_num:
            handler.send_json(
                {"error": "Missing required `cl` query parameter for `/api/gerrit/edit`."},
                status=400,
            )
            return
        res = self._service.get_change_edit(cl_num)
        res["ui_version"] = get_gerrit_ui_template_version()
        handler.send_json(res)

    def get_ai_analysis(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        cl_num = req.query_str("cl", strip=True)
        if not cl_num:
            handler.send_json(
                {
                    "error": "Missing required `cl` query parameter for `/api/gerrit/ai-analysis` "
                    "(pass e.g. `?cl=43256612&ps=1`)."
                },
                status=400,
            )
            return
        handler.send_json(
            self._service.get_ai_analysis(cl_num, patchset=req.query_int("ps", digits_only=True))
        )

    def get_update_status(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        force_flag = req.query_flag("force", "refresh")
        handler.send_json(self._service.get_sidecar_update_status(force_fetch=force_flag))

    def post_vote(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
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
        res = self._service.cast_batch_vote(
            cl_numbers=body.get("cls") or [],
            code_review=cr_arg,
            autosubmit=_coerce_vote_flag(body.get("autosubmit", False)),
            presubmit_ready=_coerce_vote_flag(body.get("presubmit_ready", False)),
            message=str(body.get("message", "")),
        )
        handler.send_json(res)

    def post_submit(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        raw_cls = req.body_list("cls", fallback_single_key="cl")
        res = self._service.submit_changes(cl_numbers=raw_cls)
        handler.send_json(res, status=200 if res.get("status") == "ok" else 409)

    def post_rebase(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
        kwargs: dict[str, Any] = {
            "cl_number": body.get("cl", ""),
            "base": req.body_opt_str("base"),
            "viewer_email": req.body_opt_str("viewer"),
        }
        if "allow_conflicts" in body:
            kwargs["allow_conflicts"] = _coerce_vote_flag(body.get("allow_conflicts", False))
        handler.send_json(self._service.rebase_change(**kwargs))

    def post_abandon(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
        handler.send_json(
            self._service.abandon_change(
                cl_number=body.get("cl", ""),
                message=str(body.get("message") or ""),
                viewer_email=req.body_opt_str("viewer"),
            )
        )

    def post_restore(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
        handler.send_json(
            self._service.restore_change(
                cl_number=body.get("cl", ""),
                message=str(body.get("message") or ""),
                viewer_email=req.body_opt_str("viewer"),
            )
        )

    def post_cherry_pick(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
        dest = req.body_str("destination", "branch", strip=True)
        if not dest:
            handler.send_json(
                {"status": "error", "error": "Destination branch is required for cherry-pick."},
                status=400,
            )
            return
        kwargs: dict[str, Any] = {
            "cl_number": body.get("cl", ""),
            "destination": dest,
            "message": str(body.get("message") or ""),
            "base": req.body_opt_str("base"),
            "revision": req.body_opt_str("revision"),
            "viewer_email": req.body_opt_str("viewer"),
        }
        if "allow_conflicts" in body:
            kwargs["allow_conflicts"] = _coerce_vote_flag(body.get("allow_conflicts", False))
        if "keep_reviewers" in body:
            kwargs["keep_reviewers"] = bool(body.get("keep_reviewers", False))
        handler.send_json(self._service.cherry_pick_change(**kwargs))

    def post_follow_up(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
        branch = req.body_str("branch", "destination", strip=True)
        if not branch:
            handler.send_json(
                {"status": "error", "error": "Destination branch is required for follow-up change."},
                status=400,
            )
            return
        subject = req.body_str("subject", "message", strip=True)
        if not subject:
            handler.send_json(
                {"status": "error", "error": "Subject / commit message is required for follow-up change."},
                status=400,
            )
            return
        handler.send_json(
            self._service.create_follow_up_change(
                cl_number=body.get("cl", ""),
                branch=branch,
                subject=subject,
                base_change=req.body_opt_str("base_change", "base"),
                topic=req.body_opt_str("topic"),
                is_private=bool(body.get("is_private") or body.get("private")),
                work_in_progress=bool(body.get("work_in_progress") or body.get("wip")),
                viewer_email=req.body_opt_str("viewer"),
            )
        )

    def post_save_change_edit(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
        cl_num = req.body_str("cl", strip=True)
        if not cl_num:
            handler.send_json(
                {"status": "error", "error": "CL number is required for saving change edit."},
                status=400,
            )
            return
        file_path = req.body_str("file", "file_path", strip=True)
        if not file_path:
            handler.send_json(
                {"status": "error", "error": "File path is required for saving change edit."},
                status=400,
            )
            return
        content = body.get("content", "")
        if not isinstance(content, str):
            content = str(content or "")
        handler.send_json(
            self._service.save_change_edit(
                cl_number=cl_num,
                file_path=file_path,
                content=content,
                viewer_email=req.body_opt_str("viewer"),
            )
        )

    def post_publish_change_edit(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
        cl_num = req.body_str("cl", strip=True)
        if not cl_num:
            handler.send_json(
                {"status": "error", "error": "CL number is required for publishing change edit."},
                status=400,
            )
            return
        handler.send_json(
            self._service.publish_change_edit(
                cl_number=cl_num,
                notify=req.body_opt_str("notify") or "OWNER",
                viewer_email=req.body_opt_str("viewer"),
            )
        )

    def post_discard_change_edit(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
        cl_num = req.body_str("cl", strip=True)
        if not cl_num:
            handler.send_json(
                {"status": "error", "error": "CL number is required for discarding change edit."},
                status=400,
            )
            return
        handler.send_json(
            self._service.discard_change_edit(
                cl_number=cl_num,
                viewer_email=req.body_opt_str("viewer"),
            )
        )

    def post_assign_reviewer(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
        handler.send_json(
            self._service.assign_second_human_reviewer(
                cl_number=body.get("cl", ""),
                reviewer_email=body.get("reviewer", ""),
            )
        )

    def post_reviewer(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
        batch_cls = body.get("cls")
        action = str(body.get("action") or "add")
        account = body.get("account") or body.get("reviewer") or ""
        state = str(body.get("state") or "REVIEWER")
        viewer_email = req.body_opt_str("viewer")
        if isinstance(batch_cls, list) and len(batch_cls) > 0:
            res = self._service.modify_reviewers_batch(
                cl_numbers=batch_cls,
                action=action,
                account=account,
                state=state,
                viewer_email=viewer_email,
            )
        else:
            res = self._service.modify_reviewer(
                cl_number=body.get("cl", ""),
                action=action,
                account=account,
                state=state,
                viewer_email=viewer_email,
            )
        handler.send_json(res)

    def post_attention(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
        handler.send_json(
            self._service.modify_attention_set(
                cl_number=body.get("cl", ""),
                action=str(body.get("action") or "remove"),
                user=body.get("user") or body.get("account") or body.get("reviewer") or "",
                reason=str(body.get("reason") or ""),
                viewer_email=req.body_opt_str("viewer"),
            )
        )

    def post_chat_dm(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
        handler.send_json(
            self._service.send_reviewer_chat_dm(
                cl_number=body.get("cl", ""),
                recipient=str(body.get("recipient") or body.get("reviewer") or ""),
                message=str(body.get("message") or ""),
            )
        )

    def post_harness(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
        handler.send_json(
            self._service.run_harness_action(
                cl_number=body.get("cl", ""),
                action=body.get("action", "review-swarm"),
            )
        )

    def post_swarm_review(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        handler.send_json(self._service.post_swarm_review(cl_number=req.body.get("cl", "")))

    def post_inline_comment(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
        handler.send_json(
            self._service.post_inline_comment(
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
        )

    def post_reply_comment(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
        handler.send_json(
            self._service.reply_inline_comment(
                cl_number=body.get("cl", ""),
                comment_id=str(body.get("comment_id") or body.get("in_reply_to") or ""),
                file_path=str(body.get("file_path") or body.get("file") or ""),
                message=str(body.get("message") or ""),
                line=req.body_int("line", digits_only=True),
                patch_set=req.body_int("patch_set", "ps", digits_only=True),
                side=str(body.get("side") or "REVISION"),
                unresolved=bool(body.get("unresolved", False)),
            )
        )

    def post_ask_code(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        kwargs = _extract_code_question_kwargs(req)
        handler.send_json(
            self._service.ask_agent_about_code(
                **kwargs,
                spawn_new=bool(req.body.get("spawnNew", False)),
            )
        )

    def post_code_question_action(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        kwargs = _extract_code_question_kwargs(req)
        handler.send_json(
            self._service.modify_or_delete_code_question(
                action=req.body_str("action"),
                **kwargs,
            )
        )

    def post_ai_analysis(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        body = req.body
        handler.send_json(
            self._service.trigger_or_cancel_ai_analysis(
                cl_number=body.get("cl", ""),
                action=str(body.get("action") or "analyze"),
                patchset=req.body_int("ps", digits_only=True),
                project=str(body.get("project") or ""),
                subject=str(body.get("subject") or ""),
                project_id=req.body_opt_str("projectId"),
                force=bool(body.get("force", False)),
            )
        )

    def post_perform_update(self, handler: JsonRequestHandler, req: WebRequest) -> None:
        force_discard = bool(req.body.get("force_discard_local", False))
        res = self._service.perform_sidecar_update(force_discard_local=force_discard)
        http_status = 200 if res.get("status") == "ok" else 409
        handler.send_json(res, status=http_status)


def _build_gerrit_router(service: Any, agent_proxy: AgentProxy) -> Router:
    """Registers all Gerrit UI GET and POST routes on a shared `Router`."""
    endpoints = _GerritRouteEndpoints(service)
    router = Router()
    agent_proxy.register_routes(router)

    router.add_get("/preload.js", endpoints.get_preload)
    router.add_get(["/", "/index.html", "/polygerrit", "/classic"], endpoints.get_index)
    router.add_get("/api/gerrit/queue", endpoints.get_queue)
    router.add_get("/api/gerrit/diffs", endpoints.get_diffs)
    router.add_get("/api/gerrit/file-content", endpoints.get_file_content)
    router.add_get("/api/gerrit/swarm-status", endpoints.get_swarm_status)
    router.add_get("/api/gerrit/code-questions", endpoints.get_code_questions)
    router.add_get("/api/gerrit/suggest-reviewers", endpoints.get_suggest_reviewers)
    router.add_get("/api/gerrit/presubmit-targets", endpoints.get_presubmit_targets)
    router.add_get(["/api/gerrit/included-in", "/api/gerrit/included_in"], endpoints.get_included_in)
    router.add_get(["/api/gerrit/edit", "/api/gerrit/change-edit"], endpoints.get_change_edit)
    router.add_get("/api/gerrit/ai-analysis", endpoints.get_ai_analysis)
    router.add_get("/api/gerrit/update-status", endpoints.get_update_status)

    router.add_post("/api/gerrit/vote", endpoints.post_vote)
    router.add_post(["/api/gerrit/checks/rerun", "/api/gerrit/checks-rerun"], endpoints.post_checks_rerun)
    router.add_post("/api/gerrit/submit", endpoints.post_submit)
    router.add_post("/api/gerrit/rebase", endpoints.post_rebase)
    router.add_post("/api/gerrit/abandon", endpoints.post_abandon)
    router.add_post("/api/gerrit/restore", endpoints.post_restore)
    router.add_post(["/api/gerrit/cherry_pick", "/api/gerrit/cherry-pick"], endpoints.post_cherry_pick)
    router.add_post(["/api/gerrit/follow_up", "/api/gerrit/follow-up", "/api/gerrit/create_change"], endpoints.post_follow_up)
    router.add_post(["/api/gerrit/edit-file", "/api/gerrit/save-edit"], endpoints.post_save_change_edit)
    router.add_post("/api/gerrit/publish-edit", endpoints.post_publish_change_edit)
    router.add_post("/api/gerrit/discard-edit", endpoints.post_discard_change_edit)
    router.add_post("/api/gerrit/assign-reviewer", endpoints.post_assign_reviewer)
    router.add_post("/api/gerrit/reviewer", endpoints.post_reviewer)
    router.add_post("/api/gerrit/attention", endpoints.post_attention)
    router.add_post("/api/gerrit/chat-dm", endpoints.post_chat_dm)
    router.add_post("/api/gerrit/harness", endpoints.post_harness)
    router.add_post("/api/gerrit/post-swarm-review", endpoints.post_swarm_review)
    router.add_post("/api/gerrit/comment", endpoints.post_inline_comment)
    router.add_post("/api/gerrit/reply", endpoints.post_reply_comment)
    router.add_post("/api/gerrit/ask-code", endpoints.post_ask_code)
    router.add_post("/api/gerrit/code-question-action", endpoints.post_code_question_action)
    router.add_post("/api/gerrit/ai-analysis", endpoints.post_ai_analysis)
    router.add_post("/api/gerrit/perform-update", endpoints.post_perform_update)
    return router


def make_gerrit_ui_handler(
    service: Any, ui_token: str | None = None
) -> type[http.server.BaseHTTPRequestHandler]:
    """Creates the HTTP request handler bound to `service`.

    Every route outside `PUBLIC_GET_ROUTES` requires `ui_token`, which defaults to
    `ANTIGRAVITY_SIDECAR_UI_TOKEN`. With no token configured, those routes reject
    every request.
    """
    expected_token = os.environ.get(SIDECAR_TOKEN_ENV, "") if ui_token is None else ui_token
    policy = SecurityPolicy(
        expected_token=expected_token,
        public_get_routes=PUBLIC_GET_ROUTES,
        token_hint=_UI_TOKEN_HINT,
        enable_cors=False,
    )
    agent_proxy = AgentProxy(
        invoker=lambda args, project_id=None: inline_qa_service._run_sidecar_agentapi(
            list(args), project_id=project_id
        )
    )
    bound_router = _build_gerrit_router(service, agent_proxy)

    class GerritUIHandler(JsonRequestHandler):
        router = bound_router
        security_policy = policy

    return GerritUIHandler


def find_available_port(host: str, start_port: int) -> int:
    """Finds the first available TCP port starting at `start_port`."""
    return _core_find_available_port(
        host,
        start_port,
        max_attempts=MAX_PORT_PROBES,
        server_label="Gerrit UI server",
    )


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

    ui_token = mint_ui_token()
    serve_http(
        lambda tok: make_gerrit_ui_handler(service, ui_token=tok),
        host=host_addr,
        port=port,
        ui_token=ui_token,
        open_browser=open_browser,
        port_finder=find_available_port,
        server_cls=http.server.ThreadingHTTPServer,
        browser_opener=webbrowser.open,
        banner_fn=lambda url, _port: (
            f"AEMU Fast Gerrit UI running at {url} (viewer: {service.default_viewer})"
        ),
        shutdown_message="\nShutting down Gerrit UI server.",
    )
    return 0
