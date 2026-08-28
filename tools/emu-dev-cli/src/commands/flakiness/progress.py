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

"""Thread-safe terminal progress display helper for flakiness operations."""

import sys
import threading
from typing import Callable, Optional


def make_progress_callback(
    json_mode: bool,
) -> Optional[Callable[[int, int, str], None]]:
    """Constructs a thread-safe terminal progress callback showing percentage bar."""
    if json_mode:
        return None

    lock = threading.Lock()

    def callback(current: int, total: int, msg: str) -> None:
        if total <= 0:
            pct = 0
            bar = "░" * 20
        else:
            pct = int((current / total) * 100)
            filled = int((pct / 100) * 20)
            bar = "█" * filled + "░" * (20 - filled)

        # Truncate message to avoid visual overflow
        trunc_msg = msg[:50] + "..." if len(msg) > 50 else msg
        line = f"\r⏳ [{bar}] {current}/{total} ({pct}%) - {trunc_msg}\033[K"
        with lock:
            sys.stderr.write(line)
            sys.stderr.flush()
            if current >= total and total > 0:
                sys.stderr.write("\n")
                sys.stderr.flush()

    return callback
