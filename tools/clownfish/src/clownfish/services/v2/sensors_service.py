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

"""Implementation of `android.emulation.v2.SensorsService`."""

import math
import queue
from collections.abc import Iterator

import grpc
import sensors_service_pb2 as pb
import sensors_service_pb2_grpc as pb_grpc

from clownfish.state import EmulatorState

# Mapping between v1 raw_sensor_overrides indices and v2 SensorType enums.
_V1_TO_V2_OVERRIDE_MAP: dict[int, int] = {
    0: pb.SENSOR_TYPE_ACCELEROMETER,
    1: pb.SENSOR_TYPE_GYROSCOPE,
    2: pb.SENSOR_TYPE_MAGNETOMETER,
    3: pb.SENSOR_TYPE_ORIENTATION,
    11: pb.SENSOR_TYPE_ACCELEROMETER_UNCALIBRATED,
    12: pb.SENSOR_TYPE_GYROSCOPE_UNCALIBRATED,
    13: pb.SENSOR_TYPE_MAGNETOMETER_UNCALIBRATED,
}


def _vec3(t: tuple[float, float, float]) -> pb.Vector3f:
    return pb.Vector3f(x=t[0], y=t[1], z=t[2])


def _is_finite_vec(v: pb.Vector3f) -> bool:
    return math.isfinite(v.x) and math.isfinite(v.y) and math.isfinite(v.z)


