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

"""Unit tests for the Android Emulator Custom Agent Harness (`emu-dev-cli harness`)."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import yaml

from commands.harness.constants import (
    HookDecision,
    HookEvent,
    MAX_STOP_CONTINUATIONS,
)
from commands.harness.guardrails import (
    dispatch_hook_event,
    evaluate_gerrit_command,
    evaluate_git_commit_command,
)
from commands.harness.ledger import SessionLedgerStore
from commands.harness.plugin_installer import (
    get_harness_plugin_status,
    install_harness_plugin,
    uninstall_harness_plugin,
)


class HarnessGuardrailTest(unittest.TestCase):
    """Tests PreToolUse, PostToolUse, PreInvocation, and Stop hook guardrails."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.ledger_store = SessionLedgerStore(base_dir=self.base_path / "harness")
        self.workflow_state_root = self.base_path / "workflows" / "state"
        self.workflow_state_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_git_commit_guardrail_blocks_non_semantic_inline_message(self) -> None:
        verdict = evaluate_git_commit_command('git commit -m "fix broken build"')
        self.assertIsNotNone(verdict)
        allowed, reason = verdict  # type: ignore[misc]
        self.assertFalse(allowed)
        self.assertIn("Semantic Commit", reason)

    def test_git_commit_guardrail_allows_amend_no_edit(self) -> None:
        verdict = evaluate_git_commit_command("git commit --amend --no-edit")
        self.assertIsNotNone(verdict)
        allowed, _ = verdict  # type: ignore[misc]
        self.assertTrue(allowed)

    def test_git_commit_guardrail_allows_valid_unicode_commit_file(self) -> None:
        msg_file = self.base_path / "commit_msg_测试_🚀.txt"
        msg_file.write_text(
            "feat(harness): Support UTF-8 paths 测试_🚀_é\n\n"
            "Ensure commit message validation handles non-ASCII filenames and\n"
            "descriptions across Linux, macOS, and Windows.\n\n"
            "Bug: 485291270\n"
            "Test: python3 -m unittest tests/harness_test.py\n"
            "Change-Id: I1234567890abcdef1234567890abcdef12345678\n",
            encoding="utf-8",
        )
        verdict = evaluate_git_commit_command(f'git commit -F "{msg_file}"')
        self.assertIsNotNone(verdict)
        allowed, reason = verdict  # type: ignore[misc]
        self.assertTrue(allowed, msg=reason)

    def test_git_commit_guardrail_blocks_invalid_commit_file(self) -> None:
        msg_file = self.base_path / "bad_commit.txt"
        msg_file.write_text(
            "feat: lowercase description without body.\n\n"
            "Bug: 485291270\n"
            "Test: pytest\n",
            encoding="utf-8",
        )
        verdict = evaluate_git_commit_command(f'git commit -F "{msg_file}"')
        self.assertIsNotNone(verdict)
        allowed, reason = verdict  # type: ignore[misc]
        self.assertFalse(allowed)
        self.assertIn("capital letter", reason)
        self.assertIn("explanatory body", reason)

    def test_gerrit_guardrail_blocks_unsafe_commands_and_missing_notify_owner(self) -> None:
        v1 = evaluate_gerrit_command("gerrit vote 43134925 Code-Review=1")
        self.assertIsNotNone(v1)
        self.assertFalse(v1[0])  # type: ignore[index]

        v2 = evaluate_gerrit_command("gerrit comments publish 43134925")
        self.assertIsNotNone(v2)
        self.assertFalse(v2[0])  # type: ignore[index]

        v3 = evaluate_gerrit_command("gerrit comments publish --notify=OWNER 43134925")
        self.assertIsNotNone(v3)
        self.assertTrue(v3[0])  # type: ignore[index]

        v4 = evaluate_gerrit_command(
            'gob-curl -X POST -d \'{"message":"LGTM"}\' '
            "https://googleplex-android-review.git.corp.google.com/a/changes/123/revisions/current/review"
        )
        self.assertIsNotNone(v4)
        self.assertFalse(v4[0])  # type: ignore[index]

        v5 = evaluate_gerrit_command(
            'gob-curl -X POST -d \'{"message":"LGTM","notify":"OWNER"}\' '
            "https://googleplex-android-review.git.corp.google.com/a/changes/123/revisions/current/review"
        )
        self.assertIsNotNone(v5)
        self.assertTrue(v5[0])  # type: ignore[index]

    def test_pre_tool_and_post_tool_update_session_ledger(self) -> None:
        conv_id = "conv-test-123"
        deny_out = dispatch_hook_event(
            HookEvent.PRE_TOOL.value,
            {
                "conversationId": conv_id,
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": 'git commit -m "wip"'},
                },
            },
            store=self.ledger_store,
        )
        self.assertEqual(deny_out["decision"], HookDecision.DENY.value)

        dispatch_hook_event(
            HookEvent.POST_TOOL.value,
            {
                "conversationId": conv_id,
                "toolCall": {
                    "name": "run_command",
                    "args": {
                        "CommandLine": "emu-dev-cli launch emulator -- -avd Pixel_Fold_API_35"
                    },
                },
            },
            store=self.ledger_store,
        )
        ledger = self.ledger_store.load(conv_id)
        self.assertEqual(ledger["stats"]["guardrails_blocked"], 1)
        self.assertEqual(ledger["stats"]["emulators_launched"], 1)
        self.assertEqual(len(ledger["active_emulators"]), 1)
        self.assertEqual(ledger["active_emulators"][0]["avd"], "Pixel_Fold_API_35")

        dispatch_hook_event(
            HookEvent.POST_TOOL.value,
            {
                "conversationId": conv_id,
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "adb -s emulator-5554 emu kill"},
                },
            },
            store=self.ledger_store,
        )
        ledger_after = self.ledger_store.load(conv_id)
        self.assertEqual(len(ledger_after["active_emulators"]), 0)

    def test_pre_invocation_and_stop_hook_workflow_continuation(self) -> None:
        conv_id = "conv-wf-999"
        wf_dir = self.workflow_state_root / "create-commit"
        wf_dir.mkdir(parents=True, exist_ok=True)
        state_file = wf_dir / "STATE-abc12345.yaml"
        state_file.write_text(
            yaml.safe_dump({
                "workflow_name": "create-commit",
                "state_id": "abc12345",
                "status": "RUNNING",
                "current_step": 1,
                "retries": 0,
                "metadata": {"conversation_id": conv_id},
            }),
            encoding="utf-8",
        )

        pre_inv = dispatch_hook_event(
            HookEvent.PRE_INVOCATION.value,
            {"conversationId": conv_id},
            store=self.ledger_store,
            state_root=self.workflow_state_root,
        )
        self.assertIn("injectSteps", pre_inv)
        ephemeral = pre_inv["injectSteps"][0]["ephemeralMessage"]
        self.assertIn("create-commit", ephemeral)
        self.assertIn("abc12345", ephemeral)

        for _ in range(MAX_STOP_CONTINUATIONS):
            stop_out = dispatch_hook_event(
                HookEvent.STOP.value,
                {"conversationId": conv_id},
                store=self.ledger_store,
                state_root=self.workflow_state_root,
            )
            self.assertEqual(stop_out["decision"], HookDecision.CONTINUE.value)

        final_stop = dispatch_hook_event(
            HookEvent.STOP.value,
            {"conversationId": conv_id},
            store=self.ledger_store,
            state_root=self.workflow_state_root,
        )
        self.assertEqual(final_stop["decision"], HookDecision.STOP.value)

    def test_code_guardrail_blocks_raw_cc_binary_in_goldfish(self) -> None:
        deny_out = dispatch_hook_event(
            HookEvent.PRE_TOOL.value,
            {
                "conversationId": "conv-code-1",
                "toolCall": {
                    "name": "write_file",
                    "args": {
                        "Path": "/work/goldfish/emulator/foo/BUILD.bazel",
                        "Content": 'cc_binary(\n    name = "foo_tool",\n    srcs = ["main.cc"],\n)\n',
                    },
                },
            },
            store=self.ledger_store,
        )
        self.assertEqual(deny_out["decision"], HookDecision.DENY.value)
        self.assertIn("cc_binary_with_manifest", deny_out["reason"])

        allow_plugin = dispatch_hook_event(
            HookEvent.PRE_TOOL.value,
            {
                "conversationId": "conv-code-1",
                "toolCall": {
                    "name": "write_file",
                    "args": {
                        "Path": "/work/goldfish/emulator/foo/BUILD.bazel",
                        "Content": 'cc_binary(\n    name = "libplugin.so",\n    linkshared = True,\n)\n',
                    },
                },
            },
            store=self.ledger_store,
        )
        self.assertEqual(allow_plugin["decision"], HookDecision.ALLOW.value)

    def test_code_guardrail_blocks_raw_fstream_and_statusor_hungarian(self) -> None:
        deny_fstream = dispatch_hook_event(
            HookEvent.PRE_TOOL.value,
            {
                "conversationId": "conv-code-2",
                "toolCall": {
                    "name": "edit_file",
                    "args": {
                        "Path": "/work/goldfish/emulator/net/client.cc",
                        "Replacement": 'std::ifstream in("测试_🚀.ini");\n',
                    },
                },
            },
            store=self.ledger_store,
        )
        self.assertEqual(deny_fstream["decision"], HookDecision.DENY.value)
        self.assertIn("android::base::file", deny_fstream["reason"])

        deny_statusor = dispatch_hook_event(
            HookEvent.PRE_TOOL.value,
            {
                "conversationId": "conv-code-2",
                "toolCall": {
                    "name": "edit_file",
                    "args": {
                        "Path": "/work/goldfish/emulator/net/client.cc",
                        "Replacement": "auto config_or = LoadConfig();\n",
                    },
                },
            },
            store=self.ledger_store,
        )
        self.assertEqual(deny_statusor["decision"], HookDecision.DENY.value)
        self.assertIn("TotW 181", deny_statusor["reason"])

    def test_pre_tool_command_auto_rewrite_overwrite(self) -> None:
        conv_id = "conv-rewrite-1"
        gerrit_out = dispatch_hook_event(
            HookEvent.PRE_TOOL.value,
            {
                "conversationId": conv_id,
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "gerrit comments publish 43134925"},
                },
            },
            store=self.ledger_store,
        )
        self.assertEqual(gerrit_out["decision"], HookDecision.ALLOW.value)
        self.assertEqual(
            gerrit_out["overwrite"]["CommandLine"],
            "gerrit comments publish --notify=OWNER 43134925",
        )

        bazel_out = dispatch_hook_event(
            HookEvent.PRE_TOOL.value,
            {
                "conversationId": conv_id,
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "bazel test //emulator/libs/file:all"},
                },
            },
            store=self.ledger_store,
        )
        self.assertEqual(bazel_out["decision"], HookDecision.ALLOW.value)
        self.assertEqual(
            bazel_out["overwrite"]["CommandLine"],
            "bazel test --test_output=errors //emulator/libs/file:all",
        )
        ledger = self.ledger_store.load(conv_id)
        self.assertEqual(ledger["stats"]["commands_rewritten"], 2)

    def test_post_tool_failure_enrichment_overwrite_result(self) -> None:
        conv_id = "conv-diag-1"
        post_out = dispatch_hook_event(
            HookEvent.POST_TOOL.value,
            {
                "conversationId": conv_id,
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "bazel test //emulator/net:client_test"},
                },
                "error": "exit status 1",
                "result": (
                    "emulator/net/client.cc:12:10: fatal error: "
                    "'goldfish/http/web_server.h' file not found\n"
                    "WARNING: ThreadSanitizer: data race (pid=1234)\n"
                ),
            },
            store=self.ledger_store,
        )
        self.assertIn("overwriteResult", post_out)
        self.assertIn("[AEMU Harness Auto-Diagnostic]", post_out["overwriteResult"])
        self.assertIn("//emulator/libs/http", post_out["overwriteResult"])
        self.assertIn("--config=tsan", post_out["overwriteResult"])
        ledger = self.ledger_store.load(conv_id)
        self.assertEqual(ledger["stats"]["diagnostics_injected"], 1)


