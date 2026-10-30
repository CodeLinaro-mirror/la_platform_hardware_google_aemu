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

"""Hermetic unit tests for shared `core.web` HTTP server scaffolding."""

from __future__ import annotations

import http.server
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from clients.agent_runtime_service import FakeAgentRuntimeService, ProdAgentRuntimeService
from core.process import FakeProcessRunner
from core.web import (
    AgentProxy,
    HotReloadHandlerDispatcher,
    JsonRequestHandler,
    RequestRejected,
    Router,
    SIDECAR_PRELOAD_JS,
    SIDECAR_PRELOAD_SCRIPT_TAG,
    SecurityPolicy,
    StaticAssetResolver,
    UI_TOKEN_HEADER,
    WebRequest,
    compute_asset_version,
    find_available_port,
    is_truthy_query_flag,
    read_bounded_utf8_preview,
    render_html_shell,
    resolve_active_conversation_id,
    serve_http,
)


class WebRequestAndRouterTest(unittest.TestCase):
    """Tests `WebRequest` parameter coercion and `Router` dispatch."""

    def test_web_request_query_helpers_and_unicode(self) -> None:
        req = WebRequest.from_get(
            "/api/items",
            "q=%E6%B5%8B%E8%AF%95_%F0%9F%9A%80_%C3%A9&fast=true&ps=42&bad=abc&cls=101, 102 ,",
        )
        self.assertEqual(req.query_str("q"), "测试_🚀_é")
        self.assertEqual(req.query_opt_str("missing"), None)
        self.assertTrue(req.query_bool("fast"))
        self.assertTrue(req.query_flag("fast"))
        self.assertFalse(req.query_flag("missing"))
        self.assertEqual(req.query_int("ps", digits_only=True), 42)
        self.assertEqual(req.query_int("ps", min_val=50), 50)
        self.assertEqual(req.query_int("ps", max_val=10), 10)
        self.assertIsNone(req.query_int("bad", digits_only=True))
        self.assertEqual(req.query_csv("cls"), ["101", "102"])
        self.assertTrue(is_truthy_query_flag(req.query_params, "fast"))

    def test_web_request_body_helpers(self) -> None:
        req = WebRequest.from_post(
            "/api/submit",
            body={
                "file": "  src/测试_🚀_é.cc  ",
                "line": "17",
                "bad_int": "xyz",
                "resume_jetski": 1,
                "cl": 999,
            },
        )
        self.assertEqual(req.body_str("file_path", "file", strip=True), "src/测试_🚀_é.cc")
        self.assertIsNone(req.body_opt_str("nonexistent"))
        self.assertEqual(req.body_int("line", digits_only=True), 17)
        self.assertIsNone(req.body_int("bad_int", digits_only=True))
        self.assertTrue(req.body_bool("resume_agent", "resume_jetski"))
        self.assertFalse(req.body_bool("missing_flag", default=False))
        self.assertEqual(req.body_list("cls", fallback_single_key="cl"), [999])

    def test_router_registers_and_dispatches_get_and_post(self) -> None:
        router = Router()
        calls: list[tuple[str, str]] = []

        router.add_get(["/", "/index.html"], lambda _h, r: calls.append(("GET", r.path)))
        router.add_post("/api/act", lambda _h, r: calls.append(("POST", r.body_str("msg"))))

        self.assertIsNotNone(router.resolve_get("/index.html"))
        self.assertIsNone(router.resolve_get("/unknown"))
        self.assertIsNotNone(router.resolve_post("/api/act"))

        self.assertTrue(router.dispatch_get(None, WebRequest.from_get("/")))
        self.assertFalse(router.dispatch_get(None, WebRequest.from_get("/nope")))
        self.assertTrue(
            router.dispatch_post(None, WebRequest.from_post("/api/act", body={"msg": "hi"}))
        )
        self.assertEqual(calls, [("GET", "/"), ("POST", "hi")])


