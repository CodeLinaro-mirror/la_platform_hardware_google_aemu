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
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
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
from commands.gerrit.ui_server import (
    GerritUIService,
    build_code_search_file_url,
    build_viewer_gerrit_query,
    classify_commit_type,
    classify_risk_and_load,
    discover_transcript_code_questions,
    enrich_change_for_ui,
    extract_bug_ids_from_change,
    format_repo_display_path,
    group_changes_into_stacks,
    is_agent_authored_or_uploaded,
    is_agent_or_bot_email,
    is_explicit_reviewer,
    make_gerrit_ui_handler,
    parse_issues_search_output,
    parse_unified_diff_files,
    render_static_gerrit_ui_html,
    resolve_code_search_base_url,
)


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
        from commands.gerrit.ui_server import (
            SIDECAR_PRELOAD_SCRIPT_TAG,
            render_static_gerrit_ui_html,
        )

        html = render_static_gerrit_ui_html(
            {"viewer": "joshuaduong@google.com", "changes": [], "note": "测试_🚀_é"}
        )
        self.assertNotIn(SIDECAR_PRELOAD_SCRIPT_TAG, html)
        self.assertIn("window.sidecar =", html)
        self.assertIn("X-Sidecar-Token", html)
        self.assertIn("测试_🚀_é", html)

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
                "commands.gerrit.ui_server.resolve_swarm_dir_for_cl", return_value=out_dir
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

        from commands.gerrit.ui_server import extract_commit_message_from_patch

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
        from commands.gerrit.ui_server import build_code_question_prompt

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
            "commands.gerrit.ui_server._run_sidecar_agentapi",
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

        from commands.gerrit.ui_server import parse_code_questions_from_transcript_lines

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
            "commands.gerrit.ui_server._resolve_gchat_cli_bin",
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
            "commands.gerrit.ui_server._resolve_gchat_cli_bin", return_value=None
        ), self.assertRaisesRegex(RuntimeError, "Copy Text"):
            service.send_reviewer_chat_dm(43217292, "jansene@google.com", unicode_msg)

        # Verify HTTP POST /api/gerrit/chat-dm returns 200 on success and 400 on ValueError
        handler_cls = make_gerrit_ui_handler(service)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        srv_thread.start()
        try:
            with patch(
                "commands.gerrit.ui_server._resolve_gchat_cli_bin",
                return_value="/google/bin/releases/gemini-agents-gchat/gchat",
            ), patch("subprocess.run", side_effect=fake_gchat_run):
                req_ok = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/gerrit/chat-dm",
                    data=json.dumps({
                        "cl": 43217292,
                        "recipient": "jansene@google.com",
                        "message": unicode_msg,
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
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
                    headers={"Content-Type": "application/json"},
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

        handler_cls = make_gerrit_ui_handler(service)
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
                    f"http://127.0.0.1:{port}/api/gerrit/presubmit-targets"
                    "?cl=43234462&ps=2&project=platform/hardware/google/aemu",
                    timeout=5,
                ) as resp:
                    self.assertEqual(resp.status, 200)
                    payload = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(payload["counts"]["total"], 4)

            with self.assertRaises(urllib.error.HTTPError) as bad_err:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/gerrit/presubmit-targets?cl=abc", timeout=5
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
            handler_cls = make_gerrit_ui_handler(service)
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
                    headers={"Content-Type": "application/json"},
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
            patch("commands.gerrit.ui_server.discover_transcript_code_questions", return_value=[]),
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
            handler_cls = make_gerrit_ui_handler(service)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
            port = httpd.server_address[1]
            srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            srv_thread.start()
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/gerrit/queue?scope=reviewer&fast=1", timeout=5
                ) as resp1:
                    d1 = json.loads(resp1.read().decode("utf-8"))
                self.assertTrue(d1["is_partial"])
                self.assertEqual(calls[-1], (True, True))
                self.assertEqual(mock_prewarm.call_count, 0)

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/gerrit/queue?scope=reviewer&prewarm=1", timeout=5
                ) as resp2:
                    d2 = json.loads(resp2.read().decode("utf-8"))
                self.assertFalse(d2["is_partial"])
                self.assertEqual(calls[-1], (False, True))
                self.assertEqual(mock_prewarm.call_count, 1)

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/gerrit/queue?scope=reviewer&fast=1&refresh=1",
                    timeout=5,
                ) as resp3:
                    json.loads(resp3.read().decode("utf-8"))
                self.assertEqual(calls[-1], (True, False))

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/gerrit/queue?prewarm_only=1&viewer=joshuaduong@google.com",
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
        from commands.gerrit.ui_server import (
            extract_enclosing_function_from_diff_lines,
            extract_symbol_from_code_line,
        )

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
                patch("commands.gerrit.ui_server._run_sidecar_agentapi", side_effect=fake_agentapi),
                patch(
                    "commands.gerrit.ui_server.discover_transcript_code_questions",
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
                patch("commands.gerrit.ui_server._run_sidecar_agentapi", side_effect=fake_agentapi),
                patch("commands.gerrit.ui_server._call_local_language_server_rpc", side_effect=fake_ls_rpc),
                patch(
                    "commands.gerrit.ui_server.discover_transcript_code_questions",
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
                handler_cls = make_gerrit_ui_handler(service)
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
                        headers={"Content-Type": "application/json"},
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
                "commands.gerrit.ai_analysis.subprocess.run",
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
                patch("commands.gerrit.ui_server._run_sidecar_agentapi", side_effect=fake_agentapi),
                patch("commands.gerrit.ui_server._call_local_language_server_rpc", side_effect=fake_ls_rpc),
                patch("commands.gerrit.ui_server.discover_transcript_code_questions", side_effect=scoped_discover),
                patch("commands.gerrit.ai_analysis.fetch_buganizer_context", return_value=""),
            ):
                # 1. Default GET never auto-triggers an agent run.
                initial = service.get_ai_analysis(43288888, patchset=2, brain_root=brain_root)
                self.assertEqual(initial["analysis_status"], "not_analyzed")
                self.assertIsNone(initial["analysis"])
                self.assertEqual(spawn_calls, [])

                # 2. Exercise HTTP GET + POST /api/gerrit/ai-analysis on local server.
                server = ThreadingHTTPServer(("127.0.0.1", 0), make_gerrit_ui_handler(service))
                port = server.server_address[1]
                server_thread = threading.Thread(target=server.serve_forever, daemon=True)
                server_thread.start()
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/gerrit/ai-analysis?cl=43288888&ps=2",
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
                        headers={"Content-Type": "application/json"},
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
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_gerrit_ui_handler(service))
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
                    headers={"Content-Type": "application/json"},
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
                    headers={"Content-Type": "application/json"},
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
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_gerrit_ui_handler(service))
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
                    headers={"Content-Type": "application/json"},
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
            handler = make_gerrit_ui_handler(service)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                # 1) Submit topic CLs -> 200 OK, notify=OWNER verified on every gob-curl call
                req_ok = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/gerrit/submit",
                    data=json.dumps({"cls": [43290001, 43290002]}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
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
                    headers={"Content-Type": "application/json"},
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


if __name__ == "__main__":
    unittest.main()
