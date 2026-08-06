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

import subprocess

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Set

from lib.tidy_types import TidyDiagnostic


@dataclass
class FileTask:
    file_path: str
    diagnostics: List[TidyDiagnostic]


@dataclass
class Cohort:
    tasks: List[FileTask] = field(default_factory=list)


class RefactorPlanner:
    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root

    def _resolve_target_label(self, file_path: str) -> str:
        path = self.workspace_root / file_path
        curr = path.parent
        while curr != self.workspace_root and len(curr.parts) > len(
            self.workspace_root.parts
        ):
            if (curr / "BUILD.bazel").exists() or (curr / "BUILD").exists():
                pkg_path = curr.relative_to(self.workspace_root).as_posix()
                file_rel = path.relative_to(curr).as_posix()
                return f"//{pkg_path}:{file_rel}"
            curr = curr.parent

        if "/" in file_path:
            parts = file_path.rsplit("/", 1)
            return f"//{parts[0]}:{parts[1]}"
        return f"//:{file_path}"

    def _get_rdeps(self, file_path: str) -> Set[str]:
        label = self._resolve_target_label(file_path)
        # TDD Mock: in tests this will be mocked.
        # In reality, this will execute: bazel query --keep_going "rdeps(//..., <label>)"
        cmd = ["bazel", "query", "--keep_going", f"rdeps(//..., {label})"]
        res = subprocess.run(
            cmd, cwd=self.workspace_root, capture_output=True, text=True
        )
        if res.returncode != 0 and not res.stdout:
            # Maybe keep going still printed to stdout?
            pass
        return {
            line.strip()
            for line in res.stdout.splitlines()
            if line.strip() and not line.startswith("WARNING")
        }

    def plan(self, diagnostics: List[TidyDiagnostic]) -> List[Cohort]:
        if not diagnostics:
            return []

        # 1. Group by file (Atomic File Rule)
        file_to_diags: Dict[str, List[TidyDiagnostic]] = {}
        for diag in diagnostics:
            file_to_diags.setdefault(diag.file_path, []).append(diag)

        file_tasks = [FileTask(f, diags) for f, diags in file_to_diags.items()]

        # 2. Get rdeps for each file
        file_rdeps: Dict[str, Set[str]] = {}
        for task in file_tasks:
            file_rdeps[task.file_path] = self._get_rdeps(task.file_path)

        # 3. Create initial Cohorts (Disjoint set by greedy graph coloring/bucketing)
        cohorts: List[Cohort] = []

        for task in file_tasks:
            rdeps_a = file_rdeps[task.file_path]

            # Find a cohort where this task doesn't intersect with *any* existing task in that cohort
            placed = False
            for cohort in cohorts:
                has_conflict = False
                for c_task in cohort.tasks:
                    rdeps_b = file_rdeps[c_task.file_path]
                    if rdeps_a.intersection(rdeps_b):
                        has_conflict = True
                        break

                if not has_conflict:
                    cohort.tasks.append(task)
                    placed = True
                    break

            if not placed:
                # Need a new cohort (sequential block)
                cohorts.append(Cohort(tasks=[task]))

        return cohorts
