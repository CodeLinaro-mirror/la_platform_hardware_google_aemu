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
import os
import platform
import re
import tempfile
from pathlib import Path
from typing import Optional

from aemu.discovery.emulator_description import (
    BasicEmulatorDescription,
    EmulatorDescription,
)


_LOGGER = logging.getLogger("aemu-grpc")


class EmulatorNotFound(Exception):
    pass


class EmulatorDiscovery(object):
    """Discovers all running emulators on the local system or creates remote connections.

    Scans standard Android emulator discovery directories (such as ~/.android/avd/running,
    OS temporary folders, XDG_RUNTIME_DIR, or LOCALAPPDATA) for `.ini` discovery files
    written by active AEMU emulator instances.

    Example usage:
        disc = EmulatorDiscovery()
        print(f"Discovered {disc.available()} active emulators.")

        # Iterate over all discovered emulators
        for emu in disc.emulators():
            print(f"Emulator {emu.name()} running at {emu.get('grpc.address')}")

        # Connect to the first discovered emulator and use its gRPC client
        first_emu = disc.first()
        with first_emu.client() as client:
            client.controller.setVmState(...)
    """

    ANDROID_SUBDIR = ".android"

    # Format of our discovery files.
    _PID_FILE_ = re.compile("pid_(\\d+).ini")

    def __init__(self) -> None:
        self.discovery_dirs = get_discovery_directories()

    def _discover_running(
        self, cleanup: bool = True
    ) -> set[EmulatorDescription]:
        """Discovers all running emulators by parsing available discovery `.ini` files.

        Args:
            cleanup (bool): If True, removes stale discovery `.ini` files and associated
                            directories for emulators that are no longer alive. Defaults to True.

        Returns:
            set[EmulatorDescription]: A set of live discovered emulators.
        """
        emulators = set()
        for discovery_dir in self.discovery_dirs:
            _LOGGER.debug("Discovering emulators in %s", discovery_dir)
            if discovery_dir.exists():
                for file in discovery_dir.glob("*.ini"):
                    pid_file = self._PID_FILE_.match(file.name)
                    if pid_file:
                        _LOGGER.debug("Found %s", file)
                        try:
                            emu = EmulatorDescription(
                                pid_file.group(1), self._parse_ini(file)
                            )
                            if emu.is_alive():
                                emulators.add(emu)
                            elif cleanup:
                                self._erase(file)
                        except Exception as err:
                            _LOGGER.error(
                                "Failed to parse %s due to %s, ignoring", file, err
                            )

        return emulators

    def cleanup_stale(self) -> int:
        """Erases all stale discovery files for emulators that are no longer alive.

        Returns:
            int: The number of active emulators remaining after cleanup.
        """
        return len(self._discover_running(cleanup=True))

    def _parse_ini(self, ini_file: Path | str) -> dict[str, str]:
        """Parse a discovered ini file

        Args:
            ini_file (str): The emulator description file

        Returns:
            dict[str, str]: The dictionary containing the emulator information.
        """
        _LOGGER.debug("Discovering emulator: %s", ini_file)
        emu = dict()
        with open(ini_file, mode="r", encoding="utf-8") as ini:
            for line in ini.readlines():
                line = line.strip()
                if "=" in line:
                    isi = line.index("=")
                    emu[line[:isi]] = line[isi + 1 :]
        return emu

    def _erase(self, file: Path) -> None:
        """Erases a dangling discovery file and its associated directory.

        This method attempts to delete the specified discovery `.ini` file and
        the corresponding directory that holds emulator instance information.
        It's primarily used for cleanup when an emulator instance is no longer active.

        Args:
            file: The Path object representing the discovery `.ini` file to be deleted.
                This file is expected to match the naming pattern 'pid_<PID>.ini'.

        Raises:
            AssertionError: If the provided `file` does not match the expected
                            pid file naming convention.
        """
        assert self._PID_FILE_.match(
            file.name
        ), "Erase should be called with a pid file"

        try:
            file.unlink(missing_ok=True)
            _LOGGER.debug("Deleted discovery file: %s", file)
            dir_to_delete = file.with_suffix("")  # Remove .ini suffix
            shutil.rmtree(str(dir_to_delete))
            _LOGGER.debug("Deleted discovery directory: %s", dir_to_delete)
        except FileNotFoundError:
            _LOGGER.debug("Directory '%s' not found.", dir_to_delete)
        except OSError as e:
            _LOGGER.debug(f"Error deleting directory '%s' due to: %s", dir_to_delete, e)

    def available(self) -> int:
        """The number of discovered emulators.

        Returns:
            int: The number of discovered emulators.
        """
        return len(self._discover_running())

    def emulators(self) -> frozenset[EmulatorDescription]:
        """All currently running discovered emulators.

        Returns:
            frozenset[EmulatorDescription]: An immutable set of all discovered emulators.
        """
        return frozenset(self._discover_running())

    def find_emulator(self, prop: str, value: str) -> Optional[EmulatorDescription]:
        """Finds the first emulator where the specified property matches the given value.

        Args:
            prop (str): The property key to search for (e.g., "pid", "grpc.port").
            value (str): The value that the property must match.

        Returns:
            Optional[EmulatorDescription]: The matching emulator description, or None
                                           if no such emulator was found.
        """
        for emu in self._discover_running():
            if emu.get(prop) == value:
                return emu

        return None

    def find_by_pid(self, pid: int) -> Optional[EmulatorDescription]:
        """Finds the running emulator with the given process ID.

        Args:
            pid (int): The process ID of the running emulator.

        Returns:
            Optional[EmulatorDescription]: The matching emulator description, or None
                                           if no emulator is running with that process ID.
        """
        return self.find_emulator("pid", str(pid))

    def first(self) -> EmulatorDescription:
        """Gets the first discovered running emulator.

        Raises:
            EmulatorNotFound: If no running emulators were found on the system.

        Returns:
            EmulatorDescription: The first discovered running emulator instance.
        """
        emulators = self._discover_running()
        if len(emulators) == 0:
            raise EmulatorNotFound("No running emulators were found.")
        return next(iter(emulators))

    @staticmethod
    def connection(
        address: str, token: Optional[str] = None
    ) -> BasicEmulatorDescription:
        """Creates a connection description for a remote emulator gRPC endpoint.

        Use this method when connecting to a remote emulator rather than discovering
        locally running emulators via `.ini` discovery files.

        Args:
            address (str): The endpoint URI (e.g., "localhost:8554" or "emu.example.com:8554").
            token (Optional[str]): An optional gRPC access token for Token authentication.

        Returns:
            BasicEmulatorDescription: A description configured to connect to the remote endpoint.
        """
        description = {"grpc.address": address}
        if token:
            description["grpc.token"] = token
        return BasicEmulatorDescription(description)


