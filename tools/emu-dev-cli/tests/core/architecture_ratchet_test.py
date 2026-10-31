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

"""Automated AST-based architecture ratchet tests for `tools/emu-dev-cli`.

These checks lock in the architectural invariants established across Phases 1-9
(b/485291270) so that future changes cannot reintroduce:
  1. New raw `subprocess` invocations outside `ProcessRunner` / allowlisted legacy adapters.
  2. New `sys.path` mutations.
  3. `sys.exit()` / `exit()` / `quit()` / `os._exit()` calls in library modules
     (which must raise `EmuCliError` subclasses instead) or new exit calls in commands.
  4. Raw `subprocess` calls with `text=True` or `universal_newlines=True` that omit an
     explicit `encoding=` argument.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
import unittest

REPO_ROOT: Path = Path(__file__).resolve().parents[2]

SUBPROCESS_CALL_ATTRS: frozenset[str] = frozenset({
    "run",
    "Popen",
    "call",
    "check_call",
    "check_output",
    "getoutput",
    "getstatusoutput",
})

SYS_PATH_MUTATION_ATTRS: frozenset[str] = frozenset({
    "insert",
    "append",
    "extend",
    "remove",
    "pop",
    "clear",
})

LIBRARY_DIR_PREFIXES: tuple[str, ...] = (
    "src/core/",
    "src/clients/",
    "src/lib/",
    "src/targets/",
    "src/prebuilts/",
    "src/install/",
)

# Per-file ratchet ceilings for raw `subprocess.*` calls in non-test Python files.
# New code MUST inject `ProcessRunner` (`SubprocessRunner` / `FakeProcessRunner`) from
# `core.process`. This allowlist may only shrink over time.
RAW_SUBPROCESS_CEILINGS: Mapping[str, int] = {
    "harness/plugin/sidecars/aemu_harness_ui/server.py": 2,
    "install.py": 2,
    "perf_probe/build_apk.py": 3,
    "src/commands/crash/reproduce.py": 1,
    "src/commands/harness/concurrency_test_check.py": 1,
    "src/commands/harness/terminal_title.py": 2,
    "src/commands/launch.py": 1,
    "src/commands/sbom.py": 2,
    "src/commands/update_cmd.py": 2,
    "src/commands/workflow.py": 2,
    "src/core/process.py": 1,
    "src/install/installer.py": 2,
    "src/lib/bazel.py": 5,
    "src/lib/corp_sso.py": 3,
    "src/lib/emulator.py": 2,
    "src/lib/gerrit_api.py": 6,
    "src/lib/git_transaction.py": 6,
    "src/lib/mesh.py": 3,
    "src/lib/path_utils.py": 1,
    "src/lib/perf_telemetry.py": 2,
    "src/lib/screencast.py": 1,
    "src/lib/tidy_utils.py": 2,
    "workflows/address_review/verify_fixes.py": 1,
    "workflows/address_review/verify_upload_and_resolve.py": 1,
    "workflows/amend_commit/verify_amend.py": 1,
    "workflows/common/git_commit/commit_verifier.py": 7,
    "workflows/core/steps.py": 1,
    "workflows/core/yaml_runner.py": 1,
    "workflows/create_commit/get_base_commit.py": 1,
    "workflows/test_driven_bug_fixer/verify_compile.py": 1,
    "workflows/ui/server.py": 2,
}

# Per-file ratchet ceilings for `sys.path` mutations in non-test Python files.
# This allowlist may only shrink over time.
SYS_PATH_MUTATION_CEILINGS: Mapping[str, int] = {
    "harness/plugin/sidecars/aemu_gerrit_ui/server.py": 2,
    "install.py": 1,
    "src/__main__.py": 2,
    "src/commands/crash/advisor.py": 2,
    "src/commands/harness/guardrails.py": 1,
    "src/commands/harness/terminal_title.py": 1,
    "src/commands/update_cmd.py": 1,
}

# Library modules under `LIBRARY_DIR_PREFIXES` must raise `EmuCliError` subclasses
# or return `CommandResult` rather than terminating the process.
LIBRARY_EXIT_CEILINGS: Mapping[str, int] = {}

# Per-file ratchet ceilings for `sys.exit()` / `exit()` / `quit()` / `os._exit()`
# across all non-test files in `tools/emu-dev-cli`.
ALL_EXIT_CEILINGS: Mapping[str, int] = {
    "install.py": 2,
    "perf_probe/build_apk.py": 1,
    "src/__main__.py": 4,
    "workflows/address_review/prepare_review_batch.py": 1,
    "workflows/address_review/verify_fixes.py": 1,
    "workflows/address_review/verify_upload_and_resolve.py": 1,
    "workflows/amend_commit/setup_msg_files.py": 1,
    "workflows/amend_commit/verify_amend.py": 1,
    "workflows/amend_commit/verify_proposed_msg.py": 1,
    "workflows/analyze_build_times/verify_report.py": 1,
    "workflows/bug_to_markdown/verify_bug_markdown.py": 2,
    "workflows/compare_perf/prepare_perf_batch.py": 1,
    "workflows/compare_perf/verify_perf_report.py": 1,
    "workflows/compare_perf/verify_perf_run.py": 1,
    "workflows/core/yaml_runner.py": 2,
    "workflows/create_commit/get_base_commit.py": 1,
    "workflows/create_commit/setup_msg_files.py": 1,
    "workflows/create_commit/verify_commit.py": 1,
    "workflows/create_commit/verify_proposed_msg.py": 1,
    "workflows/hello_world_joke_rating/verify_joke_rating.py": 2,
    "workflows/test_driven_bug_fixer/detect_env.py": 1,
    "workflows/test_driven_bug_fixer/verify_compile.py": 1,
    "workflows/triage_crashes/prepare_triage_batch.py": 1,
    "workflows/triage_crashes/verify_triage_batch.py": 1,
}


@dataclass
class FileAstReport:
    """AST violation counts and line numbers collected for a single Python source file."""

    rel_path: str
    subprocess_lines: list[int] = field(default_factory=list)
    unencoded_text_lines: list[int] = field(default_factory=list)
    sys_path_lines: list[int] = field(default_factory=list)
    exit_lines: list[int] = field(default_factory=list)
    imported_modules: set[str] = field(default_factory=set)


class _ArchitectureAstVisitor(ast.NodeVisitor):
    """AST visitor detecting raw subprocess calls, sys.path mutations, and process exits."""

    def __init__(self, rel_path: str) -> None:
        self.report = FileAstReport(rel_path=rel_path)
        self._subprocess_module_aliases: set[str] = {"subprocess"}
        self._subprocess_fn_aliases: set[str] = set()
        self._sys_aliases: set[str] = {"sys"}
        self._os_aliases: set[str] = {"os"}
        self._exit_fn_aliases: set[str] = {"exit", "quit"}
        self._module_alias_map: dict[str, set[str]] = {
            "subprocess": self._subprocess_module_aliases,
            "sys": self._sys_aliases,
            "os": self._os_aliases,
        }

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.report.imported_modules.add(alias.name.split(".", 1)[0])
            target_set = self._module_alias_map.get(alias.name)
            if target_set is not None:
                target_set.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        if mod:
            self.report.imported_modules.add(mod.split(".", 1)[0])
        for alias in node.names:
            bound = alias.asname or alias.name
            if mod == "subprocess" and alias.name in SUBPROCESS_CALL_ATTRS:
                self._subprocess_fn_aliases.add(bound)
            if (mod, alias.name) in (("sys", "exit"), ("os", "_exit")):
                self._exit_fn_aliases.add(bound)
        self.generic_visit(node)

    def _is_subprocess_call(self, node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            return (
                func.value.id in self._subprocess_module_aliases
                and func.attr in SUBPROCESS_CALL_ATTRS
            )
        if isinstance(func, ast.Name):
            return func.id in self._subprocess_fn_aliases
        return False

    def _is_unencoded_text_subprocess_call(self, node: ast.Call) -> bool:
        has_text_true = False
        has_encoding = False
        for kw in node.keywords:
            if (
                kw.arg in ("text", "universal_newlines")
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
            ):
                has_text_true = True
            if kw.arg == "encoding":
                if not (isinstance(kw.value, ast.Constant) and kw.value.value is None):
                    has_encoding = True
        return has_text_true and not has_encoding

    def _is_exit_call(self, node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in self._sys_aliases and func.attr == "exit":
                return True
            if func.value.id in self._os_aliases and func.attr == "_exit":
                return True
        if isinstance(func, ast.Name) and func.id in self._exit_fn_aliases:
            return True
        return False

    def _is_sys_path_expr(self, expr: ast.AST) -> bool:
        return (
            isinstance(expr, ast.Attribute)
            and isinstance(expr.value, ast.Name)
            and expr.value.id in self._sys_aliases
            and expr.attr == "path"
        )

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_subprocess_call(node):
            self.report.subprocess_lines.append(node.lineno)
            if self._is_unencoded_text_subprocess_call(node):
                self.report.unencoded_text_lines.append(node.lineno)
        if self._is_exit_call(node):
            self.report.exit_lines.append(node.lineno)
        if (
            isinstance(node.func, ast.Attribute)
            and self._is_sys_path_expr(node.func.value)
            and node.func.attr in SYS_PATH_MUTATION_ATTRS
        ):
            self.report.sys_path_lines.append(node.lineno)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if self._is_sys_path_expr(target) or (
                isinstance(target, ast.Subscript) and self._is_sys_path_expr(target.value)
            ):
                self.report.sys_path_lines.append(node.lineno)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if self._is_sys_path_expr(node.target):
            self.report.sys_path_lines.append(node.lineno)
        self.generic_visit(node)


def analyze_python_source(source: str, rel_path: str = "<memory>") -> FileAstReport:
    """Parses `source` into an AST and returns its `FileAstReport`."""
    tree = ast.parse(source, filename=rel_path)
    visitor = _ArchitectureAstVisitor(rel_path)
    visitor.visit(tree)
    return visitor.report


def iter_non_test_python_files(root: Path = REPO_ROOT) -> list[Path]:
    """Returns sorted non-test `.py` files under `root`."""
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if "tests" not in path.parts
        and "__pycache__" not in path.parts
        and not path.name.endswith("_test.py")
    ]


def scan_repository_ast(root: Path = REPO_ROOT) -> dict[str, FileAstReport]:
    """Scans all non-test Python files under `root` and returns `{rel_path: FileAstReport}`."""
    reports: dict[str, FileAstReport] = {}
    for file_path in iter_non_test_python_files(root):
        rel = file_path.relative_to(root).as_posix()
        reports[rel] = analyze_python_source(
            file_path.read_text(encoding="utf-8"),
            rel_path=rel,
        )
    return reports


def find_ceiling_violations(
    actual_lines_by_file: Mapping[str, list[int]],
    ceilings: Mapping[str, int],
    *,
    category: str,
    remediation: str,
) -> list[str]:
    """Returns human-readable violation strings when any file exceeds its ratchet ceiling."""
    violations: list[str] = []
    for rel_path, lines in sorted(actual_lines_by_file.items()):
        if not lines:
            continue
        allowed = ceilings.get(rel_path, 0)
        if len(lines) > allowed:
            line_list = ", ".join(f"L{ln}" for ln in lines)
            violations.append(
                f"{rel_path}: found {len(lines)} {category} at {line_list} "
                f"(ratchet ceiling is {allowed}). {remediation}"
            )
    return violations


class ArchitectureRatchetTest(unittest.TestCase):
    """Enforces AST-level architectural ratchets across `tools/emu-dev-cli`."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.reports = scan_repository_ast(REPO_ROOT)

    def test_zero_unencoded_subprocess_text_calls(self) -> None:
        offenders = {
            rel: rep.unencoded_text_lines
            for rel, rep in self.reports.items()
            if rep.unencoded_text_lines
        }
        violations = find_ceiling_violations(
            offenders,
            {},
            category="unencoded subprocess text=True call(s)",
            remediation=(
                "Use ProcessRunner from core.process (which sets encoding='utf-8', "
                "errors='replace') or pass encoding='utf-8' explicitly."
            ),
        )
        self.assertEqual(violations, [], "\n".join(violations))

    def test_raw_subprocess_calls_do_not_exceed_ratchet_ceilings(self) -> None:
        actual = {
            rel: rep.subprocess_lines
            for rel, rep in self.reports.items()
            if rep.subprocess_lines
        }
        violations = find_ceiling_violations(
            actual,
            RAW_SUBPROCESS_CEILINGS,
            category="raw subprocess call(s)",
            remediation=(
                "Inject ProcessRunner (SubprocessRunner / FakeProcessRunner) from "
                "core.process instead of calling subprocess.* directly."
            ),
        )
        self.assertEqual(violations, [], "\n".join(violations))
        self.assertLessEqual(
            sum(len(lines) for lines in actual.values()),
            sum(RAW_SUBPROCESS_CEILINGS.values()),
        )

    def test_sys_path_mutations_do_not_exceed_ratchet_ceilings(self) -> None:
        actual = {
            rel: rep.sys_path_lines
            for rel, rep in self.reports.items()
            if rep.sys_path_lines
        }
        violations = find_ceiling_violations(
            actual,
            SYS_PATH_MUTATION_CEILINGS,
            category="sys.path mutation(s)",
            remediation=(
                "Import packages via standard module resolution from src/ rather than "
                "mutating sys.path at import time."
            ),
        )
        self.assertEqual(violations, [], "\n".join(violations))

    def test_library_modules_do_not_call_sys_exit(self) -> None:
        library_exits = {
            rel: rep.exit_lines
            for rel, rep in self.reports.items()
            if rel.startswith(LIBRARY_DIR_PREFIXES) and rep.exit_lines
        }
        violations = find_ceiling_violations(
            library_exits,
            LIBRARY_EXIT_CEILINGS,
            category="process exit call(s) in library code",
            remediation=(
                "Raise a typed EmuCliError (UsageError, ConfigError, ExecutionError, "
                "SubprocessError) from core.errors instead of calling sys.exit()."
            ),
        )
        self.assertEqual(violations, [], "\n".join(violations))

    def test_all_non_test_exit_calls_do_not_exceed_ratchet_ceilings(self) -> None:
        actual = {
            rel: rep.exit_lines
            for rel, rep in self.reports.items()
            if rep.exit_lines
        }
        violations = find_ceiling_violations(
            actual,
            ALL_EXIT_CEILINGS,
            category="sys.exit()/exit() call(s)",
            remediation=(
                "Raise EmuCliError or return an exit status integer to the CLI dispatcher "
                "instead of adding new sys.exit() calls."
            ),
        )
        self.assertEqual(violations, [], "\n".join(violations))

    def test_core_web_scaffolding_is_layered_and_free_of_forbidden_calls(self) -> None:
        web_reports = {
            rel: rep
            for rel, rep in self.reports.items()
            if rel.startswith("src/core/web/")
        }
        self.assertGreaterEqual(len(web_reports), 7)
        forbidden_imports = {"commands", "workflows", "yaml"}
        for rel, rep in web_reports.items():
            self.assertEqual(rep.subprocess_lines, [], f"{rel} must not call subprocess.*")
            self.assertEqual(rep.sys_path_lines, [], f"{rel} must not mutate sys.path")
            self.assertEqual(rep.exit_lines, [], f"{rel} must not call sys.exit()")
            bad_imports = rep.imported_modules & forbidden_imports
            self.assertEqual(
                bad_imports,
                set(),
                f"{rel} must not depend on higher-level modules {bad_imports}",
            )

    def test_emu_core_library_is_hermetic_and_free_of_lib_imports(self) -> None:
        emu_core_reports = {
            rel: rep
            for rel, rep in self.reports.items()
            if rel.startswith("src/core/") or rel.startswith("src/clients/")
        }
        self.assertGreaterEqual(len(emu_core_reports), 15)
        forbidden_non_hermetic = {
            "lib",
            "commands",
            "targets",
            "install",
            "prebuilts",
            "workflows",
            "benchmarks",
        }
        for rel, rep in emu_core_reports.items():
            bad_imports = rep.imported_modules & forbidden_non_hermetic
            self.assertEqual(
                bad_imports,
                set(),
                f"emu_core file {rel} must not import non-hermetic package(s) {bad_imports}",
            )

    def test_ast_scanner_detects_synthetic_violations_with_unicode(self) -> None:
        synthetic_code = """
import os
import subprocess as sp
from subprocess import Popen
import sys

def bad_helper(label: str = "测试_🚀_é") -> None:
    sys.path.insert(0, "/tmp/测试_🚀_é")
    sys.path += ["/tmp/extra"]
    sp.run(["echo", label], text=True)
    Popen(["echo", label], universal_newlines=True, encoding="utf-8")
    sys.exit(1)
    os._exit(2)
    exit(3)
"""
        rep = analyze_python_source(synthetic_code, rel_path="src/core/bad_测试_🚀_é.py")
        self.assertEqual(len(rep.subprocess_lines), 2)
        self.assertEqual(len(rep.unencoded_text_lines), 1)
        self.assertEqual(len(rep.sys_path_lines), 2)
        self.assertEqual(len(rep.exit_lines), 3)

    def test_production_code_is_free_of_mock_probing_and_retired_facades(self) -> None:
        retired_paths = (
            "src/lib/agent.py",
            "src/lib/android_build_client.py",
            "src/lib/batched_engine.py",
            "src/lib/buganizer.py",
            "src/lib/diagnostics.py",
            "src/lib/patch_verifier.py",
            "src/lib/perf_runner.py",
            "src/lib/planner.py",
            "workflows/amend_commit/extract_change_id.py",
            "workflows/amend_commit/get_base_commit.py",
        )
        for rel in retired_paths:
            self.assertFalse(
                (REPO_ROOT / rel).exists(),
                f"Retired legacy facade/module {rel} must not be reintroduced",
            )

        forbidden_tokens = (
            "_mock_name",
            "assert_called",
            "resolve_ui_hook",
            "register_ui_hook",
            "_resolve_patched_adb_cmd",
            "_resolve_emulator_executable_fn",
        )
        for path in iter_non_test_python_files(REPO_ROOT):
            rel = path.relative_to(REPO_ROOT).as_posix()
            text = path.read_text(encoding="utf-8")
            for token in forbidden_tokens:
                self.assertNotIn(
                    token,
                    text,
                    f"Production file {rel} must not probe mock/monkeypatch token {token!r}",
                )
            if not rel.startswith(("src/core/", "src/clients/")):
                self.assertNotIn(
                    "FakeProcessRunner",
                    text,
                    f"Production file {rel} must not branch on FakeProcessRunner",
                )
                self.assertNotIn(
                    "_auth_status",
                    text,
                    f"Production file {rel} must not probe private fake attribute '_auth_status'",
                )

    def test_all_cli_subcommands_inherit_from_command_or_command_group(self) -> None:
        import argparse
        from commands import (
            build_times,
            crash,
            create,
            cts,
            docs,
            fetch_build,
            flakiness,
            gerrit,
            harness,
            init_cmd,
            launch,
            mesh,
            perf,
            sbom,
            screencast,
            source_directory,
            tidy,
            update_cmd,
            update_prebuilt,
            workflow,
        )
        from core.command import COMMAND_INSTANCE_ATTR, Command
        from install import installer

        # 1. Verify zero legacy `set_defaults(func=...)` calls exist in src/
        legacy_func_bindings: list[str] = []
        for path in iter_non_test_python_files(REPO_ROOT):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if not rel.startswith("src/"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "set_defaults"
                ):
                    for kw in node.keywords:
                        if kw.arg == "func":
                            legacy_func_bindings.append(f"{rel}:L{node.lineno}")
        self.assertEqual(
            legacy_func_bindings,
            [],
            f"All CLI subparsers in src/ must bind Command/CommandGroup instead of set_defaults(func=...): {legacy_func_bindings}",
        )

        # 2. Verify 100% of registered subparsers bind Command/CommandGroup instances
        parser = argparse.ArgumentParser(prog="emu-dev-cli")
        subparsers = parser.add_subparsers(dest="subcommand")
        for mod in (
            build_times,
            crash,
            create,
            cts,
            docs,
            flakiness,
            gerrit,
            harness,
            init_cmd,
            installer,
            launch,
            mesh,
            perf,
            sbom,
            screencast,
            source_directory,
            tidy,
            update_cmd,
            workflow,
        ):
            mod.register_parser(subparsers)
        fetch_build.register_parser(subparsers, "linux-x86_64")
        update_prebuilt.register_parser(subparsers, "linux-x86_64")

        self.assertGreaterEqual(len(subparsers.choices), 21)
        for name, subparser in sorted(subparsers.choices.items()):
            cmd_inst = subparser.get_default(COMMAND_INSTANCE_ATTR)
            self.assertIsInstance(
                cmd_inst,
                Command,
                f"Top-level subcommand '{name}' must bind a Command/CommandGroup instance",
            )
            self.assertIsNone(
                subparser.get_default("func"),
                f"Top-level subcommand '{name}' must not set legacy 'func' default",
            )

    def test_phase14_gerrit_ui_composition_and_workflow_state_store_invariants(
        self,
    ) -> None:
        from commands.gerrit.services import (
            DiffService,
            GerritWorkspaceContext,
            InlineQaService,
            ReviewQueueService,
            VoteSubmitService,
        )
        from commands.gerrit.ui_server import GerritUIService
        from core.web.handler import JsonRequestHandler
        from workflows.core.state_store import WorkflowStateStore
        from workflows.core.yaml_runner import WorkflowRegistry

        # 1. GerritUIService uses pure composition instead of 4-way MRO mixin inheritance
        self.assertEqual(GerritUIService.__bases__, (object,))
        self.assertIsNotNone(GerritWorkspaceContext)
        self.assertIsNotNone(DiffService)
        self.assertIsNotNone(ReviewQueueService)
        self.assertIsNotNone(VoteSubmitService)
        self.assertIsNotNone(InlineQaService)

        # 2. WorkflowStateStore and WorkflowRegistry exist and are wired
        self.assertIsNotNone(WorkflowStateStore)
        self.assertIsNotNone(WorkflowRegistry)

        # 3. workflows/ui/server.py and workflows/core/yaml_runner.py do not swap sys.stdout or os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"]
        ui_server_src = (REPO_ROOT / "workflows" / "ui" / "server.py").read_text(
            encoding="utf-8"
        )
        yaml_runner_src = (
            REPO_ROOT / "workflows" / "core" / "yaml_runner.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("sys.stdout =", ui_server_src)
        self.assertNotIn(
            'os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] =', ui_server_src
        )
        self.assertNotIn(
            'os.environ["EMU_DEV_CLI_WORKFLOW_STATE_DIR"] =', yaml_runner_src
        )
        self.assertNotIn('os.environ["PYTHONPATH"] =', yaml_runner_src)

        # 4. Harness sidecar server subclasses core.web.JsonRequestHandler
        harness_sidecar_src = (
            REPO_ROOT
            / "harness"
            / "plugin"
            / "sidecars"
            / "aemu_harness_ui"
            / "server.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "class HarnessSidecarHandler(JsonRequestHandler):",
            harness_sidecar_src,
        )
        self.assertIsNotNone(JsonRequestHandler)


if __name__ == "__main__":
    unittest.main()
