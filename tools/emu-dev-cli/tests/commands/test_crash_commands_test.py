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

"""Hermetic zero-patch DI unit tests for `commands.crash` Command classes."""

import argparse
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence
import unittest

_SRC_DIR = str(Path(__file__).resolve().parents[2] / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from commands.crash import (
    CrashAnalyzeCommand,
    CrashAutofixCommand,
    CrashCommand,
    CrashFileBugCommand,
    CrashFindBugCommand,
    CrashLinkBugCommand,
    CrashListCrashesCommand,
    CrashReproduceCommand,
    register_parser,
)
from core.command import CommandValidationError
from core.context import (
    ExecutionContext,
    FakeServiceFactory,
    SERVICE_AGENT_RUNTIME,
    SERVICE_ARTIFACT,
    SERVICE_BUGANIZER,
    SERVICE_F1_SQL,
)
from core.dispatcher import CliDispatcher
from core.process import FakeProcessRunner, ProcessResult


class _FakeF1Client:
    """In-memory fake F1SqlClient for crash command tests."""

    def __init__(
        self,
        *,
        csv_rows: Optional[List[Dict[str, str]]] = None,
        jsonl_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.csv_rows = list(csv_rows or [])
        self.jsonl_rows = list(jsonl_rows or [])
        self.queries: List[str] = []

    def query_csv(
        self,
        sql: str,
        *,
        accounting_group: Optional[str] = None,
        timeout_sec: Optional[float] = None,
    ) -> List[Dict[str, str]]:
        del accounting_group, timeout_sec
        self.queries.append(sql)
        return list(self.csv_rows)

    def query_jsonl(
        self,
        sql: str,
        *,
        accounting_group: Optional[str] = None,
        timeout_sec: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        del accounting_group, timeout_sec
        self.queries.append(sql)
        return list(self.jsonl_rows)


class _FakeBuganizerClient:
    """In-memory fake BuganizerService for crash command tests."""

    def __init__(self, *, created_bug_id: str = "599887766") -> None:
        self.created_bug_id = created_bug_id
        self.created_bugs: List[Dict[str, Any]] = []
        self.comments: List[Dict[str, Any]] = []

    def create_bug(
        self,
        *,
        title: str,
        description: str,
        component_id: int | str,
        assignee: Optional[str] = None,
        priority: str = "P2",
        issue_type: str = "BUG",
        status: Optional[str] = None,
        cc: Optional[Sequence[str]] = None,
    ) -> str:
        self.created_bugs.append(
            {
                "title": title,
                "description": description,
                "component_id": str(component_id),
                "assignee": assignee,
                "priority": priority,
                "issue_type": issue_type,
                "status": status,
                "cc": list(cc or ()),
            }
        )
        return self.created_bug_id

    def add_comment(self, bug_id: int | str, comment: str) -> bool:
        self.comments.append({"bug_id": str(bug_id), "comment": comment})
        return True

    def search_issues(
        self, query: str, *, limit: int = 25, **kwargs: Any
    ) -> List[Dict[str, Any]]:
        del query, limit, kwargs
        return []


class _FakeAgentRuntime:
    """In-memory fake AgentRuntimeService for crash autofix tests."""

    def __init__(self) -> None:
        self.sent_prompts: List[Dict[str, Any]] = []

    def send_prompt(
        self,
        prompt: str,
        *,
        conversation_id: Optional[str] = None,
        timeout_sec: float = 60.0,
    ) -> ProcessResult:
        self.sent_prompts.append(
            {
                "prompt": prompt,
                "conversation_id": conversation_id,
                "timeout_sec": timeout_sec,
            }
        )
        return ProcessResult(
            args=("agentapi", "send_message"),
            returncode=0,
            stdout='{"status": "ok"}',
            stderr="",
        )


class _FakeArtifactService:
    """In-memory fake ArtifactService for crash reproduce tests."""

    def __init__(self, files_to_write: Optional[Dict[str, bytes]] = None) -> None:
        self.files_to_write = dict(files_to_write or {})
        self.fetch_calls: List[Dict[str, Any]] = []

    def fetch_artifact(
        self,
        *,
        build_id: str,
        target: str,
        artifact_name: str,
        dest_dir: str | Path,
        zip_entry: Optional[str] = None,
        output_filename: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> Path:
        self.fetch_calls.append(
            {
                "build_id": build_id,
                "target": target,
                "artifact_name": artifact_name,
                "zip_entry": zip_entry,
            }
        )
        dest_path = Path(dest_dir)
        dest_path.mkdir(parents=True, exist_ok=True)
        out_name = output_filename or Path(zip_entry or artifact_name).name
        out_file = dest_path / out_name
        payload = self.files_to_write.get(out_name, b"ELF_FAKE_BINARY")
        out_file.write_bytes(payload)
        return out_file


class CrashCommandsTest(unittest.TestCase):
    """Tests Command-pattern crash subcommands with FakeServiceFactory and FakeProcessRunner."""

    def test_crash_list_crashes_validate_rejects_non_positive_hours(self) -> None:
        ctx = ExecutionContext.for_testing()
        cmd = CrashListCrashesCommand()
        bad_args = argparse.Namespace(hours=0, limit=10, json=True)
        with self.assertRaises(CommandValidationError) as err_ctx:
            cmd.validate(bad_args, ctx)
        self.assertIn("hours", str(err_ctx.exception))

    def test_crash_list_crashes_executes_via_injected_f1_service_with_unicode(
        self,
    ) -> None:
        fake_f1 = _FakeF1Client(
            csv_rows=[
                {
                    "stable_signature": "android::emulation::测试_🚀_é::Crash",
                    "crash_count": "7",
                    "sample_report_id": "05d8356e2f800000",
                    "versions": "37.1.5.0-16500000",
                    "os_platforms": "Linux",
                    "bug_ids": "570000001",
                    "notes": "Unicode note 测试_🚀_é",
                }
            ]
        )
        factory = FakeServiceFactory(
            services={SERVICE_F1_SQL: fake_f1},
        )
        ctx = ExecutionContext.for_testing(factory=factory)
        cmd = CrashListCrashesCommand()
        args = argparse.Namespace(
            hours=24,
            version="37.%",
            unassigned_only=False,
            include_known=True,
            include_modules=None,
            exclude_modules=None,
            limit=25,
            print_sql=False,
            json=True,
        )
        validated = cmd.validate(args, ctx)
        res = cmd.execute(validated, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(len(fake_f1.queries), 1)
        self.assertIn("AndroidEmulator", fake_f1.queries[0])
        self.assertEqual(len(res.data["crashes"]), 1)
        self.assertIn("测试_🚀_é", res.data["crashes"][0]["stable_signature"])

    def test_crash_link_bug_executes_via_injected_f1_and_runner_with_unicode(
        self,
    ) -> None:
        fake_f1 = _FakeF1Client(
            csv_rows=[
                {
                    "stable_signature": "sig_测试_🚀_é",
                }
            ]
        )
        fake_runner = FakeProcessRunner()
        fake_runner.register_prefix(
            ["stubby"],
            ProcessResult(
                args=("stubby",),
                returncode=0,
                stdout="status_code: SUCCESS",
                stderr="",
            ),
        )
        factory = FakeServiceFactory(
            process_runner=fake_runner,
            services={SERVICE_F1_SQL: fake_f1},
        )
        ctx = ExecutionContext.for_testing(factory=factory)
        cmd = CrashLinkBugCommand()
        args = argparse.Namespace(
            target="05d8356e2f800000",
            bug="b/571234567",
            unlink=False,
            json=True,
        )
        validated = cmd.validate(args, ctx)
        res = cmd.execute(validated, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(len(fake_f1.queries), 1)
        self.assertGreaterEqual(len(fake_runner.calls), 1)
        self.assertIn("571234567", fake_runner.calls[0].args[-1])

    def test_crash_file_bug_creates_and_links_via_injected_services_with_unicode(
        self,
    ) -> None:
        fake_buganizer = _FakeBuganizerClient(created_bug_id="588112233")
        fake_f1 = _FakeF1Client(csv_rows=[])
        fake_runner = FakeProcessRunner()
        fake_runner.register_prefix(
            ["stubby"],
            ProcessResult(
                args=("stubby",),
                returncode=0,
                stdout="status_code: SUCCESS",
                stderr="",
            ),
        )
        factory = FakeServiceFactory(
            process_runner=fake_runner,
            services={
                SERVICE_BUGANIZER: fake_buganizer,
                SERVICE_F1_SQL: fake_f1,
            },
        )
        ctx = ExecutionContext.for_testing(factory=factory)
        cmd = CrashFileBugCommand()

        with tempfile.TemporaryDirectory() as tmp_dir:
            rca_file = Path(tmp_dir) / "rca_测试_🚀_é.md"
            rca_file.write_text(
                "# Title: [Crash][Linux] Vulkan: Unicode crash 测试_🚀_é\n\n"
                "Root cause analysis for 测试_🚀_é.\n",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                crash_id="05d8356e2f800000",
                from_rca=str(rca_file),
                title=None,
                signature="vulkan_sig_测试_🚀_é",
                bug_id=None,
                component_id="1016880",
                priority="P2",
                assignee=None,
                no_link_crash=False,
                overwrite_link=False,
                token=None,
                json=True,
            )
            validated = cmd.validate(args, ctx)
            res = cmd.execute(validated, ctx)
            self.assertTrue(res.ok)
            self.assertEqual(res.exit_code, 0)

        self.assertEqual(len(fake_buganizer.created_bugs), 1)
        self.assertIn("测试_🚀_é", fake_buganizer.created_bugs[0]["title"])
        self.assertEqual(len(fake_runner.calls), 1)

    def test_crash_autofix_find_bug_reproduce_and_analyze_validation(self) -> None:
        ctx = ExecutionContext.for_testing()
        for cmd in (
            CrashFindBugCommand(),
            CrashAutofixCommand(),
            CrashReproduceCommand(),
            CrashAnalyzeCommand(),
        ):
            with self.assertRaises(CommandValidationError):
                cmd.validate(argparse.Namespace(crash_id="   "), ctx)

    def test_crash_parser_binds_command_instances_for_dispatcher(self) -> None:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        register_parser(subparsers)

        parsed = parser.parse_args(["crash", "list-crashes", "--hours", "12", "--json"])
        dispatcher = CliDispatcher()
        resolved = dispatcher.resolve_command(parsed)
        self.assertIsInstance(resolved, CrashListCrashesCommand)
        self.assertEqual(CrashCommand().name, "crash")


if __name__ == "__main__":
    unittest.main()
