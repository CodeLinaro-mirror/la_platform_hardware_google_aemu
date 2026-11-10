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

"""Shared test library (`tests/testlib`) for `emu-dev-cli` hermetic unit tests."""

from testlib.gerrit_fakes import make_fake_change
from testlib.git_repo import TempGitRepo
from testlib.workflows import (
    DISALLOWED_GIT_COMMANDS_IN_PROMPTS,
    EMU_DEV_CLI_DIR,
    SRC_DIR,
    TESTS_DIR,
    WORKFLOWS_DIR,
    WorkflowTestCase,
)

__all__ = [
    "DISALLOWED_GIT_COMMANDS_IN_PROMPTS",
    "EMU_DEV_CLI_DIR",
    "SRC_DIR",
    "TESTS_DIR",
    "TempGitRepo",
    "WORKFLOWS_DIR",
    "WorkflowTestCase",
    "make_fake_change",
]
