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
import logging
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from lib.agent_dispatcher import AgentDispatcher
from lib.bazel import BazelRunner
from lib.compiler_oracle import CompilerOracle
from lib.git_transaction import GitTransactionContext, resolve_git_repo_for_target
from lib.planner import RefactorPlanner, Cohort, FileTask
from lib.refactoring.base_engine import BaseRefactoringEngine
from lib.tidy_types import TidyDiagnostic
from lib.tidy_engine import (
    _apply_seed_declaration_rename,
    _extract_rename_symbols,
    _run_clang_format,
)

logger = logging.getLogger(__name__)


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


class BatchedRefactoringEngine(BaseRefactoringEngine):
    """Orchestrates Refactoring in O(K) parallel cohorts using the RefactorPlanner."""

    def __init__(
        self,
        runner: BazelRunner,
        oracle: CompilerOracle,
        dispatcher: AgentDispatcher,
        source_dir: str,
    ):
        super().__init__(runner, oracle, dispatcher, source_dir, logger=logger)
        self.planner = RefactorPlanner(self.source_dir)

    def run_pipeline(self, *args, **kwargs) -> None:
        """Template method hook delegating to run_refactoring_loop."""
        return self.run_refactoring_loop(*args, **kwargs)

    async def _dispatch_task(self, task: FileTask, prompt: str, model: str) -> bool:
        if hasattr(self.dispatcher, "dispatch_refactor_step_async"):
            return await self.dispatcher.dispatch_refactor_step_async(
                prompt, f"Batch for {task.file_path}", model
            )
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
        repo_dir = self.resolve_repo_dir(target)

        cohorts = self.planner.plan(diagnostics)
        logger.info("--- Planned %d independent cohort batches ---", len(cohorts))

        for cohort_idx, cohort in enumerate(cohorts):
            logger.info(
                "=== Processing Cohort %d/%d (Tasks: %d) ===",
                cohort_idx + 1,
                len(cohorts),
                len(cohort.tasks),
            )

            # 1. Apply ALL seed renames for the cohort
            applied_diags = []
            for task in cohort.tasks:
                sorted_diags = sorted(
                    task.diagnostics,
                    key=lambda d: -d.replacements[0].offset if d.replacements else 0,
                )
                for diag in sorted_diags:
                    old_s, new_s = _extract_rename_symbols(diag)
                    if not old_s or not new_s or old_s == new_s:
                        continue

                    decl_path = self.resolve_declaration_path(repo_dir, diag.file_path)
                    if _apply_seed_declaration_rename(decl_path, diag, old_s, new_s):
                        applied_diags.append((diag, old_s, new_s))

            if not applied_diags:
                logger.info("  [Seed] No valid target renames in cohort. Skipping.")
                continue

            # 2. Global Build (Oracle)
            logger.info("  [Oracle] Checking compilation for cohort...")
            report = self.verify_compilation(compilation_targets)

            if not report.success:
                logger.info(
                    "  [Oracle] Captured %d broken call sites. Dispatching AI Agents...",
                    len(report.errors),
                )

                async def dispatch_all():
                    coros = []
                    for task in cohort.tasks:
                        prompt = _build_batched_prompt(task, report.format_for_prompt())
                        coros.append(self._dispatch_task(task, prompt, model))
                    return await asyncio.gather(*coros, return_exceptions=True)

                results = asyncio.run(dispatch_all())

                failed_agents = [r for r in results if r is not True]
                if len(failed_agents) == len(results):
                    logger.warning(
                        "  All AI subagents failed to apply patches. Skipping cohort."
                    )
                    continue
                elif failed_agents:
                    logger.warning(
                        "  %d AI subagents encountered errors (some patches applied).",
                        len(failed_agents),
                    )
                    for err in failed_agents:
                        if isinstance(err, Exception):
                            logger.warning("    - Error: %s", err)

                self.format_code(repo_dir)

                self.verify_compilation_or_rollback(
                    tx,
                    compilation_targets,
                    f"Verification failed for Cohort {cohort_idx + 1}. Automating rollback.",
                )

            # Test Gate
            self.verify_test_gate_or_rollback(
                tx, test_target, "Unit tests failed. Automating rollback."
            )

            # Commit Cohort success
            self.commit_verified_step(
                tx,
                symbol_old=f"Cohort {cohort_idx+1}",
                symbol_new=f"{len(applied_diags)} symbols",
                message=f"style: Batched refactoring for Cohort {cohort_idx+1}",
            )
            logger.info("  Verified and committed Cohort %d", cohort_idx + 1)
