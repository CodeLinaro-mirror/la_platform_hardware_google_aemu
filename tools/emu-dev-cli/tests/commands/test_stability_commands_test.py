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

"""CLI command and workflow integration tests for `emu-dev-cli stability`."""

import argparse
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from commands import stability, workflow
from core.command import COMMAND_INSTANCE_ATTR
from core.errors import ExecutionError

os.environ.setdefault("HOME", tempfile.gettempdir())
os.environ.setdefault("USERPROFILE", tempfile.gettempdir())


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="emu-dev-cli")
    parser.add_argument("--json", action="store_true", default=False)
    sub = parser.add_subparsers(dest="subcommand")
    stability.register_parser(sub)
    return parser


class StabilityCommandsTest(unittest.TestCase):
    """Tests `stability list`, `compare`, `history`, `verify`, and `qualify-stability` discovery."""

    def test_stability_list_json_and_markdown(self) -> None:
        parser = _make_parser()
        args = parser.parse_args(["--json", "stability", "list"])
        cmd = getattr(args, COMMAND_INSTANCE_ATTR)
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = cmd.execute(args, None)
        self.assertTrue(res.ok)
        parsed = json.loads(buf.getvalue())
        self.assertIn("smoke", parsed["tiers"])
        self.assertIn("dev-loop", parsed["scenarios"])
        self.assertIn("snapshot-stress", parsed["scenarios"])
        self.assertIn("ui-control", parsed["scenarios"])

    def test_stability_compare_and_verify_saved_json_reports(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stab_cli_测试_🚀_é_") as tmpdir:
            base_path = os.path.join(tmpdir, "base.json")
            cand_path = os.path.join(tmpdir, "cand.json")
            comp_out = os.path.join(tmpdir, "comp.json")
            comp_md = os.path.join(tmpdir, "comp.md")

            base_rep = {
                "schema_version": 1,
                "label": "stable-baseline",
                "tier": "smoke",
                "scenarios": ["dev-loop"],
                "evaluation": {
                    "verdict": "PASS",
                    "effective_exit_code": 0,
                    "cycles_completed": 3,
                    "elapsed_sec": 300.0,
                    "rss_slope_mib_per_min": 0.5,
                    "peak_rss_mib": 1400.0,
                    "handle_net_growth": 4,
                    "findings": [],
                },
                "samples": [],
            }
            cand_rep = json.loads(json.dumps(base_rep))
            cand_rep["label"] = "next-candidate-测试_🚀_é"

            with open(base_path, "w", encoding="utf-8") as f:
                json.dump(base_rep, f)
            with open(cand_path, "w", encoding="utf-8") as f:
                json.dump(cand_rep, f)

            parser = _make_parser()
            args = parser.parse_args([
                "stability",
                "compare",
                base_path,
                cand_path,
                "--output",
                comp_out,
                "--report",
                comp_md,
            ])
            cmd = getattr(args, COMMAND_INSTANCE_ATTR)
            with redirect_stdout(io.StringIO()):
                res = cmd.execute(args, None)
            self.assertTrue(res.ok)
            self.assertTrue(os.path.isfile(comp_out))
            self.assertTrue(os.path.isfile(comp_md))

            # Verify command validates both artifacts
            v_args = parser.parse_args([
                "stability",
                "verify",
                "--report-json",
                comp_out,
                "--report-md",
                comp_md,
            ])
            v_cmd = getattr(v_args, COMMAND_INSTANCE_ATTR)
            v_res = v_cmd.execute(v_args, None)
            self.assertTrue(v_res.ok)

            # Missing JSON raises ExecutionError
            bad_args = parser.parse_args([
                "stability",
                "verify",
                "--report-json",
                os.path.join(tmpdir, "missing.json"),
            ])
            with self.assertRaises(ExecutionError):
                v_cmd.execute(bad_args, None)

    def test_qualify_stability_workflow_is_discovered(self) -> None:
        wf_dirs = workflow.discover_workflow_directories()
        catalog = workflow._scan_workflow_directories(wf_dirs)
        self.assertIn("qualify-stability", catalog)
        self.assertEqual(catalog["qualify-stability"]["target_type"], "yaml")


if __name__ == "__main__":
    unittest.main()
