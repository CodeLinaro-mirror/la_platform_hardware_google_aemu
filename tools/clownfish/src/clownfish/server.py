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

"""Clownfish gRPC server orchestrating v1, incubating, and v2 emulator services."""

from __future__ import annotations

import logging
import tempfile
import threading
from concurrent import futures
from pathlib import Path

import audio_service_pb2_grpc
import battery_service_pb2_grpc
import camera_service_pb2_grpc
import capabilities_service_pb2_grpc
import display_service_pb2_grpc
import grpc
import hypervisor_service_pb2_grpc
import input_service_pb2_grpc
import location_service_pb2_grpc
import sensors_service_pb2_grpc
import system_service_pb2_grpc
import telephony_service_pb2_grpc
import webrtc_service_pb2_grpc
from aemu.proto import (
    emulator_controller_pb2_grpc,
    screen_recording_service_pb2_grpc,
    snapshot_service_pb2_grpc,
)
from google.longrunning import operations_pb2_grpc

from clownfish.companions import CompanionManager
from clownfish.discovery import DiscoveryAdvertiser
from clownfish.presets import DevicePreset
from clownfish.renderer import SyntheticFrameRenderer
from clownfish.services.v1.emulator_controller import EmulatorControllerServicer
from clownfish.services.v1.screen_recording import ScreenRecordingServicer
from clownfish.services.v1.snapshot_service import SnapshotServiceServicer
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
from clownfish.state import EmulatorState


