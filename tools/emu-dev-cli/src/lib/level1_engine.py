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

"""Level 1 Deterministic Native AST Fix & Verification Engine."""

import logging
from pathlib import Path
from typing import Optional

from core.errors import ExecutionError
from lib.agent_dispatcher import AgentDispatcher
from lib.bazel import BazelRunner
from lib.compiler_oracle import CompilerOracle
from lib.git_transaction import GitTransactionContext, resolve_git_repo_for_target
from lib.refactoring.base_engine import BaseRefactoringEngine
import lib.tidy_engine
from lib.tidy_engine import RefactoringConfig, finalize_transaction
import lib.tidy_runner

logger = logging.getLogger(__name__)


def _build_level1_fixup_prompt(target_name: str, compiler_error_text: str) -> str:
    return f"""You are performing an automated C++ semantic fix in Google Goldfish Emulator.

### Objective
We applied automated Level 1 text-based clang-tidy refactorings on `{target_name}`.
Unfortunately, the automated string replacements broke native compilation.

### Compiler Diagnostics
```text
{compiler_error_text}
```

### Instructions
1. Read the compiler output above to deduce which file changes introduced the compilation failure.
2. Fix the broken code. Either preserve the refactoring manually or append a NOLINT annotation if the tool is being overly aggressive.
3. Do not modify unrelated functions or headers.
"""


