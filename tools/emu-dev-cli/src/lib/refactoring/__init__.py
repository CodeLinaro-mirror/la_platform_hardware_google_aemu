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
"""Unified clang-tidy refactoring engines built on BaseRefactoringEngine."""

from typing import Any

from lib.refactoring.base_engine import BaseRefactoringEngine

__all__ = [
    "BaseRefactoringEngine",
    "GitNativeRefactoringEngine",
    "Level1RefactoringEngine",
    "finalize_transaction",
]


def __getattr__(name: str) -> Any:
    if name == "GitNativeRefactoringEngine":
        from lib.tidy_engine import GitNativeRefactoringEngine

        return GitNativeRefactoringEngine
    if name == "Level1RefactoringEngine":
        from lib.level1_engine import Level1RefactoringEngine

        return Level1RefactoringEngine
    if name == "finalize_transaction":
        from lib.tidy_engine import finalize_transaction

        return finalize_transaction
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
