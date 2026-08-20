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

from unittest import TestCase
from unittest.mock import MagicMock, patch
import grpc

from aemu.discovery.emulator_client import EmulatorClient
from aemu.discovery.emulator_description import EmulatorDescription
from aemu.proto.emulator_controller_pb2_grpc import EmulatorControllerStub
from aemu.proto.snapshot_service_pb2_grpc import SnapshotServiceStub


class TestEmulatorClient(TestCase):
    def test_emulator_client_lazy_stubs(self):
        mock_channel = MagicMock(spec=grpc.Channel)
        client = EmulatorClient(mock_channel)

        assert client.channel == mock_channel
        controller_stub = client.controller
        assert isinstance(controller_stub, EmulatorControllerStub)
        # Lazy property returns the cached stub instance on subsequent access
        assert client.controller is controller_stub

        snapshot_stub = client.snapshot
        assert isinstance(snapshot_stub, SnapshotServiceStub)
        assert client.snapshot is snapshot_stub

    def test_emulator_client_context_manager(self):
        mock_channel = MagicMock(spec=grpc.Channel)
        with EmulatorClient(mock_channel) as client:
            assert isinstance(client.controller, EmulatorControllerStub)
            assert not mock_channel.close.called

        mock_channel.close.assert_called_once()

    @patch("grpc.insecure_channel")
    def test_emulator_description_client(self, mock_insecure_channel):
        mock_channel = MagicMock(spec=grpc.Channel)
        mock_insecure_channel.return_value = mock_channel

        desc = EmulatorDescription("12345", {"grpc.port": "8554"})
        with desc.client() as client:
            assert isinstance(client, EmulatorClient)
            assert client.channel == mock_channel

        mock_channel.close.assert_called_once()

    def test_all_service_stubs_lazy_load(self):
        mock_channel = MagicMock(spec=grpc.Channel)
        client = EmulatorClient(mock_channel)

        stubs = [
            client.controller,
            client.snapshot,
            client.ui_controller,
            client.screen_recording,
            client.sensor,
            client.modem,
            client.rtc,
            client.rtc_v2,
            client.avd,
            client.car,
            client.bluetooth,
            client.virtual_scene,
            client.waterfall,
        ]
        for stub in stubs:
            assert stub is not None

    def test_async_emulator_client_context_manager(self):
        import asyncio
        from aemu.discovery.emulator_client import AsyncEmulatorClient

        mock_channel = MagicMock(spec=grpc.aio.Channel)

        async def run_async_test():
            close_future = asyncio.Future()
            close_future.set_result(None)
            mock_channel.close.return_value = close_future
            async with AsyncEmulatorClient(mock_channel) as client:
                assert client.controller is not None
                assert not mock_channel.close.called
            mock_channel.close.assert_called_once()

        asyncio.run(run_async_test())

    @patch("grpc.aio.insecure_channel")
    def test_emulator_description_async_client(self, mock_insecure_channel):
        import asyncio

        mock_channel = MagicMock(spec=grpc.aio.Channel)
        mock_insecure_channel.return_value = mock_channel

        desc = EmulatorDescription("12345", {"grpc.port": "8554"})

        async def run_async_test():
            close_future = asyncio.Future()
            close_future.set_result(None)
            mock_channel.close.return_value = close_future
            async with desc.async_client() as client:
                assert client.channel == mock_channel
            mock_channel.close.assert_called_once()

        asyncio.run(run_async_test())