class Level1RefactoringEngine(BaseRefactoringEngine):
    """Orchestrates deterministic AST fixes, build/test validation, and AI fixups for Level 1."""

    def __init__(
        self,
        runner: BazelRunner,
        oracle: CompilerOracle,
        dispatcher: AgentDispatcher,
        source_dir: str,
    ):
        super().__init__(runner, oracle, dispatcher, source_dir, logger=logger)
        self.source_dir = source_dir

    def run_pipeline(self, *args, **kwargs) -> bool:
        """Template method hook delegating to run_level1_remediation."""
        return self.run_level1_remediation(*args, **kwargs)

    def run_level1_remediation(
        self,
        target: str | RefactoringConfig,
        report_target: str = "",
        tx: Optional[GitTransactionContext] = None,
        test_target: Optional[str] = None,
        checks: Optional[str] = None,
        model: str = "auto",
        dry_run: bool = False,
        force: bool = False,
        continue_tx: bool = False,
        bug_id: Optional[str] = None,
        upload: bool = False,
        *,
        config: Optional[RefactoringConfig] = None,
    ) -> bool:
        """Executes Level 1 remediation loop."""
        cfg = config or (target if isinstance(target, RefactoringConfig) else None)
        if cfg is not None:
            target = cfg.target
            report_target = report_target or cfg.report_target
            test_target = test_target if test_target is not None else cfg.test_target
            checks = checks if checks is not None else cfg.checks
            model = cfg.model
            dry_run = cfg.dry_run
            force = cfg.force
            continue_tx = cfg.continue_tx
            bug_id = bug_id if bug_id is not None else cfg.bug_id
            upload = cfg.upload
        assert tx is not None, "GitTransactionContext `tx` is required for run_level1_remediation"
        if not force and not tx.is_tree_clean():
            logger.error(
                "Error: Uncommitted changes detected in repository. Commit or stash them, or use --force."
            )
            raise ExecutionError(
                "Uncommitted changes detected in repository.",
                remediation="Commit or stash local changes, or pass --force.",
            )

        logger.info("[Phase 1] Building and analyzing diagnostics for %s...", report_target)
        self.runner.build(
            [report_target],
            flags=["--@aemu//tools/clang_tidy:clang_tidy_enabled=true"],
            check=False,
        )

        yaml_file = lib.tidy_engine._find_yaml_file(
            self.runner, report_target, str(self.source_dir)
        )
        if not yaml_file or not yaml_file.exists():
            logger.info("No diagnostic report found or target is clean. Nothing to fix.")
            return True

        diagnostics = lib.tidy_runner.parse_fixes_yaml(str(yaml_file))
        active_diags = lib.tidy_runner.filter_diagnostics_by_level(
            diagnostics, max_level=1, checks=checks
        )
        if not active_diags:
            logger.info(
                "Target %s is clean for Level 1 (0 actionable diagnostics).", target
            )
            return True

        logger.info("Found %d diagnostics matching Level 1.", len(active_diags))
        if dry_run:
            logger.info(
                "[Dry Run] Would apply fixes for %d diagnostics across target %s.",
                len(active_diags),
                target,
            )
            return True

        if not continue_tx:
            anchor = tx.start_transaction(target=target, level=1, force=force)
            logger.info(
                "[Transaction Started] Anchor commit: %s for %s (Level 1)",
                anchor[:8],
                target,
            )
        else:
            logger.info("[Transaction Resumed] Resuming Level 1 repairs for %s...", target)

        logger.info(
            "[Phase 2] Applying native compiler fixes for %d diagnostics...",
            len(active_diags),
        )
        applied = lib.tidy_runner.apply_yaml_fixes(
            str(yaml_file), cwd=self.source_path, max_level=1, checks=checks
        )
        logger.info("Applied %d textual replacements.", applied)
        repo_dir = self.resolve_repo_dir(target)
        self.format_code(repo_dir)

        self.commit_verified_step(
            tx,
            symbol_old="level1",
            symbol_new="level1_replacements",
            message="Apply raw Level 1 yaml diagnostics",
        )

        logger.info("Validating compilation with fast build...")
        verify_report = self.verify_compilation([target])
        if not verify_report.success:
            logger.warning(
                "Build verification failed after native fix application. Attempting AI fixup..."
            )
            prompt = _build_level1_fixup_prompt(
                target, verify_report.format_for_prompt()
            )
            self.dispatcher.dispatch_refactor_step(
                prompt=prompt,
                title=f"Fix Level 1 Compilation for {target}",
                model=model,
            )
            self.format_code(repo_dir)
            self.verify_compilation_or_rollback(
                tx,
                [target],
                "Build verification failed again after AI fixup. Rolling back...",
            )
            self.commit_verified_step(
                tx,
                symbol_old="level1_fix",
                symbol_new="level1_compilation",
                message="Fix Level 1 compilation breakages",
            )

        if test_target:
            logger.info("Verifying unit test target %s...", test_target)
            test_res = self.runner.test(
                [test_target], check=False, capture_output=True, text=True
            )
            if test_res.returncode != 0:
                logger.warning(
                    "Unit tests failed for %s. Attempting AI fixup...", test_target
                )
                error_text = f"{test_res.stderr}\n{test_res.stdout}"
                prompt = _build_level1_fixup_prompt(target, error_text)
                self.dispatcher.dispatch_refactor_step(
                    prompt=prompt,
                    title=f"Fix Level 1 Unit Tests for {target}",
                    model=model,
                )
                self.format_code(repo_dir)
                self.verify_test_gate_or_rollback(
                    tx,
                    test_target,
                    "Unit tests failed again after AI fixup. Rolling back...",
                )
                self.commit_verified_step(
                    tx,
                    symbol_old="level1_test_fix",
                    symbol_new="level1_tests",
                    message="Fix Level 1 unit test breakages",
                )

        unresolved = [d for d in active_diags if not d.replacements]
        if unresolved:
            logger.warning(
                "Found %d Level 1 diagnostics with no native replacements. Escalating to AI...",
                len(unresolved),
            )
            for idx, diag in enumerate(unresolved, 1):
                logger.info(
                    "--- [%d/%d] AI Refactoring L1 Diagnostic: %s ---",
                    idx,
                    len(unresolved),
                    diag.name,
                )
                prompt = lib.tidy_engine._build_generic_diagnostic_prompt(diag)
                self.dispatcher.dispatch_refactor_step(
                    prompt=prompt,
                    title=f"Fix L1 Diagnostic: {diag.name}",
                    model=model,
                )
                self.format_code(repo_dir)
                self.verify_compilation_or_rollback(
                    tx,
                    [target],
                    f"Build verification failed after AI fix for {diag.name}. Rolling back...",
                )
                self.commit_verified_step(
                    tx,
                    symbol_old=diag.name,
                    symbol_new="ai_fixed",
                    message=f"Fix missing L1 replacement for {diag.name}",
                )
        logger.info("Compilation and test verification succeeded!")

        try:
            finalize_transaction(tx, target, 1, bug_id, test_target, upload, checks)
        except Exception as e:
            logger.warning("Failed to natively commit Level 1 changes: %s", e)
        return True
