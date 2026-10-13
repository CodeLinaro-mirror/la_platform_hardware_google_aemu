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

"""Implementation of `android.emulation.v2.LocationService`."""

import queue
from collections.abc import Iterator

import grpc
import location_service_pb2 as pb
import location_service_pb2_grpc as pb_grpc

from clownfish.state import EmulatorState


class LocationServiceServicer(pb_grpc.LocationServiceServicer):
    """Stateful implementation of `android.emulation.v2.LocationService`."""

    def __init__(self, state: EmulatorState) -> None:
        self.state = state

    def _build_location_state(self) -> pb.LocationState:
        with self.state._lock:
            return pb.LocationState(
                latitude=self.state.gps_latitude,
                longitude=self.state.gps_longitude,
                altitude_meters=self.state.gps_altitude,
                speed_meters_per_second=self.state.gps_speed_mps,
                bearing_degrees=self.state.gps_bearing_deg,
                accuracy_meters=self.state.gps_accuracy_m,
                satellite_count=self.state.gps_satellites,
                timestamp_nanos=self.state.monotonic_ns(),
            )

    def _validate_and_apply(
        self, req: pb.UpdateLocationStateRequest, context: grpc.ServicerContext
    ) -> None:
        lat = req.latitude if req.HasField("latitude") else None
        lon = req.longitude if req.HasField("longitude") else None
        alt = req.altitude_meters if req.HasField("altitude_meters") else None
        spd = (
            req.speed_meters_per_second
            if req.HasField("speed_meters_per_second")
            else None
        )
        brg = req.bearing_degrees if req.HasField("bearing_degrees") else None
        acc = req.accuracy_meters if req.HasField("accuracy_meters") else None
        sats = req.satellite_count if req.HasField("satellite_count") else None

        if lat is not None and (lat < -90.0 or lat > 90.0):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"latitude {lat} must be within [-90.0, 90.0].",
            )
        if lon is not None and (lon < -180.0 or lon > 180.0):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"longitude {lon} must be within [-180.0, 180.0].",
            )
        if (
            (spd is not None and spd < 0.0)
            or (acc is not None and acc < 0.0)
            or (sats is not None and sats < 0)
        ):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "speed, accuracy, and satellite_count must be >= 0.",
            )

        self.state.update_location(
            latitude=lat,
            longitude=lon,
            altitude=alt,
            speed_mps=spd,
            bearing_deg=brg,
            accuracy_m=acc,
            satellites=sats,
        )

    def GetLocationState(
        self, request: pb.GetLocationStateRequest, context: grpc.ServicerContext
    ) -> pb.LocationState:
        return self._build_location_state()

    def StreamLocationState(
        self, request: pb.StreamLocationStateRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.LocationState]:
        with self.state.subscribe("location") as q:
            yield self._build_location_state()
            while context.is_active():
                try:
                    q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                yield self._build_location_state()

    def UpdateLocationState(
        self, request: pb.UpdateLocationStateRequest, context: grpc.ServicerContext
    ) -> pb.LocationState:
        self._validate_and_apply(request, context)
        return self._build_location_state()

    def PlayRoute(
        self, request: pb.PlayRouteRequest, context: grpc.ServicerContext
    ) -> pb.PlayRouteResponse:
        if not request.waypoints:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "PlayRouteRequest.waypoints must not be empty.",
            )
        if request.playback_speed_multiplier < 0.0:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "playback_speed_multiplier must be >= 0.0.",
            )
        for wp in request.waypoints:
            self._validate_and_apply(wp.location, context)
        return pb.PlayRouteResponse()
