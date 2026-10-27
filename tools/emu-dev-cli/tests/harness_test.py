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
from typing import Any, Dict, Tuple
import unittest
import yaml

from commands.harness.code_guardrails import grpc_utils_advisories
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
from commands.harness.mcp_server import handle_mcp_jsonrpc_request
from commands.harness.plugin_installer import (
    get_harness_plugin_status,
    install_harness_plugin,
    resolve_bundled_plugin_source_dir,
    uninstall_harness_plugin,
)
from commands.harness.presets import list_harness_presets


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

    def _file_edit_hooks(
        self, conv_id: str, tool: str, args: Dict[str, Any], result: str = "Created file."
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Runs the PreToolUse and PostToolUse hooks for one file edit."""
        tool_call = {"name": tool, "args": args}
        pre = dispatch_hook_event(
            HookEvent.PRE_TOOL.value,
            {"conversationId": conv_id, "toolCall": tool_call},
            store=self.ledger_store,
        )
        post = dispatch_hook_event(
            HookEvent.POST_TOOL.value,
            {"conversationId": conv_id, "toolCall": tool_call, "result": result, "error": ""},
            store=self.ledger_store,
        )
        return pre, post

    def test_grpc_advisories_allow_the_edit_and_append_guidance(self) -> None:
        conv_id = "conv-grpc-advisory"
        battery_cc = "/work/goldfish/emulator/plugin/grpc/services/v2/battery/测试_🚀_é.cc"
        raw_reactor = (
            "class CustomBatteryStream final\n"
            "    : public ::grpc::ServerWriteReactor<BatteryState> {\n"
            " public:\n"
            "  void OnDone() override { delete this; }\n"
            "};\n"
        )
        status_switch = (
            "switch (status.code()) {\n"
            "  case absl::StatusCode::kInvalidArgument:\n"
            "    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, \"bad\");\n"
            "  default:\n"
            "    return grpc::Status::OK;\n"
            "}\n"
        )

        # The raw reactor is allowed, and the applied edit's result carries the advice.
        pre, post = self._file_edit_hooks(
            conv_id, "write_file", {"Path": battery_cc, "Content": raw_reactor}
        )
        self.assertEqual(pre, {"decision": HookDecision.ALLOW.value})
        advisory = post["overwriteResult"]
        self.assertTrue(advisory.startswith("Created file.\n\n[AEMU Harness Code Advisory]"))
        self.assertIn("Non-blocking: the edit was applied", advisory)
        self.assertIn("'CustomBatteryStream' subclasses raw 'grpc::ServerWriteReactor'", advisory)
        self.assertIn("StateStreamWriter", advisory)
        self.assertIn("emulator/plugin/grpc/AGENTS.md", advisory)
        self.assertNotIn("Blocked by", advisory)

        # Several legacy sites in one edit yield one note with one line per pattern.
        pre, post = self._file_edit_hooks(
            conv_id,
            "edit_file",
            {
                "Path": battery_cc,
                "Replacement": raw_reactor
                + raw_reactor.replace("CustomBatteryStream", "Other")
                + status_switch
                + "return ToGrpcStatus(status);\n",
            },
            result="Made 1 replacement.",
        )
        self.assertEqual(pre["decision"], HookDecision.ALLOW.value)
        advisory = post["overwriteResult"]
        self.assertEqual(advisory.count("[AEMU Harness Code Advisory]"), 1)
        self.assertEqual(advisory.count("\n  * "), 2)
        self.assertIn("'grpc::ServerWriteReactor' (+1 more)", advisory)
        self.assertIn("'switch (status.code())' (+1 more)", advisory)
        self.assertIn("AbslStatusToGrpcStatus", advisory)
        self.assertIn("absl_status_translate.h", advisory)

        ledger = self.ledger_store.load(conv_id)
        self.assertEqual(ledger["stats"]["code_advisories"], 2)
        self.assertEqual(ledger["stats"]["guardrails_blocked"], 0)

        # Code that already uses grpc_utils, a failed edit, and grpc_utils itself get no advice.
        valid_header = (
            "template <typename Reactor = ::grpc::ServerWriteReactor<SystemState>>\n"
            "using SystemStateWriter = ::android::emulation::control::StateStreamWriter<\n"
            "    SystemState, int, StateChangeEventSource, 128, true, Reactor>;\n"
            "class SystemServiceImpl final {\n"
            " public:\n"
            "  ::grpc::ServerWriteReactor<SystemState>* StreamSystemState(\n"
            "      ::grpc::CallbackServerContext* context,\n"
            "      const ::google::protobuf::Empty* request);\n"
            "};\n"
            "return AbslStatusToGrpcStatus(status);\n"
        )
        _, post = self._file_edit_hooks(
            conv_id,
            "write_file",
            {"Path": battery_cc.replace("测试_🚀_é", "system_service_impl"), "Content": valid_header},
        )
        self.assertEqual(post, {})
        failed = dispatch_hook_event(
            HookEvent.POST_TOOL.value,
            {
                "conversationId": conv_id,
                "toolCall": {"name": "write_file", "args": {"Path": battery_cc, "Content": raw_reactor}},
                "result": "",
                "error": "permission denied",
            },
            store=self.ledger_store,
        )
        self.assertEqual(failed, {})
        self.assertEqual(
            grpc_utils_advisories(
                "/work/goldfish/emulator/libs/grpc_utils/include/android/emulation/control/"
                "simple_async_grpc.h",
                "class WithSimpleQueueWriter : public grpc::ServerWriteReactor<T> {};\n",
            ),
            [],
        )

    def test_pre_invocation_injects_grpc_playbook_context_when_working_in_grpc_plugin(self) -> None:
        conv_id = "conv-grpc-pre-inv"
        # 1. Unrelated workspace/prompt returns empty dict when no workflow/emulator is active
        unrelated = dispatch_hook_event(
            HookEvent.PRE_INVOCATION.value,
            {
                "conversationId": conv_id,
                "activeFile": "/work/goldfish/emulator/net/http_client.cc",
                "prompt": "Fix timeout handling in HttpClient",
            },
            store=self.ledger_store,
            state_root=self.workflow_state_root,
        )
        self.assertEqual(unrelated, {})

        # 2. Prompt or activeFile under emulator/plugin/grpc injects gRPC playbook context
        grpc_inv = dispatch_hook_event(
            HookEvent.PRE_INVOCATION.value,
            {
                "conversationId": conv_id,
                "activeFile": (
                    "/work/goldfish/emulator/plugin/grpc/services/v2/"
                    "battery/battery_service_impl.cc"
                ),
            },
            store=self.ledger_store,
            state_root=self.workflow_state_root,
        )
        self.assertIn("injectSteps", grpc_inv)
        msg = grpc_inv["injectSteps"][0]["ephemeralMessage"]
        self.assertIn("emulator/plugin/grpc/AGENTS.md", msg)
        self.assertIn("//emulator/libs/grpc_utils", msg)
        self.assertIn("StateStreamWriter", msg)
        self.assertIn("SimpleServerLambdaReader", msg)
        self.assertIn("AbslStatusToGrpcStatus", msg)

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
        self.assertIn("//emulator/libs/http:web_server", post_out["overwriteResult"])
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
        pre_tool_cmd = hooks_json["aemu-harness"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertIn("/custom/bin/emu-dev-cli harness hook --event pre-tool", pre_tool_cmd)
        # Code advisories ride on the PostToolUse result of file edits.
        post_tool_matcher = hooks_json["aemu-harness"]["PostToolUse"][0]["matcher"].split("|")
        self.assertTrue({"run_command", "write_file", "edit_file"}.issubset(post_tool_matcher))

        mcp_json = json.loads(
            (target_plugin_dir / "mcp_config.json").read_text(encoding="utf-8")
        )
        mcp_srv = mcp_json["mcpServers"]["aemu-harness-mcp"]
        self.assertEqual(mcp_srv["command"], "/custom/bin/emu-dev-cli")
        self.assertEqual(mcp_srv["args"], ["harness", "mcp-server"])

        sidecar_server = target_plugin_dir / "sidecars" / "aemu_harness_ui" / "server.py"
        spec = importlib.util.spec_from_file_location("aemu_harness_sidecar", sidecar_server)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        ledger_data = module.load_ledger_data("conv-sidecar-test")
        self.assertEqual(ledger_data["conversation_id"], "conv-sidecar-test")

        valid_check = module.validate_commit_message_live(
            "feat(harness): Add live commit sandbox\n\n"
            "Validate commit messages in real time inside the AuxPane.\n\n"
            "Bug: 485291270\n"
            "Test: python3 -m unittest tests/harness_test.py\n"
        )
        self.assertTrue(valid_check["valid"], msg=str(valid_check))
        invalid_check = module.validate_commit_message_live("wip")
        self.assertFalse(invalid_check["valid"])

        uninstall_report = uninstall_harness_plugin(target_dir=target_plugin_dir)
        self.assertTrue(uninstall_report["removed"])
        self.assertFalse(target_plugin_dir.exists())

    def test_mcp_server_jsonrpc_initialize_list_and_call(self) -> None:
        init_res = handle_mcp_jsonrpc_request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertIsNotNone(init_res)
        self.assertEqual(init_res["result"]["serverInfo"]["name"], "aemu-harness-mcp")  # type: ignore[index]

        list_res = handle_mcp_jsonrpc_request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        self.assertIsNotNone(list_res)
        tool_names = [t["name"] for t in list_res["result"]["tools"]]  # type: ignore[index]
        self.assertIn("aemu_validate_commit_message", tool_names)
        self.assertIn("aemu_search_reusable_libs", tool_names)

        libs_res = handle_mcp_jsonrpc_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "aemu_search_reusable_libs",
                    "arguments": {"query": "http"},
                },
            }
        )
        self.assertIsNotNone(libs_res)
        self.assertFalse(libs_res["result"]["isError"])  # type: ignore[index]
        payload = json.loads(libs_res["result"]["content"][0]["text"])  # type: ignore[index]
        targets = [lib["bazel_target"] for lib in payload["libraries"]]
        self.assertIn("//emulator/libs/http:web_server", targets)

    def test_progressive_disclosure_rules_and_swarm_presets(self) -> None:
        presets = list_harness_presets()
        self.assertGreaterEqual(len(presets), 3)
        review_preset = list_harness_presets("parallel-code-review")
        self.assertEqual(len(review_preset), 1)
        self.assertIn("aemu-code-reviewer", review_preset[0]["prompt"])

    def test_resolve_bundled_plugin_source_dir_falls_back_to_source_directory(self) -> None:
        from unittest.mock import patch

        fake_workspace = self.base_path / "workspace"
        fake_plugin_dir = (
            fake_workspace
            / "hardware"
            / "google"
            / "aemu"
            / "tools"
            / "emu-dev-cli"
            / "harness"
            / "plugin"
        )
        fake_plugin_dir.mkdir(parents=True, exist_ok=True)
        (fake_plugin_dir / "plugin.json").write_text('{"name":"aemu-harness"}\n', encoding="utf-8")

        fake_installed_file = (
            self.base_path
            / "empty_install"
            / "lib"
            / "commands"
            / "harness"
            / "plugin_installer.py"
        )
        fake_installed_file.parent.mkdir(parents=True, exist_ok=True)
        fake_installed_file.write_text("# stub\n", encoding="utf-8")

        with patch(
            "commands.harness.plugin_installer.__file__", str(fake_installed_file)
        ), patch(
            "commands.harness.plugin_installer.Path.home",
            return_value=self.base_path / "empty_home",
        ), patch(
            "commands.harness.plugin_installer.Path.cwd",
            return_value=self.base_path / "empty_cwd",
        ), patch(
            "commands.harness.plugin_installer.get_source_directory",
            side_effect=lambda branch: str(fake_workspace) if branch == "emu-main-next" else None,
        ):
            resolved = resolve_bundled_plugin_source_dir()
            self.assertEqual(resolved, fake_plugin_dir)

    def test_install_harness_plugin_accepts_string_target_dir(self) -> None:
        target_str = str(self.base_path / "str_target" / "aemu-harness")
        report = install_harness_plugin(target_dir=target_str)
        self.assertTrue(report["installed"], msg=str(report))
        self.assertTrue(Path(target_str, "plugin.json").is_file())
        reviewer_md = Path(target_str, "agents", "aemu-code-reviewer.md").read_text(encoding="utf-8")
        self.assertIn("- view_file", reviewer_md)
        self.assertNotIn("- read_file", reviewer_md)
        self.assertNotIn("- code_search", reviewer_md)
        deny_multi = dispatch_hook_event(
            HookEvent.PRE_TOOL.value,
            {
                "conversationId": "conv-multi-edit",
                "toolCall": {
                    "name": "multi_replace_file_content",
                    "args": {
                        "TargetFile": "/work/goldfish/emulator/net/client.cc",
                        "ReplacementChunks": [
                            {"ReplacementContent": 'std::ifstream in("测试_🚀_é.ini");\n'}
                        ],
                    },
                },
            },
            store=SessionLedgerStore(base_dir=self.base_path / "harness"),
        )
        self.assertEqual(deny_multi["decision"], HookDecision.DENY.value)

    def _write_valid_doc_commit_msg(self) -> Path:
        msg_file = self.base_path / "valid_msg.txt"
        msg_file.write_text(
            "docs(grpc): Update gRPC agent playbook\n\n"
            "Document reactor invariants and concurrency tests.\n\n"
            "Bug: 485291270\n"
            "Test: emu-dev-cli docs lint\n"
            "Change-Id: I1234567890abcdef1234567890abcdef12345678\n",
            encoding="utf-8",
        )
        return msg_file

    def test_goldfish_doc_drift_guardrails_on_edit_and_commit(self) -> None:
        ledger_store = SessionLedgerStore(base_dir=self.base_path / "harness")
        goldfish = self.base_path / "hardware" / "generic" / "goldfish"
        grpc_dir = goldfish / "emulator" / "plugin" / "grpc"
        grpc_dir.mkdir(parents=True, exist_ok=True)
        (goldfish / "AGENTS.md").write_text(
            "# Goldfish Root Playbook\n\n"
            "**Scope:** `hardware/generic/goldfish/**`\n"
            "- [gRPC](emulator/plugin/grpc/AGENTS.md)\n",
            encoding="utf-8",
        )
        (grpc_dir / "BUILD.bazel").write_text(
            'cc_library(\n    name = "grpc_plugin",\n)\n',
            encoding="utf-8",
        )
        (grpc_dir / "AGENTS.md").write_text(
            "# gRPC Playbook\n\n"
            "**Scope:** `hardware/generic/goldfish/emulator/plugin/grpc/**`\n"
            "Uses `//emulator/plugin/grpc:grpc_plugin`.\n",
            encoding="utf-8",
        )
        arch_path = grpc_dir / "ARCHITECTURE.md"
        arch_path.write_text(
            "# Component: gRPC\n\n"
            "**Location:** `emulator/plugin/grpc/`\n"
            "See [`AGENTS.md`](AGENTS.md).\n",
            encoding="utf-8",
        )

        # 1. PreToolUse blocks write_file on AGENTS.md with missing Bazel target
        deny_bad_target = dispatch_hook_event(
            HookEvent.PRE_TOOL.value,
            {
                "conversationId": "conv-doc-drift",
                "toolCall": {
                    "name": "write_file",
                    "args": {
                        "Path": str(grpc_dir / "AGENTS.md"),
                        "Content": (
                            "# gRPC Playbook\n\n"
                            "Uses `//emulator/plugin/grpc:deleted_target`.\n"
                        ),
                    },
                },
            },
            store=ledger_store,
        )
        self.assertEqual(deny_bad_target["decision"], HookDecision.DENY.value)
        self.assertIn("deleted_target", deny_bad_target["reason"])

        # 2. PreToolUse blocks edit_file on ARCHITECTURE.md that removes AGENTS.md link
        deny_strip_agents = dispatch_hook_event(
            HookEvent.PRE_TOOL.value,
            {
                "conversationId": "conv-doc-drift",
                "toolCall": {
                    "name": "edit_file",
                    "args": {
                        "Path": str(arch_path),
                        "Target": "See [`AGENTS.md`](AGENTS.md).\n",
                        "Replacement": "No playbook link.\n",
                    },
                },
            },
            store=ledger_store,
        )
        self.assertEqual(deny_strip_agents["decision"], HookDecision.DENY.value)
        self.assertIn("AGENTS.md", deny_strip_agents["reason"])

        # 3. PreToolUse on git -C <goldfish> commit blocks when drift exists on disk
        (grpc_dir / "AGENTS.md").write_text(
            "# gRPC Playbook\n\nBroken link to [missing](deleted_header.h).\n",
            encoding="utf-8",
        )
        msg_file = self._write_valid_doc_commit_msg()
        commit_cmd = f'git -C "{goldfish}" commit -F "{msg_file}"'
        verdict_blocked = evaluate_git_commit_command(commit_cmd)
        self.assertIsNotNone(verdict_blocked)
        self.assertFalse(verdict_blocked[0])  # type: ignore[index]
        self.assertIn("Documentation Drift Guardrail", verdict_blocked[1])  # type: ignore[index]
        self.assertIn("deleted_header.h", verdict_blocked[1])  # type: ignore[index]

        # 4. Once drift is fixed, git -C <goldfish> commit is allowed
        (grpc_dir / "AGENTS.md").write_text(
            "# gRPC Playbook\n\n"
            "**Scope:** `hardware/generic/goldfish/emulator/plugin/grpc/**`\n"
            "Uses `//emulator/plugin/grpc:grpc_plugin` and [arch](ARCHITECTURE.md).\n",
            encoding="utf-8",
        )
        verdict_ok = evaluate_git_commit_command(commit_cmd)
        self.assertIsNotNone(verdict_ok)
        self.assertTrue(verdict_ok[0], msg=verdict_ok[1])  # type: ignore[index]

    def test_goldfish_doc_drift_ignores_non_goldfish_emulator_subdir(self) -> None:
        ledger_store = SessionLedgerStore(base_dir=self.base_path / "harness")
        vendor_google = self.base_path / "studio-main" / "tools" / "vendor" / "google"
        (vendor_google / "emulator" / "ui").mkdir(parents=True, exist_ok=True)
        (vendor_google / "optional-plugins" / "lightbuild").mkdir(
            parents=True, exist_ok=True
        )
        (vendor_google / "optional-plugins" / "lightbuild" / "AGENTS.md").write_text(
            "# Studio Plugin\n\nUses `//tools/adt/idea/missing_pkg:target`.\n",
            encoding="utf-8",
        )
        msg_file = self._write_valid_doc_commit_msg()
        vendor_commit_cmd = f'cd "{vendor_google}" && git commit -F "{msg_file}"'
        verdict_vendor = evaluate_git_commit_command(vendor_commit_cmd)
        self.assertIsNotNone(verdict_vendor)
        self.assertTrue(verdict_vendor[0], msg=verdict_vendor[1])  # type: ignore[index]

        allow_vendor_edit = dispatch_hook_event(
            HookEvent.PRE_TOOL.value,
            {
                "conversationId": "conv-doc-drift",
                "toolCall": {
                    "name": "write_file",
                    "args": {
                        "Path": str(vendor_google / "emulator" / "AGENTS.md"),
                        "Content": "# Studio Emulator UI\n\nUses `//tools/missing:pkg`.\n",
                    },
                },
            },
            store=ledger_store,
        )
        self.assertEqual(allow_vendor_edit["decision"], HookDecision.ALLOW.value)

    def test_goldfish_doc_drift_detects_standalone_non_ascii_clone(self) -> None:
        standalone_gf = self.base_path / "qemu_next_测试_🚀_é"
        (standalone_gf / "emulator").mkdir(parents=True, exist_ok=True)
        (standalone_gf / "MODULE.bazel").write_text("# goldfish\n", encoding="utf-8")
        (standalone_gf / "emulator" / "BUILD.bazel").write_text("", encoding="utf-8")
        (standalone_gf / "emulator" / "AGENTS.md").write_text(
            "# Standalone Playbook\n\nBroken link to [missing](gone.h).\n",
            encoding="utf-8",
        )
        msg_file = self._write_valid_doc_commit_msg()
        standalone_cmd = f'git -C "{standalone_gf}" commit -F "{msg_file}"'
        verdict_standalone = evaluate_git_commit_command(standalone_cmd)
        self.assertIsNotNone(verdict_standalone)
        self.assertFalse(verdict_standalone[0])  # type: ignore[index]
        self.assertIn("gone.h", verdict_standalone[1])  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
