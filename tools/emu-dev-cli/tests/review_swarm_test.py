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

"""Focused unit tests for the 8-Specialist Parallel Code-Review Swarm & upload gate."""

import argparse
import ast
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from commands.harness import (
    run_harness_hook,
    run_harness_install,
    run_harness_review_swarm,
)
from commands.harness.constants import GERRIT_UPLOAD_CMD_PATTERN, HookDecision, HookEvent
from commands.harness.guardrails import (
    dispatch_hook_event,
    evaluate_git_commit_command,
)
from commands.harness.mcp_server import handle_mcp_jsonrpc_request, run_stdio_mcp_server
from commands.harness.plugin_installer import install_harness_plugin
from commands.harness.review_swarm import (
    REVIEW_SWARM_SPECIALISTS,
    build_specialist_prompts,
    extract_bug_ids_from_commit_msg,
    filter_and_aggregate_findings,
    parse_cl_identifier,
    parse_unified_diff_changed_lines,
    prepare_review_swarm_bundle,
)
from commands.harness.review_targets import _decode_gerrit_patch
from commands.harness.upload_guardrails import evaluate_upload_command
from lib.gerrit_api import parse_cl_identifier as gerrit_parse_cl_identifier
from lib.output import ensure_utf8_stdio

REGEX_PIPE_STRESS_COUNT: int = 10000
GIT_TEST_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test User",
    "GIT_AUTHOR_EMAIL": "test@google.com",
    "GIT_COMMITTER_NAME": "Test User",
    "GIT_COMMITTER_EMAIL": "test@google.com",
}


