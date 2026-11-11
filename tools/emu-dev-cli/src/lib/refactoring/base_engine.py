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
"""BaseRefactoringEngine implementing the Template Method pattern for tidy engines."""

from abc import ABC, abstractmethod
import logging
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, TypeVar, Union

from core.errors import ExecutionError, ExitCode
from lib.agent_dispatcher import AgentDispatcher
from lib.bazel import BazelRunner
from lib.compiler_oracle import CompilerOracle, CompilerOracleReport
from lib.git_transaction import GitTransactionContext, resolve_git_repo_for_target
from lib.tidy_utils import _run_clang_format

T = TypeVar("T")


class BaseRefactoringEngine(ABC):
    """Abstract base class unifying clang-tidy refactoring engines via Template Method."""

    def __init__(
        self,
        runner: BazelRunner,
        oracle: CompilerOracle,
        dispatcher: AgentDispatcher,
        source_dir: Union[str, Path],
        logger: Optional[logging.Logger] = None,
    ):
        self.runner = runner
        self.oracle = oracle
        self.dispatcher = dispatcher
        self.source_dir = Path(source_dir)
        self._logger = logger or logging.getLogger(self.__class__.__module__)

    @property
    def source_path(self) -> Path:
        """Returns the normalized Path to the workspace source root."""
        return Path(self.source_dir)

    def resolve_repo_dir(self, target: str) -> Path:
        """Resolves the Git repository directory corresponding to a Bazel target."""
        return resolve_git_repo_for_target(target, root_hint=self.source_path)

    def resolve_declaration_path(self, repo_dir: Path, relative_path: str) -> Path:
        """Resolves a diagnostic file path against repo_dir with fallback to source_path."""
        decl_path = repo_dir / relative_path
        if not decl_path.exists():
            decl_path = self.source_path / relative_path
        return decl_path

    def format_code(self, repo_dir: Path) -> None:
        """Formats modified C/C++ files in repo_dir using clang-format."""
        _run_clang_format(repo_dir)

    def verify_compilation(self, targets: Sequence[str]) -> CompilerOracleReport:
        """Runs the compiler oracle against the specified compilation targets."""
        return self.oracle.run_compilation_check(
            list(targets), workspace_root=self.source_path
        )

    def verify_compilation_or_rollback(
        self,
        tx: GitTransactionContext,
        targets: Sequence[str],
        error_message: str,
    ) -> CompilerOracleReport:
        """Verifies compilation and rolls back + exits if compilation fails."""
        report = self.verify_compilation(targets)
        if not report.success:
            self.rollback_and_exit(tx, error_message)
        return report

    def verify_test_gate_or_rollback(
        self,
        tx: GitTransactionContext,
        test_target: Optional[str],
        error_message: Optional[str] = None,
    ) -> None:
        """Runs the optional unit test gate and rolls back + exits if tests fail."""
        if not test_target:
            return
        self._logger.info("  [Test Gate] Verifying test suite %s...", test_target)
        test_res = self.runner.test([test_target], check=False)
        if test_res.returncode != 0:
            self.rollback_and_exit(
                tx,
                error_message
                or f"Unit tests failed for {test_target}. Automating transactional rollback.",
            )

    def commit_verified_step(
        self,
        tx: GitTransactionContext,
        symbol_old: str,
        symbol_new: str,
        message: str,
    ) -> None:
        """Commits a verified refactoring step into the active Git transaction."""
        tx.commit_step(
            symbol_old=symbol_old,
            symbol_new=symbol_new,
            message=message,
        )

    def rollback_and_exit(
        self,
        tx: GitTransactionContext,
        error_message: str,
        exit_code: int = 1,
    ) -> None:
        """Logs an error, rolls back the current transactional step, and raises ExecutionError."""
        self._logger.error(error_message)
        tx.rollback_step()
        exc = ExecutionError(error_message, exit_code=exit_code)
        setattr(exc, "_already_rolled_back", True)
        raise exc

    def execute_with_transaction_guard(
        self,
        tx: GitTransactionContext,
        operation: Callable[[], T],
    ) -> T:
        """Template wrapper that guarantees transactional rollback on unhandled exceptions or interrupts."""
        try:
            return operation()
        except Exception as exc:
            if getattr(exc, "_already_rolled_back", False):
                raise
            self._logger.error(
                "Fatal orchestration error: %s. Automating transactional rollback.", exc
            )
            tx.rollback_step()
            raise
        except KeyboardInterrupt as exc:
            self._logger.warning(
                "Transaction cancelled by user. Automating transactional rollback."
            )
            tx.rollback_step()
            raise ExecutionError(
                "Transaction cancelled by user. Automating transactional rollback.",
                exit_code=ExitCode.INTERRUPTED,
            ) from exc

    @abstractmethod
    def run_pipeline(self, *args: Any, **kwargs: Any) -> Any:
        """Template method entry point implemented by concrete refactoring engines."""
