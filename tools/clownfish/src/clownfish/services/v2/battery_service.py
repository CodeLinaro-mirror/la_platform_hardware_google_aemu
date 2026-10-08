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

"""Implementation of `android.emulation.v2.BatteryService`."""

import queue
from collections.abc import Iterator

import battery_service_pb2 as pb
import battery_service_pb2_grpc as pb_grpc
import grpc

from clownfish.state import EmulatorState

_V1_TO_V2_CHARGER = {
    0: pb.CHARGER_SOURCE_NONE,
    1: pb.CHARGER_SOURCE_AC,
    2: pb.CHARGER_SOURCE_USB,
    3: pb.CHARGER_SOURCE_WIRELESS,
}
_V2_TO_V1_CHARGER = {
    pb.CHARGER_SOURCE_NONE: 0,
    pb.CHARGER_SOURCE_AC: 1,
    pb.CHARGER_SOURCE_USB: 2,
    pb.CHARGER_SOURCE_WIRELESS: 3,
    pb.CHARGER_SOURCE_DOCK: 1,
}

_V1_TO_V2_HEALTH = {
    0: pb.BatteryState.GOOD,
    1: pb.BatteryState.COLD,
    2: pb.BatteryState.DEAD,
    3: pb.BatteryState.OVER_VOLTAGE,
    4: pb.BatteryState.OVERHEAT,
}
_V2_TO_V1_HEALTH = {v: k for k, v in _V1_TO_V2_HEALTH.items()}

_V1_TO_V2_STATUS = {
    1: pb.BatteryState.CHARGING,
    2: pb.BatteryState.DISCHARGING,
    3: pb.BatteryState.NOT_CHARGING,
    4: pb.BatteryState.FULL,
}
_V2_TO_V1_STATUS = {v: k for k, v in _V1_TO_V2_STATUS.items()}


class BatteryServiceServicer(pb_grpc.BatteryServiceServicer):
    """Stateful implementation of `android.emulation.v2.BatteryService`."""

    def __init__(self, state: EmulatorState) -> None:
        self.state = state

    def _build_battery_state(self) -> pb.BatteryState:
        with self.state._lock:
            return pb.BatteryState(
                level_percent=self.state.battery_charge_level,
                charger=_V1_TO_V2_CHARGER.get(
                    self.state.battery_charger_v1, pb.CHARGER_SOURCE_NONE
                ),
                health=_V1_TO_V2_HEALTH.get(
                    self.state.battery_health_v1, pb.BatteryState.GOOD
                ),
                state=_V1_TO_V2_STATUS.get(
                    self.state.battery_status_v1, pb.BatteryState.CHARGING
                ),
            )

    def GetBatteryState(
        self, request: pb.GetBatteryStateRequest, context: grpc.ServicerContext
    ) -> pb.BatteryState:
        return self._build_battery_state()

    def StreamBatteryState(
        self, request: pb.StreamBatteryStateRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.BatteryState]:
        with self.state.subscribe("battery") as q:
            yield self._build_battery_state()
            while context.is_active():
                try:
                    q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                yield self._build_battery_state()

    def UpdateBatteryState(
        self, request: pb.UpdateBatteryStateRequest, context: grpc.ServicerContext
    ) -> pb.BatteryState:
        level = request.level_percent if request.HasField("level_percent") else None
        if level is not None and (level < 0 or level > 100):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"level_percent {level} must be within [0, 100].",
            )

        charger_v1 = (
            _V2_TO_V1_CHARGER.get(request.charger, 0)
            if request.HasField("charger")
            and request.charger != pb.CHARGER_SOURCE_UNSPECIFIED
            else None
        )
        health_v1 = (
            _V2_TO_V1_HEALTH.get(request.health, 0)
            if request.HasField("health")
            and request.health != pb.BatteryState.HEALTH_UNSPECIFIED
            else None
        )
        status_v1 = (
            _V2_TO_V1_STATUS.get(request.state)
            if request.HasField("state")
            and request.state != pb.BatteryState.STATE_UNSPECIFIED
            else None
        )

        self.state.update_battery(
            charge_level=level,
            charger_v1=charger_v1,
            health_v1=health_v1,
            status_v1=status_v1,
        )
        return self._build_battery_state()
