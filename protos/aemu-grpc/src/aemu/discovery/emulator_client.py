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
"""High-level managed client sessions for interacting with Android Emulator gRPC services."""

from typing import Any, Generic, TypeVar, cast
import grpc

from aemu.proto.avd_service_pb2_grpc import AvdServiceStub
from aemu.proto.car_service_pb2_grpc import CarServiceStub
from aemu.proto.emulated_bluetooth_pb2_grpc import EmulatedBluetoothServiceStub
from aemu.proto.emulator_controller_pb2_grpc import EmulatorControllerStub
from aemu.proto.modem_service_pb2_grpc import ModemStub
from aemu.proto.rtc_service_pb2_grpc import RtcStub
from aemu.proto.rtc_service_v2_pb2_grpc import RtcStub as RtcV2Stub
from aemu.proto.screen_recording_service_pb2_grpc import ScreenRecordingStub
from aemu.proto.sensor_service_pb2_grpc import SensorServiceStub
from aemu.proto.snapshot_service_pb2_grpc import SnapshotServiceStub
from aemu.proto.ui_controller_service_pb2_grpc import UiControllerStub
from aemu.proto.virtual_scene_service_pb2_grpc import VirtualSceneServiceStub
from aemu.proto.waterfall_pb2_grpc import WaterfallStub

ChannelType = TypeVar("ChannelType", grpc.Channel, grpc.aio.Channel)


class _BaseEmulatorClient(Generic[ChannelType]):
    """Base managed client session wrapping a gRPC channel and lazily creating stubs."""

    def __init__(self, channel: ChannelType):
        self._channel = channel
        self._stubs: dict[str, Any] = {}

    def _get_stub(self, stub_cls: Any) -> Any:
        key = stub_cls.__name__
        if key not in self._stubs:
            self._stubs[key] = stub_cls(self._channel)
        return self._stubs[key]

    @property
    def channel(self) -> ChannelType:
        """The underlying gRPC channel."""
        return self._channel

    @property
    def controller(self) -> EmulatorControllerStub:
        """Stub for EmulatorController service (controlling VM, mouse, keyboard, display)."""
        return self._get_stub(EmulatorControllerStub)

    @property
    def snapshot(self) -> SnapshotServiceStub:
        """Stub for SnapshotService (push, pull, save, load snapshots)."""
        return self._get_stub(SnapshotServiceStub)

    @property
    def ui_controller(self) -> UiControllerStub:
        """Stub for UiControllerService."""
        return self._get_stub(UiControllerStub)

    @property
    def screen_recording(self) -> ScreenRecordingStub:
        """Stub for ScreenRecordingService."""
        return self._get_stub(ScreenRecordingStub)

    @property
    def sensor(self) -> SensorServiceStub:
        """Stub for SensorService (GPS, accelerometer, gyroscope, battery)."""
        return self._get_stub(SensorServiceStub)

    @property
    def modem(self) -> ModemStub:
        """Stub for ModemService (SMS, call state, signal strength)."""
        return self._get_stub(ModemStub)

    @property
    def rtc(self) -> RtcStub:
        """Stub for WebRTC RTCService."""
        return self._get_stub(RtcStub)

    @property
    def rtc_v2(self) -> RtcV2Stub:
        """Stub for WebRTC RtcV2Service."""
        return self._get_stub(RtcV2Stub)

    @property
    def avd(self) -> AvdServiceStub:
        """Stub for AvdService."""
        return self._get_stub(AvdServiceStub)

    @property
    def car(self) -> CarServiceStub:
        """Stub for CarService (rotary encoder, sensors)."""
        return self._get_stub(CarServiceStub)

    @property
    def bluetooth(self) -> EmulatedBluetoothServiceStub:
        """Stub for EmulatedBluetoothService."""
        return self._get_stub(EmulatedBluetoothServiceStub)

    @property
    def virtual_scene(self) -> VirtualSceneServiceStub:
        """Stub for VirtualSceneService."""
        return self._get_stub(VirtualSceneServiceStub)

    @property
    def waterfall(self) -> WaterfallStub:
        """Stub for Waterfall service."""
        return self._get_stub(WaterfallStub)

    def close(self) -> None:
        """Closes the underlying gRPC channel."""
        if hasattr(self._channel, "close"):
            self._channel.close()


class EmulatorClient(_BaseEmulatorClient[grpc.Channel]):
    """Synchronous managed client session for interacting with an emulator over gRPC.

    Supports use as a Python context manager:
        with emu.client() as client:
            client.controller.setVmState(...)
    """

    def __enter__(self) -> "EmulatorClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def wait_for_ready(self, timeout: float = 10.0) -> bool:
        """Waits for the emulator gRPC endpoint to become ready to handle RPCs.

        Repeatedly polls the controller getStatus RPC until it succeeds or the
        specified timeout expires.

        Args:
            timeout (float): Maximum duration in seconds to wait. Defaults to 10.0 seconds.

        Returns:
            bool: True if the gRPC endpoint became ready within the timeout, False otherwise.
        """
        import time
        from google.protobuf import empty_pb2

        start_time = time.monotonic()
        delay = 0.05
        while time.monotonic() - start_time < timeout:
            try:
                remaining = max(0.1, timeout - (time.monotonic() - start_time))
                call_timeout = min(1.0, remaining)
                self.controller.getStatus(empty_pb2.Empty(), timeout=call_timeout)
                return True
            except grpc.RpcError:
                time.sleep(delay)
                delay = min(0.5, delay * 2)
        return False


class AsyncEmulatorClient(_BaseEmulatorClient[grpc.aio.Channel]):
    """Asynchronous managed client session for interacting with an emulator over gRPC.

    Supports use as an async context manager:
        async with emu.async_client() as client:
            await client.controller.setVmState(...)
    """

    async def __aenter__(self) -> "AsyncEmulatorClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Asynchronously closes the underlying gRPC channel."""
        if hasattr(self._channel, "close"):
            res = self._channel.close()
            if hasattr(res, "__await__"):
                await res

    def close(self) -> None:
        """Synchronously closes the async channel without leaving an unawaited coroutine."""
        import asyncio

        if hasattr(self._channel, "close"):
            res = self._channel.close()
            if hasattr(res, "__await__"):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(res)
                except RuntimeError:
                    asyncio.run(res)

    async def wait_for_ready(self, timeout: float = 10.0) -> bool:
        """Asynchronously waits for the emulator gRPC endpoint to become ready to handle RPCs.

        Repeatedly polls the controller getStatus RPC until it succeeds or the
        specified timeout expires.

        Args:
            timeout (float): Maximum duration in seconds to wait. Defaults to 10.0 seconds.

        Returns:
            bool: True if the gRPC endpoint became ready within the timeout, False otherwise.
        """
        import asyncio
        import time
        from google.protobuf import empty_pb2

        start_time = time.monotonic()
        delay = 0.05
        while time.monotonic() - start_time < timeout:
            try:
                remaining = max(0.1, timeout - (time.monotonic() - start_time))
                call_timeout = min(1.0, remaining)
                await self.controller.getStatus(empty_pb2.Empty(), timeout=call_timeout)
                return True
            except grpc.RpcError:
                await asyncio.sleep(delay)
                delay = min(0.5, delay * 2)
        return False
