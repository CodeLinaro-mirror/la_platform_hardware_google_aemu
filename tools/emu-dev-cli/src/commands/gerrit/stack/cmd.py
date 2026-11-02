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

"""`emu-dev-cli gerrit stack` command group."""

from __future__ import annotations

from commands.gerrit.stack.pre_upload import StackPreUploadCommand
from commands.gerrit.stack.status import StackStatusCommand
from core.command import CommandGroup


class GerritStackCommand(CommandGroup):
    """Groups the commands that inspect and audit a stack of related CLs."""

    name = "stack"
    help_text = "Inspect a CL stack's review state and audit local commits before upload"
    subcommand_dest = "stack_command"
    subcommands = [
        StackStatusCommand(),
        StackPreUploadCommand(),
    ]
