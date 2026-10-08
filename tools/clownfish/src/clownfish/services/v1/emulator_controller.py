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

"""Implementation of `android.emulation.control.EmulatorController` (v1)."""

import queue
import time
from collections.abc import Iterator

import grpc
from aemu.proto import emulator_controller_pb2 as pb
from aemu.proto import emulator_controller_pb2_grpc as pb_grpc
from google.protobuf import empty_pb2

from clownfish.renderer import (
    MAX_DISPLAY_DIMENSION,
    MAX_DISPLAY_DPI,
    MmapSizeError,
    MmapUriError,
    SyntheticFrameRenderer,
    write_frame_to_mmap_file,
)
from clownfish.state import EmulatorState


class EmulatorControllerServicer(pb_grpc.EmulatorControllerServicer):
    """Stateful implementation of `android.emulation.control.EmulatorController`."""

    def __init__(self, state: EmulatorState, renderer: SyntheticFrameRenderer) -> None:
        self.state = state
        self.renderer = renderer

    # -------------------------------------------------------------------------
    # Status & VM Lifecycle
    # -------------------------------------------------------------------------

    def getStatus(
        self, request: empty_pb2.Empty, context: grpc.ServicerContext
    ) -> pb.EmulatorStatus:
        with self.state._lock:
            p = self.state.preset
            d0 = self.state.displays[0]
            hw_entries = [
                pb.Entry(key="avd.name", value=p.avd_name),
                pb.Entry(key="avd.id", value=p.avd_id),
                pb.Entry(key="hw.cpu.arch", value=p.cpu_arch),
                pb.Entry(key="hw.cpu.ncore", value=str(p.cpu_cores)),
                pb.Entry(key="hw.ramSize", value=str(p.ram_mb)),
                pb.Entry(key="hw.device.name", value=p.device_type),
                pb.Entry(key="hw.lcd.width", value=str(d0.width)),
                pb.Entry(key="hw.lcd.height", value=str(d0.height)),
                pb.Entry(key="hw.lcd.density", value=str(d0.dpi)),
                pb.Entry(key="hw.sensor.hinge", value="yes" if p.is_foldable else "no"),
                pb.Entry(key="hw.sensor.hinge.count", value=str(p.num_hinges)),
                pb.Entry(key="hw.sensor.hinge.ranges", value=p.hinge_ranges),
                pb.Entry(key="hw.sensor.hinge.defaults", value=p.hinge_defaults),
                pb.Entry(key="hw.sensor.posture_list", value=p.posture_list),
                pb.Entry(
                    key="hw.sensor.hinge_angles_posture_definitions",
                    value=p.hinge_angles_posture_definitions,
                ),
                pb.Entry(key="hw.resizable.configs", value=p.resizable_config),
                pb.Entry(
                    key="PlayStore.enabled",
                    value="true" if p.supports_play_store else "false",
                ),
                pb.Entry(key="multidisplay", value="available"),
                pb.Entry(key="grpc.CameraNotification", value="available"),
                pb.Entry(key="grpc.SupportTextViewFocus", value="available"),
            ]
            hw_map = {e.key: e.value for e in hw_entries}
            guest_map = {
                "androidVersion": str(p.api_level),
                "multidisplay": "available",
                "grpc.CameraNotification": "available",
                "grpc.SupportTextViewFocus": "available",
            }
            return pb.EmulatorStatus(
                version=f"{p.emulator_version}-{p.emulator_build}",
                uptime=self.state.uptime_ms(),
                booted=self.state.booted,
                vmConfig=pb.VmConfiguration(
                    hypervisorType=pb.VmConfiguration.KVM,
                    numberOfCpuCores=p.cpu_cores,
                    ramSizeBytes=p.ram_mb * 1024 * 1024,
                ),
                hardwareConfig=pb.EntryList(entry=hw_entries),
                guestConfig=guest_map,
                platformConfig=hw_map,
                heartbeat=self.state.frame_seq,
            )

    def getVmState(
        self, request: empty_pb2.Empty, context: grpc.ServicerContext
    ) -> pb.VmRunState:
        with self.state._lock:
            return pb.VmRunState(state=self.state.vm_state_v1)

    def setVmState(
        self, request: pb.VmRunState, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        self.state.set_vm_state_v1(request.state)
        return empty_pb2.Empty()

    # -------------------------------------------------------------------------
    # Screenshots & Display Management (including file:// MMAP)
    # -------------------------------------------------------------------------

    def _build_image(
        self, request: pb.ImageFormat, context: grpc.ServicerContext
    ) -> pb.Image:
        disp_id = request.display
        with self.state._lock:
            if disp_id not in self.state.displays:
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    f"Display {disp_id} does not exist.",
                )

        if request.format == pb.ImageFormat.RGB888:
            fmt_str = "RGB888"
        elif request.format == pb.ImageFormat.RGBA8888:
            fmt_str = "RGBA8888"
        else:
            fmt_str = "PNG"

        frame = self.renderer.render_frame(
            display_id=disp_id,
            req_width=request.width,
            req_height=request.height,
            output_format=fmt_str,
        )

        with self.state._lock:
            rx, ry, rz = self.state.pose_rotation_deg
            has_resizable = bool(self.state.preset.resizable_config)

        out_fmt = pb.ImageFormat(
            format=request.format,
            width=frame.width,
            height=frame.height,
            display=disp_id,
            rotation=pb.Rotation(
                rotation=frame.rotation_skin,
                xAxis=rx,
                yAxis=ry,
                zAxis=rz,
            ),
        )
        if frame.folded:
            out_fmt.foldedDisplay.CopyFrom(
                pb.FoldedDisplay(
                    width=frame.folded_width,
                    height=frame.folded_height,
                    xOffset=frame.folded_x_offset,
                    yOffset=frame.folded_y_offset,
                )
            )
        if has_resizable:
            out_fmt.displayMode = frame.display_mode

        # Handle ImageTransport.MMAP (file:// only)
        if request.HasField("transport") and (
            request.transport.channel == pb.ImageTransport.MMAP
        ):
            try:
                write_frame_to_mmap_file(
                    uri=request.transport.handle,
                    payload=frame.data,
                    width=frame.width,
                    height=frame.height,
                    bpp=frame.bpp,
                )
            except MmapUriError as err:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(err))
            except FileNotFoundError as err:
                context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(err))
            except MmapSizeError as err:
                context.abort(grpc.StatusCode.OUT_OF_RANGE, str(err))

            out_fmt.transport.CopyFrom(
                pb.ImageTransport(
                    channel=pb.ImageTransport.MMAP,
                    handle=request.transport.handle,
                )
            )
            image_bytes = b""
        else:
            image_bytes = frame.data

        return pb.Image(
            format=out_fmt,
            width=frame.width,
            height=frame.height,
            image=image_bytes,
            seq=frame.seq,
            timestampUs=frame.timestamp_ns // 1000,
        )

    def getScreenshot(
        self, request: pb.ImageFormat, context: grpc.ServicerContext
    ) -> pb.Image:
        return self._build_image(request, context)

    def streamScreenshot(
        self, request: pb.ImageFormat, context: grpc.ServicerContext
    ) -> Iterator[pb.Image]:
        with self.state.subscribe("frame") as q:
            yield self._build_image(request, context)
            while context.is_active():
                try:
                    q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                yield self._build_image(request, context)

    def getDisplayConfigurations(
        self, request: empty_pb2.Empty, context: grpc.ServicerContext
    ) -> pb.DisplayConfigurations:
        with self.state._lock:
            p = self.state.preset
            displays = [
                pb.DisplayConfiguration(
                    width=d.width,
                    height=d.height,
                    dpi=d.dpi,
                    flags=d.flags,
                    display=d.display_id,
                )
                for _, d in sorted(self.state.displays.items())
            ]
            return pb.DisplayConfigurations(
                displays=displays,
                userConfigurable=p.user_configurable_displays,
                maxDisplays=p.max_displays,
            )

    def setDisplayConfigurations(
        self, request: pb.DisplayConfigurations, context: grpc.ServicerContext
    ) -> pb.DisplayConfigurations:
        max_cfg = self.state.preset.user_configurable_displays
        seen_ids: set[int] = set()
        configs: list[tuple[int, int, int, int, int]] = []
        for d in request.displays:
            if d.display < 0 or d.display > max_cfg:
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    f"Display id {d.display} out of configurable range [0, {max_cfg}].",
                )
            if d.display in seen_ids:
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    f"Duplicate display id {d.display}.",
                )
            if (
                d.width <= 0
                or d.width > MAX_DISPLAY_DIMENSION
                or d.height <= 0
                or d.height > MAX_DISPLAY_DIMENSION
                or d.dpi <= 0
                or d.dpi > MAX_DISPLAY_DPI
            ):
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    f"Display width and height must be in [1, {MAX_DISPLAY_DIMENSION}] "
                    f"and dpi in [1, {MAX_DISPLAY_DPI}].",
                )
            seen_ids.add(d.display)
            configs.append((d.width, d.height, d.dpi, d.flags, d.display))

        self.state.set_display_configurations_v1(configs)
        return self.getDisplayConfigurations(empty_pb2.Empty(), context)

    def getDisplayMode(
        self, request: empty_pb2.Empty, context: grpc.ServicerContext
    ) -> pb.DisplayMode:
        with self.state._lock:
            if not self.state.preset.resizable_config:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Resizable display mode is not enabled on this preset.",
                )
            return pb.DisplayMode(value=self.state.display_mode)

    def setDisplayMode(
        self, request: pb.DisplayMode, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        with self.state._lock:
            if not self.state.preset.resizable_config:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Resizable display mode is not enabled on this preset.",
                )
        self.state.set_display_mode(request.value)
        return empty_pb2.Empty()

    def getBrightness(
        self, request: pb.BrightnessValue, context: grpc.ServicerContext
    ) -> pb.BrightnessValue:
        with self.state._lock:
            val = self.state.brightness_targets.get(request.target, 80)
            return pb.BrightnessValue(target=request.target, value=val)

    def setBrightness(
        self, request: pb.BrightnessValue, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        if request.value < 0 or request.value > 100:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Brightness value must be in [0, 100].",
            )
        self.state.set_brightness(request.target, request.value)
        return empty_pb2.Empty()

    # -------------------------------------------------------------------------
    # Sensors, Physical Model & Posture
    # -------------------------------------------------------------------------

    def _read_sensor_v1(self, target: int) -> pb.SensorValue:
        with self.state._lock:
            imu = self.state.compute_imu_telemetry()
            if target == pb.SensorValue.ACCELERATION:
                vals = list(imu["accelerometer"])
            elif target == pb.SensorValue.GYROSCOPE:
                vals = list(imu["gyroscope"])
            elif target == pb.SensorValue.MAGNETIC_FIELD:
                vals = list(imu["magnetometer"])
            elif target == pb.SensorValue.ORIENTATION:
                vals = list(imu["orientation_degrees"])
            elif target == pb.SensorValue.TEMPERATURE:
                vals = [self.state.env_temperature_c]
            elif target == pb.SensorValue.PROXIMITY:
                vals = [self.state.env_proximity_cm]
            elif target == pb.SensorValue.LIGHT:
                vals = [self.state.env_light_lux]
            elif target == pb.SensorValue.PRESSURE:
                vals = [self.state.env_pressure_hpa]
            elif target == pb.SensorValue.HUMIDITY:
                vals = [self.state.env_humidity_pct]
            elif target == pb.SensorValue.MAGNETIC_FIELD_UNCALIBRATED:
                vals = list(imu["magnetometer_uncalibrated"])
            elif target == pb.SensorValue.GYROSCOPE_UNCALIBRATED:
                vals = list(imu["gyroscope_uncalibrated"])
            elif target == pb.SensorValue.HINGE_ANGLE0:
                vals = [
                    self.state.hinge_angles[0] if self.state.hinge_angles else 180.0
                ]
            elif target == pb.SensorValue.HINGE_ANGLE1:
                vals = [
                    (
                        self.state.hinge_angles[1]
                        if len(self.state.hinge_angles) > 1
                        else 180.0
                    )
                ]
            elif target == pb.SensorValue.HINGE_ANGLE2:
                vals = [
                    (
                        self.state.hinge_angles[2]
                        if len(self.state.hinge_angles) > 2
                        else 180.0
                    )
                ]
            elif target == pb.SensorValue.HEART_RATE:
                vals = [self.state.env_heart_rate_bpm]
            elif target == pb.SensorValue.RGBC_LIGHT:
                vals = list(self.state.env_rgbc_light)
            elif target == pb.SensorValue.WRIST_TILT:
                vals = [self.state.env_wrist_tilt]
            elif target == pb.SensorValue.ACCELERATION_UNCALIBRATED:
                vals = list(imu["accelerometer_uncalibrated"])
            else:
                vals = [0.0]
            return pb.SensorValue(
                target=target,
                status=pb.SensorValue.ENABLED,
                value=pb.ParameterValue(data=vals),
            )

    def getSensor(
        self, request: pb.SensorValue, context: grpc.ServicerContext
    ) -> pb.SensorValue:
        return self._read_sensor_v1(request.target)

    def setSensor(
        self, request: pb.SensorValue, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        data = list(request.value.data)
        target = request.target
        with self.state._lock:
            if target in (
                pb.SensorValue.ACCELERATION,
                pb.SensorValue.GYROSCOPE,
                pb.SensorValue.MAGNETIC_FIELD,
                pb.SensorValue.ORIENTATION,
                pb.SensorValue.MAGNETIC_FIELD_UNCALIBRATED,
                pb.SensorValue.GYROSCOPE_UNCALIBRATED,
                pb.SensorValue.ACCELERATION_UNCALIBRATED,
            ):
                vec = (
                    data[0] if len(data) > 0 else 0.0,
                    data[1] if len(data) > 1 else 0.0,
                    data[2] if len(data) > 2 else 0.0,
                )
                self.state.raw_sensor_overrides[target] = vec
            elif target == pb.SensorValue.TEMPERATURE and data:
                self.state.env_temperature_c = data[0]
            elif target == pb.SensorValue.PROXIMITY and data:
                self.state.env_proximity_cm = data[0]
            elif target == pb.SensorValue.LIGHT and data:
                self.state.env_light_lux = data[0]
            elif target == pb.SensorValue.PRESSURE and data:
                self.state.env_pressure_hpa = data[0]
            elif target == pb.SensorValue.HUMIDITY and data:
                self.state.env_humidity_pct = data[0]
            elif target == pb.SensorValue.HEART_RATE and data:
                self.state.env_heart_rate_bpm = data[0]
            elif target == pb.SensorValue.RGBC_LIGHT and len(data) >= 4:
                self.state.env_rgbc_light = (data[0], data[1], data[2], data[3])
            elif target == pb.SensorValue.WRIST_TILT and data:
                self.state.env_wrist_tilt = data[0]
            elif target == pb.SensorValue.HINGE_ANGLE0 and data:
                self.state.set_hinge_angle(0, data[0])
                return empty_pb2.Empty()
            elif target == pb.SensorValue.HINGE_ANGLE1 and data:
                self.state.set_hinge_angle(1, data[0])
                return empty_pb2.Empty()
            elif target == pb.SensorValue.HINGE_ANGLE2 and data:
                self.state.set_hinge_angle(2, data[0])
                return empty_pb2.Empty()

        self.state.notify_sensors_changed()
        return empty_pb2.Empty()

    def streamSensor(
        self, request: pb.SensorValue, context: grpc.ServicerContext
    ) -> Iterator[pb.SensorValue]:
        with self.state.subscribe("sensor") as q:
            yield self._read_sensor_v1(request.target)
            while context.is_active():
                try:
                    q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                yield self._read_sensor_v1(request.target)

    def _read_physical_model_v1(self, target: int) -> pb.PhysicalModelValue:
        with self.state._lock:
            if target == pb.PhysicalModelValue.POSITION:
                vals = list(self.state.pose_position_m)
            elif target == pb.PhysicalModelValue.ROTATION:
                vals = list(self.state.pose_rotation_deg)
            elif target == pb.PhysicalModelValue.MAGNETIC_FIELD:
                vals = list(self.state.env_magnetic_field_ut)
            elif target == pb.PhysicalModelValue.TEMPERATURE:
                vals = [self.state.env_temperature_c]
            elif target == pb.PhysicalModelValue.PROXIMITY:
                vals = [self.state.env_proximity_cm]
            elif target == pb.PhysicalModelValue.LIGHT:
                vals = [self.state.env_light_lux]
            elif target == pb.PhysicalModelValue.PRESSURE:
                vals = [self.state.env_pressure_hpa]
            elif target == pb.PhysicalModelValue.HUMIDITY:
                vals = [self.state.env_humidity_pct]
            elif target == pb.PhysicalModelValue.VELOCITY:
                vals = list(self.state.pose_velocity_mps)
            elif target == pb.PhysicalModelValue.AMBIENT_MOTION:
                vals = [self.state.pose_ambient_motion]
            elif target == pb.PhysicalModelValue.HINGE_ANGLE0:
                vals = [
                    self.state.hinge_angles[0] if self.state.hinge_angles else 180.0
                ]
            elif target == pb.PhysicalModelValue.HINGE_ANGLE1:
                vals = [
                    (
                        self.state.hinge_angles[1]
                        if len(self.state.hinge_angles) > 1
                        else 180.0
                    )
                ]
            elif target == pb.PhysicalModelValue.HINGE_ANGLE2:
                vals = [
                    (
                        self.state.hinge_angles[2]
                        if len(self.state.hinge_angles) > 2
                        else 180.0
                    )
                ]
            elif target == pb.PhysicalModelValue.ROLLABLE0:
                vals = [
                    (
                        self.state.rollable_percents[0]
                        if self.state.rollable_percents
                        else 0.0
                    )
                ]
            elif target == pb.PhysicalModelValue.HEART_RATE:
                vals = [self.state.env_heart_rate_bpm]
            elif target == pb.PhysicalModelValue.RGBC_LIGHT:
                vals = list(self.state.env_rgbc_light)
            elif target == pb.PhysicalModelValue.WRIST_TILT:
                vals = [self.state.env_wrist_tilt]
            else:
                vals = [0.0]
            return pb.PhysicalModelValue(
                target=target,
                status=pb.PhysicalModelValue.OK,
                value=pb.ParameterValue(data=vals),
            )

    def getPhysicalModel(
        self, request: pb.PhysicalModelValue, context: grpc.ServicerContext
    ) -> pb.PhysicalModelValue:
        return self._read_physical_model_v1(request.target)

    def setPhysicalModel(
        self, request: pb.PhysicalModelValue, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        data = list(request.value.data)
        target = request.target
        if target == pb.PhysicalModelValue.ROTATION and len(data) >= 3:
            self.state.set_pose_rotation_deg(data[0], data[1], data[2])
            return empty_pb2.Empty()
        if target == pb.PhysicalModelValue.HINGE_ANGLE0 and data:
            self.state.set_hinge_angle(0, data[0])
            return empty_pb2.Empty()

        with self.state._lock:
            if target == pb.PhysicalModelValue.POSITION and len(data) >= 3:
                self.state.pose_position_m = (data[0], data[1], data[2])
            elif target == pb.PhysicalModelValue.VELOCITY and len(data) >= 3:
                self.state.pose_velocity_mps = (data[0], data[1], data[2])
            elif target == pb.PhysicalModelValue.MAGNETIC_FIELD and len(data) >= 3:
                self.state.env_magnetic_field_ut = (data[0], data[1], data[2])
            elif target == pb.PhysicalModelValue.AMBIENT_MOTION and data:
                self.state.pose_ambient_motion = data[0]
            elif target == pb.PhysicalModelValue.TEMPERATURE and data:
                self.state.env_temperature_c = data[0]
            elif target == pb.PhysicalModelValue.PROXIMITY and data:
                self.state.env_proximity_cm = data[0]
            elif target == pb.PhysicalModelValue.LIGHT and data:
                self.state.env_light_lux = data[0]
            elif target == pb.PhysicalModelValue.PRESSURE and data:
                self.state.env_pressure_hpa = data[0]
            elif target == pb.PhysicalModelValue.HUMIDITY and data:
                self.state.env_humidity_pct = data[0]
            elif target == pb.PhysicalModelValue.HEART_RATE and data:
                self.state.env_heart_rate_bpm = data[0]
            elif target == pb.PhysicalModelValue.RGBC_LIGHT and len(data) >= 4:
                self.state.env_rgbc_light = (data[0], data[1], data[2], data[3])
            elif target == pb.PhysicalModelValue.WRIST_TILT and data:
                self.state.env_wrist_tilt = data[0]

        self.state.notify_sensors_changed()
        return empty_pb2.Empty()

    def streamPhysicalModel(
        self, request: pb.PhysicalModelValue, context: grpc.ServicerContext
    ) -> Iterator[pb.PhysicalModelValue]:
        with self.state.subscribe("sensor") as q:
            yield self._read_physical_model_v1(request.target)
            while context.is_active():
                try:
                    q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                yield self._read_physical_model_v1(request.target)

    def setPosture(
        self, request: pb.Posture, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        if not self.state.set_posture(request.value):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"Unsupported posture value: {request.value}",
            )
        return empty_pb2.Empty()

    # -------------------------------------------------------------------------
    # GPS & Battery
    # -------------------------------------------------------------------------

    def getGps(
        self, request: empty_pb2.Empty, context: grpc.ServicerContext
    ) -> pb.GpsState:
        with self.state._lock:
            return pb.GpsState(
                passiveUpdate=self.state.gps_passive_update,
                latitude=self.state.gps_latitude,
                longitude=self.state.gps_longitude,
                speed=self.state.gps_speed_mps,
                bearing=self.state.gps_bearing_deg,
                altitude=self.state.gps_altitude,
                satellites=self.state.gps_satellites,
            )

    def setGps(
        self, request: pb.GpsState, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        with self.state._lock:
            self.state.gps_passive_update = request.passiveUpdate
        self.state.update_location(
            latitude=request.latitude,
            longitude=request.longitude,
            altitude=request.altitude,
            speed_mps=request.speed,
            bearing_deg=request.bearing,
            satellites=request.satellites,
        )
        return empty_pb2.Empty()

    def getBattery(
        self, request: empty_pb2.Empty, context: grpc.ServicerContext
    ) -> pb.BatteryState:
        with self.state._lock:
            return pb.BatteryState(
                hasBattery=self.state.battery_has_battery,
                isPresent=self.state.battery_is_present,
                charger=self.state.battery_charger_v1,
                chargeLevel=self.state.battery_charge_level,
                health=self.state.battery_health_v1,
                status=self.state.battery_status_v1,
            )

    def setBattery(
        self, request: pb.BatteryState, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        with self.state._lock:
            self.state.battery_has_battery = request.hasBattery
        self.state.update_battery(
            charge_level=request.chargeLevel,
            charger_v1=request.charger,
            health_v1=request.health,
            status_v1=request.status,
            is_present=request.isPresent,
        )
        return empty_pb2.Empty()

    # -------------------------------------------------------------------------
    # Input Injection
    # -------------------------------------------------------------------------

    def sendKey(
        self, request: pb.KeyboardEvent, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        label = request.key or request.text or f"code:{request.keyCode}"
        self.state.record_key(label, keycode=request.keyCode, action=request.eventType)
        return empty_pb2.Empty()

    def sendTouch(
        self, request: pb.TouchEvent, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        w, h = self.state.get_effective_display_dims(request.display)
        for t in request.touches:
            x_norm = t.x / max(1, w)
            y_norm = t.y / max(1, h)
            self.state.record_touch(
                pointer_id=t.identifier,
                x_norm=x_norm,
                y_norm=y_norm,
                pressure=float(t.pressure),
                display_id=request.display,
                tool="touch",
            )
        return empty_pb2.Empty()

    def sendMouse(
        self, request: pb.MouseEvent, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        w, h = self.state.get_effective_display_dims(request.display)
        self.state.record_mouse(
            x_norm=request.x / max(1, w),
            y_norm=request.y / max(1, h),
            buttons=request.buttons,
            display_id=request.display,
        )
        return empty_pb2.Empty()

    def injectWheel(
        self, request: pb.WheelEvent, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        self.state.record_rotary(request.dy)
        return empty_pb2.Empty()

    def streamInputEvent(
        self, request_iterator: Iterator[pb.InputEvent], context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        for ev in request_iterator:
            which = ev.WhichOneof("type")
            if which == "key_event":
                self.sendKey(ev.key_event, context)
            elif which == "touch_event":
                self.sendTouch(ev.touch_event, context)
            elif which == "mouse_event":
                self.sendMouse(ev.mouse_event, context)
            elif which == "wheel_event":
                self.injectWheel(ev.wheel_event, context)
            elif which == "pen_event":
                w, h = self.state.get_effective_display_dims(ev.pen_event.display)
                for p_ev in ev.pen_event.events:
                    self.state.record_touch(
                        pointer_id=p_ev.identifier,
                        x_norm=p_ev.x / max(1, w),
                        y_norm=p_ev.y / max(1, h),
                        pressure=float(p_ev.pressure),
                        display_id=ev.pen_event.display,
                        tool="stylus",
                    )
        return empty_pb2.Empty()

    def sendFingerprint(
        self, request: pb.Fingerprint, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        with self.state._lock:
            self.state.last_fingerprint = (request.isTouching, request.touchId)
        return empty_pb2.Empty()

    # -------------------------------------------------------------------------
    # Clipboard (with Echo Suppression)
    # -------------------------------------------------------------------------

    def getClipboard(
        self, request: empty_pb2.Empty, context: grpc.ServicerContext
    ) -> pb.ClipData:
        with self.state._lock:
            return pb.ClipData(text=self.state.clipboard_text)

    def setClipboard(
        self, request: pb.ClipData, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        peer = context.peer() if hasattr(context, "peer") else ""
        self.state.set_clipboard(request.text, source_peer=peer)
        return empty_pb2.Empty()

    def streamClipboard(
        self, request: empty_pb2.Empty, context: grpc.ServicerContext
    ) -> Iterator[pb.ClipData]:
        my_peer = context.peer() if hasattr(context, "peer") else ""
        with self.state.subscribe("clipboard") as q:
            with self.state._lock:
                initial_text = self.state.clipboard_text
            yield pb.ClipData(text=initial_text)

            while context.is_active():
                try:
                    item = q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                if my_peer and item.get("source_peer") == my_peer:
                    continue
                yield pb.ClipData(text=item["text"])

    # -------------------------------------------------------------------------
    # Notifications & Logcat
    # -------------------------------------------------------------------------

    def _build_sticky_notifications(self) -> list[pb.Notification]:
        with self.state._lock:
            notes: list[pb.Notification] = [
                pb.Notification(
                    booted=pb.BootCompletedNotification(
                        time=self.state.boot_completed_time_ms
                    )
                ),
            ]
            if self.state.preset.is_foldable:
                notes.append(
                    pb.Notification(posture=pb.Posture(value=self.state.posture))
                )
            if self.state.preset.is_xr:
                notes.append(
                    pb.Notification(
                        xrOptions=pb.XrOptions(
                            environment=self.state.xr_environment,
                            passthrough_coefficient=self.state.xr_passthrough_coefficient,
                        )
                    )
                )
            return notes

    def streamNotification(
        self, request: empty_pb2.Empty, context: grpc.ServicerContext
    ) -> Iterator[pb.Notification]:
        with self.state.subscribe("notification_v1") as q:
            yield from self._build_sticky_notifications()

            while context.is_active():
                try:
                    ev = q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                ev_type = ev.get("type") or ev.get("event")
                if ev_type in ("posture", "hinge"):
                    with self.state._lock:
                        yield pb.Notification(
                            posture=pb.Posture(value=self.state.posture)
                        )
                elif ev_type == "displays_changed":
                    disp_cfgs = self.getDisplayConfigurations(
                        empty_pb2.Empty(), context
                    )
                    yield pb.Notification(
                        displayConfigurationsChangedNotification=pb.DisplayConfigurationsChangedNotification(
                            displayConfigurations=disp_cfgs
                        )
                    )
                elif ev_type == "brightness":
                    yield pb.Notification(
                        brightness=pb.BrightnessValue(
                            target=ev.get("target", 0), value=ev.get("value", 80)
                        )
                    )
                elif ev_type == "camera":
                    yield pb.Notification(
                        cameraNotification=pb.CameraNotification(
                            active=bool(ev.get("active", True)),
                            display=int(ev.get("display", 0)),
                        )
                    )
                elif ev_type == "focus":
                    yield pb.Notification(
                        textViewFocus=pb.TextViewFocus(
                            textViewHasFocus=bool(ev.get("focus", True))
                        )
                    )

    def getLogcat(
        self, request: pb.LogMessage, context: grpc.ServicerContext
    ) -> pb.LogMessage:
        with self.state._lock:
            start = max(0, request.start)
            entries = self.state.logcat_entries[start:]
            if request.sort == pb.LogMessage.Parsed:
                proto_entries = [
                    pb.LogcatEntry(
                        timestamp=e["timestamp"],
                        pid=e["pid"],
                        tid=e["tid"],
                        level=e["level"],
                        tag=e["tag"],
                        msg=e["msg"],
                    )
                    for e in entries
                ]
                return pb.LogMessage(
                    start=start,
                    next=len(self.state.logcat_entries),
                    sort=pb.LogMessage.Parsed,
                    entries=proto_entries,
                )
            lines = "\n".join(f"{e['tag']}: {e['msg']}" for e in entries)
            return pb.LogMessage(
                contents=lines,
                start=start,
                next=len(self.state.logcat_entries),
                sort=pb.LogMessage.Text,
            )

    def streamLogcat(
        self, request: pb.LogMessage, context: grpc.ServicerContext
    ) -> Iterator[pb.LogMessage]:
        with self.state.subscribe("logcat") as q:
            yield self.getLogcat(request, context)
            while context.is_active():
                try:
                    e = q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not context.is_active():
                    break
                if request.sort == pb.LogMessage.Parsed:
                    yield pb.LogMessage(
                        sort=pb.LogMessage.Parsed,
                        entries=[
                            pb.LogcatEntry(
                                timestamp=e["timestamp"],
                                pid=e["pid"],
                                tid=e["tid"],
                                level=e["level"],
                                tag=e["tag"],
                                msg=e["msg"],
                            )
                        ],
                    )
                else:
                    yield pb.LogMessage(
                        contents=f"{e['tag']}: {e['msg']}\n",
                        sort=pb.LogMessage.Text,
                    )

    # -------------------------------------------------------------------------
    # Audio & Microphone
    # -------------------------------------------------------------------------

    def streamAudio(
        self, request: pb.AudioFormat, context: grpc.ServicerContext
    ) -> Iterator[pb.AudioPacket]:
        if request.samplingRate < 0 or request.samplingRate > 192000:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Audio samplingRate must be in [0, 192000].",
            )
        rate = request.samplingRate if request.samplingRate > 0 else 44100
        channels = 2 if request.channels == pb.AudioFormat.Stereo else 1
        bps = 2 if request.format == pb.AudioFormat.AUD_FMT_S16 else 1
        chunk_samples = rate // 20  # 50ms packet
        pcm = b"\x00" * (chunk_samples * channels * bps)
        out_fmt = pb.AudioFormat(
            samplingRate=rate, channels=request.channels, format=request.format
        )
        yield pb.AudioPacket(
            format=out_fmt,
            timestamp=int(time.time() * 1_000_000),
            audio=pcm,
        )

    def injectAudio(
        self, request_iterator: Iterator[pb.AudioPacket], context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        total = 0
        for pkt in request_iterator:
            total += len(pkt.audio)
        with self.state._lock:
            self.state.injected_audio_bytes += total
        return empty_pb2.Empty()

    def getMicrophoneState(
        self, request: empty_pb2.Empty, context: grpc.ServicerContext
    ) -> pb.MicrophoneState:
        with self.state._lock:
            return pb.MicrophoneState(
                hasInput=self.state.mic_has_input,
                isPluggedIn=self.state.mic_is_plugged_in,
                isMuted=self.state.mic_is_muted,
            )

    def setMicrophoneState(
        self, request: pb.MicrophoneState, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        with self.state._lock:
            self.state.mic_has_input = request.hasInput
            self.state.mic_is_plugged_in = request.isPluggedIn
            self.state.mic_is_muted = request.isMuted
        return empty_pb2.Empty()

    # -------------------------------------------------------------------------
    # Telephony
    # -------------------------------------------------------------------------

    def sendPhone(
        self, request: pb.PhoneCall, context: grpc.ServicerContext
    ) -> pb.PhoneResponse:
        if not request.number or not request.number.strip():
            return pb.PhoneResponse(response=pb.PhoneResponse.BadNumber)
        # Map v1 Operation to v2 Action (1=RING, 2=DISCONNECT, 3=ACCEPT, 4=HOLD, 5=UNHOLD)
        op_map = {
            pb.PhoneCall.InitCall: 1,
            pb.PhoneCall.AcceptCall: 3,
            pb.PhoneCall.RejectCallExplicit: 2,
            pb.PhoneCall.RejectCallBusy: 2,
            pb.PhoneCall.DisconnectCall: 2,
            pb.PhoneCall.PlaceCallOnHold: 4,
            pb.PhoneCall.TakeCallOffHold: 5,
        }
        action = op_map.get(request.operation, 0)
        if action == 0:
            return pb.PhoneResponse(response=pb.PhoneResponse.BadOperation)
        res = self.state.transition_call_v2(request.number, action)
        if res is None:
            return pb.PhoneResponse(response=pb.PhoneResponse.ActionFailed)
        return pb.PhoneResponse(response=pb.PhoneResponse.OK)

    def sendSms(
        self, request: pb.SmsMessage, context: grpc.ServicerContext
    ) -> pb.PhoneResponse:
        if not request.srcAddress or not request.srcAddress.strip():
            return pb.PhoneResponse(response=pb.PhoneResponse.BadNumber)
        with self.state._lock:
            self.state.sms_messages.append((request.srcAddress, request.text))
        return pb.PhoneResponse(response=pb.PhoneResponse.OK)

    def setPhoneNumber(
        self, request: pb.PhoneNumber, context: grpc.ServicerContext
    ) -> pb.PhoneResponse:
        if not request.number or not request.number.strip():
            return pb.PhoneResponse(response=pb.PhoneResponse.BadNumber)
        with self.state._lock:
            self.state.phone_number = request.number
        return pb.PhoneResponse(response=pb.PhoneResponse.OK)

    # -------------------------------------------------------------------------
    # XR, Virtual Scene & Host Cameras
    # -------------------------------------------------------------------------

    def setXrOptions(
        self, request: pb.XrOptions, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        with self.state._lock:
            self.state.xr_environment = request.environment
            self.state.xr_passthrough_coefficient = request.passthrough_coefficient
        return empty_pb2.Empty()

    def getXrOptions(
        self, request: empty_pb2.Empty, context: grpc.ServicerContext
    ) -> pb.XrOptions:
        with self.state._lock:
            return pb.XrOptions(
                environment=self.state.xr_environment,
                passthrough_coefficient=self.state.xr_passthrough_coefficient,
            )

    def setEnvironment(
        self, request: pb.Environment, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        with self.state._lock:
            self.state.environment_map = dict(request.environment)
        return empty_pb2.Empty()

    def getEnvironment(
        self, request: empty_pb2.Empty, context: grpc.ServicerContext
    ) -> pb.Environment:
        with self.state._lock:
            env = getattr(self.state, "environment_map", {})
            return pb.Environment(environment=env)

    def rotateVirtualSceneCamera(
        self, request: pb.RotationRadian, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        with self.state._lock:
            self.state.virtual_scene_rotation_rad = (request.x, request.y, request.z)
        return empty_pb2.Empty()

    def setVirtualSceneCameraVelocity(
        self, request: pb.Velocity, context: grpc.ServicerContext
    ) -> empty_pb2.Empty:
        with self.state._lock:
            self.state.virtual_scene_velocity_mps = (request.x, request.y, request.z)
        return empty_pb2.Empty()

    def getHostCameras(
        self, request: empty_pb2.Empty, context: grpc.ServicerContext
    ) -> pb.CameraList:
        return pb.CameraList(
            cameras=[
                pb.Camera(
                    display_name="Clownfish Virtual Webcam 0",
                    id="webcam0",
                )
            ]
        )