class SensorsServiceServicer(pb_grpc.SensorsServiceServicer):
    """Stateful implementation of `SensorsService` matching `sensors_service_impl.cc`."""

    def __init__(self, state: EmulatorState) -> None:
        self.state = state

    def _build_sensors_state(self) -> pb.SensorsState:
        with self.state._lock:
            imu = self.state.compute_imu_telemetry()
            r, g, b, c = self.state.env_rgbc_light
            active_v2 = [
                _V1_TO_V2_OVERRIDE_MAP[k]
                for k in imu["active_overrides"]
                if k in _V1_TO_V2_OVERRIDE_MAP
            ]
            return pb.SensorsState(
                timestamp_nanos=self.state.monotonic_ns(),
                pose=pb.DevicePose(
                    rotation_degrees=_vec3(self.state.pose_rotation_deg),
                    position_meters=_vec3(self.state.pose_position_m),
                    velocity_meters_per_second=_vec3(self.state.pose_velocity_mps),
                    ambient_motion=self.state.pose_ambient_motion,
                ),
                environment=pb.AmbientEnvironment(
                    light_lux=self.state.env_light_lux,
                    rgbc_light=pb.RgbcLight(red=r, green=g, blue=b, clear=c),
                    proximity_centimeters=self.state.env_proximity_cm,
                    ambient_temperature_celsius=self.state.env_temperature_c,
                    pressure_hpa=self.state.env_pressure_hpa,
                    relative_humidity_percent=self.state.env_humidity_pct,
                    magnetic_field_ut=_vec3(self.state.env_magnetic_field_ut),
                    heart_rate_bpm=self.state.env_heart_rate_bpm,
                    wrist_tilt=self.state.env_wrist_tilt,
                ),
                imu=pb.ImuTelemetry(
                    accelerometer=_vec3(imu["accelerometer"]),
                    gyroscope=_vec3(imu["gyroscope"]),
                    magnetometer=_vec3(imu["magnetometer"]),
                    orientation_degrees=_vec3(imu["orientation_degrees"]),
                    accelerometer_uncalibrated=_vec3(imu["accelerometer_uncalibrated"]),
                    gyroscope_uncalibrated=_vec3(imu["gyroscope_uncalibrated"]),
                    magnetometer_uncalibrated=_vec3(imu["magnetometer_uncalibrated"]),
                    active_overrides=active_v2,
                ),
            )

    def _build_posture_state(self) -> pb.PostureState:
        with self.state._lock:
            return pb.PostureState(
                posture=self.state.posture,
                hinges=[
                    pb.HingeState(hinge_id=i, angle_degrees=ang)
                    for i, ang in enumerate(self.state.hinge_angles)
                ],
                rollables=[
                    pb.RollableState(rollable_id=i, extension_percent=pct)
                    for i, pct in enumerate(self.state.rollable_percents)
                ],
                timestamp_nanos=self.state.monotonic_ns(),
            )

    def _apply_sensors_update(
        self, request: pb.UpdateSensorsStateRequest, context: grpc.ServicerContext
    ) -> None:
        if request.HasField("pose"):
            pose = request.pose
            if pose.HasField("rotation_degrees") and not _is_finite_vec(
                pose.rotation_degrees
            ):
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT, "rotation_degrees must be finite."
                )
            if pose.HasField("position_meters") and not _is_finite_vec(
                pose.position_meters
            ):
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT, "position_meters must be finite."
                )
            if pose.HasField("velocity_meters_per_second") and not _is_finite_vec(
                pose.velocity_meters_per_second
            ):
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "velocity_meters_per_second must be finite.",
                )
            if pose.HasField("ambient_motion") and (
                not math.isfinite(pose.ambient_motion) or pose.ambient_motion < 0.0
            ):
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "ambient_motion must be finite and >= 0.0.",
                )

        if request.HasField("environment"):
            env = request.environment
            if env.HasField("light_lux") and (
                not math.isfinite(env.light_lux) or env.light_lux < 0.0
            ):
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT, "light_lux must be >= 0.0."
                )
            if env.HasField("proximity_centimeters") and (
                not math.isfinite(env.proximity_centimeters)
                or env.proximity_centimeters < 0.0
            ):
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "proximity_centimeters must be >= 0.0.",
                )
            if env.HasField("pressure_hpa") and (
                not math.isfinite(env.pressure_hpa) or env.pressure_hpa < 0.0
            ):
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT, "pressure_hpa must be >= 0.0."
                )
            if env.HasField("relative_humidity_percent") and (
                not math.isfinite(env.relative_humidity_percent)
                or env.relative_humidity_percent < 0.0
                or env.relative_humidity_percent > 100.0
            ):
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "relative_humidity_percent must be in [0.0, 100.0].",
                )
            if env.HasField("heart_rate_bpm") and (
                not math.isfinite(env.heart_rate_bpm) or env.heart_rate_bpm < 0.0
            ):
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT, "heart_rate_bpm must be >= 0.0."
                )
            if env.HasField("wrist_tilt") and (
                not math.isfinite(env.wrist_tilt)
                or env.wrist_tilt < 0.0
                or env.wrist_tilt > 1.0
            ):
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "wrist_tilt must be in [0.0, 1.0].",
                )

        with self.state._lock:
            if request.clear_raw_overrides:
                self.state.raw_sensor_overrides.clear()

            if request.HasField("pose"):
                pose = request.pose
                if pose.HasField("position_meters"):
                    v = pose.position_meters
                    self.state.pose_position_m = (v.x, v.y, v.z)
                if pose.HasField("velocity_meters_per_second"):
                    v = pose.velocity_meters_per_second
                    self.state.pose_velocity_mps = (v.x, v.y, v.z)
                if pose.HasField("ambient_motion"):
                    self.state.pose_ambient_motion = pose.ambient_motion

            if request.HasField("environment"):
                env = request.environment
                if env.HasField("light_lux"):
                    self.state.env_light_lux = env.light_lux
                if env.HasField("rgbc_light"):
                    c = env.rgbc_light
                    self.state.env_rgbc_light = (c.red, c.green, c.blue, c.clear)
                if env.HasField("proximity_centimeters"):
                    self.state.env_proximity_cm = env.proximity_centimeters
                if env.HasField("ambient_temperature_celsius"):
                    self.state.env_temperature_c = env.ambient_temperature_celsius
                if env.HasField("pressure_hpa"):
                    self.state.env_pressure_hpa = env.pressure_hpa
                if env.HasField("relative_humidity_percent"):
                    self.state.env_humidity_pct = env.relative_humidity_percent
                if env.HasField("magnetic_field_ut"):
                    m = env.magnetic_field_ut
                    self.state.env_magnetic_field_ut = (m.x, m.y, m.z)
                if env.HasField("heart_rate_bpm"):
                    self.state.env_heart_rate_bpm = env.heart_rate_bpm
                if env.HasField("wrist_tilt"):
                    self.state.env_wrist_tilt = env.wrist_tilt

            if request.HasField("raw_overrides"):
                raw = request.raw_overrides
                if raw.HasField("accelerometer"):
                    v = raw.accelerometer
                    self.state.raw_sensor_overrides[0] = (v.x, v.y, v.z)
                if raw.HasField("gyroscope"):
                    v = raw.gyroscope
                    self.state.raw_sensor_overrides[1] = (v.x, v.y, v.z)
                if raw.HasField("magnetometer"):
                    v = raw.magnetometer
                    self.state.raw_sensor_overrides[2] = (v.x, v.y, v.z)
                if raw.HasField("orientation_degrees"):
                    v = raw.orientation_degrees
                    self.state.raw_sensor_overrides[3] = (v.x, v.y, v.z)
                if raw.HasField("accelerometer_uncalibrated"):
                    v = raw.accelerometer_uncalibrated
                    self.state.raw_sensor_overrides[11] = (v.x, v.y, v.z)
                if raw.HasField("gyroscope_uncalibrated"):
                    v = raw.gyroscope_uncalibrated
                    self.state.raw_sensor_overrides[12] = (v.x, v.y, v.z)
                if raw.HasField("magnetometer_uncalibrated"):
                    v = raw.magnetometer_uncalibrated
                    self.state.raw_sensor_overrides[13] = (v.x, v.y, v.z)

        if request.HasField("pose") and request.pose.HasField("rotation_degrees"):
            r = request.pose.rotation_degrees
            self.state.set_pose_rotation_deg(r.x, r.y, r.z)
        else:
            self.state.notify_sensors_changed()

    def GetSensorsState(
        self, request: pb.GetSensorsStateRequest, context: grpc.ServicerContext
    ) -> pb.SensorsState:
        return self._build_sensors_state()

    def StreamSensorsState(
        self, request: pb.StreamSensorsStateRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.SensorsState]:
        with self.state.subscribe("sensor") as q:
            yield self._build_sensors_state()
            while context.is_active():
                try:
                    q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                yield self._build_sensors_state()

    def UpdateSensorsState(
        self, request: pb.UpdateSensorsStateRequest, context: grpc.ServicerContext
    ) -> pb.SensorsState:
        self._apply_sensors_update(request, context)
        return self._build_sensors_state()

    def StreamSensorsUpdates(
        self,
        request_iterator: Iterator[pb.UpdateSensorsStateRequest],
        context: grpc.ServicerContext,
    ) -> pb.StreamSensorsUpdatesResponse:
        for req in request_iterator:
            self._apply_sensors_update(req, context)
        return pb.StreamSensorsUpdatesResponse()

    def GetPostureState(
        self, request: pb.GetPostureStateRequest, context: grpc.ServicerContext
    ) -> pb.PostureState:
        with self.state._lock:
            if not self.state.preset.is_foldable:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Device does not support foldable or rollable postures.",
                )
        return self._build_posture_state()

    def StreamPostureState(
        self, request: pb.StreamPostureStateRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.PostureState]:
        with self.state._lock:
            if not self.state.preset.is_foldable:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Device does not support foldable or rollable postures.",
                )
        with self.state.subscribe("posture") as q:
            yield self._build_posture_state()
            while context.is_active():
                try:
                    q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                yield self._build_posture_state()

    def UpdatePostureState(
        self, request: pb.UpdatePostureStateRequest, context: grpc.ServicerContext
    ) -> pb.PostureState:
        with self.state._lock:
            if not self.state.preset.is_foldable:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Device does not support foldable or rollable postures.",
                )
            num_hinges = max(1, self.state.preset.num_hinges)
            num_rolls = self.state.preset.num_rollables

        if request.HasField("posture"):
            if request.posture not in (1, 2, 3, 4, 5):
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    f"Unsupported PostureType: {request.posture}",
                )
            self.state.set_posture(request.posture)

        for h in request.hinges:
            if h.hinge_id < 0 or h.hinge_id >= num_hinges:
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    f"Unsupported hinge_id {h.hinge_id}.",
                )
            if not self.state.set_hinge_angle(h.hinge_id, h.angle_degrees):
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "Hinge angle_degrees must be finite and within [0.0, 360.0].",
                )

        for r in request.rollables:
            if r.rollable_id < 0 or r.rollable_id >= num_rolls:
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    f"Unsupported rollable_id {r.rollable_id}.",
                )
            if not self.state.set_rollable(r.rollable_id, r.extension_percent):
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "Rollable extension_percent must be within [0.0, 100.0].",
                )

        return self._build_posture_state()
