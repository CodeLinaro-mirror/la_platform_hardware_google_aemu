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

from pathlib import Path
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from aemu.discovery.emulator_description import (
    EmulatorDescription,
    EmulatorSecurity,
)
from aemu.discovery.emulator_discovery import (
    EmulatorDiscovery,
    EmulatorNotFound,
    get_default_emulator,
)


class TestExtendedCoverage(TestCase):
    def test_discovery_connection_static_helper(self):
        desc = EmulatorDiscovery.connection("localhost:1234", token="my-token")
        assert desc.get("grpc.address") == "localhost:1234"
        assert desc.get("grpc.token") == "my-token"

    def test_discovery_first_raises_not_found(self):
        with patch.object(EmulatorDiscovery, "_discover_running", return_value=set()):
            with self.assertRaises(EmulatorNotFound):
                get_default_emulator()

    def test_discovery_cleanup_stale_and_readonly(self, tmp_path=None):
        import tempfile
        tmp_dir = Path(tempfile.gettempdir()) / "test_aemu_grpc_cov"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        pid_file = tmp_dir / "pid_99999999.ini"
        with open(pid_file, "w") as f:
            f.write("grpc.port=8554\n")

        with patch(
            "aemu.discovery.emulator_discovery.get_discovery_directories",
            return_value=[tmp_dir],
        ):
            disc = EmulatorDiscovery()
            # Non-destructive read should NOT delete the dead ini file
            res_readonly = disc._discover_running(cleanup=False)
            assert len(res_readonly) == 0
            assert pid_file.exists()

            # cleanup_stale should erase the dead ini file
            remaining = disc.cleanup_stale()
            assert remaining == 0
            assert not pid_file.exists()

    def test_find_by_pid_not_found(self):
        with patch.object(EmulatorDiscovery, "_discover_running", return_value=set()):
            disc = EmulatorDiscovery()
            assert disc.find_by_pid(999999) is None

    def test_select_security_tink_fallback_to_token(self):
        desc = EmulatorDescription(
            "123",
            {
                "grpc.jwks": "/path/to/jwks",
                "grpc.jwk_active": "/path/to/active",
                "grpc.token": "fallback-token",
            },
        )
        with patch("aemu.discovery.emulator_description._HAVE_TINK", False):
            sec = desc._select_security({})
            assert sec == EmulatorSecurity.Token

        with patch("aemu.discovery.emulator_description._HAVE_TINK", True):
            sec = desc._select_security({})
            assert sec == EmulatorSecurity.Jwt

    def test_select_security_explicit_override(self):
        desc = EmulatorDescription("123", {"grpc.token": "my-token"})
        assert desc._select_security({"emulator.security": "nothing"}) == EmulatorSecurity.Nothing
        assert desc._select_security({"emulator.security": "jwt"}) == EmulatorSecurity.Jwt

    def test_emulator_name_fallback_without_serial(self):
        desc_with_serial = EmulatorDescription("100", {"port.serial": "5554"})
        assert desc_with_serial.name() == "emulator-5554"

        desc_without_serial = EmulatorDescription("200", {})
        assert desc_without_serial.name() == "emulator-pid-200"

    def test_channel_arguments_non_mutation(self):
        desc = EmulatorDescription("100", {"grpc.port": "8554"})
        custom_args = [("grpc.keepalive_time_ms", 10000)]
        with patch("grpc.insecure_channel") as mock_channel:
            desc.get_grpc_channel(custom_args)
            _, kwargs = mock_channel.call_args
            opts = kwargs["options"]
            assert len(opts) == 3  # keepalive + max_send + max_receive
            assert len(custom_args) == 1  # Original custom_args list was NOT mutated

    def test_shutdown_when_process_is_none(self):
        desc = EmulatorDescription("99999999", {})
        with patch.object(desc, "process", return_value=None):
            assert desc.shutdown() is True

    def test_str_and_hash_representation(self):
        desc1 = EmulatorDescription("100", {"port.serial": "5554"})
        desc2 = EmulatorDescription("100", {"port.serial": "5554"})
        assert hash(desc1) == hash(desc2)
        assert desc1 == desc2

        with patch.object(desc1, "is_alive", return_value=True):
            assert "🚀" in str(desc1)

        with patch.object(desc1, "is_alive", return_value=False):
            assert "🪦" in str(desc1)

    def test_explicit_grpc_address_precedence(self):
        desc = EmulatorDescription(
            "100", {"grpc.address": "unix:///path/to/socket", "grpc.port": "9999"}
        )
        with patch("grpc.insecure_channel") as mock_channel:
            desc.get_grpc_channel()
            args, _ = mock_channel.call_args
            assert args[0] == "unix:///path/to/socket"

    def test_client_wait_for_ready(self):
        import grpc
        from aemu.discovery.emulator_client import EmulatorClient

        mock_channel = MagicMock(spec=grpc.Channel)
        client = EmulatorClient(mock_channel)

        # Test success on first attempt
        mock_stub = MagicMock()
        mock_stub.getStatus.return_value = MagicMock()
        client._stubs["EmulatorControllerStub"] = mock_stub
        assert client.wait_for_ready(timeout=1.0) is True

        # Test failure until timeout
        mock_stub.getStatus.side_effect = grpc.RpcError("Unavailable")
        assert client.wait_for_ready(timeout=0.15) is False

    def test_async_client_wait_for_ready(self):
        import asyncio
        import grpc
        from aemu.discovery.emulator_client import AsyncEmulatorClient

        mock_channel = MagicMock(spec=grpc.aio.Channel)
        client = AsyncEmulatorClient(mock_channel)

        async def run_test():
            mock_stub = MagicMock()
            future = asyncio.Future()
            future.set_result(MagicMock())
            mock_stub.getStatus.return_value = future
            client._stubs["EmulatorControllerStub"] = mock_stub
            assert await client.wait_for_ready(timeout=1.0) is True

            err_future = asyncio.Future()
            err_future.set_exception(grpc.RpcError("Unavailable"))
            mock_stub.getStatus.return_value = err_future
            assert await client.wait_for_ready(timeout=0.15) is False

        asyncio.run(run_test())

    def test_ashutdown(self):
        import asyncio

        desc = EmulatorDescription("99999999", {})
        async def run_test():
            with patch.object(desc, "process", return_value=None):
                assert await desc.ashutdown() is True

            # Test with process that shuts down via gRPC
            mock_proc = MagicMock()
            mock_proc.pid = 99999999
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            with patch.object(desc, "process", return_value=mock_proc), \
                 patch.object(desc, "async_client", return_value=mock_client), \
                 patch("psutil.pid_exists", side_effect=[True, False]), \
                 patch.object(desc, "is_alive", return_value=False):
                assert await desc.ashutdown(timeout=1.0) is True
                mock_client.controller.setVmState.assert_called_once()

        asyncio.run(run_test())

    def test_safe_kill_zombie_cross_platform(self):
        import psutil
        from aemu.discovery.emulator_description import _safe_kill

        mock_proc = MagicMock(spec=psutil.Process)
        mock_proc.pid = 99999
        mock_proc.status.return_value = psutil.STATUS_ZOMBIE
        mock_proc.ppid.return_value = 1

        # Test POSIX non-blocking zombie check
        with patch("os.name", "posix"), patch("psutil.pid_exists", return_value=False):
            assert _safe_kill(mock_proc) is True

        # Test Windows (nt) non-blocking zombie check
        with patch("os.name", "nt"), patch("psutil.pid_exists", return_value=False):
            assert _safe_kill(mock_proc) is True
