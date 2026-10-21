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

import json
from pathlib import Path
import unittest

from benchmarks import score_benchmark


class DiscoveryBenchmarkTest(unittest.TestCase):

    def setUp(self):
        self.root_dir = Path(__file__).resolve().parent.parent
        self.tasks_path = self.root_dir / "benchmarks" / "tasks.json"
        self.hal_tasks_path = self.root_dir / "benchmarks" / "hal_agents_tasks.json"
        self.skill_path = self.root_dir / "skills" / "SKILL.md"

    def test_hal_agents_tasks_schema_and_stratification(self):
        self.assertTrue(self.hal_tasks_path.is_file())
        data = json.loads(self.hal_tasks_path.read_text(encoding="utf-8"))
        tasks = data.get("tasks", [])
        self.assertEqual(len(tasks), 6)

        dev_tasks = [t for t in tasks if t["split"] == "dev"]
        holdout_tasks = [t for t in tasks if t["split"] == "holdout"]
        self.assertEqual(len(dev_tasks), 3)
        self.assertEqual(len(holdout_tasks), 3)

        for wf in ("hal_discovery", "hal_implementation", "hal_code_review"):
            wf_tasks = [t for t in tasks if t["workflow"] == wf]
            self.assertEqual(len(wf_tasks), 2)
            self.assertEqual(len([t for t in wf_tasks if t["split"] == "dev"]), 1)
            self.assertEqual(len([t for t in wf_tasks if t["split"] == "holdout"]), 1)

    def test_tasks_json_schema_and_stratification(self):
        self.assertTrue(self.tasks_path.is_file())
        data = json.loads(self.tasks_path.read_text(encoding="utf-8"))
        tasks = data.get("tasks", [])
        self.assertEqual(len(tasks), 32)

        dev_tasks = [t for t in tasks if t["split"] == "dev"]
        holdout_tasks = [t for t in tasks if t["split"] == "holdout"]
        self.assertEqual(len(dev_tasks), 24)
        self.assertEqual(len(holdout_tasks), 8)

        for wf in ("bug_fix", "perf_bottleneck", "add_feature", "operational_cli"):
            wf_tasks = [t for t in tasks if t["workflow"] == wf]
            self.assertEqual(len(wf_tasks), 8)
            wf_holdout = [t for t in wf_tasks if t["split"] == "holdout"]
            self.assertEqual(len(wf_holdout), 2)

        for t in tasks:
            self.assertIn("id", t)
            self.assertIn("prompt", t)
            rubric = t.get("rubric", {})
            self.assertTrue(rubric.get("files"))
            self.assertTrue(rubric.get("symbols"))
            self.assertTrue(rubric.get("targets"))

    def test_skill_md_covers_operational_cli_tasks_and_fits_single_view(self):
        self.assertTrue(self.skill_path.is_file())
        skill_text = self.skill_path.read_text(encoding="utf-8")
        # Ensure SKILL.md fits within a single 46,080-byte view_file call without truncation
        self.assertLessEqual(
            len(skill_text.encode("utf-8")),
            45600,
            "SKILL.md exceeds the single-call view_file byte threshold",
        )
        data = json.loads(self.tasks_path.read_text(encoding="utf-8"))
        cli_tasks = [
            t for t in data.get("tasks", []) if t["workflow"] == "operational_cli"
        ]
        # Combined text includes SKILL.md filename so file rubrics matching SKILL.md pass
        corpus = f"File: {self.skill_path.name}\n{skill_text}"
        for task in cli_tasks:
            scored = score_benchmark.score_rubric(corpus, task["rubric"])
            self.assertEqual(
                scored["recall"],
                1.0,
                f"SKILL.md is missing operational CLI coverage for {task['id']}: {scored}",
            )
            self.assertEqual(scored["pass_at_1"], 1)

    def test_score_rubric_unweighted_recall_and_pass_at_1(self):
        rubric = {
            "files": [r"emulator/libs/perfetto/perfetto_config\.cc"],
            "symbols": [r"CONFIG_TRACE_GOLDFISH", r"GOLDFISH_PERFETTO_NOOP"],
            "targets": [r"emulator/libs/perfetto:init_test"],
            "invariants": [r"RegisterHalDevice"],
            "forbidden": [r"->RegisterDevice\("],
        }
        full_text = (
            "Found emulator/libs/perfetto/perfetto_config.cc with "
            "CONFIG_TRACE_GOLDFISH and GOLDFISH_PERFETTO_NOOP tested by "
            "@goldfish//emulator/libs/perfetto:init_test and registered via "
            "registry->RegisterHalDevice(...)."
        )
        scored = score_benchmark.score_rubric(full_text, rubric)
        self.assertEqual(scored["recall"], 1.0)
        self.assertEqual(scored["pass_at_1"], 1)
        self.assertEqual(scored["matched_items"], 6)
        self.assertEqual(scored["total_items"], 6)
        self.assertEqual(scored["missing_files"], [])
        self.assertEqual(scored["missing_symbols"], [])
        self.assertEqual(scored["missing_targets"], [])
        self.assertEqual(scored["missing_invariants"], [])
        self.assertEqual(scored["forbidden_violations"], [])

        # If a forbidden pattern appears, pass_at_1 drops to 0 and recall drops by 1/6
        bad_text = full_text + "\nregistry->RegisterDevice(plug);"
        bad_scored = score_benchmark.score_rubric(bad_text, rubric)
        self.assertEqual(bad_scored["pass_at_1"], 0)
        self.assertAlmostEqual(bad_scored["recall"], 5 / 6, places=4)
        self.assertEqual(len(bad_scored["forbidden_violations"]), 1)

    def test_paired_statistical_comparison(self):
        before = [30.0, 35.0, 40.0, 25.0, 32.0, 38.0]
        after = [60.0, 65.0, 70.0, 55.0, 62.0, 68.0]
        stats = score_benchmark.paired_t_and_wilcoxon(before, after)
        self.assertEqual(stats["mean_diff"], 30.0)
        self.assertEqual(stats["median_diff"], 30.0)
        self.assertLess(stats["p_t"], 0.001)
        self.assertLess(stats["p_wilcoxon"], 0.05)

        # Tied absolute differences (+10, -10, +10, +20) should be order-invariant
        stats_a = score_benchmark.paired_t_and_wilcoxon(
            [10.0, 20.0, 30.0, 40.0], [20.0, 10.0, 40.0, 60.0]
        )
        stats_b = score_benchmark.paired_t_and_wilcoxon(
            [20.0, 10.0, 30.0, 40.0], [10.0, 20.0, 40.0, 60.0]
        )
        self.assertEqual(stats_a["p_wilcoxon"], stats_b["p_wilcoxon"])


if __name__ == "__main__":
    unittest.main()
