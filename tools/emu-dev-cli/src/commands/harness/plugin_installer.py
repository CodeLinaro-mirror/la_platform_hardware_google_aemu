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

import json
import os
from pathlib import Path
import shlex
import shutil
import sys
from typing import Any, Dict, List, Optional, Union

from commands.harness.constants import PLUGIN_DIR_NAME
from commands.harness.goldfish_libs import REUSABLE_LIBS_CATALOG
from commands.source_directory import (
    get_all_source_directories,
    get_source_directory,
)

PLUGIN_EXECUTABLE_MODE: int = 0o755
# Rendered from `REUSABLE_LIBS_CATALOG` for the standalone AuxPane sidecar, which
# cannot import the CLI; the bundled file is an empty placeholder.
SIDECAR_LIBS_CATALOG_FILENAME: str = "libs_catalog.json"

def get_default_plugin_dest_dir(
    home_dir: Optional[Union[Path, str]] = None,
    scope: str = "global",
    workspace_dir: Optional[Union[Path, str]] = None,
) -> Path:
    """Returns `~/.gemini/config/plugins/aemu-harness` (global) or `<workspace>/.agent/plugins/aemu-harness` (workspace)."""
    if (scope or "global").strip().lower() == "workspace":
        ws_root = Path(workspace_dir) if workspace_dir else Path.cwd()
        return ws_root / ".agent" / "plugins" / PLUGIN_DIR_NAME
    base_home = Path(home_dir) if home_dir else Path.home()
    return base_home / ".gemini" / "config" / "plugins" / PLUGIN_DIR_NAME


def _candidate_plugin_dirs_for_root(root: Path) -> List[Path]:
    """Returns candidate `harness/plugin` paths relative to a workspace or tool root."""
    return [
        root / "hardware" / "google" / "aemu" / "tools" / "emu-dev-cli" / "harness" / "plugin",
        root / "tools" / "emu-dev-cli" / "harness" / "plugin",
        root / "harness" / "plugin",
        root,
    ]


def resolve_bundled_plugin_source_dir(
    explicit_source_dir: Optional[Union[Path, str]] = None,
) -> Optional[Path]:
    """Locates the bundled `harness/plugin` directory in the checkout or installed release."""
    if explicit_source_dir:
        explicit_path = Path(explicit_source_dir)
        for cand in _candidate_plugin_dirs_for_root(explicit_path):
            if (cand / "plugin.json").is_file():
                return cand

    here = Path(__file__).resolve()
    candidates: List[Path] = [
        here.parents[3] / "harness" / "plugin",
        here.parents[2] / "harness" / "plugin",
        Path.home() / ".android" / "emu-dev-cli" / "lib" / "harness" / "plugin",
    ]

    # Search upward from the current working directory
    try:
        cwd = Path.cwd().resolve()
        for parent in [cwd] + list(cwd.parents):
            candidates.extend(_candidate_plugin_dirs_for_root(parent))
    except OSError:
        pass

    # Search configured source directories (e.g. emu-main-next in ~/.android/emu-dev-cli.json)
    for branch in ("emu-main-next", "emu-main-dev", "git_main"):
        try:
            branch_src = get_source_directory(branch)
        except Exception:
            branch_src = None
        if branch_src:
            candidates.extend(_candidate_plugin_dirs_for_root(Path(branch_src)))

    try:
        for entry in get_all_source_directories():
            src_str = entry.get("path") if isinstance(entry, dict) else entry
            if src_str and isinstance(src_str, str):
                candidates.extend(_candidate_plugin_dirs_for_root(Path(src_str)))
    except Exception:
        pass

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


def _compute_sidecar_bundle_stamp(src_file: Path, cli_src_dir: Optional[Union[Path, str]] = None) -> str:
    """Computes an 8-char SHA-256 stamp over UI templates and sidecar scripts so Jetski reloads on update."""
    import hashlib

    h = hashlib.sha256()
    src_root = Path(cli_src_dir).resolve() if cli_src_dir else Path(__file__).resolve().parents[2]
    watched = [
        src_root / "templates" / "gerrit_ui.html",
        src_root / "commands" / "gerrit" / "ui_server.py",
        src_root / "commands" / "gerrit" / "presubmit_targets.py",
        src_root / "commands" / "gerrit" / "ai_analysis.py",
        src_file.parent / "sidecars" / "aemu_gerrit_ui" / "server.py",
        src_file.parent / "sidecars" / "aemu_harness_ui" / "server.py",
    ]
    for p in watched:
        try:
            if p.is_file():
                h.update(p.read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:8]