class AssetsAndUnicodePreviewTest(unittest.TestCase):
    """Tests `StaticAssetResolver`, `render_html_shell`, and UTF-8 boundary-safe previews."""

    def test_static_asset_resolver_and_render_html_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.html"
            existing = root / "template.html"
            existing.write_text(
                f"<html>{SIDECAR_PRELOAD_SCRIPT_TAG}"
                '<script>const V = "__VER__"; const D = __BOOT__;</script></html>',
                encoding="utf-8",
            )
            resolver = StaticAssetResolver([missing, existing])
            self.assertEqual(resolver.resolve_first_existing(), existing)
            self.assertEqual(len(resolver.version()), 12)
            self.assertEqual(compute_asset_version(missing), "0")

            rendered = render_html_shell(
                existing,
                inline_preload=True,
                version_placeholder='"__VER__"',
                version="v123",
                bootstrap_placeholder="__BOOT__",
                bootstrap_payload={"tag": "</script>", "unicode": "测试_🚀_é"},
            )
            self.assertIn("window.sidecar =", rendered)
            self.assertNotIn(SIDECAR_PRELOAD_SCRIPT_TAG, rendered)
            self.assertIn('"v123"', rendered)
            self.assertIn("<\\/script>", rendered)
            self.assertIn("测试_🚀_é", rendered)

    def test_read_bounded_utf8_preview_confinement_and_multibyte_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            outside = root.parent / f"outside_{os.getpid()}.txt"
            inside = root / "notes_测试_🚀_é.txt"
            payload = "A" * 10 + "🚀" * 5
            inside.write_text(payload, encoding="utf-8")

            self.assertIsNone(read_bounded_utf8_preview(outside, [root]))
            with self.assertRaises(FileNotFoundError):
                read_bounded_utf8_preview(root / "does_not_exist.txt", [root])

            # Truncate mid-emoji (10 ASCII bytes + 2 bytes of a 4-byte 🚀 UTF-8 sequence).
            preview = read_bounded_utf8_preview(inside, [root], max_bytes=12)
            self.assertIsNotNone(preview)
            assert preview is not None
            self.assertEqual(preview["content"], "A" * 10)
            self.assertNotIn("\ufffd", preview["content"])


class AgentProxyAndRuntimeRawTest(unittest.TestCase):
    """Tests `AgentProxy` routes and `AgentRuntimeService.invoke_agentapi_raw`."""

    def test_prod_agent_runtime_invoke_agentapi_raw_parses_json_and_errors(self) -> None:
        runner = FakeProcessRunner()
        runner.queue_response(
            returncode=0,
            stdout='{"response": {"ok": true}}',
        )
        svc = ProdAgentRuntimeService(
            runner,
            agentapi_bin="/bin/agentapi",
            env={"ANTIGRAVITY_SOURCE_METADATA": "strip-me"},
        )
        out = svc.invoke_agentapi_raw(["get-conversation-metadata", "conv-1"])
        self.assertEqual(out, {"response": {"ok": True}})
        self.assertNotIn("ANTIGRAVITY_SOURCE_METADATA", runner.calls[0].env or {})

        runner.queue_response(
            returncode=1,
            stderr="connection refused",
        )
        with self.assertRaisesRegex(RuntimeError, "connection refused"):
            svc.invoke_agentapi_raw(["send-message", "c1", "hi"], project_id="proj-9")

    def test_agent_proxy_with_fake_runtime_service(self) -> None:
        fake_svc = FakeAgentRuntimeService()
        fake_svc.seed_metadata("conv-abc", {"projectId": "p-123"})
        proxy = AgentProxy(runtime_service=fake_svc)
        router = proxy.register_routes(Router())

        captured: list[tuple[int, dict[str, object]]] = []

        class StubHandler:
            def send_json(self, data: dict[str, object], status: int = 200) -> None:
                captured.append((status, data))

        handler = StubHandler()
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                resolve_active_conversation_id(WebRequest.from_post("/_sidecar/send-message")),
                "",
            )
            router.dispatch_post(handler, WebRequest.from_post("/_sidecar/get-conversation-metadata"))
            self.assertEqual(captured[-1][0], 400)

        router.dispatch_post(
            handler,
            WebRequest.from_post(
                "/_sidecar/get-conversation-metadata",
                body={"conversationId": "conv-abc"},
            ),
        )
        self.assertEqual(captured[-1], (200, {"projectId": "p-123"}))

        router.dispatch_post(
            handler,
            WebRequest.from_post(
                "/_sidecar/send-message",
                body={
                    "conversationId": "conv-abc",
                    "message": "Hello 测试_🚀_é",
                    "title": "Greeting",
                    "projectId": "proj-1",
                },
            ),
        )
        self.assertEqual(captured[-1][0], 200)
        self.assertEqual(
            fake_svc.raw_agentapi_calls[-1],
            (("send-message", "--title", "Greeting", "conv-abc", "Hello 测试_🚀_é"), "proj-1"),
        )

        router.dispatch_post(
            handler,
            WebRequest.from_post(
                "/_sidecar/new-conversation",
                body={"message": "Start task", "title": "New Task"},
            ),
        )
        self.assertEqual(captured[-1][0], 200)
        self.assertIn("newConversation", captured[-1][1].get("response", {}))  # type: ignore[arg-type]


