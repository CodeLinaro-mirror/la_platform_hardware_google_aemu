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

import asyncio

import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from lib.agent_dispatcher import AgentDispatcher
from lib.bazel import BazelRunner
from lib.compiler_oracle import CompilerOracle
from lib.git_transaction import GitTransactionContext, resolve_git_repo_for_target
from lib.planner import RefactorPlanner, Cohort, FileTask
from lib.tidy_types import TidyDiagnostic
from lib.tidy_engine import (
    _apply_seed_declaration_rename,
    _extract_rename_symbols,
    _run_clang_format,
)


def _build_batched_prompt(
    task: FileTask,
    compiler_error_text: str,
) -> str:
    # Build list of renames
    renames = []
    for diag in task.diagnostics:
        old, new = _extract_rename_symbols(diag)
        renames.append(f"  - '{old}' -> '{new}' at {diag.file_path}:{diag.line}")
    renames_str = "\n".join(renames)

    return f"""You are performing an automated C++ semantic refactoring in Google Goldfish Emulator.

### Objective
Rename multiple symbols in `{task.file_path}`:
{renames_str}

### Current State
The declarations have been renamed.
Running `mise fastbuild --keep_going` identified the following broken call sites across the codebase:

### Compiler Diagnostics
```text
{compiler_error_text}
```

### Instructions
1. Update each broken call site in the listed files corresponding to the renamed variables.
2. Ensure you ONLY fix issues related to the symbols mentioned above. Ignore other broken symbols in the log.
3. Preserve all existing formatting, docstrings, and comments.
4. Do not modify unrelated code or change any functional behavior.
"""


class BatchedRefactoringEngine:
    """Orchestrates Refactoring in O(K) parallel cohorts using the RefactorPlanner."""

    def __init__(
        self,
        runner: BazelRunner,
        oracle: CompilerOracle,
        dispatcher: AgentDispatcher,
        source_dir: str,
    ):
        self.runner = runner
        self.oracle = oracle
        self.dispatcher = dispatcher
        self.source_dir = Path(source_dir)
        self.planner = RefactorPlanner(self.source_dir)

    async def _dispatch_task(self, task: FileTask, prompt: str, model: str) -> bool:
        # Check if dispatcher has async method, fallback to thread
        if hasattr(self.dispatcher, "dispatch_refactor_step_async"):
            return await self.dispatcher.dispatch_refactor_step_async(
                prompt, f"Batch for {task.file_path}", model
            )
        else:
            return await asyncio.to_thread(
                self.dispatcher.dispatch_refactor_step,
                prompt,
                f"Batch for {task.file_path}",
                model,
            )

    def run_refactoring_loop(
        self,
        target: str,
        compilation_targets: List[str],
        diagnostics: List[TidyDiagnostic],
        tx: GitTransactionContext,
        model: str,
        test_target: Optional[str] = None,
    ) -> None:
        repo_dir = resolve_git_repo_for_target(target, root_hint=self.source_dir)

        cohorts = self.planner.plan(diagnostics)
        print(f"\n--- Planned {len(cohorts)} independent cohort batches ---")

        for cohort_idx, cohort in enumerate(cohorts):
            print(f"\n======================================")
            print(
                f"=== Processing Cohort {cohort_idx+1}/{len(cohorts)} (Tasks: {len(cohort.tasks)}) ==="
            )
            print(f"======================================")

            # 1. Apply ALL seed renames for the cohort
            applied_diags = []
            for task in cohort.tasks:
                # Sort diagnostics reverse-byte to avoid shifting
                sorted_diags = sorted(
                    task.diagnostics,
                    key=lambda d: -d.replacements[0].offset if d.replacements else 0,
                )
                for diag in sorted_diags:
                    old_s, new_s = _extract_rename_symbols(diag)
                    if not old_s or not new_s or old_s == new_s:
                        continue

                    decl_path = repo_dir / diag.file_path
                    if not decl_path.exists():
                        decl_path = self.source_dir / diag.file_path

                    if _apply_seed_declaration_rename(decl_path, diag, old_s, new_s):
                        applied_diags.append((diag, old_s, new_s))

            if not applied_diags:
                print("  [Seed] No valid target renames in cohort. Skipping.")
                continue

            # 2. Global Build (Oracle)
            print(f"  [Oracle] Checking compilation for cohort...")
            report = self.oracle.run_compilation_check(
                compilation_targets, workspace_root=self.source_dir
            )

            if not report.success:
                print(
                    f"  [Oracle] Captured {len(report.errors)} broken call sites. Dispatching AI Agents..."
                )

                # 3. Parallel AI Patcher
                async def dispatch_all():
                    coros = []
                    for task in cohort.tasks:
                        prompt = _build_batched_prompt(task, report.format_for_prompt())
                        coros.append(self._dispatch_task(task, prompt, model))
                    return await asyncio.gather(*coros, return_exceptions=True)

                results = asyncio.run(dispatch_all())

                failed_agents = [r for r in results if r is not True]
                if len(failed_agents) == len(results):
                    print(
                        "  ⚠️ All AI subagents failed to apply patches. Skipping cohort.",
                        file=sys.stderr,
                    )
                    continue
                elif failed_agents:
                    print(
                        f"  ⚠️ {len(failed_agents)} AI subagents encountered errors (some patches applied).",
                        file=sys.stderr,
                    )
                    for err in failed_agents:
                        if isinstance(err, Exception):
                            print(f"    - Error: {err}", file=sys.stderr)

                _run_clang_format(repo_dir)

                # 4. Commit and Verify
                # Since AI agents apply their patches directly to the source tree in parallel
                # via `dispatcher`, we commit the entire cohort as a single transactional batch.
                # If subsequent compilation or tests fail, the transaction engine will roll back
                # the entire cohort step.
                # Verify
                verify = self.oracle.run_compilation_check(
                    compilation_targets, workspace_root=self.source_dir
                )
                if not verify.success:
                    print(
                        f"\n⚠️ Verification failed for Cohort {cohort_idx+1}. Automating rollback.",
                        file=sys.stderr,
                    )
                    tx.rollback_step()
                    sys.exit(1)

            # Test Gate
            if test_target:
                print(f"  [Test Gate] Verifying test suite...")
                if self.runner.test([test_target], check=False).returncode != 0:
                    print(
                        f"⚠️ Unit tests failed. Automating rollback.", file=sys.stderr
                    )
                    tx.rollback_step()
                    sys.exit(1)

            # Commit Cohort success
            tx.commit_step(
                symbol_old=f"Cohort {cohort_idx+1}",
                symbol_new=f"{len(applied_diags)} symbols",
                message=f"style: Batched refactoring for Cohort {cohort_idx+1}",
            )
            print(f"  ✅ Verified and committed Cohort {cohort_idx+1}")
