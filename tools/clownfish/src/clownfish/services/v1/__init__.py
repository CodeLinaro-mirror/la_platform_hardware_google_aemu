# Copyright 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""v1 and incubating gRPC service implementations for Clownfish."""

from clownfish.services.v1.emulator_controller import EmulatorControllerServicer
from clownfish.services.v1.screen_recording import ScreenRecordingServicer
from clownfish.services.v1.snapshot_service import SnapshotServiceServicer

__all__ = [
    "EmulatorControllerServicer",
    "ScreenRecordingServicer",
    "SnapshotServiceServicer",
]
