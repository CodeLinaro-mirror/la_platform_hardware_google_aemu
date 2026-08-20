# -*- coding: utf-8 -*-
#  Copyright (C) 2020 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import logging
from enum import Enum
from typing import Any, Optional, Sequence
import grpc
import psutil
from aemu.discovery.header_manipulator_client_interceptor import (
    header_adder_interceptor,
)

try:
    # This will fail if you do not have tink..
    from aemu.discovery.security.jwt_header_interceptor import jwt_header_interceptor

    _HAVE_TINK = True
except ImportError:
    _HAVE_TINK = False


from aemu.discovery.emulator_client import AsyncEmulatorClient, EmulatorClient
from aemu.proto.emulator_controller_pb2 import VmRunState
from aemu.proto.emulator_controller_pb2_grpc import EmulatorControllerStub


_LOGGER = logging.getLogger("aemu-grpc")


def _safe_kill(process: psutil.Process) -> bool:
    """Tries to kill the given process

    Args:
        process (psutil.Process): The process to be terminated

    Returns:
        bool: True if the process is no longer alive.
    """
    status = psutil.STATUS_DEAD
    try:
        status = process.status()
        process.name()  # Retrieve name if possible.
    except Exception as e:
        # Process has left, or we might have been unable to get additional info.
        _LOGGER.debug("Unable to get status, process dead? %s", e)

    if status == psutil.STATUS_ZOMBIE:
        import os

        if os.name == "posix":
            try:
                if process.ppid() == os.getpid():
                    os.waitpid(process.pid, os.WNOHANG)
            except Exception as err:
                _LOGGER.debug(
                    "Failed to non-blocking reap zombie process %s: %s", process, err
                )

        if not psutil.pid_exists(process.pid):
            _LOGGER.info("Process %s has been reaped.", process)
        return True

    # Step 1, be nice.
    try:
        _LOGGER.debug("Terminate %s", process)
        process.terminate()
    except Exception as e:
        # Process might be gone, or we could not send terminate.
        _LOGGER.debug("Failed to terminate %s due to %s", process, e)

    try:
        process.wait(timeout=3)
    except psutil.TimeoutExpired:
        _LOGGER.debug("Force kill %s", process)
        process.kill()

    if not psutil.pid_exists(process.pid):
        _LOGGER.info("Successfully terminated: %s", process)

    return not psutil.pid_exists(process.pid)


def _kill_process_tree(process: psutil.Process) -> None:
    """
    Kills the process tree rooted at the given process.

    Args:
        process: The root process of the process tree to kill.
    """
    children = process.children()
    for child in children:
        _LOGGER.debug("Found child %s, terminating..", child)
        _kill_process_tree(child)

    _safe_kill(process)


class EmulatorSecurity(Enum):
    Nothing = 1
    Jwt = 2
    Token = 3


