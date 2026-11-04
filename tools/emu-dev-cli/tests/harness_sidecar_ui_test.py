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

"""HTTP security tests for the standalone AEMU harness AuxPane sidecar (`aemu_harness_ui/server.py`)."""

from __future__ import annotations

import http.client
import importlib.util
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
from types import ModuleType
import unittest
from unittest import mock
import urllib.error
import urllib.request

SIDECAR_SERVER = (
    Path(__file__).resolve().parents[1] / "harness" / "plugin" / "sidecars" / "aemu_harness_ui" / "server.py"
)
TOKEN = "harness-sidecar-test-token"
VALID_COMMIT = (
    "fix(harness): Fail closed on tokens\n\nExplain why: 测试_🚀_é.\n\n"
    "Bug: 485291270\nTest: python3 -m unittest discover -s tests -p '*_test.py'\n"
)


def _load_sidecar() -> ModuleType:
    spec = importlib.util.spec_from_file_location("aemu_harness_sidecar_security_test", SIDECAR_SERVER)
    assert spec is not None and spec.loader is not None, f"cannot load {SIDECAR_SERVER}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HarnessSidecarSecurityTest(unittest.TestCase):

    def setUp(self) -> None:
        self.module = _load_sidecar()
        self.temp_dir = Path(tempfile.mkdtemp(prefix="harness_sidecar_security_"))
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)
        env = mock.patch.dict(
            os.environ,
            {
                "EMU_DEV_CLI_HARNESS_DIR": str(self.temp_dir / "harness"),
                "EMU_DEV_CLI_WORKFLOW_STATE_DIR": str(self.temp_dir / "state"),
            },
        )
        env.start()
        self.addCleanup(env.stop)

    def serve(self, ui_token: str) -> str:
        patcher = mock.patch.object(self.module, "UI_TOKEN", ui_token)
        patcher.start()
        self.addCleanup(patcher.stop)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), self.module.HarnessSidecarHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    def request(
        self, url: str, token: str | None = None, body: bytes | None = None, content_type: str | None = "application/json"
    ) -> tuple[int, dict]:
        headers = {"X-Sidecar-Token": token} if token is not None else {}
        if body is not None and content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read().decode("utf-8"))

    def validate(self, base: str, token: str | None, content_type: str | None = "application/json") -> int:
        body = json.dumps({"message": VALID_COMMIT}, ensure_ascii=False).encode("utf-8")
        return self.request(f"{base}/api/commit/validate", token, body, content_type)[0]

    def test_unset_token_fails_closed(self) -> None:
        base = self.serve("")
        status, payload = self.request(f"{base}/api/state?token=", "")
        self.assertEqual(status, 401)
        self.assertIn("no UI token configured", payload["error"])
        self.assertIn("ANTIGRAVITY_SIDECAR_UI_TOKEN", payload["error"])
        self.assertEqual(self.request(f"{base}/api/state?token=anything", "anything")[0], 401)
        self.assertEqual(self.validate(base, ""), 401)

    def test_requires_matching_token(self) -> None:
        base = self.serve(TOKEN)
        for token in (None, "", "wrong", TOKEN + "x", "tökén"):
            with self.subTest(token=token):
                self.assertEqual(self.request(f"{base}/api/state", token)[0], 401)
                self.assertEqual(self.validate(base, token), 401)
        self.assertEqual(self.request(f"{base}/api/state", TOKEN)[0], 200)
        self.assertEqual(self.request(f"{base}/api/state?conversationId=c1&token={TOKEN}")[0], 200)
        status, payload = self.request(
            f"{base}/api/commit/validate", TOKEN, json.dumps({"message": VALID_COMMIT}, ensure_ascii=False).encode("utf-8")
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["valid"], payload)

    def test_uses_constant_time_comparison(self) -> None:
        base = self.serve(TOKEN)
        hmac_module = self.module.web_security.hmac
        with mock.patch.object(hmac_module, "compare_digest", wraps=hmac_module.compare_digest) as compare_digest:
            self.assertEqual(self.request(f"{base}/api/state", TOKEN)[0], 200)
        compare_digest.assert_called_with(TOKEN.encode("utf-8"), TOKEN.encode("utf-8"))

    def test_post_requires_json_content_type(self) -> None:
        base = self.serve(TOKEN)
        for content_type in ("text/plain", "application/x-www-form-urlencoded", None):
            with self.subTest(content_type=content_type):
                self.assertEqual(self.validate(base, TOKEN, content_type), 415)
        with mock.patch.object(self.module.subprocess, "run") as run:
            status, _ = self.request(
                f"{base}/api/workflow/approve", TOKEN, b'{"state_id": "s1"}', content_type="text/plain"
            )
        self.assertEqual(status, 415)
        run.assert_not_called()

    def test_post_rejects_malformed_and_oversized_bodies(self) -> None:
        base = self.serve(TOKEN)
        for body in (b"not json", b"[]", b"", b"\xff\xfe{}"):
            with self.subTest(body=body):
                self.assertEqual(self.request(f"{base}/api/commit/validate", TOKEN, body)[0], 400)
        conn = http.client.HTTPConnection("127.0.0.1", int(base.rsplit(":", 1)[1]), timeout=5)
        try:
            conn.putrequest("POST", "/api/commit/validate")
            conn.putheader("X-Sidecar-Token", TOKEN)
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(self.module.web_security.MAX_JSON_BODY_BYTES + 1))
            conn.endheaders(b"{}")
            self.assertEqual(conn.getresponse().status, 413)
        finally:
            conn.close()

    def test_workflow_approve_returns_500_when_cli_fails_and_200_on_success(self) -> None:
        base = self.serve(TOKEN)
        fail_proc = mock.MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: workflow state 'state_测试_🚀_é' not found\n",
        )
        with mock.patch.object(self.module.subprocess, "run", return_value=fail_proc):
            status, payload = self.request(
                f"{base}/api/workflow/approve",
                TOKEN,
                json.dumps({"state_id": "state_测试_🚀_é"}, ensure_ascii=False).encode("utf-8"),
            )
        self.assertEqual(status, 500)
        self.assertIn("state_测试_🚀_é", payload["error"])
        self.assertEqual(payload["exit_code"], 1)

        ok_proc = mock.MagicMock(returncode=0, stdout="Approved\n", stderr="")
        with mock.patch.object(self.module.subprocess, "run", return_value=ok_proc):
            status_ok, payload_ok = self.request(
                f"{base}/api/workflow/approve",
                TOKEN,
                json.dumps({"state_id": "state_测试_🚀_é"}, ensure_ascii=False).encode("utf-8"),
            )
        self.assertEqual(status_ok, 200)
        self.assertEqual(payload_ok["status"], "approved")

    def test_root_json_flag_is_not_shadowed_by_harness_subparser(self) -> None:
        import argparse
        from commands import harness

        root_parser = argparse.ArgumentParser()
        root_parser.add_argument("--json", action="store_true")
        subparsers = root_parser.add_subparsers(dest="subcommand")
        harness.register_parser(subparsers)

        parsed = root_parser.parse_args(["--json", "harness", "status"])
        self.assertTrue(parsed.json)


class HarnessSidecarInstallTest(unittest.TestCase):

    def test_installed_sidecar_loads_its_copy_of_web_security(self) -> None:
        from commands.harness.plugin_installer import (
            SIDECAR_WEB_SECURITY_RELPATH,
            get_harness_plugin_status,
            install_harness_plugin,
        )

        shared = Path(__file__).resolve().parents[1] / "src" / "core" / "web" / "security.py"
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "aemu-harness_测试"
            report = install_harness_plugin(
                target_dir=plugin_dir, source_plugin_dir=SIDECAR_SERVER.parents[2], cli_command="emu-dev-cli"
            )
            self.assertTrue(report["installed"], msg=str(report))
            installed_copy = plugin_dir / SIDECAR_WEB_SECURITY_RELPATH
            self.assertEqual(installed_copy.read_bytes(), shared.read_bytes())
            self.assertEqual(get_harness_plugin_status(target_dir=plugin_dir)["missing_files"], [])

            spec = importlib.util.spec_from_file_location(
                "aemu_harness_sidecar_installed_test", installed_copy.with_name("server.py")
            )
            module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            self.assertEqual(Path(module.web_security.__file__), installed_copy.resolve())


if __name__ == "__main__":
    unittest.main()