class ReviewSwarmTest(unittest.TestCase):
    """Tests diff parsing, 8-specialist workflow bundles, filtering, and upload gates."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base_path = Path(self.temp_dir.name)
        env_patcher = mock.patch.dict(
            os.environ,
            {"EMU_DEV_CLI_HARNESS_DIR": str(self.base_path / "harness")},
        )
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        os.environ.pop("AEMU_SKIP_REVIEW_SWARM", None)
        os.environ.pop("AEMU_ALLOW_REVIEW_FINDINGS", None)

    def _init_seed_remote(self, remote_bare: Path, clean_proj: Path) -> None:
        subprocess.run(["git", "init", "--bare", str(remote_bare)], check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "studio-main"], cwd=str(clean_proj), check=True, capture_output=True)
        (clean_proj / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=str(clean_proj), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "chore: Initial commit"],
            cwd=str(clean_proj),
            env=GIT_TEST_ENV,
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "remote", "add", "goog", str(remote_bare)], cwd=str(clean_proj), check=True, capture_output=True)
        subprocess.run(["git", "push", "-u", "goog", "studio-main"], cwd=str(clean_proj), check=True, capture_output=True)

    def _create_multi_project_repo_fixture(self) -> tuple[Path, Path]:
        ws_root = self.base_path / "studio-main"
        remote_bare = self.base_path / "remote.git"
        clean_proj = ws_root / "prebuilts" / "clean_proj"
        dirty_proj = ws_root / "tools" / "vendor_测试_🚀_é"
        for d in (ws_root / ".repo", remote_bare, clean_proj, dirty_proj):
            d.mkdir(parents=True, exist_ok=True)

        self._init_seed_remote(remote_bare, clean_proj)
        subprocess.run(["git", "clone", "-o", "goog", str(remote_bare), str(dirty_proj)], check=True, capture_output=True)
        subprocess.run(
            ["git", "checkout", "-b", "b574245946", "--track", "goog/studio-main"],
            cwd=str(dirty_proj),
            check=True,
            capture_output=True,
        )
        (dirty_proj / "DeviceHub_测试_🚀_é.kt").write_text("class DeviceHub\n", encoding="utf-8")
        subprocess.run(["git", "add", "DeviceHub_测试_🚀_é.kt"], cwd=str(dirty_proj), check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                "fix(cli): Make --device optional in device hub\n\nBody 测试_🚀_é.\n\nBug: 574245946\nTest: unit\nChange-Id: I1234567890abcdef1234567890abcdef12345678",
            ],
            cwd=str(dirty_proj),
            env=GIT_TEST_ENV,
            check=True,
            capture_output=True,
        )
        (ws_root / ".repo" / "project.list").write_text(
            "prebuilts/clean_proj\ntools/vendor_测试_🚀_é\n",
            encoding="utf-8",
        )
        return ws_root, dirty_proj

    def test_diff_parser_and_bug_footer_extraction(self) -> None:
        sample_diff = (
            "--- a/emulator/net/client.cc\n"
            "+++ b/emulator/net/client.cc\n"
            "@@ -10,2 +10,4 @@\n"
            " int existing_line = 0;\n"
            "+std::ifstream bad_stream(\"测试_🚀_é.ini\");\n"
            "+++existing_line;\n"
            " return existing_line;\n"
        )
        changed = parse_unified_diff_changed_lines(sample_diff)
        self.assertEqual(changed["emulator/net/client.cc"], {11, 12})
        self.assertEqual(extract_bug_ids_from_commit_msg("Title\n\nBody\n\nBug: b/485291270\n"), ["485291270"])

    def test_prepare_bundle_generates_8_specialist_workflow(self) -> None:
        self.assertEqual(len(REVIEW_SWARM_SPECIALISTS), 8)
        bundle = prepare_review_swarm_bundle(
            mode="workflow",
            output_dir=self.base_path / "swarm-out",
            preloaded_target={
                "target_type": "gerrit_cl",
                "target_id": "43137255",
                "project": "platform/hardware/google/aemu",
                "branch": "studio-main",
                "subject": "feat(net): Add client request helper",
                "current_revision": "rev1",
                "commit_message": "feat(net): Add client request helper\n\nBug: 485291270\n",
                "bug_ids": ["485291270"],
                "files": ["emulator/net/client.cc"],
                "diff_text": "",
                "changed_lines_by_file": {"emulator/net/client.cc": [11, 12]},
            },
        )
        self.assertEqual(bundle["specialist_count"], 8)
        script_text = Path(bundle["workflow_script_path"]).read_text(encoding="utf-8")
        self.assertIn("MAX_SWARM_CONCURRENCY", script_text)
        self.assertIn("filter_and_aggregate_findings", script_text)

    def test_filter_and_aggregate_drops_out_of_diff_and_duplicates(self) -> None:
        aggregated = filter_and_aggregate_findings(
            [
                {
                    "specialist": "duplication-and-reuse-auditor",
                    "checked_count": 3,
                    "duplicate_count": 1,
                    "finding_count": 1,
                    "findings": [
                        {"file": "emulator/net/client.cc", "line": 11, "category": "reuse", "severity": "blocking", "message": "Use android::base::file.", "suggested_fix": ""},
                        {"file": "emulator/net/client.cc", "line": 99, "category": "reuse", "severity": "warning", "message": "Untouched line.", "suggested_fix": ""},
                    ],
                },
                {
                    "specialist": "unicode-and-platform-auditor",
                    "checked_count": 1,
                    "duplicate_count": 0,
                    "finding_count": 1,
                    "findings": [
                        {"file": "emulator/net/client.cc", "line": 11, "category": "reuse", "severity": "blocking", "message": "Duplicate.", "suggested_fix": ""},
                    ],
                },
                {
                    "specialist": "error-messages-and-statusor-auditor",
                    "checked_count": 4,
                    "duplicate_count": 0,
                    "finding_count": 1,
                    "findings": [
                        {"file": "emulator/net/client.cc", "line": 12, "category": "statusor", "severity": "blocking", "message": "Avoid _or suffix.", "suggested_fix": ""},
                    ],
                },
            ],
            {"emulator/net/client.cc": [11, 12]},
        )
        self.assertEqual(aggregated["total_findings"], 2)
        self.assertEqual(aggregated["dropped_out_of_bounds"], 1)
        self.assertEqual(aggregated["dropped_duplicates"], 1)
        self.assertEqual(aggregated["gerrit_review_payload"]["notify"], "OWNER")

    def test_mcp_prepare_review_swarm_tool(self) -> None:
        _, repo_dir = self._create_multi_project_repo_fixture()
        list_res = handle_mcp_jsonrpc_request({"jsonrpc": "2.0", "id": 10, "method": "tools/list"})
        tool_names = [t["name"] for t in list_res["result"]["tools"]]  # type: ignore[index]
        self.assertIn("aemu_prepare_review_swarm", tool_names)
        call_res = handle_mcp_jsonrpc_request({
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "aemu_prepare_review_swarm",
                "arguments": {"target": "HEAD", "mode": "workflow", "repo_dir": str(repo_dir)},
            },
        })
        self.assertIsNotNone(call_res)
        self.assertFalse(call_res["result"]["isError"])  # type: ignore[index]

    def test_pre_upload_review_swarm_gate(self) -> None:
        _, repo_dir = self._create_multi_project_repo_fixture()
        cache_root = self.base_path / "review-swarm-cache"
        self.assertIsNone(evaluate_upload_command("git status", review_swarm_cache_root=cache_root))
        win_cmd = f'cd /d "{repo_dir}" & repo.cmd upload --cbr -y .'
        verdict = evaluate_upload_command(win_cmd, review_swarm_cache_root=cache_root)
        self.assertFalse(verdict[0])  # type: ignore[index]
        head_out_dir = next(cache_root.iterdir())
        (head_out_dir / "gerrit_review_payload.json").write_text(
            json.dumps({"message": "1 finding", "notify": "OWNER", "comments": {"a.py": [{"line": 1, "message": "m", "unresolved": True}]}}),
            encoding="utf-8",
        )
        self.assertFalse(evaluate_upload_command(win_cmd, review_swarm_cache_root=cache_root)[0])  # type: ignore[index]
        (head_out_dir / "gerrit_review_payload.json").write_text(
            json.dumps({"message": "0 findings", "notify": "OWNER", "comments": {}}),
            encoding="utf-8",
        )
        self.assertTrue(evaluate_upload_command(win_cmd, review_swarm_cache_root=cache_root)[0])  # type: ignore[index]

    def test_ansi_c_git_commit_message(self) -> None:
        ansi_cmd = (
            "git commit -m $'fix(cli): Make --device optional 测试_🚀_é\\n\\n"
            "Allow invoking \\'android device hub\\' without \\'--device\\'.\\n\\n"
            "Bug: 574245946\\n"
            "Test: //tools/vendor/google/android-cli/ui:android-cli-ui-test'"
        )
        ansi_verdict = evaluate_git_commit_command(ansi_cmd)
        self.assertIsNotNone(ansi_verdict)
        self.assertTrue(ansi_verdict[0], msg=ansi_verdict[1])  # type: ignore[index]

    def test_upload_help_flag_bypasses_gate(self) -> None:
        non_git_plugin_dir = self.base_path / "plugin_cwd"
        non_git_plugin_dir.mkdir(parents=True, exist_ok=True)
        self.assertIsNone(
            evaluate_upload_command("repo upload --help | head -n 40", default_cwd=non_git_plugin_dir)
        )

    def test_corrupt_review_payload_fails_closed(self) -> None:
        _, dirty_proj = self._create_multi_project_repo_fixture()
        cache_root = self.base_path / "corrupt_cache"
        first_verdict = evaluate_upload_command(
            f'cd "{dirty_proj}" && repo upload --cbr -y .',
            review_swarm_cache_root=cache_root,
        )
        self.assertFalse(first_verdict[0])  # type: ignore[index]
        head_out_dir = next(cache_root.iterdir())
        (head_out_dir / "gerrit_review_payload.json").write_text("{truncated", encoding="utf-8")
        corrupt_verdict = evaluate_upload_command(
            f'cd "{dirty_proj}" && repo upload --cbr -y .',
            review_swarm_cache_root=cache_root,
        )
        self.assertFalse(corrupt_verdict[0])  # type: ignore[index]
        self.assertIn("Failed to read or parse review-swarm payload", corrupt_verdict[1])  # type: ignore[index]

    def test_unicode_quoted_diff_and_prompt_serialization(self) -> None:
        quoted_diff = (
            '--- /dev/null\n'
            '+++ "b/DeviceHub_\\346\\265\\213\\350\\257\\225_\\360\\237\\232\\200_\\303\\251.kt"\n'
            '@@ -0,0 +1,1 @@\n'
            '+class DeviceHub\n'
        )
        parsed_quoted = parse_unified_diff_changed_lines(quoted_diff)
        self.assertEqual(parsed_quoted, {"DeviceHub_测试_🚀_é.kt": {1}})
        prompts = build_specialist_prompts({"changed_lines_by_file": {"DeviceHub_测试_🚀_é.kt": [1]}})
        self.assertIn("DeviceHub_测试_🚀_é.kt", prompts[0]["prompt"])
        self.assertEqual(_decode_gerrit_patch(")]}'\n" + json.dumps(quoted_diff)), quoted_diff)

    def test_repo_workspace_upload_gate_and_mcp_resolution(self) -> None:
        ws_root, _ = self._create_multi_project_repo_fixture()
        non_git_plugin_dir = self.base_path / "plugin_hook_cwd"
        non_git_plugin_dir.mkdir(parents=True, exist_ok=True)
        old_cwd = Path.cwd()
        os.chdir(non_git_plugin_dir)
        try:
            hook_out = dispatch_hook_event(
                HookEvent.PRE_TOOL.value,
                {
                    "conversationId": "conv-repo-ws",
                    "workspacePaths": [str(ws_root)],
                    "toolCall": {"name": "run_command", "args": {"CommandLine": "repo upload --br=b574245946 --yes"}},
                },
            )
            self.assertEqual(hook_out["decision"], HookDecision.DENY.value)
            mcp_res = handle_mcp_jsonrpc_request({
                "jsonrpc": "2.0",
                "id": 99,
                "method": "tools/call",
                "params": {"name": "aemu_prepare_review_swarm", "arguments": {"target": "HEAD", "mode": "workflow", "repo_dir": str(ws_root)}},
            })
            mcp_data = json.loads(mcp_res["result"]["content"][0]["text"])  # type: ignore[index]
            self.assertEqual(mcp_data["bug_ids"], ["574245946"])
            self.assertEqual(mcp_data["files"], ["DeviceHub_测试_🚀_é.kt"])
        finally:
            os.chdir(old_cwd)


    def test_windows_cp1252_cli_and_python_launcher_upload_gate(self) -> None:
        _, repo_dir = self._create_multi_project_repo_fixture()
        cache_root = self.base_path / "py_launcher_cache"
        for cmd in (
            f'py -3 C:\\tools\\repo upload --cbr -y "{repo_dir}"',
            f'python C:\\src\\__main__.py gerrit upload --repo "{repo_dir}"',
        ):
            verdict = evaluate_upload_command(cmd, review_swarm_cache_root=cache_root)
            self.assertFalse(verdict[0])  # type: ignore[index]
        buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        with mock.patch.object(sys, "stdout", buf):
            res = run_harness_review_swarm(
                argparse.Namespace(
                    cl=None,
                    commit="HEAD",
                    mode="workflow",
                    repo=str(repo_dir),
                    output_dir=str(self.base_path / "cli_测试_🚀_é"),
                    aggregate_from=None,
                    json=False,
                )
            )
        self.assertEqual(res.get("status"), "success")
        buf.flush()
        self.assertIn("cli_测试_🚀_é", buf.buffer.getvalue().decode("utf-8", errors="replace"))
        wf_src = (self.base_path / "cli_测试_🚀_é" / "run_review_swarm_workflow.py").read_text(encoding="utf-8")
        wf_code = compile(wf_src, "<wf>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        self.assertTrue([c.encode("utf-8") for c in wf_code.co_consts if isinstance(c, str)])

    def test_workspace_unicode_plugin_install_and_linear_regex(self) -> None:
        self.assertIsNone(GERRIT_UPLOAD_CMD_PATTERN.search("a|b|" * REGEX_PIPE_STRESS_COUNT))
        ws_unicode = self.base_path / "ws_测试_🚀_é"
        ws_unicode.mkdir(parents=True, exist_ok=True)
        install_buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        with mock.patch("pathlib.Path.cwd", return_value=ws_unicode), mock.patch.object(
            sys, "stdout", install_buf
        ):
            install_res = run_harness_install(
                argparse.Namespace(scope="workspace", target_dir=None, json=False)
            )
        self.assertEqual(install_res.get("status"), "success")
        win_cli = r'"C:\Users\测试_🚀_é\python.exe" "C:\tools\emu-dev-cli\src\__main__.py"'
        install_harness_plugin(scope="workspace", workspace_dir=ws_unicode, cli_command=win_cli)
        plugin_root = ws_unicode / ".agent" / "plugins" / "aemu-harness"
        self.assertIn(r"C:\Users\测试_🚀_é\python.exe", json.loads((plugin_root / "hooks.json").read_text(encoding="utf-8"))["aemu-harness"]["SessionStart"][0]["command"])
        self.assertEqual(json.loads((plugin_root / "mcp_config.json").read_text(encoding="utf-8"))["mcpServers"]["aemu-harness-mcp"]["command"], r"C:\Users\测试_🚀_é\python.exe")

    def test_cp1252_stdin_hook_and_mcp_server_unicode_paths(self) -> None:
        _, dirty_proj = self._create_multi_project_repo_fixture()
        self.assertEqual(gerrit_parse_cl_identifier("HEAD", cwd=dirty_proj), parse_cl_identifier("I1234567890abcdef1234567890abcdef12345678"))
        hook_bytes = json.dumps(
            {
                "conversationId": "conv-cp1252-ws",
                "workspacePaths": [str(dirty_proj)],
                "toolCall": {"name": "run_command", "args": {"CommandLine": "repo upload --cbr -y"}},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        hook_in = io.TextIOWrapper(io.BytesIO(hook_bytes), encoding="cp1252", errors="strict")
        hook_out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        with mock.patch.object(sys, "stdin", hook_in), mock.patch.object(sys, "stdout", hook_out):
            run_harness_hook(argparse.Namespace(event=HookEvent.PRE_TOOL.value))
            ensure_utf8_stdio()
        hook_resp = json.loads(hook_out.buffer.getvalue().decode("utf-8"))
        self.assertEqual(hook_resp["decision"], HookDecision.DENY.value)
        self.assertIn("vendor_测试_🚀_é", hook_resp.get("reason", ""))

        mcp_bytes = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {"name": "aemu_prepare_review_swarm", "arguments": {"commit": "HEAD", "repo_dir": str(dirty_proj)}},
                },
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
        mcp_in = io.TextIOWrapper(io.BytesIO(mcp_bytes), encoding="cp1252", errors="strict")
        mcp_out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        with mock.patch.object(sys, "stdin", mcp_in), mock.patch.object(sys, "stdout", mcp_out):
            run_stdio_mcp_server()
        mcp_resp = json.loads(mcp_out.buffer.getvalue().decode("utf-8"))
        self.assertFalse(mcp_resp["result"]["isError"])

    def test_cli_aggregate_from_persists_payload_and_satisfies_mise_presubmit_gate(self) -> None:
        _, repo_dir = self._create_multi_project_repo_fixture()
        blocked = evaluate_upload_command("mise presubmit", default_cwd=repo_dir)
        self.assertIsNotNone(blocked)
        self.assertFalse(blocked[0])  # type: ignore[index]

        raw_file = self.base_path / "raw_specialist_outputs_测试_🚀_é.json"
        raw_file.write_text(
            json.dumps([
                {
                    "specialist": "duplication-and-reuse-auditor",
                    "duration_sec": 1.2,
                    "checked_count": 3,
                    "duplicate_count": 0,
                    "finding_count": 0,
                    "findings": [],
                }
            ]),
            encoding="utf-8",
        )
        custom_out = self.base_path / "custom_swarm_out_测试_🚀_é"
        buf = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
        with mock.patch.object(sys, "stdout", buf):
            res = run_harness_review_swarm(
                argparse.Namespace(
                    target="HEAD",
                    cl=None,
                    commit="HEAD",
                    mode="swarm",
                    repo=str(repo_dir),
                    output_dir=str(custom_out),
                    aggregate_from=str(raw_file),
                    full=False,
                    json=True,
                )
            )
        self.assertEqual(res.get("status"), "success")
        self.assertTrue((custom_out / "review_swarm_report.md").is_file())
        self.assertTrue((custom_out / "gerrit_review_payload.json").is_file())
        self.assertTrue((custom_out / "review_swarm_metrics.json").is_file())
        allowed = evaluate_upload_command("mise presubmit", default_cwd=repo_dir)
        self.assertIsNotNone(allowed)
        self.assertTrue(allowed[0], msg=allowed[1])  # type: ignore[index]

    def _seed_clean_head_review_cache(self, repo_dir: Path, cache_root: Path) -> None:
        first_verdict = evaluate_upload_command(
            f'cd "{repo_dir}" && repo upload --cbr -y .',
            review_swarm_cache_root=cache_root,
        )
        self.assertIsNotNone(first_verdict)
        self.assertFalse(first_verdict[0])  # type: ignore[index]
        head_out_dir = next(cache_root.iterdir())
        (head_out_dir / "gerrit_review_payload.json").write_text(
            json.dumps({"message": "0 findings", "notify": "OWNER", "comments": {}}),
            encoding="utf-8",
        )

    def test_chained_head_mutation_before_upload_blocks_pre_tool_gate(self) -> None:
        _, repo_dir = self._create_multi_project_repo_fixture()
        cache_root = self.base_path / "chained_mutation_cache"
        self._seed_clean_head_review_cache(repo_dir, cache_root)

        msg_file = self.base_path / "msg_测试_🚀_é.txt"
        msg_file.write_text(
            "feat(gerrit): Add reviewer edit 测试_🚀_é\n\n"
            "Explain what and why in commit body.\n\n"
            "Bug: N/A\n"
            "Test: bazel test @aemu//tools/emu-dev-cli:review_swarm_test\n"
            "Change-Id: I1234567890abcdef1234567890abcdef12345678\n",
            encoding="utf-8",
        )
        for cmd in (
            f'cd "{repo_dir}" && git add -u && git commit -F "{msg_file}" && repo upload --cbr -y .',
            f'cd "{repo_dir}" && git rebase goog/studio-main && repo upload --cbr -y .',
            f'cd "{repo_dir}" && git cherry-pick abc1234 && git push goog HEAD:refs/for/studio-main',
            f'cd "{repo_dir}" && emu-dev-cli workflow amend-commit && emu-dev-cli gerrit upload',
            f'cd "{repo_dir}" && mise gum && mise presubmit',
            f'cd "{repo_dir}" && repo sync . && repo upload --cbr -y .',
            f'cd "{repo_dir}" && git checkout other-branch && repo upload --cbr -y .',
            f'git -C "{repo_dir}" commit -m "docs: Mention --skip-review-swarm" && repo upload --cbr -y "{repo_dir}"',
        ):
            verdict = evaluate_upload_command(cmd, review_swarm_cache_root=cache_root)
            self.assertIsNotNone(verdict, msg=cmd)
            self.assertFalse(verdict[0], msg=cmd)  # type: ignore[index]
            self.assertIn("Cannot chain a HEAD-mutating command", verdict[1], msg=cmd)  # type: ignore[index]

    def test_pre_tool_hook_denies_chained_commit_upload_and_allows_safe_prefixes(self) -> None:
        _, repo_dir = self._create_multi_project_repo_fixture()
        cache_root = self.base_path / "safe_prefix_cache"
        self._seed_clean_head_review_cache(repo_dir, cache_root)
        msg_file = self.base_path / "hook_msg_测试_🚀_é.txt"
        msg_file.write_text(
            "feat(gerrit): Add reviewer edit 测试_🚀_é\n\nBody.\n\nBug: N/A\nTest: unit\n",
            encoding="utf-8",
        )
        hook_out = dispatch_hook_event(
            HookEvent.PRE_TOOL.value,
            {
                "conversationId": "conv-chained-upload",
                "workspacePaths": [str(repo_dir)],
                "toolCall": {
                    "name": "run_command",
                    "args": {
                        "CommandLine": (
                            f'cd "{repo_dir}" && git commit -F "{msg_file}" && '
                            "repo upload --cbr -y ."
                        )
                    },
                },
            },
        )
        self.assertEqual(hook_out["decision"], HookDecision.DENY.value)
        self.assertIn("Cannot chain a HEAD-mutating command", hook_out["reason"])

        for safe_cmd in (
            f'cd "{repo_dir}" && git status && repo upload --cbr -y .',
            f'cd "{repo_dir}" && git checkout -- README.md && repo upload --cbr -y .',
            f'cd "{repo_dir}" && grep -C 3 DeviceHub README.md ; repo upload --cbr -y .',
        ):
            safe_verdict = evaluate_upload_command(safe_cmd, review_swarm_cache_root=cache_root)
            self.assertIsNotNone(safe_verdict, msg=safe_cmd)
            self.assertTrue(safe_verdict[0], msg=f"{safe_cmd}: {safe_verdict[1]}")  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
