# Copyright 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Hermetic test discovery bootstrap for `python3 -m unittest discover -s tests -p '*_test.py'`."""

import atexit
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile

_TESTS_DIR = Path(__file__).resolve().parent
_TOOL_ROOT = _TESTS_DIR.parent
_SRC_DIR = str(_TOOL_ROOT / "src")
if str(_TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOL_ROOT))
if _SRC_DIR in sys.path:
    sys.path.remove(_SRC_DIR)
sys.path.insert(0, _SRC_DIR)

import core  # noqa: E402
import clients  # noqa: E402
import commands  # noqa: E402
import workflows  # noqa: E402

for _pkg, _sub in (
    (core, "core"),
    (clients, "clients"),
    (commands, "commands"),
    (workflows, "workflows"),
):
    _test_sub = str(_TESTS_DIR / _sub)
    if hasattr(_pkg, "__path__") and _test_sub not in _pkg.__path__:
        _pkg.__path__.append(_test_sub)

import commands.source_directory as _source_directory  # noqa: E402

_ORIG_GET_SOURCE_DIR = _source_directory.get_source_directory
_HERMETIC_WS_DIR: str | None = None


def _ensure_hermetic_workspace() -> str:
    global _HERMETIC_WS_DIR
    if _HERMETIC_WS_DIR is not None:
        return _HERMETIC_WS_DIR
    tmp_ws = Path(tempfile.mkdtemp(prefix="emu_dev_cli_hermetic_ws_"))
    atexit.register(lambda: shutil.rmtree(tmp_ws, ignore_errors=True))
    advisor_dir = (
        tmp_ws
        / "hardware"
        / "generic"
        / "goldfish"
        / "emulator"
        / "crashreport"
        / "tool"
        / "advisor"
    )
    advisor_dir.mkdir(parents=True, exist_ok=True)
    for mod_name in (
        "api",
        "buganizer",
        "client",
        "context",
        "dump",
        "metadata",
        "rca",
        "symbols",
        "advisor",
    ):
        (advisor_dir / f"{mod_name}.py").write_text(
            f'"""Hermetic stub for {mod_name}."""\n', encoding="utf-8"
        )
    libs_py_dir = tmp_ws / "hardware" / "google" / "aemu" / "tools" / "libs_python"
    libs_py_dir.mkdir(parents=True, exist_ok=True)
    apk_dir = tmp_ws / "tools" / "emu-dev-cli" / "perf_probe"
    apk_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(apk_dir / "perf_probe.apk", "w") as zf:
        zf.writestr("AndroidManifest.xml", "<manifest/>")
        zf.writestr("classes.dex", "dex\n035\x00")
    _HERMETIC_WS_DIR = str(tmp_ws)
    os.environ.setdefault("RUNFILES_DIR", _HERMETIC_WS_DIR)
    return _HERMETIC_WS_DIR


_ensure_hermetic_workspace()


def _hermetic_get_source_directory(branch: str) -> str | None:
    res = _ORIG_GET_SOURCE_DIR(branch)
    if res is not None:
        return res
    if branch == "emu-main-next":
        return _ensure_hermetic_workspace()
    return None


_source_directory.get_source_directory = _hermetic_get_source_directory


class BootstrapTest(unittest.TestCase):
    """Verifies that `src/` is on `sys.path` during test discovery."""

    def test_src_dir_on_sys_path(self) -> None:
        self.assertIn(_SRC_DIR, sys.path)
