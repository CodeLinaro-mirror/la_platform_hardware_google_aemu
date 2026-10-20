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

"""Installer and status inspector for the `aemu-harness` Jetski plugin."""

import os
from pathlib import Path
import shutil
import sys
from typing import Any, Dict, List, Optional

from commands.harness.constants import PLUGIN_DIR_NAME


def get_default_plugin_dest_dir(home_dir: Optional[Path] = None) -> Path:
    """Returns `~/.gemini/config/plugins/aemu-harness`."""
    base_home = home_dir or Path.home()
    return base_home / ".gemini" / "config" / "plugins" / PLUGIN_DIR_NAME


def resolve_bundled_plugin_source_dir(explicit_source_dir: Optional[Path] = None) -> Optional[Path]:
    """Locates the bundled `harness/plugin` directory in the checkout or installed release."""
    if explicit_source_dir:
        candidate = explicit_source_dir / "harness" / "plugin"
        if (candidate / "plugin.json").is_file():
            return candidate
        if (explicit_source_dir / "plugin.json").is_file():
            return explicit_source_dir

    here = Path(__file__).resolve()
    candidates = [
        here.parents[3] / "harness" / "plugin",
        here.parents[2] / "harness" / "plugin",
        Path.home() / ".android" / "emu-dev-cli" / "lib" / "harness" / "plugin",
    ]
    for cand in candidates:
        if (cand / "plugin.json").is_file():
            return cand
    return None


def resolve_emu_dev_cli_command() -> str:
    """Resolves the best executable command string for invoking `emu-dev-cli` from hooks."""
    which_bin = shutil.which("emu-dev-cli")
    if which_bin:
        return which_bin

    home = Path.home()
    for candidate in (
        home / ".android" / "bin" / "emu-dev-cli",
        home / ".local" / "bin" / "emu-dev-cli",
        home / ".android" / "emu-dev-cli" / "emu-dev-cli",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    main_py = Path(__file__).resolve().parents[2] / "__main__.py"
    if main_py.is_file():
        return f"{sys.executable} {main_py}"

    return "emu-dev-cli"


def install_harness_plugin(
    target_dir: Optional[Path] = None,
    source_plugin_dir: Optional[Path] = None,
    cli_command: Optional[str] = None,
) -> Dict[str, Any]:
    """Installs the `aemu-harness` plugin bundle into `~/.gemini/config/plugins/aemu-harness`."""
    src_dir = resolve_bundled_plugin_source_dir(source_plugin_dir)
    if not src_dir or not src_dir.is_dir():
        return {
            "installed": False,
            "error": "Could not locate bundled 'harness/plugin' directory.",
            "plugin_dir": str(target_dir or get_default_plugin_dest_dir()),
            "installed_files": [],
        }

    dest_dir = target_dir or get_default_plugin_dest_dir()
    resolved_cli = cli_command or resolve_emu_dev_cli_command()
    installed_files: List[str] = []

    dest_dir.mkdir(parents=True, exist_ok=True)
    for root, _, files in os.walk(src_dir):
        rel_root = Path(root).relative_to(src_dir)
        target_root = dest_dir / rel_root
        target_root.mkdir(parents=True, exist_ok=True)

        for filename in sorted(files):
            src_file = Path(root) / filename
            dst_file = target_root / filename
            if filename == "hooks.json":
                raw_text = src_file.read_text(encoding="utf-8")
                rendered = raw_text.replace("__EMU_DEV_CLI_BIN__", resolved_cli)
                dst_file.write_text(rendered, encoding="utf-8")
            else:
                shutil.copy2(src_file, dst_file)

            if dst_file.suffix == ".py":
                try:
                    os.chmod(dst_file, 0o755)
                except OSError:
                    pass
            installed_files.append(str(dst_file))

    return {
        "installed": True,
        "plugin_dir": str(dest_dir),
        "cli_command": resolved_cli,
        "installed_files": installed_files,
    }


def uninstall_harness_plugin(target_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Removes the installed `aemu-harness` plugin directory."""
    dest_dir = target_dir or get_default_plugin_dest_dir()
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
        return {
            "removed": True,
            "plugin_dir": str(dest_dir),
        }
    return {
        "removed": False,
        "plugin_dir": str(dest_dir),
    }


def get_harness_plugin_status(target_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Inspects whether `aemu-harness` is installed and verifies its core components."""
    dest_dir = target_dir or get_default_plugin_dest_dir()
    required_files = [
        "plugin.json",
        "hooks.json",
        "rules/AGENTS.md",
        "agents/aemu-harness-orchestrator.md",
        "agents/aemu-code-reviewer.md",
        "agents/aemu-flake-hunter.md",
        "sidecars/aemu_harness_ui/sidecar.json",
        "sidecars/aemu_harness_ui/server.py",
    ]
    present_files = [
        rel for rel in required_files if (dest_dir / rel).is_file()
    ]
    missing_files = [
        rel for rel in required_files if not (dest_dir / rel).is_file()
    ]
    return {
        "installed": dest_dir.is_dir() and len(missing_files) == 0,
        "plugin_dir": str(dest_dir),
        "present_files": present_files,
        "missing_files": missing_files,
        "cli_command": resolve_emu_dev_cli_command(),
    }
