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

"""Unit tests for the Workflow Web UI Visualizer."""

import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
import yaml

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_EMU_DEV_CLI_DIR = os.path.dirname(_TESTS_DIR)
if os.path.basename(_TESTS_DIR) == "tests" and os.path.basename(_EMU_DEV_CLI_DIR) == "workflows":
    _EMU_DEV_CLI_DIR = os.path.dirname(_EMU_DEV_CLI_DIR)
_SRC_DIR = os.path.join(_EMU_DEV_CLI_DIR, "src")
_WORKFLOWS_DIR = os.path.join(_EMU_DEV_CLI_DIR, "workflows")

for d in (_SRC_DIR, _EMU_DEV_CLI_DIR, _WORKFLOWS_DIR):
    if d not in sys.path:
        sys.path.insert(0, d)

from workflows.ui.server import (
    clear_sessions,
    delete_session,
    extract_artifacts_from_metadata,
    find_available_port,
    get_session_detail,
    get_ui_html_path,
    scan_all_sessions,
    start_server,
    stop_session,
    WorkflowUIHandler,
)
from commands.workflow import register_parser, run_workflow_command


class WorkflowUITest(unittest.TestCase):

    def setUp(self):
        self.temp_dir = str(Path(tempfile.mkdtemp(prefix="workflow_ui_test_")).resolve())
        self.state_dir = Path(self.temp_dir) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mock_session(
        self,
        workflow_name: str,
        state_id: str,
        state: str = "RUNNING",
        step: int = 1,
        retries: int = 0,
        stuck_reason: str = "",
        metadata: dict = None,
    ):
        wf_dir = self.state_dir / workflow_name
        wf_dir.mkdir(parents=True, exist_ok=True)
        state_file = wf_dir / f"STATE-{state_id}.yaml"
        data = {
            "state": state,
            "step": step,
            "retries": retries,
            "stuck_reason": stuck_reason,
            "metadata": metadata or {},
        }
        with open(state_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f)
        return state_file

    def test_scan_all_sessions_empty(self):
        sessions = scan_all_sessions(state_base_dir=self.state_dir)
        self.assertEqual(sessions, [])

    def test_scan_all_sessions_discovery_and_sorting(self):
        self._create_mock_session(
            workflow_name="wf-one",
            state_id="sess1",
            state="DONE",
            step=3,
            metadata={"bug-number": "123456"},
        )
        time.sleep(0.05)
        self._create_mock_session(
            workflow_name="wf-two",
            state_id="sess2",
            state="STUCK",
            step=1,
            retries=3,
            stuck_reason="Verifier compile failed",
            metadata={
                "joke-file": "/tmp/joke.txt",
                "_sub_workflow": {"name": "sub-wf", "state_id": "child1"},
            },
        )

        sessions = scan_all_sessions(state_base_dir=self.state_dir)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["state_id"], "sess2")
        self.assertEqual(sessions[0]["workflow_name"], "wf-two")
        self.assertEqual(sessions[0]["state"], "STUCK")
        self.assertEqual(sessions[0]["stuck_reason"], "Verifier compile failed")
        self.assertTrue(sessions[0]["has_sub_workflow"])
        self.assertEqual(sessions[0]["sub_workflow_name"], "sub-wf")
        self.assertEqual(sessions[0]["key_args"].get("joke-file"), "/tmp/joke.txt")

        self.assertEqual(sessions[1]["state_id"], "sess1")
        self.assertEqual(sessions[1]["workflow_name"], "wf-one")
        self.assertEqual(sessions[1]["state"], "DONE")
        self.assertEqual(sessions[1]["key_args"].get("bug-number"), "123456")

    def test_scan_all_sessions_sub_workflow_filtering(self):
        # Parent workflow
        self._create_mock_session(
            workflow_name="parent-pipe",
            state_id="parent1",
            state="DONE",
            metadata={
                "_call_stack": ["parent-pipe"],
                "_sub_workflow_history": [
                    {"name": "child-wf", "state_id": "child1", "parent_step": 0}
                ],
            },
        )
        # Child sub-workflow
        self._create_mock_session(
            workflow_name="child-wf",
            state_id="child1",
            state="DONE",
            metadata={
                "_call_stack": ["parent-pipe", "child-wf"],
            },
        )

        # By default, child sub-workflows are excluded from the root session list
        top_level = scan_all_sessions(state_base_dir=self.state_dir, include_sub_workflows=False)
        self.assertEqual(len(top_level), 1)
        self.assertEqual(top_level[0]["state_id"], "parent1")
        self.assertEqual(top_level[0]["workflow_name"], "parent-pipe")

        # When include_sub_workflows=True, both are returned
        all_sessions = scan_all_sessions(state_base_dir=self.state_dir, include_sub_workflows=True)
        self.assertEqual(len(all_sessions), 2)

    def test_get_session_detail(self):
        artifact_path = Path(self.temp_dir) / "sample_output.txt"
        artifact_path.write_text("Test content", encoding="utf-8")

        self._create_mock_session(
            workflow_name="hello-world-example",
            state_id="detail_sess",
            state="RUNNING",
            step=0,
            metadata={"hello-file": str(artifact_path)},
        )

        detail = get_session_detail(
            "hello-world-example",
            "detail_sess",
            state_base_dir=self.state_dir,
        )
        self.assertIsNotNone(detail)
        self.assertEqual(detail["workflow_name"], "hello-world-example")
        self.assertEqual(detail["state"], "RUNNING")
        self.assertEqual(detail["step"], 0)
        self.assertTrue(len(detail["steps"]) > 0)
        self.assertEqual(detail["steps"][0]["status"], "running")
        self.assertIn("emu-dev-cli workflow hello-world-example --state=detail_sess", detail["continuation_cmd"])

        artifacts = detail["artifacts"]
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["filename"], "sample_output.txt")
        self.assertEqual(artifacts[0]["path"], str(artifact_path.resolve()))

    def test_get_session_detail_stopped(self):
        self._create_mock_session(
            workflow_name="hello-world-example",
            state_id="stopped_sess",
            state="STOPPED",
            step=1,
            stuck_reason="Workflow stopped by user via Web UI",
        )
        detail = get_session_detail(
            "hello-world-example",
            "stopped_sess",
            state_base_dir=self.state_dir,
        )
        self.assertIsNotNone(detail)
        self.assertEqual(detail["state"], "STOPPED")
        self.assertEqual(detail["stuck_reason"], "Workflow stopped by user via Web UI")
        self.assertEqual(detail["steps"][0]["status"], "completed")
        self.assertEqual(detail["steps"][1]["status"], "stopped")

    def test_extract_artifacts_recursive(self):
        file1 = Path(self.temp_dir) / "output1.txt"
        file1.write_text("Hello", encoding="utf-8")
        file2 = Path(self.temp_dir) / "output2.md"
        file2.write_text("Rating", encoding="utf-8")

        metadata = {
            "top_file": str(file1),
            "sub_wf_name": {
                "nested_file": str(file2),
                "ignore_dir": "/some/nonexistent/path.txt",
            },
            "_sub_workflow": {
                "name": "ignored_internal",
                "state_id": "ignored",
            },
        }

        artifacts = extract_artifacts_from_metadata(metadata)
        self.assertEqual(len(artifacts), 2)
        paths = {a["filename"]: a for a in artifacts}
        self.assertIn("output1.txt", paths)
        self.assertEqual(paths["output1.txt"]["key"], "top_file")
        self.assertIsNone(paths["output1.txt"]["source_workflow"])

        self.assertIn("output2.md", paths)
        self.assertEqual(paths["output2.md"]["key"], "sub_wf_name.nested_file")
        self.assertEqual(paths["output2.md"]["source_workflow"], "sub_wf_name")

    def test_sub_workflow_parent_link_and_child_artifacts(self):
        child_file = Path(self.temp_dir) / "child_joke.txt"
        child_file.write_text("Funny joke", encoding="utf-8")

        # Create parent session
        self._create_mock_session(
            workflow_name="test-pipeline",
            state_id="parent_sess_100",
            state="DONE",
            step=2,
            metadata={
                "_call_stack": ["test-pipeline"],
                "_sub_workflow_history": [
                    {"name": "child-step-wf", "state_id": "child_sess_200", "parent_step": 0, "status": "DONE"}
                ],
                "child-step-wf": {
                    "joke_file": str(child_file),
                },
            },
        )

        # Create child session
        self._create_mock_session(
            workflow_name="child-step-wf",
            state_id="child_sess_200",
            state="DONE",
            step=1,
            metadata={
                "_call_stack": ["test-pipeline", "child-step-wf"],
                "joke_file": str(child_file),
            },
        )

        # 1. Detail of child session should resolve parent
        child_detail = get_session_detail("child-step-wf", "child_sess_200", state_base_dir=self.state_dir)
        self.assertIsNotNone(child_detail)
        self.assertIsNotNone(child_detail.get("parent"))
        self.assertEqual(child_detail["parent"]["workflow_name"], "test-pipeline")
        self.assertEqual(child_detail["parent"]["state_id"], "parent_sess_100")
        self.assertEqual(len(child_detail["artifacts"]), 1)
        self.assertEqual(child_detail["artifacts"][0]["filename"], "child_joke.txt")

        # 2. Detail of parent session should aggregate child artifacts
        parent_detail = get_session_detail("test-pipeline", "parent_sess_100", state_base_dir=self.state_dir)
        self.assertIsNotNone(parent_detail)
        self.assertIsNone(parent_detail.get("parent"))
        self.assertEqual(len(parent_detail["artifacts"]), 1)
        self.assertEqual(parent_detail["artifacts"][0]["filename"], "child_joke.txt")
        self.assertEqual(parent_detail["artifacts"][0]["source_workflow"], "child-step-wf")

    def test_stop_session(self):
        f = self._create_mock_session(
            workflow_name="test-wf",
            state_id="s123",
            state="RUNNING",
        )
        self.assertTrue(f.is_file())
        res = stop_session("test-wf", "s123", state_base_dir=self.state_dir)
        self.assertTrue(res)

        detail = get_session_detail("test-wf", "s123", state_base_dir=self.state_dir)
        self.assertEqual(detail["state"], "STOPPED")
        self.assertIn("stopped by user", detail["stuck_reason"])

    def test_delete_session(self):
        f = self._create_mock_session(
            workflow_name="test-wf",
            state_id="del123",
            state="DONE",
        )
        self.assertTrue(f.is_file())
        res = delete_session("test-wf", "del123", state_base_dir=self.state_dir)
        self.assertTrue(res)
        self.assertFalse(f.is_file())

        # Second call returns False because already deleted
        res2 = delete_session("test-wf", "del123", state_base_dir=self.state_dir)
        self.assertFalse(res2)

    def test_clear_sessions_filters(self):
        self._create_mock_session("wf", "done1", state="DONE")
        self._create_mock_session("wf", "done2", state="DONE")
        self._create_mock_session("wf", "run1", state="RUNNING")
        self._create_mock_session("wf", "stop1", state="STOPPED")

        # 1. Clear done
        res_done = clear_sessions("done", state_base_dir=self.state_dir)
        self.assertEqual(res_done["deleted"], 2)

        # 2. Stop running
        res_stop = clear_sessions("stop_all_active", state_base_dir=self.state_dir)
        self.assertEqual(res_stop["updated"], 1)

        # 3. Clear obsolete (stop1 + run1 which is now stopped)
        res_obs = clear_sessions("obsolete", state_base_dir=self.state_dir)
        self.assertEqual(res_obs["deleted"], 2)

        # Nothing left
        sessions = scan_all_sessions(state_base_dir=self.state_dir)
        self.assertEqual(len(sessions), 0)

    def test_find_available_port(self):
        port = find_available_port("127.0.0.1", start_port=18080)
        self.assertGreaterEqual(port, 18080)

    def test_index_html_exists_and_servable(self):
        html_path = get_ui_html_path()
        self.assertTrue(html_path.is_file(), f"index.html not found at {html_path}")
        content = html_path.read_text(encoding="utf-8")
        self.assertIn("emu-dev-cli Workflow Visualizer", content)
        self.assertIn("Pipeline Stepper", content)
        self.assertIn("Manage Sessions", content)

    def test_http_server_endpoints(self):
        self._create_mock_session(
            workflow_name="hello-world-example",
            state_id="http_sess",
            state="RUNNING",
            step=1,
            metadata={"test_key": "test_val"},
        )

        import workflows.ui.server as server_module
        old_base_dir = getattr(server_module, "get_state_base_dir", None)
        server_module.get_state_base_dir = lambda: self.state_dir

        port = find_available_port("127.0.0.1", start_port=19090)
        httpd, actual_port = start_server(
            host="127.0.0.1",
            port=port,
            open_browser=False,
            block=False,
        )
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()

        base_url = f"http://127.0.0.1:{actual_port}"

        try:
            # 1. GET /api/sessions
            with urllib.request.urlopen(f"{base_url}/api/sessions") as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertIn("sessions", data)
                self.assertEqual(data["count"], 1)

            # 2. POST /api/session/stop
            req = urllib.request.Request(
                f"{base_url}/api/session/stop",
                data=json.dumps({"workflow": "hello-world-example", "id": "http_sess"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                stop_res = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(stop_res["status"], "success")

            # Verify session is now STOPPED
            detail_url = f"{base_url}/api/session?workflow=hello-world-example&id=http_sess"
            with urllib.request.urlopen(detail_url) as resp:
                self.assertEqual(resp.status, 200)
                detail = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(detail["state"], "STOPPED")

            # 3. POST /api/session/delete
            req_del = urllib.request.Request(
                f"{base_url}/api/session/delete",
                data=json.dumps({"workflow": "hello-world-example", "id": "http_sess"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req_del) as resp:
                self.assertEqual(resp.status, 200)
                del_res = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(del_res["status"], "success")

            # 4. POST /api/sessions/clear
            self._create_mock_session("hello-world-example", "bulk_done", state="DONE")
            req_clear = urllib.request.Request(
                f"{base_url}/api/sessions/clear",
                data=json.dumps({"filter": "done"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req_clear) as resp:
                self.assertEqual(resp.status, 200)
                clear_res = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(clear_res["deleted"], 1)

        finally:
            httpd.shutdown()
            httpd.server_close()
            if old_base_dir:
                server_module.get_state_base_dir = old_base_dir

    def test_workflow_ui_cli_argparser(self):
        import argparse
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()
        register_parser(subparsers)

        args = parser.parse_args(["workflow", "ui", "--port", "9090", "--host", "0.0.0.0", "--no-browser"])
        self.assertEqual(args.workflow_cmd, "ui")
        self.assertEqual(args.port, 9090)
        self.assertEqual(args.host, "0.0.0.0")
        self.assertTrue(args.no_browser)

    def test_create_composed_session_and_approve(self):
        from workflows.ui.server import create_composed_session
        from workflows.core.yaml_runner import execute_workflow_step, approve_workflow_session

        old_env = os.environ.get("EMU_DEV_CLI_WORKFLOW_STATE_DIR")
        os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = str(self.state_dir)
        try:
            res = create_composed_session(
                steps_payload=[
                    {
                        "type": "workflow",
                        "workflow_name": "reproduce-bug",
                        "title": "Reproduce Bug",
                        "custom_instructions": "Run emulator with -gpu host and trigger snapshot save.",
                    },
                    {
                        "type": "human_verify",
                        "workflow_name": "human-verify",
                        "title": "Verify Reproduction",
                        "custom_instructions": "Check crash log before fixing.",
                    },
                    {
                        "type": "workflow",
                        "workflow_name": "fix-bug",
                        "title": "Fix Bug",
                        "custom_instructions": "Add null check in Snapshot.cpp.",
                    },
                ],
                shared_args={"bug-number": "471234567", "repo": "hardware/google/aemu"},
                title="Fix Snapshot Crash",
                launch_jetski=False,
                state_base_dir=self.state_dir,
            )
            self.assertEqual(res["workflow_name"], "custom-workflow")
            state_id = res["state_id"]
            self.assertTrue(state_id)

            detail = get_session_detail("custom-workflow", state_id, state_base_dir=self.state_dir)
            self.assertIsNotNone(detail)
            self.assertEqual(detail["custom_title"], "Fix Snapshot Crash")
            self.assertEqual(detail["state"], "RUNNING")
            self.assertEqual(detail["step"], 0)
            self.assertEqual(
                detail["steps"][0]["custom_instructions"],
                "Run emulator with -gpu host and trigger snapshot save.",
            )

            # Advance step 0 (reproduce-bug pass-through verifier succeeds -> advances to step 1: human_verify barrier)
            spec_path = detail["metadata"]["_workflow_spec_path"]
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                execute_workflow_step(spec_path, state_id)
            finally:
                sys.stdout = old_stdout

            detail_waiting = get_session_detail("custom-workflow", state_id, state_base_dir=self.state_dir)
            self.assertEqual(detail_waiting["state"], "WAITING_HUMAN")
            self.assertEqual(detail_waiting["step"], 1)
            self.assertEqual(detail_waiting["steps"][1]["status"], "waiting_human")

            # Approve the human barrier -> advances to step 2 (fix-bug)
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                ok, msg = approve_workflow_session(
                    state_id=state_id,
                    workflow_name="custom-workflow",
                    feedback="Confirmed crash reproduction!",
                    state_base_dir=self.state_dir,
                    advance=True,
                )
            finally:
                sys.stdout = old_stdout
            self.assertTrue(ok)

            detail_after = get_session_detail("custom-workflow", state_id, state_base_dir=self.state_dir)
            self.assertEqual(detail_after["state"], "RUNNING")
            self.assertEqual(detail_after["step"], 2)
        finally:
            if old_env is not None:
                os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] = old_env
            else:
                os.environ.pop("EMU_DEV_CLI_WORKFLOW_STATE_DIR", None)

    def test_get_known_source_directories(self):
        from workflows.ui.server import get_known_source_directories

        cfg_path = Path(self.temp_dir) / "emu-dev-cli.json"
        fake_dev = Path(self.temp_dir) / "emu-main-dev"
        fake_next = Path(self.temp_dir) / "emu-main-next"
        fake_dev.mkdir(parents=True, exist_ok=True)
        fake_next.mkdir(parents=True, exist_ok=True)

        cfg_path.write_text(
            json.dumps({
                "source_directories": {
                    "emu-main-dev": str(fake_dev),
                    "emu-main-next": str(fake_next),
                    "trunk-release": "",
                }
            }),
            encoding="utf-8",
        )

        old_cfg = os.environ.get("EMU_DEV_CLI_CONFIG_PATH")
        os.environ["EMU_DEV_CLI_CONFIG_PATH"] = str(cfg_path)
        try:
            dirs = get_known_source_directories()
            self.assertEqual(len(dirs), 2)
            by_branch = {d["branch"]: d for d in dirs}
            self.assertIn("emu-main-dev", by_branch)
            self.assertIn("emu-main-next", by_branch)
            self.assertNotIn("trunk-release", by_branch)
            self.assertEqual(by_branch["emu-main-dev"]["path"], str(fake_dev.resolve()))
            self.assertTrue(by_branch["emu-main-dev"]["exists"])
        finally:
            if old_cfg is not None:
                os.environ["EMU_DEV_CLI_CONFIG_PATH"] = old_cfg
            else:
                os.environ.pop("EMU_DEV_CLI_CONFIG_PATH", None)

    def test_get_conversation_transcript_and_api(self):
        from workflows.ui.server import get_conversation_transcript, get_session_transcript
        import workflows.ui.server as server_module

        fake_brain = Path(self.temp_dir) / "brain"
        conv_id = "613b389e-dc47-49b5-906b-b2a766e5f38f"
        logs_dir = fake_brain / conv_id / ".system_generated" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        transcript_file = logs_dir / "transcript.jsonl"

        steps_lines = [
            json.dumps({
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "status": "DONE",
                "created_at": "2026-09-25T06:44:12Z",
                "content": "You are executing workflow session abc12345.",
            }),
            json.dumps({
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "created_at": "2026-09-25T06:44:16Z",
                "thinking": "Let's run the reproduction test first.",
                "content": "I will run the reproduction command now.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "run_command",
                        "args": {"CommandLine": "pytest tests/repro_test.py", "toolSummary": "Run repro test"},
                    }
                ],
            }),
            json.dumps({
                "step_index": 2,
                "source": "SYSTEM",
                "type": "GENERIC",
                "status": "DONE",
                "created_at": "2026-09-25T06:44:20Z",
                "content": "1 failed in 0.12s",
            }),
        ]
        transcript_file.write_text("\n".join(steps_lines) + "\n", encoding="utf-8")

        self._create_mock_session(
            workflow_name="custom-workflow",
            state_id="conv_sess",
            state="RUNNING",
            step=0,
            metadata={"conversation_id": conv_id, "_agent_status": "RUNNING"},
        )

        old_brain = os.environ.get("JETSKI_BRAIN_DIR")
        old_base_dir = getattr(server_module, "get_state_base_dir", None)
        os.environ["JETSKI_BRAIN_DIR"] = str(fake_brain)
        server_module.get_state_base_dir = lambda: self.state_dir

        try:
            direct = get_conversation_transcript(conversation_id=conv_id)
            self.assertEqual(direct["total_steps"], 3)
            self.assertEqual(len(direct["steps"]), 3)
            self.assertEqual(direct["steps"][0]["type"], "USER_INPUT")
            self.assertEqual(direct["steps"][1]["type"], "PLANNER_RESPONSE")
            self.assertEqual(direct["steps"][1]["thinking"], "Let's run the reproduction test first.")
            self.assertEqual(direct["steps"][1]["tool_calls"][0]["name"], "run_command")

            # 1. When no background process is alive and transcript ends at GENERIC tool output,
            #    resolve_live_agent_status detects INTERRUPTED and updates the state file.
            by_sess = get_session_transcript(
                workflow_name="custom-workflow",
                state_id="conv_sess",
                state_base_dir=self.state_dir,
            )
            self.assertEqual(by_sess["conversation_id"], conv_id)
            self.assertEqual(by_sess["agent_status"], "INTERRUPTED")
            self.assertIn("interrupted mid-turn", by_sess["agent_status_reason"])
            self.assertEqual(by_sess["total_steps"], 3)

            # 2. When background agent is actively running, agent_status is RUNNING
            server_module._STARTING_AGENTS.add("conv_sess")
            try:
                by_sess_running = get_session_transcript(
                    workflow_name="custom-workflow",
                    state_id="conv_sess",
                    state_base_dir=self.state_dir,
                )
                self.assertEqual(by_sess_running["agent_status"], "RUNNING")
            finally:
                server_module._STARTING_AGENTS.discard("conv_sess")
        finally:
            if old_brain is not None:
                os.environ["JETSKI_BRAIN_DIR"] = old_brain
            else:
                os.environ.pop("JETSKI_BRAIN_DIR", None)
            if old_base_dir:
                server_module.get_state_base_dir = old_base_dir

    def test_poke_session_attaches_existing_conversation(self):
        import workflows.ui.server as server_module
        from workflows.ui.server import launch_jetski_for_session

        conv_id = "613b389e-dc47-49b5-906b-b2a766e5f38f"
        self._create_mock_session(
            workflow_name="custom-workflow",
            state_id="poke_sess",
            state="RUNNING",
            step=0,
            metadata={
                "conversation_id": conv_id,
                "_agent_status": "INTERRUPTED",
                "_last_step_prompt": "Run reproduce test.",
            },
        )

        captured_cmds = []

        class FakeProc:
            pid = 999999
            returncode = 0
            stdout = iter([])

            def poll(self):
                return 0

            def wait(self):
                return 0

        old_resolve = server_module.resolve_jetski_cli
        old_popen = server_module.subprocess.Popen
        server_module.resolve_jetski_cli = lambda: "/fake/jetski_cli"

        def fake_popen(cmd, **kwargs):
            captured_cmds.append(cmd)
            return FakeProc()

        server_module.subprocess.Popen = fake_popen
        try:
            ok, msg, returned_cid = launch_jetski_for_session(
                workflow_name="custom-workflow",
                state_id="poke_sess",
                resume=True,
                poke=True,
                custom_prompt="Please continue running the test.",
                state_base_dir=self.state_dir,
            )
            self.assertTrue(ok)
            self.assertEqual(returned_cid, conv_id)
            self.assertIn("Poked", msg)
            time.sleep(0.05)
            self.assertEqual(len(captured_cmds), 1)
            cmd = captured_cmds[0]
            self.assertIn("--conversation", cmd)
            self.assertIn(conv_id, cmd)
            prompt_idx = cmd.index("--prompt") + 1
            self.assertIn("Please continue running the test.", cmd[prompt_idx])
        finally:
            server_module.resolve_jetski_cli = old_resolve
            server_module.subprocess.Popen = old_popen

    def test_scan_all_conversations_and_poke_conversation(self):
        import sqlite3
        import workflows.ui.server as server_module
        from workflows.ui.server import poke_conversation, scan_all_conversations

        fake_jetski_root = Path(self.temp_dir) / "jetski_home"
        fake_brain = fake_jetski_root / "brain"
        fake_brain.mkdir(parents=True, exist_ok=True)

        # Create mock conversation_summaries.db
        db_path = fake_jetski_root / "conversation_summaries.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE conversation_summaries (
                conversation_id TEXT PRIMARY KEY,
                title TEXT,
                preview TEXT,
                step_count INTEGER,
                last_modified_time TEXT,
                workspace_uris TEXT,
                status TEXT,
                not_fully_idle INTEGER,
                parent_conversation_id TEXT,
                nesting_depth INTEGER,
                killed INTEGER
            )
            """
        )
        # 1. Running conversation
        cid_running = "11111111-1111-1111-1111-111111111111"
        conn.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cid_running,
                "Investigate WebRTC audio glitch",
                "Running unit tests...",
                12,
                "2026-09-25T16:50:00Z",
                json.dumps(["file:///tmp/emu-main-next"]),
                "CASCADE_RUN_STATUS_RUNNING",
                1,
                "",
                0,
                0,
            ),
        )
        # 2. Waiting for input conversation (turn finished with PLANNER_RESPONSE DONE and no tool calls)
        cid_waiting = "22222222-2222-2222-2222-222222222222"
        conn.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cid_waiting,
                "Fix macOS codesign Entitlements",
                "Should I update the plist file now?",
                8,
                "2026-09-25T16:48:00Z",
                json.dumps(["file:///tmp/emu-main-dev"]),
                "CASCADE_RUN_STATUS_IDLE",
                0,
                "",
                0,
                0,
            ),
        )
        # 3. Interrupted conversation (idle in DB, but transcript stopped mid-turn after GENERIC tool output)
        cid_interrupted = "33333333-3333-3333-3333-333333333333"
        conn.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cid_interrupted,
                "Reproduce snapshot load crash",
                "Tool output finished",
                5,
                "2026-09-25T16:45:00Z",
                json.dumps(["file:///tmp/emu-main-next"]),
                "CASCADE_RUN_STATUS_IDLE",
                0,
                "",
                0,
                0,
            ),
        )
        conn.commit()
        conn.close()

        # Write transcripts for cid_waiting and cid_interrupted
        w_logs = fake_brain / cid_waiting / ".system_generated" / "logs"
        w_logs.mkdir(parents=True, exist_ok=True)
        (w_logs / "transcript.jsonl").write_text(
            json.dumps({
                "step_index": 7,
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "content": "Should I update the plist file now?",
                "tool_calls": [],
            })
            + "\n",
            encoding="utf-8",
        )

        i_logs = fake_brain / cid_interrupted / ".system_generated" / "logs"
        i_logs.mkdir(parents=True, exist_ok=True)
        (i_logs / "transcript.jsonl").write_text(
            json.dumps({
                "step_index": 4,
                "type": "GENERIC",
                "status": "DONE",
                "content": "Command completed with exit code 0",
            })
            + "\n",
            encoding="utf-8",
        )

        old_brain = os.environ.get("JETSKI_BRAIN_DIR")
        os.environ["JETSKI_BRAIN_DIR"] = str(fake_brain)
        try:
            convs = scan_all_conversations(state_base_dir=self.state_dir)
            by_id = {c["conversation_id"]: c for c in convs}
            self.assertIn(cid_running, by_id)
            self.assertIn(cid_waiting, by_id)
            self.assertIn(cid_interrupted, by_id)

            self.assertEqual(by_id[cid_running]["status"], "RUNNING")
            self.assertEqual(by_id[cid_waiting]["status"], "WAITING_INPUT")
            self.assertEqual(by_id[cid_interrupted]["status"], "INTERRUPTED")

            # Test poke_conversation on standalone conversation
            captured_cmds = []

            class FakeProc:
                pid = 888888
                returncode = 0
                stdout = iter([])

                def poll(self):
                    return 0

                def wait(self):
                    return 0

            old_resolve = server_module.resolve_jetski_cli
            old_popen = server_module.subprocess.Popen
            server_module.resolve_jetski_cli = lambda: "/fake/jetski_cli"
            server_module.subprocess.Popen = lambda cmd, **kw: (captured_cmds.append(cmd), FakeProc())[1]
            try:
                ok, msg = poke_conversation(
                    conversation_id=cid_waiting,
                    prompt="Yes, please update the plist file.",
                    state_base_dir=self.state_dir,
                )
                self.assertTrue(ok)
                time.sleep(0.05)
                self.assertEqual(len(captured_cmds), 1)
                self.assertIn(cid_waiting, captured_cmds[0])
                self.assertIn("Yes, please update the plist file.", captured_cmds[0])
            finally:
                server_module.resolve_jetski_cli = old_resolve
                server_module.subprocess.Popen = old_popen
        finally:
            if old_brain is not None:
                os.environ["JETSKI_BRAIN_DIR"] = old_brain
            else:
                os.environ.pop("JETSKI_BRAIN_DIR", None)


if __name__ == "__main__":
    unittest.main()


