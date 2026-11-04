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

"""Flakiness management and remediation commands for emu-dev-cli."""

from .fetch_logs import FlakinessFetchLogsCommand
from .fix import FlakinessFixCommand
from .history import FlakinessHistoryCommand
from .list_cmd import FlakinessListCommand
from .parser import FlakinessCommand, register_parser
from .progress import make_progress_callback
from .reproduce import FlakinessReproduceCommand
from .sponge import FlakinessSpongeCommand
from .triage import FlakinessTriageCommand

__all__ = [
    "FlakinessCommand",
    "FlakinessFetchLogsCommand",
    "FlakinessFixCommand",
    "FlakinessHistoryCommand",
    "FlakinessListCommand",
    "FlakinessReproduceCommand",
    "FlakinessSpongeCommand",
    "FlakinessTriageCommand",
    "register_parser",
    "make_progress_callback",
]
