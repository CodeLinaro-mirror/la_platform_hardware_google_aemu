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
import lib.tidy_runner
from lib.tidy_types import TidyDiagnostic

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


class GitNativeRefactoringEngine:
    """Orchestrates Levels 2-4 semantic refactoring using a Compiler-Guided Git-Native Transaction Loop."""

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

    def run_refactoring_loop(
        self,
        target: str,
        compilation_targets: List[str],
        level_diags: List[TidyDiagnostic],
        tx: GitTransactionContext,
        level: int,
        model: str,
        report_target: str,
        test_target: Optional[str] = None,
        checks: Optional[str] = None,
        force: bool = False,
    ) -> None:
        """Executes the loop iterating over all diagnostics, checking them with the compiler and agent."""
        repo_dir = resolve_git_repo_for_target(target, root_hint=self.source_dir)

        try:
            idx = 1
            total_diags = len(level_diags)
            failed_symbols = set()

            while True:
                # Re-fetch the diagnostic report dynamically so offsets are perfectly fresh after every single commit!
                self.runner.build(
                    [report_target],
                    flags=["--@goldfish_build//:clang_tidy_enabled=true"],
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

                print(
                    f"\n--- [{idx}/{total_diags}] Refactoring Symbol: '{old_symbol}' -> '{new_symbol}' ---"
                )
                decl_path = repo_dir / diag.file_path
                if not decl_path.exists():
                    decl_path = self.source_dir / diag.file_path

                # 1. Apply seed rename strictly at the declaration site to trigger compiler breakage
                applied = _apply_seed_declaration_rename(
                    decl_path, diag, old_symbol, new_symbol
                )
                if applied:
                    print(
                        f"  [Seed] Updated declaration at {diag.file_path}:{diag.line} for '{old_symbol}' -> '{new_symbol}'"
                    )
                else:
                    print(
                        f"  [Seed] Skipping '{old_symbol}' (could not apply seed rename)."
                    )
                    continue

                # 2. Compiler oracle: verify compilation of the actual library/test target closure
                targets_str = ", ".join(compilation_targets)
                print(f"  [Oracle] Checking compilation for {targets_str}...")
                report = self.oracle.run_compilation_check(
                    compilation_targets, workspace_root=self.source_dir
                )

                if not report.success:
                    print(
                        f"  [Oracle] Captured {len(report.errors)} broken call sites across {len(report.affected_files)} files."
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
                    print(
                        f"  [Agent] Dispatching call-site refactor (model={model})..."
                    )
                    step_ok = self.dispatcher.dispatch_refactor_step(
                        prompt=prompt,
                        title=f"Refactor {old_symbol} -> {new_symbol}",
                        model=model,
                    )

                    _run_clang_format(repo_dir)
                    # Verification check
                    verify = self.oracle.run_compilation_check(
                        compilation_targets, workspace_root=self.source_dir
                    )
                    if not verify.success:
                        print(
                            f"\n⚠️ Verification failed for symbol '{old_symbol}'. Automating transactional rollback.",
                            file=sys.stderr,
                        )
                        tx.rollback_step()
                        sys.exit(1)

                # 4. Run test gate if test target is provided
                if test_target:
                    print(f"  [Test Gate] Verifying test suite {test_target}...")
                    test_res = self.runner.test([test_target], check=False)
                    if test_res.returncode != 0:
                        print(
                            f"⚠️ Unit tests failed for {test_target}. Automating transactional rollback.",
                            file=sys.stderr,
                        )
                        tx.rollback_step()
                        sys.exit(1)

                # 5. Commit verified step into Git transaction
                tx.commit_step(
                    symbol_old=old_symbol,
                    symbol_new=new_symbol,
                    message=f"style: Modernize identifier '{old_symbol}' to '{new_symbol}'",
                )
                print(
                    f"  ✅ Verified and committed step for '{old_symbol}' -> '{new_symbol}'"
                )
                failed_symbols.remove(old_symbol)  # Success! Remove from failed set
                idx += 1

        except Exception as e:
            print(
                f"\n⚠️ Fatal orchestration error: {e}. Automating transactional rollback.",
                file=sys.stderr,
            )
            tx.rollback_step()
            raise
        except KeyboardInterrupt:
            print(
                f"\n⚠️ Transaction cancelled by user. Automating transactional rollback.",
                file=sys.stderr,
            )
            tx.rollback_step()
            sys.exit(130)


__all__ = ["GitNativeRefactoringEngine", "finalize_transaction"]
