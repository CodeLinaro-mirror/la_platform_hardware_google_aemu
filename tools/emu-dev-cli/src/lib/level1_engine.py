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
import sys
from typing import Optional

from lib.agent_dispatcher import AgentDispatcher
from lib.bazel import BazelRunner
from lib.compiler_oracle import CompilerOracle
from lib.git_transaction import GitTransactionContext, resolve_git_repo_for_target
import lib.tidy_engine
from lib.tidy_engine import finalize_transaction
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


class Level1RefactoringEngine:
    """Orchestrates deterministic AST fixes, build/test validation, and AI fixups for Level 1."""

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
        self.source_dir = source_dir

    def run_level1_remediation(
        self,
        target: str,
        report_target: str,
        tx: GitTransactionContext,
        test_target: Optional[str] = None,
        checks: Optional[str] = None,
        model: str = "auto",
        dry_run: bool = False,
        force: bool = False,
        continue_tx: bool = False,
        bug_id: Optional[str] = None,
        upload: bool = False,
    ) -> bool:
        """Executes Level 1 remediation loop."""
        if not force and not tx.is_tree_clean():
            print(
                "Error: Uncommitted changes detected in repository. Commit or stash them, or use --force.",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"\n[Phase 1] Building and analyzing diagnostics for {report_target}...")
        self.runner.build(
            [report_target],
            flags=["--@goldfish_build//:clang_tidy_enabled=true"],
            check=False,
        )

        yaml_file = lib.tidy_engine._find_yaml_file(
            self.runner, report_target, self.source_dir
        )
        if not yaml_file or not yaml_file.exists():
            print("✨ No diagnostic report found or target is clean. Nothing to fix.")
            return True

        diagnostics = lib.tidy_runner.parse_fixes_yaml(str(yaml_file))
        active_diags = lib.tidy_runner.filter_diagnostics_by_level(
            diagnostics, max_level=1, checks=checks
        )
        if not active_diags:
            print(
                f"✨ Target {target} is clean for Level 1 (0 actionable diagnostics)."
            )
            return True

        print(f"Found {len(active_diags)} diagnostics matching Level 1.")
        if dry_run:
            print(
                f"\n[Dry Run] Would apply fixes for {len(active_diags)} diagnostics across target {target}."
            )
            return True

        if not continue_tx:
            anchor = tx.start_transaction(target=target, level=1, force=force)
            print(
                f"\n[Transaction Started] Anchor commit: {anchor[:8]} for {target} (Level 1)"
            )
        else:
            print(f"\n[Transaction Resumed] Resuming Level 1 repairs for {target}...")

        print(
            f"\n[Phase 2] Applying native compiler fixes for {len(active_diags)} diagnostics..."
        )
        applied = lib.tidy_runner.apply_yaml_fixes(
            str(yaml_file), cwd=Path(self.source_dir), max_level=1, checks=checks
        )
        print(f"Applied {applied} textual replacements.")
        repo_dir = resolve_git_repo_for_target(target, root_hint=Path(self.source_dir))
        lib.tidy_engine._run_clang_format(repo_dir)

        # Commit the raw Level 1 application immediately to get a safe recovery point before AI verification
        tx.commit_step(
            symbol_old="level1",
            symbol_new="level1_replacements",
            message="Apply raw Level 1 yaml diagnostics",
        )

        print("Validating compilation with fast build...")
        verify_report = self.oracle.run_compilation_check(
            [target], workspace_root=Path(self.source_dir)
        )
        if not verify_report.success:
            print(
                "⚠️ Build verification failed after native fix application. Attempting AI fixup...",
                file=sys.stderr,
            )
            prompt = _build_level1_fixup_prompt(
                target, verify_report.format_for_prompt()
            )
            self.dispatcher.dispatch_refactor_step(
                prompt=prompt,
                title=f"Fix Level 1 Compilation for {target}",
                model=model,
            )
            lib.tidy_engine._run_clang_format(repo_dir)
            verify_report_2 = self.oracle.run_compilation_check(
                [target], workspace_root=Path(self.source_dir)
            )
            if not verify_report_2.success:
                print(
                    "⚠️ Build verification failed again after AI fixup. Rolling back...",
                    file=sys.stderr,
                )
                tx.rollback_step()
                sys.exit(1)

            # Commit the AI compilation fix
            tx.commit_step(
                symbol_old="level1_fix",
                symbol_new="level1_compilation",
                message="Fix Level 1 compilation breakages",
            )

        if test_target:
            print(f"Verifying unit test target {test_target}...")
            test_res = self.runner.test(
                [test_target], check=False, capture_output=True, text=True
            )
            if test_res.returncode != 0:
                print(
                    f"⚠️ Unit tests failed for {test_target}. Attempting AI fixup...",
                    file=sys.stderr,
                )
                error_text = f"{test_res.stderr}\n{test_res.stdout}"
                prompt = _build_level1_fixup_prompt(target, error_text)
                self.dispatcher.dispatch_refactor_step(
                    prompt=prompt,
                    title=f"Fix Level 1 Unit Tests for {target}",
                    model=model,
                )
                lib.tidy_engine._run_clang_format(repo_dir)
                test_res2 = self.runner.test([test_target], check=False)
                if test_res2.returncode != 0:
                    print(
                        f"⚠️ Unit tests failed again after AI fixup. Rolling back...",
                        file=sys.stderr,
                    )
                    tx.rollback_step()
                    sys.exit(1)

                tx.commit_step(
                    symbol_old="level1_test_fix",
                    symbol_new="level1_tests",
                    message="Fix Level 1 unit test breakages",
                )

        unresolved = [d for d in active_diags if not d.replacements]
        if unresolved:
            print(
                f"⚠️ Found {len(unresolved)} Level 1 diagnostics with no native replacements. Escalating to AI...",
                file=sys.stderr,
            )
            for idx, diag in enumerate(unresolved, 1):
                print(
                    f"\n--- [{idx}/{len(unresolved)}] AI Refactoring L1 Diagnostic: {diag.name} ---"
                )
                prompt = lib.tidy_engine._build_generic_diagnostic_prompt(diag)
                self.dispatcher.dispatch_refactor_step(
                    prompt=prompt,
                    title=f"Fix L1 Diagnostic: {diag.name}",
                    model=model,
                )
                lib.tidy_engine._run_clang_format(repo_dir)
                verify_report_3 = self.oracle.run_compilation_check(
                    [target], workspace_root=Path(self.source_dir)
                )
                if not verify_report_3.success:
                    print(
                        f"⚠️ Build verification failed after AI fix for {diag.name}. Rolling back...",
                        file=sys.stderr,
                    )
                    tx.rollback_step()
                    sys.exit(1)

                tx.commit_step(
                    symbol_old=diag.name,
                    symbol_new="ai_fixed",
                    message=f"Fix missing L1 replacement for {diag.name}",
                )
        print("✅ Compilation and test verification succeeded!")

        try:
            finalize_transaction(tx, target, 1, bug_id, test_target, upload, checks)
        except Exception as e:
            print(f"⚠️ Failed to natively commit Level 1 changes: {e}", file=sys.stderr)
        return True
