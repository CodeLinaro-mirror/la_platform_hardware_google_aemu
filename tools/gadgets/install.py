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

"""Installer for developer and agent gadgets in hardware/google/aemu/tools/gadgets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import stat
import sys

SCRIPT_DIR = Path(__file__).resolve().parent

PANE_TITLE_HOOK_SPEC = {
    "enabled": True,
    "PreInvocation": [
        {
            "type": "command",
            "command": "python3 ~/.gemini/config/hooks/zellij_pane_title.py --hook pre",
            "timeout": 5,
        }
    ],
    "Stop": [
        {
            "type": "command",
            "command": "python3 ~/.gemini/config/hooks/zellij_pane_title.py --hook stop",
            "timeout": 5,
        }
    ],
}


def install_pane_title_gadget(config_dir: Path, src_dir: Path = SCRIPT_DIR) -> list[Path]:
    """Installs the pane_title hook script, global rule, and hooks.json entry."""
    hooks_dir = config_dir / "hooks"
    rules_dir = config_dir / "rules"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    rules_dir.mkdir(parents=True, exist_ok=True)

    pane_dir = src_dir / "pane_title"
    dst_script = hooks_dir / "zellij_pane_title.py"
    dst_rule = rules_dir / "zellij_pane_title.md"
    hooks_json_path = config_dir / "hooks.json"

    shutil.copy2(pane_dir / "zellij_pane_title.py", dst_script)
    dst_script.chmod(dst_script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    shutil.copy2(pane_dir / "zellij_pane_title.md", dst_rule)

    hooks_data: dict = {}
    if hooks_json_path.is_file():
        try:
            hooks_data = json.loads(hooks_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            hooks_data = {}

    hooks_data["zellij-pane-title"] = PANE_TITLE_HOOK_SPEC
    hooks_json_path.write_text(
        json.dumps(hooks_data, indent=2) + "\n", encoding="utf-8"
    )

    return [dst_script, dst_rule, hooks_json_path]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install AEMU developer and agent gadgets into ~/.gemini/config."
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path.home() / ".gemini" / "config",
        help="Target Jetski config directory (default: ~/.gemini/config).",
    )
    args = parser.parse_args(argv)

    installed = install_pane_title_gadget(args.config_dir)
    print("Installed AEMU agent gadgets:")
    for path in installed:
        print(f"  + {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
