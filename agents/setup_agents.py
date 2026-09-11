#!/usr/bin/env python3
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

"""Redirector script forwarding to emu-dev-cli installation script."""

from pathlib import Path
import subprocess
import sys


def main():
    script_dir = Path(__file__).resolve().parent
    install_script = (
        script_dir.parent / "tools" / "emu-dev-cli" / "install.py"
    ).resolve()

    if not install_script.exists():
        sys.stderr.write(f"ERROR: Could not find install script at '{install_script}'\n")
        sys.exit(1)

    cmd = [sys.executable, str(install_script)] + sys.argv[1:]
    res = subprocess.run(cmd)
    sys.exit(res.returncode)


if __name__ == "__main__":
    main()