def get_discovery_directories() -> list[Path]:
    """All the discovery directories that can contain emulator discovery files.

    Returns:
        list[Path]: A list of directories with possible discovery files.
    """
    path = None
    if platform.system() == "Windows" and "LOCALAPPDATA" in os.environ:
        path = Path(os.environ.get("LOCALAPPDATA")) / "Temp"
    if platform.system() == "Linux":
        if "XDG_RUNTIME_DIR" in os.environ:
            path = Path(os.environ.get("XDG_RUNTIME_DIR"))
        if path is None or not path.exists():
            path = Path("/") / "run" / "user" / Path(str(os.getuid()))
    if platform.system() == "Darwin" and "HOME" in os.environ:
        path = Path.home() / "Library" / "Caches" / "TemporaryItems"

    paths = _get_user_directories()
    paths.append(path)

    dirs = [p / "avd" / "running" for p in paths if p is not None]
    tmp = Path(tempfile.gettempdir())
    dirs.append(tmp / "avd" / "running")
    dirs.append(tmp)

    seen = set()
    result = []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            result.append(d)
    return result


def _get_user_directories() -> list[Path]:
    # See ConfigDirs::getUserDirectory()
    _LOGGER.debug("Retrieving user directories")
    paths = []

    if "ANDROID_EMULATOR_HOME" in os.environ:
        paths.append(Path(os.environ.get("ANDROID_EMULATOR_HOME")))

    if "ANDROID_SDK_HOME" in os.environ:
        paths.append(
            Path(os.environ.get("ANDROID_SDK_HOME")) / EmulatorDiscovery.ANDROID_SUBDIR
        )

    if "ANDROID_AVD_HOME" in os.environ:
        paths.append(Path(os.environ.get("ANDROID_AVD_HOME")))

    paths.append(Path.home() / EmulatorDiscovery.ANDROID_SUBDIR)
    return paths


def get_default_emulator() -> EmulatorDescription:
    """The first discovered emulator.

        Useful if you expect only one running emulator.

    Raises:
        EmulatorNotFound: No running emulators were found.

    Returns:
        EmulatorDescription: The first discovered emulator.
    """
    return EmulatorDiscovery().first()
