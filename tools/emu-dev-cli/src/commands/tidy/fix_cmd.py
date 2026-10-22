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

"""CLI command coordinator for emu-dev-cli tidy fix."""

import argparse
import json
import logging
import os
from pathlib import Path
import sys

from commands.source_directory import get_source_directory
from lib.agent_dispatcher import AgentDispatcher
from lib.bazel import BazelRunner
from lib.compiler_oracle import CompilerOracle
from lib.git_transaction import GitTransactionContext, resolve_git_repo_for_target
from lib.level1_engine import Level1RefactoringEngine
from lib.output import print_result, print_status
from lib.target_resolver import resolve_dependent_targets, resolve_report_targets
import lib.tidy_engine
from lib.tidy_engine import GitNativeRefactoringEngine, finalize_transaction
import lib.tidy_runner
from lib.tidy_parser import normalize_tidy_level

logger = logging.getLogger(__name__)


def _run_tidy_fix_single_level(
    args: argparse.Namespace, emit_json_result: bool = True
) -> None:
    """Runs closed-loop automated remediation supporting Level 1 and Levels 2-4 transactions."""
    json_mode = getattr(args, "json", False)
    source_dir = get_source_directory("emu-main-next") or os.getcwd()
    target = getattr(args, "target_flag", None) or getattr(args, "target", None)
    level = normalize_tidy_level(getattr(args, "level", 1))
    continue_tx = getattr(args, "continue_tx", False)

    # 1. Handle --abort
    if getattr(args, "abort", False):
        tx = GitTransactionContext(workspace_root=Path(source_dir), target=target)
        active = tx.get_active_transaction(workspace_root=Path(source_dir))
        if not active:
            if json_mode:
                print_result(
                    {
                        "status": "success",
                        "action": "tidy fix --abort",
                        "aborted": False,
                        "message": "No active transaction found to abort.",
                    },
                    json_mode=True,
                )
            else:
                print("No active transaction found to abort.")
            sys.exit(0)
        print_status(
            f"Aborting transaction for {active.target} and rolling back to anchor {active.anchor_commit[:8]}...",
            json_mode=json_mode,
        )
        tx.abort_transaction(anchor_commit=active.anchor_commit)
        if json_mode:
            print_result(
                {
                    "status": "success",
                    "action": "tidy fix --abort",
                    "aborted": True,
                    "target": active.target,
                    "anchor_commit": active.anchor_commit,
                },
                json_mode=True,
            )
        else:
            print(
                "✅ Successfully aborted transaction. Working tree restored to clean baseline."
            )
        sys.exit(0)

    # 2. Handle --continue
    if continue_tx:
        tx = GitTransactionContext(workspace_root=Path(source_dir), target=target)
        active = tx.get_active_transaction(workspace_root=Path(source_dir))
        if not active:
            if json_mode:
                print_result(
                    {
                        "status": "error",
                        "action": "tidy fix --continue",
                        "message": "No active transaction found in Git trailers to continue.",
                    },
                    json_mode=True,
                    is_error=True,
                )
            else:
                print(
                    "Error: No active transaction found in Git trailers to continue.",
                    file=sys.stderr,
                )
            sys.exit(1)
        print_status(
            f"Resuming transaction for target {active.target} at Level {active.level} (Anchor: {active.anchor_commit[:8]})...",
            json_mode=json_mode,
        )
        target = active.target
        level = active.level
        tx._anchor_commit = active.anchor_commit
        tx._target = target
        tx._level = level
    else:
        if not target:
            if json_mode:
                print_result(
                    {
                        "status": "error",
                        "action": "tidy fix",
                        "message": "Target is required unless using --continue, --abort, or --status.",
                    },
                    json_mode=True,
                    is_error=True,
                )
            else:
                print(
                    "Error: Target is required unless using --continue, --abort, or --status.",
                    file=sys.stderr,
                )
            sys.exit(1)
        tx = GitTransactionContext(workspace_root=Path(source_dir), target=target)

    test_target = getattr(args, "test", None)
    model = getattr(args, "model", "auto")
    dry_run = getattr(args, "dry_run", False)
    force = getattr(args, "force", False)
    upload = getattr(args, "upload", False)
    bug_id = getattr(args, "bug", None)
    checks = getattr(args, "checks", None)

    runner = BazelRunner(source_dir=source_dir)
    oracle = CompilerOracle(runner=runner)
    dispatcher = AgentDispatcher()
    report_targets = resolve_report_targets(runner, target)
    if not report_targets:
        if json_mode:
            print_result(
                {
                    "status": "error",
                    "action": "tidy fix",
                    "target": target,
                    "message": f"Could not resolve any actionable tidy targets for pattern '{target}'.",
                },
                json_mode=True,
                is_error=True,
            )
        else:
            print(
                f"Error: Could not resolve any actionable tidy targets for pattern '{target}'."
            )
        return
    report_target = report_targets[0]

    # =========================================================================
    # LEVEL 1: Deterministic Native AST Fixes
    # =========================================================================
    if level == 1:
        l1_engine = Level1RefactoringEngine(runner, oracle, dispatcher, source_dir)
        l1_engine.run_level1_remediation(
            target=target,
            report_target=report_target,
            tx=tx,
            test_target=test_target,
            checks=checks,
            model=model,
            dry_run=dry_run,
            force=force,
            continue_tx=continue_tx,
            bug_id=bug_id,
            upload=upload,
        )
        if json_mode and emit_json_result:
            print_result(
                {
                    "status": "success",
                    "action": "tidy fix",
                    "target": target,
                    "level": level,
                    "dry_run": dry_run,
                },
                json_mode=True,
            )
        return

    # =========================================================================
    # LEVELS 2-4: Compiler-Guided Git-Native Transaction Loop
    # =========================================================================
    print_status(
        f"\n[Phase 1] Building diagnostic report for {report_target}...",
        json_mode=json_mode,
    )
    runner.build(
        [report_target],
        flags=["--@goldfish_build//:clang_tidy_enabled=true"],
        check=False,
    )
    yaml_file = lib.tidy_engine._find_yaml_file(runner, report_target, source_dir)
    if not yaml_file or not yaml_file.exists():
        if json_mode and emit_json_result:
            print_result(
                {
                    "status": "success",
                    "action": "tidy fix",
                    "target": target,
                    "level": level,
                    "diagnostics_count": 0,
                },
                json_mode=True,
            )
        elif not json_mode:
            print("✨ Target is clean! Zero diagnostics found.")
        return

    diagnostics = lib.tidy_runner.parse_fixes_yaml(str(yaml_file))
    active_diags = lib.tidy_runner.filter_diagnostics_by_level(
        diagnostics, max_level=level, checks=checks
    )
    level_diags = [d for d in active_diags if d.level == level]

    # Process from the end of the file backwards to prevent offset desyncs on sequential commits
    level_diags.sort(
        key=lambda d: (d.file_path, -d.replacements[0].offset if d.replacements else 0)
    )

    if not level_diags:
        if json_mode and emit_json_result:
            print_result(
                {
                    "status": "success",
                    "action": "tidy fix",
                    "target": target,
                    "level": level,
                    "diagnostics_count": 0,
                },
                json_mode=True,
            )
        elif not json_mode:
            print(
                f"✨ Target {target} is clean for Level {level} (0 actionable diagnostics)."
            )
        return

    print_status(
        f"Found {len(level_diags)} Level {level} diagnostics to refactor.",
        json_mode=json_mode,
    )

    if dry_run:
        if json_mode and emit_json_result:
            symbols = []
            for d in level_diags:
                o_sym, n_sym = lib.tidy_engine._extract_rename_symbols(d)
                symbols.append(
                    {"old": o_sym, "new": n_sym, "file": d.file_path, "line": d.line}
                )
            print_result(
                {
                    "status": "success",
                    "action": "tidy fix",
                    "target": target,
                    "level": level,
                    "dry_run": True,
                    "symbols": symbols,
                },
                json_mode=True,
            )
        elif not json_mode:
            print(
                f"\n[Dry Run] Would refactor {len(level_diags)} symbol(s) across target {target} (Level {level}):"
            )
            for d in level_diags:
                o_sym, n_sym = lib.tidy_engine._extract_rename_symbols(d)
                print(f"  • '{o_sym}' -> '{n_sym}' ({d.file_path}:{d.line})")
        return

    if not continue_tx:
        anchor = tx.start_transaction(target=target, level=level, force=force)
        print_status(
            f"\n[Transaction Started] Anchor commit: {anchor[:8]} for {target} (Level {level})",
            json_mode=json_mode,
        )

    compilation_targets = resolve_dependent_targets(runner, target, level=level)
    engine = GitNativeRefactoringEngine(runner, oracle, dispatcher, source_dir)

    engine.run_refactoring_loop(
        target=target,
        compilation_targets=compilation_targets,
        level_diags=level_diags,
        tx=tx,
        level=level,
        model=model,
        report_target=report_target,
        test_target=test_target,
        checks=checks,
        force=force,
    )

    finalize_transaction(tx, target, level, bug_id, test_target, upload, checks)
    if json_mode and emit_json_result:
        print_result(
            {
                "status": "success",
                "action": "tidy fix",
                "target": target,
                "level": level,
                "diagnostics_count": len(level_diags),
            },
            json_mode=True,
        )


