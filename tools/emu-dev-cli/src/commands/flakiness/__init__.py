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

from .fetch_logs import handle_flakiness_fetch_logs
from .fix import handle_flakiness_fix
from .history import handle_flakiness_history
from .list_cmd import handle_flakiness_list
from .parser import register_parser, run_flakiness_help
from .progress import make_progress_callback
from .reproduce import handle_flakiness_reproduce
from .triage import handle_flakiness_triage

__all__ = [
    "register_parser",
    "run_flakiness_help",
    "handle_flakiness_list",
    "handle_flakiness_fetch_logs",
    "handle_flakiness_triage",
    "handle_flakiness_history",
    "handle_flakiness_reproduce",
    "handle_flakiness_fix",
    "make_progress_callback",
]