class JsonRequestHandlerAndServerLifecycleTest(unittest.TestCase):
    """Tests `JsonRequestHandler`, `serve_http`, and `HotReloadHandlerDispatcher` over loopback."""

    def test_json_request_handler_security_cors_and_routing(self) -> None:
        router = Router()
        router.add_get("/public", lambda h, _r: h.send_json({"public": True}))
        router.add_get("/api/ping", lambda h, r: h.send_json({"pong": r.query_str("echo")}))
        router.add_post("/api/echo", lambda h, r: h.send_json({"got": dict(r.body)}))

        class TestHandler(JsonRequestHandler):
            security_policy = SecurityPolicy(
                expected_token="tok-123",
                public_get_routes=frozenset({"/public"}),
                enable_cors=True,
            )
            ui_token = "tok-123"

        TestHandler.router = router
        httpd, port = serve_http(
            TestHandler,
            host="127.0.0.1",
            port=find_available_port("127.0.0.1", 19100),
            ui_token="tok-123",
            open_browser=False,
            block=False,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{port}"
            with urllib.request.urlopen(f"{base}/public", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(json.loads(resp.read().decode("utf-8")), {"public": True})

            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(f"{base}/api/ping", timeout=5)
            self.assertEqual(ctx.exception.code, 401)
            ctx.exception.close()

            req = urllib.request.Request(
                f"{base}/api/echo",
                data=json.dumps({"hello": "测试_🚀_é"}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    UI_TOKEN_HEADER: "tok-123",
                    "Origin": "http://127.0.0.1:3000",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(
                    resp.headers.get("Access-Control-Allow-Origin"),
                    "http://127.0.0.1:3000",
                )
                self.assertEqual(
                    json.loads(resp.read().decode("utf-8")),
                    {"got": {"hello": "测试_🚀_é"}},
                )

            opt_req = urllib.request.Request(
                f"{base}/api/echo",
                headers={"Origin": "https://evil.example.com"},
                method="OPTIONS",
            )
            with self.assertRaises(urllib.error.HTTPError) as opt_ctx:
                urllib.request.urlopen(opt_req, timeout=5)
            self.assertEqual(opt_ctx.exception.code, 403)
            opt_ctx.exception.close()
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_hot_reload_handler_dispatcher_reloads_when_mtime_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mod_file = Path(tmp) / "dummy_hot_mod.py"
            mod_file.write_text("VALUE = 1\n", encoding="utf-8")
            mod_name = "_test_dummy_hot_mod"
            mod = types.ModuleType(mod_name)
            mod.__file__ = str(mod_file)
            sys.modules[mod_name] = mod
            try:
                built_count = 0

                def build_handler() -> type[http.server.BaseHTTPRequestHandler]:
                    nonlocal built_count
                    built_count += 1
                    return JsonRequestHandler

                dispatcher = HotReloadHandlerDispatcher(
                    module_names=[mod_name],
                    handler_builder=build_handler,
                )
                self.assertEqual(built_count, 1)
                dispatcher._reload_if_changed()
                self.assertEqual(built_count, 1)

                # Bump mtime and verify reload triggers handler_builder again.
                os.utime(mod_file, (mod_file.stat().st_atime + 5, mod_file.stat().st_mtime + 5))
                with patch("importlib.reload") as mock_reload:
                    dispatcher._reload_if_changed()
                    mock_reload.assert_called_once_with(mod)
                self.assertEqual(built_count, 2)
            finally:
                sys.modules.pop(mod_name, None)


if __name__ == "__main__":
    unittest.main()