class FaultInjectionInterceptor(grpc.ServerInterceptor):
    """gRPC server interceptor for injecting synthetic status errors in UI tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rules: dict[str, tuple[grpc.StatusCode, str]] = {}

    def inject_error(
        self,
        method_substr: str,
        status_code: grpc.StatusCode = grpc.StatusCode.UNAVAILABLE,
        details: str = "Injected Clownfish fault",
    ) -> None:
        """Registers an error rule matching any RPC method containing `method_substr`."""
        with self._lock:
            self._rules[method_substr] = (status_code, details)

    def remove_error(self, method_substr: str) -> None:
        """Removes a specific error rule."""
        with self._lock:
            self._rules.pop(method_substr, None)

    def clear_errors(self) -> None:
        """Clears all injected error rules."""
        with self._lock:
            self._rules.clear()

    def _match_rule(self, method_name: str) -> tuple[grpc.StatusCode, str] | None:
        with self._lock:
            for substr, rule in self._rules.items():
                if substr in method_name:
                    return rule
        return None

    def intercept_service(self, continuation, handler_call_details):
        handler = continuation(handler_call_details)
        if handler is None:
            return None

        rule = self._match_rule(handler_call_details.method)
        if rule is None:
            return handler

        status_code, details = rule

        def abort_unary(request, context):
            context.abort(status_code, details)

        def abort_stream(request, context):
            context.abort(status_code, details)
            yield  # pragma: no cover

        if not handler.request_streaming and not handler.response_streaming:
            return grpc.unary_unary_rpc_method_handler(
                abort_unary,
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )
        if not handler.request_streaming and handler.response_streaming:
            return grpc.unary_stream_rpc_method_handler(
                abort_stream,
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )
        if handler.request_streaming and not handler.response_streaming:
            return grpc.stream_unary_rpc_method_handler(
                abort_unary,
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )
        return grpc.stream_stream_rpc_method_handler(
            abort_stream,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


class ClownfishServer:
    """In-process or standalone fake Android Emulator gRPC server."""

    def __init__(
        self,
        preset: str | DevicePreset = "phone",
        port: int = 0,
        serial: str | int = 5554,
        avd_name: str | None = None,
        enable_discovery: bool = False,
        discovery_dir: str | Path | None = None,
        fps: float = 15.0,
        max_workers: int = 16,
        enable_webrtc: bool = False,
        videobridge_port: int = 0,
        webrtc_max_dim: int = 720,
        videobridge_bin: str | Path | None = None,
        enable_grpc_web: bool = False,
        grpc_web_port: int = 8888,
        grpc_web_address: str = "127.0.0.1",
        grpc_web_bin: str | Path | None = None,
        enable_aquarium: bool = False,
        aquarium_port: int = 8080,
        aquarium_address: str = "0.0.0.0",
        aquarium_bin: str | Path | None = None,
        aquarium_static_dir: str | Path | None = None,
    ) -> None:
        serial_port = int(str(serial).replace("emulator-", ""))
        self.state = EmulatorState(
            preset=preset,
            avd_name=avd_name,
            adb_port=serial_port,
        )
        self.fault_injector = FaultInjectionInterceptor()
        self.renderer = SyntheticFrameRenderer(self.state, fps=fps)
        self.companions = CompanionManager()
        self._port = port
        self._bound_port: int | None = None
        self._serial_port = serial_port
        self._enable_discovery = enable_discovery
        self._discovery_dir = discovery_dir
        self._temp_discovery_dir = None
        self._max_workers = max_workers
        self._enable_webrtc = enable_webrtc
        self._videobridge_port = videobridge_port
        self._webrtc_max_dimension = webrtc_max_dim
        self._videobridge_bin = videobridge_bin
        self._enable_grpc_web = enable_grpc_web
        self._grpc_web_port = grpc_web_port
        self._grpc_web_address = grpc_web_address
        self._grpc_web_bin = grpc_web_bin
        self._enable_aquarium = enable_aquarium
        self._aquarium_port = aquarium_port
        self._aquarium_address = aquarium_address
        self._aquarium_bin = aquarium_bin
        self._aquarium_static_dir = aquarium_static_dir
        self._server: grpc.Server | None = None
        self._advertiser: DiscoveryAdvertiser | None = None

        # Instantiate v1 & incubating servicers
        self.emulator_controller = EmulatorControllerServicer(self.state, self.renderer)
        self.snapshot_service = SnapshotServiceServicer(self.state, self.renderer)
        self.screen_recording = ScreenRecordingServicer(self.state)

        # Instantiate v2 servicers
        self.capabilities_service = CapabilitiesServiceServicer(self.state)
        self.display_service = DisplayServiceServicer(self.state, self.renderer)
        self.input_service = InputServiceServicer(self.state)
        self.sensors_service = SensorsServiceServicer(self.state)
        self.location_service = LocationServiceServicer(self.state)
        self.battery_service = BatteryServiceServicer(self.state)
        self.system_service = SystemServiceServicer(self.state)
        self.telephony_service = TelephonyServiceServicer(self.state)
        self.camera_service = CameraServiceServicer(self.state)
        self.audio_service = AudioServiceServicer(self.state)
        self.hypervisor_service = HypervisorServiceServicer(self.state, self.renderer)
        self.operations_service = OperationsServicer(self.state)
        self.rtc_service = RtcServiceServicer(self.state)

    @property
    def port(self) -> int:
        if self._bound_port is None:
            raise RuntimeError("ClownfishServer has not been started yet")
        return self._bound_port

    @property
    def address(self) -> str:
        return f"localhost:{self.port}"

    @property
    def videobridge_address(self) -> str | None:
        return self.companions.videobridge_address

    @property
    def grpc_web_url(self) -> str | None:
        return self.companions.grpc_web_url

    @property
    def aquarium_url(self) -> str | None:
        return self.companions.aquarium_url

    @property
    def discovery_file(self) -> Path | None:
        if self._advertiser is not None:
            return self._advertiser.ini_path
        return None

    def inject_error(
        self,
        method_substr: str,
        status_code: grpc.StatusCode = grpc.StatusCode.UNAVAILABLE,
        details: str = "Injected Clownfish fault",
    ) -> None:
        """Injects a gRPC error for any RPC matching `method_substr`."""
        self.fault_injector.inject_error(method_substr, status_code, details)

    def clear_errors(self) -> None:
        """Clears all injected gRPC errors."""
        self.fault_injector.clear_errors()

    def start(self) -> ClownfishServer:
        """Starts the gRPC server, frame animation loop, discovery file, and companions."""
        if self._server is not None:
            return self

        _max_msg_bytes = 64 * 1024 * 1024
        self._server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=self._max_workers),
            interceptors=[self.fault_injector],
            options=[
                ("grpc.max_send_message_length", _max_msg_bytes),
                ("grpc.max_receive_message_length", _max_msg_bytes),
                ("grpc.so_reuseport", 0),
            ],
        )

        # Register v1 & incubating services
        emulator_controller_pb2_grpc.add_EmulatorControllerServicer_to_server(
            self.emulator_controller, self._server
        )
        snapshot_service_pb2_grpc.add_SnapshotServiceServicer_to_server(
            self.snapshot_service, self._server
        )
        screen_recording_service_pb2_grpc.add_ScreenRecordingServicer_to_server(
            self.screen_recording, self._server
        )

        # Register v2 services
        capabilities_service_pb2_grpc.add_CapabilitiesServiceServicer_to_server(
            self.capabilities_service, self._server
        )
        display_service_pb2_grpc.add_DisplayServiceServicer_to_server(
            self.display_service, self._server
        )
        input_service_pb2_grpc.add_InputServiceServicer_to_server(
            self.input_service, self._server
        )
        sensors_service_pb2_grpc.add_SensorsServiceServicer_to_server(
            self.sensors_service, self._server
        )
        location_service_pb2_grpc.add_LocationServiceServicer_to_server(
            self.location_service, self._server
        )
        battery_service_pb2_grpc.add_BatteryServiceServicer_to_server(
            self.battery_service, self._server
        )
        system_service_pb2_grpc.add_SystemServiceServicer_to_server(
            self.system_service, self._server
        )
        telephony_service_pb2_grpc.add_TelephonyServiceServicer_to_server(
            self.telephony_service, self._server
        )
        camera_service_pb2_grpc.add_CameraServiceServicer_to_server(
            self.camera_service, self._server
        )
        audio_service_pb2_grpc.add_AudioServiceServicer_to_server(
            self.audio_service, self._server
        )
        hypervisor_service_pb2_grpc.add_HypervisorServiceServicer_to_server(
            self.hypervisor_service, self._server
        )
        operations_pb2_grpc.add_OperationsServicer_to_server(
            self.operations_service, self._server
        )
        webrtc_service_pb2_grpc.add_RtcServiceServicer_to_server(
            self.rtc_service, self._server
        )

        try:
            self._bound_port = self._server.add_insecure_port(f"[::1]:{self._port}")
        except RuntimeError:
            self._bound_port = self._server.add_insecure_port(f"127.0.0.1:{self._port}")
        self.renderer.start()
        self._server.start()

        if self._enable_discovery or self._enable_webrtc:
            disc_dir = self._discovery_dir
            if not self._enable_discovery and disc_dir is None:
                self._temp_discovery_dir = tempfile.TemporaryDirectory(
                    prefix="clownfish_disc_"
                )
                disc_dir = self._temp_discovery_dir.name
            self._advertiser = DiscoveryAdvertiser(
                state=self.state,
                grpc_port=self._bound_port,
                serial_port=self._serial_port,
                discovery_dir=disc_dir,
            )
            self._advertiser.write()

        try:
            if self._enable_webrtc and self._advertiser and self._advertiser.ini_path:
                vb_addr = self.companions.start_videobridge(
                    discovery_file=self._advertiser.ini_path,
                    max_dimension=self._webrtc_max_dimension,
                    binary_path=self._videobridge_bin,
                    port=self._videobridge_port,
                )
                self.rtc_service.set_videobridge_address(vb_addr)

            if self._enable_grpc_web:
                self.companions.start_grpc_web_proxy(
                    grpc_target=self.address,
                    http_address=self._grpc_web_address,
                    http_port=self._grpc_web_port,
                    binary_path=self._grpc_web_bin,
                )

            if self._enable_aquarium:
                self.companions.start_aquarium_ui(
                    grpc_target=self.address,
                    http_address=self._aquarium_address,
                    http_port=self._aquarium_port,
                    binary_path=self._aquarium_bin,
                    static_dir=self._aquarium_static_dir,
                )
        except Exception as err:
            logging.getLogger(__name__).error(
                "Failed to initialize Clownfish companion processes for %s: %s",
                self.address,
                err,
            )
            self.stop()
            raise

        return self

    def stop(self, grace: float | None = 0.0) -> None:
        """Stops companion processes, discovery advertiser, frame renderer, and gRPC server."""
        self.rtc_service.set_videobridge_address(None)
        self.companions.stop_all()

        if self._advertiser is not None:
            self._advertiser.remove()
            self._advertiser = None

        if self._temp_discovery_dir is not None:
            self._temp_discovery_dir.cleanup()
            self._temp_discovery_dir = None

        self.renderer.stop()

        if self._server is not None:
            self._server.stop(grace).wait(timeout=5.0)
            self._server = None

    def wait_for_termination(self, timeout: float | None = None) -> bool:
        if self._server is None:
            return True
        return self._server.wait_for_termination(timeout=timeout)

    def __enter__(self) -> ClownfishServer:  # noqa: PYI034
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
