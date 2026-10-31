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

"""Core orchestration logic for Git-Native Automated Refactoring."""

import logging
from pathlib import Path
import sys
from typing import List, Optional

from lib.agent_dispatcher import AgentDispatcher
from lib.bazel import BazelRunner
from lib.compiler_oracle import CompilerOracle
from lib.git_transaction import GitTransactionContext, resolve_git_repo_for_target
from lib.refactoring.base_engine import BaseRefactoringEngine
import lib.tidy_runner
from lib.tidy_types import RefactoringConfig, TidyDiagnostic

# Submodule imports
from lib.tidy_utils import (
    _apply_seed_declaration_rename,
    _find_yaml_file,
    _extract_rename_symbols,
    _run_clang_format,
    finalize_transaction,
)
from lib.tidy_prompts import (
    _build_generic_diagnostic_prompt,
    _build_refactor_prompt,
)

logger = logging.getLogger(__name__)


class GitNativeRefactoringEngine(BaseRefactoringEngine):
    """Orchestrates Levels 2-4 semantic refactoring using a Compiler-Guided Git-Native Transaction Loop."""

    def __init__(
        self,
        runner: BazelRunner,
        oracle: CompilerOracle,
        dispatcher: AgentDispatcher,
        source_dir: str,
    ):
        super().__init__(runner, oracle, dispatcher, source_dir, logger=logger)

    def run_pipeline(self, *args, **kwargs) -> None:
        """Template method hook delegating to run_refactoring_loop."""
        return self.run_refactoring_loop(*args, **kwargs)

    def run_refactoring_loop(
        self,
        target: str | RefactoringConfig,
        compilation_targets: Optional[List[str]] = None,
        level_diags: Optional[List[TidyDiagnostic]] = None,
        tx: Optional[GitTransactionContext] = None,
        level: int = 2,
        model: str = "auto",
        report_target: str = "",
        test_target: Optional[str] = None,
        checks: Optional[str] = None,
        force: bool = False,
        *,
        config: Optional[RefactoringConfig] = None,
    ) -> None:
        """Executes the loop iterating over all diagnostics, checking them with the compiler and agent."""
        cfg = config or (target if isinstance(target, RefactoringConfig) else None)
        if cfg is not None:
            target = cfg.target
            compilation_targets = list(
                compilation_targets
                if compilation_targets is not None
                else cfg.compilation_targets
            )
            level = cfg.level
            model = cfg.model
            report_target = report_target or cfg.report_target
            test_target = test_target if test_target is not None else cfg.test_target
            checks = checks if checks is not None else cfg.checks
            force = force or cfg.force
        compilation_targets = list(compilation_targets or [])
        level_diags = list(level_diags or [])
        assert tx is not None, "GitTransactionContext `tx` is required for run_refactoring_loop"
        repo_dir = self.resolve_repo_dir(target)

        def _loop_body() -> None:
            idx = 1
            total_diags = len(level_diags)
            failed_symbols = set()

            while True:
                # Re-fetch the diagnostic report dynamically so offsets are perfectly fresh after every single commit!
                self.runner.build(
                    [report_target],
                    flags=["--@aemu//tools/clang_tidy:clang_tidy_enabled=true"],
                    check=False,
                )
                yaml_file = _find_yaml_file(
                    self.runner, report_target, str(self.source_dir)
                )
                if not yaml_file or not yaml_file.exists():
                    break

                diagnostics = lib.tidy_runner.parse_fixes_yaml(str(yaml_file))
                active_diags = lib.tidy_runner.filter_diagnostics_by_level(
                    diagnostics, max_level=level, checks=checks
                )
                current_level_diags = [d for d in active_diags if d.level == level]

                # Filter out symbols we already know are unprocessable
                valid_diags = []
                for d in current_level_diags:
                    syms = _extract_rename_symbols(d)
                    if (
                        syms[0]
                        and syms[1]
                        and syms[0] != syms[1]
                        and syms[0] not in failed_symbols
                    ):
                        valid_diags.append((d, syms[0], syms[1]))

                if not valid_diags:
                    break

                # Select the next diagnostic by finding the alphabetically earliest file path,
                # but within that file, process the largest byte offset first to prevent shifting coordinates
                diag, old_symbol, new_symbol = min(
                    valid_diags,
                    key=lambda t: (
                        t[0].file_path,
                        -t[0].replacements[0].offset if t[0].replacements else 0,
                    ),
                )

                failed_symbols.add(
                    old_symbol
                )  # Temporarily mark as failed so we don't infinite loop if it skips

                logger.info(
                    "--- [%d/%d] Refactoring Symbol: '%s' -> '%s' ---",
                    idx,
                    total_diags,
                    old_symbol,
                    new_symbol,
                )
                decl_path = self.resolve_declaration_path(repo_dir, diag.file_path)

                # 1. Apply seed rename strictly at the declaration site to trigger compiler breakage
                applied = _apply_seed_declaration_rename(
                    decl_path, diag, old_symbol, new_symbol
                )
                if applied:
                    logger.info(
                        "  [Seed] Updated declaration at %s:%d for '%s' -> '%s'",
                        diag.file_path,
                        diag.line,
                        old_symbol,
                        new_symbol,
                    )
                else:
                    logger.info(
                        "  [Seed] Skipping '%s' (could not apply seed rename).",
                        old_symbol,
                    )
                    continue

                # 2. Compiler oracle: verify compilation of the actual library/test target closure
                targets_str = ", ".join(compilation_targets)
                logger.info("  [Oracle] Checking compilation for %s...", targets_str)
                report = self.verify_compilation(compilation_targets)

                if not report.success:
                    logger.info(
                        "  [Oracle] Captured %d broken call sites across %d files.",
                        len(report.errors),
                        len(report.affected_files),
                    )
                    prompt = _build_refactor_prompt(
                        symbol_old=old_symbol,
                        symbol_new=new_symbol,
                        level=level,
                        decl_file=diag.file_path,
                        decl_line=diag.line,
                        compiler_error_text=report.format_for_prompt(),
                    )

                    # 3. Dispatch to agent with model tier escalation
                    logger.info(
                        "  [Agent] Dispatching call-site refactor (model=%s)...", model
                    )
                    self.dispatcher.dispatch_refactor_step(
                        prompt=prompt,
                        title=f"Refactor {old_symbol} -> {new_symbol}",
                        model=model,
                    )

                    self.format_code(repo_dir)
                    self.verify_compilation_or_rollback(
                        tx,
                        compilation_targets,
                        f"Verification failed for symbol '{old_symbol}'. Automating transactional rollback.",
                    )

                # 4. Run test gate if test target is provided
                self.verify_test_gate_or_rollback(tx, test_target)

                # 5. Commit verified step into Git transaction
                self.commit_verified_step(
                    tx,
                    symbol_old=old_symbol,
                    symbol_new=new_symbol,
                    message=f"style: Modernize identifier '{old_symbol}' to '{new_symbol}'",
                )
                logger.info(
                    "  Verified and committed step for '%s' -> '%s'",
                    old_symbol,
                    new_symbol,
                )
                failed_symbols.remove(old_symbol)
                idx += 1

        self.execute_with_transaction_guard(tx, _loop_body)


__all__ = ["GitNativeRefactoringEngine", "RefactoringConfig", "finalize_transaction"]
