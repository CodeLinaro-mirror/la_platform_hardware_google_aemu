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

"""Unit tests for the `compare-perf` workflow (`workflows/compare_perf/`)."""

import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT_DIR, "src")
WORKFLOW_DIR = os.path.join(ROOT_DIR, "workflows", "compare_perf")
for p in (ROOT_DIR, SRC_DIR, WORKFLOW_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import prepare_perf_batch
import verify_perf_report
import verify_perf_run
from commands.workflow import list_available_workflows
from workflows.core.yaml_runner import load_state_yaml, run_yaml_workflow


def _make_sample_comparison() -> dict:
    return {
        "baseline_label": "stable-37.2.12 (qemu-now)",
        "candidate_label": "tree (qemu-next)",
        "comparisons": [
            {
                "workload": "idle_steady_state",
                "metric": "host_cpu_pct",
                "unit": "% CPU",
                "baseline_p50": 45.2,
                "candidate_p50": 34.8,
                "baseline_mean": 45.2,
                "candidate_mean": 34.8,
                "baseline_stdev": 1.1,
                "candidate_stdev": 0.9,
                "pct_delta_p50": -23.0,
                "pct_delta_mean": -23.0,
                "verdict": "win",
                "badge": "✅ WIN (p=0.0004)",
                "p_value": 0.0004,
                "high_variance": False,
                "baseline_cpu_ms_per_mib": None,
                "candidate_cpu_ms_per_mib": None,
                "baseline_host_cpu_pct": 45.2,
                "candidate_host_cpu_pct": 34.8,
            }
        ],
        "thread_diffs": {},
        "symbol_diffs": {},
        "hypervisor_diffs": [],
        "baseline_insights": [],
        "candidate_insights": [],
    }


class ComparePerfWorkflowTest(unittest.TestCase):
    """Tests for `emu-dev-cli workflow compare-perf` scripts and state machine."""

    def test_workflow_is_discoverable(self):
        workflows = list_available_workflows()
        self.assertIn("compare-perf", workflows)
        self.assertTrue(
            workflows["compare-perf"]["target_path"].endswith("workflow.yaml")
        )

    @patch("prepare_perf_batch._resolve_perf_avd_template")
    @patch("prepare_perf_batch._detect_default_host", return_value="mac-arm64")
    def test_prepare_perf_batch_ready_and_missing(
        self, _mock_host, mock_resolve_avd
    ):
        mock_resolve_avd.return_value = {
            "template_avd": "Pixel_9_API_36",
            "sysimg_dir": "/Users/dev/Library/Android/sdk/system-images/arm64-v8a",
            "cores": 4,
            "ram": 2048,
            "disk_size": "6G",
        }
        info = prepare_perf_batch.inspect_perf_environment(
            baseline="stable",
            candidate="latest:emu-main-next",
            suite="idle,cpu",
            avd="auto",
        )
        self.assertEqual(info["host"], "mac-arm64")
        self.assertTrue(info["avd_ready"])
        self.assertIn("Pixel_9_API_36", info["avd_summary"])
        self.assertIn("idle_steady_state", info["workloads"])
        self.assertIn("cpu_compute_scaling", info["workloads"])

        mock_resolve_avd.side_effect = RuntimeError("No system image found")
        info_missing = prepare_perf_batch.inspect_perf_environment(
            baseline="stable",
            candidate="tree",
            suite="idle",
            avd="auto",
        )
        self.assertFalse(info_missing["avd_ready"])
        self.assertIn("MISSING", info_missing["avd_summary"])

    def test_verify_perf_run_validates_workloads_and_writes_scorecard(self):
        with tempfile.TemporaryDirectory() as td:
            comp_json = os.path.join(td, "comp.json")
            scorecard_md = os.path.join(td, "scorecard.md")

            ok, msg = verify_perf_run.verify_perf_run(
                comp_json, scorecard_md, suite="idle"
            )
            self.assertFalse(ok)
            self.assertIn("does not exist", msg)

            comp_data = _make_sample_comparison()
            with open(comp_json, "w", encoding="utf-8") as f:
                json.dump(comp_data, f)

            # Requesting 'idle,cpu' when only 'idle_steady_state' is in comparisons should fail
            ok_missing, msg_missing = verify_perf_run.verify_perf_run(
                comp_json, scorecard_md, suite="idle,cpu"
            )
            self.assertFalse(ok_missing)
            self.assertIn("cpu_compute_scaling", msg_missing)

            # Requesting 'idle' succeeds and writes scorecard.md
            ok_pass, _ = verify_perf_run.verify_perf_run(
                comp_json, scorecard_md, suite="idle"
            )
            self.assertTrue(ok_pass)
            self.assertTrue(os.path.isfile(scorecard_md))
            with open(scorecard_md, "r", encoding="utf-8") as f:
                md_text = f.read()
            self.assertIn("idle_steady_state", md_text)

    def test_verify_perf_report_enforces_sections_and_workloads(self):
        with tempfile.TemporaryDirectory() as td:
            comp_json = os.path.join(td, "comp.json")
            report_md = os.path.join(td, "report.md")
            with open(comp_json, "w", encoding="utf-8") as f:
                json.dump(_make_sample_comparison(), f)

            # Missing workload name
            with open(report_md, "w", encoding="utf-8") as f:
                f.write(
                    "# Weekly Emulator Performance Qualification Report\n\n"
                    "## Executive Summary\nAll good on Linux and macOS.\n\n"
                    "## Full 4-Part Performance Scorecard\nScorecard table here.\n\n"
                    "## Guest Kernel & Hypervisor Telemetry\nKVM exits nominal.\n\n"
                    "## Regressions & Root-Cause Attribution\nZero regressions found in this run.\n"
                )
            ok_no_wl, msg_no_wl = verify_perf_report.verify_perf_report(
                comp_json, report_md
            )
            self.assertFalse(ok_no_wl)
            self.assertIn("idle_steady_state", msg_no_wl)

            # Include workload name -> passes
            with open(report_md, "a", encoding="utf-8") as f:
                f.write("\n| `idle_steady_state` | 45.2% | 34.8% | -23.0% | WIN |\n")
            ok_pass, _ = verify_perf_report.verify_perf_report(
                comp_json, report_md
            )
            self.assertTrue(ok_pass)

    @patch("prepare_perf_batch._resolve_perf_avd_template")
    def test_end_to_end_workflow_lifecycle(self, mock_resolve_avd):
        mock_resolve_avd.return_value = {
            "template_avd": "perf_avd",
            "sysimg_dir": "/tmp/sysimg",
            "cores": 4,
            "ram": 2048,
            "disk_size": "6G",
        }
        wf_yaml = os.path.join(WORKFLOW_DIR, "workflow.yaml")
        with tempfile.TemporaryDirectory() as td:
            env_patch = {
                "EMU_DEV_CLI_WORKFLOW_STATE_DIR": os.path.join(td, "state"),
                "XDG_CACHE_HOME": os.path.join(td, "cache"),
            }
            with patch.dict(os.environ, env_patch):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    rc = run_yaml_workflow(
                        wf_yaml,
                        [
                            "init",
                            "--baseline",
                            "stable",
                            "--candidate",
                            "tree",
                            "--suite",
                            "idle",
                        ],
                    )
                self.assertEqual(rc, 0)
                init_out = buf.getvalue()
                m_state = re.search(r"--state=([0-9a-f]{8})", init_out)
                self.assertIsNotNone(m_state)
                state_id = m_state.group(1)

                state_file = os.path.join(
                    td,
                    "state",
                    "compare-perf",
                    f"STATE-{state_id}.yaml",
                )
                state_data = load_state_yaml(state_file)
                comp_json = state_data["metadata"]["comparison-json"]
                raw_scorecard = state_data["metadata"]["raw-scorecard"]
                report_file = state_data["metadata"]["report-file"]

                # Transition INIT -> RUNNING (Step 0 prompt)
                buf_start = io.StringIO()
                with patch("sys.stdout", buf_start):
                    rc_start = run_yaml_workflow(
                        wf_yaml, [f"--state={state_id}"]
                    )
                self.assertEqual(rc_start, 0)
                state_running = load_state_yaml(state_file)
                self.assertEqual(state_running["state"], "RUNNING")
                self.assertEqual(state_running["step"], 0)

                # Populate Step 0 artifact (comparison JSON)
                os.makedirs(os.path.dirname(comp_json), exist_ok=True)
                with open(comp_json, "w", encoding="utf-8") as f:
                    json.dump(_make_sample_comparison(), f)

                # Advance Step 0 -> Step 1
                buf_s0 = io.StringIO()
                with patch("sys.stdout", buf_s0):
                    rc_s0 = run_yaml_workflow(wf_yaml, [f"--state={state_id}"])
                self.assertEqual(rc_s0, 0)
                self.assertTrue(os.path.isfile(raw_scorecard))
                state_after_s0 = load_state_yaml(state_file)
                self.assertEqual(state_after_s0["step"], 1)

                # Populate Step 1 artifact (weekly report)
                with open(raw_scorecard, "r", encoding="utf-8") as f:
                    scorecard_text = f.read()
                with open(report_file, "w", encoding="utf-8") as f:
                    f.write(
                        "# Weekly Emulator Performance Qualification Report\n\n"
                        "## Executive Summary & Environment\n"
                        "Compared stable vs tree on clean isolated AVDs.\n\n"
                        "## Full 4-Part Performance Scorecard\n\n"
                        f"{scorecard_text}\n\n"
                        "## Guest Kernel & Hypervisor Telemetry\n"
                        "Idle CPU reduced by 23%.\n\n"
                        "## Regressions, Broken Workloads & Root-Cause Attribution\n"
                        "No regressions detected.\n"
                    )

                # Advance Step 1 -> Step 2 (DONE)
                buf_s1 = io.StringIO()
                with patch("sys.stdout", buf_s1):
                    rc_s1 = run_yaml_workflow(wf_yaml, [f"--state={state_id}"])
                self.assertEqual(rc_s1, 0)
                state_final = load_state_yaml(state_file)
                self.assertEqual(state_final["state"], "DONE")


if __name__ == "__main__":
    unittest.main()
