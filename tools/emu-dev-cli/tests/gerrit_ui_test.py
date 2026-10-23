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
import json
import unittest
from unittest.mock import patch

from commands.gerrit.ui_server import (
    GerritUIService,
    build_viewer_gerrit_query,
    classify_commit_type,
    classify_risk_and_load,
    enrich_change_for_ui,
    extract_bug_ids_from_change,
    group_changes_into_stacks,
    is_agent_authored_or_uploaded,
    is_agent_or_bot_email,
    is_explicit_reviewer,
    parse_issues_search_output,
    parse_unified_diff_files,
    render_static_gerrit_ui_html,
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

        # Viewing as Joshua (has not given +2 yet)
        as_joshua = enrich_change_for_ui(sample_change, "joshuaduong@google.com", now_dt=now)
        self.assertFalse(as_joshua["approved_by_viewer"])
        self.assertIn("Needs Your +2", as_joshua["approval_slots"][1]["text"])

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


if __name__ == "__main__":
    unittest.main()
