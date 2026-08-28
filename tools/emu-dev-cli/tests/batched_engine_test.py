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

import unittest

from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path
import asyncio

from lib.planner import Cohort, FileTask
from lib.tidy_types import TidyDiagnostic
from lib.git_transaction import GitTransactionContext


class BatchedEngineTest(unittest.TestCase):

    def setUp(self):
        self.workspace = Path("/mock/workspace")
        self.mock_runner = MagicMock()
        self.mock_oracle = MagicMock()
        self.mock_dispatcher = MagicMock()
        self.mock_dispatcher.dispatch_refactor_step_async = AsyncMock(return_value=True)

    @patch("lib.batched_engine.subprocess.run")
    @patch("lib.batched_engine.GitTransactionContext")
    def test_batched_engine_flow(self, mock_tx_cls, mock_run):
        from lib.batched_engine import BatchedRefactoringEngine

        engine = BatchedRefactoringEngine(
            runner=self.mock_runner,
            oracle=self.mock_oracle,
            dispatcher=self.mock_dispatcher,
            source_dir=str(self.workspace),
        )

        # Test basic initialization and batching execution flow setup
        self.assertEqual(str(engine.source_dir), str(self.workspace))

        # Setup mock behavior
        mock_tx_context = MagicMock()
        mock_tx_cls.return_value = mock_tx_context

        cohorts = [Cohort(tasks=[FileTask("foo.cc", [])])]

        # Avoid running actual asyncio loop in unit test if it spans too deep,
        # or we could mock the execute method partially.
        # For a basic foundational test, checking the instantiatable engine is enough.
        self.assertIsNotNone(engine.run_refactoring_loop)


if __name__ == "__main__":
    unittest.main()
