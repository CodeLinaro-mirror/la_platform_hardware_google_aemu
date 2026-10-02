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

"""`android.emulation.v2` (`services_v2`) gRPC service implementations for Clownfish."""

from clownfish.services.v2.audio_service import AudioServiceServicer
from clownfish.services.v2.battery_service import BatteryServiceServicer
from clownfish.services.v2.camera_service import CameraServiceServicer
from clownfish.services.v2.capabilities_service import CapabilitiesServiceServicer
from clownfish.services.v2.display_service import DisplayServiceServicer
from clownfish.services.v2.hypervisor_service import (
    HypervisorServiceServicer,
    OperationsServicer,
)
from clownfish.services.v2.input_service import InputServiceServicer
from clownfish.services.v2.location_service import LocationServiceServicer
from clownfish.services.v2.rtc_service import RtcServiceServicer
from clownfish.services.v2.sensors_service import SensorsServiceServicer
from clownfish.services.v2.system_service import SystemServiceServicer
from clownfish.services.v2.telephony_service import TelephonyServiceServicer

__all__ = [
    "AudioServiceServicer",
    "BatteryServiceServicer",
    "CameraServiceServicer",
    "CapabilitiesServiceServicer",
    "DisplayServiceServicer",
    "HypervisorServiceServicer",
    "InputServiceServicer",
    "LocationServiceServicer",
    "OperationsServicer",
    "RtcServiceServicer",
    "SensorsServiceServicer",
    "SystemServiceServicer",
    "TelephonyServiceServicer",
]