class HarnessPluginInstallerAndSidecarTest(unittest.TestCase):
    """Tests plugin installation, status verification, and sidecar state helpers."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_install_verify_and_uninstall_harness_plugin(self) -> None:
        target_plugin_dir = self.base_path / "plugins" / "aemu-harness"
        report = install_harness_plugin(
            target_dir=target_plugin_dir,
            cli_command="/custom/bin/emu-dev-cli",
        )
        self.assertTrue(report["installed"], msg=str(report))

        status = get_harness_plugin_status(target_dir=target_plugin_dir)
        self.assertTrue(status["installed"])
        self.assertEqual(status["missing_files"], [])

        hooks_json = json.loads(
            (target_plugin_dir / "hooks.json").read_text(encoding="utf-8")
        )
        pre_tool_cmd = hooks_json["PreToolUse"][0]["hooks"][0]["command"]
        self.assertIn("/custom/bin/emu-dev-cli harness hook --event pre-tool", pre_tool_cmd)

        sidecar_server = target_plugin_dir / "sidecars" / "aemu_harness_ui" / "server.py"
        spec = importlib.util.spec_from_file_location("aemu_harness_sidecar", sidecar_server)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        ledger_data = module.load_ledger_data("conv-sidecar-test")
        self.assertEqual(ledger_data["conversation_id"], "conv-sidecar-test")

        uninstall_report = uninstall_harness_plugin(target_dir=target_plugin_dir)
        self.assertTrue(uninstall_report["removed"])
        self.assertFalse(target_plugin_dir.exists())


if __name__ == "__main__":
    unittest.main()