def handle_tidy_fix(args: argparse.Namespace) -> None:
    """Runs automated remediation sequentially from Level 1 up to the requested level."""
    is_json = getattr(args, "json", False)
    if getattr(args, "status", False):
        source_dir = get_source_directory("emu-main-next") or os.getcwd()
        target = getattr(args, "target_flag", None) or getattr(args, "target", None)
        tx = GitTransactionContext(workspace_root=Path(source_dir), target=target)
        active = tx.get_active_transaction(workspace_root=Path(source_dir))
        if is_json:
            if active:
                print(
                    json.dumps(
                        {
                            "active": True,
                            "target": active.target,
                            "level": active.level,
                            "anchor_commit": active.anchor_commit,
                            "latest_symbol_old": active.latest_symbol_old,
                            "latest_symbol_new": active.latest_symbol_new,
                            "status": active.status,
                        },
                        indent=2,
                    )
                )
            else:
                print(
                    json.dumps(
                        {
                            "active": False,
                            "message": "No active tidy transaction found.",
                        },
                        indent=2,
                    )
                )
        else:
            if active:
                print("Active Tidy Refactoring Transaction:")
                print(f"  • Target:        {active.target}")
                print(f"  • Level:         {active.level}")
                print(f"  • Anchor Commit: {active.anchor_commit[:8]}")
                print(
                    f"  • Last Symbol:   {active.latest_symbol_old} -> {active.latest_symbol_new}"
                )
                print(f"  • Status:        {active.status}")
            else:
                print("ℹ️ No active tidy refactoring transaction found in Git history.")
        return

    if getattr(args, "abort", False):
        _run_tidy_fix_single_level(args, emit_json_result=True)
        return

    if getattr(args, "continue_tx", False):
        _run_tidy_fix_single_level(args, emit_json_result=True)
        return

    target_level = normalize_tidy_level(getattr(args, "level", 1))

    # Sequentially execute levels
    for lvl in range(1, target_level + 1):
        print_status(
            f"\n=======================================================\n"
            f"            EXECUTING TIDY LEVEL {lvl}                  \n"
            f"=======================================================",
            json_mode=is_json,
        )
        args.level = lvl
        _run_tidy_fix_single_level(args, emit_json_result=(lvl == target_level))