def _render_plugin_file(
    src_file: Path,
    dst_file: Path,
    resolved_cli: str,
    cli_src_dir: Optional[Union[Path, str]] = None,
) -> None:
    """Renders or copies a single plugin file with JSON-safe cross-platform paths."""
    if src_file.name == "plugin.json":
        try:
            manifest = json.loads(src_file.read_text(encoding="utf-8"))
            base_ver = str(manifest.get("version") or "0.1.0").split("+", 1)[0]
            stamp = _compute_sidecar_bundle_stamp(src_file, cli_src_dir=cli_src_dir)
            manifest["version"] = f"{base_ver}+{stamp}"
            dst_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return
        except Exception:
            shutil.copy2(src_file, dst_file)
            return
    if src_file.name == "hooks.json":
        escaped_cli = json.dumps(resolved_cli, ensure_ascii=False)[1:-1]
        rendered = src_file.read_text(encoding="utf-8").replace("__EMU_DEV_CLI_BIN__", escaped_cli)
        dst_file.write_text(rendered, encoding="utf-8")
    elif src_file.name == "mcp_config.json":
        use_posix = os.name != "nt" and "\\" not in resolved_cli
        cli_parts = [t.strip("\"'") for t in shlex.split(resolved_cli, posix=use_posix)] or ["emu-dev-cli"]
        mcp_payload = {
            "mcpServers": {
                "aemu-harness-mcp": {
                    "command": cli_parts[0],
                    "args": cli_parts[1:] + ["harness", "mcp-server"],
                }
            }
        }
        dst_file.write_text(json.dumps(mcp_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    elif src_file.suffix == ".py":
        src_root_str = str(Path(cli_src_dir).resolve() if cli_src_dir else Path(__file__).resolve().parents[2])
        escaped_src_root = json.dumps(src_root_str, ensure_ascii=False)[1:-1]
        rendered = src_file.read_text(encoding="utf-8").replace("__EMU_DEV_CLI_SRC_DIR__", escaped_src_root)
        dst_file.write_text(rendered, encoding="utf-8")
    elif src_file.name == SIDECAR_LIBS_CATALOG_FILENAME:
        dst_file.write_text(
            json.dumps(REUSABLE_LIBS_CATALOG, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    else:
        shutil.copy2(src_file, dst_file)

    if dst_file.suffix == ".py":
        try:
            os.chmod(dst_file, PLUGIN_EXECUTABLE_MODE)
        except OSError:
            pass


def install_harness_plugin(
    target_dir: Optional[Union[Path, str]] = None,
    source_plugin_dir: Optional[Union[Path, str]] = None,
    cli_command: Optional[str] = None,
    scope: str = "global",
    workspace_dir: Optional[Union[Path, str]] = None,
    cli_src_dir: Optional[Union[Path, str]] = None,
) -> Dict[str, Any]:
    """Installs the `aemu-harness` plugin bundle into global or workspace plugin dir."""
    dest_dir = (
        Path(target_dir)
        if target_dir
        else get_default_plugin_dest_dir(scope=scope, workspace_dir=workspace_dir)
    )
    src_dir = resolve_bundled_plugin_source_dir(source_plugin_dir)
    if not src_dir or not src_dir.is_dir():
        return {
            "installed": False,
            "error": "Could not locate bundled 'harness/plugin' directory.",
            "plugin_dir": str(dest_dir),
            "installed_files": [],
        }

    resolved_cli = cli_command or resolve_emu_dev_cli_command()
    installed_files: List[str] = []
    dest_dir.mkdir(parents=True, exist_ok=True)
    for root, _, files in os.walk(src_dir):
        target_root = dest_dir / Path(root).relative_to(src_dir)
        target_root.mkdir(parents=True, exist_ok=True)
        for filename in sorted(files):
            dst_file = target_root / filename
            _render_plugin_file(
                Path(root) / filename,
                dst_file,
                resolved_cli,
                cli_src_dir=cli_src_dir,
            )
            installed_files.append(str(dst_file))

    _enable_plugin_and_sidecar_in_config(dest_dir)
    return {
        "installed": True,
        "plugin_dir": str(dest_dir),
        "cli_command": resolved_cli,
        "installed_files": installed_files,
    }


def _enable_plugin_and_sidecar_in_config(dest_dir: Path) -> None:
    """Ensures `aemu-harness` and `aemu-harness/aemu_harness_ui` are enabled in `config.json`."""
    try:
        if dest_dir.parent.name != "plugins":
            return
        config_json = dest_dir.parents[1] / "config.json"
        data: Dict[str, Any] = {}
        if config_json.is_file():
            raw = config_json.read_text(encoding="utf-8").strip()
            if raw:
                data = json.loads(raw)
        plugins_cfg = data.setdefault("plugins", {})
        if isinstance(plugins_cfg, dict):
            plugin_entry = plugins_cfg.setdefault(PLUGIN_DIR_NAME, {})
            if isinstance(plugin_entry, dict):
                plugin_entry["enabled"] = True
        sidecars_cfg = data.setdefault("sidecars", {})
        if isinstance(sidecars_cfg, dict):
            for sidecar_id in ("aemu_harness_ui", "aemu_gerrit_ui"):
                sidecar_entry = sidecars_cfg.setdefault(
                    f"{PLUGIN_DIR_NAME}/{sidecar_id}", {}
                )
                if isinstance(sidecar_entry, dict):
                    sidecar_entry["enabled"] = True
        config_json.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )
    except (OSError, ValueError, TypeError):
        pass


def uninstall_harness_plugin(
    target_dir: Optional[Union[Path, str]] = None,
) -> Dict[str, Any]:
    """Removes the installed `aemu-harness` plugin directory."""
    dest_dir = Path(target_dir) if target_dir else get_default_plugin_dest_dir()
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


def get_harness_plugin_status(
    target_dir: Optional[Union[Path, str]] = None,
) -> Dict[str, Any]:
    """Inspects whether `aemu-harness` is installed and verifies its core components."""
    dest_dir = Path(target_dir) if target_dir else get_default_plugin_dest_dir()
    required_files = [
        "plugin.json",
        "hooks.json",
        "mcp_config.json",
        "rules/AGENTS.md",
        "rules/goldfish-library-reuse.md",
        "rules/error-messages-and-statusor.md",
        "rules/flake-and-sanitizer-debugging.md",
        "agents/aemu-harness-orchestrator.md",
        "agents/aemu-code-reviewer.md",
        "agents/aemu-flake-hunter.md",
        "sidecars/aemu_harness_ui/sidecar.json",
        "sidecars/aemu_harness_ui/server.py",
        "sidecars/aemu_gerrit_ui/sidecar.json",
        "sidecars/aemu_gerrit_ui/server.py",
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
