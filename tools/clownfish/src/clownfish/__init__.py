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

"""Clownfish: Stateful Python fake Android Emulator gRPC server for UI validation."""

import pkgutil

import google

google.__path__ = pkgutil.extend_path(google.__path__, google.__name__)

from clownfish.discovery import DiscoveryAdvertiser
from clownfish.presets import DEVICE_PRESETS, DevicePreset, get_preset
from clownfish.renderer import SyntheticFrameRenderer
from clownfish.server import ClownfishServer
from clownfish.state import EmulatorState

__all__ = [
    "DEVICE_PRESETS",
    "ClownfishServer",
    "DevicePreset",
    "DiscoveryAdvertiser",
    "EmulatorState",
    "SyntheticFrameRenderer",
    "get_preset",
]