class BasicEmulatorDescription(dict):
    """A description of an emulator you can connect to using gRPC."""

    # Unlimited max message length (INT_MAX / 2147483647 bytes, max Protobuf size)
    MAX_MESSAGE_LENGTH = 2147483647

    def __init__(self, description_dict):
        """Initializes a BasicEmulatorDescription from a dictionary of properties.

        Expected properties include:
        - "grpc.port" or "grpc.address": The gRPC endpoint port or address.
        - "grpc.token" (optional): Access token for token-authenticated endpoints.
        - "grpc.jwks" / "grpc.jwk_active" (optional): JWK keys for JWT authentication.
        - "port.serial" (optional): ADB serial console port number.

        Args:
            description_dict (dict[str, str]): Key-value discovery properties.
        """
        self._description = description_dict

    def _select_security(self, arguments: dict[str, Any]) -> EmulatorSecurity:
        if "emulator.security" in arguments:
            choice = arguments.get("emulator.security").lower()
            if choice == "jwt":
                _LOGGER.debug("Selecting JWT security")
                return EmulatorSecurity.Jwt
            if choice == "token":
                _LOGGER.debug("Selecting token security")
                return EmulatorSecurity.Token
            _LOGGER.debug("Selecting no security")
            return EmulatorSecurity.Nothing

        if (
            _HAVE_TINK
            and ("grpc.jwks" in self._description)
            and ("grpc.jwk_active" in self._description)
        ):
            _LOGGER.debug("Defaulting to JWT security")
            return EmulatorSecurity.Jwt

        if "grpc.token" in self._description:
            _LOGGER.debug("Defaulting to token security")
            return EmulatorSecurity.Token

        _LOGGER.debug("Defaulting to no security")
        return EmulatorSecurity.Nothing

    def get_grpc_channel(
        self, channel_arguments: Optional[Sequence[tuple[str, Any]]] = None
    ) -> grpc.Channel:
        """Gets a configured gRPC channel to the emulator.

           This will setup all the necessary security credentials, and tokens
           if needed.

        Args:
            channel_arguments: A list of key-value pairs to configure the underlying
            gRPC Core channel or server object. Channel arguments are meant for advanced
            usages and contain experimental API (some may not labeled as experimental).
            Full list of available channel arguments and documentation can be found
            under the “grpc_arg_keys” section of “channel_arg_names.h” header file
            (https://github.com/grpc/grpc/blob/v1.60.x/include/grpc/impl/channel_arg_names.h).

            You can set "emulator.jwks.issuer" to the issuer of the JWT token that will
            be used if JWT is enabled in the emulator. If this is not set it will
            default to PyModule.

            You must make sure that the emulator security configuration
            found in $ANDROID_SDK_ROOT/emulator/lib/emulator_access.json contains
            the appropriate access rules.

            You can force security by setting "emulator.security" to any of:
            ["Nothing", "Token", "Jwt"] otherwise it will use this preference ordering
            based on discovered capabilities:

            Jwt > Token > Nothing

        Returns:
            A fully configured gRPC channel to the emulator.
        """
        addr = self.get("grpc.address")
        if not addr:
            port = self.get("grpc.port", 8554)
            addr = f"localhost:{port}"
        channel_args = list(channel_arguments) if channel_arguments else []
        args_as_dict = dict(channel_args)
        if "grpc.max_send_message_length" not in args_as_dict:
            channel_args.append(
                ("grpc.max_send_message_length", self.MAX_MESSAGE_LENGTH)
            )
        if "grpc.max_receive_message_length" not in args_as_dict:
            channel_args.append(
                ("grpc.max_receive_message_length", self.MAX_MESSAGE_LENGTH)
            )

        channel = grpc.insecure_channel(
            addr,
            options=channel_args,
        )

        security = self._select_security(args_as_dict)

        if security == EmulatorSecurity.Jwt:
            issuer = args_as_dict.get("emulator.jwks.issuer", "PyModule")
            # We need to create jwks..
            key_path = self._description["grpc.jwks"]
            active_path = self._description["grpc.jwk_active"]
            if not _HAVE_TINK:
                raise NotImplementedError(
                    "Tink is not avaible, cannot provide a secure channel."
                )
            return grpc.intercept_channel(
                channel,
                jwt_header_interceptor(key_path, active_path, False, issuer=issuer),
            )

        if security == EmulatorSecurity.Token:
            bearer = "Bearer {}".format(self.get("grpc.token", ""))
            _LOGGER.debug("Insecure Channel with token to: %s", addr)
            return grpc.intercept_channel(
                channel,
                header_adder_interceptor("authorization", bearer, False),
            )

        return channel

    def get_async_grpc_channel(
        self, channel_arguments: Optional[Sequence[tuple[str, Any]]] = None
    ) -> grpc.aio.Channel:
        """Gets a configured gRPC asynchronous channel to the emulator.

           This will setup all the necessary security credentials, and tokens
           if needed.

         Args:
            channel_arguments: A list of key-value pairs to configure the underlying
            gRPC Core channel or server object. Channel arguments are meant for advanced
            usages and contain experimental API (some may not labeled as experimental).
            Full list of available channel arguments and documentation can be found
            under the “grpc_arg_keys” section of “channel_arg_names.h” header file
            (https://github.com/grpc/grpc/blob/v1.60.x/include/grpc/impl/channel_arg_names.h).

            You can set "emulator.jwks.issuer" to the issuer of the JWT token that will
            be used if JWT is enabled in the emulator. If this is not set it will
            default to PyModule.

            You must make sure that the emulator security configuration
            found in $ANDROID_SDK_ROOT/emulator/lib/emulator_access.json contains
            the appropriate access rules.

            You can force security by setting "emulator.security" to any of:
            ["Nothing", "Token", "Jwt"] otherwise it will use this preference ordering
            based on discovered capabilities:

            Jwt > Token > Nothing


        Returns:
            A fully configured gRPC async channel to the emulator.
        """
        addr = self.get("grpc.address")
        if not addr:
            port = self.get("grpc.port", 8554)
            addr = f"localhost:{port}"
        channel_args = list(channel_arguments) if channel_arguments else []
        args_as_dict = dict(channel_args)
        if "grpc.max_send_message_length" not in args_as_dict:
            channel_args.append(
                ("grpc.max_send_message_length", self.MAX_MESSAGE_LENGTH)
            )
        if "grpc.max_receive_message_length" not in args_as_dict:
            channel_args.append(
                ("grpc.max_receive_message_length", self.MAX_MESSAGE_LENGTH)
            )

        security = args_as_dict.get("emulator.security", "none")
        interceptors = []

        security = self._select_security(args_as_dict)

        if security == EmulatorSecurity.Jwt:
            issuer = args_as_dict.get("emulator.jwks.issuer", "PyModule")

            # We need to create jwks..
            key_path = self._description["grpc.jwks"]
            active_path = self._description["grpc.jwk_active"]
            if not _HAVE_TINK:
                raise NotImplementedError(
                    "Tink is not avaible, cannot provide a secure channel."
                )
            interceptors = jwt_header_interceptor(
                key_path,
                active_path,
                use_async=True,
                issuer=issuer,
            )
        elif security == EmulatorSecurity.Token:
            bearer = "Bearer {}".format(self.get("grpc.token", ""))
            _LOGGER.debug("Insecure async channel with token to: %s", addr)
            interceptors = header_adder_interceptor("authorization", bearer, True)

        return grpc.aio.insecure_channel(
            addr,
            options=channel_args,
            interceptors=interceptors,
        )

    def client(
        self, channel_arguments: Optional[Sequence[tuple[str, Any]]] = None
    ) -> EmulatorClient:
        """Gets a managed EmulatorClient session wrapping a synchronous gRPC channel.

        Supports context manager protocol:
            with emu.client() as client:
                client.controller.setVmState(...)
        """
        return EmulatorClient(self.get_grpc_channel(channel_arguments))

    def async_client(
        self, channel_arguments: Optional[Sequence[tuple[str, Any]]] = None
    ) -> AsyncEmulatorClient:
        """Gets a managed AsyncEmulatorClient session wrapping an asynchronous gRPC channel.

        Supports async context manager protocol:
            async with emu.async_client() as client:
                await client.controller.setVmState(...)
        """
        return AsyncEmulatorClient(self.get_async_grpc_channel(channel_arguments))

    def get(self, prop: str, default_value: Optional[str] = None) -> Optional[str]:
        """Gets a property value from the emulator description dictionary.

        Args:
            prop (str): The property key from the discovery `.ini` file.
            default_value (str, optional): Default value returned if the property
                                           does not exist. Defaults to None.

        Returns:
            str: The property value, or default_value if not found.
        """
        return self._description.get(prop, default_value)

    def __hash__(self):
        return hash(frozenset(self._description.items()))


