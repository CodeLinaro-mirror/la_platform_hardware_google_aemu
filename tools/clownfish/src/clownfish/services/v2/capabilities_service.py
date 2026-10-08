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

"""Implementation of `android.emulation.v2.CapabilitiesService`."""

import queue
from collections.abc import Iterator

import battery_service_pb2
import capabilities_service_pb2 as pb
import capabilities_service_pb2_grpc as pb_grpc
import common_pb2
import grpc
import sensors_service_pb2

from clownfish.state import EmulatorState


class CapabilitiesServiceServicer(pb_grpc.CapabilitiesServiceServicer):
    """Stateful implementation of `CapabilitiesService` matching `capabilities_service_impl.cc`."""

    def __init__(self, state: EmulatorState) -> None:
        self.state = state

    def _build_capabilities(self) -> pb.DeviceCapabilities:
        with self.state._lock:
            p = self.state.preset
            d0 = self.state.displays[0]

            meta = pb.DeviceMetadata(
                device_kind=common_pb2.DEVICE_KIND_EMULATED,
                display_name=p.display_name,
                model_id=p.model_id,
                api_level=p.api_level,
                cpu_architecture=p.cpu_arch,
                cpu_core_count=p.cpu_cores,
                total_ram_bytes=p.ram_mb * 1024 * 1024,
                supports_play_store=p.supports_play_store,
                manufacturer=p.manufacturer,
                brand=p.brand,
                total_storage_bytes=p.storage_bytes,
                server_version=p.server_version,
                server_implementation=p.server_implementation,
            )

            disp_caps = pb.DisplayCapabilities(
                access_mode=common_pb2.ACCESS_MODE_READ_WRITE,
                fidelity=common_pb2.FIDELITY_HARDWARE,
                supports_virtual_display_creation=True,
                max_supported_display_count=p.user_configurable_displays + 1,
                built_in_displays=[
                    pb.DisplayHardware(
                        display_id=0,
                        width=d0.width,
                        height=d0.height,
                        density_dpi=d0.dpi,
                        circular=d0.circular,
                        refresh_rate_hz=d0.refresh_rate_hz,
                        supports_dynamic_resizing=True,
                        supports_rotation=True,
                        corner_radius_normalized=p.corner_radius_normalized,
                    )
                ],
                screen_capture=pb.ScreenCaptureCapabilities(
                    access_mode=common_pb2.ACCESS_MODE_READ_WRITE,
                    fidelity=common_pb2.FIDELITY_HARDWARE,
                    supported_formats=[
                        common_pb2.IMAGE_FORMAT_PNG,
                        common_pb2.IMAGE_FORMAT_RAW_RGBA8888,
                        common_pb2.IMAGE_FORMAT_JPEG,
                    ],
                    supports_row_stride=True,
                ),
            )

            battery_caps = pb.BatteryCapabilities(
                access_mode=common_pb2.ACCESS_MODE_READ_WRITE,
                fidelity=common_pb2.FIDELITY_HARDWARE,
                supported_chargers=[
                    battery_service_pb2.CHARGER_SOURCE_AC,
                    battery_service_pb2.CHARGER_SOURCE_USB,
                    battery_service_pb2.CHARGER_SOURCE_WIRELESS,
                ],
            )

            loc_caps = pb.LocationCapabilities(
                access_mode=common_pb2.ACCESS_MODE_READ_WRITE,
                fidelity=common_pb2.FIDELITY_HARDWARE,
            )

            avail_sensors = [
                sensors_service_pb2.SENSOR_TYPE_ACCELEROMETER,
                sensors_service_pb2.SENSOR_TYPE_GYROSCOPE,
                sensors_service_pb2.SENSOR_TYPE_MAGNETOMETER,
                sensors_service_pb2.SENSOR_TYPE_ORIENTATION,
                sensors_service_pb2.SENSOR_TYPE_AMBIENT_TEMPERATURE,
                sensors_service_pb2.SENSOR_TYPE_PROXIMITY,
                sensors_service_pb2.SENSOR_TYPE_LIGHT,
                sensors_service_pb2.SENSOR_TYPE_PRESSURE,
                sensors_service_pb2.SENSOR_TYPE_RELATIVE_HUMIDITY,
                sensors_service_pb2.SENSOR_TYPE_RGBC_LIGHT,
                sensors_service_pb2.SENSOR_TYPE_ACCELEROMETER_UNCALIBRATED,
                sensors_service_pb2.SENSOR_TYPE_GYROSCOPE_UNCALIBRATED,
                sensors_service_pb2.SENSOR_TYPE_MAGNETOMETER_UNCALIBRATED,
            ]
            if p.has_heart_rate:
                avail_sensors.append(sensors_service_pb2.SENSOR_TYPE_HEART_RATE)
            if p.has_wrist_tilt:
                avail_sensors.append(sensors_service_pb2.SENSOR_TYPE_WRIST_TILT)

            sensors_caps = pb.SensorsCapabilities(
                access_mode=common_pb2.ACCESS_MODE_READ_WRITE,
                fidelity=common_pb2.FIDELITY_HARDWARE,
                available_sensors=avail_sensors,
            )
            if p.is_foldable:
                sensors_caps.available_sensors.append(
                    sensors_service_pb2.SENSOR_TYPE_HINGE_ANGLE
                )
                fold_axis = (
                    sensors_service_pb2.FOLD_AXIS_VERTICAL
                    if p.fold_axis == "vertical"
                    else sensors_service_pb2.FOLD_AXIS_HORIZONTAL
                )
                sensors_caps.posture.CopyFrom(
                    pb.PostureCapabilities(
                        access_mode=common_pb2.ACCESS_MODE_READ_WRITE,
                        fidelity=common_pb2.FIDELITY_HARDWARE,
                        supported_postures=[
                            sensors_service_pb2.POSTURE_TYPE_CLOSED,
                            sensors_service_pb2.POSTURE_TYPE_HALF_OPENED,
                            sensors_service_pb2.POSTURE_TYPE_OPENED,
                            sensors_service_pb2.POSTURE_TYPE_FLIPPED,
                            sensors_service_pb2.POSTURE_TYPE_TENT,
                        ],
                        hinges=[
                            pb.HingeHardware(
                                hinge_id=i,
                                fold_axis=fold_axis,
                                min_angle_degrees=0.0,
                                max_angle_degrees=360.0,
                            )
                            for i in range(max(1, p.num_hinges))
                        ],
                        posture_definitions=[
                            pb.PostureDefinition(
                                posture=sensors_service_pb2.POSTURE_TYPE_CLOSED,
                                min_angle_degrees=0.0,
                                max_angle_degrees=30.0,
                                default_angle_degrees=0.0,
                            ),
                            pb.PostureDefinition(
                                posture=sensors_service_pb2.POSTURE_TYPE_HALF_OPENED,
                                min_angle_degrees=30.0,
                                max_angle_degrees=150.0,
                                default_angle_degrees=90.0,
                            ),
                            pb.PostureDefinition(
                                posture=sensors_service_pb2.POSTURE_TYPE_OPENED,
                                min_angle_degrees=150.0,
                                max_angle_degrees=210.0,
                                default_angle_degrees=180.0,
                            ),
                        ],
                    )
                )

            input_features = [
                pb.INPUT_FEATURE_TOUCH,
                pb.INPUT_FEATURE_MULTI_TOUCH,
                pb.INPUT_FEATURE_KEYBOARD,
                pb.INPUT_FEATURE_MOUSE_POINTER,
                pb.INPUT_FEATURE_STYLUS,
                pb.INPUT_FEATURE_DPAD,
                pb.INPUT_FEATURE_HARDWARE_NAVIGATION_KEYS,
                pb.INPUT_FEATURE_RELATIVE_MOUSE,
            ]
            if p.has_rotary_input:
                input_features.append(pb.INPUT_FEATURE_ROTARY_ENCODER)

            input_caps = pb.InputCapabilities(
                access_mode=common_pb2.ACCESS_MODE_READ_WRITE,
                fidelity=common_pb2.FIDELITY_HARDWARE,
                supported_features=input_features,
                max_touch_point_count=10,
                supports_input_activity_observation=True,
                max_batch_duration_ms=30000,
            )

            cam_caps = pb.CameraCapabilities(
                access_mode=common_pb2.ACCESS_MODE_READ_WRITE,
                fidelity=common_pb2.FIDELITY_HARDWARE,
                supported_modes=[
                    pb.CAMERA_INJECTION_MODE_STILL_FRAME,
                    pb.CAMERA_INJECTION_MODE_VIDEO_STREAM,
                ],
                cameras=[
                    pb.CameraHardware(
                        camera_id="0",
                        lens_facing=pb.CameraHardware.BACK,
                        sensor_orientation_degrees=90,
                        supported_formats=[
                            common_pb2.IMAGE_FORMAT_PNG,
                            common_pb2.IMAGE_FORMAT_RAW_RGBA8888,
                            common_pb2.IMAGE_FORMAT_JPEG,
                        ],
                    ),
                    pb.CameraHardware(
                        camera_id="1",
                        lens_facing=pb.CameraHardware.FRONT,
                        sensor_orientation_degrees=270,
                        supported_formats=[
                            common_pb2.IMAGE_FORMAT_PNG,
                            common_pb2.IMAGE_FORMAT_RAW_RGBA8888,
                            common_pb2.IMAGE_FORMAT_JPEG,
                        ],
                    ),
                ],
                supported_formats=[
                    common_pb2.IMAGE_FORMAT_PNG,
                    common_pb2.IMAGE_FORMAT_RAW_RGBA8888,
                    common_pb2.IMAGE_FORMAT_JPEG,
                ],
            )

            audio_profile = pb.AudioFormatProfile(
                supported_formats=[
                    common_pb2.SAMPLE_FORMAT_PCM16_BIT,
                    common_pb2.SAMPLE_FORMAT_F32_BIT,
                ],
                supported_sample_rates_hz=[16000, 44100, 48000],
                supported_channel_counts=[1, 2],
            )
            audio_caps = pb.AudioCapabilities(
                access_mode=common_pb2.ACCESS_MODE_READ_WRITE,
                fidelity=common_pb2.FIDELITY_HARDWARE,
                microphone_profile=audio_profile,
                speaker_profile=audio_profile,
            )

            vm_caps = pb.VmCapabilities(
                access_mode=common_pb2.ACCESS_MODE_READ_WRITE,
                fidelity=common_pb2.FIDELITY_HARDWARE,
                supported_operations=[
                    pb.VM_OPERATION_PAUSE_RESUME,
                    pb.VM_OPERATION_HARD_RESET,
                    pb.VM_OPERATION_SHUTDOWN,
                ],
            )

            snap_caps = pb.SnapshotCapabilities(
                access_mode=common_pb2.ACCESS_MODE_READ_WRITE,
                fidelity=common_pb2.FIDELITY_HARDWARE,
                supported_features=[
                    pb.SNAPSHOT_FEATURE_LOCAL,
                    pb.SNAPSHOT_FEATURE_CLOUD_STORAGE,
                ],
                supported_storage_schemes=["gs", "s3", "https", "file"],
            )

            sys_caps = pb.SystemCapabilities(
                access_mode=common_pb2.ACCESS_MODE_READ_WRITE,
                fidelity=common_pb2.FIDELITY_HARDWARE,
                supports_reboot=True,
                clipboard=pb.ClipboardCapabilities(
                    access_mode=common_pb2.ACCESS_MODE_READ_WRITE,
                    fidelity=common_pb2.FIDELITY_HARDWARE,
                ),
                biometrics=pb.BiometricsCapabilities(
                    access_mode=common_pb2.ACCESS_MODE_READ_WRITE,
                    fidelity=common_pb2.FIDELITY_HARDWARE,
                    supports_fingerprint=True,
                ),
            )

            rtc_caps = pb.RtcCapabilities(
                access_mode=common_pb2.ACCESS_MODE_READ_WRITE,
                fidelity=common_pb2.FIDELITY_HARDWARE,
                supported_tracks=[
                    common_pb2.TRACK_TYPE_SCREEN_PRIMARY,
                    common_pb2.TRACK_TYPE_SCREEN_SECONDARY,
                    common_pb2.TRACK_TYPE_AUDIO_SPEAKER,
                    common_pb2.TRACK_TYPE_AUDIO_MICROPHONE,
                ],
                max_concurrent_sessions=4,
            )

            caps = pb.DeviceCapabilities(
                metadata=meta,
                battery=battery_caps,
                location=loc_caps,
                display=disp_caps,
                sensors=sensors_caps,
                input=input_caps,
                camera=cam_caps,
                audio=audio_caps,
                vm=vm_caps,
                snapshot=snap_caps,
                system=sys_caps,
                rtc=rtc_caps,
            )
            if p.has_telephony:
                caps.telephony.CopyFrom(
                    pb.TelephonyCapabilities(
                        access_mode=common_pb2.ACCESS_MODE_READ_WRITE,
                        fidelity=common_pb2.FIDELITY_HARDWARE,
                        supported_features=[
                            pb.TELEPHONY_FEATURE_RADIO_POWER_TOGGLE,
                            pb.TELEPHONY_FEATURE_SIGNAL_STRENGTH_SIMULATION,
                            pb.TELEPHONY_FEATURE_SMS_SIMULATION,
                            pb.TELEPHONY_FEATURE_CALL_SIMULATION,
                        ],
                    )
                )
            return caps

    def GetDeviceCapabilities(
        self, request: pb.GetDeviceCapabilitiesRequest, context: grpc.ServicerContext
    ) -> pb.DeviceCapabilities:
        return self._build_capabilities()

    def StreamDeviceCapabilities(
        self, request: pb.StreamDeviceCapabilitiesRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.DeviceCapabilities]:
        with self.state.subscribe("displays") as q:
            yield self._build_capabilities()
            while context.is_active():
                try:
                    q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                yield self._build_capabilities()
