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

"""Shared Jetski Web AuxPane `/_sidecar/*` proxy routes backed by `AgentRuntimeService`."""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus
import os
from typing import Any

from clients.agent_runtime_service import AgentRuntimeService, ProdAgentRuntimeService
from core.process import SubprocessRunner
from core.web.router import Router, WebRequest

SIDECAR_METADATA_ROUTE = "/_sidecar/get-conversation-metadata"
SIDECAR_SEND_MESSAGE_ROUTE = "/_sidecar/send-message"
SIDECAR_NEW_CONVERSATION_ROUTE = "/_sidecar/new-conversation"

ENV_SIDECAR_CONVERSATION_ID = "ANTIGRAVITY_SIDECAR_CONVERSATION_ID"
ENV_CONVERSATION_ID = "ANTIGRAVITY_CONVERSATION_ID"

AgentApiInvoker = Callable[[list[str], str | None], dict[str, Any]]


def resolve_active_conversation_id(request: WebRequest) -> str:
    """Resolves `conversationId` from `request.body` or active Antigravity env vars."""
    from_body = request.body_str("conversationId", strip=True)
    if from_body:
        return from_body
    return (
        os.environ.get(ENV_SIDECAR_CONVERSATION_ID)
        or os.environ.get(ENV_CONVERSATION_ID)
        or ""
    ).strip()


class AgentProxy:
    """Registers and dispatches `/_sidecar/*` proxy endpoints over `AgentRuntimeService`."""

    def __init__(
        self,
        *,
        invoker: AgentApiInvoker | None = None,
        runtime_service: AgentRuntimeService | None = None,
    ) -> None:
        self._invoker = invoker
        self._runtime_service = runtime_service

    def _invoke(self, args: list[str], project_id: str | None = None) -> dict[str, Any]:
        """Invokes the configured `invoker` callback or `AgentRuntimeService.invoke_agentapi_raw`."""
        if self._invoker is not None:
            return self._invoker(args, project_id)
        service = self._runtime_service or ProdAgentRuntimeService(SubprocessRunner())
        return service.invoke_agentapi_raw(args, project_id=project_id)

    def handle_get_metadata(self, handler: Any, request: WebRequest) -> None:
        """Handles `POST /_sidecar/get-conversation-metadata`."""
        conv_id = resolve_active_conversation_id(request)
        if not conv_id:
            handler.send_json(
                {
                    "error": (
                        "Missing conversationId for /api/sidecar/metadata: provide "
                        "'conversationId' in the JSON body or set ANTIGRAVITY_CONVERSATION_ID."
                    )
                },
                status=int(HTTPStatus.BAD_REQUEST),
            )
            return
        handler.send_json(self._invoke(["get-conversation-metadata", conv_id], None))

    def handle_send_message(self, handler: Any, request: WebRequest) -> None:
        """Handles `POST /_sidecar/send-message`."""
        msg = request.body_str("message", strip=True)
        conv_id = resolve_active_conversation_id(request)
        if not msg or not conv_id:
            handler.send_json(
                {
                    "error": (
                        "Missing message or conversationId for /api/sidecar/send-message: "
                        "provide non-empty 'message' and 'conversationId' fields in the JSON body."
                    )
                },
                status=int(HTTPStatus.BAD_REQUEST),
            )
            return
        args = ["send-message"]
        title = request.body_opt_str("title")
        if title:
            args.extend(["--title", title])
        args.extend([conv_id, msg])
        handler.send_json(self._invoke(args, request.body_opt_str("projectId")))

    def handle_new_conversation(self, handler: Any, request: WebRequest) -> None:
        """Handles `POST /_sidecar/new-conversation`."""
        msg = request.body_str("message", strip=True)
        if not msg:
            handler.send_json(
                {
                    "error": (
                        "Missing message for /api/sidecar/new-conversation: provide "
                        "a non-empty 'message' field in the JSON body."
                    )
                },
                status=int(HTTPStatus.BAD_REQUEST),
            )
            return
        args = ["new-conversation"]
        title = request.body_opt_str("title")
        if title:
            args.extend(["--title", title])
        args.append(msg)
        handler.send_json(self._invoke(args, request.body_opt_str("projectId")))

    def register_routes(self, router: Router) -> Router:
        """Registers all three `/_sidecar/*` POST endpoints on `router`."""
        router.add_post(SIDECAR_METADATA_ROUTE, self.handle_get_metadata)
        router.add_post(SIDECAR_SEND_MESSAGE_ROUTE, self.handle_send_message)
        router.add_post(SIDECAR_NEW_CONVERSATION_ROUTE, self.handle_new_conversation)
        return router


__all__ = [
    "AgentApiInvoker",
    "AgentProxy",
    "ENV_CONVERSATION_ID",
    "ENV_SIDECAR_CONVERSATION_ID",
    "SIDECAR_METADATA_ROUTE",
    "SIDECAR_NEW_CONVERSATION_ROUTE",
    "SIDECAR_SEND_MESSAGE_ROUTE",
    "resolve_active_conversation_id",
]