class EmulatorDescription(BasicEmulatorDescription):
    """A description of a locally discovered, running Android emulator instance.

    Provides liveness checking (`is_alive()`), OS process inspection (`process()`),
    graceful gRPC/SIGTERM termination (`shutdown()`), and managed client session
    wrappers (`client()` and `async_client()`).
    """

    def __init__(self, pid: int | str, description_dict: dict[str, str]) -> None:
        super().__init__(description_dict)
        self._description["pid"] = pid

    def __str__(self) -> str:
        alive = '🚀' if self.is_alive() else '🪦'
        return f"{self.name()} (pid: {self.pid()} {alive})"

    def process(self) -> Optional[psutil.Process]:
        """Returns the psutil process object associated with the QEMU/emulator process.

        Returns:
            Optional[psutil.Process]: The psutil process object, or None if not running.
        """
        procs = [p for p in psutil.process_iter(["pid"]) if p and p.pid == self.pid()]
        return next(iter(procs), None)

    def is_alive(self) -> bool:
        """Checks to see if this emulator description is still alive.

        A Liveness check is done by determining if the pid is still alive, and its
        status is not zombie or dead.

        Returns:
            bool: True if the pid associated with this description is alive
        """
        if not psutil.pid_exists(self.pid()):
            _LOGGER.debug("pid: %s does not exist", self.pid())
            return False

        try:
            status = psutil.Process(self.pid()).status()
            _LOGGER.debug("pid: %s status: %s", self.pid(), status)
            return status != psutil.STATUS_DEAD and status != psutil.STATUS_ZOMBIE
        except Exception as err:
            _LOGGER.debug("pid: %s, failed to retrieve status: %s", self.pid(), err)
            return False

    def _terminate_proc(self, proc: psutil.Process, timeout: float | int) -> None:
        try:
            with self.client() as client:
                _LOGGER.debug("Sending shutdown to %s.", self.pid())
                client.controller.setVmState(VmRunState(state=VmRunState.SHUTDOWN))
        except Exception as err:
            _LOGGER.error("Failed to shutdown using gRPC (%s), terminating.", err)
            proc.terminate()

        try:
            proc.wait(timeout=timeout)
        except psutil.TimeoutExpired as expired:
            _LOGGER.error(
                "Emulator did not shutdown gracefully, terminating. (%s)", expired
            )
            _kill_process_tree(proc)

    def shutdown(self, timeout=30) -> bool:
        """Gracefully shutdown the emulator.

        This sends the shutdown signal to the emulator over gRPC.
        The emulator will be terminated if:

        - The emulator does not shutdown in timeout seconds.
        - The gRPC endpoint is not responding

        Args:
            timeout (int, optional): Timeout before forcefully terminating the emulator. Defaults to 30.

        Returns:
            bool: True if the emulator is no longer running.
        """
        proc = self.process()
        if not proc:
            _LOGGER.debug("No process found, terminated.")
            return True

        try:
            self._terminate_proc(proc, timeout)
        except psutil.NoSuchProcess as no:
            _LOGGER.debug("Process was terminated. (%s)", no)
        except Exception as err:
            _LOGGER.debug(
                "Unexpected error while terminating process. %s", err, exc_info=True
            )

        return not self.is_alive()

    async def _aterminate_proc(
        self, proc: psutil.Process, timeout: float | int
    ) -> None:
        import asyncio

        start_time = asyncio.get_event_loop().time()
        try:
            async with self.async_client() as client:
                _LOGGER.debug("Sending async shutdown to %s.", self.pid())
                await client.controller.setVmState(VmRunState(state=VmRunState.SHUTDOWN))
        except Exception as err:
            _LOGGER.error("Failed to shutdown using async gRPC (%s), terminating.", err)
            proc.terminate()

        while asyncio.get_event_loop().time() - start_time < timeout:
            if not psutil.pid_exists(proc.pid):
                return
            try:
                status = proc.status()
                if status in (psutil.STATUS_DEAD, psutil.STATUS_ZOMBIE):
                    return
            except Exception:
                return
            await asyncio.sleep(0.1)

        if psutil.pid_exists(proc.pid):
            _LOGGER.error(
                "Emulator did not shutdown gracefully within timeout, terminating."
            )
            _kill_process_tree(proc)

    async def ashutdown(self, timeout: float | int = 30) -> bool:
        """Gracefully and asynchronously shutdown the emulator without blocking the event loop.

        Sends the shutdown signal over gRPC asynchronously and polls process liveness
        cooperatively (`await asyncio.sleep(0.1)`).

        Args:
            timeout (float | int, optional): Timeout in seconds before forcefully
                                             terminating the process. Defaults to 30.

        Returns:
            bool: True if the emulator is no longer running.
        """
        proc = self.process()
        if not proc:
            _LOGGER.debug("No process found, terminated.")
            return True

        try:
            await self._aterminate_proc(proc, timeout)
        except psutil.NoSuchProcess as no:
            _LOGGER.debug("Process was terminated. (%s)", no)
        except Exception as err:
            _LOGGER.debug(
                "Unexpected error while asynchronously terminating process. %s",
                err,
                exc_info=True,
            )

        return not self.is_alive()

    def name(self) -> str:
        """Returns the name of the emulator as displayed by adb.

        You can use this name with adb using the -s flag,
        or this name can be set as the ANDROID_SERIAL environment variable.

        Returns:
            str: The name of the emulator, as displayed by adb
        """
        serial = self.get("port.serial")
        if serial:
            return f"emulator-{serial}"
        return f"emulator-pid-{self.pid()}"

    def pid(self) -> int:
        """Gets the pid of the emulator.

        Returns:
            int: The process id of the emulator.
        """
        return int(self.get("pid"))
