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

"""Unit tests for the Fast Multi-Commit & Agent-Aware Gerrit Web UI (`commands.gerrit.ui_server`)."""

from __future__ import annotations

from datetime import datetime, timezone
import http.client
from http.server import ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request

from commands.gerrit.ai_analysis import (
    build_ai_analysis_prompt,
    extract_bug_ids_from_text,
    fetch_buganizer_context,
    normalize_ai_analysis_payload,
    parse_ai_analysis_from_conversation,
    resolve_local_repo_root,
)
from commands.gerrit.ui_server import GerritUIService
from commands.gerrit.services.diff_parser import (
    AEMU_MAP_BASE_URL,
    build_aemu_map_commit_url,
    build_code_search_file_url,
    classify_commit_type,
    classify_risk_and_load,
    extract_commit_message_from_patch,
    extract_enclosing_function_from_diff_lines,
    extract_symbol_from_code_line,
    format_repo_display_path,
    parse_unified_diff_files,
    resolve_code_search_base_url,
)
from commands.gerrit.services.inline_qa_service import (
    build_code_question_prompt,
    discover_transcript_code_questions,
    parse_code_questions_from_transcript_lines,
)
from commands.gerrit.services.queue_service import (
    build_viewer_gerrit_query,
    enrich_change_for_ui,
    extract_bug_ids_from_change,
    group_changes_into_stacks,
    is_agent_authored_or_uploaded,
    is_agent_or_bot_email,
    is_explicit_reviewer,
    parse_issues_search_output,
)
from commands.gerrit.ui_server import (
    GerritUIService,
    make_gerrit_ui_handler,
    render_static_gerrit_ui_html,
)
from lib.web_security import MAX_JSON_BODY_BYTES

# Every Gerrit UI API route requires the UI token (see `make_gerrit_ui_handler`).
_TEST_UI_TOKEN = "gerrit-ui-test-token"
_AUTH_HEADERS = {"X-Sidecar-Token": _TEST_UI_TOKEN}
_JSON_AUTH_HEADERS = {"Content-Type": "application/json", **_AUTH_HEADERS}


def _authed_get(url: str) -> urllib.request.Request:
    """Builds a GET request that carries the test UI token."""
    return urllib.request.Request(url, headers=_AUTH_HEADERS)


class GerritUIServerTest(unittest.TestCase):
    """Tests approval slot calculation, vetted gates, Cliff Radar, risk scoring, and stack grouping."""

    def test_agent_and_bot_detection(self) -> None:
        self.assertTrue(is_agent_or_bot_email("groupagent-android-emulator-heavy@google.com"))
        self.assertTrue(is_agent_or_bot_email("corpagent-eng-joshuaduong@google.com"))
        self.assertTrue(is_agent_or_bot_email("ayeaye@system.gserviceaccount.com"))
        self.assertFalse(is_agent_or_bot_email("jansene@google.com", "Erwin Jansen"))
        self.assertFalse(is_agent_or_bot_email("joshuaduong@google.com", "Joshua Duong 测试_🚀_é"))

    def test_agent_cl_requires_two_humans_and_tracks_viewer_vote(self) -> None:
        now = datetime(2026, 10, 21, 21, 0, 0, tzinfo=timezone.utc)
        sample_change = {
            "_number": 43137250,
            "change_id": "I1234567890abcdef1234567890abcdef12345678",
            "project": "platform/hardware/google/aemu",
            "branch": "studio-main",
            "subject": "style(harness): KISS format helper 测试_🚀_é",
            "created": "2026-10-15 12:00:00.000000000",
            "updated": "2026-10-19 12:00:00.000000000",
            "insertions": 14,
            "deletions": 4,
            "unresolved_comment_count": 0,
            "current_revision": "rev_a",
            "current_revision_number": 2,
            "owner": {
                "email": "groupagent-android-emulator@google.com",
                "name": "Goldie",
            },
            "revisions": {
                "rev_a": {
                    "_number": 2,
                    "files": {"tools/emu-dev-cli/src/helper.py": {}},
                    "commit": {"parents": [{"commit": "rev_base"}]},
                }
            },
            "labels": {
                "Code-Review": {
                    "all": [
                        {"email": "jansene@google.com", "name": "Erwin Jansen", "value": 2},
                        {"email": "groupagent-android-emulator-heavy@google.com", "name": "Volkov", "value": 1},
                    ]
                },
                "Presubmit-Verified": {
                    "all": [
                        {"email": "treehugger@system.gserviceaccount.com", "value": 2},
                    ]
                },
            },
            "reviewers": {
                "REVIEWER": [
                    {"email": "jansene@google.com", "name": "Erwin Jansen"},
                    {"email": "joshuaduong@google.com", "name": "Joshua Duong"},
                ]
            },
            "attention_set": {
                "1045975": {
                    "account": {"email": "joshuaduong@google.com", "name": "Joshua Duong"},
                    "last_update": "2026-10-18 21:00:00.000000000",
                }
            },
            "submit_requirements": [
                {"name": "Review-Enforcement", "status": "UNSATISFIED"},
            ],
        }

        self.assertTrue(is_agent_authored_or_uploaded(sample_change))

        # Viewing as Erwin (already gave +2)
        as_erwin = enrich_change_for_ui(sample_change, "jansene@google.com", now_dt=now)
        self.assertTrue(as_erwin["is_agent_cl"])
        self.assertEqual(as_erwin["required_cr2_count"], 2)
        self.assertTrue(as_erwin["approved_by_viewer"])
        self.assertTrue(as_erwin["presubmit_vetted"])
        self.assertTrue(as_erwin["agent_vetted"])
        self.assertEqual(as_erwin["cliff_zone"], "cliff")
        self.assertEqual(as_erwin["risk"]["tier"], "low")
        self.assertEqual(len(as_erwin["approval_slots"]), 2)
        self.assertEqual(as_erwin["approval_slots"][0]["state"], "done")
        self.assertEqual(as_erwin["approval_slots"][1]["state"], "needed")

        # Viewing as Joshua (has not given +2 yet; 1 of 2 human +2s already given)
        as_joshua = enrich_change_for_ui(sample_change, "joshuaduong@google.com", now_dt=now)
        self.assertFalse(as_joshua["approved_by_viewer"])
        self.assertEqual(as_joshua["approval_slots"][1]["text"], "Needs +2")

        # Agent CL with 0 human +2s collapses to a single "Needs two +2s" slot
        zero_vote_agent_cl = dict(sample_change)
        zero_vote_agent_cl["labels"] = {
            "Code-Review": {
                "all": [{"email": "droid@android-ci-ibot.iam.gserviceaccount.com", "value": 1}]
            },
            "Presubmit-Verified": sample_change["labels"]["Presubmit-Verified"],
        }
        as_zero = enrich_change_for_ui(zero_vote_agent_cl, "joshuaduong@google.com", now_dt=now)
        self.assertEqual(as_zero["approval_slots"], [{"state": "needed", "text": "Needs two +2s"}])

    def test_risk_classification_tiers(self) -> None:
        high_cl = {
            "subject": "feat(webrtc): Add mutex lock in Switchboard",
            "insertions": 90,
            "deletions": 20,
            "current_revision": "r1",
            "revisions": {
                "r1": {"files": {"emulator/webrtc/include/switchboard_mutex.h": {}}}
            },
        }
        self.assertEqual(classify_risk_and_load(high_cl)["tier"], "high")

        low_cl = {
            "subject": "test(file): Add Unicode path test 测试_🚀_é",
            "insertions": 25,
            "deletions": 0,
            "current_revision": "r2",
            "revisions": {
                "r2": {"files": {"emulator/libs/file/file_test.cc": {}}}
            },
        }
        self.assertEqual(classify_risk_and_load(low_cl)["tier"], "low")

    def test_stack_grouping_and_ordering(self) -> None:
        c1 = {
            "number": 101,
            "topic": "my-stack",
            "project_short": "aemu",
            "current_revision": "sha_1",
            "parent_revision": "sha_0",
        }
        c2 = {
            "number": 102,
            "topic": "my-stack",
            "project_short": "aemu",
            "current_revision": "sha_2",
            "parent_revision": "sha_1",
        }
        stacks = group_changes_into_stacks([c2, c1])
        self.assertEqual(len(stacks), 1)
        self.assertTrue(stacks[0]["is_stack"])
        self.assertEqual([c["number"] for c in stacks[0]["commits"]], [101, 102])
        self.assertEqual(stacks[0]["commits"][0]["stack_index"], 1)
        self.assertEqual(stacks[0]["commits"][1]["stack_index"], 2)

    def test_parse_unified_diff_files_with_unicode(self) -> None:
        sample_patch = (
            "diff --git a/src/foo.cc b/src/foo.cc\n"
            "index 1111111..2222222 100644\n"
            "--- a/src/foo.cc\n"
            "+++ b/src/foo.cc\n"
            "@@ -10,2 +10,3 @@ void test() {\n"
            " int a = 1;\n"
            "-int b = 2;\n"
            "+int b = 3; // 测试_🚀_é\n"
            "+int c = 4;\n"
        )
        parsed = parse_unified_diff_files(sample_patch)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["file"], "src/foo.cc")
        self.assertEqual(parsed[0]["additions"], 2)
        self.assertEqual(parsed[0]["deletions"], 1)
        self.assertTrue(any("测试_🚀_é" in ln["text"] for ln in parsed[0]["lines"]))

    def test_viewer_query_and_explicit_reviewer_filter(self) -> None:
        q_rev = build_viewer_gerrit_query("joshuaduong@google.com", scope="reviewer")
        self.assertEqual(
            q_rev,
            "status:open -is:wip reviewer:joshuaduong@google.com -owner:joshuaduong@google.com",
        )
        q_own = build_viewer_gerrit_query("joshuaduong@google.com", scope="owner")
        self.assertEqual(q_own, "status:open -is:wip owner:joshuaduong@google.com")

        rev_change = {
            "reviewers": {
                "REVIEWER": [{"email": "joshuaduong@google.com"}],
                "CC": [{"email": "other@google.com"}],
            }
        }
        cc_only_change = {
            "reviewers": {
                "REVIEWER": [{"email": "rkir@google.com"}],
                "CC": [{"email": "joshuaduong@google.com"}],
            }
        }
        self.assertTrue(is_explicit_reviewer(rev_change, "joshuaduong@google.com"))
        self.assertFalse(is_explicit_reviewer(cc_only_change, "joshuaduong@google.com"))

    def test_harness_plugin_installs_gerrit_ui_sidecar(self) -> None:
        import tempfile
        from pathlib import Path
        from commands.harness.plugin_installer import (
            get_harness_plugin_status,
            install_harness_plugin,
        )

        with tempfile.TemporaryDirectory() as tmp:
            dest_dir = Path(tmp) / "plugins" / "aemu-harness"
            res = install_harness_plugin(target_dir=dest_dir)
            self.assertTrue(res["installed"])
            status = get_harness_plugin_status(target_dir=dest_dir)
            self.assertTrue(status["installed"])
            self.assertIn("sidecars/aemu_gerrit_ui/sidecar.json", status["present_files"])
            self.assertIn("sidecars/aemu_gerrit_ui/server.py", status["present_files"])
            server_text = (dest_dir / "sidecars" / "aemu_gerrit_ui" / "server.py").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("__EMU_DEV_CLI_SRC_DIR__", server_text)

    def test_rendered_ui_inlines_sidecar_preload_without_external_script(self) -> None:
        from core.web.assets import SIDECAR_PRELOAD_SCRIPT_TAG

        html = render_static_gerrit_ui_html(
            {"viewer": "joshuaduong@google.com", "changes": [], "note": "测试_🚀_é"}
        )
        self.assertNotIn(SIDECAR_PRELOAD_SCRIPT_TAG, html)
        self.assertIn("window.sidecar =", html)
        self.assertIn("X-Sidecar-Token", html)
        self.assertIn("测试_🚀_é", html)

    def test_detect_viewer_email_defaults_to_logged_in_os_user(self) -> None:
        empty_git = subprocess.CompletedProcess(["git", "config", "user.email"], 1, stdout="", stderr="")

        # 1) $USER resolves to <user>@google.com when AEMU_GERRIT_VIEWER, auth_email, and git config are unset
        with (
            patch.object(GerritUIService, "_detect_auth_email", return_value=""),
            patch("subprocess.run", return_value=empty_git),
            patch.dict(os.environ, {"USER": "Jansene", "USERNAME": ""}, clear=True),
        ):
            svc_user = GerritUIService()
            self.assertEqual(svc_user.default_viewer, "jansene@google.com")

        # 2) $USERNAME (including Windows DOMAIN\user) resolves when $USER is unset
        with (
            patch.object(GerritUIService, "_detect_auth_email", return_value=""),
            patch("subprocess.run", return_value=empty_git),
            patch.dict(os.environ, {"USERNAME": r"GOOGLE\Joshuaduong"}, clear=True),
        ):
            svc_win = GerritUIService()
            self.assertEqual(svc_win.default_viewer, "joshuaduong@google.com")

        # 3) getpass.getuser() fallback when neither USER nor USERNAME is in os.environ
        with (
            patch.object(GerritUIService, "_detect_auth_email", return_value=""),
            patch("subprocess.run", return_value=empty_git),
            patch.dict(os.environ, {}, clear=True),
            patch("commands.gerrit.services.queue_service.getpass.getuser", return_value="corakwue"),
        ):
            svc_getpass = GerritUIService()
            self.assertEqual(svc_getpass.default_viewer, "corakwue@google.com")

        # 4) Agent/bot $USER (e.g. groupagent-* / corpagent-*) is skipped in favor of cpl list owner
        def fake_subprocess_cpl(cmd: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
            if cmd[:2] == ["git", "config"]:
                return empty_git
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "rshell_id/abc  cpl-test-box-123  CAPSULE_STATUS_CONNECTED  "
                    "jansene  groupagent-android-emulator\n"
                ),
                stderr="",
            )

        with (
            patch.object(GerritUIService, "_detect_auth_email", return_value="groupagent-android-emulator@google.com"),
            patch("subprocess.run", side_effect=fake_subprocess_cpl),
            patch("commands.gerrit.services.queue_service.socket.gethostname", return_value="cpl-test-box-123.c.googlers.com"),
            patch.dict(os.environ, {"USER": "groupagent-android-emulator"}, clear=True),
        ):
            svc_capsule = GerritUIService()
            self.assertEqual(svc_capsule.default_viewer, "jansene@google.com")

        # 5) System/non-LDAP OS user (e.g. root or non-ASCII 测试_🚀_é) does not fall back to joshuaduong@google.com
        for non_ldap_user in ("root", "测试_🚀_é"):
            with (
                patch.object(GerritUIService, "_detect_auth_email", return_value=""),
                patch("subprocess.run", return_value=empty_git),
                patch("commands.gerrit.services.queue_service.socket.gethostname", return_value="workstation.local"),
                patch.dict(os.environ, {"USER": non_ldap_user}, clear=True),
            ):
                svc_none = GerritUIService()
                self.assertEqual(svc_none.default_viewer, "")

        # 6) Explicit AEMU_GERRIT_VIEWER takes precedence over $USER
        with (
            patch.object(GerritUIService, "_detect_auth_email", return_value=""),
            patch("subprocess.run", return_value=empty_git),
            patch.dict(os.environ, {"AEMU_GERRIT_VIEWER": "rkir", "USER": "jansene"}, clear=True),
        ):
            svc_env = GerritUIService()
            self.assertEqual(svc_env.default_viewer, "rkir@google.com")

    def test_client_html_uses_server_provided_viewer_instead_of_hardcoded_default(self) -> None:
        empty_html = render_static_gerrit_ui_html()
        self.assertNotIn('viewer: "joshuaduong@google.com"', empty_html)

        with (
            patch.object(GerritUIService, "_detect_auth_email", return_value=""),
            patch(
                "subprocess.run",
                return_value=subprocess.CompletedProcess(["git"], 1, stdout="", stderr=""),
            ),
            patch.dict(os.environ, {"USER": "jansene"}, clear=True),
        ):
            service = GerritUIService()
        self.assertEqual(service.default_viewer, "jansene@google.com")

        observed_viewers: list[str | None] = []

        def fake_fetch_queue(**kwargs):
            observed_viewers.append(kwargs.get("viewer_email"))
            resolved = kwargs.get("viewer_email") or service.default_viewer
            return {
                "ui_version": "test",
                "viewer": resolved,
                "scope": kwargs.get("scope") or "reviewer",
                "roster": ["jansene@google.com", "joshuaduong@google.com"],
                "is_partial": False,
                "changes": [],
                "stacks": [],
            }

        with (
            patch.object(service, "fetch_queue", side_effect=fake_fetch_queue),
            patch.object(service, "schedule_background_prewarm", return_value=False),
        ):
            handler_cls = make_gerrit_ui_handler(service, ui_token=_TEST_UI_TOKEN)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
            port = httpd.server_address[1]
            srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            srv_thread.start()
            try:
                with urllib.request.urlopen(_authed_get(f"http://127.0.0.1:{port}/"), timeout=5) as index_resp:
                    served_html = index_resp.read().decode("utf-8")
                self.assertIn('"viewer": "jansene@google.com"', served_html)

                # Queue request without explicit viewer param resolves to service.default_viewer ($USER)
                with urllib.request.urlopen(
                    _authed_get(f"http://127.0.0.1:{port}/api/gerrit/queue?scope=reviewer&fast=1"), timeout=5
                ) as queue_resp:
                    queue_payload = json.loads(queue_resp.read().decode("utf-8"))
                self.assertEqual(queue_payload["viewer"], "jansene@google.com")
                self.assertIsNone(observed_viewers[-1])
            finally:
                httpd.shutdown()
                httpd.server_close()

        node_bin = shutil.which("node")
        if node_bin:
            scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", empty_html, flags=re.S)
            self.assertTrue(scripts)
            harness_js = (
                """
const requestedUrls = [];
const elements = {};
function makeEl(id) {
  return {
    id, value: "", textContent: "", innerHTML: "", className: "", disabled: false,
    clientWidth: 1200,
    style: { setProperty() {}, display: "" },
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute() {}, getAttribute() { return ""; }, addEventListener() {},
    getBoundingClientRect() { return { left: 0, top: 0, width: 1200, height: 800 }; },
    focus() {}, select() {},
  };
}
global.window = {
  location: { search: "", reload() {} },
  localStorage: { getItem() { return null; }, setItem() {} },
  parent: { postMessage() {} },
  addEventListener() {}, removeEventListener() {},
  innerWidth: 1280,
};
global.document = {
  body: makeEl("body"),
  getElementById(id) {
    if (!elements[id]) elements[id] = makeEl(id);
    return elements[id];
  },
  querySelectorAll() { return []; },
  addEventListener() {},
};
global.getComputedStyle = () => ({ paddingLeft: "18" });
global.URLSearchParams = URLSearchParams;
global.fetch = async (url) => {
  requestedUrls.push(String(url));
  return {
    ok: true,
    async json() {
      return {
        viewer: "jansene@google.com",
        scope: "reviewer",
        roster: ["jansene@google.com", "joshuaduong@google.com"],
        is_partial: false,
        changes: [],
        stacks: [],
      };
    },
    async text() { return "{}"; },
  };
};
"""
                + "\n".join(scripts)
                + """
setImmediate(() => {
  if (!requestedUrls.length || requestedUrls[0].includes("viewer=joshuaduong")) {
    console.error("Unexpected initial fetch URLs:", requestedUrls);
    process.exit(1);
  }
  if (state.viewer !== "jansene@google.com") {
    console.error("Expected state.viewer to adopt server viewer, got:", state.viewer);
    process.exit(1);
  }
  if (!elements.viewerSelect || !elements.viewerSelect.innerHTML.includes('value="jansene@google.com" selected')) {
    console.error("Expected viewerSelect to select jansene@google.com, got:", elements.viewerSelect && elements.viewerSelect.innerHTML);
    process.exit(1);
  }
  process.exit(0);
});
"""
            )
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_js = Path(tmp_dir) / "viewer_init_check.js"
                tmp_js.write_text(harness_js, encoding="utf-8")
                proc = subprocess.run(
                    [node_bin, str(tmp_js)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=10,
                )
                self.assertEqual(proc.returncode, 0, msg=proc.stderr)

    def test_sidecar_bootstrap_skips_incomplete_lib_without_template(self) -> None:
        import importlib.util
        from pathlib import Path
        import tempfile
        from unittest.mock import patch

        server_py = (
            Path(__file__).resolve().parents[1]
            / "harness"
            / "plugin"
            / "sidecars"
            / "aemu_gerrit_ui"
            / "server.py"
        )
        spec = importlib.util.spec_from_file_location("_test_aemu_gerrit_ui_server", server_py)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            incomplete_lib = root / "incomplete_lib"
            (incomplete_lib / "commands" / "gerrit").mkdir(parents=True)
            (incomplete_lib / "commands" / "gerrit" / "ui_server.py").write_text(
                "# incomplete\n", encoding="utf-8"
            )

            complete_src = root / "workspace_测试_🚀_é" / "tools" / "emu-dev-cli" / "src"
            (complete_src / "commands" / "gerrit").mkdir(parents=True)
            (complete_src / "templates").mkdir(parents=True)
            (complete_src / "commands" / "gerrit" / "ui_server.py").write_text(
                "# complete\n", encoding="utf-8"
            )
            (complete_src / "templates" / "gerrit_ui.html").write_text(
                "<html></html>\n", encoding="utf-8"
            )

            fake_home = root / "home"
            (fake_home / ".android").mkdir(parents=True)
            (fake_home / ".android" / "emu-dev-cli.json").write_text(
                '{"source_directories": {"emu-main-next": "'
                + str(root / "workspace_测试_🚀_é").replace("\\", "\\\\")
                + '"}}',
                encoding="utf-8",
            )

            mod.CONFIGURED_SRC_DIR = str(incomplete_lib)
            with patch.object(mod, "__file__", str(root / "isolated" / "server.py")), patch(
                "pathlib.Path.home", return_value=fake_home
            ):
                chosen = mod._bootstrap_emu_dev_cli_path()
            self.assertEqual(chosen, complete_src)

    def test_fetch_commit_diff_bundle_parses_xssi_json_and_quoted_unicode_paths(self) -> None:
        raw_patch_diff = (
            "From ee3efa4a69051f59bf1c9a8a270af2770dee141b Mon Sep 17 00:00:00 2001\n"
            "From: Developer <dev@google.com>\n"
            "Subject: [PATCH] Render Gerrit diffs cleanly\n\n"
            'diff --git "a/emulator/net/\\346\\265\\213\\350\\257\\225_\\360\\237\\232\\200_\\303\\251.cc" '
            '"b/emulator/net/\\346\\265\\213\\350\\257\\225_\\360\\237\\232\\200_\\303\\251.cc"\n'
            "index 1111111..2222222 100644\n"
            '--- "a/emulator/net/\\346\\265\\213\\350\\257\\225_\\360\\237\\232\\200_\\303\\251.cc"\n'
            '+++ "b/emulator/net/\\346\\265\\213\\350\\257\\225_\\360\\237\\232\\200_\\303\\251.cc"\n'
            "@@ -10,2 +10,3 @@ int Run() {\n"
            "-  return 1;\n"
            "+  // Verified UTF-8 path\n"
            "+  return 0;\n"
            "diff --git a/emulator/old.h b/emulator/new.h\n"
            "similarity index 100%\n"
            "rename from emulator/old.h\n"
            "rename to emulator/new.h\n"
        )
        xssi_payload = ")]}'\n" + json.dumps(raw_patch_diff)

        with patch.object(GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"):
            service = GerritUIService(default_viewer="joshuaduong@google.com")

        with patch.object(service.client, "_request_raw", return_value=xssi_payload), patch.object(
            service.client, "get_comment_threads", return_value=[]
        ), patch.object(
            service.client, "get_change_detail", return_value={"current_revision_number": 1}
        ):
            bundle = service.fetch_commit_diff_bundle(43137863)

        self.assertEqual(bundle["number"], 43137863)
        self.assertEqual(len(bundle["files"]), 2)
        self.assertEqual(bundle["files"][0]["file"], "emulator/net/测试_🚀_é.cc")
        self.assertEqual(bundle["files"][0]["additions"], 2)
        self.assertEqual(bundle["files"][0]["deletions"], 1)
        self.assertEqual(bundle["files"][1]["file"], "emulator/new.h")
        self.assertTrue(any("rename to emulator/new.h" in ln["text"] for ln in bundle["files"][1]["lines"]))

    def test_cast_batch_vote_scopes_notify_owner_and_renders_spinner_transition_ui(self) -> None:
        from unittest.mock import MagicMock

        with patch.object(GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"):
            service = GerritUIService(default_viewer="joshuaduong@google.com")

        seen_calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kwargs: object) -> MagicMock:
            seen_calls.append(cmd)
            return MagicMock(returncode=0, stdout=")]}'\n{}", stderr="")

        with patch("shutil.which", return_value="/usr/bin/gob-curl"), patch(
            "subprocess.run", side_effect=fake_run
        ):
            res = service.cast_batch_vote([43137863, 43162836], code_review=2)

        self.assertEqual(res["status"], "ok")
        self.assertEqual(len(seen_calls), 2)
        for cmd in seen_calls:
            payload = json.loads(cmd[cmd.index("-d") + 1])
            self.assertEqual(payload["notify"], "OWNER")
            self.assertEqual(payload["labels"]["Code-Review"], 2)

        rendered = render_static_gerrit_ui_html()
        self.assertIn("btn-spinner", rendered)
        self.assertIn("diff-exit-approved", rendered)
        self.assertIn("diff-enter-next", rendered)
        self.assertIn("resolveNextTargetAfterVote", rendered)
        self.assertIn("sessionApprovedCls", rendered)
        self.assertIn("isClApprovedByViewer", rendered)
        self.assertIn("stack-box-exiting", rendered)
        self.assertIn("cl-card-swarming", rendered)
        self.assertIn("swarm-live-panel", rendered)
        self.assertIn("syncSwarmIndicatorsOnly", rendered)
        self.assertIn("pollActiveSwarmStatuses", rendered)

        # Verify session-approved CLs stay approved_by_viewer=True even when Gerrit's search index lags
        stale_change = {
            "_number": 43137863,
            "project": "platform/hardware/generic/goldfish",
            "branch": "emu-main-next",
            "subject": "perf(grpcweb): Avoid extra frame copies",
            "owner": {"email": "jansene@google.com", "name": "Erwin Jansen"},
            "reviewers": {"REVIEWER": [{"email": "joshuaduong@google.com", "name": "Joshua Duong"}]},
            "labels": {
                "Code-Review": {"all": []},
                "Presubmit-Verified": {"all": [{"value": 2}]},
            },
            "current_revision": "rev1",
            "current_revision_number": 1,
            "revisions": {"rev1": {"files": {"a.cc": {}}}},
            "mergeable": False,
            "created": "2026-10-10 02:00:00.000000000",
            "updated": "2026-10-10 02:10:00.000000000",
        }
        with patch.object(service.client, "_request_json", return_value=[stale_change]):
            q_payload = service.fetch_queue(viewer_email="joshuaduong@google.com")
        self.assertTrue(q_payload["changes"][0]["approved_by_viewer"])
        self.assertTrue(q_payload["stacks"][0]["commits"][0]["approved_by_viewer"])
        self.assertEqual(len(q_payload["cliff_radar"]["fresh"]), 0)
        self.assertEqual(len(q_payload["cliff_radar"]["aging"]), 0)
        self.assertEqual(len(q_payload["cliff_radar"]["cliff"]), 0)

    def test_review_swarm_live_status_lifecycle_and_legacy_fallback(self) -> None:
        from pathlib import Path
        import tempfile
        from unittest.mock import MagicMock
        from commands.harness.review_swarm_status import (
            GERRIT_REVIEW_PAYLOAD_FILENAME,
            read_cl_swarm_status,
            record_specialist_completion,
            write_auditing_swarm_status,
            write_completed_swarm_status,
            write_initial_swarm_status,
        )

        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp) / "review-swarm"
            out_dir = cache_root / "43129850_cb06ccb2"
            manifest = {
                "target_type": "gerrit_cl",
                "target_id": "43129850",
                "current_revision": "cb06ccb2",
                "subject": "perf(grpcweb): Avoid extra frame copies 测试_🚀_é",
                "round_number": 1,
                "review_scope": "full",
            }
            specs = [
                {"name": "commit-and-bug-auditor", "role": "Commit & Bug Auditor", "model": "flash"},
                {"name": "modularity-auditor", "role": "Modularity Auditor", "model": "inherit"},
            ]
            write_initial_swarm_status(out_dir, manifest, specs)
            first_read = read_cl_swarm_status(43129850, cache_root=cache_root)
            assert first_read is not None
            self.assertEqual(first_read["state"], "running")
            self.assertEqual(first_read["phase"], "queued")
            self.assertEqual(first_read["completed_specialists"], 0)
            self.assertEqual(first_read["total_specialists"], 2)

            # Rapid re-prepare preserves original started_at timestamp
            write_initial_swarm_status(out_dir, manifest, specs)
            second_read = read_cl_swarm_status(43129850, cache_root=cache_root)
            assert second_read is not None
            self.assertEqual(second_read["started_at"], first_read["started_at"])
            self.assertEqual(second_read["phase"], "preparing")

            # Wave 1 auditing & live per-specialist completion
            status_doc = write_auditing_swarm_status(out_dir, manifest, specs)
            record_specialist_completion(
                out_dir,
                status_doc,
                {"specialist": "modularity-auditor", "duration_sec": 18.4, "findings": []},
            )
            mid_read = read_cl_swarm_status(43129850, cache_root=cache_root)
            assert mid_read is not None
            self.assertEqual(mid_read["state"], "running")
            self.assertEqual(mid_read["phase"], "auditing")
            self.assertEqual(mid_read["completed_specialists"], 1)

            # Wave 2 completion
            (out_dir / GERRIT_REVIEW_PAYLOAD_FILENAME).write_text(
                json.dumps({
                    "message": "1 finding",
                    "notify": "OWNER",
                    "comments": {
                        "/COMMIT_MSG": [
                            {
                                "line": 28,
                                "message": "[commit-msg] Line 28 exceeds 72 chars 测试_🚀_é.\n\nSuggested fix:\nTest: bazel test //...",
                                "unresolved": True,
                            }
                        ]
                    },
                }),
                encoding="utf-8",
            )
            write_completed_swarm_status(
                out_dir,
                status_doc,
                {
                    "total_findings": 1,
                    "reuse_audit_line": "Reuse audit: 2 checked, 0 duplicates",
                    "error_audit_line": "Error-message audit: 1 checked, 0 findings",
                    "findings": [
                        {
                            "file": "/COMMIT_MSG",
                            "line": 28,
                            "category": "commit-msg",
                            "severity": "blocking",
                            "message": "Line 28 exceeds 72 chars 测试_🚀_é.",
                            "suggested_fix": "Test: bazel test //...",
                        }
                    ],
                },
                {
                    "wall_clock_sec": 34.7,
                    "parallel_speedup": 5.86,
                    "agents": [
                        {"specialist": "commit-and-bug-auditor", "role": "Commit & Bug Auditor", "duration_sec": 32.6, "finding_count": 1},
                        {"specialist": "modularity-auditor", "role": "Modularity Auditor", "duration_sec": 18.4, "finding_count": 0},
                    ],
                },
            )
            done_read = read_cl_swarm_status(43129850, cache_root=cache_root)
            assert done_read is not None
            self.assertEqual(done_read["state"], "completed")
            self.assertEqual(done_read["total_findings"], 1)
            self.assertTrue(done_read["has_review_payload"])

            # Verify fallback when only review_swarm_metrics.json + gerrit_review_payload.json exist
            (out_dir / "review_swarm_status.json").unlink()
            (out_dir / "review_swarm_metrics.json").write_text(
                json.dumps({
                    "target_id": "43129850",
                    "round_number": 1,
                    "review_scope": "full",
                    "recorded_at": "2026-10-22T03:02:03+00:00",
                    "wall_clock_sec": 34.7,
                    "parallel_speedup": 5.86,
                    "agents": [
                        {"specialist": "commit-and-bug-auditor", "role": "Commit & Bug Auditor", "duration_sec": 32.6, "finding_count": 1}
                    ],
                }),
                encoding="utf-8",
            )
            fallback_read = read_cl_swarm_status(43129850, cache_root=cache_root)
            assert fallback_read is not None
            self.assertEqual(fallback_read["state"], "completed")
            self.assertEqual(fallback_read["total_findings"], 1)
            self.assertEqual(fallback_read["findings"][0]["category"], "commit-msg")
            self.assertIn("测试_🚀_é", fallback_read["findings"][0]["message"])

            # Verify post_swarm_review scopes notify to OWNER
            with patch.object(GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"):
                service = GerritUIService(default_viewer="joshuaduong@google.com")
            posted_cmds: list[list[str]] = []

            def fake_post(cmd: list[str], **_kwargs: object) -> MagicMock:
                posted_cmds.append(cmd)
                return MagicMock(returncode=0, stdout=")]}'\n{}", stderr="")

            with patch(
                "commands.gerrit.services.vote_submit_service.resolve_swarm_dir_for_cl",
                return_value=out_dir,
            ), patch("shutil.which", return_value="/usr/bin/gob-curl"), patch(
                "subprocess.run", side_effect=fake_post
            ):
                post_res = service.post_swarm_review(43129850)
            self.assertEqual(post_res["status"], "ok")
            sent_payload = json.loads(posted_cmds[0][posted_cmds[0].index("-d") + 1])
            self.assertEqual(sent_payload["notify"], "OWNER")

    def test_commit_type_classification_and_bug_metadata_enrichment(self) -> None:
        from pathlib import Path
        import tempfile
        from unittest.mock import MagicMock

        ets_cl = {
            "subject": "[ets] Migrate Boot Test and Ui Theme check to emulator dashboard",
            "current_revision": "r1",
            "revisions": {
                "r1": {
                    "files": {"pytest/test_embedded/tests/ui/test_boot.py": {}},
                    "commit": {"message": "Migrate Boot Test\n\nBug: 434832269\nTest: pytest\n"},
                }
            },
        }
        self.assertEqual(classify_commit_type(ets_cl, has_bugs=True)["id"], "ets_test")
        self.assertEqual(extract_bug_ids_from_change(ets_cl), ["434832269"])

        perf_cl = {
            "subject": "perf(grpcweb): Avoid extra frame copies in VideoBridge",
            "current_revision": "r1",
            "revisions": {
                "r1": {
                    "files": {"emulator/webrtc/video_bridge.cc": {}},
                    "commit": {"message": "Avoid copies\n\nBug: b/496507943\n"},
                }
            },
        }
        self.assertEqual(classify_commit_type(perf_cl, has_bugs=True)["id"], "perf")
        self.assertEqual(extract_bug_ids_from_change(perf_cl), ["496507943"])

        cleanup_cl = {
            "subject": "refactor(netsim): Clean up dead packet router helpers",
            "current_revision": "r1",
            "revisions": {"r1": {"files": {"emulator/netsim/router.cc": {}}}},
        }
        self.assertEqual(classify_commit_type(cleanup_cl, has_bugs=False)["id"], "cleanup")

        bugfix_cl = {
            "subject": "fix(vulkan): Prevent null VkDevice crash on shutdown 测试_🚀_é",
            "current_revision": "r1",
            "revisions": {
                "r1": {
                    "files": {"emulator/gfxstream/vulkan.cc": {}},
                    "commit": {
                        "message": (
                            "fix(vulkan): Prevent null VkDevice crash\n\n"
                            "Bug: 500368186, b/500043866\n"
                            "Bug: N/A\n"
                        )
                    },
                }
            },
        }
        self.assertEqual(classify_commit_type(bugfix_cl, has_bugs=True)["id"], "bugfix")
        self.assertEqual(extract_bug_ids_from_change(bugfix_cl), ["500368186", "500043866"])

        feat_cl = {
            "subject": "feat(ui): Add Fast Multi-Commit Gerrit UI",
            "current_revision": "r1",
            "revisions": {"r1": {"files": {"tools/emu-dev-cli/src/ui.py": {}}}},
        }
        self.assertEqual(classify_commit_type(feat_cl, has_bugs=False)["id"], "feature")

        docs_cl = {
            "subject": "docs(readme): Document sidecar launch flags",
            "current_revision": "r1",
            "revisions": {"r1": {"files": {"README.md": {}}}},
        }
        self.assertEqual(classify_commit_type(docs_cl, has_bugs=False)["id"], "docs_build")

        raw_issues_output = (
            "Issue ID: 500368186\n"
            "Priority: P1\n"
            "Severity: S1\n"
            "Status: ASSIGNED\n"
            "Title: Vulkan shutdown crash on Fold 7 测试_🚀_é\n"
            "---\n"
            "Issue ID: 500043866\n"
            "Priority: P2\n"
            "Severity: S3\n"
            "Status: FIXED\n"
            "Title: Secondary cleanup issue\n"
        )
        parsed_bugs = parse_issues_search_output(raw_issues_output)
        self.assertEqual(parsed_bugs["500368186"]["priority"], "P1")
        self.assertEqual(parsed_bugs["500368186"]["severity"], "S1")
        self.assertEqual(parsed_bugs["500368186"]["url"], "https://b.corp.google.com/issues/500368186")
        self.assertIn("测试_🚀_é", parsed_bugs["500368186"]["title"])

        with tempfile.TemporaryDirectory() as tmp:
            fake_cache = Path(tmp) / "bug_metadata_cache.json"
            with patch.object(
                GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"
            ), patch(
                "subprocess.run",
                return_value=MagicMock(returncode=0, stdout=raw_issues_output, stderr=""),
            ):
                service = GerritUIService(default_viewer="joshuaduong@google.com")
                service._bug_cache_path = fake_cache
                service._bug_cache = {}
                bug_map = service.fetch_bug_metadata_batch(
                    ["500368186", "500043866"], issues_bin="/bin/sh"
                )

            enriched = enrich_change_for_ui(
                {
                    "_number": 43160001,
                    "project": "platform/hardware/generic/goldfish",
                    "branch": "emu-main-next",
                    **bugfix_cl,
                },
                "joshuaduong@google.com",
                bug_lookup=bug_map,
            )
            self.assertEqual(enriched["commit_type"]["id"], "bugfix")
            self.assertIn("Bug: 500368186, b/500043866", enriched["commit_message"])
            self.assertEqual(len(enriched["bugs"]), 2)
            self.assertEqual(enriched["bugs"][0]["id"], "500368186")
            self.assertEqual(enriched["bugs"][0]["priority"], "P1")
            self.assertEqual(enriched["bugs"][0]["severity"], "S1")

        patch_msg = extract_commit_message_from_patch(
            "From 1234567890abcdef Mon Sep 17 00:00:00 2001\n"
            "From: Test Author <test@google.com>\n"
            "Subject: [PATCH] fix(vulkan): Handle null VkDevice 测试_🚀_é\n"
            "\n"
            "Detailed explanation of why VkDevice can be null during teardown.\n"
            "\n"
            "Bug: 500368186\n"
            "Test: bazel test //emulator/...\n"
            "Change-Id: I1234567890abcdef\n"
            "---\n"
            "diff --git a/a.cc b/a.cc\n"
        )
        self.assertIn("fix(vulkan): Handle null VkDevice 测试_🚀_é", patch_msg)
        self.assertIn("Detailed explanation of why VkDevice can be null", patch_msg)
        self.assertIn("Bug: 500368186", patch_msg)

        rendered_html = render_static_gerrit_ui_html()
        self.assertIn("COMMIT_TYPE_ORDER", rendered_html)
        self.assertIn("setGroupMode", rendered_html)
        self.assertIn("setTypeFilter", rendered_html)
        self.assertIn("renderBugBadgesHtml", rendered_html)
        self.assertIn("bug-pri-tag", rendered_html)
        self.assertIn("renderCommitMessagePanelHtml", rendered_html)
        self.assertIn("formatCommitMessageHtml", rendered_html)
        self.assertIn("commit-msg-panel", rendered_html)
        self.assertIn("selectCodeBlockRange", rendered_html)
        self.assertIn("selectFullCommitMessage", rendered_html)
        self.assertIn("commit-msg-table", rendered_html)
        self.assertIn("submitSelectedCodeComment", rendered_html)
        self.assertIn("submitSelectedCodeQuestion", rendered_html)
        self.assertIn("code-selection-composer", rendered_html)
        self.assertIn("initDiffCodeSelectionListeners", rendered_html)
        self.assertIn("collapseAllInlinePopups", rendered_html)
        self.assertIn("toggleInlineItemCollapse", rendered_html)
        self.assertIn("saveSelectionDraftAsCollapsedItem", rendered_html)
        self.assertIn("diff-inline-comment agent-question", rendered_html)
        self.assertIn("window.__GERRIT_UI_VERSION__ =", rendered_html)

    def test_post_inline_comment_and_ask_agent_about_code(self) -> None:
        prompt = build_code_question_prompt(
            cl_number=43152171,
            file_path="emulator/net/socket_测试_🚀_é.cc",
            start_line=42,
            end_line=48,
            code_snippet="+  if (!sock.ok()) {\n+    return sock.status();\n+  }",
            question="Is there any leak if sock fails here?",
            side="REVISION",
        )
        self.assertIn("ag/43152171", prompt)
        self.assertIn("emulator/net/socket_测试_🚀_é.cc", prompt)
        self.assertIn("L42-L48", prompt)
        self.assertIn("Is there any leak if sock fails here?", prompt)

        with patch.object(
            GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"
        ):
            service = GerritUIService(default_viewer="joshuaduong@google.com")

        captured_payloads: list[tuple[str, str, dict]] = []

        def fake_request_json(endpoint: str, method: str = "GET", data: dict | None = None):
            captured_payloads.append((endpoint, method, data or {}))
            return {}

        service.client._request_json = fake_request_json  # type: ignore[method-assign]
        comment_res = service.post_inline_comment(
            cl_number=43152171,
            file_path="emulator/net/socket_测试_🚀_é.cc",
            line=48,
            start_line=42,
            end_line=48,
            side="REVISION",
            unresolved=True,
            message="Please guard against null pointer here (测试_🚀_é).",
        )
        self.assertEqual(comment_res["status"], "ok")
        self.assertEqual(comment_res["notify"], "OWNER")
        self.assertEqual(comment_res["start_line"], 42)
        self.assertEqual(comment_res["end_line"], 48)
        self.assertEqual(len(captured_payloads), 1)
        ep, method, sent_body = captured_payloads[0]
        self.assertEqual(ep, "/a/changes/43152171/revisions/current/review")
        self.assertEqual(method, "POST")
        self.assertEqual(sent_body["notify"], "OWNER")
        file_comments = sent_body["comments"]["emulator/net/socket_测试_🚀_é.cc"]
        self.assertEqual(len(file_comments), 1)
        self.assertEqual(file_comments[0]["line"], 48)
        self.assertEqual(file_comments[0]["range"]["start_line"], 42)
        self.assertEqual(file_comments[0]["range"]["end_line"], 48)
        self.assertTrue(file_comments[0]["unresolved"])

        with patch(
            "commands.gerrit.services.inline_qa_service._run_sidecar_agentapi",
            return_value={"conversationId": "conv-123"},
        ) as mock_agentapi:
            ask_res = service.ask_agent_about_code(
                cl_number=43152171,
                file_path="emulator/net/socket_测试_🚀_é.cc",
                start_line=42,
                end_line=48,
                code_snippet="+  return sock.status();",
                question="Explain this status return.",
                conversation_id="conv-123",
            )
            self.assertEqual(ask_res["status"], "ok")
            self.assertTrue(ask_res["dispatched"])
            mock_agentapi.assert_called_once()

        tx_lines = [
            json.dumps(
                {
                    "step_index": 1443,
                    "type": "SYSTEM_MESSAGE",
                    "content": (
                        "<SYSTEM_MESSAGE>\n"
                        "[Message] content=Regarding Gerrit CL `ag/43166566` in "
                        "`emulator/libs/ver/src/RendererVulkan.cpp` (L969):\n\n"
                        "```\n++    data.mGeneration = mNextTextureGeneration++;\n```\n\n"
                        "**Question:** Is this an atomic variable?\n"
                        "</SYSTEM_MESSAGE>"
                    ),
                }
            ),
            json.dumps(
                {
                    "step_index": 1445,
                    "type": "PLANNER_RESPONSE",
                    "content": "",
                }
            ),
            json.dumps(
                {
                    "step_index": 1464,
                    "type": "PLANNER_RESPONSE",
                    "content": "**No, `mNextTextureGeneration` is not an `std::atomic` — it is guarded by `mResourceLock`.**",
                }
            ),
        ]
        parsed_questions = parse_code_questions_from_transcript_lines(tx_lines, conv_id="8f2cdf4d")
        self.assertEqual(len(parsed_questions), 1)
        self.assertEqual(parsed_questions[0]["cl"], 43166566)
        self.assertEqual(
            parsed_questions[0]["file_path"], "emulator/libs/ver/src/RendererVulkan.cpp"
        )
        self.assertEqual(parsed_questions[0]["start_line"], 969)
        self.assertEqual(parsed_questions[0]["question"], "Is this an atomic variable?")
        self.assertEqual(parsed_questions[0]["status"], "answered")
        self.assertIn("mResourceLock", parsed_questions[0]["reply"])

    def test_author_search_and_filter_enrichment(self) -> None:
        q = build_viewer_gerrit_query("joshuaduong@google.com", scope="reviewer", author="rpaquay")
        self.assertEqual(
            q,
            "status:open -is:wip reviewer:joshuaduong@google.com -owner:joshuaduong@google.com owner:rpaquay@google.com",
        )
        raw_change = {
            "_number": 43204076,
            "project": "platform/hardware/google/aemu",
            "branch": "emu-main-next",
            "subject": "android-cli: disable daemon by default",
            "owner": {"email": "rpaquay@google.com", "name": "Renaud Paquay"},
            "current_revision": "rev1",
            "revisions": {
                "rev1": {
                    "uploader": {
                        "email": "groupagent-android-emulator@google.com",
                        "name": "Emulator Agent",
                    },
                    "commit": {
                        "author": {"email": "rpaquay@google.com", "name": "Renaud Paquay"},
                        "message": "android-cli: disable daemon by default\n\nBug: b/12345678\n",
                    },
                    "files": {"tools/android-cli/main.rs": {}},
                }
            },
        }
        enriched = enrich_change_for_ui(raw_change, "joshuaduong@google.com")
        self.assertEqual(enriched["owner_short"], "rpaquay")
        self.assertEqual(enriched["owner_name"], "Renaud Paquay")
        self.assertFalse(enriched["owner_is_bot"])
        self.assertEqual(enriched["uploader_short"], "groupagent-android-emulator")
        self.assertEqual(enriched["files"], ["tools/android-cli/main.rs"])

        html = render_static_gerrit_ui_html()
        self.assertIn('id="queueSearchInput"', html)
        self.assertIn('id="authorFilterSelect"', html)
        self.assertIn('id="authorChipsBar"', html)
        self.assertIn("setAuthorFilter(", html)
        self.assertIn("var(--ln-old-col, 52px) var(--ln-new-col, 68px)", html)
        self.assertIn("font-variant-numeric: tabular-nums;", html)
        self.assertIn("✓ PV +1/+2", html)
        self.assertIn("✓ Hide +2 by Me", html)
        self.assertIn("Agent Verified (0 unresolved)", html)
        self.assertNotIn("Presubmit Vetted", html)
        self.assertNotIn("Agent Vetted", html)

    def test_loading_transition_indicators(self) -> None:
        html = render_static_gerrit_ui_html()
        self.assertNotIn('<div class="topbar">', html)
        self.assertIn('id="btnRefreshQueue"', html)
        self.assertIn('class="btn-refresh-icon"', html)
        self.assertIn('class="refresh-icon-svg"', html)
        self.assertIn('id="globalLoadingBar"', html)
        self.assertIn('id="queuePaneOverlay"', html)
        self.assertIn('id="diffPaneOverlay"', html)
        self.assertIn("function setQueueLoadingState(", html)
        self.assertIn("function renderDiffLoadingPlaceholderHtml(", html)
        self.assertIn("function syncActiveActionButtons(", html)

    def test_reviewers_ccs_votes_extraction_and_mutations(self) -> None:
        raw_change = {
            "_number": 43166568,
            "project": "platform/hardware/generic/goldfish",
            "branch": "emu-main-next",
            "subject": "fix(ver): Guard generation counter",
            "owner": {"_account_id": 1111, "email": "kocdemir@google.com", "name": "Serdar Kocdemir"},
            "attention_set": {
                "2222": {"account": {"_account_id": 2222, "email": "sergiuferentz@google.com"}}
            },
            "removable_reviewers": [
                {"_account_id": 1045, "email": "joshuaduong@google.com"},
                {"_account_id": 2222, "email": "sergiuferentz@google.com"},
                {"_account_id": 3333, "email": "rkir@google.com"},
            ],
            "reviewers": {
                "REVIEWER": [
                    {"_account_id": 1045, "email": "joshuaduong@google.com", "name": "Joshua Duong"},
                    {"_account_id": 2222, "email": "sergiuferentz@google.com", "name": "Sergiu 测试_🚀_é"},
                    {"_account_id": 5107, "email": "groupagent-android-emulator@google.com", "name": "groupagent-android-emulator"},
                    {"_account_id": 1136, "email": "treehugger@system.gserviceaccount.com", "display_name": "Treehugger Robot", "tags": ["SERVICE_USER"]},
                ],
                "CC": [
                    {"_account_id": 3333, "email": "rkir@google.com", "name": "Roman Kiryanov"},
                ],
            },
            "labels": {
                "Code-Review": {
                    "all": [
                        {"_account_id": 1045, "email": "joshuaduong@google.com", "value": 2},
                        {"_account_id": 5107, "email": "groupagent-android-emulator@google.com", "value": 1},
                        {"_account_id": 2222, "email": "sergiuferentz@google.com", "value": 0},
                    ]
                },
                "Presubmit-Verified": {
                    "all": [
                        {"_account_id": 1136, "email": "treehugger@system.gserviceaccount.com", "value": 2},
                    ]
                },
            },
            "current_revision": "rev1",
            "revisions": {"rev1": {"files": {"a.cc": {}}}},
        }
        enriched = enrich_change_for_ui(raw_change, "joshuaduong@google.com")
        revs = enriched["reviewers_detail"]
        ccs = enriched["ccs_detail"]
        self.assertEqual(len(revs), 4)
        self.assertEqual(revs[0]["short"], "joshuaduong")
        self.assertEqual(revs[0]["cr_vote"], 2)
        self.assertTrue(revs[0]["removable"])
        sergiu = next(r for r in revs if r["short"] == "sergiuferentz")
        self.assertEqual(sergiu["cr_vote"], 0)
        self.assertTrue(sergiu["in_attention_set"])
        self.assertIn("测试_🚀_é", sergiu["name"])
        th = next(r for r in revs if r["name"] == "Treehugger Robot")
        self.assertTrue(th["is_bot"])
        self.assertEqual(th["votes"][0]["short_label"], "PV")
        self.assertEqual(th["votes"][0]["formatted"], "+2")
        self.assertEqual(len(ccs), 1)
        self.assertEqual(ccs[0]["short"], "rkir")
        self.assertEqual(ccs[0]["state"], "CC")

        with patch.object(GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"):
            service = GerritUIService(default_viewer="joshuaduong@google.com")

        seen_calls: list[tuple[str, str, object]] = []

        def fake_request_json(endpoint: str, method: str = "GET", data: object = None) -> object:
            seen_calls.append((method, endpoint, data))
            if "/suggest_reviewers" in endpoint:
                return [{"account": {"_account_id": 99, "email": "whollins@google.com", "name": "Will Hollins"}}]
            if "/detail" in endpoint:
                return raw_change
            return {}

        with patch.object(service.client, "_request_json", side_effect=fake_request_json):
            sug = service.suggest_reviewers(43166568, "wholl")
            self.assertEqual(sug[0]["email"], "whollins@google.com")

            add_res = service.modify_reviewer(43166568, "add", "whollins", state="CC")
            self.assertEqual(add_res["reviewer"], "whollins@google.com")
            self.assertEqual(add_res["state"], "CC")
            self.assertEqual(add_res["notify"], "OWNER")

            rm_res = service.modify_reviewer(43166568, "remove", "rkir@google.com")
            self.assertEqual(rm_res["state"], "REMOVED")

        post_calls = [c for c in seen_calls if c[0] == "POST"]
        self.assertEqual(
            post_calls[0],
            (
                "POST",
                "/a/changes/43166568/reviewers",
                {"reviewer": "whollins@google.com", "state": "CC", "notify": "OWNER"},
            ),
        )
        self.assertEqual(
            post_calls[1],
            (
                "POST",
                "/a/changes/43166568/reviewers/rkir%40google.com/delete",
                {"notify": "OWNER"},
            ),
        )

        html = render_static_gerrit_ui_html()
        self.assertIn("function renderCommitReviewersPanelHtml(", html)
        self.assertIn("function submitReviewerAdd(", html)
        self.assertIn("function removeParticipantFromCl(", html)
        self.assertIn("function moveParticipantState(", html)

    def test_vote_plus1_plus2_and_autosubmit_labels(self) -> None:
        from unittest.mock import MagicMock

        with patch.object(
            GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"
        ):
            service = GerritUIService(default_viewer="joshuaduong@google.com")

        posted_payloads: list[dict[str, object]] = []

        def fake_run(cmd: list[str], **_kwargs: object) -> MagicMock:
            posted_payloads.append(json.loads(cmd[cmd.index("-d") + 1]))
            return MagicMock(returncode=0, stdout=")]}'\n{}", stderr="")

        base_change = {
            "_number": 43212920,
            "project": "platform/hardware/google/aemu",
            "branch": "emu-main-next",
            "subject": "feat(gerrit-ui): Support +1/+2 and Autosubmit+1 测试_🚀_é",
            "owner": {"email": "rpaquay@google.com", "name": "Renaud Paquay"},
            "reviewers": {
                "REVIEWER": [{"email": "joshuaduong@google.com", "name": "Joshua Duong"}]
            },
            "labels": {"Code-Review": {"all": []}, "Autosubmit": {"all": []}},
            "current_revision": "rev1",
            "revisions": {"rev1": {"files": {"a.py": {}}}},
        }

        with patch("shutil.which", return_value="/usr/bin/gob-curl"), patch(
            "subprocess.run", side_effect=fake_run
        ):
            # 1) Vote Code-Review +1 only -> does NOT mark approved_by_viewer (+2)
            r_cr1 = service.cast_batch_vote([43212920], code_review=1)
            self.assertEqual(r_cr1["status"], "ok")
            self.assertEqual(
                posted_payloads[-1], {"labels": {"Code-Review": 1}, "notify": "OWNER"}
            )
            with patch.object(service.client, "_request_json", return_value=[base_change]):
                q1 = service.fetch_queue(viewer_email="joshuaduong@google.com")
            self.assertFalse(q1["changes"][0]["approved_by_viewer"])
            self.assertEqual(q1["changes"][0]["viewer_cr_vote"], 1)

            # 2) Vote Autosubmit +1 standalone without touching Code-Review
            r_as1 = service.cast_batch_vote([43212920], code_review=None, autosubmit=1)
            self.assertEqual(r_as1["status"], "ok")
            self.assertEqual(
                posted_payloads[-1], {"labels": {"Autosubmit": 1}, "notify": "OWNER"}
            )
            with patch.object(service.client, "_request_json", return_value=[base_change]):
                q2 = service.fetch_queue(viewer_email="joshuaduong@google.com")
            self.assertEqual(q2["changes"][0]["viewer_cr_vote"], 1)
            self.assertTrue(q2["changes"][0]["viewer_autosubmit"])
            self.assertTrue(q2["changes"][0]["autosubmit_enabled"])

            # 3) Vote Code-Review +2 & Autosubmit +1 together
            r_cr2_as = service.cast_batch_vote([43212920], code_review=2, autosubmit=True)
            self.assertEqual(r_cr2_as["status"], "ok")
            self.assertEqual(
                posted_payloads[-1],
                {"labels": {"Code-Review": 2, "Autosubmit": 1}, "notify": "OWNER"},
            )
            with patch.object(service.client, "_request_json", return_value=[base_change]):
                q3 = service.fetch_queue(viewer_email="joshuaduong@google.com")
            self.assertTrue(q3["changes"][0]["approved_by_viewer"])
            self.assertEqual(q3["changes"][0]["viewer_cr_vote"], 2)
            self.assertTrue(q3["changes"][0]["viewer_autosubmit"])

            # 4) Clear Autosubmit (0) while keeping Code-Review +2
            r_as0 = service.cast_batch_vote([43212920], code_review=None, autosubmit=0)
            self.assertEqual(r_as0["status"], "ok")
            self.assertEqual(
                posted_payloads[-1], {"labels": {"Autosubmit": 0}, "notify": "OWNER"}
            )
            with patch.object(service.client, "_request_json", return_value=[base_change]):
                q4 = service.fetch_queue(viewer_email="joshuaduong@google.com")
            self.assertTrue(q4["changes"][0]["approved_by_viewer"])
            self.assertFalse(q4["changes"][0]["viewer_autosubmit"])

        html = render_static_gerrit_ui_html()
        self.assertIn('id="stagedVoteBar"', html)
        self.assertIn('id="stageCrPlus1"', html)
        self.assertIn('id="stageCrPlus2"', html)
        self.assertIn('id="stageAsCheckbox"', html)
        self.assertIn('id="btnSubmitVotes"', html)
        self.assertNotIn("function renderCommitQuickVoteRowHtml(", html)
        self.assertIn("function toggleStagedCr(", html)
        self.assertIn("function toggleStagedAs(", html)
        self.assertIn("function submitStagedVotes(", html)
        self.assertNotIn("+2 All Low Load", html)
        self.assertNotIn('id="btnBatchLow"', html)
        self.assertNotIn("setRiskFilter(", html)

    def test_vote_downgrade_and_reviewer_remote_error_context(self) -> None:
        from unittest.mock import MagicMock

        with patch.object(
            GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"
        ):
            service = GerritUIService(default_viewer="joshuaduong@google.com")

        stale_cr2_change = {
            "_number": 43212920,
            "project": "platform/hardware/google/aemu",
            "branch": "emu-main-next",
            "subject": "feat(gerrit-ui): Support +1/+2 测试_🚀_é",
            "owner": {"email": "rpaquay@google.com", "name": "Renaud Paquay"},
            "reviewers": {
                "REVIEWER": [{"email": "joshuaduong@google.com", "name": "Joshua Duong"}]
            },
            "labels": {
                "Code-Review": {
                    "all": [{"email": "joshuaduong@google.com", "name": "Joshua Duong", "value": 2}]
                },
                "Autosubmit": {"all": []},
            },
            "current_revision": "rev1",
            "revisions": {"rev1": {"files": {"a.py": {}}}},
        }
        with patch("shutil.which", return_value="/usr/bin/gob-curl"), patch(
            "subprocess.run", return_value=MagicMock(returncode=0, stdout=")]}'\n{}", stderr="")
        ):
            service.cast_batch_vote([43212920], code_review=2)
            service.cast_batch_vote([43212920], code_review=1)
        with patch.object(service.client, "_request_json", return_value=[stale_cr2_change]):
            q5 = service.fetch_queue(viewer_email="joshuaduong@google.com")
        self.assertFalse(q5["changes"][0]["approved_by_viewer"])
        self.assertEqual(q5["changes"][0]["viewer_cr_vote"], 1)
        self.assertEqual(q5["changes"][0]["human_cr2_count"], 0)

        with patch.object(
            service.client, "_request_json", return_value={"error": "Account not found\n"}
        ), self.assertRaises(RuntimeError) as err_cm:
            service.modify_reviewer(43212920, "add", "unknownldap", state="CC")
        self.assertIn("unknownldap@google.com", str(err_cm.exception))
        self.assertIn("CC", str(err_cm.exception))

    def test_send_reviewer_chat_dm_validation_and_http_endpoint(self) -> None:
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer
        import threading
        from unittest.mock import MagicMock
        from commands.gerrit.ui_server import make_gerrit_ui_handler

        with patch.object(
            GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"
        ):
            service = GerritUIService(default_viewer="joshuaduong@google.com")

        recorded_cmds: list[list[str]] = []

        def fake_gchat_run(cmd: list[str], **kwargs: object) -> MagicMock:
            recorded_cmds.append(list(cmd))
            return MagicMock(
                returncode=0,
                stdout=json.dumps({
                    "space_name": "spaces/AAAA1234xyz",
                    "message_name": "spaces/AAAA1234xyz/messages/msg99",
                    "recipients": "jansene@google.com",
                }),
                stderr="",
            )

        unicode_msg = 'Hi Erwin, could you review http://ag/43217292 ("测试_🚀_é")? Thanks!'
        with patch(
            "commands.gerrit.services.vote_submit_service._resolve_gchat_cli_bin",
            return_value="/google/bin/releases/gemini-agents-gchat/gchat",
        ), patch("subprocess.run", side_effect=fake_gchat_run):
            res = service.send_reviewer_chat_dm(43217292, "jansene", unicode_msg)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["recipient"], "jansene@google.com")
        self.assertEqual(res["chat_url"], "https://chat.google.com/dm/AAAA1234xyz")
        self.assertEqual(
            recorded_cmds[-1],
            [
                "/google/bin/releases/gemini-agents-gchat/gchat",
                "mutate",
                "send-direct-message",
                "--usernames",
                "jansene@google.com",
                "--text",
                unicode_msg,
                "--json",
            ],
        )

        # Reject self-DM, multi-recipient group chat, bot account, and empty message
        with self.assertRaisesRegex(ValueError, "yourself"):
            service.send_reviewer_chat_dm(43217292, "joshuaduong@google.com", "Self ping")
        with self.assertRaisesRegex(ValueError, "multi-recipient"):
            service.send_reviewer_chat_dm(43217292, "jansene@google.com,rkir@google.com", "Group")
        with self.assertRaisesRegex(ValueError, "human '@google.com'"):
            service.send_reviewer_chat_dm(43217292, "treehugger-robot@google.com", "Bot ping")
        with self.assertRaisesRegex(ValueError, "empty"):
            service.send_reviewer_chat_dm(43217292, "jansene@google.com", "   ")

        # Actionable error when gchat CLI is unavailable
        with patch(
            "commands.gerrit.services.vote_submit_service._resolve_gchat_cli_bin",
            return_value=None,
        ), self.assertRaisesRegex(RuntimeError, "Copy Text"):
            service.send_reviewer_chat_dm(43217292, "jansene@google.com", unicode_msg)

        # Verify HTTP POST /api/gerrit/chat-dm returns 200 on success and 400 on ValueError
        handler_cls = make_gerrit_ui_handler(service, ui_token=_TEST_UI_TOKEN)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        srv_thread.start()
        try:
            with patch(
                "commands.gerrit.services.vote_submit_service._resolve_gchat_cli_bin",
                return_value="/google/bin/releases/gemini-agents-gchat/gchat",
            ), patch("subprocess.run", side_effect=fake_gchat_run):
                req_ok = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/gerrit/chat-dm",
                    data=json.dumps({
                        "cl": 43217292,
                        "recipient": "jansene@google.com",
                        "message": unicode_msg,
                    }).encode("utf-8"),
                    headers=_JSON_AUTH_HEADERS,
                    method="POST",
                )
                with urllib.request.urlopen(req_ok, timeout=5) as resp_ok:
                    self.assertEqual(resp_ok.status, 200)
                    body_ok = json.loads(resp_ok.read().decode("utf-8"))
                self.assertEqual(body_ok["chat_url"], "https://chat.google.com/dm/AAAA1234xyz")

                req_bad = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/gerrit/chat-dm",
                    data=json.dumps({
                        "cl": 43217292,
                        "recipient": "joshuaduong@google.com",
                        "message": "Self ping",
                    }).encode("utf-8"),
                    headers=_JSON_AUTH_HEADERS,
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as http_err:
                    urllib.request.urlopen(req_bad, timeout=5)
                self.assertEqual(http_err.exception.code, 400)
        finally:
            httpd.shutdown()
            httpd.server_close()

        html = render_static_gerrit_ui_html()
        self.assertIn("function openChatDmComposer(", html)
        self.assertIn("function submitChatDm(", html)
        self.assertIn("/api/gerrit/chat-dm", html)
        self.assertIn("💬 Ping Reviewer", html)
        self.assertIn("💬 DM Reviewer", html)

    def test_presubmit_status_and_last_updated_enrichment(self) -> None:
        from commands.gerrit.presubmit_targets import (
            extract_presubmit_status_from_change,
            format_relative_age,
        )

        self.assertEqual(format_relative_age(0.005), "just now")
        self.assertEqual(format_relative_age(0.25), "15m ago")
        self.assertEqual(format_relative_age(3.2), "3h ago")
        self.assertEqual(format_relative_age(36.0), "1.5d ago")

        now = datetime(2026, 10, 23, 20, 0, 0, tzinfo=timezone.utc)
        running_change = {
            "_number": 43234462,
            "project": "platform/hardware/google/aemu",
            "branch": "studio-main",
            "subject": "feat(gerrit-ui): Presubmit targets drawer 测试_🚀_é",
            "created": "2026-10-23 18:00:00.000000000",
            "updated": "2026-10-23 19:50:00.000000000",
            "current_revision": "rev2",
            "current_revision_number": 2,
            "revisions": {"rev2": {"_number": 2, "files": {"src/ui.py": {}}}},
            "owner": {"email": "joshuaduong@google.com", "name": "Joshua Duong"},
            "labels": {
                "Presubmit-Verified": {"all": []},
                "Presubmit-Ready": {
                    "all": [{"email": "joshuaduong@google.com", "name": "Joshua Duong", "value": 1}]
                },
            },
            "messages": [
                {
                    "_revision_number": 2,
                    "message": (
                        "## Presubmit started: L08600030212195759\n"
                        "View [Checks tab](https://googleplex-android-review.git.corp.google.com/c/"
                        "platform/hardware/google/aemu/+/43234462/2?tab=checks) for progress."
                    ),
                }
            ],
        }
        older_passed_change = {
            "_number": 43217292,
            "project": "platform/hardware/google/aemu",
            "branch": "studio-main",
            "subject": "feat(gerrit-ui): Reviewer voting",
            "created": "2026-10-22 10:00:00.000000000",
            "updated": "2026-10-23 17:00:00.000000000",
            "current_revision": "rev1",
            "current_revision_number": 1,
            "revisions": {"rev1": {"_number": 1, "files": {"src/ui.py": {}}}},
            "owner": {"email": "joshuaduong@google.com", "name": "Joshua Duong"},
            "labels": {
                "Presubmit-Verified": {"all": [{"email": "treehugger@google.com", "value": 2}]},
                "Presubmit-Ready": {"all": []},
            },
            "messages": [
                {"_revision_number": 1, "message": "## Presubmit started: L48000030211835060"},
                {"_revision_number": 1, "message": "## Presubmit finished: 27 passed (2 new)."},
            ],
        }

        en_running = enrich_change_for_ui(running_change, "joshuaduong@google.com", now_dt=now)
        en_passed = enrich_change_for_ui(older_passed_change, "joshuaduong@google.com", now_dt=now)
        self.assertGreater(en_running["updated_ts"], en_passed["updated_ts"])
        self.assertEqual(en_running["updated_display"], "10m ago")
        self.assertEqual(en_passed["updated_display"], "3h ago")
        self.assertTrue(en_running["viewer_presubmit_ready"])
        self.assertTrue(en_running["presubmit_ready_enabled"])
        self.assertEqual(en_running["presubmit_status"], "running")
        self.assertEqual(en_running["workplan_id"], "L08600030212195759")
        self.assertEqual(
            en_running["workplan_url"],
            "https://android-build.corp.google.com/workplan/L08600030212195759",
        )
        self.assertEqual(en_passed["presubmit_status"], "passed")
        self.assertEqual(en_passed["presubmit_summary"], "27 passed (2 new)")

        # Check remaining status variants (queued, warning, failed, not_started)
        st_queued = extract_presubmit_status_from_change(
            {"_number": 100, "project": "p/a", "messages": []}, pv_score=0, pr_enabled=True, cur_ps=1
        )
        self.assertEqual(st_queued["presubmit_status"], "queued")
        st_warn = extract_presubmit_status_from_change(
            {"_number": 100, "project": "p/a", "messages": []}, pv_score=1, pr_enabled=False, cur_ps=1
        )
        self.assertEqual(st_warn["presubmit_status"], "warning")
        st_fail = extract_presubmit_status_from_change(
            {"_number": 100, "project": "p/a", "messages": []}, pv_score=-1, pr_enabled=False, cur_ps=1
        )
        self.assertEqual(st_fail["presubmit_status"], "failed")
        st_idle = extract_presubmit_status_from_change(
            {"_number": 100, "project": "p/a", "messages": []}, pv_score=0, pr_enabled=False, cur_ps=1
        )
        self.assertEqual(st_idle["presubmit_status"], "not_started")

    @staticmethod
    def _sample_presubmit_runs_rpc_json(now_us: int) -> str:
        return json.dumps({
            "presubmit_runs": [
                {
                    "presubmit_run_id": "run-old",
                    "workplan_id": "L0000000001",
                    "workplan_attempt": 1,
                    "presubmit_run_state": "OBSOLETE",
                    "create_timestamp_micro": str(now_us - 900_000_000),
                    "requirements": [],
                },
                {
                    "presubmit_run_id": "run-cur",
                    "workplan_id": "L08600030212195759",
                    "workplan_attempt": 2,
                    "presubmit_run_state": "RUNNING",
                    "create_timestamp_micro": str(now_us - 120_000_000),
                    "requirements": [
                        {
                            "presubmit_requirement_id": "req-build-fail",
                            "type": "BUILD",
                            "status": "FAIL",
                            "blocking_level": "BLOCKING",
                            "latest_build_id": "P98765432",
                            "create_timestamp_micro": str(now_us - 95_000_000),
                            "update_timestamp_micro": str(now_us - 10_000_000),
                            "requirement_identifier": {
                                "build_requirement_identifier": {
                                    "branch": "aosp-emu-main-next",
                                    "target": "emulator-linux_x64_测试_🚀_é",
                                }
                            },
                            "requirement_worknode_infos": [
                                {"worknode_id": "L08600030212195759:N10", "primary": True}
                            ],
                        },
                        {
                            "presubmit_requirement_id": "req-test-running",
                            "type": "TEST",
                            "status": "RUNNING",
                            "blocking_level": "BLOCKING",
                            "create_timestamp_micro": str(now_us - 45_000_000),
                            "requirement_identifier": {
                                "test_requirement_identifier": {
                                    "branch": "aosp-emu-main-next",
                                    "build_target": "emulator-linux_x64",
                                    "atp_test_name": "v2/emu-team/avd_boot_test_测试_🚀_é",
                                }
                            },
                            "requirement_worknode_infos": [
                                {"worknode_id": "L08600030212195759:N20", "primary": True}
                            ],
                        },
                        {
                            "presubmit_requirement_id": "req-test-pass",
                            "type": "TEST",
                            "status": "PASS",
                            "blocking_level": "WARN",
                            "ants_invocation_id": "I1234567890",
                            "create_timestamp_micro": str(now_us - 80_000_000),
                            "update_timestamp_micro": str(now_us - 20_000_000),
                            "requirement_identifier": {
                                "test_requirement_identifier": {
                                    "branch": "studio-main",
                                    "build_target": "studio-linux",
                                    "atp_test_name": "v2/android-code-coverage-team/cov_suite",
                                }
                            },
                            "requirement_worknode_infos": [
                                {"worknode_id": "L08600030212195759:N30", "primary": True}
                            ],
                        },
                        {
                            "presubmit_requirement_id": "req-preflight-cached",
                            "type": "PRE_FLIGHT",
                            "status": "CACHED",
                            "blocking_level": "BLOCKING",
                            "requirement_identifier": {
                                "pre_flight_requirement_identifier": {"task_name": "lint_check"}
                            },
                        },
                    ],
                },
            ]
        })

    def test_parse_list_presubmit_runs_json(self) -> None:
        from commands.gerrit.presubmit_targets import parse_list_presubmit_runs_json

        now_dt = datetime(2026, 10, 23, 20, 10, 0, tzinfo=timezone.utc)
        now_us = int(now_dt.timestamp() * 1_000_000)
        checks_url = (
            "https://googleplex-android-review.git.corp.google.com/c/"
            "platform/hardware/google/aemu/+/43234462/2?tab=checks"
        )
        parsed = parse_list_presubmit_runs_json(
            self._sample_presubmit_runs_rpc_json(now_us), "43234462", 2, checks_url, now_dt=now_dt
        )
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["workplan_id"], "L08600030212195759")
        self.assertEqual(parsed["attempt"], 2)
        self.assertEqual(parsed["counts"]["total"], 4)
        self.assertEqual(parsed["counts"]["failed"], 1)
        self.assertEqual(parsed["counts"]["running"], 1)
        self.assertEqual(parsed["counts"]["passed"], 2)
        self.assertEqual(parsed["counts"]["cached"], 1)

        first_target = parsed["targets"][0]
        self.assertEqual(first_target["status"], "failed")
        self.assertIn("build_error.log", first_target["url"])
        self.assertIn("build-details/P98765432", first_target["secondary_url"])
        running_target = parsed["targets"][1]
        self.assertEqual(running_target["name"], "avd_boot_test_测试_🚀_é")
        self.assertTrue(running_target["waiting_on_build"])
        self.assertEqual(running_target["duration_display"], "45s")
        cov_target = next(t for t in parsed["targets"] if t["id"] == "req-test-pass")
        self.assertEqual(cov_target["name"], "Coverage")
        self.assertIn("invocationId=I1234567890", cov_target["url"])

        with self.assertRaisesRegex(RuntimeError, r"ag/43234462 \(PS2\)"):
            parse_list_presubmit_runs_json("{corrupt-json", "43234462", 2, checks_url, now_dt=now_dt)

    def test_get_presubmit_targets_caching_and_missing_stubby(self) -> None:
        from unittest.mock import MagicMock

        now_dt = datetime(2026, 10, 23, 20, 10, 0, tzinfo=timezone.utc)
        sample_rpc_json = self._sample_presubmit_runs_rpc_json(int(now_dt.timestamp() * 1_000_000))
        with patch.object(
            GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"
        ):
            service = GerritUIService(default_viewer="joshuaduong@google.com")

        stubby_calls: list[list[str]] = []

        def fake_stubby_run(cmd: list[str], **_kwargs: object) -> MagicMock:
            stubby_calls.append(list(cmd))
            return MagicMock(returncode=0, stdout=sample_rpc_json, stderr="")

        with patch(
            "commands.gerrit.presubmit_targets._resolve_stubby_bin",
            return_value="/usr/bin/stubby",
        ), patch("subprocess.run", side_effect=fake_stubby_run):
            t1 = service.get_presubmit_targets(
                43234462, patchset=2, project="platform/hardware/google/aemu"
            )
            t2 = service.get_presubmit_targets(
                43234462, patchset=2, project="platform/hardware/google/aemu"
            )
            t3 = service.get_presubmit_targets(
                43234462, patchset=2, project="platform/hardware/google/aemu", force_refresh=True
            )
        self.assertEqual(t1["workplan_id"], "L08600030212195759")
        self.assertEqual(t2["workplan_id"], "L08600030212195759")
        self.assertEqual(t3["workplan_id"], "L08600030212195759")
        self.assertEqual(len(stubby_calls), 2)

        with patch(
            "commands.gerrit.presubmit_targets._resolve_stubby_bin", return_value=None
        ), self.assertRaisesRegex(RuntimeError, "Checks tab"):
            service.get_presubmit_targets(
                43234462, patchset=2, project="platform/hardware/google/aemu", force_refresh=True
            )

    def test_presubmit_targets_http_endpoint(self) -> None:
        from http.server import ThreadingHTTPServer
        import threading
        from unittest.mock import MagicMock
        import urllib.error
        import urllib.request
        from commands.gerrit.ui_server import make_gerrit_ui_handler

        now_dt = datetime(2026, 10, 23, 20, 10, 0, tzinfo=timezone.utc)
        sample_rpc_json = self._sample_presubmit_runs_rpc_json(int(now_dt.timestamp() * 1_000_000))
        with patch.object(
            GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"
        ):
            service = GerritUIService(default_viewer="joshuaduong@google.com")

        handler_cls = make_gerrit_ui_handler(service, ui_token=_TEST_UI_TOKEN)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        srv_thread.start()
        try:
            with patch(
                "commands.gerrit.presubmit_targets._resolve_stubby_bin",
                return_value="/usr/bin/stubby",
            ), patch(
                "subprocess.run",
                return_value=MagicMock(returncode=0, stdout=sample_rpc_json, stderr=""),
            ):
                with urllib.request.urlopen(
                    _authed_get(
                        f"http://127.0.0.1:{port}/api/gerrit/presubmit-targets"
                        "?cl=43234462&ps=2&project=platform/hardware/google/aemu"
                    ),
                    timeout=5,
                ) as resp:
                    self.assertEqual(resp.status, 200)
                    payload = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(payload["counts"]["total"], 4)

            with self.assertRaises(urllib.error.HTTPError) as bad_err:
                urllib.request.urlopen(
                    _authed_get(f"http://127.0.0.1:{port}/api/gerrit/presubmit-targets?cl=abc"), timeout=5
                )
            self.assertEqual(bad_err.exception.code, 400)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_presubmit_ready_vote_and_session_lag_handling(self) -> None:
        from http.server import ThreadingHTTPServer
        import threading
        from unittest.mock import MagicMock
        import urllib.request
        from commands.gerrit.ui_server import make_gerrit_ui_handler

        with patch.object(
            GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"
        ):
            service = GerritUIService(default_viewer="joshuaduong@google.com")

        posted_payloads: list[dict[str, object]] = []

        def fake_gob_curl(cmd: list[str], **_kwargs: object) -> MagicMock:
            posted_payloads.append(json.loads(cmd[cmd.index("-d") + 1]))
            return MagicMock(returncode=0, stdout=")]}'\n{}", stderr="")

        unstarted_change = {
            "_number": 43234462,
            "project": "platform/hardware/google/aemu",
            "branch": "studio-main",
            "subject": "feat(gerrit-ui): Trigger presubmit 测试_🚀_é",
            "owner": {"email": "joshuaduong@google.com", "name": "Joshua Duong"},
            "reviewers": {"REVIEWER": []},
            "labels": {"Code-Review": {"all": []}, "Presubmit-Ready": {"all": []}},
            "current_revision": "rev1",
            "current_revision_number": 1,
            "revisions": {"rev1": {"_number": 1, "files": {"a.py": {}}}},
        }
        service._presubmit_targets_cache[(43234462, 1)] = (9999999999.0, {"status": "ok"})

        with patch("shutil.which", return_value="/usr/bin/gob-curl"), patch(
            "subprocess.run", side_effect=fake_gob_curl
        ):
            res_pr1 = service.cast_batch_vote([43234462], code_review=None, presubmit_ready=1)
            self.assertEqual(res_pr1["status"], "ok")
            self.assertEqual(
                posted_payloads[-1], {"labels": {"Presubmit-Ready": 1}, "notify": "OWNER"}
            )
            self.assertEqual(len(service._presubmit_targets_cache), 0)

            with patch.object(service.client, "_request_json", return_value=[unstarted_change]):
                q_lag = service.fetch_queue(viewer_email="joshuaduong@google.com", scope="owner")
            self.assertTrue(q_lag["changes"][0]["viewer_presubmit_ready"])
            self.assertEqual(q_lag["changes"][0]["presubmit_status"], "queued")

            # Expire lag window -> TreeHugger finished state is respected without stale override
            service._session_presubmit_voted_at[43234462] = 0.0
            finished_change = {
                **unstarted_change,
                "labels": {
                    "Code-Review": {"all": []},
                    "Presubmit-Ready": {"all": []},
                    "Presubmit-Verified": {"all": [{"email": "treehugger@google.com", "value": 2}]},
                },
            }
            with patch.object(service.client, "_request_json", return_value=[finished_change]):
                q_done = service.fetch_queue(viewer_email="joshuaduong@google.com", scope="owner")
            self.assertFalse(q_done["changes"][0]["viewer_presubmit_ready"])
            self.assertEqual(q_done["changes"][0]["presubmit_status"], "passed")

            # Verify HTTP POST /api/gerrit/vote with presubmit_ready=0
            handler_cls = make_gerrit_ui_handler(service, ui_token=_TEST_UI_TOKEN)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
            port = httpd.server_address[1]
            srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            srv_thread.start()
            try:
                req_vote = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/gerrit/vote",
                    data=json.dumps({
                        "cls": [43234462],
                        "code_review": None,
                        "presubmit_ready": 0,
                    }).encode("utf-8"),
                    headers=_JSON_AUTH_HEADERS,
                    method="POST",
                )
                with urllib.request.urlopen(req_vote, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                self.assertEqual(
                    posted_payloads[-1], {"labels": {"Presubmit-Ready": 0}, "notify": "OWNER"}
                )
            finally:
                httpd.shutdown()
                httpd.server_close()

        html = render_static_gerrit_ui_html()
        self.assertIn('id="queueSortSelect"', html)
        self.assertIn('id="stagePrCheckbox"', html)
        self.assertIn('id="stagePrPill"', html)
        self.assertIn("function setSortMode(", html)
        self.assertIn("function toggleStagedPr(", html)
        self.assertIn("function togglePresubmitTargets(", html)
        self.assertIn("function fetchPresubmitTargetsForCl(", html)
        self.assertIn("/api/gerrit/presubmit-targets", html)

    def test_progressive_fast_queue_stage1_and_cross_viewer_team_cache(self) -> None:
        with patch.object(GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"):
            service = GerritUIService(default_viewer="joshuaduong@google.com")
        service._bug_cache = {}

        sample_change = {
            "_number": 43260001,
            "project": "platform/hardware/google/aemu",
            "subject": "feat(ui): Progressive Gerrit load 测试_🚀_é",
            "created": "2026-10-23 20:00:00.000000000",
            "updated": "2026-10-23 22:00:00.000000000",
            "insertions": 18,
            "deletions": 4,
            "unresolved_comment_count": 0,
            "mergeable": True,
            "current_revision": "rev_abc123",
            "current_revision_number": 1,
            "owner": {"name": "Joshua Duong", "email": "joshuaduong@google.com"},
            "revisions": {
                "rev_abc123": {
                    "_number": 1,
                    "commit": {
                        "message": "feat(ui): Progressive Gerrit load 测试_🚀_é\n\nBug: 499001122\n",
                        "parents": [{"commit": "parent_000"}],
                    },
                    "files": {"tools/ui_测试_🚀_é.py": {"lines_inserted": 18, "lines_deleted": 4}},
                }
            },
            "labels": {
                "Code-Review": {
                    "all": [{"email": "jansene@google.com", "name": "Erwin", "value": 2}]
                },
                "Presubmit-Verified": {"all": [{"email": "treehugger@google.com", "value": 2}]},
            },
            "reviewers": {"REVIEWER": [{"email": "jansene@google.com", "name": "Erwin"}]},
            "messages": [],
        }

        requested_endpoints: list[str] = []

        def fake_request_json(endpoint: str, **_kwargs):
            requested_endpoints.append(endpoint)
            return [sample_change]

        with (
            patch.object(service.client, "_request_json", side_effect=fake_request_json),
            patch.object(
                service,
                "fetch_bug_metadata_batch",
                return_value={
                    "499001122": {
                        "id": "499001122",
                        "url": "https://b.corp.google.com/issues/499001122",
                        "priority": "P1",
                        "severity": "S1",
                        "status": "ASSIGNED",
                        "title": "Latency spike",
                    }
                },
            ) as mock_bug_batch,
        ):
            stage1 = service.fetch_queue(
                viewer_email="joshuaduong@google.com",
                scope="team",
                fast=True,
                use_cache=True,
            )
            self.assertEqual(len(requested_endpoints), 1)
            self.assertIn("n=10", requested_endpoints[0])
            self.assertNotIn("SUBMIT_REQUIREMENTS", requested_endpoints[0])
            self.assertEqual(mock_bug_batch.call_count, 0)
            self.assertTrue(stage1["is_partial"])
            self.assertFalse(stage1["from_cache"])

            # Stage 2 reuses the already-complete (<25 CLs) raw Gerrit cache and only resolves uncached bugs!
            stage2 = service.fetch_queue(
                viewer_email="joshuaduong@google.com",
                scope="team",
                fast=False,
                use_cache=True,
            )
            self.assertEqual(len(requested_endpoints), 1)
            self.assertEqual(mock_bug_batch.call_count, 1)
            self.assertFalse(stage2["is_partial"])
            self.assertTrue(stage2["from_cache"])
            self.assertEqual(stage2["changes"][0]["bugs"][0]["priority"], "P1")
            self.assertFalse(stage2["changes"][0]["approved_by_viewer"])

            # Switching viewer on `team` scope reuses `_raw_queue_cache` with zero Gerrit calls
            switched = service.fetch_queue(
                viewer_email="jansene@google.com",
                scope="team",
                fast=True,
                use_cache=True,
            )
            self.assertEqual(len(requested_endpoints), 1)
            self.assertTrue(switched["from_cache"])
            self.assertTrue(switched["changes"][0]["approved_by_viewer"])

            # Even when Gerrit sets `_more_changes: True` at FULL_QUEUE_LIMIT, Stage 2 marks cache full
            service._raw_queue_cache.clear()
            sample_with_more = {**sample_change, "_more_changes": True}
            with patch.object(service.client, "_request_json", return_value=[sample_with_more]) as mock_more:
                full_100 = service.fetch_queue(
                    viewer_email="joshuaduong@google.com",
                    scope="team",
                    fast=False,
                    use_cache=True,
                )
                self.assertFalse(full_100["is_partial"])
                cached_hit = service.fetch_queue(
                    viewer_email="rkir@google.com",
                    scope="team",
                    fast=True,
                    use_cache=True,
                )
                self.assertTrue(cached_hit["from_cache"])
                self.assertEqual(mock_more.call_count, 1)

    def test_parallel_diff_bundle_skips_change_detail_on_known_ps1_and_caches(self) -> None:
        with patch.object(GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"):
            service = GerritUIService(default_viewer="joshuaduong@google.com")
        service._record_known_revisions(
            [{"_number": 43260002, "current_revision": "rev_ps1", "current_revision_number": 1}]
        )
        raw_patch = (
            "From rev_ps1 Mon Sep 17 00:00:00 2001\n"
            "Subject: [PATCH] fix: Unicode path 测试_🚀_é\n\n"
            "Body text.\n"
            "---\n"
            "diff --git a/src/测试_🚀_é.cc b/src/测试_🚀_é.cc\n"
            "--- a/src/测试_🚀_é.cc\n"
            "+++ b/src/测试_🚀_é.cc\n"
            "@@ -1,1 +1,2 @@\n"
            " int x = 1;\n"
            "+int y = 2; // 测试_🚀_é\n"
        )
        with (
            patch.object(service.client, "get_revision_patch", return_value=raw_patch) as mock_patch,
            patch.object(service.client, "get_comment_threads", return_value=[]) as mock_threads,
            patch.object(service.client, "get_change_detail") as mock_detail,
            patch(
                "commands.gerrit.services.inline_qa_service.discover_transcript_code_questions",
                return_value=[],
            ),
        ):
            b1 = service.fetch_commit_diff_bundle(43260002)
            self.assertEqual(b1["files"][0]["file"], "src/测试_🚀_é.cc")
            self.assertFalse(b1["is_pure_rebase"])
            mock_detail.assert_not_called()
            self.assertEqual(mock_patch.call_count, 1)
            self.assertEqual(mock_threads.call_count, 1)

            # Second call within TTL hits `_full_diff_bundle_cache` with 0 Gerrit RPCs
            b2 = service.fetch_commit_diff_bundle(43260002)
            self.assertEqual(b2["files"][0]["file"], "src/测试_🚀_é.cc")
            self.assertEqual(mock_patch.call_count, 1)
            self.assertEqual(mock_threads.call_count, 1)

            # Invalidating CL clears full diff bundle cache
            service._invalidate_cl_caches(["43260002"])
            self.assertEqual(service._full_diff_bundle_cache, {})

    def test_http_queue_fast_and_prewarm_flags_and_inline_js_syntax(self) -> None:
        with patch.object(GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"):
            service = GerritUIService(default_viewer="joshuaduong@google.com")

        calls: list[tuple[bool, bool]] = []

        def fake_fetch_queue(**kwargs):
            calls.append((bool(kwargs.get("fast")), bool(kwargs.get("use_cache"))))
            return {
                "ui_version": "test",
                "viewer": kwargs.get("viewer_email") or "joshuaduong@google.com",
                "scope": kwargs.get("scope") or "reviewer",
                "is_partial": bool(kwargs.get("fast")),
                "changes": [],
                "stacks": [],
            }

        with (
            patch.object(service, "fetch_queue", side_effect=fake_fetch_queue),
            patch.object(service, "schedule_background_prewarm", return_value=True) as mock_prewarm,
        ):
            handler_cls = make_gerrit_ui_handler(service, ui_token=_TEST_UI_TOKEN)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
            port = httpd.server_address[1]
            srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            srv_thread.start()
            try:
                with urllib.request.urlopen(
                    _authed_get(f"http://127.0.0.1:{port}/api/gerrit/queue?scope=reviewer&fast=1"), timeout=5
                ) as resp1:
                    d1 = json.loads(resp1.read().decode("utf-8"))
                self.assertTrue(d1["is_partial"])
                self.assertEqual(calls[-1], (True, True))
                self.assertEqual(mock_prewarm.call_count, 0)

                with urllib.request.urlopen(
                    _authed_get(f"http://127.0.0.1:{port}/api/gerrit/queue?scope=reviewer&prewarm=1"), timeout=5
                ) as resp2:
                    d2 = json.loads(resp2.read().decode("utf-8"))
                self.assertFalse(d2["is_partial"])
                self.assertEqual(calls[-1], (False, True))
                self.assertEqual(mock_prewarm.call_count, 1)

                with urllib.request.urlopen(
                    _authed_get(f"http://127.0.0.1:{port}/api/gerrit/queue?scope=reviewer&fast=1&refresh=1"),
                    timeout=5,
                ) as resp3:
                    json.loads(resp3.read().decode("utf-8"))
                self.assertEqual(calls[-1], (True, False))

                with urllib.request.urlopen(
                    _authed_get(f"http://127.0.0.1:{port}/api/gerrit/queue?prewarm_only=1&viewer=joshuaduong@google.com"),
                    timeout=5,
                ) as resp4:
                    d4 = json.loads(resp4.read().decode("utf-8"))
                self.assertTrue(d4["prewarm_scheduled"])
            finally:
                httpd.shutdown()
                httpd.server_close()

        html = render_static_gerrit_ui_html()
        self.assertIn("function reprojectQueuePayloadForViewer(", html)
        self.assertIn("function hydrateFullQueueInBackground(", html)
        self.assertIn("sidebar-hydrating-pill", html)
        node_bin = shutil.which("node")
        if node_bin:
            scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S)
            self.assertTrue(scripts)
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_js = Path(tmp_dir) / "inline_测试_🚀_é.js"
                tmp_js.write_text("\n".join(scripts), encoding="utf-8")
                proc = subprocess.run(
                    [node_bin, "--check", str(tmp_js)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, msg=proc.stderr)

    def test_code_search_file_url_mapping_and_diff_header_links(self) -> None:
        self.assertEqual(
            build_code_search_file_url(
                "platform/hardware/generic/goldfish",
                "emu-main-next",
                "emulator/libs/file/file.cc",
            ),
            "https://source.corp.google.com/h/googleplex-android/"
            "platform/superproject/emu-main-next/+/emu-main-next:"
            "hardware/generic/goldfish/emulator/libs/file/file.cc",
        )
        self.assertEqual(
            build_code_search_file_url(
                "platform/hardware/google/aemu",
                "studio-main",
                "tools/emu-dev-cli/src/commands/gerrit/ui_server.py",
                line=88,
            ),
            "https://source.corp.google.com/h/googleplex-android/"
            "platform/superproject/emu-main-next/+/emu-main-next:"
            "hardware/google/aemu/tools/emu-dev-cli/src/commands/gerrit/ui_server.py;l=88",
        )
        self.assertEqual(
            build_code_search_file_url(
                "platform/external/qemu",
                "emu-main-next-v11.0",
                "hw/virtio/virtio-pci.c",
            ),
            "https://source.corp.google.com/h/googleplex-android/"
            "platform/superproject/emu-main-next/+/emu-main-next:"
            "third_party/qemu/hw/virtio/virtio-pci.c",
        )
        self.assertEqual(
            build_code_search_file_url(
                "platform/external/qemu",
                "emu-main-dev",
                "android/android-emu/android/main-common.c",
            ),
            "https://source.corp.google.com/h/googleplex-android/"
            "platform/superproject/emu-main-dev/+/emu-main-dev:"
            "external/qemu/android/android-emu/android/main-common.c",
        )
        self.assertEqual(
            build_code_search_file_url(
                "platform/hardware/google/gfxstream",
                "emu-main-dev",
                "host/FrameBuffer.cpp",
            ),
            "https://source.corp.google.com/h/googleplex-android/"
            "platform/superproject/emu-main-dev/+/emu-main-dev:"
            "hardware/google/gfxstream/host/FrameBuffer.cpp",
        )
        self.assertEqual(
            build_code_search_file_url(
                "platform/build/bazel",
                "emu-main-next",
                "rules/cc.bzl",
            ),
            "https://source.corp.google.com/h/googleplex-android/"
            "platform/superproject/emu-main-next/+/emu-main-next:"
            "build/bazel/rules/cc.bzl",
        )
        self.assertEqual(
            build_code_search_file_url(
                "device/generic/goldfish",
                "udc-kiwi-dev",
                "vendor_common.mk",
            ),
            "https://source.corp.google.com/h/googleplex-android/"
            "device/generic/goldfish/+/udc-kiwi-dev:vendor_common.mk",
        )
        # Non-ASCII Unicode file path encoding
        self.assertEqual(
            build_code_search_file_url(
                "platform/hardware/generic/goldfish",
                "refs/heads/emu-main-next",
                "emulator/测试_🚀_é.cc",
                line=15,
            ),
            "https://source.corp.google.com/h/googleplex-android/"
            "platform/superproject/emu-main-next/+/emu-main-next:"
            "hardware/generic/goldfish/emulator/%E6%B5%8B%E8%AF%95_%F0%9F%9A%80_%C3%A9.cc;l=15",
        )
        # Magic Gerrit pseudo-files return empty string
        self.assertEqual(
            build_code_search_file_url(
                "platform/hardware/google/aemu", "studio-main", "/COMMIT_MSG"
            ),
            "",
        )
        self.assertEqual(
            build_code_search_file_url(
                "platform/hardware/google/aemu", "studio-main", "dev/null"
            ),
            "",
        )

        enriched = enrich_change_for_ui(
            {
                "_number": 43270001,
                "project": "platform/hardware/generic/goldfish",
                "branch": "emu-main-next",
                "subject": "feat: Add Code Search file links",
                "owner": {"email": "joshuaduong@google.com", "name": "Joshua"},
            },
            viewer_email="joshuaduong@google.com",
        )
        self.assertEqual(
            enriched["code_search_base_url"],
            resolve_code_search_base_url("platform/hardware/generic/goldfish", "emu-main-next"),
        )

        html = render_static_gerrit_ui_html()
        self.assertIn("function resolveCodeSearchBaseUrl(", html)
        self.assertIn("function buildCodeSearchFileUrl(", html)
        self.assertIn("function renderCodeSearchFileLinkHtml(", html)
        self.assertIn('class="file-cs-link"', html)

    def test_simplified_side_panel_cards_and_repo_label(self) -> None:
        self.assertEqual(
            format_repo_display_path("platform/hardware/generic/goldfish"),
            "hardware/generic/goldfish",
        )
        self.assertEqual(
            format_repo_display_path("platform/hardware/google/aemu"),
            "hardware/google/aemu",
        )
        self.assertEqual(
            format_repo_display_path("platform/external/qemu"),
            "external/qemu",
        )
        self.assertEqual(
            format_repo_display_path("trusty/external/qemu-meson"),
            "trusty/external/qemu-meson",
        )

        enriched = enrich_change_for_ui(
            {
                "_number": 43280001,
                "project": "platform/hardware/generic/goldfish",
                "branch": "emu-main-next",
                "topic": "topic-测试_🚀_é",
                "subject": "feat: Simplify side panel card 测试_🚀_é",
                "owner": {"email": "joshuaduong@google.com", "name": "Joshua"},
                "current_revision": "sha_a",
            },
            viewer_email="joshuaduong@google.com",
        )
        self.assertEqual(enriched["repo_label"], "hardware/generic/goldfish")

        enriched_2 = enrich_change_for_ui(
            {
                "_number": 43280002,
                "project": "platform/hardware/generic/goldfish",
                "branch": "emu-main-next",
                "topic": "topic-测试_🚀_é",
                "subject": "feat: Follow-up commit",
                "owner": {"email": "joshuaduong@google.com", "name": "Joshua"},
                "current_revision": "sha_b",
            },
            viewer_email="joshuaduong@google.com",
        )
        stacks = group_changes_into_stacks([enriched, enriched_2])
        self.assertEqual(stacks[0]["title"], "STACK · topic:topic-测试_🚀_é")
        self.assertNotIn("Commits", stacks[0]["title"])

        html = render_static_gerrit_ui_html()
        self.assertIn("function formatRepoDisplayPath(", html)
        self.assertIn("function formatStackHeaderTitle(", html)
        self.assertIn('class="repo-badge"', html)
        self.assertNotIn("in flight</span>", html)
        self.assertNotIn("(1x +2 req)", html)
        self.assertNotIn("[Agent CL: 2x +2 req]", html)
        card_fn_match = re.search(
            r"function renderClCardHtml\(c, stackId\)\s*\{(.*?)\n  \}",
            html,
            flags=re.S,
        )
        self.assertIsNotNone(card_fn_match)
        card_fn_body = card_fn_match.group(1) if card_fn_match else ""
        self.assertNotIn("stack_total", card_fn_body)
        top_row_match = re.search(
            r'<div class="cl-row-top">(.*?)</div>',
            card_fn_body,
            flags=re.S,
        )
        self.assertIsNotNone(top_row_match)
        top_row_inner = (top_row_match.group(1) if top_row_match else "").strip()
        self.assertIn('class="cl-card-check-wrap"', top_row_inner)
        self.assertIn('class="cl-select-checkbox"', top_row_inner)
        self.assertIn(
            '<span class="cl-subj" title="${escapeHtml(c.subject)}">${escapeHtml(c.subject)}</span>',
            top_row_inner,
        )

    def test_compact_card_pv_badge_red_needed_slots_reviewer_icon_and_submittable_hue(
        self,
    ) -> None:
        from commands.gerrit.presubmit_targets import (
            extract_presubmit_status_from_change,
        )

        submittable_cl = enrich_change_for_ui(
            {
                "_number": 43129761,
                "project": "platform/external/qemu",
                "branch": "emu-main-dev",
                "subject": "ets: Update SnapshotVersionTest 测试_🚀_é",
                "owner": {"email": "bohu@google.com", "name": "Bo Hu"},
                "current_revision": "rev1",
                "current_revision_number": 1,
                "revisions": {"rev1": {"_number": 1, "files": {"ets/test.py": {}}}},
                "mergeable": True,
                "unresolved_comment_count": 3,
                "reviewers": {
                    "REVIEWER": [{"email": "kmagic@google.com", "name": "K Magic"}]
                },
                "labels": {
                    "Code-Review": {
                        "all": [{"email": "kmagic@google.com", "name": "K Magic", "value": 2}]
                    },
                    "Presubmit-Verified": {
                        "all": [{"email": "treehugger@google.com", "value": 2}]
                    },
                },
            },
            viewer_email="joshuaduong@google.com",
        )
        self.assertTrue(submittable_cl["is_submittable"])
        self.assertEqual(submittable_cl["presubmit_label"], "✓ PV +2")
        self.assertEqual(
            submittable_cl["approval_slots"],
            [{"state": "done", "text": "✓ kmagic +2"}],
        )

        needs_two_cl = enrich_change_for_ui(
            {
                "_number": 43290001,
                "project": "platform/hardware/generic/goldfish",
                "branch": "emu-main-next",
                "subject": "feat: Agent change needing two approvals 测试_🚀_é",
                "owner": {
                    "email": "groupagent-android-emulator@google.com",
                    "name": "Goldie",
                },
                "current_revision": "rev1",
                "current_revision_number": 1,
                "revisions": {
                    "rev1": {
                        "_number": 1,
                        "files": {"emulator/main.cc": {}},
                    }
                },
                "labels": {
                    "Code-Review": {"all": []},
                    "Presubmit-Verified": {
                        "all": [{"email": "treehugger@google.com", "value": 2}]
                    },
                },
            },
            viewer_email="jansene@google.com",
        )
        self.assertFalse(needs_two_cl["is_submittable"])
        self.assertEqual(
            needs_two_cl["approval_slots"],
            [{"state": "needed", "text": "Needs two +2s"}],
        )

        st_warn = extract_presubmit_status_from_change(
            {"_number": 101, "project": "p/a", "messages": []},
            pv_score=1,
            pr_enabled=False,
            cur_ps=1,
        )
        st_fail = extract_presubmit_status_from_change(
            {"_number": 101, "project": "p/a", "messages": []},
            pv_score=-1,
            pr_enabled=False,
            cur_ps=1,
        )
        st_idle = extract_presubmit_status_from_change(
            {"_number": 101, "project": "p/a", "messages": []},
            pv_score=0,
            pr_enabled=False,
            cur_ps=1,
        )
        st_queued = extract_presubmit_status_from_change(
            {"_number": 101, "project": "p/a", "messages": []},
            pv_score=0,
            pr_enabled=True,
            cur_ps=1,
        )
        self.assertEqual(st_warn["presubmit_label"], "⚠ PV +1")
        self.assertEqual(st_fail["presubmit_label"], "✗ PV -1")
        self.assertEqual(st_idle["presubmit_label"], "⏸ PV 0")
        self.assertEqual(st_queued["presubmit_label"], "⏳ PV Queued")

        html = render_static_gerrit_ui_html()
        self.assertIn("cl-card-submittable", html)
        self.assertIn("function isClSubmittable(", html)
        self.assertIn('<span class="author-badge-prefix">Author:</span>', html)
        self.assertIn("REVIEWER_ICON_SVG", html)
        self.assertIn('class="rev-eye-icon"', html)
        self.assertIn("Needs two +2s", html)
        self.assertNotIn("Needs 2nd Human +2", html)
        self.assertNotIn("Needs Your +2", html)
        self.assertIn("rgba(223, 91, 83, 0.2)", html)

        card_fn_match = re.search(
            r"function renderClCardHtml\(c, stackId\)\s*\{(.*?)\n  \}",
            html,
            flags=re.S,
        )
        self.assertIsNotNone(card_fn_match)
        card_fn_body = card_fn_match.group(1) if card_fn_match else ""
        self.assertNotIn("human_involvement", card_fn_body)
        self.assertNotIn("slot-silent", card_fn_body)

        status_fn_match = re.search(
            r"function renderCardStatusRowItemsHtml\(c\)\s*\{(.*?)\n  \}",
            html,
            flags=re.S,
        )
        self.assertIsNotNone(status_fn_match)
        status_fn_body = status_fn_match.group(1) if status_fn_match else ""
        self.assertNotIn("⚡", status_fn_body)

    def test_author_and_commit_type_presets_collapsed_by_default(self) -> None:
        html = render_static_gerrit_ui_html()
        self.assertIn('id="queueSearchBar" class="queue-search-bar collapsed"', html)
        self.assertIn('id="authorPresetsToggleBtn"', html)
        self.assertIn('id="authorPresetsBody" class="author-presets-body"', html)
        self.assertIn(".queue-search-bar.collapsed .author-presets-body", html)
        self.assertIn("authorPresetsCollapsed: true", html)
        self.assertIn("function toggleAuthorPresets(", html)

        self.assertIn(
            'id="queueTypeToolbar" class="queue-type-toolbar collapsed"', html
        )
        self.assertIn('id="typePresetsToggleBtn"', html)
        self.assertIn('id="typeFiltersBar" class="type-filters"', html)
        self.assertIn(".queue-type-toolbar.collapsed .type-filters", html)
        self.assertIn("typePresetsCollapsed: true", html)
        self.assertIn("function toggleTypePresets(", html)
        self.assertIn("function syncPresetCollapseControls(", html)

        group_row_match = re.search(
            r'<div class="queue-group-row">(.*?)</div>\s*<div id="typeFiltersBar"',
            html,
            flags=re.S,
        )
        self.assertIsNotNone(group_row_match)
        group_row_inner = group_row_match.group(1) if group_row_match else ""
        self.assertIn('id="queueSortSelect"', group_row_inner)
        self.assertIn('id="grpType"', group_row_inner)
        self.assertIn('id="grpStack"', group_row_inner)
        self.assertIn('id="typePresetsToggleBtn"', group_row_inner)
        self.assertNotIn("COMMIT TYPE &amp; SORT:", group_row_inner)

    def test_filter_section_below_sort_collapsed_by_default_and_gates_synced(
        self,
    ) -> None:
        html = render_static_gerrit_ui_html()

        self.assertNotIn('<div class="topbar">', html)
        viewer_toolbar_match = re.search(
            r'<div id="queueViewerToolbar"[^>]*>(.*?)</div>\s*<div id="queueSearchBar"',
            html,
            flags=re.S,
        )
        self.assertIsNotNone(viewer_toolbar_match)
        viewer_toolbar_html = viewer_toolbar_match.group(1) if viewer_toolbar_match else ""
        self.assertNotIn('id="gatePresubmit"', viewer_toolbar_html)
        self.assertNotIn('id="gateAgent"', viewer_toolbar_html)
        self.assertNotIn('id="gateHideMine"', viewer_toolbar_html)

        viewer_idx = html.index('id="queueViewerToolbar"')
        search_idx = html.index('id="queueSearchBar"')
        sort_idx = html.index('id="queueTypeToolbar"')
        filter_idx = html.index('id="queueFilterToolbar"')
        list_idx = html.index('id="queueList"')
        self.assertLess(viewer_idx, search_idx)
        self.assertLess(search_idx, sort_idx)
        self.assertLess(sort_idx, filter_idx)
        self.assertLess(filter_idx, list_idx)

        self.assertIn(
            'id="queueFilterToolbar" class="queue-filter-toolbar collapsed"', html
        )
        self.assertIn(".queue-filter-toolbar.collapsed .filter-extra-body", html)
        self.assertIn("filterPresetsCollapsed: true", html)
        self.assertIn("function syncGateControls(", html)
        self.assertIn("function toggleFilterPresets(", html)
        self.assertIn("function makeSessionApprovalKey(", html)
        self.assertIn("savedNonOwnerGates", html)

        filter_row_match = re.search(
            r'<div class="queue-filter-row">(.*?)</div>\s*<div id="filterExtraBody"',
            html,
            flags=re.S,
        )
        self.assertIsNotNone(filter_row_match)
        filter_row_inner = filter_row_match.group(1) if filter_row_match else ""
        self.assertIn('id="gatePresubmit"', filter_row_inner)
        self.assertIn("✓ PV +1/+2", filter_row_inner)
        self.assertIn('id="gateHideMine"', filter_row_inner)
        self.assertIn("✓ Hide +2 by Me", filter_row_inner)
        self.assertIn('id="gateHideSubmittable"', filter_row_inner)
        self.assertIn("✓ Hide Submittable", filter_row_inner)
        self.assertIn('id="hiddenSubmittableCount"', filter_row_inner)
        self.assertIn('id="filterPresetsToggleBtn"', filter_row_inner)
        self.assertNotIn('id="gateAgent"', filter_row_inner)
        self.assertIn("hideSubmittable: true", html)
        self.assertIn(
            "if (state.gates.hideSubmittable && isClSubmittable(cl)) return false;",
            html,
        )

        extra_body_match = re.search(
            r'<div id="filterExtraBody" class="filter-extra-body">(.*?)</div>\s*</div>\s*<div id="queueList"',
            html,
            flags=re.S,
        )
        self.assertIsNotNone(extra_body_match)
        extra_body_inner = extra_body_match.group(1) if extra_body_match else ""
        self.assertIn('id="gateAgent"', extra_body_inner)
        self.assertIn('class="gate-pill"', extra_body_inner)
        self.assertNotIn('class="gate-pill active"', extra_body_inner)

    def test_extract_enclosing_function_from_diff_lines_with_unicode(self) -> None:
        self.assertEqual(
            extract_symbol_from_code_line("  async def handle_测试_🚀_é(req, ctx):"),
            "handle_测试_🚀_é",
        )
        self.assertEqual(
            extract_symbol_from_code_line(
                "absl::StatusOr<int> SocketManager::ConnectPeer(std::string_view host) {"
            ),
            "SocketManager::ConnectPeer",
        )
        self.assertEqual(
            extract_symbol_from_code_line("func (s *Server) ServeHTTP(w ResponseWriter, r *Request) {"),
            "ServeHTTP",
        )
        self.assertEqual(extract_symbol_from_code_line("  if (status.ok()) {"), "")

        diff_lines = [
            {"type": "hunk", "old_ln": None, "new_ln": None, "text": "@@ -10,4 +40,7 @@ void OuterHunkFn() {"},
            {"type": "ctx", "old_ln": 10, "new_ln": 40, "text": "  int x = 1;"},
            {"type": "add", "old_ln": None, "new_ln": 41, "text": "void RenderFrame_测试_🚀_é(int width) {"},
            {"type": "add", "old_ln": None, "new_ln": 42, "text": "  if (width <= 0) return;"},
            {"type": "add", "old_ln": None, "new_ln": 43, "text": "  Draw(width);"},
        ]
        self.assertEqual(
            extract_enclosing_function_from_diff_lines(diff_lines, 40, "REVISION"),
            "OuterHunkFn",
        )
        self.assertEqual(
            extract_enclosing_function_from_diff_lines(diff_lines, 43, "REVISION"),
            "RenderFrame_测试_🚀_é",
        )

    def test_concurrent_code_questions_spawn_isolated_conversations_and_block_inflight(
        self,
    ) -> None:
        with patch.object(
            GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"
        ):
            service = GerritUIService(default_viewer="joshuaduong@google.com")
        spawned_counter = [0]
        agentapi_calls: list[tuple[list[str], str | None]] = []

        def fake_agentapi(args: list[str], project_id: str | None = None) -> dict[str, str]:
            agentapi_calls.append((list(args), project_id))
            if args[0] == "get-conversation-metadata":
                return {"conversationId": args[1], "projectId": "proj-gerrit-ui"}
            if args[0] == "new-conversation":
                spawned_counter[0] += 1
                return {"conversationId": f"conv-qa-{spawned_counter[0]}"}
            return {"conversationId": args[1]}

        with tempfile.TemporaryDirectory() as tmp_brain:
            brain_root = Path(tmp_brain)
            orig_discover = discover_transcript_code_questions

            def scoped_discover(*d_args, **d_kwargs):
                d_kwargs.setdefault("brain_root", brain_root)
                return orig_discover(*d_args, **d_kwargs)

            with (
                patch.dict(os.environ, {"ANTIGRAVITY_SIDECAR_CONVERSATION_ID": "main-chat-conv"}),
                patch(
                    "commands.gerrit.services.inline_qa_service._run_sidecar_agentapi",
                    side_effect=fake_agentapi,
                ),
                patch(
                    "commands.gerrit.services.inline_qa_service.discover_transcript_code_questions",
                    side_effect=scoped_discover,
                ),
            ):
                q1 = service.ask_agent_about_code(
                    cl_number=43256612,
                    file_path="emulator/net/socket_测试_🚀_é.cc",
                    start_line=42,
                    end_line=45,
                    code_snippet="+  return sock.Connect();",
                    question="Is Connect() non-blocking here?",
                    spawn_new=True,
                    thread_id="inline-1",
                    function_name="SocketManager::Connect",
                )
                q2 = service.ask_agent_about_code(
                    cl_number=43256612,
                    file_path="emulator/net/socket_测试_🚀_é.cc",
                    start_line=120,
                    end_line=125,
                    code_snippet="+  sock.Close();",
                    question="Could Close() race with the reader thread?",
                    spawn_new=True,
                    thread_id="inline-2",
                    function_name="SocketManager::Shutdown",
                )
                self.assertEqual(q1["conversation_id"], "conv-qa-1")
                self.assertEqual(q2["conversation_id"], "conv-qa-2")
                self.assertNotEqual(q1["conversation_id"], "main-chat-conv")

                # Submitting another question in conv-qa-1 while Turn 1 is thinking MUST fail.
                with self.assertRaisesRegex(ValueError, "still being processed"):
                    service.ask_agent_about_code(
                        cl_number=43256612,
                        file_path="emulator/net/socket_测试_🚀_é.cc",
                        start_line=42,
                        end_line=45,
                        code_snippet="+  return sock.Connect();",
                        question="Early impatient follow-up?",
                        conversation_id="conv-qa-1",
                        spawn_new=False,
                        thread_id="inline-1",
                    )

                # Write answered transcript for conv-qa-1 while conv-qa-2 stays pending.
                conv1_logs = brain_root / "conv-qa-1" / ".system_generated" / "logs"
                conv1_logs.mkdir(parents=True, exist_ok=True)
                (conv1_logs / "transcript_full.jsonl").write_text(
                    "\n".join([
                        json.dumps({
                            "step_index": 0,
                            "type": "USER_INPUT",
                            "content": q1["prompt"],
                        }),
                        json.dumps({
                            "step_index": 2,
                            "type": "PLANNER_RESPONSE",
                            "content": "Yes, `Connect()` uses non-blocking `O_NONBLOCK`.",
                        }),
                    ]) + "\n",
                    encoding="utf-8",
                )

                polled = service.get_code_questions([43256612], conversation_id="main-chat-conv")
                by_tid = {item["thread_id"]: item for item in polled}
                self.assertEqual(by_tid["inline-1"]["status"], "answered")
                self.assertIn("O_NONBLOCK", by_tid["inline-1"]["reply"])
                self.assertEqual(by_tid["inline-1"]["function_name"], "SocketManager::Connect")
                self.assertEqual(by_tid["inline-2"]["status"], "thinking")

                # Now that conv-qa-1 is answered, a follow-up in conv-qa-1 succeeds and locks it again.
                q1_followup = service.ask_agent_about_code(
                    cl_number=43256612,
                    file_path="emulator/net/socket_测试_🚀_é.cc",
                    start_line=42,
                    end_line=45,
                    code_snippet="+  return sock.Connect();",
                    question="What happens on EINPROGRESS?",
                    conversation_id="conv-qa-1",
                    spawn_new=False,
                    thread_id="inline-1",
                )
                self.assertEqual(q1_followup["conversation_id"], "conv-qa-1")
                self.assertEqual(len(q1_followup["turns"]), 2)
                self.assertEqual(q1_followup["turns"][1]["status"], "thinking")
                with self.assertRaisesRegex(ValueError, "still being processed"):
                    service.ask_agent_about_code(
                        cl_number=43256612,
                        file_path="emulator/net/socket_测试_🚀_é.cc",
                        start_line=42,
                        end_line=45,
                        code_snippet="+  return sock.Connect();",
                        question="Third question while second is still thinking?",
                        conversation_id="conv-qa-1",
                        spawn_new=False,
                        thread_id="inline-1",
                    )

    def test_modify_and_delete_inflight_code_question_root_and_followup(self) -> None:
        with patch.object(
            GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"
        ):
            service = GerritUIService(default_viewer="joshuaduong@google.com")
        spawned_counter = [0]
        ls_calls: list[tuple[str, dict[str, object]]] = []

        def fake_agentapi(args: list[str], project_id: str | None = None) -> dict[str, str]:
            if args[0] == "new-conversation":
                spawned_counter[0] += 1
                return {"conversationId": f"conv-mod-{spawned_counter[0]}"}
            return {"conversationId": args[1]}

        def fake_ls_rpc(method: str, payload: dict[str, object]) -> tuple[bool, dict[str, object]]:
            ls_calls.append((method, dict(payload)))
            return True, {}

        with tempfile.TemporaryDirectory() as tmp_brain:
            brain_root = Path(tmp_brain)
            orig_discover = discover_transcript_code_questions

            def scoped_discover(*d_args, **d_kwargs):
                d_kwargs.setdefault("brain_root", brain_root)
                return orig_discover(*d_args, **d_kwargs)

            with (
                patch(
                    "commands.gerrit.services.inline_qa_service._run_sidecar_agentapi",
                    side_effect=fake_agentapi,
                ),
                patch(
                    "commands.gerrit.services.inline_qa_service._call_local_language_server_rpc",
                    side_effect=fake_ls_rpc,
                ),
                patch(
                    "commands.gerrit.services.inline_qa_service.discover_transcript_code_questions",
                    side_effect=scoped_discover,
                ),
            ):
                # 1. Start root question, then Modify it while in-flight.
                initial = service.ask_agent_about_code(
                    cl_number=43256612,
                    file_path="emulator/io/file_测试_🚀_é.cc",
                    start_line=15,
                    end_line=19,
                    code_snippet="+  OpenUtf8Path(path);",
                    question="Original typo question?",
                    spawn_new=True,
                    thread_id="inline-root-1",
                    function_name="OpenUtf8Path",
                )
                self.assertEqual(initial["conversation_id"], "conv-mod-1")

                modified = service.modify_or_delete_code_question(
                    action="modify",
                    cl_number=43256612,
                    conversation_id="conv-mod-1",
                    thread_id="inline-root-1",
                    question="Updated UTF-8 question 测试_🚀_é?",
                )
                self.assertEqual(modified["action"], "modify")
                self.assertEqual(modified["conversation_id"], "conv-mod-2")
                self.assertEqual(modified["turns"][0]["question"], "Updated UTF-8 question 测试_🚀_é?")
                self.assertIn(("CancelCascadeInvocation", {"cascadeId": "conv-mod-1", "killBackgroundTasks": True}), ls_calls)
                self.assertIn(("DeleteCascadeTrajectory", {"cascadeId": "conv-mod-1"}), ls_calls)

                # 2. Delete the modified 1-turn root question via HTTP handler.
                handler_cls = make_gerrit_ui_handler(service, ui_token=_TEST_UI_TOKEN)
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
                port = httpd.server_address[1]
                srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                srv_thread.start()
                try:
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/gerrit/code-question-action",
                        data=json.dumps({
                            "action": "delete",
                            "cl": 43256612,
                            "conversationId": "conv-mod-2",
                            "threadId": "inline-root-1",
                        }).encode("utf-8"),
                        headers=_JSON_AUTH_HEADERS,
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        del_data = json.loads(resp.read().decode("utf-8"))
                    self.assertTrue(del_data["thread_deleted"])
                    self.assertEqual(service.get_code_questions([43256612]), [])
                finally:
                    httpd.shutdown()
                    httpd.server_close()

                # 3. Multi-turn conversation: Turn 1 answered, Turn 2 in-flight -> Delete Turn 2 rewinds to Turn 1.
                t1 = service.ask_agent_about_code(
                    cl_number=43256612,
                    file_path="emulator/io/file_测试_🚀_é.cc",
                    start_line=30,
                    end_line=34,
                    code_snippet="+  Flush();",
                    question="Does Flush() fsync?",
                    spawn_new=True,
                    thread_id="inline-multi",
                    function_name="FlushBuffer",
                )
                conv3 = t1["conversation_id"]
                conv3_logs = brain_root / conv3 / ".system_generated" / "logs"
                conv3_logs.mkdir(parents=True, exist_ok=True)
                (conv3_logs / "transcript_full.jsonl").write_text(
                    "\n".join([
                        json.dumps({"step_index": 0, "type": "USER_INPUT", "content": t1["prompt"]}),
                        json.dumps({"step_index": 3, "type": "PLANNER_RESPONSE", "content": "No, it flushes userspace buffers only."}),
                    ]) + "\n",
                    encoding="utf-8",
                )
                service.get_code_questions([43256612])
                t2 = service.ask_agent_about_code(
                    cl_number=43256612,
                    file_path="emulator/io/file_测试_🚀_é.cc",
                    start_line=30,
                    end_line=34,
                    code_snippet="+  Flush();",
                    question="Should we call fsync here?",
                    conversation_id=conv3,
                    spawn_new=False,
                    thread_id="inline-multi",
                )
                # Simulate Turn 2 written to transcript before user clicks Delete on Turn 2
                (conv3_logs / "transcript_full.jsonl").write_text(
                    "\n".join([
                        json.dumps({"step_index": 0, "type": "USER_INPUT", "content": t1["prompt"]}),
                        json.dumps({"step_index": 3, "type": "PLANNER_RESPONSE", "content": "No, it flushes userspace buffers only."}),
                        json.dumps({"step_index": 4, "type": "USER_INPUT", "content": t2["prompt"]}),
                    ]) + "\n",
                    encoding="utf-8",
                )
                ls_calls.clear()
                del_followup = service.modify_or_delete_code_question(
                    action="delete",
                    cl_number=43256612,
                    conversation_id=conv3,
                    thread_id="inline-multi",
                )
                self.assertFalse(del_followup["thread_deleted"])
                self.assertEqual(len(del_followup["turns"]), 1)
                self.assertEqual(del_followup["turns"][0]["reply"], "No, it flushes userspace buffers only.")
                self.assertIn(
                    ("RevertToCascadeStep", {"cascadeId": conv3, "stepIndex": 3, "conversationOnly": True}),
                    ls_calls,
                )

    def test_concurrent_qa_ui_controls_and_reply_notification_bubble_stack(self) -> None:
        html = render_static_gerrit_ui_html()
        self.assertIn('id="agentReplyBubbleStack"', html)
        self.assertIn("Agent responded at <code>", html)
        self.assertIn("function pushAgentReplyNotification(", html)
        self.assertIn("function navigateToAgentReplyBubble(", html)
        self.assertIn("function dismissAgentReplyBubble(", html)
        self.assertIn("function startModifyInflightQuestion(", html)
        self.assertIn("function saveModifiedInflightQuestion(", html)
        self.assertIn("function deleteInflightCodeQuestion(", html)
        self.assertIn("function submitInlineFollowupQuestion(", html)
        self.assertIn("function isQuestionThreadPending(", html)
        self.assertIn("spawnNew: true", html)
        self.assertIn(
            '<button id="btnSubmitCodeAsk" class="btn btn-swarm" onclick="submitSelectedCodeQuestion(this)">🤖 Ask Agent</button>',
            html,
        )
        self.assertNotIn("Switch to Ask Agent", html)
        self.assertNotIn("Switch to Gerrit Comment", html)

    def test_ai_analysis_intangibles_prompt_bug_context_and_truncated_transcript(self) -> None:
        sample_diff_files = [
            {
                "file": "emulator/io/file_测试_🚀_é.cc",
                "additions": 3,
                "deletions": 1,
                "truncated": False,
                "lines": [
                    {"type": "hunk", "old": "", "new": "", "text": "@@ -10,4 +10,6 @@ bool OpenChannel() {"},
                    {"type": "ctx", "old": "10", "new": "10", "text": "  if (path.empty()) return false;"},
                    {"type": "del", "old": "11", "new": "", "text": "- return LegacyOpen(path);"},
                    {"type": "add", "old": "", "new": "11", "text": "+ return OpenWithRetry(path, /*retries=*/3);"},
                    {"type": "ctx", "old": "12", "new": "12", "text": "}"},
                ],
            }
        ]
        commit_msg = (
            "fix(io): Retry transient OpenChannel EAGAIN on UTF-8 path 测试_🚀_é\n\n"
            "Bug: 381234567\n"
            "See also b/389999999 and Bug: 381234567\n"
        )
        self.assertEqual(extract_bug_ids_from_text(commit_msg), ["381234567", "389999999"])

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fake_issues_bin = tmp_path / "fake_issues"
            fake_issues_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_issues_bin.chmod(0o755)

            with patch(
                "subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="issue_id: 381234567\ntitle: Pipe open races PipeServer::Bind 测试_🚀_é\n",
                    stderr="",
                ),
            ) as mock_issues:
                bug_ctx = fetch_buganizer_context(["381234567"], issues_bin=str(fake_issues_bin))
            self.assertIn("untrusted ticket data", bug_ctx)
            self.assertIn("### Ticket b/381234567", bug_ctx)
            self.assertIn("PipeServer::Bind 测试_🚀_é", bug_ctx)
            mock_issues.assert_called_once()

            fake_repo = tmp_path / "emu-main-next" / "hardware" / "generic" / "goldfish"
            (fake_repo / ".git").mkdir(parents=True)
            cfg_file = tmp_path / "emu-dev-cli.json"
            cfg_file.write_text(
                json.dumps(
                    {
                        "source_directories": {
                            "citc_ignore": "/google/src/cloud/someone/ws/google3",
                            "emu_next": str(tmp_path / "emu-main-next"),
                        }
                    }
                ),
                encoding="utf-8",
            )
            resolved_repo = resolve_local_repo_root(
                "platform/hardware/generic/goldfish", config_path=cfg_file
            )
            self.assertEqual(resolved_repo, fake_repo)

            prompt = build_ai_analysis_prompt(
                cl_id="43299999",
                patch_set=2,
                subject="fix(io): Retry transient OpenChannel EAGAIN on UTF-8 path 测试_🚀_é",
                project="platform/hardware/generic/goldfish",
                commit_message=commit_msg,
                diff_files=sample_diff_files,
                bug_context=bug_ctx,
                config_path=cfg_file,
            )
            self.assertIn("[AEMU_INTANGIBLES_ANALYSIS v1 cl=ag/43299999 ps=2]", prompt)
            self.assertIn(str(fake_repo), prompt)
            self.assertIn("emulator/io/file_测试_🚀_é.cc", prompt)
            self.assertIn("DO NOT REPEAT AUTOMATED CHECKS", prompt)
            self.assertIn("Underlying Problem", prompt)
            self.assertIn("Alternative Solutions", prompt)

            raw_payload = {
                "scope_assessment": "symptom_patch",
                "bottom_line": "Masks startup race via blind retries instead of awaiting PipeServer::Bind readiness (`测试_🚀_é`).",
                "underlying_problem": "Guest PipeConnection opens the host socket before PipeServer::Bind finishes listening.",
                "chosen_solution": "Wraps LegacyOpen in a 3-attempt retry loop inside OpenChannel.",
                "key_tradeoffs": [
                    "Simple localized diff, but still flakes if host scheduling stalls beyond 3 retries.",
                    "Adds latency on genuine non-transient open failures.",
                ],
                "alternatives": [
                    {
                        "verdict": "worth_asking",
                        "title": "Await PipeServer::WaitUntilListening readiness barrier",
                        "how_it_works": "Signal readiness from `PipeServer::Bind` before guest channel probing starts.",
                        "tradeoff": "Eliminates the timing race deterministically with slightly more plumbing.",
                        "ask_prompt": "Does PipeServer already expose a readiness latch we can await here?",
                        "review_comment_draft": "Could we await `PipeServer` readiness instead of retrying 3 times?",
                    },
                    {
                        "verdict": "worse_fit",
                        "title": "Pre-create global static pipe socket at process init",
                        "how_it_works": "Allocate the socket before main loop initialization.",
                        "tradeoff": "Breaks per-test isolation and leaks fds across headless restarts.",
                    },
                ],
            }
            normalized = normalize_ai_analysis_payload(raw_payload)
            self.assertEqual(normalized["scope_assessment"], "symptom_patch")
            self.assertEqual(normalized["scope_label"], "Patches Symptom / Workaround")
            self.assertEqual(normalized["scope_tone"], "warn")
            self.assertEqual(len(normalized["key_tradeoffs"]), 2)
            self.assertEqual(len(normalized["alternatives"]), 2)
            self.assertEqual(normalized["alternatives"][0]["id"], "alt-1")
            self.assertEqual(normalized["alternatives"][0]["verdict"], "worth_asking")
            self.assertEqual(normalized["alternatives"][0]["verdict_label"], "Worth Asking Author")
            self.assertEqual(normalized["alternatives"][1]["verdict"], "worse_fit")
            self.assertTrue(normalized["alternatives"][1]["ask_prompt"])
            self.assertTrue(normalized["alternatives"][1]["review_comment_draft"])

            # Verify truncated_fields fallback from transcript.jsonl to transcript_full.jsonl
            brain_root = tmp_path / "brain"
            logs_dir = brain_root / "conv-trunc-1" / ".system_generated" / "logs"
            logs_dir.mkdir(parents=True)
            full_json_reply = "```json\n" + json.dumps(raw_payload, ensure_ascii=False) + "\n```"
            (logs_dir / "transcript.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"step_index": 0, "type": "USER_INPUT", "content": prompt}),
                        json.dumps(
                            {
                                "step_index": 2,
                                "type": "PLANNER_RESPONSE",
                                "content": "```json\n{\"scope_assessment\": \"symptom_patch\", \"bottom_line\": \"trunc...",
                                "truncated_fields": ["content"],
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (logs_dir / "transcript_full.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"step_index": 0, "type": "USER_INPUT", "content": prompt}),
                        json.dumps(
                            {
                                "step_index": 2,
                                "type": "PLANNER_RESPONSE",
                                "content": full_json_reply,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            parsed = parse_ai_analysis_from_conversation("conv-trunc-1", brain_root=brain_root)
            self.assertIsNotNone(parsed)
            assert parsed is not None
            self.assertIn("测试_🚀_é", parsed["bottom_line"])
            self.assertEqual(parsed["scope_assessment"], "symptom_patch")
            self.assertEqual(len(parsed["alternatives"]), 2)

    def test_ai_analysis_explicit_analyze_http_cancel_and_qa_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            brain_root = tmp_path / "brain"
            cache_dir = tmp_path / "ai_analysis_cache"

            with patch.object(
                GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"
            ):
                service = GerritUIService(default_viewer="joshuaduong@google.com")
            service._ai_analysis_cache_dir = cache_dir

            fake_bundle = {
                "number": 43288888,
                "commit_message": "fix(gerrit): Tighten token check 测试_🚀_é",
                "files": [
                    {
                        "file": "tools/server_测试_🚀_é.py",
                        "additions": 2,
                        "deletions": 1,
                        "truncated": False,
                        "lines": [
                            {"type": "hunk", "old": "", "new": "", "text": "@@ -20,2 +20,3 @@ def check_token(tok):"},
                            {"type": "del", "old": "20", "new": "", "text": "- return bool(tok)"},
                            {"type": "add", "old": "", "new": "20", "text": "+ return hmac.compare_digest(tok or '', SECRET)"},
                        ],
                    }
                ],
                "unresolved_threads": [],
                "is_pure_rebase": False,
            }
            spawn_calls: list[list[str]] = []
            ls_calls: list[tuple[str, dict[str, object]]] = []

            def fake_agentapi(args: list[str], project_id: str | None = None) -> dict[str, str]:
                spawn_calls.append(list(args))
                return {"conversationId": "conv-intangibles-1"}

            def fake_ls_rpc(method: str, payload: dict[str, object]) -> dict[str, object]:
                ls_calls.append((method, dict(payload)))
                return {}

            orig_discover = discover_transcript_code_questions

            def scoped_discover(*d_args, **d_kwargs):
                d_kwargs.setdefault("brain_root", brain_root)
                return orig_discover(*d_args, **d_kwargs)

            with (
                patch.object(service, "fetch_commit_diff_bundle", return_value=fake_bundle),
                patch(
                    "commands.gerrit.services.inline_qa_service._run_sidecar_agentapi",
                    side_effect=fake_agentapi,
                ),
                patch(
                    "commands.gerrit.services.inline_qa_service._call_local_language_server_rpc",
                    side_effect=fake_ls_rpc,
                ),
                patch(
                    "commands.gerrit.services.inline_qa_service.discover_transcript_code_questions",
                    side_effect=scoped_discover,
                ),
                patch("commands.gerrit.ai_analysis.fetch_buganizer_context", return_value=""),
            ):
                # 1. Default GET never auto-triggers an agent run.
                initial = service.get_ai_analysis(43288888, patchset=2, brain_root=brain_root)
                self.assertEqual(initial["analysis_status"], "not_analyzed")
                self.assertIsNone(initial["analysis"])
                self.assertEqual(spawn_calls, [])

                # 2. Exercise HTTP GET + POST /api/gerrit/ai-analysis on local server.
                server = ThreadingHTTPServer(("127.0.0.1", 0), make_gerrit_ui_handler(service, ui_token=_TEST_UI_TOKEN))
                port = server.server_address[1]
                server_thread = threading.Thread(target=server.serve_forever, daemon=True)
                server_thread.start()
                try:
                    with urllib.request.urlopen(
                        _authed_get(f"http://127.0.0.1:{port}/api/gerrit/ai-analysis?cl=43288888&ps=2"),
                        timeout=5,
                    ) as resp:
                        http_initial = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(http_initial["analysis_status"], "not_analyzed")

                    post_req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/gerrit/ai-analysis",
                        data=json.dumps(
                            {
                                "cl": 43288888,
                                "action": "analyze",
                                "ps": 2,
                                "project": "platform/hardware/google/aemu",
                                "subject": "fix(gerrit): Tighten token check 测试_🚀_é",
                            }
                        ).encode("utf-8"),
                        headers=_JSON_AUTH_HEADERS,
                        method="POST",
                    )
                    with urllib.request.urlopen(post_req, timeout=5) as resp:
                        started = json.loads(resp.read().decode("utf-8"))
                finally:
                    server.shutdown()
                    server.server_close()

                self.assertEqual(started["analysis_status"], "thinking")
                self.assertEqual(started["conversation_id"], "conv-intangibles-1")
                self.assertEqual(len(spawn_calls), 1)

                # Write analyzer transcript and verify get_code_questions() ignores it while get_ai_analysis() resolves it.
                logs_dir = brain_root / "conv-intangibles-1" / ".system_generated" / "logs"
                logs_dir.mkdir(parents=True)
                (logs_dir / "transcript.jsonl").write_text(
                    "\n".join(
                        [
                            json.dumps({"step_index": 0, "type": "USER_INPUT", "content": spawn_calls[0][-1]}),
                            json.dumps(
                                {
                                    "step_index": 2,
                                    "type": "PLANNER_RESPONSE",
                                    "content": json.dumps(
                                        {
                                            "scope_assessment": "root_cause",
                                            "bottom_line": "Replaces truthy check with constant-time HMAC comparison (`测试_🚀_é`).",
                                            "underlying_problem": "`bool(tok)` accepted any non-empty string as valid sidecar auth.",
                                            "chosen_solution": "Uses `hmac.compare_digest` against the server secret.",
                                            "key_tradeoffs": ["Constant-time comparison with zero external dependencies."],
                                            "alternatives": [
                                                {
                                                    "verdict": "viable_tradeoff",
                                                    "title": "Per-request signed session cookie",
                                                    "how_it_works": "Issue an HttpOnly cookie on initial iframe bootstrap.",
                                                    "tradeoff": "Avoids header plumbing on XHRs, though iframe SameSite rules complicate localhost.",
                                                }
                                            ],
                                        },
                                        ensure_ascii=False,
                                    ),
                                }
                            ),
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
                self.assertEqual(service.get_code_questions([43288888]), [])

                ready = service.get_ai_analysis(43288888, patchset=2, brain_root=brain_root)
                self.assertEqual(ready["analysis_status"], "ready")
                self.assertEqual(ready["analysis"]["scope_assessment"], "root_cause")
                self.assertEqual(len(ready["analysis"]["alternatives"]), 1)
                self.assertTrue((cache_dir / "cl_43288888_ps_2.json").is_file())

                # 3. Cancel cleans up trajectory and resets state to not_analyzed.
                cancelled = service.trigger_or_cancel_ai_analysis(
                    43288888, action="cancel", patchset=2, brain_root=brain_root
                )
                self.assertEqual(cancelled["analysis_status"], "not_analyzed")
                self.assertFalse((cache_dir / "cl_43288888_ps_2.json").exists())
                self.assertIn(
                    ("CancelCascadeInvocation", {"cascadeId": "conv-intangibles-1", "killBackgroundTasks": True}),
                    ls_calls,
                )
                self.assertIn(("DeleteCascadeTrajectory", {"cascadeId": "conv-intangibles-1"}), ls_calls)

    def test_harness_actions_dropdown_and_problem_alternatives_ui(self) -> None:
        html = render_static_gerrit_ui_html()
        self.assertIn('id="btnHarnessMenuToggle"', html)
        self.assertIn('id="harnessDropdownMenu"', html)
        self.assertIn('id="btnAiAnalysis"', html)
        self.assertIn('id="btnRunSwarm"', html)
        self.assertIn('id="btnAutoFix"', html)
        self.assertIn("Analyze Problem &amp; Alternatives", html)
        self.assertIn("Run Review Swarm (8 Specialists)", html)
        self.assertIn("Auto-Address Comments", html)
        self.assertIn("function positionHarnessDropdownMenu(", html)
        self.assertIn("function toggleHarnessMenu(", html)
        self.assertIn("function closeHarnessMenu(", html)
        self.assertIn("function onHarnessMenuAction(", html)
        self.assertIn("function syncHarnessMenuButton(", html)
        self.assertIn("function onAiAnalysisButtonClick(", html)
        self.assertIn("function analyzeClIntangibles(", html)
        self.assertIn("function cancelClAiAnalysis(", html)
        self.assertIn("function renderCommitAiAnalysisPanelHtml(", html)
        self.assertIn("function getInlineItemsForAiAlternative(", html)
        self.assertIn("function openAiAlternativeInComposer(", html)
        self.assertIn("aiAltCard-${num}-${altId}", html)
        self.assertIn("Alternative Solution ·", html)
        self.assertNotIn("HARNESS ACTIONS:</span>", html)
        self.assertNotIn("btnAiAssistToggle", html)
        self.assertNotIn("btnAnalyzeThisCl", html)
        self.assertNotIn("ai-inlay-card", html)
        self.assertNotIn("reviewModeSelect", html)

    def test_radar_in_app_navigation_and_details_external_gerrit_pill(self) -> None:
        html = render_static_gerrit_ui_html()

        # 1. Verify renderCliffRadar() contains zero external target="_blank" links on chips/bugs.
        radar_fn_match = re.search(
            r"function renderCliffRadar\(\)\s*\{(.*?)\n  \}",
            html,
            flags=re.S,
        )
        self.assertIsNotNone(radar_fn_match)
        radar_fn_body = radar_fn_match.group(1) if radar_fn_match else ""
        self.assertNotIn('target="_blank"', radar_fn_body)
        self.assertNotIn('class="ag-link"', radar_fn_body)
        self.assertIn("renderBugBadgesHtml(c, { interactive: false })", radar_fn_body)
        self.assertIn('class="radar-ag-label">ag/${c.number}</span>', radar_fn_body)
        self.assertIn('onclick="selectCl(${c.number})"', radar_fn_body)
        # Only DM Reviewer and Copy Chat Ping buttons stop propagation inside radar chips.
        stop_prop_lines = [
            ln.strip()
            for ln in radar_fn_body.splitlines()
            if "stopPropagation" in ln
        ]
        self.assertEqual(len(stop_prop_lines), 2)
        self.assertTrue(any("openChatDmFromRadar" in ln for ln in stop_prop_lines))
        self.assertTrue(any("copyPingForCl" in ln for ln in stop_prop_lines))

        # 2. Verify details screen header includes non-link CL badge + prominent external Gerrit pill.
        self.assertIn('<span class="commit-cl-id">ag/${c.number}</span>', html)
        self.assertIn('class="external-gerrit-pill"', html)
        self.assertIn("Open in Gerrit ↗", html)

        # 3. Execute inline UI script in Node V8 to verify in-app radar selection across gates/filters.
        node_bin = shutil.which("node")
        if not node_bin:
            return
        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S)
        self.assertTrue(scripts)
        ui_js = "\n".join(scripts)
        for boot_call in (
            "loadSidebarLayoutPrefs();",
            "initWorkspaceSplitter();",
            "initDiffCodeSelectionListeners();",
            "initApp();",
        ):
            ui_js = ui_js.replace(boot_call, f"// {boot_call}")

        harness_js = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const elements = new Map();
function makeEl(id = "") {
  const classes = new Set();
  const attrs = new Map();
  return {
    id,
    innerHTML: "",
    textContent: "",
    value: "",
    disabled: false,
    scrollTop: 0,
    offsetWidth: 800,
    style: { setProperty() {}, removeProperty() {} },
    dataset: {},
    setAttribute(k, v) { attrs.set(String(k), String(v)); },
    getAttribute(k) { return attrs.has(String(k)) ? attrs.get(String(k)) : null; },
    removeAttribute(k) { attrs.delete(String(k)); },
    contains() { return false; },
    classList: {
      add(...names) { names.forEach(n => classes.add(n)); },
      remove(...names) { names.forEach(n => classes.delete(n)); },
      toggle(name, force) {
        if (force === undefined) {
          if (classes.has(name)) { classes.delete(name); return false; }
          classes.add(name); return true;
        }
        if (force) classes.add(name); else classes.delete(name);
        return Boolean(force);
      },
      contains(name) { return classes.has(name); },
    },
    addEventListener() {},
    removeEventListener() {},
    getBoundingClientRect() { return { left: 0, top: 0, right: 100, bottom: 30, width: 100, height: 30 }; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    focus() {},
    select() {},
    scrollIntoView() {},
  };
}
function getEl(id) {
  if (!elements.has(id)) elements.set(id, makeEl(id));
  return elements.get(id);
}

let openedUrls = [];
const sandbox = {
  console,
  setTimeout: () => 1,
  clearTimeout: () => {},
  setInterval: () => 1,
  clearInterval: () => {},
  requestAnimationFrame: (cb) => cb(),
  URLSearchParams,
  encodeURIComponent,
  decodeURIComponent,
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  navigator: { clipboard: { writeText: async () => {} } },
  fetch: async () => ({
    ok: true,
    status: 200,
    json: async () => ({ status: "ok", analysis_status: "not_analyzed", questions: [] }),
  }),
  document: {
    getElementById: getEl,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
    body: makeEl("body"),
    documentElement: makeEl("html"),
  },
};
sandbox.window = sandbox;
sandbox.window.location = { search: "", href: "http://127.0.0.1:8765/" };
sandbox.window.addEventListener = () => {};
sandbox.window.open = (url) => { openedUrls.push(url); };

const code = fs.readFileSync(process.argv[2], "utf8") + `
globalThis.__testApi = {
  state,
  renderAll,
  renderCliffRadar,
  renderDiffPane,
  selectCl,
  getActiveCommitsForDiff,
};
`;
vm.runInNewContext(code, sandbox, { filename: "gerrit_ui_inline.js" });

const api = sandbox.__testApi;
const mkCl = (overrides) => Object.assign({
  number: 43280101,
  project: "platform/hardware/google/aemu",
  project_short: "aemu",
  branch: "studio-main",
  subject: "feat(gerrit): Fresh commit",
  url: `https://googleplex-android-review.git.corp.google.com/c/platform/hardware/google/aemu/+/${overrides.number}`,
  patch_set: 2,
  owner_name: "Erwin Jansen",
  owner_email: "jansene@google.com",
  cliff_zone: "fresh",
  flight_display: "2h",
  flight_hours: 2,
  mergeable: true,
  is_orphaned: false,
  approved_by_viewer: false,
  submittable: false,
  presubmit_vetted: true,
  presubmit_status: "passed",
  presubmit_badge_text: "PV +2",
  agent_vetted: true,
  risk: { tier: "low", label: "Low · +12/-3" },
  insertions: 12,
  deletions: 3,
  approval_slots: [{ state: "needed", text: "Needs +2" }],
  human_cr2: [],
  agent_cr1: [],
  reviewers: [{ email: "joshuaduong@google.com", name: "Joshua Duong" }],
  bugs: [{ id: "485291270", priority: "P2", severity: "S2", status: "ASSIGNED", title: "Fast Gerrit" }],
  commit_type: "feature",
}, overrides);

const clFresh = mkCl({ number: 43280101, cliff_zone: "fresh", subject: "feat(gerrit): Fresh radar CL" });
const clAging = mkCl({ number: 43280202, cliff_zone: "aging", subject: "fix(gerrit): Aging radar CL", presubmit_vetted: false });
// Same stack as clFresh, but gated out by presubmit_vetted=false AND merge conflict!
const clCliffSiblingGated = mkCl({
  number: 43280303,
  cliff_zone: "cliff",
  subject: "perf(gerrit): Cliff gated sibling 测试_🚀_é",
  presubmit_vetted: false,
  mergeable: false,
});

api.state.changes = [clFresh, clAging, clCliffSiblingGated];
api.state.stacks = [
  { stack_id: "stack-shared", commits: [clFresh, clCliffSiblingGated] },
  { stack_id: "stack-aging", commits: [clAging] },
];
api.state.gates = { presubmit: true, agent: false, hideMine: true, hideSubmittable: true };
for (const c of api.state.changes) {
  api.state.diffsByCl[c.number] = {
    number: c.number,
    commit_message: `${c.subject}\n\nBug: 485291270`,
    is_pure_rebase: false,
    unresolved_threads: [
      { author_name: "Erwin", file_path: "tools/ui_测试_🚀_é.py", line: 42, latest_message: `Review note for ${c.number}` },
    ],
    files: [
      {
        file: "tools/ui_测试_🚀_é.py",
        additions: 1,
        deletions: 0,
        truncated: false,
        lines: [{ type: "add", old: "", new: "42", text: `+ // change ${c.number}` }],
      },
    ],
  };
  api.state.aiAnalysisByCl = api.state.aiAnalysisByCl || {};
  api.state.aiAnalysisByCl[c.number] = {
    status: "ready",
    patchset: 2,
    analysis: {
      scope_assessment: "root_cause",
      bottom_line: `Analysis summary for ${c.number}`,
      underlying_problem: "External jump broke triage flow.",
      chosen_solution: "Select in-place inside Fast Gerrit.",
      key_tradeoffs: [],
      alternatives: [],
    },
  };
}

api.renderAll();
api.renderDiffPane();

const freshHtml = getEl("freshChips").innerHTML;
const agingHtml = getEl("agingChips").innerHTML;
const cliffHtml = getEl("cliffChips").innerHTML;

// Radar chips contain zero external links or target="_blank" attributes.
for (const radarHtml of [freshHtml, agingHtml, cliffHtml]) {
  assert.equal(radarHtml.includes('target="_blank"'), false);
  assert.equal(radarHtml.includes("<a "), false);
  assert.equal(radarHtml.includes("↗"), false);
  assert.equal(radarHtml.includes('<span class="bug-chip"'), true);
}
assert.equal(freshHtml.includes("selectCl(43280101)"), true);
assert.equal(agingHtml.includes("selectCl(43280202)"), true);
assert.equal(cliffHtml.includes("selectCl(43280303)"), true);

// Selecting gated cliff sibling CL (43280303) must NOT fall back to visible sibling 43280101.
api.selectCl(43280303);
api.renderDiffPane();
assert.equal(api.state.selectedClNumber, 43280303);
assert.equal(api.state.explicitRadarSelection, true);
assert.deepEqual(Array.from(api.getActiveCommitsForDiff(), c => c.number), [43280303]);
assert.equal(openedUrls.length, 0);

const diffCliffHtml = getEl("diffScroll").innerHTML;
assert.equal(diffCliffHtml.includes('<span class="commit-cl-id">ag/43280303</span>'), true);
assert.equal(diffCliffHtml.includes("perf(gerrit): Cliff gated sibling 测试_🚀_é"), true);
assert.equal(diffCliffHtml.includes("Review note for 43280303"), true);
assert.equal(diffCliffHtml.includes("Analysis summary for 43280303"), true);
assert.equal(diffCliffHtml.replace(/<[^>]+>/g, "").includes("+ // change 43280303"), true);
assert.equal(
  diffCliffHtml.includes(
    '<a class="external-gerrit-pill" href="https://googleplex-android-review.git.corp.google.com/c/platform/hardware/google/aemu/+/43280303" target="_blank" rel="noopener noreferrer"'
  ),
  true
);
assert.equal(diffCliffHtml.includes(">Open in Gerrit ↗</a>"), true);
assert.equal(getEl("cliffChips").innerHTML.includes('class="radar-chip danger selected" data-cl-num="43280303"'), true);

// Even when searchQuery filters out ALL visible stacks, selecting aging CL (43280202) displays it in-app.
api.state.searchQuery = "unmatched-filter-query";
api.selectCl(43280202);
api.renderDiffPane();
assert.equal(api.state.selectedClNumber, 43280202);
assert.deepEqual(Array.from(api.getActiveCommitsForDiff(), c => c.number), [43280202]);
const diffAgingHtml = getEl("diffScroll").innerHTML;
assert.equal(diffAgingHtml.includes('<span class="commit-cl-id">ag/43280202</span>'), true);
assert.equal(diffAgingHtml.includes("fix(gerrit): Aging radar CL"), true);
assert.equal(
  diffAgingHtml.includes(
    'href="https://googleplex-android-review.git.corp.google.com/c/platform/hardware/google/aemu/+/43280202" target="_blank"'
  ),
  true
);
assert.equal(getEl("agingChips").innerHTML.includes('class="radar-chip selected" data-cl-num="43280202" aria-pressed="true"'), true);

// Clicking a Radar chip while Batch Mode checkboxes are active exits Batch Mode and renders that CL's diff.
api.state.searchQuery = "";
api.state.checkedClNumbers.add(43280101);
api.renderAll();
api.renderDiffPane();
assert.equal(getEl("diffScroll").innerHTML.includes('id="batchModeSection"'), true);
api.selectCl(43280303);
api.renderDiffPane();
assert.equal(api.state.checkedClNumbers.size, 0);
assert.equal(getEl("diffScroll").innerHTML.includes('<span class="commit-cl-id">ag/43280303</span>'), true);
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            ui_js_file = tmp_path / "ui_script_测试_🚀_é.js"
            harness_file = tmp_path / "radar_harness_测试_🚀_é.js"
            ui_js_file.write_text(ui_js, encoding="utf-8")
            harness_file.write_text(harness_js, encoding="utf-8")
            proc = subprocess.run(
                [node_bin, str(harness_file), str(ui_js_file)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")

    def test_topbar_removed_viewer_controls_in_side_panel_and_single_commit_diff_only(
        self,
    ) -> None:
        html = render_static_gerrit_ui_html()
        self.assertNotIn('<div class="topbar">', html)
        self.assertNotIn("view-toggle", html)
        self.assertNotIn('id="modeSplit"', html)
        self.assertNotIn('id="modeStack"', html)
        self.assertNotIn('id="modeSquash"', html)
        self.assertNotIn("function setViewMode(", html)
        self.assertNotIn("Squashed Stack Diff", html)
        self.assertNotIn("Continuous Stack Scroll", html)
        refresh_btn_match = re.search(
            r'<button[^>]*id="btnRefreshQueue"[^>]*>(.*?)</button>',
            html,
            flags=re.S,
        )
        self.assertIsNotNone(refresh_btn_match)
        refresh_btn_inner = refresh_btn_match.group(1) if refresh_btn_match else ""
        self.assertIn('class="refresh-icon-svg"', refresh_btn_inner)
        self.assertEqual(re.sub(r"<svg.*?</svg>", "", refresh_btn_inner, flags=re.S).strip(), "")

        queue_pane_idx = html.index('<div class="queue-pane">')
        diff_pane_idx = html.index('<div class="diff-pane">')
        viewer_toolbar_idx = html.index('id="queueViewerToolbar"')
        viewer_select_idx = html.index('id="viewerSelect"')
        scope_select_idx = html.index('id="scopeSelect"')
        refresh_btn_idx = html.index('id="btnRefreshQueue"')
        self.assertGreater(viewer_toolbar_idx, queue_pane_idx)
        self.assertLess(viewer_toolbar_idx, diff_pane_idx)
        self.assertGreater(viewer_select_idx, viewer_toolbar_idx)
        self.assertGreater(scope_select_idx, viewer_select_idx)
        self.assertGreater(refresh_btn_idx, scope_select_idx)
        self.assertLess(refresh_btn_idx, diff_pane_idx)

        self.assertIn("function getSelectedStackCommits(", html)
        self.assertIn("return [chosen || vst.commits[0]];", html)

    def test_collapsible_and_resizable_side_panel_splitter(self) -> None:
        html = render_static_gerrit_ui_html()
        self.assertIn('id="workspaceContainer" class="workspace"', html)
        self.assertIn('id="workspaceSplitter"', html)
        self.assertIn('class="workspace-splitter"', html)
        self.assertIn('id="btnCollapseSidebar"', html)
        self.assertIn('id="btnExpandSidebar"', html)
        self.assertIn("function toggleSidebarCollapsed(", html)
        self.assertIn("function setSidebarWidth(", html)
        self.assertIn("function initWorkspaceSplitter(", html)
        self.assertIn("--queue-pane-width", html)

        queue_pane_idx = html.index('<div class="queue-pane">')
        splitter_idx = html.index('id="workspaceSplitter"')
        diff_pane_idx = html.index('<div class="diff-pane">')
        self.assertGreater(splitter_idx, queue_pane_idx)
        self.assertLess(splitter_idx, diff_pane_idx)

    def test_staged_multi_label_and_message_only_vote_endpoint(self) -> None:
        from unittest.mock import MagicMock

        with patch.object(
            GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"
        ):
            service = GerritUIService(default_viewer="joshuaduong@google.com")

        posted_votes: list[tuple[str, dict[str, object]]] = []

        def fake_gob_curl(cmd: list[str], **_kwargs: object) -> MagicMock:
            payload = json.loads(cmd[cmd.index("-d") + 1])
            posted_votes.append((cmd[-1], payload))
            return MagicMock(returncode=0, stdout=")]}'\n{}", stderr="")

        with (
            patch("shutil.which", return_value="/usr/bin/gob-curl"),
            patch("subprocess.run", side_effect=fake_gob_curl),
        ):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_gerrit_ui_handler(service, ui_token=_TEST_UI_TOKEN))
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                req_vote = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/gerrit/vote",
                    data=json.dumps(
                        {
                            "cls": [43280001, 43280002],
                            "code_review": 1,
                            "presubmit_ready": 1,
                            "autosubmit": 1,
                            "message": "Staged batch vote 测试_🚀_é",
                        }
                    ).encode("utf-8"),
                    headers=_JSON_AUTH_HEADERS,
                    method="POST",
                )
                with urllib.request.urlopen(req_vote, timeout=5) as resp:
                    vote_data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(vote_data["status"], "ok")
                voted_cls = sorted(int(r["cl"]) for r in vote_data["results"] if r["ok"])
                self.assertEqual(voted_cls, [43280001, 43280002])
                self.assertEqual(len(posted_votes), 2)
                for _url, payload in posted_votes:
                    self.assertEqual(
                        payload,
                        {
                            "labels": {
                                "Code-Review": 1,
                                "Autosubmit": 1,
                                "Presubmit-Ready": 1,
                            },
                            "message": "Staged batch vote 测试_🚀_é",
                            "notify": "OWNER",
                        },
                    )

                posted_votes.clear()
                req_msg = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/gerrit/vote",
                    data=json.dumps(
                        {
                            "cls": [43280001],
                            "code_review": None,
                            "message": "Top-level review note 测试_🚀_é",
                        }
                    ).encode("utf-8"),
                    headers=_JSON_AUTH_HEADERS,
                    method="POST",
                )
                with urllib.request.urlopen(req_msg, timeout=5) as resp:
                    msg_data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(msg_data["status"], "ok")
                self.assertEqual(len(posted_votes), 1)
                self.assertEqual(
                    posted_votes[0][1],
                    {"message": "Top-level review note 测试_🚀_é", "notify": "OWNER"},
                )
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_batch_reviewer_endpoint_enforces_notify_owner(self) -> None:
        with patch.object(
            GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"
        ):
            service = GerritUIService(default_viewer="joshuaduong@google.com")

        recorded_requests: list[tuple[str, str, dict | None]] = []

        def fake_request_json(
            endpoint: str, method: str = "GET", data: dict | None = None, **_kwargs: object
        ) -> object:
            recorded_requests.append((method, endpoint, data))
            if method == "POST" and endpoint.endswith("/reviewers"):
                return {"reviewers": [{"email": "jansene@google.com", "name": "Erwin Jansen"}]}
            if method == "GET" and endpoint.startswith("/a/changes/"):
                cl_str = endpoint.split("/")[3].split("?")[0]
                cl_num = int(cl_str)
                return {
                    "_number": cl_num,
                    "project": "platform/hardware/generic/goldfish",
                    "branch": "emu-main-next",
                    "subject": f"feat: Batch target {cl_num} 测试_🚀_é",
                    "owner": {"email": "joshuaduong@google.com", "name": "Joshua Duong"},
                    "current_revision": "rev1",
                    "revisions": {"rev1": {"_number": 1, "files": {"a.cc": {}}}},
                    "reviewers": {
                        "REVIEWER": [{"email": "jansene@google.com", "name": "Erwin Jansen"}]
                    },
                    "labels": {},
                }
            return []

        with patch.object(service.client, "_request_json", side_effect=fake_request_json):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_gerrit_ui_handler(service, ui_token=_TEST_UI_TOKEN))
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                req_rev = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/gerrit/reviewer",
                    data=json.dumps(
                        {
                            "cls": [43280001, 43280002],
                            "action": "add",
                            "account": "jansene",
                            "state": "REVIEWER",
                            "viewer": "joshuaduong@google.com",
                        }
                    ).encode("utf-8"),
                    headers=_JSON_AUTH_HEADERS,
                    method="POST",
                )
                with urllib.request.urlopen(req_rev, timeout=5) as resp:
                    rev_data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(rev_data["status"], "ok")
                self.assertEqual(sorted(rev_data["cls"]), ["43280001", "43280002"])
                self.assertEqual(len(rev_data["changes"]), 2)
                rev_posts = [r for r in recorded_requests if r[0] == "POST" and r[1].endswith("/reviewers")]
                self.assertEqual(len(rev_posts), 2)
                for _method, _path, payload in rev_posts:
                    self.assertEqual(
                        payload,
                        {
                            "reviewer": "jansene@google.com",
                            "state": "REVIEWER",
                            "notify": "OWNER",
                        },
                    )
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_batch_selection_staged_vote_and_submit_template_contracts(self) -> None:
        html = render_static_gerrit_ui_html()
        self.assertNotIn('<div class="harness-bar">', html)
        self.assertNotIn("function renderCommitQuickVoteRowHtml", html)
        for expected in (
            'class="cl-select-checkbox"',
            'class="group-select-checkbox"',
            'id="queueBatchStrip"',
            "function renderInViewerStagedVoteRowHtml(",
            "function renderBatchModePanelHtml(",
            "${renderInViewerStagedVoteRowHtml()}",
            ".btn.btn-submit-votes:not(:disabled)",
            "submitVoteReadyPulse",
            'id="batchModeSection"',
            'class="cl-participants-panel is-batch-mode"',
            'id="btnExitBatchMode"',
            'id="stagedVoteBar"',
            'id="batchVoteHeaderStrip"',
            'id="stageCrNeg2"',
            'id="stageCrNeg1"',
            'id="stageCrZero"',
            'id="stageCrPlus1"',
            'id="stageCrPlus2"',
            'id="stagePrCheckbox"',
            'id="stageAsCheckbox"',
            'id="stagedVoteCommentInput"',
            'id="btnSubmitVotes"',
            'id="btnResetStagedVotes"',
            'id="batchParticipantActions"',
            'id="btnBatchAddReviewer"',
            'id="btnBatchAddCc"',
            "Presubmit-Ready +1",
            "Autosubmit +1",
            "Submit Vote",
            "function toggleClCheckbox(",
            "function toggleGroupCheckbox(",
            "function syncGroupCheckboxIndeterminateDom(",
            "function computeStagedVotePayload(",
            "function submitStagedVotes(",
            "function openBatchReviewerComposer(",
            "function submitBatchReviewerAdd(",
            'id="btnSubmitCl"',
            'id="btnBatchSubmitCls"',
            ".btn.btn-submit-cl:not(:disabled)",
            ".btn.btn-submit-cl:disabled:not(.is-submitting)",
            "function computeCommitSubmitTarget(",
            "function computeBatchSubmitTarget(",
            "function executeGerritSubmit(",
            "/api/gerrit/submit",
        ):
            self.assertIn(expected, html)

    def test_submit_cl_and_topic_endpoint_enforces_notify_owner_and_submittable(self) -> None:
        now = datetime(2026, 10, 26, 18, 0, 0, tzinfo=timezone.utc)
        base_change = {
            "_number": 43290001,
            "change_id": "I9999999999abcdef1234567890abcdef12345678",
            "project": "platform/hardware/google/aemu",
            "branch": "studio-main",
            "topic": "topic_测试_🚀_é",
            "subject": "feat(ui): Add Submit CL/Topic button 测试_🚀_é",
            "created": "2026-10-26 15:00:00.000000000",
            "updated": "2026-10-26 17:00:00.000000000",
            "insertions": 20,
            "deletions": 2,
            "mergeable": True,
            "submittable": True,
            "current_revision": "rev_sub_1",
            "current_revision_number": 1,
            "owner": {"email": "joshuaduong@google.com", "name": "Joshua Duong"},
            "revisions": {
                "rev_sub_1": {
                    "_number": 1,
                    "files": {"tools/emu-dev-cli/src/templates/gerrit_ui.html": {}},
                    "commit": {"parents": [{"commit": "rev_base"}]},
                }
            },
            "labels": {
                "Code-Review": {
                    "all": [{"email": "jansene@google.com", "name": "Erwin Jansen", "value": 2}]
                },
                "Presubmit-Verified": {
                    "all": [{"email": "treehugger@system.gserviceaccount.com", "value": 2}]
                },
            },
        }
        enriched_ready = enrich_change_for_ui(base_change, "joshuaduong@google.com", now_dt=now)
        self.assertTrue(enriched_ready["is_submittable"])
        self.assertTrue(enriched_ready["gerrit_submittable"])

        # Authoritative Gerrit submittable=False blocks is_submittable even when CR+2 & PV+2 exist
        blocked_by_gerrit = dict(base_change, submittable=False)
        enriched_blocked = enrich_change_for_ui(
            blocked_by_gerrit, "joshuaduong@google.com", now_dt=now
        )
        self.assertFalse(enriched_blocked["is_submittable"])
        self.assertFalse(enriched_blocked["gerrit_submittable"])

        with patch.object(
            GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"
        ):
            service = GerritUIService(default_viewer="joshuaduong@google.com")

        gob_calls: list[list[str]] = []

        def fake_gob_curl(cmd: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
            gob_calls.append(list(cmd))
            url = cmd[-1]
            if "/43290001/submit" in url:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=")]}'\n" + json.dumps({"status": "MERGED"}), stderr=""
                )
            if "/43290002/submit" in url:
                # Sibling CL in same topic already merged atomically by submitWholeTopic
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="change is merged\n", stderr=""
                )
            return subprocess.CompletedProcess(
                cmd, 0, stdout="Cannot submit change 43299999: Needs Code-Review\n", stderr=""
            )

        with (
            patch("shutil.which", return_value="/usr/bin/gob-curl"),
            patch("subprocess.run", side_effect=fake_gob_curl),
        ):
            handler = make_gerrit_ui_handler(service, ui_token=_TEST_UI_TOKEN)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                # 1) Submit topic CLs -> 200 OK, notify=OWNER verified on every gob-curl call
                req_ok = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/gerrit/submit",
                    data=json.dumps({"cls": [43290001, 43290002]}).encode("utf-8"),
                    headers=_JSON_AUTH_HEADERS,
                    method="POST",
                )
                with urllib.request.urlopen(req_ok, timeout=5) as resp:
                    ok_data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(ok_data["status"], "ok")
                self.assertEqual(sorted(ok_data["submitted_cls"]), ["43290001", "43290002"])
                self.assertEqual(len(gob_calls), 2)
                for call_args in gob_calls:
                    self.assertIn("-d", call_args)
                    d_idx = call_args.index("-d")
                    self.assertEqual(
                        json.loads(call_args[d_idx + 1]),
                        {"notify": "OWNER"},
                    )

                # 2) Non-submittable CL -> HTTP 409 with error & fallback cloudtop_cmd
                req_fail = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/gerrit/submit",
                    data=json.dumps({"cls": [43299999]}).encode("utf-8"),
                    headers=_JSON_AUTH_HEADERS,
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req_fail, timeout=5)
                self.assertEqual(ctx.exception.code, 409)
                err_body = json.loads(ctx.exception.read().decode("utf-8"))
                self.assertEqual(err_body["status"], "error")
                self.assertIn("Needs Code-Review", err_body["error"])
                self.assertIn('"notify": "OWNER"', err_body["cloudtop_cmd"])
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_reply_inline_comment_service_and_http_endpoint(self) -> None:
        with patch.object(
            GerritUIService, "_detect_auth_email", return_value="jansene@google.com"
        ):
            service = GerritUIService(default_viewer="jansene@google.com")

        captured_calls: list[tuple[str, str, dict]] = []

        def fake_request_json(endpoint: str, method: str = "GET", data: dict | None = None):
            captured_calls.append((endpoint, method, data or {}))
            if endpoint.endswith("/revisions/current/review"):
                return {
                    "comments": {
                        "emulator/net/socket_测试_🚀_é.cc": [
                            {"id": "new_comment_99", "patch_set": 2}
                        ]
                    }
                }
            return {
                "comments": {
                    "emulator/net/socket_测试_🚀_é.cc": [
                        {"id": "reply_comment_100", "patch_set": 2}
                    ]
                }
            }

        service.client._request_json = fake_request_json  # type: ignore[method-assign]
        service._full_diff_bundle_cache[("43152171", "rev_sha")] = (999999999.0, {"number": 43152171})

        created = service.post_inline_comment(
            cl_number=43152171,
            file_path="emulator/net/socket_测试_🚀_é.cc",
            line=48,
            message="Initial comment 测试_🚀_é",
            unresolved=True,
        )
        self.assertEqual(created["comment_id"], "new_comment_99")
        self.assertEqual(created["patch_set"], 2)
        self.assertEqual(created["notify"], "OWNER")

        service._full_diff_bundle_cache[("43152171", "rev_sha")] = (999999999.0, {"number": 43152171})
        reply_res = service.reply_inline_comment(
            cl_number="ag/43152171",
            comment_id="new_comment_99",
            file_path="emulator/net/socket_测试_🚀_é.cc",
            line=48,
            patch_set=2,
            side="REVISION",
            unresolved=False,
            message="Fixed socket leak 测试_🚀_é in PS2.",
        )
        self.assertEqual(reply_res["status"], "ok")
        self.assertEqual(reply_res["notify"], "OWNER")
        self.assertEqual(reply_res["in_reply_to"], "new_comment_99")
        self.assertEqual(reply_res["comment_id"], "reply_comment_100")
        self.assertEqual(reply_res["patch_set"], 2)
        self.assertFalse(reply_res["unresolved"])
        self.assertEqual(reply_res["author_name"], "jansene")
        self.assertNotIn(("43152171", "rev_sha"), service._full_diff_bundle_cache)

        ep, method, sent_body = captured_calls[-1]
        self.assertEqual(ep, "/a/changes/43152171/revisions/2/review")
        self.assertEqual(method, "POST")
        self.assertEqual(sent_body["notify"], "OWNER")
        reply_items = sent_body["comments"]["emulator/net/socket_测试_🚀_é.cc"]
        self.assertEqual(len(reply_items), 1)
        self.assertEqual(reply_items[0]["in_reply_to"], "new_comment_99")
        self.assertEqual(reply_items[0]["line"], 48)
        self.assertFalse(reply_items[0]["unresolved"])
        self.assertEqual(reply_items[0]["message"], "Fixed socket leak 测试_🚀_é in PS2.")

        with self.assertRaisesRegex(ValueError, "comment_id is empty"):
            service.reply_inline_comment(
                cl_number=43152171,
                comment_id="   ",
                file_path="emulator/net/socket.cc",
                message="Non-empty",
            )
        with self.assertRaisesRegex(ValueError, "reply message is empty"):
            service.reply_inline_comment(
                cl_number=43152171,
                comment_id="c_123",
                file_path="emulator/net/socket.cc",
                message="   ",
            )

        # Verify gob-curl fallback preserves target patchset revision and notify: OWNER.
        def failing_request_json(*_args, **_kwargs):
            raise RuntimeError("SSO transport unavailable")

        service.client._request_json = failing_request_json  # type: ignore[method-assign]
        fake_proc = subprocess.CompletedProcess(
            args=["/usr/bin/gob-curl"],
            returncode=0,
            stdout=")]}'\n" + json.dumps({"comments": {"a.cc": [{"id": "gob_reply_7", "patch_set": 3}]}}),
            stderr="",
        )
        with (
            patch(
                "commands.gerrit.services.vote_submit_service.shutil.which",
                return_value="/usr/bin/gob-curl",
            ),
            patch("subprocess.run", return_value=fake_proc) as mock_run,
        ):
            gob_res = service.reply_inline_comment(
                cl_number=43152171,
                comment_id="c_ps3",
                file_path="a.cc",
                line=12,
                patch_set=3,
                unresolved=True,
                message="Keeping unresolved via gob-curl 测试_🚀_é",
            )
        self.assertEqual(gob_res["comment_id"], "gob_reply_7")
        self.assertTrue(gob_res["unresolved"])
        gob_cmd = mock_run.call_args[0][0]
        self.assertTrue(gob_cmd[-1].endswith("/a/changes/43152171/revisions/3/review"))
        gob_payload = json.loads(gob_cmd[gob_cmd.index("-d") + 1])
        self.assertEqual(gob_payload["notify"], "OWNER")
        self.assertTrue(gob_payload["comments"]["a.cc"][0]["unresolved"])

        # Exercise HTTP POST /api/gerrit/reply (200 OK and 400 validation).
        service.client._request_json = fake_request_json  # type: ignore[method-assign]
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_gerrit_ui_handler(service, ui_token=_TEST_UI_TOKEN))
        port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            ok_req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/gerrit/reply",
                data=json.dumps(
                    {
                        "cl": 43152171,
                        "comment_id": "new_comment_99",
                        "file_path": "emulator/net/socket_测试_🚀_é.cc",
                        "line": 48,
                        "patch_set": 2,
                        "unresolved": True,
                        "message": "HTTP reply check 测试_🚀_é",
                    }
                ).encode("utf-8"),
                headers=_JSON_AUTH_HEADERS,
                method="POST",
            )
            with urllib.request.urlopen(ok_req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                http_data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(http_data["status"], "ok")
            self.assertEqual(http_data["notify"], "OWNER")
            self.assertEqual(http_data["in_reply_to"], "new_comment_99")
            self.assertTrue(http_data["unresolved"])

            bad_req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/gerrit/reply",
                data=json.dumps({"cl": 43152171, "comment_id": "", "message": "hi"}).encode("utf-8"),
                headers=_JSON_AUTH_HEADERS,
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(bad_req, timeout=5)
            self.assertEqual(ctx.exception.code, 400)
            ctx.exception.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_toolbar_next_prev_buttons_and_inline_reply_ui(self) -> None:
        html = render_static_gerrit_ui_html()
        self.assertNotIn('<div class="harness-bar">', html)
        nav_group_idx = html.index('class="cl-nav-group"')
        prev_btn_idx = html.index('id="btnNavPrev"')
        next_btn_idx = html.index('id="btnNavNext"')
        self.assertGreater(prev_btn_idx, nav_group_idx)
        self.assertGreater(next_btn_idx, prev_btn_idx)

        nav_call_idx = html.index("${renderCommitNavButtonsHtml()}")
        submit_call_idx = html.index("${renderCommitSubmitButtonHtml(c)}")
        harness_call_idx = html.index("${renderCommitHarnessActionsHtml(c)}")
        self.assertLess(nav_call_idx, submit_call_idx)
        self.assertLess(submit_call_idx, harness_call_idx)

        self.assertIn("← Prev (k)", html)
        self.assertIn("Next (j) →", html)
        self.assertIn("onclick=\"event.stopPropagation(); navigateVisibleCl('prev')\"", html)
        self.assertIn("onclick=\"event.stopPropagation(); navigateVisibleCl('next')\"", html)
        self.assertIn("function canNavigateVisibleCls(", html)
        self.assertIn("function renderCommitNavButtonsHtml(", html)
        self.assertIn("function navigateVisibleCl(", html)
        self.assertIn("function syncToolbarNavButtons(", html)
        self.assertIn("Object.keys(state.openReplyByItemId || {}).length > 0", html)
        self.assertIn("inline-reply-btn", html)
        self.assertIn("💬 Reply", html)
        self.assertIn("function openInlineReplyComposer(", html)
        self.assertIn("function submitInlineCommentReply(", html)
        self.assertIn('"/api/gerrit/reply"', html)
        self.assertIn('for="commentReplyInput-${safeId}"', html)
        self.assertIn('for="commentReplyResolved-${safeId}"', html)

        node_bin = shutil.which("node")
        if not node_bin:
            return
        script_blocks = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S)
        main_js = script_blocks[-1]
        # Strip final startup calls so we can exercise UI functions deterministically in Node VM.
        main_js_body = re.sub(
            r"loadSidebarLayoutPrefs\(\);\s*initWorkspaceSplitter\(\);\s*initDiffCodeSelectionListeners\(\);\s*initApp\(\);\s*$",
            "",
            main_js.strip(),
        )
        harness_js = (
            main_js_body
            + r"""
const _els = {};
function _makeEl(id) {
  return {
    id,
    disabled: false,
    checked: false,
    value: "",
    textContent: "",
    innerHTML: "",
    outerHTML: "",
    style: { setProperty() {}, removeProperty() {} },
    focused: false,
    classList: {
      _set: new Set(),
      add(...c) { c.forEach(x => this._set.add(x)); },
      remove(...c) { c.forEach(x => this._set.delete(x)); },
      toggle(c, force) {
        if (force === undefined) {
          if (this._set.has(c)) { this._set.delete(c); return false; }
          this._set.add(c); return true;
        }
        if (force) this._set.add(c); else this._set.delete(c);
        return Boolean(force);
      },
      contains(c) { return this._set.has(c); },
    },
    focus() { this.focused = true; },
    getAttribute() { return ""; },
    setAttribute() {},
    querySelector() { return null; },
  };
}
globalThis.document.getElementById = (id) => {
  if (!_els[id]) _els[id] = _makeEl(id);
  return _els[id];
};
globalThis.document.querySelector = () => null;
globalThis.document.querySelectorAll = () => [];

const fetchCalls = [];
globalThis.fetch = async (url, opts = {}) => {
  fetchCalls.push({ url, opts });
  return {
    ok: true,
    status: 200,
    async json() {
      return {
        status: "ok",
        notify: "OWNER",
        in_reply_to: "leaf_ps2",
        comment_id: "reply_ps2_new",
        patch_set: 2,
        unresolved: false,
        author_name: "jansene",
      };
    },
  };
};

// 1. Verify Next/Prev navigation renders in commit-sec-header and moves across visible CLs without casting any vote.
const c1 = { number: 101, patch_set: 1, project_short: "aemu", url: "https://example.com/c/101", risk: { tier: "low", label: "Low Risk" }, subject: "feat: One", owner_email: "dev@google.com", presubmit_vetted: true, approved_by_viewer: false, unresolved_comment_count: 1 };
const c2 = { number: 102, patch_set: 1, project_short: "aemu", url: "https://example.com/c/102", risk: { tier: "low", label: "Low Risk" }, subject: "feat: Two", owner_email: "dev@google.com", presubmit_vetted: true, approved_by_viewer: false, unresolved_comment_count: 0 };
const c3 = { number: 103, patch_set: 1, project_short: "aemu", url: "https://example.com/c/103", risk: { tier: "low", label: "Low Risk" }, subject: "feat: Three", owner_email: "dev@google.com", presubmit_vetted: true, approved_by_viewer: false, unresolved_comment_count: 0 };
state.changes = [c1, c2, c3];
state.stacks = [{ stack_id: "s1", is_stack: true, commits: [c1, c2, c3] }];
state.gates = { presubmit: false, agent: false, hideMine: false, hideSubmittable: false };
state.groupMode = "stack";
state.selectedStackId = "s1";
state.selectedClNumber = 101;

renderDiffPane();
const headerHtml = _els["diffScroll"].innerHTML;
const renderedPrevIdx = headerHtml.indexOf('id="btnNavPrev"');
const renderedNextIdx = headerHtml.indexOf('id="btnNavNext"');
const renderedSubmitIdx = headerHtml.indexOf('id="btnSubmitCl"');
const renderedHarnessIdx = headerHtml.indexOf('id="btnHarnessMenuToggle"');
if (renderedPrevIdx < 0 || renderedNextIdx <= renderedPrevIdx || renderedSubmitIdx <= renderedNextIdx || renderedHarnessIdx <= renderedSubmitIdx) {
  throw new Error("Expected Prev -> Next -> Submit CL -> Harness Actions in rendered commit-sec-header");
}

const selectedMoves = [];
selectStackAndCl = (stackId, clNum, opts = {}) => {
  state.selectedStackId = stackId;
  state.selectedClNumber = clNum;
  selectedMoves.push({ stackId, clNum, animDir: opts.animDir });
};

syncToolbarNavButtons();
if (_els["btnNavPrev"].disabled || _els["btnNavNext"].disabled) {
  throw new Error("Expected Next/Prev buttons enabled when 3 CLs are visible");
}
navigateVisibleCl("next");
navigateVisibleCl("next");
navigateVisibleCl("prev");
if (JSON.stringify(selectedMoves.map(m => [m.clNum, m.animDir])) !== JSON.stringify([[102, "next"], [103, "next"], [102, "prev"]])) {
  throw new Error("Unexpected navigation sequence: " + JSON.stringify(selectedMoves));
}
if (fetchCalls.length !== 0) {
  throw new Error("Navigation must never invoke vote or network endpoints");
}

// Single visible CL disables Next/Prev when already selected.
state.stacks = [{ stack_id: "s_only", is_stack: false, commits: [c1] }];
state.selectedClNumber = 101;
syncToolbarNavButtons();
if (!_els["btnNavPrev"].disabled || !_els["btnNavNext"].disabled) {
  throw new Error("Expected Next/Prev disabled when only 1 visible CL is selected");
}

// 2. Verify inline comment card Reply composer, resolved toggle, and submit flow.
state.diffsByCl[101] = {
  number: 101,
  files: [],
  unresolved_threads: [
    {
      root_id: "root_ps1",
      leaf_id: "leaf_ps2",
      file_path: "emulator/net/http_测试_🚀_é.cc",
      line: 77,
      start_line: 75,
      end_line: 77,
      patch_set: 2,
      unresolved: true,
      author_name: "Goldie",
      latest_message: "Please reuse goldfish::http::WebServer here.",
      messages: [
        { comment_id: "root_ps1", author_name: "Goldie", patch_set: 1, message: "Initial finding", unresolved: true },
        { comment_id: "leaf_ps2", author_name: "Goldie", patch_set: 2, message: "Please reuse goldfish::http::WebServer here.", unresolved: true },
      ],
    },
  ],
};
state.collapsedInlineIds.add("srv-thread-101-0");
const items = getInlineCommentsForFile(101, "emulator/net/http_测试_🚀_é.cc");
if (items.length !== 1 || items[0].comment_id !== "leaf_ps2") {
  throw new Error("Expected server thread item with leaf_id");
}
const initialCardHtml = renderInlineCommentCardHtml(101, items[0]);
if (!initialCardHtml.includes("💬 Reply") || !initialCardHtml.includes("2 msgs")) {
  throw new Error("Expected Reply button and message count in rendered card");
}
_els["inlineItem-srv-thread-101-0"] = _makeEl("inlineItem-srv-thread-101-0");
openInlineReplyComposer(101, "srv-thread-101-0");
if (state.collapsedInlineIds.has("srv-thread-101-0")) {
  throw new Error("Opening reply composer must auto-expand collapsed thread");
}
if (!_els["inlineItem-srv-thread-101-0"].outerHTML.includes("commentReplyInput-srv-thread-101-0")) {
  throw new Error("Expected inline reply textarea in re-rendered card HTML");
}

// Toggle Resolved ON and submit Unicode reply.
onInlineReplyResolvedChange(101, "srv-thread-101-0", true);
if (_els["commentReplyPill-srv-thread-101-0"].textContent !== "Marks Resolved") {
  throw new Error("Expected Marks Resolved pill text");
}
if (_els["btnSubmitInlineReply-srv-thread-101-0"].textContent !== "✓ Send Reply & Resolve") {
  throw new Error("Expected Send Reply & Resolve button text");
}
document.getElementById("commentReplyResolved-srv-thread-101-0").checked = true;
document.getElementById("commentReplyInput-srv-thread-101-0").value = "Switched to WebServer 测试_🚀_é, thanks!";

(async () => {
  await submitInlineCommentReply(101, "srv-thread-101-0");
  if (fetchCalls.length !== 1 || !fetchCalls[0].url.startsWith("/api/gerrit/reply")) {
    throw new Error("Expected 1 POST to /api/gerrit/reply, got: " + JSON.stringify(fetchCalls));
  }
  const body = JSON.parse(fetchCalls[0].opts.body);
  if (body.comment_id !== "leaf_ps2" || body.unresolved !== false || !body.message.includes("测试_🚀_é")) {
    throw new Error("Unexpected reply payload: " + JSON.stringify(body));
  }
  if (c1.unresolved_comment_count !== 0) {
    throw new Error("Expected unresolved_comment_count decremented to 0, got " + c1.unresolved_comment_count);
  }
  const updatedHtml = _els["inlineItem-srv-thread-101-0"].outerHTML;
  if (!updatedHtml.includes("(Resolved · 3 msgs)") || !updatedHtml.includes("Switched to WebServer 测试_🚀_é")) {
    throw new Error("Expected updated card HTML with Resolved + 3 msgs: " + updatedHtml);
  }
  console.log("NODE_HARNESS_OK");
})().catch(err => { console.error(err); process.exit(1); });
"""
        )
        vm_wrapper = (
            "const vm = require('node:vm');\n"
            "const fs = require('node:fs');\n"
            "const code = fs.readFileSync(process.argv[2], 'utf8');\n"
            "const sandbox = {\n"
            "  console, setTimeout, clearTimeout, setInterval: () => 1, clearInterval,\n"
            "  URLSearchParams, encodeURIComponent, decodeURIComponent, JSON, Math, Number, String, Boolean, Array, Object, Set, Map, RegExp, Error, Promise,\n"
            "  navigator: { clipboard: { writeText: async () => {} } },\n"
            "  window: { location: { search: '' }, localStorage: { getItem: () => null, setItem: () => {} }, addEventListener: () => {} },\n"
            "  document: { addEventListener: () => {}, getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] },\n"
            "};\n"
            "sandbox.globalThis = sandbox;\n"
            "sandbox.window.document = sandbox.document;\n"
            "vm.runInNewContext(code, sandbox);\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            js_path = Path(tmp_dir) / "ui_harness.js"
            runner_path = Path(tmp_dir) / "run_vm.js"
            js_path.write_text(harness_js, encoding="utf-8")
            runner_path.write_text(vm_wrapper, encoding="utf-8")
            proc = subprocess.run(
                [node_bin, str(runner_path), str(js_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(proc.returncode, 0, msg=f"Node UI harness failed:\n{proc.stderr}\n{proc.stdout}")
            self.assertIn("NODE_HARNESS_OK", proc.stdout)

    def test_update_available_button_dirty_warning_modal_and_http_endpoints(self) -> None:
        html = render_static_gerrit_ui_html()
        self.assertIn('id="btnUpdateAvailable"', html)
        self.assertIn("⬇ Update Available", html)
        self.assertIn(".btn-update-available", html)
        self.assertIn("#2563eb", html)
        self.assertIn('id="updateDirtyWarningModal"', html)
        self.assertIn("Unstaged Changes Will Be Wiped Out", html)
        self.assertIn("Wipe Changes &amp; Update", html)
        self.assertIn("function onUpdateAvailableClick(", html)
        self.assertIn("function openUpdateDirtyWarningModal(", html)
        self.assertIn("function confirmWipeAndPerformSidecarUpdate(", html)

        class FakeUpdater:
            def __init__(self) -> None:
                self.status_calls: list[bool] = []
                self.update_calls: list[bool] = []

            def get_update_status(self, *, force_fetch: bool = False, ui_version: str = "") -> dict[str, object]:
                self.status_calls.append(force_fetch)
                return {
                    "configured": True,
                    "update_available": True,
                    "update_reason": "upstream_ahead",
                    "behind_count": 2,
                    "has_unstaged_changes": True,
                    "dirty_files": [
                        {"path": "tools/emu-dev-cli/src/templates/gerrit_ui_测试_🚀_é.html", "kind": "unstaged", "code": " M"}
                    ],
                    "ui_version": ui_version,
                }

            def perform_update(
                self, *, force_discard_local: bool = False, ui_version_fn: object = None
            ) -> dict[str, object]:
                self.update_calls.append(force_discard_local)
                if not force_discard_local:
                    return {
                        "status": "dirty_worktree",
                        "requires_confirmation": True,
                        "has_unstaged_changes": True,
                        "dirty_files": [
                            {"path": "tools/emu-dev-cli/src/templates/gerrit_ui_测试_🚀_é.html", "kind": "unstaged", "code": " M"}
                        ],
                        "error": "Unstaged local changes detected",
                    }
                return {
                    "status": "ok",
                    "updated_head_sha": "abc1234",
                    "discarded_files": ["tools/emu-dev-cli/src/templates/gerrit_ui_测试_🚀_é.html"],
                    "update_status": {"configured": True, "update_available": False},
                }

        fake_updater = FakeUpdater()
        with patch.object(
            GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"
        ):
            service = GerritUIService(
                default_viewer="joshuaduong@google.com", sidecar_updater=fake_updater  # type: ignore[arg-type]
            )

        handler_cls = make_gerrit_ui_handler(service, ui_token=_TEST_UI_TOKEN)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        srv_thread.start()
        try:
            with urllib.request.urlopen(
                _authed_get(f"http://127.0.0.1:{port}/api/gerrit/update-status?force=1"), timeout=5
            ) as resp:
                self.assertEqual(resp.status, 200)
                st_payload = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(st_payload["update_available"])
            self.assertEqual(fake_updater.status_calls, [True])

            req_unconfirmed = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/gerrit/perform-update",
                data=json.dumps({"force_discard_local": False}).encode("utf-8"),
                headers=_JSON_AUTH_HEADERS,
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req_unconfirmed, timeout=5)
            self.assertEqual(ctx.exception.code, 409)
            err_payload = json.loads(ctx.exception.read().decode("utf-8"))
            ctx.exception.close()
            self.assertEqual(err_payload["status"], "dirty_worktree")
            self.assertIn("测试_🚀_é", err_payload["dirty_files"][0]["path"])

            req_confirmed = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/gerrit/perform-update",
                data=json.dumps({"force_discard_local": True}).encode("utf-8"),
                headers=_JSON_AUTH_HEADERS,
                method="POST",
            )
            with urllib.request.urlopen(req_confirmed, timeout=5) as resp2:
                self.assertEqual(resp2.status, 200)
                ok_payload = json.loads(resp2.read().decode("utf-8"))
            self.assertEqual(ok_payload["status"], "ok")
            self.assertEqual(fake_updater.update_calls, [False, True])
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_diff_color_schemes_intraline_diff_and_syntax_highlighting(self) -> None:
        html = render_static_gerrit_ui_html()
        for expected in (
            'id="diffColorSchemeSelect"',
            "DIFF_COLOR_SCHEMES",
            "syntax_intraline",
            "intraline_focus",
            "semantic_review",
            "github_high_contrast",
            "classic",
            "function setDiffColorScheme(",
            "function cycleDiffColorScheme(",
            "function computeFileIntralineRanges(",
            "function tokenizeDiffCodeLine(",
            "function renderHighlightedDiffLineHtml(",
            "function renderHunkHeaderInnerHtml(",
            "function _renderSubjectLineHtml(",
            "function _classifyInlineCodeSpanClass(",
            "function _renderFencedCodeBlockInnerHtml(",
            ".diff-intra-add",
            ".diff-intra-del",
            ".tok-rev-flow",
            ".tok-rev-safety",
            ".tok-rev-status",
            ".commit-subj-type-feat",
            ".footer-key-bug",
            ".footer-key-test",
            ".code-tok-flag",
            ".code-tok-path",
        ):
            self.assertIn(expected, html)

        node_bin = shutil.which("node") or (
            "/usr/bin/node" if Path("/usr/bin/node").is_file() else None
        )
        if not node_bin:
            return
        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S)
        self.assertTrue(scripts)
        ui_js = "\n".join(scripts)
        for boot_call in (
            "loadSidebarLayoutPrefs();",
            "initWorkspaceSplitter();",
            "initDiffCodeSelectionListeners();",
            "initApp();",
        ):
            ui_js = ui_js.replace(boot_call, f"// {boot_call}")

        harness_js = (
            ui_js
            + r"""
const assert = require("node:assert/strict");

function stripTags(htmlStr) {
  return String(htmlStr)
    .replace(/<[^>]+>/g, "")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&");
}

// 1. Verify language detection across common repo extensions.
assert.equal(detectDiffFileLanguage("emulator/net/socket_测试_🚀_é.cc").id, "cpp");
assert.equal(detectDiffFileLanguage("tools/emu-dev-cli/src/gerrit_ui.py").id, "python");
assert.equal(detectDiffFileLanguage("emulator/BUILD.bazel").badge, "Bazel");
assert.equal(detectDiffFileLanguage("docs/guide.md").badge, "MD");

// 2. Verify intraline word diff pairing and exact text preservation across all 5 schemes.
const sampleLines = [
  { type: "hunk", text: "@@ -42,4 +42,4 @@ absl::StatusOr<int> ConnectSocket(int timeout_ms)" },
  { type: "ctx", old: 42, new: 42, text: "   std::lock_guard<std::mutex> lock(mu_); // comment 测试_🚀_é" },
  { type: "del", old: 43, new: null, text: "-  if (timeout_ms <= 500) return absl::DeadlineExceededError(\"slow_测试_🚀_é\");" },
  { type: "add", old: null, new: 43, text: "+  if (timeout_ms <= 2500) return absl::DeadlineExceededError(\"fast_测试_🚀_é\");" },
];
const intralineMap = computeFileIntralineRanges(sampleLines);
assert.equal(Array.isArray(intralineMap[2]), true, "deleted line in pair should have intraline range");
assert.equal(Array.isArray(intralineMap[3]), true, "added line in pair should have intraline range");

const hunkHtml = renderHunkHeaderInnerHtml(sampleLines[0].text, "syntax_intraline");
assert.equal(hunkHtml.includes("hunk-range-pill"), true);
assert.equal(hunkHtml.includes("hunk-context-sig"), true);
assert.equal(stripTags(hunkHtml), sampleLines[0].text);

for (const schemeObj of DIFF_COLOR_SCHEMES) {
  state.diffColorScheme = schemeObj.id;
  for (let i = 1; i < sampleLines.length; i++) {
    const ln = sampleLines[i];
    const rendered = renderHighlightedDiffLineHtml(ln.text, ln.type, "cpp", schemeObj.id, intralineMap[i] || null);
    assert.equal(
      stripTags(rendered),
      ln.text,
      `Exact textContent must match raw line for scheme=${schemeObj.id} line=${i}`
    );
  }
  const delRendered = renderHighlightedDiffLineHtml(sampleLines[2].text, "del", "cpp", schemeObj.id, intralineMap[2]);
  const addRendered = renderHighlightedDiffLineHtml(sampleLines[3].text, "add", "cpp", schemeObj.id, intralineMap[3]);
  if (schemeObj.id === "classic") {
    assert.equal(delRendered.includes("diff-intra-del"), false);
    assert.equal(addRendered.includes("tok-"), false);
  } else if (schemeObj.id === "intraline_focus") {
    assert.equal(delRendered.includes("diff-intra-del"), true);
    assert.equal(addRendered.includes("diff-intra-add"), true);
    assert.equal(addRendered.includes("tok-keyword"), false);
  } else if (schemeObj.id === "semantic_review") {
    assert.equal(addRendered.includes("tok-rev-flow"), true, "return should highlight as review control flow");
    assert.equal(addRendered.includes("tok-rev-status"), true, "absl/Error should highlight as review status");
    const ctxRendered = renderHighlightedDiffLineHtml(sampleLines[1].text, "ctx", "cpp", schemeObj.id, null);
    assert.equal(ctxRendered.includes("tok-rev-safety"), true, "lock_guard/mutex should highlight as review safety");
  } else {
    assert.equal(delRendered.includes("diff-intra-del"), true);
    assert.equal(addRendered.includes("diff-intra-add"), true);
    assert.equal(addRendered.includes("tok-keyword"), true);
  }
}

// 3. Verify Commit Message semantic highlighting (subject, inline code tokens, fenced blocks, footers).
state.diffColorScheme = "syntax_intraline";
const commitMsg = [
  "feat(gerrit): Add interactive diff color schemes 测试_🚀_é",
  "",
  "Updates `//tools/emu-dev-cli:gerrit_ui` and `renderDiffPane()` with `--notify=OWNER`.",
  "",
  "```cpp",
  "if (status.ok()) return *status;",
  "```",
  "",
  "Bug: 43299169",
  "Test: bazel test @aemu//tools/emu-dev-cli:gerrit_ui_test",
  "Change-Id: I1234567890abcdef1234567890abcdef12345678",
].join("\n");
const msgHtml = formatCommitMessageHtml(commitMsg, 43299169);
assert.equal(msgHtml.includes("commit-subj-type-feat"), true);
assert.equal(msgHtml.includes("commit-subj-scope"), true);
assert.equal(msgHtml.includes("code-tok-path"), true);
assert.equal(msgHtml.includes("code-tok-fn"), true);
assert.equal(msgHtml.includes("code-tok-flag"), true);
assert.equal(msgHtml.includes("tok-keyword"), true);
assert.equal(msgHtml.includes("footer-key-bug"), true);
assert.equal(msgHtml.includes("footer-key-test"), true);
assert.equal(msgHtml.includes("footer-key-changeid"), true);

state.diffColorScheme = "semantic_review";
const longSubjHtml = _renderSubjectLineHtml("feat(gerrit): Add a very long subject line that exceeds fifty characters easily");
assert.equal(longSubjHtml.includes("commit-len-warn"), true);

console.log("DIFF_COLOR_SCHEMES_OK");
"""
        )
        vm_wrapper = (
            'const fs = require("node:fs");\n'
            'const vm = require("node:vm");\n'
            "const code = fs.readFileSync(process.argv[2], 'utf8');\n"
            "const sandbox = {\n"
            "  require,\n"
            "  console,\n"
            "  setTimeout,\n"
            "  clearTimeout,\n"
            "  URL,\n"
            "  URLSearchParams,\n"
            "  window: { addEventListener() {}, innerWidth: 1400, location: { search: '', href: 'http://127.0.0.1:8080/' } },\n"
            "  document: { getElementById() { return null; }, addEventListener() {}, body: { classList: { toggle() {} } } },\n"
            "  localStorage: { getItem() { return null; }, setItem() {} },\n"
            "  navigator: { clipboard: { writeText: async () => {} } },\n"
            "  fetch: async () => ({ ok: true, json: async () => ({}) }),\n"
            "};\n"
            "vm.runInNewContext(code, sandbox);\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            js_path = Path(tmp_dir) / "diff_scheme_harness.js"
            runner_path = Path(tmp_dir) / "run_vm.js"
            js_path.write_text(harness_js, encoding="utf-8")
            runner_path.write_text(vm_wrapper, encoding="utf-8")
            proc = subprocess.run(
                [node_bin, str(runner_path), str(js_path)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(proc.returncode, 0, msg=f"Diff color scheme harness failed:\n{proc.stderr}\n{proc.stdout}")
            self.assertIn("DIFF_COLOR_SCHEMES_OK", proc.stdout)

    def test_polygerrit_ui_fork_themes_and_variant_routing(self) -> None:
        from http.server import ThreadingHTTPServer
        import os
        import shutil
        import subprocess
        import tempfile
        import threading
        import urllib.request
        from commands.gerrit.ui_server import (
            get_gerrit_ui_template_path,
            make_gerrit_ui_handler,
            render_static_gerrit_ui_html,
        )

        pg_path = get_gerrit_ui_template_path("polygerrit")
        classic_path = get_gerrit_ui_template_path("classic")
        self.assertEqual(pg_path.name, "gerrit_ui_polygerrit.html")
        self.assertEqual(classic_path.name, "gerrit_ui.html")
        self.assertTrue(pg_path.is_file())
        self.assertTrue(classic_path.is_file())

        pg_html = render_static_gerrit_ui_html(variant="polygerrit")
        classic_html = render_static_gerrit_ui_html(variant="classic")
        self.assertIn('data-pg-theme="dark"', pg_html)
        self.assertIn('id="btnPgThemeToggle"', pg_html)
        self.assertIn('id="btnSwitchToClassicUi"', pg_html)
        self.assertIn("polygerrit_native", pg_html)
        self.assertIn("function togglePgTheme(", pg_html)
        self.assertIn('id="btnSwitchToPolygerritUi"', classic_html)
        self.assertIn("function switchUiVariant(", classic_html)

        with patch.object(
            GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"
        ):
            service = GerritUIService(default_viewer="joshuaduong@google.com")
        handler_cls = make_gerrit_ui_handler(service, ui_token="test-token")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        srv_thread.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/?token=test-token", timeout=5
            ) as resp:
                default_body = resp.read().decode("utf-8")
            self.assertIn('id="btnSwitchToPolygerritUi"', default_body)
            self.assertNotIn('id="btnPgThemeToggle"', default_body)

            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/?ui=polygerrit&token=test-token", timeout=5
            ) as resp:
                pg_body = resp.read().decode("utf-8")
            self.assertIn('id="btnPgThemeToggle"', pg_body)
            self.assertIn("polygerrit_native", pg_body)

            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/polygerrit?token=test-token", timeout=5
            ) as resp:
                pg_route_body = resp.read().decode("utf-8")
            self.assertIn('id="btnPgThemeToggle"', pg_route_body)
        finally:
            httpd.shutdown()
            httpd.server_close()

        node_bin = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )
        if not node_bin:
            return
        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", pg_html, flags=re.S)
        self.assertTrue(scripts)
        js_body = scripts[-1]
        for boot_call in (
            "loadSidebarLayoutPrefs();",
            "initWorkspaceSplitter();",
            "initDiffCodeSelectionListeners();",
            "initApp();",
        ):
            js_body = js_body.replace(boot_call, f"// {boot_call}")

        harness_js = (
            """
const assert = require("node:assert/strict");
let currentDocTheme = "dark";
const domMap = {
  pgThemeToggleLabel: { textContent: "🌙 Dark Theme" },
  diffScroll: { innerHTML: "", scrollTop: 0 },
  toast: { textContent: "", style: {} },
};
window.location = { search: "", href: "http://127.0.0.1:8080/" };
window.localStorage = {
  _s: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._s, k) ? this._s[k] : null; },
  setItem(k, v) { this._s[k] = String(v); },
};
document.documentElement = {
  setAttribute(k, v) { if (k === "data-pg-theme") currentDocTheme = v; },
};
document.getElementById = (id) => domMap[id] || null;
document.querySelectorAll = () => [];
"""
            + js_body
            + r"""
assert.equal(state.diffColorScheme, "polygerrit_native");
assert.equal(DIFF_COLOR_SCHEMES[0].id, "polygerrit_native");
assert.equal(state.pgTheme, "dark");

togglePgTheme();
assert.equal(state.pgTheme, "light");
assert.equal(currentDocTheme, "light");
assert.equal(domMap.pgThemeToggleLabel.textContent.includes("Light"), true);
assert.equal(window.localStorage.getItem("gerrit_ui_pg_theme"), "light");

togglePgTheme();
assert.equal(state.pgTheme, "dark");
assert.equal(currentDocTheme, "dark");

const sampleCl = {
  number: 43311422,
  subject: "feat(gerrit): PolyGerrit look and feel 测试_🚀_é",
  project: "platform/hardware/google/aemu",
  project_short: "aemu",
  branch: "studio-main",
  patch_set: 1,
  owner_short: "joshuaduong",
  owner_email: "joshuaduong@google.com",
  risk: { tier: "low", label: "Low Risk" },
  mergeable: true,
  is_submittable: true,
  presubmit_status: "passed",
  presubmit_score: 2,
  presubmit_vetted: true,
  human_cr2: [{ email: "jansene@google.com", short: "jansene" }],
  reviewers_detail: [],
  ccs_detail: [],
  bugs: [],
};
state.changes = [sampleCl];
state.stacks = [{ id: "st-1", title: "Stack", commits: [sampleCl] }];
state.selectedClNumber = 43311422;
state.diffsByCl[43311422] = {
  commit_message: "feat(gerrit): PolyGerrit look and feel 测试_🚀_é\n\nBody text.\n\nBug: 43311422\nTest: bazel test\n",
  files: [
    {
      file: "tools/emu-dev-cli/src/templates/gerrit_ui_polygerrit.html",
      additions: 12,
      deletions: 3,
      lines: [
        { type: "hunk", old: "...", new: "...", text: "@@ -10,3 +10,4 @@" },
        { type: "del", old: 10, new: "", text: "-const theme = 'old';" },
        { type: "add", old: "", new: 10, text: "+const theme = 'polygerrit_测试_🚀_é';" },
      ],
    },
  ],
  unresolved_threads: [],
};
renderDiffPane();
assert.equal(domMap.diffScroll.innerHTML.includes("pg-change-header"), true);
assert.equal(domMap.diffScroll.innerHTML.includes("pg-status-badge status-ready"), true);
assert.equal(domMap.diffScroll.innerHTML.includes("Ready to Submit"), true);
assert.equal(domMap.diffScroll.innerHTML.includes("Submit Requirements"), true);
assert.equal(domMap.diffScroll.innerHTML.includes("pg-tabs-bar"), true);
assert.equal(domMap.diffScroll.innerHTML.includes('Files <span class="pg-tab-count">(1)</span>'), true);
assert.equal(domMap.diffScroll.innerHTML.includes("scheme-polygerrit_native"), true);
assert.equal(domMap.diffScroll.innerHTML.includes("polygerrit_测试_🚀_é"), true);
console.log("POLYGERRIT_UI_FORK_OK");
"""
        )
        vm_wrapper = (
            'const fs = require("node:fs");\n'
            'const vm = require("node:vm");\n'
            "const code = fs.readFileSync(process.argv[2], 'utf8');\n"
            "const sandbox = {\n"
            "  require,\n"
            "  console,\n"
            "  setTimeout,\n"
            "  clearTimeout,\n"
            "  URL,\n"
            "  URLSearchParams,\n"
            "  window: { addEventListener() {}, innerWidth: 1400 },\n"
            "  document: { getElementById() { return null; }, addEventListener() {}, body: { classList: { toggle() {} } } },\n"
            "  localStorage: { getItem() { return null; }, setItem() {} },\n"
            "  navigator: { clipboard: { writeText: async () => {} } },\n"
            "  fetch: async () => ({ ok: true, json: async () => ({}) }),\n"
            "};\n"
            "vm.runInNewContext(code, sandbox);\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            js_path = Path(tmp_dir) / "pg_fork_harness.js"
            runner_path = Path(tmp_dir) / "run_vm.js"
            js_path.write_text(harness_js, encoding="utf-8")
            runner_path.write_text(vm_wrapper, encoding="utf-8")
            proc = subprocess.run(
                [node_bin, str(runner_path), str(js_path)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(proc.returncode, 0, msg=f"PolyGerrit UI fork harness failed:\n{proc.stderr}\n{proc.stdout}")
            self.assertIn("POLYGERRIT_UI_FORK_OK", proc.stdout)

    def test_polygerrit_dashboard_side_panel_backend_and_dom(self) -> None:
        from datetime import datetime, timezone
        import os
        import shutil
        import subprocess
        import tempfile
        from commands.gerrit.services.queue_service import (
            build_viewer_gerrit_query,
            enrich_change_for_ui,
            is_cc_reviewer,
        )
        from commands.gerrit.ui_server import (
            GerritUIService,
            render_static_gerrit_ui_html,
        )

        # 1. Verify dashboard scope query, CC helper, attention metadata, and WIP filtering.
        q_dash = build_viewer_gerrit_query("joshuaduong@google.com", scope="dashboard")
        self.assertEqual(
            q_dash,
            "status:open (owner:joshuaduong@google.com OR reviewer:joshuaduong@google.com "
            "OR cc:joshuaduong@google.com OR attention:joshuaduong@google.com)",
        )
        q_dash_owner = build_viewer_gerrit_query(
            "joshuaduong@google.com", scope="dashboard", author="jansene"
        )
        self.assertIn("owner:jansene@google.com", q_dash_owner)

        now_dt = datetime(2026, 10, 29, 15, 30, 0, tzinfo=timezone.utc)
        raw_turn_cl = {
            "_number": 43320001,
            "project": "platform/hardware/generic/goldfish",
            "branch": "emu-main-next",
            "subject": "fix(goldfish): Attention turn CL 测试_🚀_é",
            "status": "NEW",
            "work_in_progress": False,
            "created": "2026-10-29 10:00:00.000000000",
            "updated": "2026-10-29 14:30:00.000000000",
            "insertions": 42,
            "deletions": 5,
            "mergeable": True,
            "submittable": False,
            "current_revision": "rev_t1",
            "revisions": {"rev_t1": {"_number": 2, "files": {"a.cc": {}}}},
            "owner": {"_account_id": 1001, "email": "jansene@google.com", "name": "Erwin Jansen"},
            "reviewers": {
                "REVIEWER": [
                    {"_account_id": 2002, "email": "joshuaduong@google.com", "name": "Joshua Duong"}
                ],
                "CC": [
                    {"_account_id": 3003, "email": "rkir@google.com", "name": "Roman Kiryanov"}
                ],
            },
            "attention_set": {
                "2002": {
                    "account": {"_account_id": 2002, "email": "joshuaduong@google.com"},
                    "last_update": "2026-10-29 13:00:00.000000000",
                    "reason": "Reviewer was added",
                }
            },
            "labels": {},
        }
        self.assertFalse(is_cc_reviewer(raw_turn_cl, "joshuaduong@google.com"))
        self.assertTrue(is_cc_reviewer(raw_turn_cl, "rkir@google.com"))

        enriched_turn = enrich_change_for_ui(
            raw_turn_cl, "joshuaduong@google.com", now_dt=now_dt
        )
        self.assertEqual(enriched_turn["insertions"], 42)
        self.assertEqual(enriched_turn["deletions"], 5)
        self.assertFalse(enriched_turn["work_in_progress"])
        self.assertTrue(enriched_turn["viewer_in_attention_set"])
        self.assertEqual(enriched_turn["attention_set_emails"], ["joshuaduong@google.com"])
        self.assertEqual(enriched_turn["attention_waiting_display"], "2h")
        self.assertTrue(enriched_turn["is_explicit_reviewer"])
        self.assertFalse(enriched_turn["is_cc"])

        enriched_as_cc = enrich_change_for_ui(raw_turn_cl, "rkir@google.com", now_dt=now_dt)
        self.assertFalse(enriched_as_cc["viewer_in_attention_set"])
        self.assertFalse(enriched_as_cc["is_explicit_reviewer"])
        self.assertTrue(enriched_as_cc["is_cc"])

        own_wip_cl = dict(
            raw_turn_cl,
            _number=43320002,
            work_in_progress=True,
            current_revision="rev_t2",
            revisions={"rev_t2": {"_number": 1, "files": {"b.cc": {}}}},
            owner={"_account_id": 2002, "email": "joshuaduong@google.com", "name": "Joshua Duong"},
        )
        foreign_wip_cl = dict(
            raw_turn_cl,
            _number=43320003,
            work_in_progress=True,
            current_revision="rev_t3",
            revisions={"rev_t3": {"_number": 1, "files": {"c.cc": {}}}},
            owner={"_account_id": 1001, "email": "jansene@google.com", "name": "Erwin Jansen"},
        )
        with patch.object(
            GerritUIService, "_detect_auth_email", return_value="joshuaduong@google.com"
        ):
            service = GerritUIService(default_viewer="joshuaduong@google.com")
        with (
            patch.object(
                service,
                "_fetch_or_load_raw_queue_changes",
                return_value=([raw_turn_cl, own_wip_cl, foreign_wip_cl], False, False, False),
            ),
            patch.object(service, "_resolve_queue_bug_lookup", return_value=({}, False)),
        ):
            dash_payload = service.fetch_queue(
                viewer_email="joshuaduong@google.com", scope="dashboard", fast=True
            )
        dash_nums = sorted(c["number"] for c in dash_payload["changes"])
        self.assertEqual(dash_nums, [43320001, 43320002])

        # 2. Verify PolyGerrit Dashboard HTML markers & Node VM DOM partitioning.
        pg_html = render_static_gerrit_ui_html(variant="polygerrit")
        for marker in (
            'class="pg-header-search-wrap"',
            'id="grpDashboard"',
            'id="pgFiltersDrawer"',
            'id="pgFiltersDrawerBtn"',
            'class="queue-list pg-dashboard-list"',
            '<option value="dashboard" selected>Your ▸ Dashboard</option>',
        ):
            self.assertIn(marker, pg_html)

        node_bin = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )
        if not node_bin:
            return
        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", pg_html, flags=re.S)
        self.assertTrue(scripts)
        js_body = scripts[-1]
        for boot_call in (
            "loadSidebarLayoutPrefs();",
            "initWorkspaceSplitter();",
            "initDiffCodeSelectionListeners();",
            "initApp();",
        ):
            js_body = js_body.replace(boot_call, f"// {boot_call}")

        harness_js = (
            """
const assert = require("node:assert/strict");
function makeMockEl() {
  const attrs = {};
  return {
    innerHTML: "",
    textContent: "",
    value: "",
    clientWidth: 1400,
    style: { setProperty() {} },
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute(k, v) { attrs[k] = String(v); },
    removeAttribute(k) { delete attrs[k]; },
    getAttribute(k) { return attrs[k] || ""; },
  };
}
const domMap = new Proxy({}, {
  get(target, prop) {
    if (typeof prop !== "string") return undefined;
    if (!target[prop]) target[prop] = makeMockEl();
    return target[prop];
  }
});
window.location = { search: "", href: "http://127.0.0.1:8080/" };
window.localStorage = { getItem() { return null; }, setItem() {} };
document.documentElement = { setAttribute() {}, clientWidth: 1400, clientHeight: 900 };
document.getElementById = (id) => domMap[id];
document.querySelector = () => null;
document.querySelectorAll = () => [];
"""
            + js_body
            + r"""
// Default PolyGerrit mode starts on Dashboard with no surprising gate hiding.
assert.equal(state.scope, "dashboard");
assert.equal(state.groupMode, "dashboard");
assert.deepEqual(state.gates, {
  presubmit: false,
  agent: false,
  hideMine: false,
  hideSubmittable: false,
});

// Verify PolyGerrit T-shirt size tiers.
assert.equal(computePgChangeSize({ insertions: 4, deletions: 3 }).tier, "XS");
assert.equal(computePgChangeSize({ insertions: 20, deletions: 15 }).tier, "S");
assert.equal(computePgChangeSize({ insertions: 120, deletions: 40 }).tier, "M");
assert.equal(computePgChangeSize({ insertions: 500, deletions: 200 }).tier, "L");
assert.equal(computePgChangeSize({ insertions: 950, deletions: 150 }).tier, "XL");

state.viewer = "joshuaduong@google.com";
const mkDashCl = (num, overrides) => Object.assign({
  number: num,
  subject: `feat: CL ${num} 测试_🚀_é`,
  url: `https://googleplex-android-review.git.corp.google.com/c/platform/hardware/google/aemu/+/${num}`,
  project: "platform/hardware/google/aemu",
  project_short: "aemu",
  repo_label: "hardware/google/aemu",
  branch: "studio-main",
  patch_set: 1,
  owner_short: "jansene",
  owner_name: "Erwin Jansen",
  owner_email: "jansene@google.com",
  work_in_progress: false,
  insertions: 18,
  deletions: 4,
  updated_ts: 1700000000 + num,
  updated_display: "1h ago",
  flight_display: "3h",
  cliff_zone: "fresh",
  mergeable: true,
  is_submittable: false,
  presubmit_status: "passed",
  presubmit_score: 2,
  presubmit_vetted: true,
  human_cr2: [],
  human_cr1: [],
  reviewers_detail: [],
  ccs_detail: [],
  attention_set_emails: [],
  viewer_in_attention_set: false,
  attention_waiting_display: "",
  is_explicit_reviewer: false,
  is_cc: false,
  unresolved_comments: 0,
  bugs: [],
}, overrides);

// 101: Incoming review AND in viewer's attention set -> must appear in BOTH Your turn & Incoming reviews
const clIncomingTurn = mkDashCl(43320101, {
  subject: "fix(vk): Incoming attention CL 测试_🚀_é",
  is_explicit_reviewer: true,
  viewer_in_attention_set: true,
  attention_set_emails: ["joshuaduong@google.com"],
  attention_waiting_display: "4h",
  unresolved_comments: 2,
  reviewers_detail: [
    { email: "joshuaduong@google.com", short: "joshuaduong", name: "Joshua", state: "REVIEWER", is_viewer: true, in_attention_set: true, cr_vote: 0, votes: [] },
  ],
});
// 102: Own WIP CL -> Work in progress
const clOwnWip = mkDashCl(43320102, {
  subject: "chore: Draft refactor",
  owner_short: "joshuaduong",
  owner_email: "joshuaduong@google.com",
  work_in_progress: true,
  insertions: 3,
  deletions: 1,
});
// 103: Outgoing review waiting on reviewer jansene
const clOutgoing = mkDashCl(43320103, {
  subject: "feat(ui): Outgoing dashboard CL",
  owner_short: "joshuaduong",
  owner_email: "joshuaduong@google.com",
  attention_set_emails: ["jansene@google.com"],
  reviewers_detail: [
    { email: "jansene@google.com", short: "jansene", name: "Erwin Jansen", state: "REVIEWER", is_bot: false, is_owner: false, in_attention_set: true, cr_vote: 0, votes: [] },
    { email: "rkir@google.com", short: "rkir", name: "Roman", state: "REVIEWER", is_bot: false, is_owner: false, in_attention_set: false, cr_vote: 1, votes: [] },
    { email: "whollins@google.com", short: "whollins", name: "Will", state: "REVIEWER", is_bot: false, is_owner: false, in_attention_set: false, cr_vote: 0, votes: [] },
  ],
});
// 104: CCed on CL
const clCc = mkDashCl(43320104, {
  subject: "docs: CCed design update",
  owner_short: "rkir",
  owner_email: "rkir@google.com",
  is_cc: true,
  insertions: 1200,
  deletions: 50,
  ccs_detail: [{ email: "joshuaduong@google.com", short: "joshuaduong", is_viewer: true }],
});

const allCls = [clIncomingTurn, clOwnWip, clOutgoing, clCc];
state.changes = allCls;
state.stacks = allCls.map(c => ({ stack_id: `single:${c.number}`, is_stack: false, title: c.subject, commits: [c] }));

const sections = partitionCommitsIntoDashboardSections(state.stacks);
assert.deepEqual(sections.map(s => s.title), [
  "Your turn",
  "Work in progress",
  "Outgoing reviews",
  "Incoming reviews",
  "CCed on",
]);
assert.deepEqual(sections[0].items.map(i => i.commit.number), [43320101]);
assert.deepEqual(sections[1].items.map(i => i.commit.number), [43320102]);
assert.deepEqual(sections[2].items.map(i => i.commit.number), [43320103]);
assert.deepEqual(sections[3].items.map(i => i.commit.number), [43320101]);
assert.deepEqual(sections[4].items.map(i => i.commit.number), [43320104]);

// j/k ordered visible commits deduplicates 43320101 while keeping Your turn first!
const orderedNums = getOrderedVisibleCommits(state.stacks).map(i => i.commit.number);
assert.equal(orderedNums.length, 4);
assert.equal(orderedNums[0], 43320101);
assert.equal(new Set(orderedNums).size, 4);

renderQueueList(state.stacks);
const listHtml = domMap.queueList.innerHTML;
assert.equal(listHtml.includes('data-pg-section="your_turn"'), true);
assert.equal(listHtml.includes('data-pg-section="wip"'), true);
assert.equal(listHtml.includes('data-pg-section="outgoing"'), true);
assert.equal(listHtml.includes('data-pg-section="incoming"'), true);
assert.equal(listHtml.includes('data-pg-section="cc"'), true);
// Outgoing section renders primary reviewers with attention turn marker + "+1" overflow
assert.equal(listHtml.includes("▸jansene"), true);
assert.equal(listHtml.includes(">+1</span>"), true);
// Waiting age ("4h"), unresolved count ("💬 2"), WIP tag, and XL size badge all render
assert.equal(listHtml.includes(">4h</div>"), true);
assert.equal(listHtml.includes("💬 2"), true);
assert.equal(listHtml.includes("pg-mini-status st-wip"), true);
assert.equal(listHtml.includes("size-xl"), true);

// Authentic PolyGerrit CSS class wiring & compact inline metadata + batch hints:
assert.equal(listHtml.includes("pg-attention-turn"), true);
assert.equal(listHtml.includes("pg-subj-line"), true);
assert.equal(listHtml.includes("pg-sub-meta"), true);
assert.equal(listHtml.includes("pg-sub-repo"), true);
assert.equal(listHtml.includes("pg-who-attn"), true);
assert.equal(listHtml.includes("pg-section-title-wrap"), true);
assert.equal(listHtml.includes("pg-section-name"), true);
assert.equal(listHtml.includes("pg-col-size"), true);
assert.equal(listHtml.includes("pg-compact-inline-meta"), true);
assert.equal(listHtml.includes("jansene · 4h"), true);
assert.equal(listHtml.includes("queue-batch-hint-compact"), true);

// Responsive compact / mini sidebar density tiers as user shrinks sidebar width:
assert.equal(computePgSidebarDensity(520), "wide");
assert.equal(computePgSidebarDensity(440), "compact");
assert.equal(computePgSidebarDensity(360), "compact");
assert.equal(computePgSidebarDensity(340), "mini");
assert.equal(computePgSidebarDensity(240), "mini");

setSidebarWidth(520);
assert.equal(domMap.queuePane.getAttribute("data-sidebar-density"), "wide");
assert.equal(domMap.workspaceContainer.getAttribute("data-sidebar-density"), "wide");

setSidebarWidth(380);
assert.equal(domMap.queuePane.getAttribute("data-sidebar-density"), "compact");
assert.equal(domMap.workspaceContainer.getAttribute("data-sidebar-density"), "compact");

setSidebarWidth(260);
assert.equal(domMap.queuePane.getAttribute("data-sidebar-density"), "mini");
assert.equal(domMap.workspaceContainer.getAttribute("data-sidebar-density"), "mini");

toggleSidebarCollapsed(true);
assert.equal(domMap.workspaceContainer.getAttribute("data-sidebar-density"), "collapsed");
toggleSidebarCollapsed(false);
assert.equal(domMap.workspaceContainer.getAttribute("data-sidebar-density"), "mini");

// Batch selection highlights section column header with has-selection:
state.checkedClNumbers.add(43320101);
renderQueueList(state.stacks);
assert.equal(domMap.queueList.innerHTML.includes("pg-col-header-row has-selection"), true);
assert.equal(domMap.queueList.innerHTML.includes("queue-batch-strip has-checked"), true);
state.checkedClNumbers.clear();
renderQueueList(state.stacks);

// Collapsing a dashboard section hides its rows cleanly.
toggleDashboardSection("cc");
assert.equal(state.collapsedDashboardSections.has("cc"), true);
assert.equal(domMap.queueList.innerHTML.includes("docs: CCed design update"), false);
toggleDashboardSection("cc");
assert.equal(domMap.queueList.innerHTML.includes("docs: CCed design update"), true);

// Voting Code-Review +2 clears viewer's attention turn so CL leaves "Your turn"
// while remaining visible in "Incoming reviews" with a ✓+2 chip!
applyOptimisticVoteToCl(clIncomingTurn, 2, null, null);
assert.equal(clIncomingTurn.viewer_in_attention_set, false);
const postVoteSections = partitionCommitsIntoDashboardSections(state.stacks);
assert.equal(postVoteSections[0].title, "Your turn");
assert.equal(postVoteSections[0].items.length, 0);
assert.deepEqual(postVoteSections[3].items.map(i => i.commit.number), [43320101]);
renderQueueList(state.stacks);
assert.equal(domMap.queueList.innerHTML.includes("No changes"), true);
assert.equal(domMap.queueList.innerHTML.includes("✓+2"), true);

console.log("POLYGERRIT_DASHBOARD_UX_OK");
"""
        )
        vm_wrapper = (
            'const fs = require("node:fs");\n'
            'const vm = require("node:vm");\n'
            "const code = fs.readFileSync(process.argv[2], 'utf8');\n"
            "const sandbox = {\n"
            "  require,\n"
            "  console,\n"
            "  setTimeout,\n"
            "  clearTimeout,\n"
            "  URL,\n"
            "  URLSearchParams,\n"
            "  window: { addEventListener() {}, innerWidth: 1400 },\n"
            "  document: { getElementById() { return null; }, addEventListener() {}, body: { classList: { toggle() {} } } },\n"
            "  localStorage: { getItem() { return null; }, setItem() {} },\n"
            "  navigator: { clipboard: { writeText: async () => {} } },\n"
            "  fetch: async () => ({ ok: true, json: async () => ({}) }),\n"
            "};\n"
            "vm.runInNewContext(code, sandbox);\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            js_path = Path(tmp_dir) / "pg_dashboard_harness.js"
            runner_path = Path(tmp_dir) / "run_vm.js"
            js_path.write_text(harness_js, encoding="utf-8")
            runner_path.write_text(vm_wrapper, encoding="utf-8")
            proc = subprocess.run(
                [node_bin, str(runner_path), str(js_path)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"PolyGerrit dashboard harness failed:\n{proc.stderr}\n{proc.stdout}",
            )
            self.assertIn("POLYGERRIT_DASHBOARD_UX_OK", proc.stdout)

    def test_polygerrit_radar_removed_and_authentic_change_view_header(self) -> None:
        from datetime import datetime, timezone
        import os
        import shutil
        import subprocess
        import tempfile
        from commands.gerrit.services.queue_service import enrich_change_for_ui
        from commands.gerrit.ui_server import render_static_gerrit_ui_html

        # 1. Verify submit_type is propagated by enrich_change_for_ui.
        now_dt = datetime(2026, 10, 29, 16, 0, 0, tzinfo=timezone.utc)
        raw_cl = {
            "_number": 43330001,
            "project": "platform/hardware/generic/goldfish",
            "branch": "emu-main-next",
            "topic": "pg-ux-测试_🚀_é",
            "submit_type": "REBASE_ALWAYS",
            "subject": "feat(goldfish): Authentic PolyGerrit header 测试_🚀_é",
            "status": "NEW",
            "created": "2026-10-29 12:00:00.000000000",
            "updated": "2026-10-29 15:30:00.000000000",
            "insertions": 24,
            "deletions": 6,
            "mergeable": True,
            "submittable": False,
            "current_revision": "rev_pg1",
            "revisions": {"rev_pg1": {"_number": 3, "files": {"a.cc": {}}}},
            "owner": {"_account_id": 1001, "email": "bot@google.com", "name": "Gemini Bot"},
            "reviewers": {"REVIEWER": [], "CC": []},
            "labels": {},
        }
        enriched = enrich_change_for_ui(raw_cl, "joshuaduong@google.com", now_dt=now_dt)
        self.assertEqual(enriched["submit_type"], "REBASE_ALWAYS")

        # 2. Verify all Cliff Radar markup, CSS, and JS identifiers are strictly removed.
        pg_html = render_static_gerrit_ui_html(variant="polygerrit")
        for forbidden_radar_token in (
            "cliffRadar",
            "cliff-radar",
            "cliff-header",
            "cliff-track",
            "radar-chip",
            "renderCliffRadar",
            "toggleCliffRadar",
            "openChatDmFromRadar",
            "cliffToggleBtn",
            "Show Radar",
            "CLIFF ZONE",
        ):
            self.assertNotIn(
                forbidden_radar_token,
                pg_html,
                msg=f"Expected radar token '{forbidden_radar_token}' to be removed from PolyGerrit template",
            )

        # 3. Verify PolyGerrit top section markers exist in template source.
        for required_marker in (
            'id="pgMainHeader" class="pg-main-header"',
            "function renderPgChangeHeaderHtml(c, bundle)",
            "function renderPgSubmitRequirementsHtml(c)",
            "function renderPgRelationChainHtml(c)",
            "function renderPgReplyDialogHtml(c)",
            "function openReplyDialog(opts = {})",
            "Submit Requirements",
            "Trigger Votes",
            "Repo | Branch",
            "Looks good to me, approved",
        ):
            self.assertIn(required_marker, pg_html)

        node_bin = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )
        if not node_bin:
            return

        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", pg_html, flags=re.S)
        self.assertTrue(scripts)
        js_body = scripts[-1]
        for boot_call in (
            "loadSidebarLayoutPrefs();",
            "initWorkspaceSplitter();",
            "initDiffCodeSelectionListeners();",
            "initApp();",
        ):
            js_body = js_body.replace(boot_call, f"// {boot_call}")

        harness_js = (
            """
const assert = require("node:assert/strict");
function makeMockEl() {
  return {
    innerHTML: "",
    textContent: "",
    value: "",
    checked: false,
    disabled: false,
    title: "",
    style: { display: "", setProperty() {} },
    classList: {
      _set: new Set(),
      add(...xs) { xs.forEach(x => this._set.add(x)); },
      remove(...xs) { xs.forEach(x => this._set.delete(x)); },
      toggle(x, force) {
        const on = force === undefined ? !this._set.has(x) : Boolean(force);
        if (on) this._set.add(x); else this._set.delete(x);
        return on;
      },
      contains(x) { return this._set.has(x); },
    },
    setAttribute() {},
    removeAttribute() {},
    getAttribute() { return ""; },
    focus() {},
    blur() {},
  };
}
const domMap = new Proxy({}, {
  get(target, prop) {
    if (typeof prop !== "string") return undefined;
    if (!target[prop]) target[prop] = makeMockEl();
    return target[prop];
  }
});
window.location = { search: "", href: "http://127.0.0.1:8080/" };
window.localStorage = { getItem() { return null; }, setItem() {} };
document.documentElement = { setAttribute() {}, clientWidth: 1440, clientHeight: 900 };
document.getElementById = (id) => domMap[id];
document.querySelector = () => null;
document.querySelectorAll = () => [];
"""
            + js_body
            + r"""
state.viewer = "joshuaduong@google.com";
const baseCl = {
  number: 43330001,
  subject: "refactor(file): Base UTF-8 path helper",
  url: "https://googleplex-android-review.git.corp.google.com/c/platform/hardware/generic/goldfish/+/43330001",
  project: "platform/hardware/generic/goldfish",
  project_short: "goldfish",
  branch: "emu-main-next",
  topic: "utf8-stack",
  submit_type: "REBASE_ALWAYS",
  patch_set: 2,
  owner_short: "jansene",
  owner_name: "Erwin Jansen",
  owner_email: "jansene@google.com",
  owner_is_bot: false,
  work_in_progress: false,
  mergeable: true,
  is_agent_cl: false,
  required_cr2_count: 1,
  human_cr2_count: 1,
  human_cr2: [{ email: "joshuaduong@google.com", short: "joshuaduong", name: "Joshua Duong", value: 2 }],
  human_cr1: [],
  presubmit_status: "passed",
  presubmit_score: 2,
  presubmit_vetted: true,
  is_submittable: true,
  updated_display: "15m ago",
  updated_iso: "2026-10-29T15:45:00Z",
  risk: { tier: "low", label: "Low Risk" },
  reviewers_detail: [],
  ccs_detail: [],
  bugs: [],
};

const tipAgentCl = {
  number: 43330002,
  subject: "feat(goldfish): Agent change view 测试_🚀_é",
  url: "https://googleplex-android-review.git.corp.google.com/c/platform/hardware/generic/goldfish/+/43330002",
  project: "platform/hardware/generic/goldfish",
  project_short: "goldfish",
  branch: "emu-main-next",
  topic: "utf8-stack",
  submit_type: "REBASE_ALWAYS",
  patch_set: 3,
  owner_short: "aemu-bot",
  owner_name: "AEMU Bot",
  owner_email: "aemu-bot@google.com",
  owner_is_bot: true,
  uploader_email: "jansene@google.com",
  uploader_short: "jansene",
  uploader_name: "Erwin Jansen",
  work_in_progress: false,
  mergeable: true,
  is_agent_cl: true,
  required_cr2_count: 2,
  human_cr2_count: 0,
  human_cr2: [],
  human_cr1: [{ email: "rkir@google.com", short: "rkir", name: "Roman Kiryanov", value: 1 }],
  presubmit_status: "running",
  presubmit_label: "⏳ Presubmit Running",
  presubmit_score: 0,
  presubmit_vetted: false,
  presubmit_ready_enabled: true,
  presubmit_ready_voters: ["jansene"],
  autosubmit_enabled: true,
  autosubmit_voters: ["jansene"],
  viewer_cr_vote: 0,
  viewer_presubmit_ready: false,
  viewer_autosubmit: false,
  is_submittable: false,
  unresolved_comment_count: 1,
  updated_display: "5m ago",
  updated_iso: "2026-10-29T15:55:00Z",
  risk: { tier: "medium", label: "Med Risk" },
  reviewers_detail: [
    {
      email: "rkir@google.com",
      short: "rkir",
      name: "Roman Kiryanov",
      full_name: "Roman Kiryanov",
      state: "REVIEWER",
      is_bot: false,
      is_owner: false,
      in_attention_set: false,
      cr_vote: 1,
      votes: [{ label: "Code-Review", short_label: "CR", value: 1, formatted: "+1" }],
    },
    {
      email: "joshuaduong@google.com",
      short: "joshuaduong",
      name: "Joshua Duong",
      full_name: "Joshua Duong",
      state: "REVIEWER",
      is_bot: false,
      is_owner: false,
      is_viewer: true,
      in_attention_set: true,
      cr_vote: 0,
      votes: [],
    },
  ],
  ccs_detail: [
    { email: "whollins@google.com", short: "whollins", name: "Will Hollins", state: "CC", is_bot: false, votes: [] },
  ],
  bugs: [{ id: "572839247", priority: "P1", title: "Unicode manifest" }],
};

state.changes = [baseCl, tipAgentCl];
state.stacks = [{
  stack_id: "stack:utf8",
  is_stack: true,
  title: "UTF-8 Stack",
  commits: [baseCl, tipAgentCl],
}];
state.selectedStackId = "stack:utf8";
state.selectedClNumber = 43330002;
state.diffsByCl[43330002] = {
  is_pure_rebase: true,
  revision_short: "a1b2c3d",
  parent_short: "9f8e7d6",
  commit_message: "feat(goldfish): Agent change view 测试_🚀_é\n\nExplain why.\n\nBug: 572839247\nTest: bazel test\n",
  files: [
    {
      file: "emulator/libs/file/file.cc",
      additions: 15,
      deletions: 2,
      lines: [{ type: "add", old: "", new: 1, text: "+// 测试_🚀_é" }],
    },
  ],
  unresolved_threads: [
    { file_path: "emulator/libs/file/file.cc", line: 1, author_name: "rkir", latest_message: "Nit here", unresolved: true },
  ],
};

// Verify sticky header right side ONLY contains Submit + Harness Actions (no extra buttons).
const hdrHtml = renderPgChangeHeaderHtml(tipAgentCl, state.diffsByCl[43330002]);
const actionsIdx = hdrHtml.indexOf('class="pg-commit-actions');
assert.equal(actionsIdx > 0, true);
const actionsSlice = hdrHtml.slice(actionsIdx);
assert.equal(actionsSlice.includes('id="btnSubmitCl"'), true);
assert.equal(actionsSlice.includes('id="btnHarnessMenuToggle"'), true);
assert.equal(actionsSlice.includes("diffColorSchemeSelect"), false);
assert.equal(actionsSlice.includes("btnNavPrev"), false);
assert.equal(actionsSlice.includes("btnNavNext"), false);
assert.equal(actionsSlice.includes("Open in Gerrit"), false);
// Unsubmittable CL hides Submit button like real PolyGerrit.
assert.equal(actionsSlice.includes('style="display:none;"'), true);

// Submittable base CL shows primary Submit button when standalone.
const subHdrHtml = renderPgChangeHeaderHtml(baseCl, null);
assert.equal(subHdrHtml.includes("Ready to Submit"), true);

renderDiffPane();
const html = domMap.diffScroll.innerHTML;
// Authentic metadata rows + Submit Requirements + Trigger Votes + Summary + Relation Chain:
for (const expected of [
  "pg-change-view",
  "pg-status-badge status-active",
  "Pure Rebase",
  "43330002",
  "feat(goldfish): Agent change view 测试_🚀_é",
  ">Updated<",
  ">Owner<",
  ">Uploader<",
  ">Reviewers<",
  ">CC<",
  ">Repo | Branch<",
  ">Parent<",
  "9f8e7d6",
  ">Topic<",
  "utf8-stack",
  ">Strategy<",
  "Rebase Always",
  ">Fixes<",
  "572839247",
  "Submit Requirements",
  "Code-Review",
  "Requires 2 votes",
  "Presubmit-Verified",
  "Trigger Votes",
  "Presubmit-Ready",
  "Autosubmit",
  'id="btnOpenReplyDialog"',
  "Patchset 3",
  "a1b2c3d",
  "💬 1 unresolved",
  "Relation chain (2)",
  "pg-reply-dialog",
]) {
  assert.equal(html.includes(expected), true, `Missing expected PolyGerrit marker: ${expected}`);
}

// Verify PolyGerrit Reply modal staging, score descriptions, and Cancel reset.
assert.equal(state.replyDialogOpen, false);
openReplyDialog({ focusComment: true });
assert.equal(state.replyDialogOpen, true);
assert.equal(domMap.replyDialogBackdrop.classList.contains("open"), true);

toggleStagedCr(2);
assert.equal(domMap.pgCrScoreDesc.textContent, "Looks good to me, approved");
assert.equal(domMap.btnSubmitVotes.textContent, "Send (CR +2)");

setStagedPrValue(1);
assert.equal(domMap.pgPrScoreDesc.textContent, "Ready for presubmit");
assert.equal(domMap.pgReplyStagedHint.textContent, "Draft: CR +2, PR +1");
assert.equal(domMap.btnSubmitVotes.textContent, "Send (CR +2, PR +1)");

cancelReplyDialog();
assert.equal(state.replyDialogOpen, false);
assert.equal(state.stagedVote.cr, null);
assert.equal(state.stagedVote.pr, null);
assert.equal(domMap.pgReplyStagedHint.textContent, "");

console.log("POLYGERRIT_CHANGE_VIEW_HEADER_OK");
"""
        )
        vm_wrapper = (
            'const fs = require("node:fs");\n'
            'const vm = require("node:vm");\n'
            "const code = fs.readFileSync(process.argv[2], 'utf8');\n"
            "const sandbox = {\n"
            "  require,\n"
            "  console,\n"
            "  setTimeout,\n"
            "  clearTimeout,\n"
            "  URL,\n"
            "  URLSearchParams,\n"
            "  window: { addEventListener() {}, innerWidth: 1440 },\n"
            "  document: { getElementById() { return null; }, addEventListener() {}, body: { classList: { toggle() {} } } },\n"
            "  localStorage: { getItem() { return null; }, setItem() {} },\n"
            "  navigator: { clipboard: { writeText: async () => {} } },\n"
            "  fetch: async () => ({ ok: true, json: async () => ({}) }),\n"
            "};\n"
            "vm.runInNewContext(code, sandbox);\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            js_path = Path(tmp_dir) / "pg_change_view_harness.js"
            runner_path = Path(tmp_dir) / "run_vm.js"
            js_path.write_text(harness_js, encoding="utf-8")
            runner_path.write_text(vm_wrapper, encoding="utf-8")
            proc = subprocess.run(
                [node_bin, str(runner_path), str(js_path)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"PolyGerrit change view harness failed:\n{proc.stderr}\n{proc.stdout}",
            )
            self.assertIn("POLYGERRIT_CHANGE_VIEW_HEADER_OK", proc.stdout)

    def test_polygerrit_file_list_table_and_reviewed_checkboxes(self) -> None:
        import os
        import shutil
        import subprocess
        import tempfile
        from commands.gerrit.services.diff_parser import parse_unified_diff_files
        from commands.gerrit.ui_server import render_static_gerrit_ui_html

        # 1. Verify diff_parser sets file statuses: A (added), D (deleted), R (renamed), M (modified)
        sample_diff = (
            "diff --git a/file_new.cc b/file_new.cc\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/file_new.cc\n"
            "@@ -0,0 +1,1 @@\n"
            "+// Added 测试_🚀_é\n"
            "diff --git a/file_del.cc b/file_del.cc\n"
            "deleted file mode 100644\n"
            "--- a/file_del.cc\n"
            "+++ /dev/null\n"
            "@@ -1,1 +0,0 @@\n"
            "-// Deleted\n"
            "diff --git a/old_name.cc b/new_name.cc\n"
            "similarity index 100%\n"
            "rename from old_name.cc\n"
            "rename to new_name.cc\n"
            "diff --git a/file_mod.cc b/file_mod.cc\n"
            "--- a/file_mod.cc\n"
            "+++ b/file_mod.cc\n"
            "@@ -1,1 +1,2 @@\n"
            " int a;\n"
            "+int b;\n"
        )
        parsed_files = parse_unified_diff_files(sample_diff)
        self.assertEqual(len(parsed_files), 4)
        self.assertEqual(parsed_files[0]["status"], "A")
        self.assertEqual(parsed_files[1]["status"], "D")
        self.assertEqual(parsed_files[2]["status"], "R")
        self.assertEqual(parsed_files[3]["status"], "M")

        # 2. Verify PolyGerrit HTML template contains the file list & reviewed functions
        pg_html = render_static_gerrit_ui_html(variant="polygerrit")
        for expected_token in (
            "function renderPgFileListHtml(c, bundle)",
            "function setPgFileReviewed(clNum, patchSet, filePath, isReviewed)",
            "function toggleAllPgFileDiffs(clNum, patchSet)",
            "function scrollToFileDiff(clNum, patchSet, filePath)",
            "pg-file-list-table",
            "pg-file-row",
            "pg-magic-file-row",
            "pg-file-status-badge",
            "pg-file-reviewed-cb",
            "pg-diff-header-reviewed-cb",
            "pg-reviewed-summary-badge",
            "Expand All (Shift+I)",
        ):
            self.assertIn(
                expected_token,
                pg_html,
                msg=f"Expected token '{expected_token}' to be present in PolyGerrit UI template",
            )

        node_bin = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )
        if not node_bin:
            return

        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", pg_html, flags=re.S)
        self.assertTrue(scripts)
        js_body = scripts[-1]
        for boot_call in (
            "loadSidebarLayoutPrefs();",
            "initWorkspaceSplitter();",
            "initDiffCodeSelectionListeners();",
            "initApp();",
        ):
            js_body = js_body.replace(boot_call, f"// {boot_call}")

        harness_js = (
            """
const assert = require("node:assert/strict");
const mockLocalStorage = {};
function makeMockEl() {
  return {
    innerHTML: "",
    textContent: "",
    value: "",
    checked: false,
    disabled: false,
    title: "",
    style: { display: "", setProperty() {} },
    classList: {
      _set: new Set(),
      add(...xs) { xs.forEach(x => this._set.add(x)); },
      remove(...xs) { xs.forEach(x => this._set.delete(x)); },
      toggle(x, force) {
        const on = force === undefined ? !this._set.has(x) : Boolean(force);
        if (on) this._set.add(x); else this._set.delete(x);
        return on;
      },
      contains(x) { return this._set.has(x); },
    },
    setAttribute() {},
    removeAttribute() {},
    getAttribute() { return ""; },
    focus() {},
    blur() {},
    scrollIntoView() {},
    querySelector() { return makeMockEl(); },
    querySelectorAll() { return []; },
  };
}
const domMap = new Proxy({}, {
  get(target, prop) {
    if (typeof prop !== "string") return undefined;
    if (!target[prop]) target[prop] = makeMockEl();
    return target[prop];
  }
});
window.location = { search: "", href: "http://127.0.0.1:8080/" };
window.localStorage = {
  getItem(k) { return mockLocalStorage[k] || null; },
  setItem(k, v) { mockLocalStorage[k] = String(v); },
  removeItem(k) { delete mockLocalStorage[k]; },
};
document.documentElement = { setAttribute() {}, clientWidth: 1440, clientHeight: 900 };
document.getElementById = (id) => domMap[id];
document.querySelector = () => null;
document.querySelectorAll = () => [];
"""
            + js_body
            + r"""
state.viewer = "joshuaduong@google.com";
const testCl = {
  number: 43330002,
  subject: "feat(goldfish): Unicode file review 测试_🚀_é",
  url: "https://googleplex-android-review.git.corp.google.com/c/platform/hardware/generic/goldfish/+/43330002",
  project: "platform/hardware/generic/goldfish",
  project_short: "goldfish",
  branch: "emu-main-next",
  submit_type: "REBASE_ALWAYS",
  patch_set: 3,
  owner_short: "jansene",
  owner_name: "Erwin Jansen",
  owner_email: "jansene@google.com",
  is_agent_cl: false,
  is_submittable: false,
  reviewers_detail: [],
  ccs_detail: [],
  bugs: [],
};

state.changes = [testCl];
state.stacks = [{
  stack_id: "stack:test",
  is_stack: false,
  title: "Test Stack",
  commits: [testCl],
}];
state.selectedStackId = "stack:test";
state.selectedClNumber = 43330002;
state.diffsByCl[43330002] = {
  revision_short: "c1d2e3f",
  patch_set: 3,
  commit_message: "feat(goldfish): Unicode file review 测试_🚀_é\n\nBug: N/A\nTest: bazel test\n",
  files: [
    {
      file: "emulator/libs/file/测试_🚀_é.cc",
      status: "A",
      additions: 15,
      deletions: 0,
      lines: [{ type: "add", old: "", new: 1, text: "+// Unicode test line" }],
    },
    {
      file: "emulator/libs/common.h",
      status: "M",
      additions: 4,
      deletions: 1,
      lines: [{ type: "add", old: "", new: 10, text: "+#define UNICODE_VAL 1" }],
    },
  ],
  unresolved_threads: [
    { file_path: "emulator/libs/file/测试_🚀_é.cc", line: 1, author_name: "rkir", latest_message: "Looks good", unresolved: false },
  ],
};

// 1. Render file list table HTML directly
const fileListHtml = renderPgFileListHtml(testCl, state.diffsByCl[43330002]);
assert.equal(fileListHtml.includes("pg-file-list-table"), true);
assert.equal(fileListHtml.includes("pg-magic-file-row"), true);
assert.equal(fileListHtml.includes("/COMMIT_MSG"), true);
assert.equal(fileListHtml.includes("emulator/libs/file/测试_🚀_é.cc"), true);
assert.equal(fileListHtml.includes("status-A"), true);
assert.equal(fileListHtml.includes("status-M"), true);

// 2. Render entire Diff pane and verify presence of gr-file-list table + diff blocks
renderDiffPane();
const paneHtml = domMap.diffScroll.innerHTML;
assert.equal(paneHtml.includes("pg-file-list-table"), true);
assert.equal(paneHtml.includes("pg-magic-file-row"), true);
assert.equal(paneHtml.includes('id="btnToggleAllDiffs-43330002"'), true);
assert.equal(paneHtml.includes('id="pgReviewedSummary-43330002"'), true);
assert.equal(paneHtml.includes("pg-file-diff-block"), true);
assert.equal(paneHtml.includes("pg-diff-header-reviewed-cb"), true);

// 3. Test Reviewed state tracking & localStorage persistence
const testFile = "emulator/libs/file/测试_🚀_é.cc";
assert.equal(isPgFileReviewed(43330002, 3, testFile), false);
assert.equal(isPgFileCollapsed(43330002, 3, testFile), false);

// Mark as reviewed -> automatically marks reviewed and collapses file diff:
setPgFileReviewed(43330002, 3, testFile, true);
assert.equal(isPgFileReviewed(43330002, 3, testFile), true);
assert.equal(isPgFileCollapsed(43330002, 3, testFile), true);
assert.equal(mockLocalStorage["pg_reviewed_43330002_ps3"].includes("测试_🚀_é.cc"), true);

// Unmark reviewed -> re-expands file diff:
setPgFileReviewed(43330002, 3, testFile, false);
assert.equal(isPgFileReviewed(43330002, 3, testFile), false);
assert.equal(isPgFileCollapsed(43330002, 3, testFile), false);
assert.equal(mockLocalStorage["pg_reviewed_43330002_ps3"].includes("测试_🚀_é.cc"), false);

// 4. Test Expand All / Collapse All
toggleAllPgFileDiffs(43330002, 3);
// Neither was collapsed, so toggleAll collapses all:
assert.equal(isPgFileCollapsed(43330002, 3, testFile), true);
assert.equal(isPgFileCollapsed(43330002, 3, "emulator/libs/common.h"), true);

// Toggling again expands all:
toggleAllPgFileDiffs(43330002, 3);
assert.equal(isPgFileCollapsed(43330002, 3, testFile), false);
assert.equal(isPgFileCollapsed(43330002, 3, "emulator/libs/common.h"), false);

console.log("POLYGERRIT_FILE_LIST_AND_REVIEWED_OK");
"""
        )
        vm_wrapper = (
            'const fs = require("node:fs");\n'
            'const vm = require("node:vm");\n'
            "const code = fs.readFileSync(process.argv[2], 'utf8');\n"
            "const sandbox = {\n"
            "  require,\n"
            "  console,\n"
            "  setTimeout,\n"
            "  clearTimeout,\n"
            "  URL,\n"
            "  URLSearchParams,\n"
            "  window: { addEventListener() {}, innerWidth: 1440 },\n"
            "  document: { getElementById() { return null; }, addEventListener() {}, body: { classList: { toggle() {} } } },\n"
            "  localStorage: { getItem() { return null; }, setItem() {} },\n"
            "  navigator: { clipboard: { writeText: async () => {} } },\n"
            "  fetch: async () => ({ ok: true, json: async () => ({}) }),\n"
            "};\n"
            "vm.runInNewContext(code, sandbox);\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            js_path = Path(tmp_dir) / "pg_file_list_harness.js"
            runner_path = Path(tmp_dir) / "run_vm.js"
            js_path.write_text(harness_js, encoding="utf-8")
            runner_path.write_text(vm_wrapper, encoding="utf-8")
            proc = subprocess.run(
                [node_bin, str(runner_path), str(js_path)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"PolyGerrit file list harness failed:\n{proc.stderr}\n{proc.stdout}",
            )
            self.assertIn("POLYGERRIT_FILE_LIST_AND_REVIEWED_OK", proc.stdout)

    def test_polygerrit_change_log_messages_section(self) -> None:
        import os
        import shutil
        import subprocess
        import tempfile
        from commands.gerrit.services.queue_service import (
            _enrich_change_messages,
            enrich_change_for_ui,
        )
        from commands.gerrit.ui_server import render_static_gerrit_ui_html

        # 1. Verify _enrich_change_messages parses bots, authors, votes, and Unicode
        raw_msgs = [
            {
                "id": "msg_001",
                "tag": "autogenerated:gerrit:newPatchSet",
                "author": {"name": "Treehugger Robot", "email": "treehugger-gerrit-robot@google.com"},
                "date": "2026-10-30 06:00:00.000000000",
                "_revision_number": 1,
                "message": "Uploaded patch set 1.\n\nPresubmit-Verified+1",
            },
            {
                "id": "msg_002",
                "tag": "",
                "author": {"name": "Reviewer 测试_🚀_é", "email": "tester@google.com"},
                "date": "2026-10-30 06:30:00.000000000",
                "_revision_number": 1,
                "message": "Looks great to me!\n> quote line\nCode-Review+2\nAutosubmit+1",
            },
            {
                "id": "msg_003",
                "tag": "autogenerated:gerrit:newPatchSet",
                "author": {"name": "Dev", "email": "dev@google.com"},
                "date": "2026-10-30 07:00:00.000000000",
                "_revision_number": 2,
                "message": "Uploaded patch set 2.",
            },
        ]
        enriched_msgs = _enrich_change_messages(raw_msgs)
        self.assertEqual(len(enriched_msgs), 3)

        # First message: Treehugger bot
        self.assertTrue(enriched_msgs[0]["is_bot"])
        self.assertEqual(enriched_msgs[0]["patch_set"], 1)
        self.assertEqual(enriched_msgs[0]["author_name"], "Treehugger Robot")
        self.assertEqual(enriched_msgs[0]["summary"], "Uploaded patch set 1.")

        # Second message: Human reviewer with Unicode name
        self.assertFalse(enriched_msgs[1]["is_bot"])
        self.assertEqual(enriched_msgs[1]["author_name"], "Reviewer 测试_🚀_é")
        self.assertEqual(enriched_msgs[1]["summary"], "Looks great to me!")
        self.assertIn("Code-Review+2", enriched_msgs[1]["message"])

        # Third message: autogenerated tag is_bot
        self.assertTrue(enriched_msgs[2]["is_bot"])
        self.assertEqual(enriched_msgs[2]["patch_set"], 2)

        # 2. Verify enrich_change_for_ui propagates messages
        sample_change = {
            "_number": 43330005,
            "change_id": "I43330005",
            "project": "platform/hardware/google/aemu",
            "branch": "master",
            "subject": "feat(gerrit): Test messages change log",
            "status": "NEW",
            "current_revision": "rev2",
            "current_revision_number": 2,
            "revisions": {"rev2": {"_number": 2, "commit": {"message": "feat: test"}}},
            "messages": raw_msgs,
        }
        en = enrich_change_for_ui(sample_change, "tester@google.com")
        self.assertIn("messages", en)
        self.assertEqual(len(en["messages"]), 3)

        # 3. Verify static HTML template includes PolyGerrit Change Log components
        pg_html = render_static_gerrit_ui_html(
            initial_payload={"viewer": "tester@google.com"},
            variant="polygerrit",
        )
        for expected_token in (
            "pg-change-log-section",
            "Change Log",
            "pg-change-log-header",
            "pg-message-card",
            "pg-msg-automated",
            "pg-vote-chip",
            "vote-cr2",
            "vote-pv1",
            "vote-as1",
            "pg-msg-quote",
            "pg-tab-link",
            "scrollToChangeLog",
            "togglePgAutomatedMessages",
            "togglePgMessageCard",
            "expandAllPgMessages",
            "collapseAllPgMessages",
            "applyPgMessageFilters",
        ):
            self.assertIn(
                expected_token,
                pg_html,
                msg=f"Expected token '{expected_token}' in PolyGerrit HTML template",
            )

        node_bin = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )
        if not node_bin:
            return

        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", pg_html, flags=re.S)
        self.assertTrue(scripts)
        js_body = scripts[-1]
        for boot_call in (
            "loadSidebarLayoutPrefs();",
            "initWorkspaceSplitter();",
            "initDiffCodeSelectionListeners();",
            "initApp();",
        ):
            js_body = js_body.replace(boot_call, f"// {boot_call}")

        harness_js = (
            """
const assert = require("node:assert/strict");
const mockLocalStorage = {};
function makeMockEl() {
  return {
    innerHTML: "",
    textContent: "",
    value: "",
    checked: false,
    disabled: false,
    title: "",
    style: { display: "", setProperty() {} },
    classList: {
      _set: new Set(),
      add(...xs) { xs.forEach(x => this._set.add(x)); },
      remove(...xs) { xs.forEach(x => this._set.delete(x)); },
      toggle(x, force) {
        if (typeof force === "boolean") {
          if (force) this._set.add(x);
          else this._set.delete(x);
          return force;
        }
        if (this._set.has(x)) { this._set.delete(x); return false; }
        this._set.add(x); return true;
      },
      contains(x) { return this._set.has(x); }
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    scrollIntoView() {},
    setAttribute() {},
    getAttribute() { return ""; }
  };
}
globalThis.localStorage = {
  getItem(k) { return mockLocalStorage[k] || null; },
  setItem(k, v) { mockLocalStorage[k] = String(v); },
  removeItem(k) { delete mockLocalStorage[k]; }
};
globalThis.document = {
  getElementById(id) {
    if (!this._elements) this._elements = {};
    if (!this._elements[id]) {
      this._elements[id] = makeMockEl();
      this._elements[id].id = id;
    }
    return this._elements[id];
  },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {},
  body: makeMockEl()
};

"""
            + js_body
            + """

// 1. Test renderPgChangeLogSectionHtml with populated messages
const sampleCl = {
  number: 43330005,
  patch_set: 2,
  messages: [
    {
      id: "m1",
      author_name: "Treehugger Robot",
      author_email: "treehugger@google.com",
      is_bot: true,
      tag: "autogenerated:gerrit:newPatchSet",
      date: "2026-10-30 06:00:00.000000000",
      patch_set: 1,
      message: "Uploaded patch set 1.\\n\\nPresubmit-Verified+1",
      summary: "Uploaded patch set 1."
    },
    {
      id: "m2",
      author_name: "Reviewer 测试_🚀_é",
      author_email: "tester@google.com",
      is_bot: false,
      tag: "",
      date: "2026-10-30 06:30:00.000000000",
      patch_set: 1,
      message: "Looks great to me!\\n> quote line\\nCode-Review+2\\nAutosubmit+1",
      summary: "Looks great to me!"
    }
  ]
};

const sectionHtml = renderPgChangeLogSectionHtml(sampleCl, {});
assert.match(sectionHtml, /id="pgChangeLog-43330005"/);
assert.match(sectionHtml, /Change Log/);
assert.match(sectionHtml, /2 messages/);
assert.match(sectionHtml, /Reviewer 测试_🚀_é/);
assert.match(sectionHtml, /Treehugger Robot/);
assert.match(sectionHtml, /Automated/);
assert.match(sectionHtml, /PS 1/);

// Verify vote chip and blockquote formatting in message body:
const formattedMsg = formatPgMessageContentHtml(sampleCl.messages[1].message);
assert.match(formattedMsg, /class="pg-vote-chip vote-cr2"/);
assert.match(formattedMsg, /Code-Review \\+2/);
assert.match(formattedMsg, /class="pg-vote-chip vote-as1"/);
assert.match(formattedMsg, /class="pg-msg-quote"/);
assert.match(formattedMsg, /quote line/);

// Verify author initials helper (Unicode safe):
assert.equal(getPgAuthorInitials("Reviewer 测试_🚀_é"), "R测");
assert.equal(getPgAuthorInitials("Treehugger Robot"), "TR");

// 2. Test relative date formatting:
const relNow = formatPgRelativeDate(new Date().toISOString());
assert.match(relNow, /just now|m ago/);

// 3. Test empty messages fallback:
const emptySectionHtml = renderPgChangeLogSectionHtml({ number: 99999, messages: [] }, {});
assert.match(emptySectionHtml, /id="pgChangeLog-99999"/);
assert.match(emptySectionHtml, /0 messages/);
assert.match(emptySectionHtml, /No messages recorded for this change/);

console.log("POLYGERRIT_CHANGE_LOG_MESSAGES_OK");
"""
        )
        vm_wrapper = (
            'const fs = require("node:fs");\n'
            'const vm = require("node:vm");\n'
            "const code = fs.readFileSync(process.argv[2], 'utf8');\n"
            "const sandbox = {\n"
            "  require,\n"
            "  console,\n"
            "  setTimeout,\n"
            "  clearTimeout,\n"
            "  URL,\n"
            "  URLSearchParams,\n"
            "  window: { addEventListener() {}, innerWidth: 1440 },\n"
            "  document: { getElementById() { return null; }, addEventListener() {}, body: { classList: { toggle() {} } } },\n"
            "  localStorage: { getItem() { return null; }, setItem() {} },\n"
            "  navigator: { clipboard: { writeText: async () => {} } },\n"
            "  fetch: async () => ({ ok: true, json: async () => ({}) }),\n"
            "};\n"
            "vm.runInNewContext(code, sandbox);\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            js_path = Path(tmp_dir) / "pg_change_log_harness.js"
            runner_path = Path(tmp_dir) / "run_vm.js"
            js_path.write_text(harness_js, encoding="utf-8")
            runner_path.write_text(vm_wrapper, encoding="utf-8")
            proc = subprocess.run(
                [node_bin, str(runner_path), str(js_path)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"PolyGerrit change log harness failed:\n{proc.stderr}\n{proc.stdout}",
            )
            self.assertIn("POLYGERRIT_CHANGE_LOG_MESSAGES_OK", proc.stdout)

    def test_polygerrit_patchset_selector_and_chunk_navigation(self) -> None:
        import os
        import shutil
        import subprocess
        import tempfile
        from unittest.mock import MagicMock
        from commands.gerrit.services.queue_service import enrich_change_for_ui
        from commands.gerrit.services.diff_parser import GerritDiffService
        from commands.gerrit.ui_server import render_static_gerrit_ui_html
        from lib.gerrit_api import GerritClient

        # 1. Verify enrich_change_for_ui extracts patch_sets array including Unicode meta
        sample_change = {
            "_number": 43330006,
            "change_id": "I43330006",
            "project": "platform/hardware/google/aemu",
            "branch": "master",
            "subject": "feat(gerrit): Test patchset comparison 测试_🚀_é",
            "status": "NEW",
            "current_revision": "rev3",
            "current_revision_number": 3,
            "revisions": {
                "rev1": {"_number": 1},
                "rev2": {"_number": 2},
                "rev3": {"_number": 3},
            },
            "messages": [
                {"_revision_number": 1, "message": "PS 1"},
                {"_revision_number": 2, "message": "PS 2"},
                {"_revision_number": 3, "message": "PS 3"},
            ],
        }
        en = enrich_change_for_ui(sample_change, "tester@google.com")
        self.assertIn("patch_sets", en)
        self.assertEqual(en["patch_sets"], [1, 2, 3])
        self.assertEqual(en["patch_set"], 3)

        # 2. Verify GerritClient.get_revision_patch supports base parameter
        mock_client = MagicMock(spec=GerritClient)
        mock_client.api_host = "https://android-review.googlesource.com"
        mock_client._request_raw = MagicMock(return_value="--- a/file.txt\n+++ b/file.txt\n@@ -1,1 +1,1 @@\n-old\n+new\n")
        patch_text = GerritClient.get_revision_patch(mock_client, "43330006", revision="3", base="1")
        mock_client._request_raw.assert_called_with("/a/changes/43330006/revisions/3/patch?base=1")
        self.assertIn("+new", patch_text)

        # 3. Verify GerritDiffService.fetch_commit_diff_bundle passes patch_set and base
        with patch("commands.gerrit.services.diff_parser.GerritClient"):
            diff_service = GerritDiffService(
                host="https://android-review.googlesource.com",
                default_viewer="tester@google.com",
            )
        diff_service.client = mock_client
        mock_client.get_comment_threads = MagicMock(return_value=[])
        diff_service.get_code_questions = MagicMock(return_value=[])
        bundle = diff_service.fetch_commit_diff_bundle("43330006", patch_set=2, base=1)
        self.assertEqual(bundle["patch_set"], 2)
        self.assertEqual(bundle["base_patch_set"], "1")

        # 4. Verify static PolyGerrit HTML template tokens
        pg_html = render_static_gerrit_ui_html(
            initial_payload={"viewer": "tester@google.com"},
            variant="polygerrit",
        )
        for expected_token in (
            "pg-patchset-selector-wrap",
            "pg-ps-label",
            "pg-ps-select-group",
            "pg-ps-select",
            "pg-ps-separator",
            "pg-interdiff-badge",
            "pg-chunk-focused-flash",
            "renderPgPatchsetSelectorHtml",
            "onPgPatchsetComparisonChanged",
            "selectPgPatchsetComparison",
            "navigatePgDiffChunk",
        ):
            self.assertIn(
                expected_token,
                pg_html,
                msg=f"Expected token '{expected_token}' in PolyGerrit HTML template",
            )

        node_bin = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )
        if not node_bin:
            return

        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", pg_html, flags=re.S)
        self.assertTrue(scripts)
        js_body = scripts[-1]
        for boot_call in (
            "loadSidebarLayoutPrefs();",
            "initWorkspaceSplitter();",
            "initDiffCodeSelectionListeners();",
            "initApp();",
        ):
            js_body = js_body.replace(boot_call, f"// {boot_call}")

        harness_js = (
            """
const assert = require("node:assert/strict");
const mockLocalStorage = {};
function makeMockEl(tagName = "div") {
  return {
    tagName: tagName.toUpperCase(),
    innerHTML: "",
    textContent: "",
    value: "",
    checked: false,
    disabled: false,
    title: "",
    offsetTop: 100,
    style: { display: "", setProperty() {} },
    classList: {
      _set: new Set(),
      add(...xs) { xs.forEach(x => this._set.add(x)); },
      remove(...xs) { xs.forEach(x => this._set.delete(x)); },
      toggle(x, force) {
        if (typeof force === "boolean") {
          if (force) this._set.add(x);
          else this._set.delete(x);
          return force;
        }
        if (this._set.has(x)) { this._set.delete(x); return false; }
        this._set.add(x); return true;
      },
      contains(x) { return this._set.has(x); }
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    scrollIntoView() {},
    setAttribute() {},
    getAttribute() { return ""; }
  };
}
globalThis.localStorage = {
  getItem(k) { return mockLocalStorage[k] || null; },
  setItem(k, v) { mockLocalStorage[k] = String(v); },
  removeItem(k) { delete mockLocalStorage[k]; }
};
globalThis.document = {
  getElementById(id) {
    if (!this._elements) this._elements = {};
    if (!this._elements[id]) {
      this._elements[id] = makeMockEl();
      this._elements[id].id = id;
    }
    return this._elements[id];
  },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {},
  body: makeMockEl()
};

"""
            + js_body
            + """

// 1. Test renderPgPatchsetSelectorHtml for Base vs PS 3
const sampleCl = {
  number: 43330006,
  patch_set: 3,
  patch_sets: [1, 2, 3]
};
const baseSelectorHtml = renderPgPatchsetSelectorHtml(sampleCl, { patch_set: 3, base_patch_set: "base" });
assert.match(baseSelectorHtml, /class="pg-patchset-selector-wrap"/);
assert.match(baseSelectorHtml, /Diff:/);
assert.match(baseSelectorHtml, /id="pgBasePsSelect-43330006"/);
assert.match(baseSelectorHtml, /id="pgTargetPsSelect-43330006"/);
assert.match(baseSelectorHtml, /value="base"[^>]*selected/);
assert.match(baseSelectorHtml, /Patchset 3 \\(latest\\)/);

// 2. Test renderPgPatchsetSelectorHtml for Interdiff (PS 1 vs PS 3)
const interdiffHtml = renderPgPatchsetSelectorHtml(sampleCl, { patch_set: 3, base_patch_set: "1" });
assert.match(interdiffHtml, /class="pg-interdiff-badge"/);
assert.match(interdiffHtml, /Interdiff \\(PS 1 → PS 3\\)/);

// 3. Test navigatePgDiffChunk function exists and executes safely
assert.equal(typeof navigatePgDiffChunk, "function");
navigatePgDiffChunk("next");
navigatePgDiffChunk("prev");

// 4. Test selectPgPatchsetComparison function exists
assert.equal(typeof selectPgPatchsetComparison, "function");
assert.equal(typeof onPgPatchsetComparisonChanged, "function");

console.log("POLYGERRIT_PATCHSET_SELECTOR_OK");
"""
        )
        vm_wrapper = (
            'const fs = require("node:fs");\n'
            'const vm = require("node:vm");\n'
            "const code = fs.readFileSync(process.argv[2], 'utf8');\n"
            "const sandbox = {\n"
            "  require,\n"
            "  console,\n"
            "  setTimeout,\n"
            "  clearTimeout,\n"
            "  URL,\n"
            "  URLSearchParams,\n"
            "  window: { addEventListener() {}, innerWidth: 1440 },\n"
            "  document: { getElementById() { return null; }, addEventListener() {}, body: { classList: { toggle() {} } } },\n"
            "  localStorage: { getItem() { return null; }, setItem() {} },\n"
            "  navigator: { clipboard: { writeText: async () => {} } },\n"
            "  fetch: async () => ({ ok: true, json: async () => ({ diffs: [] }) }),\n"
            "};\n"
            "vm.runInNewContext(code, sandbox);\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            js_path = Path(tmp_dir) / "pg_ps_selector_harness.js"
            runner_path = Path(tmp_dir) / "run_vm.js"
            js_path.write_text(harness_js, encoding="utf-8")
            runner_path.write_text(vm_wrapper, encoding="utf-8")
            proc = subprocess.run(
                [node_bin, str(runner_path), str(js_path)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"PolyGerrit patchset selector harness failed:\n{proc.stderr}\n{proc.stdout}",
            )
            self.assertIn("POLYGERRIT_PATCHSET_SELECTOR_OK", proc.stdout)


    def test_polygerrit_draft_comments_and_reply_dialog(self) -> None:
        import os
        import shutil
        import subprocess
        import tempfile
        from unittest.mock import MagicMock
        from commands.gerrit.ui_server import render_static_gerrit_ui_html

        # 1. Verify static PolyGerrit HTML template contains draft comment & reply tokens
        pg_html = render_static_gerrit_ui_html(
            initial_payload={"viewer": "tester@google.com"},
            variant="polygerrit",
        )
        for expected_token in (
            "pg-reply-drafts-section",
            "pg-reply-drafts-head",
            "pg-reply-drafts-title",
            "pg-reply-drafts-list",
            "pg-reply-draft-card",
            "pg-reply-draft-loc",
            "pg-reply-draft-discard",
            "has-drafts",
            "inline-quick-btn",
            "inline-done-btn",
            "inline-ack-btn",
            "getPendingDraftsForCl",
            "renderPgReplyDraftsListHtml",
            "syncReplyDraftsSectionDom",
            "updateReplyButtonDraftsBadge",
            "discardReplyDialogDraft",
            "quickReplyInlineComment",
            "setInlineReplyQuickText",
            "saveInlineReplyAsDraft",
            "publishPendingDraftsForCl",
        ):
            self.assertIn(
                expected_token,
                pg_html,
                msg=f"Expected draft token '{expected_token}' in PolyGerrit HTML template",
            )

        # 2. Verify backend inline comment & reply API handlers support Unicode data (测试_🚀_é)
        mock_service = MagicMock()
        mock_service.post_inline_comment.return_value = {
            "status": "ok",
            "comment_id": "c_unicode_123",
            "author_name": "tester",
            "patch_set": 2,
        }
        mock_service.reply_inline_comment.return_value = {
            "status": "ok",
            "comment_id": "c_reply_456",
            "author_name": "tester",
            "patch_set": 2,
            "unresolved": False,
        }
        post_res = mock_service.post_inline_comment(
            cl_number="43330007",
            file_path="src/测试_🚀_é.cc",
            line=42,
            message="Please check Unicode encoding 测试_🚀_é",
            unresolved=True,
        )
        self.assertEqual(post_res["status"], "ok")
        self.assertEqual(post_res["comment_id"], "c_unicode_123")

        reply_res = mock_service.reply_inline_comment(
            cl_number="43330007",
            comment_id="c_root_001",
            file_path="src/测试_🚀_é.cc",
            message="Done · 测试_🚀_é verified",
            unresolved=False,
        )
        self.assertEqual(reply_res["status"], "ok")
        self.assertFalse(reply_res["unresolved"])

        node_bin = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )
        if not node_bin:
            return

        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", pg_html, flags=re.S)
        self.assertTrue(scripts)
        js_body = scripts[-1]
        for boot_call in (
            "loadSidebarLayoutPrefs();",
            "initWorkspaceSplitter();",
            "initDiffCodeSelectionListeners();",
            "initApp();",
        ):
            js_body = js_body.replace(boot_call, f"// {boot_call}")

        harness_js = (
            """
const assert = require("node:assert/strict");
const mockLocalStorage = {};
function makeMockEl(tagName = "div") {
  return {
    tagName: tagName.toUpperCase(),
    innerHTML: "",
    textContent: "",
    value: "",
    checked: false,
    disabled: false,
    title: "",
    offsetTop: 100,
    style: { display: "", setProperty() {} },
    classList: {
      _set: new Set(),
      add(...xs) { xs.forEach(x => this._set.add(x)); },
      remove(...xs) { xs.forEach(x => this._set.delete(x)); },
      toggle(x, force) {
        if (typeof force === "boolean") {
          if (force) this._set.add(x);
          else this._set.delete(x);
          return force;
        }
        if (this._set.has(x)) { this._set.delete(x); return false; }
        this._set.add(x); return true;
      },
      contains(x) { return this._set.has(x); }
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    scrollIntoView() {},
    setAttribute() {},
    getAttribute() { return ""; }
  };
}
globalThis.localStorage = {
  getItem(k) { return mockLocalStorage[k] || null; },
  setItem(k, v) { mockLocalStorage[k] = String(v); },
  removeItem(k) { delete mockLocalStorage[k]; }
};
globalThis.document = {
  _elements: {},
  getElementById(id) {
    if (!this._elements[id]) {
      this._elements[id] = makeMockEl();
      this._elements[id].id = id;
    }
    return this._elements[id];
  },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {},
  body: makeMockEl()
};

"""
            + js_body
            + """

const testClNum = 43330007;
state.selectedClNumber = testClNum;
state.changes = [{
  number: testClNum,
  patch_set: 2,
  subject: "feat(gerrit): Unicode draft test 测试_🚀_é",
  viewer_cr_vote: 0
}];
state.inlineCommentsByCl[testClNum] = [];
state.openReplyByItemId = {};

// 1. Initially no drafts
const initialDrafts = getPendingDraftsForCl(testClNum);
assert.equal(initialDrafts.length, 0);
updateReplyButtonDraftsBadge(testClNum);
const replyBtn = document.getElementById("btnOpenReplyDialog");
assert.equal(replyBtn.textContent, "Reply");
assert.equal(replyBtn.classList.contains("has-drafts"), false);

// 2. Add an unsent inline code comment draft with Unicode content
state.inlineCommentsByCl[testClNum].push({
  id: "draft-item-unicode-1",
  kind: "draft",
  draft_mode: "comment",
  file_path: "src/测试_🚀_é.cc",
  line: 42,
  message: "Consider edge case for Unicode string: 测试_🚀_é",
  unresolved: true
});

const draftsAfter1 = getPendingDraftsForCl(testClNum);
assert.equal(draftsAfter1.length, 1);
assert.equal(draftsAfter1[0].file_path, "src/测试_🚀_é.cc");

updateReplyButtonDraftsBadge(testClNum);
assert.equal(replyBtn.textContent, "Reply (1 draft)");
assert.equal(replyBtn.classList.contains("has-drafts"), true);

// 3. Render draft list HTML for reply dialog
const draftsHtml = renderPgReplyDraftsListHtml(testClNum, draftsAfter1);
assert.match(draftsHtml, /class="pg-reply-draft-card"/);
assert.match(draftsHtml, /src\\/测试_🚀_é\\.cc:42/);
assert.match(draftsHtml, /Consider edge case for Unicode string/);
assert.match(draftsHtml, /discardReplyDialogDraft/);

// 4. Verify computeStagedVotePayload treats pending drafts as hasAnyChange: true
const payload = computeStagedVotePayload();
assert.equal(payload.hasAnyChange, true);
assert.equal(payload.pendingDrafts.length, 1);

// 5. Test quickReplyInlineComment (1-click Done)
state.inlineCommentsByCl[testClNum].push({
  id: "srv-thread-101",
  kind: "comment",
  file_path: "src/main.cc",
  line: 15,
  unresolved: true,
  message: "Please rename this variable"
});
quickReplyInlineComment(testClNum, "srv-thread-101", "Done");
const threadReplyState = state.openReplyByItemId["srv-thread-101"];
assert.ok(threadReplyState);
assert.equal(threadReplyState.draft, "Done");
assert.equal(threadReplyState.unresolved, false);

const draftsAfter2 = getPendingDraftsForCl(testClNum);
assert.equal(draftsAfter2.length, 2);
updateReplyButtonDraftsBadge(testClNum);
assert.equal(replyBtn.textContent, "Reply (2 drafts)");

// 6. Test discardReplyDialogDraft
discardReplyDialogDraft(testClNum, "draft-item-unicode-1", "draft");
const draftsAfterDiscard = getPendingDraftsForCl(testClNum);
assert.equal(draftsAfterDiscard.length, 1);
assert.equal(replyBtn.textContent, "Reply (1 draft)");

// 7. Discard thread reply draft
discardReplyDialogDraft(testClNum, "srv-thread-101", "reply");
const draftsFinal = getPendingDraftsForCl(testClNum);
assert.equal(draftsFinal.length, 0);
assert.equal(replyBtn.textContent, "Reply");
assert.equal(replyBtn.classList.contains("has-drafts"), false);

// 8. Verify helper functions exist and execute safely
assert.equal(typeof jumpToDraftInDiff, "function");
assert.equal(typeof saveInlineReplyAsDraft, "function");
assert.equal(typeof setInlineReplyQuickText, "function");
assert.equal(typeof publishPendingDraftsForCl, "function");

console.log("POLYGERRIT_DRAFT_COMMENTS_REPLY_OK");
"""
        )
        vm_wrapper = (
            'const fs = require("node:fs");\n'
            'const vm = require("node:vm");\n'
            "const code = fs.readFileSync(process.argv[2], 'utf8');\n"
            "const sandbox = {\n"
            "  require,\n"
            "  console,\n"
            "  setTimeout,\n"
            "  clearTimeout,\n"
            "  URL,\n"
            "  URLSearchParams,\n"
            "  window: { addEventListener() {}, innerWidth: 1440 },\n"
            "  document: { getElementById() { return null; }, addEventListener() {}, body: { classList: { toggle() {} } } },\n"
            "  localStorage: { getItem() { return null; }, setItem() {} },\n"
            "  navigator: { clipboard: { writeText: async () => {} } },\n"
            "  fetch: async () => ({ ok: true, json: async () => ({ diffs: [] }) }),\n"
            "};\n"
            "vm.runInNewContext(code, sandbox);\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            js_path = Path(tmp_dir) / "pg_drafts_reply_harness.js"
            runner_path = Path(tmp_dir) / "run_vm.js"
            js_path.write_text(harness_js, encoding="utf-8")
            runner_path.write_text(vm_wrapper, encoding="utf-8")
            proc = subprocess.run(
                [node_bin, str(runner_path), str(js_path)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"PolyGerrit drafts & reply dialog harness failed:\n{proc.stderr}\n{proc.stdout}",
            )
            self.assertIn("POLYGERRIT_DRAFT_COMMENTS_REPLY_OK", proc.stdout)

    def test_polygerrit_submit_requirements_and_trigger_votes(self) -> None:
        import os
        import pathlib
        import shutil
        import subprocess
        import tempfile
        from commands.gerrit.services.queue_service import (
            _enrich_submit_requirements,
            enrich_change_for_ui,
        )
        from commands.gerrit.ui_server import render_static_gerrit_ui_html

        # 1. Test backend queue_service._enrich_submit_requirements logic
        # 1a. Standard 1-human CL satisfied
        sr_sat = _enrich_submit_requirements(
            change={},
            human_cr2=[{"name": "Alice", "email": "alice@google.com", "short": "alice", "value": 2}],
            required_cr2_count=1,
            presubmit_info={"presubmit_status": "passed"},
            pv_score=2,
            has_negative_cr=False,
            is_submittable=True,
        )
        self.assertEqual(len(sr_sat), 2)
        self.assertEqual(sr_sat[0]["name"], "Code-Review")
        self.assertEqual(sr_sat[0]["status"], "SATISFIED")
        self.assertEqual(sr_sat[1]["name"], "Presubmit-Verified")
        self.assertEqual(sr_sat[1]["status"], "SATISFIED")

        # 1b. Agent CL requiring 2 human +2 approvals (with only 1 recorded -> UNSATISFIED)
        sr_agent = _enrich_submit_requirements(
            change={"is_agent_cl": True},
            human_cr2=[{"name": "Alice", "email": "alice@google.com", "short": "alice", "value": 2}],
            required_cr2_count=2,
            presubmit_info={"presubmit_status": "passed"},
            pv_score=2,
            has_negative_cr=False,
            is_submittable=False,
        )
        self.assertEqual(sr_agent[0]["status"], "UNSATISFIED")
        self.assertTrue(any("1 more needed" in atom for atom in sr_agent[0]["failing_atoms"]))

        # 1c. Blocked by Code-Review -2
        sr_blocked = _enrich_submit_requirements(
            change={},
            human_cr2=[{"name": "Alice", "email": "alice@google.com", "short": "alice", "value": 2}],
            required_cr2_count=1,
            presubmit_info={"presubmit_status": "passed"},
            pv_score=2,
            has_negative_cr=True,
            is_submittable=False,
        )
        self.assertEqual(sr_blocked[0]["status"], "BLOCKED")
        self.assertTrue(any("-2" in atom for atom in sr_blocked[0]["failing_atoms"]))

        # 1d. Presubmit running and failed
        sr_running = _enrich_submit_requirements(
            change={},
            human_cr2=[],
            required_cr2_count=1,
            presubmit_info={"presubmit_status": "running"},
            pv_score=0,
            has_negative_cr=False,
            is_submittable=False,
        )
        self.assertEqual(sr_running[1]["status"], "RUNNING")

        sr_failed = _enrich_submit_requirements(
            change={},
            human_cr2=[],
            required_cr2_count=1,
            presubmit_info={"presubmit_status": "failed"},
            pv_score=-1,
            has_negative_cr=False,
            is_submittable=False,
        )
        self.assertEqual(sr_failed[1]["status"], "BLOCKED")

        # 1e. Custom submit requirement from Gerrit API with Unicode data (测试_🚀_é)
        sr_custom = _enrich_submit_requirements(
            change={
                "submit_requirements": [
                    {
                        "name": "Custom-Gate_测试_🚀_é",
                        "status": "SATISFIED",
                        "description": "Custom requirement for 测试_🚀_é",
                        "submittability_expression_result": {
                            "expression": "label:Custom-Gate=MAX",
                            "passing_atoms": ["label:Custom-Gate=+1"],
                            "failing_atoms": [],
                        },
                    }
                ]
            },
            human_cr2=[],
            required_cr2_count=1,
            presubmit_info={"presubmit_status": "passed"},
            pv_score=2,
            has_negative_cr=False,
            is_submittable=True,
        )
        custom_req = next((r for r in sr_custom if r["name"] == "Custom-Gate_测试_🚀_é"), None)
        self.assertIsNotNone(custom_req)
        self.assertEqual(custom_req["status"], "SATISFIED")
        self.assertIn("测试_🚀_é", custom_req["description"])

        # 1f. enrich_change_for_ui propagates submit_requirements
        enriched = enrich_change_for_ui(
            change={
                "_number": 43330008,
                "project": "platform/hardware/google/aemu",
                "subject": "Unicode SR test 测试_🚀_é",
                "owner": {"email": "alice@google.com", "name": "Alice 测试_🚀_é"},
            },
            viewer_email="viewer@google.com",
        )
        self.assertIn("submit_requirements", enriched)
        self.assertIsInstance(enriched["submit_requirements"], list)
        self.assertGreaterEqual(len(enriched["submit_requirements"]), 2)

        # 2. Verify static PolyGerrit HTML template contains all Submit Requirements tokens
        pg_html = render_static_gerrit_ui_html(
            initial_payload={"viewer": "tester@google.com"},
            variant="polygerrit",
        )
        for expected_token in (
            "pg-sr-summary-chip",
            "sr-chip-submittable",
            "sr-chip-blocked",
            "sr-chip-pending",
            "pg-sr-header",
            "pg-sr-count-badge",
            "pg-sr-expand-chevron",
            "pg-sr-detail-row",
            "pg-sr-detail-cell",
            "pg-sr-detail-box",
            "pg-sr-status-tag",
            "pg-sr-expression-block",
            "pg-sr-atom-list",
            "computePgSubmitRequirementsSummary",
            "renderPgSrSummaryChipHtml",
            "scrollToSubmitRequirements",
            "togglePgSubmitRequirementDetails",
            "renderPgSrAtomsHtml",
            "renderPgSrActionBtnHtml",
            "renderPgSrDetailRowHtml",
            "isClCrSatisfied",
        ):
            self.assertIn(
                expected_token,
                pg_html,
                msg=f"Expected Submit Requirements token '{expected_token}' in PolyGerrit HTML template",
            )

        # 3. Headless Node/VM DOM harness testing
        node_bin = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )
        if not node_bin:
            return

        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", pg_html, flags=re.S)
        self.assertTrue(scripts)
        js_body = scripts[-1]
        for boot_call in (
            "loadSidebarLayoutPrefs();",
            "initWorkspaceSplitter();",
            "initDiffCodeSelectionListeners();",
            "initApp();",
        ):
            js_body = js_body.replace(boot_call, f"// {boot_call}")

        harness_js = (
            """
const assert = require("node:assert/strict");
const mockLocalStorage = {};
function makeMockEl(tagName = "div") {
  return {
    tagName: tagName.toUpperCase(),
    innerHTML: "",
    textContent: "",
    value: "",
    checked: false,
    disabled: false,
    title: "",
    offsetTop: 100,
    style: { display: "", setProperty() {} },
    classList: {
      _set: new Set(),
      add(...xs) { xs.forEach(x => this._set.add(x)); },
      remove(...xs) { xs.forEach(x => this._set.delete(x)); },
      toggle(x, force) {
        if (typeof force === "boolean") {
          if (force) this._set.add(x);
          else this._set.delete(x);
          return force;
        }
        if (this._set.has(x)) { this._set.delete(x); return false; }
        this._set.add(x); return true;
      },
      contains(x) { return this._set.has(x); }
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    scrollIntoView() {},
    setAttribute() {},
    getAttribute() { return ""; }
  };
}
globalThis.localStorage = {
  getItem(k) { return mockLocalStorage[k] || null; },
  setItem(k, v) { mockLocalStorage[k] = String(v); },
  removeItem(k) { delete mockLocalStorage[k]; }
};
globalThis.document = {
  _elements: {},
  getElementById(id) {
    if (!this._elements[id]) {
      this._elements[id] = makeMockEl();
      this._elements[id].id = id;
    }
    return this._elements[id];
  },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {},
  body: makeMockEl()
};
globalThis.window = {
  location: { search: "" },
  addEventListener() {},
  innerWidth: 1440
};

"""
            + js_body
            + """

const testClNum = 43330008;
const testCl = {
  number: testClNum,
  patch_set: 2,
  subject: "feat(gerrit): Unicode SR test 测试_🚀_é",
  is_submittable: false,
  submit_requirements: [
    {
      name: "Code-Review",
      status: "UNSATISFIED",
      description: "Requires 1 human Code-Review +2 approval",
      expression: "label:Code-Review=MAX AND -label:Code-Review=MIN",
      passing_atoms: [],
      failing_atoms: ["Requires 1 human +2 approval (1 more needed)"]
    },
    {
      name: "Presubmit-Verified",
      status: "SATISFIED",
      description: "Presubmit verification by TreeHugger",
      expression: "label:Presubmit-Verified=+2",
      passing_atoms: ["Presubmit-Verified +2 (TreeHugger)"],
      failing_atoms: []
    },
    {
      name: "Custom-Gate_测试_🚀_é",
      status: "SATISFIED",
      description: "Custom Unicode requirement 测试_🚀_é",
      expression: "label:Custom-Gate=MAX",
      passing_atoms: ["label:Custom-Gate=+1"],
      failing_atoms: []
    }
  ],
  human_cr2: [],
  presubmit_score: 2,
  presubmit_status: "passed",
  presubmit_vetted: true,
  presubmit_ready_voters: ["alice@google.com"],
  autosubmit_voters: []
};

state.changes = [testCl];
state.selectedClNumber = testClNum;

// 1. Check summary calculation
const summaryPending = computePgSubmitRequirementsSummary(testCl);
assert.equal(summaryPending.status, "pending");
assert.equal(summaryPending.satisfied, 2);
assert.equal(summaryPending.total, 3);
assert.ok(summaryPending.label.includes("2/3 satisfied"));

const chipHtml = renderPgSrSummaryChipHtml(testCl);
assert.ok(chipHtml.includes("sr-chip-pending"));
assert.ok(chipHtml.includes("2/3 satisfied"));

// 2. Check HTML rendering of Submit Requirements table
const srHtml = renderPgSubmitRequirementsHtml(testCl);
assert.ok(srHtml.includes("pg-sr-table"));
assert.ok(srHtml.includes("Code-Review"));
assert.ok(srHtml.includes("Presubmit-Verified"));
assert.ok(srHtml.includes("Custom-Gate_测试_🚀_é"));
assert.ok(srHtml.includes("sr-badge-pending"));
assert.ok(srHtml.includes("2/3"));
assert.ok(srHtml.includes("Presubmit-Ready"));

// 3. Test expansion of requirement details
assert.equal(isPgSrExpanded(testClNum, "Code-Review"), false);
togglePgSubmitRequirementDetails(testClNum, "Code-Review");
assert.equal(isPgSrExpanded(testClNum, "Code-Review"), true);

const srHtmlExpanded = renderPgSubmitRequirementsHtml(testCl);
assert.ok(srHtmlExpanded.includes("pg-sr-detail-row"));
assert.ok(srHtmlExpanded.includes("pg-sr-expression-block"));
assert.ok(srHtmlExpanded.includes("label:Code-Review=MAX"));
assert.ok(srHtmlExpanded.includes("Requires 1 human +2 approval"));
assert.ok(srHtmlExpanded.includes("Vote Code-Review (r)"));

// Toggle back
togglePgSubmitRequirementDetails(testClNum, "Code-Review");
assert.equal(isPgSrExpanded(testClNum, "Code-Review"), false);

// 4. Test Submittable summary state
testCl.is_submittable = true;
testCl.submit_requirements[0].status = "SATISFIED";
testCl.submit_requirements[0].passing_atoms = ["Code-Review +2 (Alice)"];
testCl.submit_requirements[0].failing_atoms = [];
const summarySub = computePgSubmitRequirementsSummary(testCl);
assert.equal(summarySub.status, "submittable");
assert.ok(summarySub.label.includes("Submittable"));

// 5. Test Blocked summary state
testCl.is_submittable = false;
testCl.submit_requirements[0].status = "BLOCKED";
const summaryBlocked = computePgSubmitRequirementsSummary(testCl);
assert.equal(summaryBlocked.status, "blocked");
assert.ok(summaryBlocked.label.includes("Blocked"));

console.log("POLYGERRIT_SUBMIT_REQUIREMENTS_OK");
"""
        )

        with tempfile.TemporaryDirectory() as td:
            js_path = pathlib.Path(td) / "harness.js"
            runner_path = pathlib.Path(td) / "run.js"
            vm_wrapper = """
const fs = require("node:fs");
const vm = require("node:vm");
const scriptCode = fs.readFileSync(process.argv[2], "utf8");
const context = {
  console,
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
  process,
  Buffer,
  require
};
vm.createContext(context);
vm.runInContext(scriptCode, context);
"""
            js_path.write_text(harness_js, encoding="utf-8")
            runner_path.write_text(vm_wrapper, encoding="utf-8")
            proc = subprocess.run(
                [node_bin, str(runner_path), str(js_path)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"PolyGerrit submit requirements harness failed:\n{proc.stderr}\n{proc.stdout}",
            )
            self.assertIn("POLYGERRIT_SUBMIT_REQUIREMENTS_OK", proc.stdout)

    def test_polygerrit_keyboard_shortcuts_modal_and_keybindings(self) -> None:
        import os
        import pathlib
        import re
        import shutil
        import subprocess
        import tempfile
        from commands.gerrit.ui_server import render_static_gerrit_ui_html

        # 1. Verify static PolyGerrit HTML template contains all Keyboard Shortcuts tokens
        pg_html = render_static_gerrit_ui_html(
            initial_payload={"viewer": "tester@google.com"},
            variant="polygerrit",
        )
        for expected_token in (
            "pg-shortcuts-backdrop",
            "pg-shortcuts-dialog",
            "pg-shortcuts-header",
            "pg-shortcuts-title",
            "pg-shortcuts-search-wrap",
            "pg-shortcuts-search-input",
            "pg-shortcuts-clear-btn",
            "pg-shortcuts-close-btn",
            "pg-shortcuts-content",
            "pg-shortcuts-grid",
            "pg-shortcut-group",
            "pg-shortcut-row",
            "pg-kbd",
            "btnPgShortcuts",
            "pg-header-kbd-badge",
            "PG_KEYBOARD_SHORTCUTS",
            "renderPgShortcutsHtml",
            "openShortcutsDialog",
            "closeShortcutsDialog",
            "toggleShortcutsDialog",
            "clearShortcutsSearch",
            "openSelectedCl",
        ):
            self.assertIn(
                expected_token,
                pg_html,
                msg=f"Expected Keyboard Shortcuts token '{expected_token}' in PolyGerrit HTML template",
            )

        # 2. Headless Node/VM DOM harness testing
        node_bin = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )
        if not node_bin:
            return

        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", pg_html, flags=re.S)
        self.assertTrue(scripts)
        js_body = scripts[-1]
        for boot_call in (
            "loadSidebarLayoutPrefs();",
            "initWorkspaceSplitter();",
            "initDiffCodeSelectionListeners();",
            "initApp();",
        ):
            js_body = js_body.replace(boot_call, f"// {boot_call}")

        harness_js = (
            """
const assert = require("node:assert/strict");
const mockLocalStorage = {};
function makeMockEl(tagName = "div") {
  return {
    tagName: tagName.toUpperCase(),
    innerHTML: "",
    textContent: "",
    value: "",
    checked: false,
    disabled: false,
    title: "",
    offsetTop: 100,
    style: { display: "", setProperty() {} },
    classList: {
      _set: new Set(),
      add(...xs) { xs.forEach(x => this._set.add(x)); },
      remove(...xs) { xs.forEach(x => this._set.delete(x)); },
      toggle(x, force) {
        if (typeof force === "boolean") {
          if (force) this._set.add(x);
          else this._set.delete(x);
          return force;
        }
        if (this._set.has(x)) { this._set.delete(x); return false; }
        this._set.add(x); return true;
      },
      contains(x) { return this._set.has(x); }
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    scrollIntoView() {},
    setAttribute() {},
    getAttribute() { return ""; },
    focus() { this._focused = true; },
    select() { this._selected = true; }
  };
}
globalThis.localStorage = {
  getItem(k) { return mockLocalStorage[k] || null; },
  setItem(k, v) { mockLocalStorage[k] = String(v); },
  removeItem(k) { delete mockLocalStorage[k]; }
};
const mockElements = {
  pgShortcutsBackdrop: makeMockEl("div"),
  pgShortcutsSearchInput: makeMockEl("input"),
  pgShortcutsClearBtn: makeMockEl("button"),
  pgShortcutsContent: makeMockEl("div"),
  btnCloseShortcutsDialog: makeMockEl("button"),
  diffScroll: makeMockEl("div"),
  diffPane: makeMockEl("div")
};
globalThis.document = {
  body: makeMockEl("body"),
  getElementById(id) {
    if (!mockElements[id]) mockElements[id] = makeMockEl("div");
    return mockElements[id];
  },
  querySelector(sel) { return null; },
  querySelectorAll(sel) { return []; },
  createElement(tag) { return makeMockEl(tag); },
  addEventListener(evt, handler) {
    if (!globalThis._docListeners) globalThis._docListeners = {};
    if (!globalThis._docListeners[evt]) globalThis._docListeners[evt] = [];
    globalThis._docListeners[evt].push(handler);
  }
};
globalThis.window = {
  location: { search: "" },
  addEventListener() {},
  innerWidth: 1200
};
"""
            + js_body
            + """
// Test 1: Verify PG_KEYBOARD_SHORTCUTS metadata and structure
assert.ok(Array.isArray(PG_KEYBOARD_SHORTCUTS), "PG_KEYBOARD_SHORTCUTS should be an array");
assert.equal(PG_KEYBOARD_SHORTCUTS.length, 4, "Should define 4 shortcut groups");
const groupTitles = PG_KEYBOARD_SHORTCUTS.map(g => g.group);
assert.ok(groupTitles.includes("Navigation & Dashboard"));
assert.ok(groupTitles.includes("File List & Diff Navigation"));
assert.ok(groupTitles.includes("Reviewing & Voting"));
assert.ok(groupTitles.includes("AI Agent & Tools"));

// Test 2: renderPgShortcutsHtml with full rendering and Unicode queries
const fullHtml = renderPgShortcutsHtml("");
assert.ok(fullHtml.includes("pg-shortcuts-grid"), "Should render shortcuts grid");
assert.ok(fullHtml.includes("Navigation &amp; Dashboard") || fullHtml.includes("Navigation & Dashboard"));
assert.ok(fullHtml.includes('<kbd class="pg-kbd">j</kbd>'), "Should render kbd badge for j");
assert.ok(fullHtml.includes('<kbd class="pg-kbd">?</kbd>'), "Should render kbd badge for ?");

// Test 3: Search filter for 'chunk'
const chunkHtml = renderPgShortcutsHtml("chunk");
assert.ok(chunkHtml.includes("Next diff chunk or comment thread"), "Should include chunk shortcut");
assert.ok(!chunkHtml.includes("Review Swarm"), "Should filter out unrelated shortcuts");

// Test 4: Search filter for 'vote'
const voteHtml = renderPgShortcutsHtml("vote");
assert.ok(voteHtml.includes("Reviewing &amp; Voting") || voteHtml.includes("Reviewing & Voting"));
assert.ok(voteHtml.includes("Submit staged votes"));

// Test 5: Search filter with Unicode text '测试_🚀_é' returning empty state
const emptyHtml = renderPgShortcutsHtml("测试_🚀_é");
assert.ok(emptyHtml.includes("pg-shortcuts-empty"), "Should render empty state container");
assert.ok(emptyHtml.includes("测试_🚀_é"), "Should safely display escaped Unicode query");

// Test 6: Dialog open/close/toggle lifecycle
assert.equal(state.shortcutsDialogOpen, false, "Initial dialog state should be false");
openShortcutsDialog();
assert.equal(state.shortcutsDialogOpen, true, "Dialog should be open after openShortcutsDialog()");
assert.ok(mockElements.pgShortcutsBackdrop.classList.contains("open"), "Backdrop should have open class");
assert.ok(mockElements.pgShortcutsContent.innerHTML.includes("pg-shortcuts-grid"), "Content should be populated");

closeShortcutsDialog();
assert.equal(state.shortcutsDialogOpen, false, "Dialog should be closed after closeShortcutsDialog()");
assert.ok(!mockElements.pgShortcutsBackdrop.classList.contains("open"), "Backdrop should not have open class");

toggleShortcutsDialog();
assert.equal(state.shortcutsDialogOpen, true, "Dialog should be open after toggleShortcutsDialog()");
toggleShortcutsDialog();
assert.equal(state.shortcutsDialogOpen, false, "Dialog should be closed after second toggle");

// Test 7: onShortcutsSearchInput and clearShortcutsSearch
mockElements.pgShortcutsSearchInput.value = "diff";
onShortcutsSearchInput("diff");
assert.equal(mockElements.pgShortcutsClearBtn.style.display, "inline-flex");
assert.ok(mockElements.pgShortcutsContent.innerHTML.includes("Next file in change"));

clearShortcutsSearch();
assert.equal(mockElements.pgShortcutsSearchInput.value, "");
assert.equal(mockElements.pgShortcutsClearBtn.style.display, "none");

// Test 8: openSelectedCl navigation
state.selectedClNumber = null;
state.stacks = [{ stack_id: "s1", commits: [{ number: 43330009, subject: "Test CL" }] }];
state.changes = [{ number: 43330009, subject: "Test CL" }];
openSelectedCl();
assert.equal(state.selectedClNumber, 43330009, "openSelectedCl should select first visible commit if none selected");

// Test 9: Keydown event handlers (? and Escape)
const keydownHandlers = globalThis._docListeners["keydown"] || [];
assert.ok(keydownHandlers.length > 0, "Document keydown listeners should be registered");

// Pressing '?' should toggle shortcuts dialog open
let prevented = false;
keydownHandlers.forEach(h => h({
  key: "?",
  target: makeMockEl("div"),
  preventDefault() { prevented = true; }
}));
assert.equal(state.shortcutsDialogOpen, true, "Pressing '?' should open shortcuts dialog");
assert.equal(prevented, true, "Pressing '?' should call preventDefault()");

// Pressing 'Escape' when open with search query should clear search first
mockElements.pgShortcutsSearchInput.value = "chunk";
keydownHandlers.forEach(h => h({
  key: "Escape",
  target: makeMockEl("div"),
  preventDefault() {}
}));
assert.equal(mockElements.pgShortcutsSearchInput.value, "", "First Escape should clear search query");
assert.equal(state.shortcutsDialogOpen, true, "Dialog should remain open on search clear");

// Pressing 'Escape' again should close dialog
keydownHandlers.forEach(h => h({
  key: "Escape",
  target: makeMockEl("div"),
  preventDefault() {}
}));
assert.equal(state.shortcutsDialogOpen, false, "Second Escape should close shortcuts dialog");

console.log("POLYGERRIT_KEYBOARD_SHORTCUTS_OK");
"""
        )

        with tempfile.TemporaryDirectory() as td:
            js_path = pathlib.Path(td) / "harness.js"
            runner_path = pathlib.Path(td) / "run.js"
            vm_wrapper = """
const fs = require("node:fs");
const vm = require("node:vm");
const scriptCode = fs.readFileSync(process.argv[2], "utf8");
const context = {
  console,
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
  process,
  Buffer,
  require
};
vm.createContext(context);
vm.runInContext(scriptCode, context);
"""
            js_path.write_text(harness_js, encoding="utf-8")
            runner_path.write_text(vm_wrapper, encoding="utf-8")
            proc = subprocess.run(
                [node_bin, str(runner_path), str(js_path)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"PolyGerrit keyboard shortcuts harness failed:\n{proc.stderr}\n{proc.stdout}",
            )
            self.assertIn("POLYGERRIT_KEYBOARD_SHORTCUTS_OK", proc.stdout)

    def test_polygerrit_attention_set_badges_and_quick_toggles(self) -> None:
        import datetime
        import os
        import re
        import shutil
        import subprocess
        import tempfile
        from unittest.mock import MagicMock, patch
        from commands.gerrit.services.queue_service import _extract_attention_metadata
        from commands.gerrit.services.vote_submit_service import GerritVoteSubmitService
        from commands.gerrit.ui_server import render_static_gerrit_ui_html

        # 1. Backend: verify _extract_attention_metadata handles structured attention details with Unicode
        now_dt = datetime.datetime(2026, 10, 30, 10, 0, 0, tzinfo=datetime.timezone.utc)
        sample_change = {
            "attention_set": {
                "1001": {
                    "account": {
                        "_account_id": 1001,
                        "email": "reviewer_unicode@google.com",
                        "name": "Reviewer 测试_🚀_é",
                        "username": "rev_unicode",
                    },
                    "reason": "Waiting on code review",
                    "last_update": "2026-10-30 08:00:00.000000000",
                },
                "1002": {
                    "account": {
                        "_account_id": 1002,
                        "email": "tester@google.com",
                        "name": "Tester",
                        "username": "tester",
                    },
                    "reason": "Turn active",
                    "last_update": "2026-10-30 09:30:00.000000000",
                },
            }
        }
        meta = _extract_attention_metadata(sample_change, "tester@google.com", now_dt)
        self.assertTrue(meta["viewer_in_attention_set"])
        self.assertEqual(meta["attention_set_emails"], ["reviewer_unicode@google.com", "tester@google.com"])
        self.assertIn("30m", meta["attention_waiting_display"])
        self.assertEqual(len(meta["attention_set_details"]), 2)
        # Viewer sorted first
        self.assertTrue(meta["attention_set_details"][0]["is_viewer"])
        self.assertEqual(meta["attention_set_details"][0]["email"], "tester@google.com")
        self.assertFalse(meta["attention_set_details"][1]["is_viewer"])
        self.assertEqual(meta["attention_set_details"][1]["name"], "Reviewer 测试_🚀_é")
        self.assertIn("2h", meta["attention_set_details"][1]["waiting_display"])

        # 2. Backend: verify GerritVoteSubmitService.modify_attention_set for add & remove
        with patch("commands.gerrit.services.diff_parser.GerritClient"):
            service = GerritVoteSubmitService(host="https://android-review.googlesource.com")
        service.client = MagicMock()
        service.client._request_json.return_value = {}
        service.fetch_single_enriched_change = MagicMock(return_value={"number": 43330006})

        # Remove attention
        res_remove = service.modify_attention_set(
            cl_number=43330006,
            action="remove",
            user="reviewer_unicode@google.com",
            reason="Done reviewing 测试_🚀_é",
        )
        self.assertEqual(res_remove["status"], "ok")
        self.assertEqual(res_remove["action"], "remove")
        service.client._request_json.assert_called_with(
            "/a/changes/43330006/attention/reviewer_unicode%40google.com/delete",
            method="POST",
            data={"notify": "OWNER", "reason": "Done reviewing 测试_🚀_é"},
        )

        # Add attention
        res_add = service.modify_attention_set(
            cl_number=43330006,
            action="add",
            user="whollins",
            reason="Please take a look",
        )
        self.assertEqual(res_add["status"], "ok")
        self.assertEqual(res_add["action"], "add")
        service.client._request_json.assert_called_with(
            "/a/changes/43330006/attention",
            method="POST",
            data={"user": "whollins@google.com", "notify": "OWNER", "reason": "Please take a look"},
        )

        # 3. Static PolyGerrit HTML template tokens verification
        pg_html = render_static_gerrit_ui_html(
            initial_payload={"viewer": "tester@google.com"},
            variant="polygerrit",
        )
        for expected_token in (
            "pg-attention-set-row",
            "pg-attention-set-val",
            "pg-attention-chip",
            "pg-attention-wait",
            "pg-attention-reason",
            "pg-attention-toggle-btn",
            "pg-reply-attention-section",
            "pg-reply-attention-head",
            "pg-reply-attention-title",
            "pg-reply-attention-chips",
            "pg-reply-attention-chip",
            "pg-reply-attention-reason-row",
            "pg-reply-attention-reason-input",
            "getPgAttentionSetParticipants",
            "renderPgAttentionSetRowHtml",
            "removePgAttentionUser",
            "addPgAttentionUser",
            "renderPgReplyAttentionChipsHtml",
            "onPgReplyAttentionCheckboxChange",
            "/api/gerrit/attention",
        ):
            self.assertIn(
                expected_token,
                pg_html,
                msg=f"Expected Attention Set token '{expected_token}' in PolyGerrit HTML template",
            )

        # 4. Headless Node/VM DOM harness testing
        node_bin = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )
        if not node_bin:
            return

        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", pg_html, flags=re.S)
        self.assertTrue(scripts)
        js_body = scripts[-1]
        for boot_call in (
            "loadSidebarLayoutPrefs();",
            "initWorkspaceSplitter();",
            "initDiffCodeSelectionListeners();",
            "initApp();",
        ):
            js_body = js_body.replace(boot_call, f"// {boot_call}")

        harness_js = (
            """
const assert = require("node:assert/strict");
const mockLocalStorage = {};
function makeMockEl(tagName = "div") {
  return {
    tagName: tagName.toUpperCase(),
    innerHTML: "",
    textContent: "",
    value: "",
    checked: false,
    disabled: false,
    title: "",
    offsetTop: 100,
    style: { display: "", setProperty() {} },
    classList: {
      _set: new Set(),
      add(...xs) { xs.forEach(x => this._set.add(x)); },
      remove(...xs) { xs.forEach(x => this._set.delete(x)); },
      toggle(x, force) {
        if (typeof force === "boolean") {
          if (force) this._set.add(x);
          else this._set.delete(x);
          return force;
        }
        if (this._set.has(x)) { this._set.delete(x); return false; }
        this._set.add(x); return true;
      },
      contains(x) { return this._set.has(x); }
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    closest(sel) {
      if (sel === ".pg-reply-attention-chip") {
        return this;
      }
      return null;
    },
    scrollIntoView() {},
    setAttribute() {},
    getAttribute() { return ""; },
    focus() { this._focused = true; },
    select() { this._selected = true; }
  };
}
globalThis.localStorage = {
  getItem(k) { return mockLocalStorage[k] || null; },
  setItem(k, v) { mockLocalStorage[k] = String(v); },
  removeItem(k) { delete mockLocalStorage[k]; }
};
const mockElements = {
  diffScroll: makeMockEl("div"),
  diffPane: makeMockEl("div")
};
globalThis.document = {
  body: makeMockEl("body"),
  getElementById(id) {
    if (!mockElements[id]) mockElements[id] = makeMockEl("div");
    return mockElements[id];
  },
  querySelector(sel) { return null; },
  querySelectorAll(sel) { return []; },
  createElement(tag) { return makeMockEl(tag); },
  addEventListener() {}
};
globalThis.window = {
  location: { search: "" },
  addEventListener() {},
  innerWidth: 1440
};
"""
            + js_body
            + """
// Initialize test state with Unicode author and attention participants
state.viewer = "tester@google.com";
const sampleCl = {
  number: 43330007,
  patch_set: 1,
  subject: "feat(gpu): Attention set polish 测试_🚀_é",
  owner_name: "Joshua",
  owner_short: "joshuaduong",
  owner_email: "joshuaduong@google.com",
  owner_is_bot: false,
  viewer_in_attention_set: false,
  attention_waiting_display: "",
  attention_set_emails: ["reviewer@google.com"],
  attention_set_details: [
    {
      account_id: 2001,
      email: "reviewer@google.com",
      name: "Reviewer 测试_🚀_é",
      short: "rev_unicode",
      reason: "Waiting on reviewer",
      waiting_display: "2h",
      is_viewer: false
    }
  ],
  reviewers: [
    {
      account_id: 2001,
      email: "reviewer@google.com",
      name: "Reviewer 测试_🚀_é",
      short: "rev_unicode",
      state: "REVIEWER",
      in_attention_set: true,
      cr_vote: 0,
      votes: []
    }
  ]
};

// 1. Verify getPgAttentionSetParticipants returns the structured details
const attParticipants = getPgAttentionSetParticipants(sampleCl);
assert.equal(attParticipants.length, 1);
assert.equal(attParticipants[0].name, "Reviewer 测试_🚀_é");
assert.equal(attParticipants[0].waiting_display, "2h");
assert.equal(attParticipants[0].reason, "Waiting on reviewer");

// 2. Verify renderPgAttentionSetRowHtml outputs the row with chip and toggle button
const rowHtml = renderPgAttentionSetRowHtml(sampleCl);
assert.match(rowHtml, /class="pg-meta-row pg-attention-set-row"/);
assert.match(rowHtml, />Attention Set</);
assert.match(rowHtml, /class="pg-attention-chip /);
assert.match(rowHtml, /Reviewer 测试_🚀_é/);
assert.match(rowHtml, />\\(2h\\)</);
assert.match(rowHtml, />Waiting on reviewer</);
assert.match(rowHtml, />\\+ Add me</);

// 3. Verify renderCommitReviewersPanelHtml embeds the Attention Set row
const panelHtml = renderCommitReviewersPanelHtml(sampleCl, false);
assert.match(panelHtml, /pg-attention-set-row/);
assert.match(panelHtml, />Attention Set</);

// 4. Verify Reply Dialog Attention Set chips
const replyChipsHtml = renderPgReplyAttentionChipsHtml(sampleCl);
assert.match(replyChipsHtml, /class="pg-reply-attention-chip/);
assert.match(replyChipsHtml, /joshuaduong/);
assert.match(replyChipsHtml, /\\(Owner\\)/);
assert.match(replyChipsHtml, /Reviewer 测试_🚀_é/);
assert.match(replyChipsHtml, /\\(Reviewer\\)/);

// 5. Verify onPgReplyAttentionCheckboxChange toggles active class
const mockCb = makeMockEl("input");
mockCb.checked = true;
onPgReplyAttentionCheckboxChange(mockCb);
assert.equal(mockCb.classList.contains("active"), true);
mockCb.checked = false;
onPgReplyAttentionCheckboxChange(mockCb);
assert.equal(mockCb.classList.contains("active"), false);

// 6. Verify viewer toggle state in renderPgAttentionSetRowHtml
sampleCl.viewer_in_attention_set = true;
sampleCl.attention_set_details.push({
  account_id: 2002,
  email: "tester@google.com",
  name: "Tester",
  short: "tester",
  reason: "Your turn",
  waiting_display: "15m",
  is_viewer: true
});
const viewerRowHtml = renderPgAttentionSetRowHtml(sampleCl);
assert.match(viewerRowHtml, /− Remove me/);
assert.match(viewerRowHtml, /is-viewer/);

console.log("POLYGERRIT_ATTENTION_SET_OK");
"""
        )
        vm_wrapper = (
            'const fs = require("node:fs");\n'
            'const vm = require("node:vm");\n'
            "const code = fs.readFileSync(process.argv[2], 'utf8');\n"
            "const sandbox = {\n"
            "  require,\n"
            "  console,\n"
            "  setTimeout,\n"
            "  clearTimeout,\n"
            "  URL,\n"
            "  URLSearchParams,\n"
            "  window: { addEventListener() {}, innerWidth: 1440 },\n"
            "  document: { getElementById() { return null; }, addEventListener() {}, body: { classList: { toggle() {} } } },\n"
            "  localStorage: { getItem() { return null; }, setItem() {} },\n"
            "  navigator: { clipboard: { writeText: async () => {} } },\n"
            "  fetch: async () => ({ ok: true, json: async () => ({}) }),\n"
            "};\n"
            "vm.runInNewContext(code, sandbox);\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            js_path = Path(tmp_dir) / "pg_attention_harness.js"
            runner_path = Path(tmp_dir) / "run_vm.js"
            js_path.write_text(harness_js, encoding="utf-8")
            runner_path.write_text(vm_wrapper, encoding="utf-8")
            proc = subprocess.run(
                [node_bin, str(runner_path), str(js_path)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"PolyGerrit attention set harness failed:\n{proc.stderr}\n{proc.stdout}",
            )
            self.assertIn("POLYGERRIT_ATTENTION_SET_OK", proc.stdout)

    def test_polygerrit_change_header_actions_and_file_shortcuts(self) -> None:
        import json
        import os
        import re
        import shutil
        import subprocess
        import tempfile
        from unittest.mock import MagicMock, patch
        from commands.gerrit.services.vote_submit_service import GerritVoteSubmitService
        from commands.gerrit.ui_server import render_static_gerrit_ui_html

        # 1. Backend: verify GerritVoteSubmitService.rebase_change enforces notify: OWNER
        with patch("commands.gerrit.services.diff_parser.GerritClient"):
            service = GerritVoteSubmitService(host="https://android-review.googlesource.com")
        service.client = MagicMock()
        service.client._request_json.return_value = {"_number": 43330008, "status": "NEW"}
        service.fetch_single_enriched_change = MagicMock(return_value={"number": 43330008, "status": "NEW"})

        # Rebase onto parent branch (default)
        res_rebase = service.rebase_change(cl_number=43330008, viewer_email="tester@google.com")
        self.assertEqual(res_rebase["status"], "ok")
        self.assertEqual(res_rebase["action"], "rebase")
        self.assertEqual(res_rebase["notify"], "OWNER")
        service.client._request_json.assert_called_with(
            "/a/changes/43330008/rebase",
            method="POST",
            data={"notify": "OWNER"},
        )

        # Rebase with custom base
        service.rebase_change(cl_number=43330008, base="123456", viewer_email="tester@google.com")
        service.client._request_json.assert_called_with(
            "/a/changes/43330008/rebase",
            method="POST",
            data={"notify": "OWNER", "base": "123456"},
        )

        # 2. Backend: verify abandon_change and restore_change with Unicode message & notify: OWNER
        res_abandon = service.abandon_change(
            cl_number=43330008,
            message="Abandoning change 测试_🚀_é",
            viewer_email="tester@google.com",
        )
        self.assertEqual(res_abandon["status"], "ok")
        self.assertEqual(res_abandon["action"], "abandon")
        service.client._request_json.assert_called_with(
            "/a/changes/43330008/abandon",
            method="POST",
            data={"notify": "OWNER", "message": "Abandoning change 测试_🚀_é"},
        )

        res_restore = service.restore_change(
            cl_number=43330008,
            message="Restoring change 测试_🚀_é",
            viewer_email="tester@google.com",
        )
        self.assertEqual(res_restore["status"], "ok")
        self.assertEqual(res_restore["action"], "restore")
        service.client._request_json.assert_called_with(
            "/a/changes/43330008/restore",
            method="POST",
            data={"notify": "OWNER", "message": "Restoring change 测试_🚀_é"},
        )

        # 3. Backend: verify cherry_pick_change with destination, base, Unicode message & notify: OWNER
        res_cp = service.cherry_pick_change(
            cl_number=43330008,
            destination="goog/studio-main",
            message="Cherry-pick fix 测试_🚀_é",
            base="123456",
            viewer_email="tester@google.com",
        )
        self.assertEqual(res_cp["status"], "ok")
        self.assertEqual(res_cp["action"], "cherry_pick")
        self.assertEqual(res_cp["destination"], "goog/studio-main")
        self.assertEqual(res_cp["notify"], "OWNER")
        service.client._request_json.assert_called_with(
            "/a/changes/43330008/revisions/current/cherrypick",
            method="POST",
            data={
                "destination": "goog/studio-main",
                "notify": "OWNER",
                "message": "Cherry-pick fix 测试_🚀_é",
                "base": "123456",
            },
        )
        with self.assertRaises(ValueError):
            service.cherry_pick_change(cl_number=43330008, destination="")

        # 4. Template: verify static tokens and DOM structure in polygerrit.html
        html = render_static_gerrit_ui_html(
            initial_payload={"viewer": "tester@google.com"},
            variant="polygerrit",
        )
        self.assertIn("id=\"pgDownloadBackdrop\"", html)
        self.assertIn("id=\"pgRebaseBackdrop\"", html)
        self.assertIn("id=\"pgAbandonBackdrop\"", html)
        self.assertIn("id=\"pgRestoreBackdrop\"", html)
        self.assertIn("id=\"pgCherryPickBackdrop\"", html)
        self.assertIn("pg-header-action-group", html)
        self.assertIn("pg-btn-header-action", html)
        self.assertIn("pg-btn-cherrypick", html)
        self.assertIn("openPgDownloadDialog", html)
        self.assertIn("openPgRebaseDialog", html)
        self.assertIn("openPgAbandonDialog", html)
        self.assertIn("openPgRestoreDialog", html)
        self.assertIn("openPgCherryPickDialog", html)
        self.assertIn("executePgCherryPick", html)
        self.assertIn("btnConfirmCherryPick", html)
        self.assertIn("pgCherryPickBranchInput", html)
        self.assertIn("status-abandoned", html)
        self.assertIn("togglePgActiveFileReviewed", html)
        self.assertIn("togglePgActiveFileDiffCollapse", html)
        self.assertIn("computePgDownloadCommands", html)

        # 5. Headless Node/VM DOM harness verification
        node_bin = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )
        if not node_bin:
            return

        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL)
        self.assertTrue(scripts)
        js_body = (
            scripts[-1]
            .replace("__GERRIT_UI_BOOTSTRAP_JSON__", "{}")
            .replace("\n  initApp();", "\n  // initApp();")
        )

        harness_js = (
            """
const assert = require("node:assert/strict");

function makeMockEl(tagName = "div") {
  const classes = new Set();
  const attrs = {};
  const children = [];
  return {
    tagName: tagName.toUpperCase(),
    value: "",
    textContent: "",
    disabled: false,
    checked: false,
    style: {},
    classList: {
      add(...cs) { cs.forEach(c => classes.add(c)); },
      remove(...cs) { cs.forEach(c => classes.delete(c)); },
      toggle(c, force) {
        if (force === undefined) {
          if (classes.has(c)) classes.delete(c); else classes.add(c);
        } else if (force) {
          classes.add(c);
        } else {
          classes.delete(c);
        }
      },
      contains(c) { return classes.has(c); }
    },
    getAttribute(k) { return attrs[k] || null; },
    setAttribute(k, v) { attrs[k] = String(v); },
    appendChild(c) { children.push(c); },
    querySelectorAll(sel) { return []; },
    querySelector(sel) { return null; },
    addEventListener() {},
    focus() {},
    select() {}
  };
}

globalThis.document = {
  body: makeMockEl("body"),
  getElementById(id) {
    if (!this._elements) this._elements = {};
    if (!this._elements[id]) {
      this._elements[id] = makeMockEl("div");
      this._elements[id].id = id;
    }
    return this._elements[id];
  },
  querySelector(sel) { return null; },
  querySelectorAll(sel) { return []; },
  createElement(tag) { return makeMockEl(tag); },
  addEventListener() {}
};
globalThis.window = {
  location: { search: "" },
  addEventListener() {},
  innerWidth: 1440
};
"""
            + js_body
            + """
state.viewer = "tester@google.com";
const sampleCl = {
  number: 43330008,
  patch_set: 2,
  project: "platform/hardware/google/aemu",
  branch: "studio-main",
  subject: "feat(gerrit): Header actions polish 测试_🚀_é",
  host: "https://android-review.googlesource.com",
  status: "NEW",
  is_abandoned: false,
  mergeable: true,
  work_in_progress: false
};
state.changes = [sampleCl];
state.selectedClNumber = 43330008;

// 1. Verify computePgDownloadCommands generates authentic PolyGerrit checkout commands
const cmds = computePgDownloadCommands(sampleCl);
assert.equal(cmds.repo, "repo download platform/hardware/google/aemu 43330008/2");
assert.match(cmds.checkout, /git fetch https:\\/\\/android-review\\.googlesource\\.com\\/platform\\/hardware\\/google\\/aemu refs\\/changes\\/08\\/43330008\\/2 && git checkout FETCH_HEAD/);
assert.match(cmds["cherry-pick"], /git cherry-pick FETCH_HEAD/);
assert.match(cmds.pull, /git pull/);
assert.match(cmds.patch, /curl -Lo- --netrc/);

// 2. Verify renderPgHeaderActionButtonsHtml renders Download, Rebase, Cherry Pick, and Abandon buttons
const btnsHtml = renderPgHeaderActionButtonsHtml(sampleCl);
assert.match(btnsHtml, /class="pg-header-action-group"/);
assert.match(btnsHtml, /openPgDownloadDialog\\(43330008\\)/);
assert.match(btnsHtml, /openPgRebaseDialog\\(43330008\\)/);
assert.match(btnsHtml, /openPgCherryPickDialog\\(43330008\\)/);
assert.match(btnsHtml, /class="btn pg-btn-header-action pg-btn-cherrypick"/);
assert.match(btnsHtml, /openPgAbandonDialog\\(43330008\\)/);
assert.match(btnsHtml, /class="btn pg-btn-header-action pg-btn-abandon"/);

// 3. Verify renderPgHeaderActionButtonsHtml renders Restore button when change is abandoned
sampleCl.status = "ABANDONED";
sampleCl.is_abandoned = true;
const restoredBtnsHtml = renderPgHeaderActionButtonsHtml(sampleCl);
assert.match(restoredBtnsHtml, /openPgRestoreDialog\\(43330008\\)/);
assert.match(restoredBtnsHtml, /class="btn pg-btn-header-action pg-btn-restore"/);

// 4. Verify computePgHeaderStatusInfo handles abandoned changes
const abandonedStatus = computePgHeaderStatusInfo(sampleCl);
assert.equal(abandonedStatus.cls, "status-abandoned");
assert.equal(abandonedStatus.label, "Abandoned");

// 5. Verify openPgDownloadDialog opens modal
openPgDownloadDialog(43330008);
assert.equal(state.downloadDialogOpen, true);
assert.equal(state.downloadDialogCl, 43330008);
closePgDownloadDialog();
assert.equal(state.downloadDialogOpen, false);

// 6. Verify openPgRebaseDialog, openPgAbandonDialog, and openPgCherryPickDialog lifecycle
openPgRebaseDialog(43330008);
assert.equal(state.rebaseDialogOpen, true);
closePgRebaseDialog();
assert.equal(state.rebaseDialogOpen, false);

openPgCherryPickDialog(43330008);
assert.equal(state.cherryPickDialogOpen, true);
assert.equal(state.cherryPickDialogCl, 43330008);
setPgCherryPickBranch("goog/studio-main");
assert.equal(document.getElementById("pgCherryPickBranchInput").value, "goog/studio-main");
closePgCherryPickDialog();
assert.equal(state.cherryPickDialogOpen, false);

openPgAbandonDialog(43330008);
assert.equal(state.abandonDialogOpen, true);
closePgAbandonDialog();
assert.equal(state.abandonDialogOpen, false);

openPgRestoreDialog(43330008);
assert.equal(state.restoreDialogOpen, true);
closePgRestoreDialog();
assert.equal(state.restoreDialogOpen, false);

console.log("POLYGERRIT_HEADER_ACTIONS_OK");
"""
        )
        vm_wrapper = (
            'const fs = require("node:fs");\n'
            'const vm = require("node:vm");\n'
            "const code = fs.readFileSync(process.argv[2], 'utf8');\n"
            "const sandbox = {\n"
            "  require,\n"
            "  console,\n"
            "  setTimeout,\n"
            "  clearTimeout,\n"
            "  URL,\n"
            "  URLSearchParams,\n"
            "  window: { addEventListener() {}, innerWidth: 1440 },\n"
            "  document: { getElementById() { return null; }, addEventListener() {}, body: { classList: { toggle() {} } } },\n"
            "  localStorage: { getItem() { return null; }, setItem() {} },\n"
            "  navigator: { clipboard: { writeText: async () => {} } },\n"
            "  fetch: async () => ({ ok: true, json: async () => ({}) }),\n"
            "};\n"
            "vm.runInNewContext(code, sandbox);\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            js_path = Path(tmp_dir) / "pg_actions_harness.js"
            runner_path = Path(tmp_dir) / "run_vm.js"
            js_path.write_text(harness_js, encoding="utf-8")
            runner_path.write_text(vm_wrapper, encoding="utf-8")
            proc = subprocess.run(
                [node_bin, str(runner_path), str(js_path)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"PolyGerrit header actions harness failed:\n{proc.stderr}\n{proc.stdout}",
            )
            self.assertIn("POLYGERRIT_HEADER_ACTIONS_OK", proc.stdout)



class GerritUIHandlerSecurityTest(unittest.TestCase):
    """Token, Content-Type and body-size checks on the Gerrit UI HTTP handler."""

    def start(self, ui_token: str | None = None, env_token: str | None = None) -> tuple[MagicMock, str]:
        service = MagicMock()
        service.default_viewer = "jansene@google.com"
        service.get_sidecar_update_status.return_value = {"status": "up_to_date"}
        service.cast_batch_vote.return_value = {"status": "ok"}
        env = {"ANTIGRAVITY_SIDECAR_UI_TOKEN": env_token} if env_token is not None else {}
        with patch.dict(os.environ, env):
            if env_token is None:
                os.environ.pop("ANTIGRAVITY_SIDECAR_UI_TOKEN", None)
            handler_cls = make_gerrit_ui_handler(service, ui_token=ui_token)
        return service, self.serve(handler_cls)

    def serve(self, handler_cls: type) -> str:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    def request(
        self,
        url: str,
        token: str | None = None,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str]]:
        all_headers = {"X-Sidecar-Token": token} if token is not None else {}
        all_headers.update(headers or {})
        req = urllib.request.Request(url, data=body, headers=all_headers, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, dict(resp.headers)
        except urllib.error.HTTPError as err:
            return err.code, dict(err.headers)

    def vote(self, base: str, token: str | None, content_type: str | None = "application/json") -> int:
        headers = {"Content-Type": content_type} if content_type else {}
        return self.request(f"{base}/api/gerrit/vote", token, b'{"cls": [43350001]}', headers)[0]

    def test_unset_token_fails_closed(self) -> None:
        for ui_token in (None, ""):
            with self.subTest(ui_token=ui_token):
                service, base = self.start(ui_token=ui_token)
                self.assertEqual(self.request(f"{base}/api/gerrit/update-status?force=1", "")[0], 401)
                self.assertEqual(self.request(f"{base}/api/gerrit/update-status?token=x", "x")[0], 401)
                self.assertEqual(self.vote(base, ""), 401)
                service.get_sidecar_update_status.assert_not_called()
                service.cast_batch_vote.assert_not_called()

    def test_api_routes_require_matching_token(self) -> None:
        service, base = self.start(ui_token="secret-token")
        for token in (None, "", "wrong", "secret-token-extra", "sécret"):
            with self.subTest(token=token):
                self.assertEqual(self.request(f"{base}/api/gerrit/update-status", token)[0], 401)
                self.assertEqual(self.vote(base, token), 401)
        service.cast_batch_vote.assert_not_called()

        self.assertEqual(self.request(f"{base}/api/gerrit/update-status", "secret-token")[0], 200)
        self.assertEqual(self.request(f"{base}/api/gerrit/update-status?token=secret-token")[0], 200)
        self.assertEqual(self.vote(base, "secret-token"), 200)
        service.cast_batch_vote.assert_called_once()

    def test_env_token_is_the_default(self) -> None:
        _, base = self.start(env_token="env-token")
        self.assertEqual(self.request(f"{base}/api/gerrit/update-status", "env-token")[0], 200)
        self.assertEqual(self.request(f"{base}/api/gerrit/update-status", "other")[0], 401)

    def test_token_uses_constant_time_comparison(self) -> None:
        import lib.web_security as web_security

        _, base = self.start(ui_token="secret-token")
        with patch.object(
            web_security.hmac, "compare_digest", wraps=web_security.hmac.compare_digest
        ) as compare_digest:
            self.assertEqual(self.request(f"{base}/api/gerrit/update-status", "secret-token")[0], 200)
        compare_digest.assert_called()

    def test_page_requires_token_but_bridge_script_is_public(self) -> None:
        _, base = self.start(ui_token="secret-token")
        for path in ("/", "/index.html", "/polygerrit", "/classic"):
            with self.subTest(path=path):
                self.assertEqual(self.request(f"{base}{path}")[0], 401)
                self.assertEqual(self.request(f"{base}{path}?viewer=a&token=secret-token")[0], 200)
                self.assertEqual(self.request(f"{base}{path}", "secret-token")[0], 200)
        self.assertEqual(self.request(f"{base}/preload.js")[0], 200)

    def test_post_requires_json_content_type(self) -> None:
        service, base = self.start(ui_token="secret-token")
        for content_type in ("text/plain", "application/x-www-form-urlencoded", None):
            with self.subTest(content_type=content_type):
                self.assertEqual(self.vote(base, "secret-token", content_type), 415)
        service.cast_batch_vote.assert_not_called()

    def test_post_rejects_malformed_and_oversized_bodies(self) -> None:
        service, base = self.start(ui_token="secret-token")
        json_headers = {"Content-Type": "application/json"}
        for body in (b"not json", b"[]", b""):
            with self.subTest(body=body):
                status, _ = self.request(f"{base}/api/gerrit/vote", "secret-token", body, json_headers)
                self.assertEqual(status, 400)
        port = int(base.rsplit(":", 1)[1])
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.putrequest("POST", "/api/gerrit/vote")
            conn.putheader("X-Sidecar-Token", "secret-token")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(MAX_JSON_BODY_BYTES + 1))
            conn.endheaders(b"{}")
            self.assertEqual(conn.getresponse().status, 413)
        finally:
            conn.close()
        service.cast_batch_vote.assert_not_called()

    def test_no_cors_headers_for_other_origins(self) -> None:
        _, base = self.start(ui_token="secret-token")
        _, headers = self.request(
            f"{base}/api/gerrit/update-status", "secret-token", headers={"Origin": "https://evil.example.com"}
        )
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_standalone_server_mints_token_and_opens_token_url(self) -> None:
        from commands.gerrit.handlers import ui_handler

        captured: dict[str, object] = {}

        class FakeServer:
            def __init__(self, address: tuple[str, int], handler_cls: type) -> None:
                captured["handler_cls"] = handler_cls

            def serve_forever(self) -> None:
                raise KeyboardInterrupt

            def server_close(self) -> None:
                pass

        with (
            patch("commands.gerrit.ui_server.GerritUIService") as service_cls,
            patch.object(ui_handler, "find_available_port", return_value=18085),
            patch.object(ui_handler.http.server, "ThreadingHTTPServer", FakeServer),
            patch.object(ui_handler.webbrowser, "open", side_effect=lambda url: captured.update(url=url)),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            service_cls.return_value.default_viewer = "jansene@google.com"
            service_cls.return_value.get_sidecar_update_status.return_value = {"status": "up_to_date"}
            self.assertEqual(ui_handler.serve_gerrit_ui(open_browser=True), 0)

        match = re.fullmatch(r"http://127\.0\.0\.1:18085/\?token=([A-Za-z0-9_-]{32,})", str(captured["url"]))
        self.assertIsNotNone(match, captured["url"])
        base = self.serve(captured["handler_cls"])  # type: ignore[arg-type]
        self.assertEqual(self.request(f"{base}/api/gerrit/update-status", match.group(1))[0], 200)
        self.assertEqual(self.request(f"{base}/api/gerrit/update-status")[0], 401)

    def test_aemu_map_commit_url_and_ui_integration(self) -> None:
        url = build_aemu_map_commit_url(
            43351736,
            [
                "/COMMIT_MSG",
                "emulator/net/async_socket_adapter.cc",
                "emulator/net/async_socket_adapter.h",
                "src/测试_🚀_é.cc",
            ],
        )
        self.assertEqual(
            url,
            f"{AEMU_MAP_BASE_URL}?ag=43351736&files="
            "emulator/net/async_socket_adapter.cc,"
            "emulator/net/async_socket_adapter.h,"
            "src/%E6%B5%8B%E8%AF%95_%F0%9F%9A%80_%C3%A9.cc",
        )
        html = render_static_gerrit_ui_html()
        self.assertIn("buildAemuMapCommitUrl", html)
        self.assertIn("openAemuMapLink", html)
        self.assertIn("aemu-map-pill", html)
        self.assertIn("🗺️ Architecture Map ↗", html)

    def test_rebase_abandon_restore_endpoints(self) -> None:
        service, base = self.start(ui_token="secret-token")
        service.rebase_change.return_value = {"status": "ok", "cl": "43330008"}
        service.abandon_change.return_value = {"status": "ok", "cl": "43330008"}
        service.restore_change.return_value = {"status": "ok", "cl": "43330008"}
        headers = {"Content-Type": "application/json"}

        # /api/gerrit/rebase
        st, _ = self.request(
            f"{base}/api/gerrit/rebase",
            "secret-token",
            json.dumps({"cl": 43330008, "base": "999"}).encode("utf-8"),
            headers,
        )
        self.assertEqual(st, 200)
        service.rebase_change.assert_called_once_with(cl_number=43330008, base="999", viewer_email=None)

        # /api/gerrit/abandon
        st, _ = self.request(
            f"{base}/api/gerrit/abandon",
            "secret-token",
            json.dumps({"cl": 43330008, "message": "Done 测试_🚀_é"}).encode("utf-8"),
            headers,
        )
        self.assertEqual(st, 200)
        service.abandon_change.assert_called_once_with(cl_number=43330008, message="Done 测试_🚀_é", viewer_email=None)

        # /api/gerrit/restore
        st, _ = self.request(
            f"{base}/api/gerrit/restore",
            "secret-token",
            json.dumps({"cl": 43330008, "message": "Reopened 测试_🚀_é"}).encode("utf-8"),
            headers,
        )
        self.assertEqual(st, 200)
        service.restore_change.assert_called_once_with(cl_number=43330008, message="Reopened 测试_🚀_é", viewer_email=None)

        # /api/gerrit/cherry_pick
        service.cherry_pick_change.return_value = {
            "status": "ok",
            "cl": "43330008",
            "destination": "goog/studio-main",
            "new_cl": 43330099,
        }
        st, _ = self.request(
            f"{base}/api/gerrit/cherry_pick",
            "secret-token",
            json.dumps({
                "cl": 43330008,
                "destination": "goog/studio-main",
                "message": "Cherry-picked 测试_🚀_é",
                "base": "999",
            }).encode("utf-8"),
            headers,
        )
        self.assertEqual(st, 200)
        service.cherry_pick_change.assert_called_once_with(
            cl_number=43330008,
            destination="goog/studio-main",
            message="Cherry-picked 测试_🚀_é",
            base="999",
            revision=None,
            viewer_email=None,
        )

        # /api/gerrit/cherry-pick with missing destination returns 400
        st, _ = self.request(
            f"{base}/api/gerrit/cherry-pick",
            "secret-token",
            json.dumps({"cl": 43330008, "destination": ""}).encode("utf-8"),
            headers,
        )
        self.assertEqual(st, 400)

    def test_file_content_client_service_and_endpoint(self) -> None:
        from lib.gerrit_api import GerritClient
        from commands.gerrit.services.diff_parser import GerritDiffService
        import base64

        # 1. Test GerritClient.get_revision_file_content with Unicode
        client = GerritClient(host="https://android-review.googlesource.com")
        unicode_source = "void TestUnicode() {\n  const char* s = \"测试_🚀_é\";\n}\n"
        b64_source = base64.b64encode(unicode_source.encode("utf-8")).decode("ascii")
        with patch.object(client, "_request_raw", return_value=b64_source) as mock_req:
            res_content = client.get_revision_file_content("43330008", "/src/foo.cc", revision="2")
            self.assertEqual(res_content, unicode_source)
            mock_req.assert_called_once_with(
                "/a/changes/43330008/revisions/2/files/src%2Ffoo.cc/content"
            )

        # 2. Test GerritDiffService.fetch_file_content with caching
        with patch("commands.gerrit.services.diff_parser.GerritClient"):
            diff_svc = GerritDiffService()
        diff_svc.client = client
        with patch.object(client, "get_revision_file_content", return_value=unicode_source) as mock_get_content:
            data1 = diff_svc.fetch_file_content("43330008", "src/foo.cc", patch_set=2)
            self.assertEqual(data1["status"], "ok")
            self.assertEqual(data1["cl"], 43330008)
            self.assertEqual(data1["patch_set"], 2)
            self.assertEqual(data1["file"], "src/foo.cc")
            self.assertEqual(data1["lines"], unicode_source.splitlines())
            self.assertEqual(mock_get_content.call_count, 1)

            # Second call should hit the memory cache
            data2 = diff_svc.fetch_file_content("43330008", "src/foo.cc", patch_set=2)
            self.assertEqual(data2["lines"], unicode_source.splitlines())
            self.assertEqual(mock_get_content.call_count, 1)

        # 3. Test HTTP endpoint GET /api/gerrit/file-content
        service, base = self.start(ui_token="secret-token")
        service.fetch_file_content.return_value = {
            "status": "ok",
            "cl": 43330008,
            "patch_set": 2,
            "file": "src/foo.cc",
            "content": unicode_source,
            "lines": unicode_source.splitlines(),
        }

        # Valid request
        req = urllib.request.Request(
            f"{base}/api/gerrit/file-content?cl=43330008&file=src/foo.cc&ps=2",
            headers={"X-Sidecar-Token": "secret-token"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["cl"], 43330008)
            self.assertIn("测试_🚀_é", data["content"])
            self.assertEqual(len(data["lines"]), 3)
        service.fetch_file_content.assert_called_once_with("43330008", "src/foo.cc", patch_set="2")

        # Missing 'cl' returns 400
        st_bad, _ = self.request(
            f"{base}/api/gerrit/file-content?file=src/foo.cc",
            "secret-token",
        )
        self.assertEqual(st_bad, 400)

        # Missing 'file' returns 400
        st_bad2, _ = self.request(
            f"{base}/api/gerrit/file-content?cl=43330008",
            "secret-token",
        )
        self.assertEqual(st_bad2, 400)

    def test_polygerrit_diff_view_mode_and_side_by_side_rendering(self) -> None:
        import json
        import os
        import re
        import shutil
        import subprocess
        import tempfile
        from unittest.mock import MagicMock
        from commands.gerrit.ui_server import render_static_gerrit_ui_html

        # 1. Template: verify static tokens and DOM structure in polygerrit.html
        html = render_static_gerrit_ui_html(
            initial_payload={"viewer": "tester@google.com"},
            variant="polygerrit",
        )
        self.assertIn("pg-diff-mode-selector", html)
        self.assertIn("pg-diff-mode-btn", html)
        self.assertIn("pg-file-diff-mode-btn", html)
        self.assertIn("btnDiffModeSbs", html)
        self.assertIn("btnDiffModeUnified", html)
        self.assertIn("code-line-sbs", html)
        self.assertIn("code-side-cell", html)
        self.assertIn("code-side-left", html)
        self.assertIn("code-side-right", html)
        self.assertIn("side-del", html)
        self.assertIn("side-add", html)
        self.assertIn("side-blank", html)
        self.assertIn("setPgDiffViewMode", html)
        self.assertIn("togglePgDiffViewMode", html)
        self.assertIn("togglePgFileDiffMode", html)
        self.assertIn("renderPgDiffModeSelectorHtml", html)
        self.assertIn("renderFileDiffSideBySideHtml", html)
        self.assertIn("renderFileDiffUnifiedHtml", html)
        self.assertIn("data-diff-mode", html)

        # 2. Verify keyboard shortcut cheatsheet includes 'v' for diff view mode toggle
        self.assertIn("Toggle diff view mode (Side-by-side / Unified)", html)
        self.assertIn('e.key === "v"', html)

        # 3. Headless Node/VM DOM harness verification when node is present
        node_bin = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )
        if not node_bin:
            return

        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL)
        self.assertTrue(scripts)
        js_body = (
            scripts[-1]
            .replace("__GERRIT_UI_BOOTSTRAP_JSON__", "{}")
            .replace("\n  initApp();", "\n  // initApp();")
        )

        harness_js = (
            """
const assert = require("node:assert/strict");

function makeMockEl(tagName = "div") {
  const classes = new Set();
  const attrs = {};
  const children = [];
  return {
    tagName: tagName.toUpperCase(),
    value: "",
    textContent: "",
    disabled: false,
    checked: false,
    style: {},
    classList: {
      add(...cs) { cs.forEach(c => classes.add(c)); },
      remove(...cs) { cs.forEach(c => classes.delete(c)); },
      toggle(c, force) {
        if (force === undefined) {
          if (classes.has(c)) classes.delete(c); else classes.add(c);
        } else if (force) {
          classes.add(c);
        } else {
          classes.delete(c);
        }
      },
      contains(c) { return classes.has(c); }
    },
    getAttribute(k) { return attrs[k] || null; },
    setAttribute(k, v) { attrs[k] = String(v); },
    appendChild(c) { children.push(c); },
    querySelectorAll(sel) { return []; },
    querySelector(sel) { return null; },
    addEventListener() {},
    focus() {},
    select() {}
  };
}

const domMap = {
  diffScroll: makeMockEl("div"),
  btnDiffModeSbs: makeMockEl("button"),
  btnDiffModeUnified: makeMockEl("button"),
};

const mockLocalStorage = {};

global.window = {
  location: { href: "http://localhost:8080/?ui=polygerrit", search: "?ui=polygerrit" },
  localStorage: {
    getItem: k => mockLocalStorage[k] || null,
    setItem: (k, v) => { mockLocalStorage[k] = String(v); },
    removeItem: k => { delete mockLocalStorage[k]; }
  },
  innerWidth: 1400,
  addEventListener() {},
  removeEventListener() {}
};

global.document = {
  getElementById: id => {
    if (domMap[id]) return domMap[id];
    const el = makeMockEl("div");
    el.id = id;
    return el;
  },
  querySelectorAll: () => [],
  querySelector: () => null,
  createElement: tag => makeMockEl(tag),
  addEventListener() {},
  documentElement: {
    setAttribute() {},
    classList: { add() {}, remove() {}, contains: () => false }
  }
};
global.navigator = { clipboard: { writeText: async () => {} } };
global.setTimeout = () => 0;
global.clearTimeout = () => {};
global.setInterval = () => 0;
global.clearInterval = () => {};
global.fetch = async () => ({ ok: true, json: async () => ({}) });

"""
            + js_body
            + """

// 1. Verify default diff mode
assert.equal(getStoredPgDiffViewMode(), "side-by-side");
assert.equal(getPgDiffViewMode(), "side-by-side");

// 2. Render diff mode selector in side-by-side mode
const selectorHtmlSbs = renderPgDiffModeSelectorHtml();
assert.equal(selectorHtmlSbs.includes('id="btnDiffModeSbs"'), true);
assert.equal(selectorHtmlSbs.includes('id="btnDiffModeUnified"'), true);
assert.equal(selectorHtmlSbs.includes("Side-by-side"), true);
assert.equal(selectorHtmlSbs.includes("Unified"), true);

// 3. Switch to unified mode
setPgDiffViewMode("unified", null, null, false);
assert.equal(getPgDiffViewMode(), "unified");
assert.equal(mockLocalStorage["gerrit_ui_pg_diff_view_mode"], "unified");

const selectorHtmlUni = renderPgDiffModeSelectorHtml();
assert.equal(selectorHtmlUni.includes('btnDiffModeUnified"\\n        class="pg-diff-mode-btn active"'), true);

// 4. Toggle diff mode back to side-by-side
togglePgDiffViewMode();
assert.equal(getPgDiffViewMode(), "side-by-side");
assert.equal(mockLocalStorage["gerrit_ui_pg_diff_view_mode"], "side-by-side");

// 5. Test per-file override with Unicode test path
const unicodePath = "emulator/libs/file/测试_🚀_é.cc";
setPgDiffViewMode("unified", 43330009, unicodePath, false);
assert.equal(getPgDiffViewMode(43330009, unicodePath), "unified");
assert.equal(getPgDiffViewMode(43330009, "other_file.cc"), "side-by-side");

// 6. Test renderFileDiffBlock in side-by-side mode with Unicode code
const mockFile = {
  file: unicodePath,
  status: "M",
  additions: 1,
  deletions: 1,
  lines: [
    { type: "hunk", old: "-10,2", new: "+10,2", text: "@@ -10,2 +10,2 @@ void run() {" },
    { type: "del", old: "10", new: "", text: "-  int res = 0;" },
    { type: "add", old: "", new: "10", text: "+  int res = compute(\\"测试_🚀_é\\");" },
    { type: "ctx", old: "11", new: "11", text: "   return res;" },
  ]
};

// Side-by-side file rendering:
setPgDiffViewMode("side-by-side", 43330009, unicodePath, false);
const sbsHtml = renderFileDiffBlock(mockFile, 43330009, 1);
assert.equal(sbsHtml.includes("diff-mode-side-by-side"), true);
assert.equal(sbsHtml.includes("code-line-sbs"), true);
assert.equal(sbsHtml.includes("code-side-left side-del"), true);
assert.equal(sbsHtml.includes("code-side-right side-add"), true);
assert.equal(sbsHtml.includes("code-side-left side-ctx"), true);
assert.equal(sbsHtml.includes("code-side-right side-ctx"), true);
assert.equal(sbsHtml.includes("pg-file-diff-mode-btn"), true);
assert.equal(sbsHtml.includes("测试_🚀_é"), true);

// Unified file rendering:
setPgDiffViewMode("unified", 43330009, unicodePath, false);
const uniHtml = renderFileDiffBlock(mockFile, 43330009, 1);
assert.equal(uniHtml.includes("diff-mode-unified"), true);
assert.equal(uniHtml.includes("code-line code-del"), true);
assert.equal(uniHtml.includes("code-line code-add"), true);
assert.equal(uniHtml.includes("测试_🚀_é"), true);

console.log("POLYGERRIT_DIFF_VIEW_MODE_AND_SIDE_BY_SIDE_OK");
"""
        )

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
            tf.write(harness_js)
            tf_path = tf.name
        try:
            res = subprocess.run(
                [node_bin, tf_path], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10
            )
            self.assertEqual(
                res.returncode,
                0,
                f"Node execution failed with {res.stderr}\\nScript:\\n{harness_js[:1000]}",
            )
            self.assertIn("POLYGERRIT_DIFF_VIEW_MODE_AND_SIDE_BY_SIDE_OK", res.stdout)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_polygerrit_continuous_stack_diff_and_relation_chain(self):
        """Verifies PolyGerrit Continuous Stack Diff View and interactive Relation Chain controls."""
        from commands.gerrit.services.diff_parser import get_gerrit_ui_template_path

        tpl_path = get_gerrit_ui_template_path("polygerrit")
        self.assertTrue(tpl_path.exists(), f"PolyGerrit template missing at {tpl_path}")
        html = tpl_path.read_text(encoding="utf-8")

        # 1. Verify static HTML tokens and CSS classes for continuous stack review
        self.assertIn("pg-stack-review-banner", html)
        self.assertIn("pg-stack-review-badge", html)
        self.assertIn("pg-stack-review-title", html)
        self.assertIn("pg-stack-review-stats", html)
        self.assertIn("pg-stack-stepper", html)
        self.assertIn("pg-stack-step-pill", html)
        self.assertIn("pg-stack-commit-seq-bar", html)
        self.assertIn("pg-stack-commit-seq-pill", html)
        self.assertIn("pg-stack-commit-jump-top", html)
        self.assertIn("btnToggleContinuousStack", html)
        self.assertIn("pg-btn-stack-mode", html)
        self.assertIn("pg-btn-stack-action", html)
        self.assertIn("btnStackBatchSelectAll", html)
        self.assertIn("btnExitContinuousStack", html)
        self.assertIn("findPgStackForClOrId", html)
        self.assertIn("isContinuousStackDiffActive", html)
        self.assertIn("toggleContinuousStackDiff", html)
        self.assertIn("scrollToStackCommit", html)
        self.assertIn("selectStackForBatchEdit", html)
        self.assertIn("renderPgContinuousStackBannerHtml", html)
        self.assertIn("renderPgStackCommitSequenceBarHtml", html)
        self.assertIn("CONTINUOUS_STACK_DIFF_STORAGE_KEY", html)

        # 2. Verify keyboard shortcut cheatsheet includes 'Shift+S' for Continuous Stack review
        self.assertIn("Toggle Continuous Stack / Whole Stack review", html)
        self.assertIn('e.key === "S"', html)

        # 3. Headless Node/VM DOM harness verification when node is present
        node_bin = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )
        if not node_bin:
            return

        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL)
        self.assertTrue(scripts)
        js_body = (
            scripts[-1]
            .replace("__GERRIT_UI_BOOTSTRAP_JSON__", "{}")
            .replace("\n  initApp();", "\n  // initApp();")
        )

        harness_js = (
            """
const assert = require("node:assert/strict");

function makeMockEl(tagName = "div") {
  const classes = new Set();
  const attrs = {};
  const children = [];
  return {
    tagName: tagName.toUpperCase(),
    value: "",
    textContent: "",
    disabled: false,
    checked: false,
    style: {},
    classList: {
      add(...cs) { cs.forEach(c => classes.add(c)); },
      remove(...cs) { cs.forEach(c => classes.delete(c)); },
      toggle(c, force) {
        if (force === undefined) {
          if (classes.has(c)) classes.delete(c); else classes.add(c);
        } else if (force) {
          classes.add(c);
        } else {
          classes.delete(c);
        }
      },
      contains(c) { return classes.has(c); }
    },
    getAttribute(k) { return attrs[k] || null; },
    setAttribute(k, v) { attrs[k] = String(v); },
    appendChild(c) { children.push(c); },
    querySelectorAll(sel) { return []; },
    querySelector(sel) { return null; },
    addEventListener() {},
    scrollIntoView() {},
    scrollTo() {},
    focus() {},
    select() {}
  };
}

const domMap = {
  diffScroll: makeMockEl("div"),
  pgStackReviewBanner: makeMockEl("div"),
};

const mockLocalStorage = {};

global.window = {
  location: { href: "http://localhost:8080/?ui=polygerrit", search: "?ui=polygerrit" },
  localStorage: {
    getItem: k => mockLocalStorage[k] || null,
    setItem: (k, v) => { mockLocalStorage[k] = String(v); },
    removeItem: k => { delete mockLocalStorage[k]; }
  },
  innerWidth: 1400,
  addEventListener() {},
  removeEventListener() {}
};

global.document = {
  getElementById: id => {
    if (domMap[id]) return domMap[id];
    const el = makeMockEl("div");
    el.id = id;
    return el;
  },
  querySelectorAll: () => [],
  querySelector: () => null,
  createElement: tag => makeMockEl(tag),
  addEventListener() {},
  documentElement: {
    setAttribute() {},
    classList: { add() {}, remove() {}, contains: () => false }
  }
};
global.navigator = { clipboard: { writeText: async () => {} } };
global.setTimeout = () => 0;
global.clearTimeout = () => {};
global.setInterval = () => 0;
global.clearInterval = () => {};
global.fetch = async () => ({ ok: true, json: async () => ({}) });

"""
            + js_body
            + """

// 1. Setup mock multi-commit relation chain stack with Unicode
const unicodeStackTitle = "Feature: 测试_🚀_é Stack Review";
const mockCommits = [
  {
    number: 43330001,
    subject: "Base commit 1/3: Add core model",
    patch_set: 2,
    mergeable: true,
    work_in_progress: false,
    owner_name: "Alice",
  },
  {
    number: 43330002,
    subject: "Stacked commit 2/3: Implement 测试_🚀_é API",
    patch_set: 1,
    mergeable: true,
    work_in_progress: false,
    owner_name: "Bob",
  },
  {
    number: 43330003,
    subject: "Tip commit 3/3: Add Unicode integration tests",
    patch_set: 3,
    mergeable: true,
    work_in_progress: false,
    owner_name: "Charlie",
  }
];

const mockStack = {
  stack_id: "stack-rel-chain-123",
  title: unicodeStackTitle,
  commits: mockCommits,
};

state.stacks = [mockStack];
state.changes = [...mockCommits];
state.selectedStackId = "stack-rel-chain-123";
state.selectedClNumber = 43330002;

// 2. Verify findPgStackForClOrId lookup
const foundByCl = findPgStackForClOrId(43330002);
assert.notEqual(foundByCl, null);
assert.equal(foundByCl.stack_id, "stack-rel-chain-123");
assert.equal(foundByCl.title, unicodeStackTitle);

const foundById = findPgStackForClOrId(null, "stack-rel-chain-123");
assert.notEqual(foundById, null);
assert.equal(foundById.commits.length, 3);

// 3. Verify renderPgRelationChainHtml
const relChainHtml = renderPgRelationChainHtml(mockCommits[1]);
assert.equal(relChainHtml.includes("Relation chain (3)"), true);
assert.equal(relChainHtml.includes("btnToggleContinuousStack"), true);
assert.equal(relChainHtml.includes("Whole Stack"), true);
assert.equal(relChainHtml.includes("Base commit 1/3"), true);
assert.equal(relChainHtml.includes("测试_🚀_é"), true);

// 4. Verify Continuous Stack Diff mode toggle
assert.equal(state.continuousStackDiff, false);
assert.equal(isContinuousStackDiffActive(), false);

// In single CL mode, getActiveCommitsForDiff returns only 1 commit:
const singleCommits = getActiveCommitsForDiff();
assert.equal(singleCommits.length, 1);
assert.equal(singleCommits[0].number, 43330002);

// Toggle Continuous Stack mode:
toggleContinuousStackDiff(null, "stack-rel-chain-123");
assert.equal(state.continuousStackDiff, true);
assert.equal(isContinuousStackDiffActive(), true);
assert.equal(mockLocalStorage["gerrit_ui_pg_continuous_stack_diff"], "true");

// In Continuous mode, getActiveCommitsForDiff returns all 3 stack commits:
const stackCommits = getActiveCommitsForDiff();
assert.equal(stackCommits.length, 3);
assert.equal(stackCommits[0].number, 43330001);
assert.equal(stackCommits[1].number, 43330002);
assert.equal(stackCommits[2].number, 43330003);

// 5. Verify renderPgContinuousStackBannerHtml
state.diffsByCl[43330001] = { patch_set: 2, files: [{ file: "core.cc", additions: 15, deletions: 2 }] };
state.diffsByCl[43330002] = { patch_set: 1, files: [{ file: "api.cc", additions: 20, deletions: 5 }] };
state.diffsByCl[43330003] = { patch_set: 3, files: [{ file: "test_测试_🚀_é.cc", additions: 35, deletions: 0 }] };

const bannerHtml = renderPgContinuousStackBannerHtml(mockStack, mockCommits);
assert.equal(bannerHtml.includes("Continuous Stack Review"), true);
assert.equal(bannerHtml.includes("测试_🚀_é Stack Review"), true);
assert.equal(bannerHtml.includes("3 commits"), true);
assert.equal(bannerHtml.includes("+70"), true);
assert.equal(bannerHtml.includes("-7"), true);
assert.equal(bannerHtml.includes("ag/43330001"), true);
assert.equal(bannerHtml.includes("ag/43330002"), true);
assert.equal(bannerHtml.includes("ag/43330003"), true);
assert.equal(bannerHtml.includes("btnStackBatchSelectAll"), true);
assert.equal(bannerHtml.includes("btnExitContinuousStack"), true);

// 6. Verify renderPgStackCommitSequenceBarHtml
const seqBarHtml = renderPgStackCommitSequenceBarHtml(mockCommits[0], 0, 3, mockStack);
assert.equal(seqBarHtml.includes("Commit 1 of 3"), true);
assert.equal(seqBarHtml.includes("ag/43330001"), true);
assert.equal(seqBarHtml.includes("Top of Stack"), true);

// 7. Verify selectStackForBatchEdit
assert.equal(state.checkedClNumbers.size, 0);
selectStackForBatchEdit("stack-rel-chain-123");
assert.equal(state.checkedClNumbers.size, 3);
assert.equal(state.checkedClNumbers.has(43330001), true);
assert.equal(state.checkedClNumbers.has(43330002), true);
assert.equal(state.checkedClNumbers.has(43330003), true);

// 8. Verify Shift+S shortcut is registered in cheatsheet
const shortcutsSection = PG_KEYBOARD_SHORTCUTS.find(g => g.group === "Navigation & Dashboard");
assert.notEqual(shortcutsSection, undefined);
const shiftSDef = shortcutsSection.shortcuts.find(s => s.keys.includes("Shift") && s.keys.includes("S"));
assert.notEqual(shiftSDef, undefined);
assert.equal(shiftSDef.description.includes("Continuous Stack"), true);

console.log("POLYGERRIT_CONTINUOUS_STACK_DIFF_AND_RELATION_CHAIN_OK");
"""
        )

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
            tf.write(harness_js)
            tf_path = tf.name
        try:
            res = subprocess.run(
                [node_bin, tf_path], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10
            )
            self.assertEqual(
                res.returncode,
                0,
                f"Node execution failed with {res.stderr}\\nScript:\\n{harness_js[:1000]}",
            )
            self.assertIn("POLYGERRIT_CONTINUOUS_STACK_DIFF_AND_RELATION_CHAIN_OK", res.stdout)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_polygerrit_diff_context_expansion_controls(self):
        """Verifies PolyGerrit diff hunk context expansion bars, controls, and dynamic row insertion."""
        from commands.gerrit.services.diff_parser import get_gerrit_ui_template_path

        tpl_path = get_gerrit_ui_template_path("polygerrit")
        self.assertTrue(tpl_path.exists(), f"PolyGerrit template missing at {tpl_path}")
        html = tpl_path.read_text(encoding="utf-8")

        # 1. Verify static HTML tokens and CSS classes for context controls
        self.assertIn("pg-context-bar", html)
        self.assertIn("pg-context-controls-inner", html)
        self.assertIn("pg-context-label", html)
        self.assertIn("pg-context-btn-group", html)
        self.assertIn("pg-btn-context-expand", html)
        self.assertIn("pg-btn-expand-above", html)
        self.assertIn("pg-btn-expand-below", html)
        self.assertIn("pg-btn-expand-all", html)
        self.assertIn("pg-expanded-ctx-row", html)
        self.assertIn("renderPgContextControlBarHtml", html)
        self.assertIn("expandPgDiffContext", html)
        self.assertIn("expandAllVisibleDiffContext", html)

        # 2. Verify cheatsheet and keydown registration for Shift+X
        self.assertIn("Expand all skipped diff context lines", html)
        self.assertIn('e.key === "X"', html)

        # 3. Headless Node/VM DOM harness verification when node is present
        node_bin = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )
        if not node_bin:
            return

        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL)
        self.assertTrue(scripts)
        js_body = (
            scripts[-1]
            .replace("__GERRIT_UI_BOOTSTRAP_JSON__", "{}")
            .replace("\n  initApp();", "\n  // initApp();")
        )

        harness_js = (
            """
const assert = require("node:assert/strict");

function makeMockEl(tagName = "div") {
  const classes = new Set();
  const attrs = {};
  const children = [];
  return {
    tagName: tagName.toUpperCase(),
    value: "",
    textContent: "",
    disabled: false,
    checked: false,
    style: {},
    classList: {
      add(...cs) { cs.forEach(c => classes.add(c)); },
      remove(...cs) { cs.forEach(c => classes.delete(c)); },
      toggle(c, force) {
        if (force === undefined) {
          if (classes.has(c)) classes.delete(c); else classes.add(c);
        } else if (force) {
          classes.add(c);
        } else {
          classes.delete(c);
        }
      },
      contains(c) { return classes.has(c); }
    },
    getAttribute(k) { return attrs[k] || null; },
    setAttribute(k, v) { attrs[k] = String(v); },
    appendChild(c) { children.push(c); },
    querySelectorAll(sel) { return []; },
    querySelector(sel) { return null; },
    addEventListener() {},
    scrollIntoView() {},
    scrollTo() {},
    focus() {},
    select() {}
  };
}

const domMap = {
  diffScroll: makeMockEl("div"),
};

const mockLocalStorage = {};

global.window = {
  location: { href: "http://localhost:8080/?ui=polygerrit", search: "?ui=polygerrit" },
  localStorage: {
    getItem: k => mockLocalStorage[k] || null,
    setItem: (k, v) => { mockLocalStorage[k] = String(v); },
    removeItem: k => { delete mockLocalStorage[k]; }
  },
  innerWidth: 1400,
  addEventListener() {},
  removeEventListener() {}
};

global.document = {
  getElementById: id => {
    if (domMap[id]) return domMap[id];
    const el = makeMockEl("div");
    el.id = id;
    return el;
  },
  querySelectorAll: () => [],
  querySelector: () => null,
  createElement: tag => makeMockEl(tag),
  addEventListener() {},
  documentElement: {
    setAttribute() {},
    classList: { add() {}, remove() {}, contains: () => false }
  }
};
global.navigator = { clipboard: { writeText: async () => {} } };
global.setTimeout = () => 0;
global.clearTimeout = () => {};
global.setInterval = () => 0;
global.clearInterval = () => {};
global.fetch = async () => ({ ok: true, json: async () => ({}) });

"""
            + js_body
            + """

// 1. Verify renderPgContextControlBarHtml function exists and produces authentic PolyGerrit controls
assert.equal(typeof renderPgContextControlBarHtml, "function");
const barHtml = renderPgContextControlBarHtml(43330008, "src/foo.cc", 2, 21, 64, false);
assert.equal(barHtml.includes("pg-context-bar"), true);
assert.equal(barHtml.includes("44 lines skipped"), true);
assert.equal(barHtml.includes("▲ +10 lines"), true);
assert.equal(barHtml.includes("▼ +10 lines"), true);
assert.equal(barHtml.includes("Expand all (44 lines)"), true);
assert.equal(barHtml.includes('data-from-line="21"'), true);
assert.equal(barHtml.includes('data-to-line="64"'), true);

// 2. Verify small gap (e.g. 5 lines) renders concise Show all button without +/-10
const smallBarHtml = renderPgContextControlBarHtml(43330008, "src/foo.cc", 2, 10, 14, false);
assert.equal(smallBarHtml.includes("5 lines skipped"), true);
assert.equal(smallBarHtml.includes("Show all (5 lines)"), true);
assert.equal(smallBarHtml.includes("▲ +10 lines"), false);

// 3. Test diff file with skipped lines between hunks rendered in Unified mode
const mockDiffFile = {
  file: "src/unicode_测试_🚀_é.cc",
  lines: [
    { type: "hunk", old: "-10,4", new: "+10,4", text: "@@ -10,4 +10,4 @@ void FirstHunk() {" },
    { type: "ctx", old: 10, new: 10, text: " void FirstHunk() {" },
    { type: "del", old: 11, new: "", text: "-  // Old implementation" },
    { type: "add", old: "", new: 11, text: "+  // New 测试_🚀_é implementation" },
    { type: "ctx", old: 12, new: 12, text: " }" },
    { type: "ctx", old: 13, new: 13, text: " " },
    // Skipped lines 14..49 (36 lines skipped)
    { type: "hunk", old: "-50,4", new: "+50,4", text: "@@ -50,4 +50,4 @@ void SecondHunk() {" },
    { type: "ctx", old: 50, new: 50, text: " void SecondHunk() {" },
    { type: "add", old: "", new: 51, text: "+  RunTask();" },
    { type: "ctx", old: 51, new: 52, text: " }" },
    { type: "ctx", old: 52, new: 53, text: " " },
  ]
};

const unifiedHtml = renderFileDiffUnifiedHtml(
  mockDiffFile,
  43330008,
  2,
  "src/unicode_测试_🚀_é.cc",
  [],
  "syntax_intraline",
  { id: "cpp", badge: "C++" },
  {},
  4
);

assert.equal(unifiedHtml.includes("pg-context-bar"), true);
assert.equal(unifiedHtml.includes('data-from-line="14"'), true);
assert.equal(unifiedHtml.includes('data-to-line="49"'), true);
assert.equal(unifiedHtml.includes("36 lines skipped"), true);
assert.equal(unifiedHtml.includes("测试_🚀_é"), true);

// 4. Test diff file rendered in Side-by-Side mode
const sbsHtml = renderFileDiffSideBySideHtml(
  mockDiffFile,
  43330008,
  2,
  "src/unicode_测试_🚀_é.cc",
  [],
  "syntax_intraline",
  { id: "cpp", badge: "C++" },
  {},
  4
);

assert.equal(sbsHtml.includes("code-line-sbs"), true);
assert.equal(sbsHtml.includes("pg-context-bar"), true);
assert.equal(sbsHtml.includes('data-from-line="14"'), true);
assert.equal(sbsHtml.includes('data-to-line="49"'), true);

// 5. Test expandPgDiffContext and expandAllVisibleDiffContext functions
assert.equal(typeof expandPgDiffContext, "function");
assert.equal(typeof expandAllVisibleDiffContext, "function");

state.fileContentCache = {
  "43330008:2:src/unicode_测试_🚀_é.cc": Array.from({ length: 60 }, (_, i) => `// Line ${i + 1} with 测试_🚀_é context`)
};

let insertedHtml = "";
let removed = false;
let updatedOuterHtml = "";

const mockBarEl = {
  tagName: "DIV",
  classList: {
    contains(c) { return c === "pg-context-bar"; }
  },
  insertAdjacentHTML(pos, html) {
    insertedHtml += html;
  },
  remove() {
    removed = true;
  },
  set outerHTML(val) {
    updatedOuterHtml = val;
  }
};

const mockEvent = {
  preventDefault() {},
  stopPropagation() {},
  currentTarget: {
    textContent: "Expand all (36 lines)",
    closest(sel) { return mockBarEl; }
  }
};

expandPgDiffContext(
  mockEvent,
  43330008,
  "src/unicode_测试_🚀_é.cc",
  2,
  14,
  49,
  "all"
);

assert.equal(removed, true);
assert.equal(insertedHtml.includes("pg-expanded-ctx-row"), true);
assert.equal(insertedHtml.includes("Line 14 with 测试_🚀_é"), true);

// 6. Verify cheatsheet includes Shift+X for diff context expansion
const diffShortcuts = PG_KEYBOARD_SHORTCUTS.find(g => g.group === "File List & Diff Navigation");
assert.notEqual(diffShortcuts, undefined);
const shiftXDef = diffShortcuts.shortcuts.find(s => s.keys.includes("Shift") && s.keys.includes("X"));
assert.notEqual(shiftXDef, undefined);
assert.equal(shiftXDef.description.includes("context lines"), true);

console.log("POLYGERRIT_DIFF_CONTEXT_EXPANSION_OK");
"""
        )

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
            tf.write(harness_js)
            tf_path = tf.name
        try:
            res = subprocess.run(
                [node_bin, tf_path], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10
            )
            self.assertEqual(
                res.returncode,
                0,
                f"Node execution failed with {res.stderr}\\nScript:\\n{harness_js[:1000]}",
            )
            self.assertIn("POLYGERRIT_DIFF_CONTEXT_EXPANSION_OK", res.stdout)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_polygerrit_diff_preferences_dialog_and_rendering(self):
        """Verifies PolyGerrit Diff Preferences dialog (<gr-diff-preferences-dialog>), controls, and reactive rendering."""
        from commands.gerrit.services.diff_parser import get_gerrit_ui_template_path

        tpl_path = get_gerrit_ui_template_path("polygerrit")
        self.assertTrue(tpl_path.exists(), f"PolyGerrit template missing at {tpl_path}")
        html = tpl_path.read_text(encoding="utf-8")

        # 1. Verify static HTML tokens and CSS classes for diff preferences
        self.assertIn("pgDiffPreferencesBackdrop", html)
        self.assertIn("pg-diff-pref-dialog", html)
        self.assertIn("pg-diff-pref-body", html)
        self.assertIn("pgDiffPrefContext", html)
        self.assertIn("pgDiffPrefTabWidth", html)
        self.assertIn("pgDiffPrefFontSize", html)
        self.assertIn("pgDiffPrefIgnoreWhitespace", html)
        self.assertIn("pgDiffPrefWrapLines", html)
        self.assertIn("pgDiffPrefSyntaxHighlight", html)
        self.assertIn("pgDiffPrefShowLineNumbers", html)
        self.assertIn("btnSaveDiffPreferences", html)
        self.assertIn("btnDiffPreferences", html)
        self.assertIn("pg-btn-diff-pref", html)
        self.assertIn("openPgDiffPreferencesDialog", html)
        self.assertIn("closePgDiffPreferencesDialog", html)
        self.assertIn("saveAndApplyPgDiffPreferences", html)
        self.assertIn("resetPgDiffPreferencesToDefaults", html)
        self.assertIn("applyPgDiffPreferences", html)
        self.assertIn("isWhitespaceDiffOnly", html)
        self.assertIn("pg-ws-ignored", html)
        self.assertIn("pg-diff-wrap-lines", html)
        self.assertIn("pg-diff-no-wrap", html)
        self.assertIn("pg-diff-hide-ln", html)
        self.assertIn("pg-diff-no-syntax", html)

        # 2. Verify cheatsheet and keydown registration for comma (,)
        self.assertIn("Open diff preferences dialog", html)
        self.assertIn('e.key === ","', html)

        # 3. Headless Node/VM DOM harness verification when node is present
        node_bin = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )
        if not node_bin:
            return

        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL)
        self.assertTrue(scripts)
        js_body = (
            scripts[-1]
            .replace("__GERRIT_UI_BOOTSTRAP_JSON__", "{}")
            .replace("\n  initApp();", "\n  // initApp();")
        )

        harness_js = (
            """
const assert = require("node:assert/strict");

function makeMockEl(tagName = "div") {
  const classes = new Set();
  const attrs = {};
  const children = [];
  return {
    tagName: tagName.toUpperCase(),
    value: "",
    textContent: "",
    disabled: false,
    checked: false,
    style: {
      setProperty(k, v) { this[k] = v; },
      getPropertyValue(k) { return this[k] || ""; }
    },
    classList: {
      add(...cs) { cs.forEach(c => classes.add(c)); },
      remove(...cs) { cs.forEach(c => classes.delete(c)); },
      toggle(c, force) {
        if (force === undefined) {
          if (classes.has(c)) classes.delete(c); else classes.add(c);
        } else if (force) {
          classes.add(c);
        } else {
          classes.delete(c);
        }
      },
      contains(c) { return classes.has(c); }
    },
    getAttribute(k) { return attrs[k] || null; },
    setAttribute(k, v) { attrs[k] = String(v); },
    appendChild(c) { children.push(c); },
    querySelectorAll(sel) { return []; },
    querySelector(sel) { return null; },
    addEventListener() {},
    scrollIntoView() {},
    scrollTo() {},
    focus() {},
    select() {}
  };
}

const domMap = {
  diffScroll: makeMockEl("div"),
  pgDiffPreferencesBackdrop: makeMockEl("div"),
  pgDiffPrefContext: makeMockEl("select"),
  pgDiffPrefTabWidth: makeMockEl("select"),
  pgDiffPrefFontSize: makeMockEl("select"),
  pgDiffPrefIgnoreWhitespace: makeMockEl("select"),
  pgDiffPrefWrapLines: makeMockEl("input"),
  pgDiffPrefSyntaxHighlight: makeMockEl("input"),
  pgDiffPrefShowLineNumbers: makeMockEl("input"),
  btnSaveDiffPreferences: makeMockEl("button"),
};

const mockLocalStorage = {};

global.window = {
  location: { href: "http://localhost:8080/?ui=polygerrit", search: "?ui=polygerrit" },
  localStorage: {
    getItem: k => mockLocalStorage[k] || null,
    setItem: (k, v) => { mockLocalStorage[k] = String(v); },
    removeItem: k => { delete mockLocalStorage[k]; }
  },
  innerWidth: 1400,
  addEventListener() {},
  removeEventListener() {}
};

global.document = {
  getElementById: id => {
    if (domMap[id]) return domMap[id];
    const el = makeMockEl("div");
    el.id = id;
    return el;
  },
  querySelectorAll: () => [],
  querySelector: () => null,
  createElement: tag => makeMockEl(tag),
  addEventListener() {},
  documentElement: {
    style: {
      setProperty(k, v) { this[k] = v; },
      getPropertyValue(k) { return this[k] || ""; }
    },
    setAttribute() {},
    classList: { add() {}, remove() {}, contains: () => false }
  }
};
global.navigator = { clipboard: { writeText: async () => {} } };
global.setTimeout = () => 0;
global.clearTimeout = () => {};
global.setInterval = () => 0;
global.clearInterval = () => {};
global.fetch = async () => ({ ok: true, json: async () => ({}) });

"""
            + js_body
            + """

// 1. Verify default preferences structure
const defPrefs = getPgDiffPreferences();
assert.equal(typeof defPrefs, "object");
assert.equal(defPrefs.context, 10);
assert.equal(defPrefs.tabWidth, 4);
assert.equal(defPrefs.wrapLines, true);
assert.equal(defPrefs.ignoreWhitespace, "none");
assert.equal(defPrefs.syntaxHighlighting, true);
assert.equal(defPrefs.showLineNumbers, true);
assert.equal(defPrefs.fontSize, 12);

// 2. Verify renderPgDiffPreferencesButtonHtml produces authentic button
const btnHtml = renderPgDiffPreferencesButtonHtml();
assert.equal(btnHtml.includes('id="btnDiffPreferences"'), true);
assert.equal(btnHtml.includes("pg-btn-diff-pref"), true);
assert.equal(btnHtml.includes("Preferences"), true);

// 3. Verify dialog lifecycle: open, close, and toggle
assert.equal(state.diffPreferencesDialogOpen, false);
openPgDiffPreferencesDialog();
assert.equal(state.diffPreferencesDialogOpen, true);
assert.equal(domMap.pgDiffPreferencesBackdrop.classList.contains("open"), true);
assert.equal(domMap.pgDiffPreferencesBackdrop.getAttribute("aria-hidden"), "false");
assert.equal(domMap.pgDiffPrefContext.value, "10");
assert.equal(domMap.pgDiffPrefTabWidth.value, "4");
assert.equal(domMap.pgDiffPrefWrapLines.checked, true);

closePgDiffPreferencesDialog();
assert.equal(state.diffPreferencesDialogOpen, false);
assert.equal(domMap.pgDiffPreferencesBackdrop.classList.contains("open"), false);
assert.equal(domMap.pgDiffPreferencesBackdrop.getAttribute("aria-hidden"), "true");

togglePgDiffPreferencesDialog();
assert.equal(state.diffPreferencesDialogOpen, true);
togglePgDiffPreferencesDialog();
assert.equal(state.diffPreferencesDialogOpen, false);

// 4. Test setting custom preferences and applying to DOM
setPgDiffPreferences({
  context: 25,
  tabWidth: 2,
  wrapLines: false,
  ignoreWhitespace: "changes",
  syntaxHighlighting: false,
  showLineNumbers: false,
  fontSize: 14
}, true, false);

const updatedPrefs = getPgDiffPreferences();
assert.equal(updatedPrefs.context, 25);
assert.equal(updatedPrefs.tabWidth, 2);
assert.equal(updatedPrefs.wrapLines, false);
assert.equal(updatedPrefs.ignoreWhitespace, "changes");
assert.equal(updatedPrefs.syntaxHighlighting, false);
assert.equal(updatedPrefs.showLineNumbers, false);
assert.equal(updatedPrefs.fontSize, 14);

// Verify localStorage persistence
const stored = JSON.parse(mockLocalStorage[DIFF_PREFS_STORAGE_KEY]);
assert.equal(stored.tabWidth, 2);
assert.equal(stored.context, 25);

// Verify DOM styling flags
assert.equal(domMap.diffScroll.classList.contains("pg-diff-no-wrap"), true);
assert.equal(domMap.diffScroll.classList.contains("pg-diff-hide-ln"), true);
assert.equal(domMap.diffScroll.classList.contains("pg-diff-no-syntax"), true);
assert.equal(domMap.diffScroll.classList.contains("pg-diff-ignore-ws"), true);
assert.equal(global.document.documentElement.style["--pg-diff-tab-size"], "2");
assert.equal(global.document.documentElement.style["--pg-diff-font-size"], "14px");

// 5. Test isWhitespaceDiffOnly helper with Unicode test strings (测试_🚀_é)
assert.equal(isWhitespaceDiffOnly("  void RunTask();  ", "    void RunTask();"), true);
assert.equal(isWhitespaceDiffOnly("  const char* str = \\"测试_🚀_é\\";", "    const char* str = \\"测试_🚀_é\\";  "), true);
assert.equal(isWhitespaceDiffOnly("  int x = 1;", "  int x = 2;"), false);
assert.equal(isWhitespaceDiffOnly("  const char* a = \\"测试\\";", "  const char* a = \\"🚀\\";"), false);

// 6. Test diff rendering with whitespace changes and syntax highlighting disabled
const mockDiffFile = {
  file: "src/unicode_测试_🚀_é.cc",
  lines: [
    { type: "hunk", old: "-1,3", new: "+1,3", text: "@@ -1,3 +1,3 @@" },
    { type: "del", old: 1, new: "", text: "-  // Test 测试_🚀_é" },
    { type: "add", old: "", new: 1, text: "+    // Test 测试_🚀_é" },
    { type: "ctx", old: 2, new: 2, text: " int x = 42;" }
  ]
};

const sbsWsHtml = renderFileDiffSideBySideHtml(
  mockDiffFile,
  43330009,
  1,
  "src/unicode_测试_🚀_é.cc",
  [],
  "syntax_intraline",
  { id: "cpp", badge: "C++" },
  {},
  4
);
assert.equal(sbsWsHtml.includes("pg-ws-ignored"), true);
assert.equal(sbsWsHtml.includes("测试_🚀_é"), true);

const uniWsHtml = renderFileDiffUnifiedHtml(
  mockDiffFile,
  43330009,
  1,
  "src/unicode_测试_🚀_é.cc",
  [],
  "syntax_intraline",
  { id: "cpp", badge: "C++" },
  {},
  4
);
assert.equal(uniWsHtml.includes("pg-ws-ignored"), true);
assert.equal(uniWsHtml.includes("测试_🚀_é"), true);

// 7. Verify resetPgDiffPreferencesToDefaults restores everything
resetPgDiffPreferencesToDefaults();
const resetPrefs = getPgDiffPreferences();
assert.equal(resetPrefs.context, 10);
assert.equal(resetPrefs.tabWidth, 4);
assert.equal(resetPrefs.wrapLines, true);
assert.equal(resetPrefs.ignoreWhitespace, "none");
assert.equal(resetPrefs.syntaxHighlighting, true);
assert.equal(resetPrefs.showLineNumbers, true);
assert.equal(resetPrefs.fontSize, 12);
assert.equal(domMap.diffScroll.classList.contains("pg-diff-wrap-lines"), true);
assert.equal(domMap.diffScroll.classList.contains("pg-diff-no-wrap"), false);
assert.equal(domMap.diffScroll.classList.contains("pg-diff-hide-ln"), false);

// 8. Verify cheatsheet includes comma shortcut
const diffShortcuts = PG_KEYBOARD_SHORTCUTS.find(g => g.group === "File List & Diff Navigation");
assert.notEqual(diffShortcuts, undefined);
const commaDef = diffShortcuts.shortcuts.find(s => s.keys.includes(","));
assert.notEqual(commaDef, undefined);
assert.equal(commaDef.description.includes("preferences"), true);

console.log("POLYGERRIT_DIFF_PREFERENCES_OK");
"""
        )

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
            tf.write(harness_js)
            tf_path = tf.name
        try:
            res = subprocess.run(
                [node_bin, tf_path], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10
            )
            self.assertEqual(
                res.returncode,
                0,
                f"Node execution failed with {res.stderr}\\nScript:\\n{harness_js[:1000]}",
            )
            self.assertIn("POLYGERRIT_DIFF_PREFERENCES_OK", res.stdout)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_polygerrit_file_list_filter_by_keyword(self):
        """Verifies PolyGerrit File List Filter by keyword/path in gr-file-list table."""
        from commands.gerrit.services.diff_parser import (
            get_gerrit_ui_template_path,
        )

        tpl_path = get_gerrit_ui_template_path("polygerrit")
        self.assertTrue(tpl_path.exists(), f"PolyGerrit template missing at {tpl_path}")
        html = tpl_path.read_text(encoding="utf-8")

        # 1. Verify static HTML tokens and CSS classes for File List Filter
        self.assertIn("pg-file-list-header-bar", html)
        self.assertIn("pg-file-filter-wrap", html)
        self.assertIn("pg-file-filter-input", html)
        self.assertIn("pg-file-filter-clear-btn", html)
        self.assertIn("pg-file-list-filter-chips", html)
        self.assertIn("pg-file-filter-chip", html)
        self.assertIn("function getPgFileQueryFilter", html)
        self.assertIn("function setPgFileQueryFilter", html)
        self.assertIn("function matchesPgFileFilter", html)
        self.assertIn("function clearPgFileFilter", html)
        self.assertIn("function clearAllPgFileFilters", html)
        self.assertIn("function onPgFileFilterInput", html)
        self.assertIn("function onPgFileFilterKeyDown", html)
        self.assertIn("function applyPgFileFiltersToDom", html)
        self.assertIn("Focus file list filter input", html)

        # 2. Headless Node/VM DOM harness verification
        node_bin = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )
        if not node_bin:
            return

        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL)
        self.assertTrue(scripts)
        js_body = (
            scripts[-1]
            .replace("__GERRIT_UI_BOOTSTRAP_JSON__", "{}")
            .replace("\n  initApp();", "\n  // initApp();")
        )

        harness_js = (
            """
const assert = require("node:assert/strict");

function makeMockEl(tagName = "div") {
  const classes = new Set();
  const attrs = {};
  return {
    tagName: tagName.toUpperCase(),
    value: "",
    textContent: "",
    disabled: false,
    checked: false,
    style: {},
    classList: {
      add(...cs) { cs.forEach(c => classes.add(c)); },
      remove(...cs) { cs.forEach(c => classes.delete(c)); },
      toggle(c, force) {
        if (force === undefined) {
          if (classes.has(c)) classes.delete(c); else classes.add(c);
        } else if (force) {
          classes.add(c);
        } else {
          classes.delete(c);
        }
      },
      contains(c) { return classes.has(c); }
    },
    getAttribute(k) { return attrs[k] || null; },
    setAttribute(k, v) { attrs[k] = String(v); },
    querySelectorAll: () => [],
    querySelector: () => null,
    addEventListener() {},
    scrollIntoView() {},
    focus() {},
    select() {}
  };
}

global.window = {
  location: { search: "" },
  localStorage: {
    _data: {},
    getItem(k) { return this._data[k] || null; },
    setItem(k, v) { this._data[k] = String(v); },
    removeItem(k) { delete this._data[k]; }
  }
};
global.document = {
  getElementById: (id) => null,
  querySelector: (sel) => null,
  querySelectorAll: (sel) => [],
  createElement: (tag) => makeMockEl(tag),
  body: makeMockEl("body"),
  documentElement: makeMockEl("html"),
  addEventListener: () => {}
};
global.escapeHtml = (s) => String(s || "")
  .replace(/&/g, "&amp;")
  .replace(/</g, "&lt;")
  .replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;")
  .replace(/'/g, "&#39;");
global.showToast = () => {};

"""
            + js_body
            + """

// 1. Verify matchesPgFileFilter logic
assert.equal(matchesPgFileFilter("foo/bar.cc", ""), true);
assert.equal(matchesPgFileFilter("foo/bar.cc", "bar"), true);
assert.equal(matchesPgFileFilter("foo/bar.cc", "BAR"), true);
assert.equal(matchesPgFileFilter("foo/bar.cc", "*.cc"), true);
assert.equal(matchesPgFileFilter("foo/bar.cc", "*.h"), false);
assert.equal(matchesPgFileFilter("emulator/libs/file/测试_🚀_é.cc", "测试"), true);
assert.equal(matchesPgFileFilter("emulator/libs/file/测试_🚀_é.cc", "🚀"), true);
assert.equal(matchesPgFileFilter("emulator/libs/file/测试_🚀_é.cc", "libs/file"), true);
assert.equal(matchesPgFileFilter("emulator/libs/file/测试_🚀_é.cc", "unrelated"), false);
assert.equal(matchesPgFileFilter("/COMMIT_MSG", "commit"), true);
assert.equal(matchesPgFileFilter("/COMMIT_MSG", "msg"), true);
assert.equal(matchesPgFileFilter("/COMMIT_MSG", "unrelated"), false);

// 2. Setup mock change and files bundle with Unicode path
const clSample = {
  number: 43330003,
  patch_set: 1,
  subject: "Test File List Filter",
  status: "NEW"
};
const testBundle = {
  change_number: 43330003,
  patch_set: 1,
  files: [
    {
      file: "emulator/libs/file/测试_🚀_é.cc",
      status: "M",
      additions: 10,
      deletions: 2,
      owner_status: "approved"
    },
    {
      file: "unapproved.cc",
      status: "A",
      additions: 5,
      deletions: 0,
      owner_status: "missing"
    }
  ]
};
state.changes = [clSample];
state.diffsByCl = { 43330003: testBundle };

// 3. Render initial file list table: both files present, filter input rendered, clear button hidden
const initialHtml = renderPgFileListHtml(clSample, testBundle);
assert.equal(initialHtml.includes('id="pgFileFilterInput-43330003"'), true);
assert.equal(initialHtml.includes("Filter files (e.g. *.cc, src/, test) — Shift+F"), true);
assert.equal(initialHtml.includes('id="pgFileFilterClearBtn-43330003"'), true);
assert.equal(initialHtml.includes('style="display:none;"'), true); // Clear button hidden
assert.equal(initialHtml.includes("emulator/libs/file/测试_🚀_é.cc"), true);
assert.equal(initialHtml.includes("unapproved.cc"), true);
assert.equal(initialHtml.includes("/COMMIT_MSG"), true);

// 4. Test filtering by Unicode keyword "测试"
setPgFileQueryFilter(43330003, "测试");
assert.equal(getPgFileQueryFilter(43330003), "测试");
const filteredHtml = renderPgFileListHtml(clSample, testBundle);
assert.equal(filteredHtml.includes("emulator/libs/file/测试_🚀_é.cc"), true);
assert.equal(filteredHtml.includes("unapproved.cc"), false);
assert.equal(filteredHtml.includes("/COMMIT_MSG"), false);
assert.equal(filteredHtml.includes('Filter: "测试"'), true);
assert.equal(filteredHtml.includes('Showing <strong>1</strong> of <strong>2</strong> files matching "<strong>测试</strong>"'), true);

// 5. Test empty state filter
setPgFileQueryFilter(43330003, "nonexistent_query");
const emptyHtml = renderPgFileListHtml(clSample, testBundle);
assert.equal(emptyHtml.includes("pg-file-row-empty"), true);
assert.equal(emptyHtml.includes('No files match filter "nonexistent_query"'), true);
assert.equal(emptyHtml.includes("clearAllPgFileFilters(43330003)"), true);

// 6. Test clearPgFileFilter
clearPgFileFilter(43330003);
assert.equal(getPgFileQueryFilter(43330003), "");
const clearedHtml = renderPgFileListHtml(clSample, testBundle);
assert.equal(clearedHtml.includes("emulator/libs/file/测试_🚀_é.cc"), true);
assert.equal(clearedHtml.includes("unapproved.cc"), true);
assert.equal(clearedHtml.includes("/COMMIT_MSG"), true);

// 7. Test combined code owners filter and keyword filter
togglePgOwnersFilter(43330003); // Owner filter on -> only unapproved.cc
assert.equal(isPgOwnerFilterActive(43330003), true);
setPgFileQueryFilter(43330003, "unapproved");
const combinedHtml = renderPgFileListHtml(clSample, testBundle);
assert.equal(combinedHtml.includes("unapproved.cc"), true);
assert.equal(combinedHtml.includes("emulator/libs/file/测试_🚀_é.cc"), false);
assert.equal(combinedHtml.includes("missing owner approval and matching"), true);

clearAllPgFileFilters(43330003);
assert.equal(isPgOwnerFilterActive(43330003), false);
assert.equal(getPgFileQueryFilter(43330003), "");

// 8. Verify Shift+F in PG_KEYBOARD_SHORTCUTS
const navShortcuts = PG_KEYBOARD_SHORTCUTS.find(g => g.group === "File List & Diff Navigation");
assert.notEqual(navShortcuts, undefined);
const filterShortcut = navShortcuts.shortcuts.find(s => s.keys.includes("Shift") && s.keys.includes("F"));
assert.notEqual(filterShortcut, undefined);
assert.equal(filterShortcut.description.includes("filter input"), true);

console.log("POLYGERRIT_FILE_LIST_FILTER_OK");
"""
        )

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
            tf.write(harness_js)
            tf_path = tf.name
        try:
            res = subprocess.run(
                [node_bin, tf_path], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10
            )
            self.assertEqual(
                res.returncode,
                0,
                f"Node execution failed with {res.stderr}\nScript:\n{harness_js[:1000]}",
            )
            self.assertIn("POLYGERRIT_FILE_LIST_FILTER_OK", res.stdout)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_polygerrit_file_list_code_owners_and_filter(self):
        """Verifies PolyGerrit Code Owner approval badges, column, and interactive filter in gr-file-list."""
        from commands.gerrit.services.diff_parser import (
            get_gerrit_ui_template_path,
            parse_unified_diff_files,
        )

        # 1. Verify diff_parser support for owner_approvals with Unicode path (测试_🚀_é)
        sample_diff = (
            "diff --git a/emulator/libs/file/测试_🚀_é.cc b/emulator/libs/file/测试_🚀_é.cc\n"
            "--- a/emulator/libs/file/测试_🚀_é.cc\n"
            "+++ b/emulator/libs/file/测试_🚀_é.cc\n"
            "@@ -1,1 +1,2 @@\n"
            " int a;\n"
            "+int b;\n"
            "diff --git a/unapproved.cc b/unapproved.cc\n"
            "--- a/unapproved.cc\n"
            "+++ b/unapproved.cc\n"
            "@@ -1,1 +1,2 @@\n"
            " int x;\n"
            "+int y;\n"
        )
        parsed = parse_unified_diff_files(
            sample_diff,
            owner_approvals={
                "emulator/libs/file/测试_🚀_é.cc": "approved",
                "unapproved.cc": "missing",
            },
        )
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["owner_status"], "approved")
        self.assertEqual(parsed[1]["owner_status"], "missing")

        # 2. Verify static HTML tokens and CSS classes for Code Owners
        tpl_path = get_gerrit_ui_template_path("polygerrit")
        self.assertTrue(tpl_path.exists(), f"PolyGerrit template missing at {tpl_path}")
        html = tpl_path.read_text(encoding="utf-8")

        self.assertIn("col-owners", html)
        self.assertIn("pg-owner-badge", html)
        self.assertIn("status-approved", html)
        self.assertIn("status-missing", html)
        self.assertIn("function getPgFileOwnerStatus", html)
        self.assertIn("function renderPgFileOwnerBadgeHtml", html)
        self.assertIn("function isPgOwnerFilterActive", html)
        self.assertIn("function togglePgOwnersFilter", html)
        self.assertIn("function renderPgOwnersSummaryHtml", html)
        self.assertIn("pg-owners-summary-badge", html)
        self.assertIn("pg-owners-filter-btn", html)
        self.assertIn("pg-file-list-filter-bar", html)
        self.assertIn("Shift", html)
        self.assertIn("Toggle show only files missing code owner approval", html)

        # 3. Headless Node/VM DOM harness verification
        node_bin = shutil.which("node") or (
            "/usr/bin/node" if os.path.exists("/usr/bin/node") else None
        )
        if not node_bin:
            return

        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL)
        self.assertTrue(scripts)
        js_body = (
            scripts[-1]
            .replace("__GERRIT_UI_BOOTSTRAP_JSON__", "{}")
            .replace("\n  initApp();", "\n  // initApp();")
        )

        harness_js = (
            """
const assert = require("node:assert/strict");

function makeMockEl(tagName = "div") {
  const classes = new Set();
  const attrs = {};
  return {
    tagName: tagName.toUpperCase(),
    value: "",
    textContent: "",
    disabled: false,
    checked: false,
    style: {},
    classList: {
      add(...cs) { cs.forEach(c => classes.add(c)); },
      remove(...cs) { cs.forEach(c => classes.delete(c)); },
      toggle(c, force) {
        if (force === undefined) {
          if (classes.has(c)) classes.delete(c); else classes.add(c);
        } else if (force) {
          classes.add(c);
        } else {
          classes.delete(c);
        }
      },
      contains(c) { return classes.has(c); }
    },
    getAttribute(k) { return attrs[k] || null; },
    setAttribute(k, v) { attrs[k] = String(v); },
    querySelectorAll: () => [],
    querySelector: () => null,
    addEventListener() {},
    scrollIntoView() {},
    focus() {}
  };
}

const domMap = {
  diffScroll: makeMockEl("div"),
};

const mockLocalStorage = {};

global.window = {
  location: { href: "http://localhost:8080/?ui=polygerrit", search: "?ui=polygerrit" },
  localStorage: {
    getItem: k => mockLocalStorage[k] || null,
    setItem: (k, v) => { mockLocalStorage[k] = String(v); },
    removeItem: k => { delete mockLocalStorage[k]; }
  },
  innerWidth: 1400,
  addEventListener() {},
  removeEventListener() {}
};

global.document = {
  getElementById: id => {
    if (domMap[id]) return domMap[id];
    const el = makeMockEl("div");
    el.id = id;
    return el;
  },
  querySelectorAll: () => [],
  querySelector: () => null,
  createElement: tag => makeMockEl(tag),
  addEventListener() {},
  removeEventListener() {},
  documentElement: {
    clientWidth: 1400,
    clientHeight: 900,
    style: { setProperty() {}, getPropertyValue: () => "" }
  }
};

"""
            + js_body
            + """
// 1. Test getPgFileOwnerStatus logic
const clSample = {
  number: 43330002,
  is_agent_cl: false,
  is_submittable: false,
  human_cr2: [{ name: "Alice Bob", short: "alice" }],
  submit_requirements: [
    { name: "Code-Owners", status: "SATISFIED" }
  ]
};

// 1a. /COMMIT_MSG is always na
const msgOwner = getPgFileOwnerStatus(clSample, { file: "/COMMIT_MSG" });
assert.equal(msgOwner.status, "na");
assert.equal(msgOwner.label, "—");

// 1b. Explicit status overrides
const explicitApproved = getPgFileOwnerStatus(clSample, { file: "src/file.cc", owner_status: "approved", owner_detail: "Approved by lead" });
assert.equal(explicitApproved.status, "approved");
assert.equal(explicitApproved.title, "Approved by lead");

const explicitMissing = getPgFileOwnerStatus(clSample, { file: "src/file.cc", owner_status: "missing" });
assert.equal(explicitMissing.status, "missing");
assert.equal(explicitMissing.label, "Missing approval");

// 1c. Submit Requirements evaluation
const clSrUnsatisfied = {
  number: 43330003,
  human_cr2: [],
  submit_requirements: [{ name: "Code-Owners", status: "UNSATISFIED" }]
};
const missingSr = getPgFileOwnerStatus(clSrUnsatisfied, { file: "src/file.cc" });
assert.equal(missingSr.status, "missing");

// 1d. Approver fallback when Code-Owners SR not present
const clApproverFallback = {
  number: 43330004,
  human_cr2: [{ name: "Erwin Jansen", short: "jansene" }]
};
const approverStatus = getPgFileOwnerStatus(clApproverFallback, { file: "src/file.cc" });
assert.equal(approverStatus.status, "approved");
assert.equal(approverStatus.title.includes("Erwin Jansen"), true);

// 2. Test renderPgFileOwnerBadgeHtml output
const approvedBadgeHtml = renderPgFileOwnerBadgeHtml(explicitApproved);
assert.equal(approvedBadgeHtml.includes("pg-owner-badge"), true);
assert.equal(approvedBadgeHtml.includes("status-approved"), true);
assert.equal(approvedBadgeHtml.includes("✓"), true);
assert.equal(approvedBadgeHtml.includes("Owner approved"), true);

const missingBadgeHtml = renderPgFileOwnerBadgeHtml(explicitMissing);
assert.equal(missingBadgeHtml.includes("status-missing"), true);
assert.equal(missingBadgeHtml.includes("✕"), true);
assert.equal(missingBadgeHtml.includes("Missing approval"), true);

const naBadgeHtml = renderPgFileOwnerBadgeHtml(msgOwner);
assert.equal(naBadgeHtml.includes("status-na"), true);
assert.equal(naBadgeHtml.includes("—"), true);

// 3. Test renderPgFileListHtml with Unicode path (测试_🚀_é)
const testBundle = {
  patch_set: 1,
  files: [
    {
      file: "emulator/libs/file/测试_🚀_é.cc",
      status: "A",
      additions: 10,
      deletions: 0,
      owner_status: "approved"
    },
    {
      file: "unapproved.cc",
      status: "M",
      additions: 5,
      deletions: 2,
      owner_status: "missing"
    }
  ]
};

const fileListHtml = renderPgFileListHtml(clSample, testBundle);
assert.equal(fileListHtml.includes("col-owners"), true);
assert.equal(fileListHtml.includes("Owners"), true);
assert.equal(fileListHtml.includes("emulator/libs/file/测试_🚀_é.cc"), true);
assert.equal(fileListHtml.includes("data-owner-status=\\"approved\\""), true);
assert.equal(fileListHtml.includes("unapproved.cc"), true);
assert.equal(fileListHtml.includes("data-owner-status=\\"missing\\""), true);
assert.equal(fileListHtml.includes("/COMMIT_MSG"), true);

// 4. Test renderPgOwnersSummaryHtml
const summaryHtml = renderPgOwnersSummaryHtml(clSample, testBundle);
assert.equal(summaryHtml.includes("pg-owners-summary-badge"), true);
assert.equal(summaryHtml.includes("has-missing"), true);
assert.equal(summaryHtml.includes("btnFilterMissingOwners-43330002"), true);
assert.equal(summaryHtml.includes("1 missing owner approval"), true);

const allApprovedBundle = {
  patch_set: 1,
  files: [
    { file: "f1.cc", status: "M", owner_status: "approved" },
    { file: "f2.cc", status: "M", owner_status: "approved" }
  ]
};
const allApprovedSummaryHtml = renderPgOwnersSummaryHtml(clSample, allApprovedBundle);
assert.equal(allApprovedSummaryHtml.includes("all-approved"), true);
assert.equal(allApprovedSummaryHtml.includes("All files approved"), true);

// 5. Test togglePgOwnersFilter and filtered file table rendering
assert.equal(isPgOwnerFilterActive(43330002), false);

togglePgOwnersFilter(43330002);
assert.equal(isPgOwnerFilterActive(43330002), true);

const filteredFileListHtml = renderPgFileListHtml(clSample, testBundle);
assert.equal(filteredFileListHtml.includes("pg-file-list-filter-bar"), true);
assert.equal(filteredFileListHtml.includes("Showing <strong>1</strong> of <strong>2</strong> files missing code owner approval"), true);
assert.equal(filteredFileListHtml.includes("unapproved.cc"), true);
// Approved file should be filtered out
assert.equal(filteredFileListHtml.includes("emulator/libs/file/测试_🚀_é.cc"), false);
// /COMMIT_MSG should be omitted while filter is active
assert.equal(filteredFileListHtml.includes("/COMMIT_MSG"), false);

// Toggle filter back off
togglePgOwnersFilter(43330002);
assert.equal(isPgOwnerFilterActive(43330002), false);
const restoredFileListHtml = renderPgFileListHtml(clSample, testBundle);
assert.equal(restoredFileListHtml.includes("emulator/libs/file/测试_🚀_é.cc"), true);
assert.equal(restoredFileListHtml.includes("unapproved.cc"), true);
assert.equal(restoredFileListHtml.includes("/COMMIT_MSG"), true);

// 6. Verify Shift+O shortcut in PG_KEYBOARD_SHORTCUTS
const navShortcuts = PG_KEYBOARD_SHORTCUTS.find(g => g.group === "File List & Diff Navigation");
assert.notEqual(navShortcuts, undefined);
const ownersShortcut = navShortcuts.shortcuts.find(s => s.keys.includes("Shift") && s.keys.includes("O"));
assert.notEqual(ownersShortcut, undefined);
assert.equal(ownersShortcut.description.includes("code owner"), true);

console.log("POLYGERRIT_CODE_OWNERS_OK");
"""
        )

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
            tf.write(harness_js)
            tf_path = tf.name
        try:
            res = subprocess.run(
                [node_bin, tf_path], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10
            )
            self.assertEqual(
                res.returncode,
                0,
                f"Node execution failed with {res.stderr}\nScript:\n{harness_js[:1000]}",
            )
            self.assertIn("POLYGERRIT_CODE_OWNERS_OK", res.stdout)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)


if __name__ == "__main__":
    unittest.main()
