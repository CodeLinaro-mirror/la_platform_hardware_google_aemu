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

"""Jetski Web AuxPane UI Sidecar for AEMU Fast Gerrit (`emu-dev-cli gerrit ui`)."""

from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys

PORT: int = int(os.environ.get("ANTIGRAVITY_SIDECAR_WEB_PORT", "8085"))
CONFIGURED_SRC_DIR: str = "__EMU_DEV_CLI_SRC_DIR__"


def _source_dirs_from_config(cfg_file: Path) -> list[Path]:
    """Extracts candidate `emu-dev-cli/src` directories from `~/.android/emu-dev-cli.json`."""
    if not cfg_file.is_file():
        return []
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    except Exception:
        return []
    src_dirs = cfg.get("source_directories") or {}
    if not isinstance(src_dirs, dict):
        return []
    resolved: list[Path] = []
    for root_str in src_dirs.values():
        if isinstance(root_str, str) and root_str.strip():
            root = Path(root_str.strip())
            resolved.append(root / "hardware" / "google" / "aemu" / "tools" / "emu-dev-cli" / "src")
            resolved.append(root / "tools" / "emu-dev-cli" / "src")
    return resolved


def _has_complete_gerrit_ui_bundle(cand: Path) -> bool:
    """Returns True when both `ui_server.py` and `templates/gerrit_ui.html` exist in `cand`."""
    return (
        (cand / "commands" / "gerrit" / "ui_server.py").is_file()
        and (cand / "templates" / "gerrit_ui.html").is_file()
    )


def _activate_candidate_src_dir(cand: Path) -> None:
    """Prepends `cand` and its parent directory to `sys.path`."""
    cand_str = str(cand)
    if cand_str not in sys.path:
        sys.path.insert(0, cand_str)
    parent_str = str(cand.parent)
    if parent_str not in sys.path:
        sys.path.insert(1, parent_str)


def _bootstrap_emu_dev_cli_path() -> Path | None:
    """Adds `emu-dev-cli/src` to `sys.path` from the installed or source checkout."""
    candidates: list[Path] = []
    if CONFIGURED_SRC_DIR and not CONFIGURED_SRC_DIR.startswith("__"):
        candidates.append(Path(CONFIGURED_SRC_DIR))

    # Relative to source tree (`tools/emu-dev-cli/harness/plugin/sidecars/aemu_gerrit_ui/server.py`)
    here = Path(__file__).resolve()
    if len(here.parents) >= 5:
        candidates.append(here.parents[4] / "src")

    home = Path.home()
    candidates.extend([
        home / ".android" / "emu-dev-cli" / "lib",
        home / ".android" / "emu-dev-cli" / "lib" / "src",
        home / ".android" / "emu-dev-cli" / "src",
        home / "tools" / "aemu" / "tools" / "emu-dev-cli" / "src",
    ])
    candidates.extend(_source_dirs_from_config(home / ".android" / "emu-dev-cli.json"))

    for cand in candidates:
        if _has_complete_gerrit_ui_bundle(cand):
            _activate_candidate_src_dir(cand)
            return cand

    for cand in candidates:
        if (cand / "commands" / "gerrit" / "ui_server.py").is_file():
            _activate_candidate_src_dir(cand)
            return cand

    return None


WATCHED_GERRIT_MODULES: tuple[str, ...] = (
    "lib.gerrit_api",
    "lib.web_security",
    "core.web.security",
    "core.web.router",
    "core.web.assets",
    "core.web.handler",
    "core.web.agent_proxy",
    "core.web.server",
    "core.web",
    "commands.gerrit.ai_analysis",
    "commands.gerrit.presubmit_targets",
    "commands.gerrit.sidecar_updater",
    "commands.gerrit.services.diff_parser",
    "commands.gerrit.services.queue_service",
    "commands.gerrit.services.vote_submit_service",
    "commands.gerrit.services.inline_qa_service",
    "commands.gerrit.services",
    "commands.gerrit.handlers.ui_handler",
    "commands.gerrit.handlers",
    "commands.gerrit.ui_server",
)


def main() -> None:
    _bootstrap_emu_dev_cli_path()
    import commands.gerrit.ui_server as ui_mod
    from core.web.server import HotReloadHandlerDispatcher

    if not os.environ.get("ANTIGRAVITY_SIDECAR_UI_TOKEN"):
        print(
            "warning: ANTIGRAVITY_SIDECAR_UI_TOKEN is unset, so the AEMU Gerrit sidecar rejects "
            "every API request. Start it from Jetski, which sets the token.",
            file=sys.stderr,
        )

    viewer = os.environ.get("AEMU_GERRIT_VIEWER", "")
    dispatcher = HotReloadHandlerDispatcher(
        module_names=WATCHED_GERRIT_MODULES,
        handler_builder=lambda: ui_mod.make_gerrit_ui_handler(
            ui_mod.GerritUIService(default_viewer=viewer)
        ),
        extra_paths_fn=lambda: tuple(
            ui_mod.get_gerrit_ui_template_path(variant)
            for variant in ("classic", "polygerrit")
        ),
    )

    server = ThreadingHTTPServer(("127.0.0.1", PORT), dispatcher)  # type: ignore[arg-type]
    server.serve_forever()


if __name__ == "__main__":
    main()
