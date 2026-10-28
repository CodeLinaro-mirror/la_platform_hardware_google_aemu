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

"""HTTP REST endpoint handlers for AEMU Fast Gerrit UI (`emu-dev-cli gerrit ui`)."""

from commands.gerrit.handlers.ui_handler import (
    DEFAULT_GERRIT_UI_PORT,
    MAX_PORT_PROBES,
    SIDECAR_PRELOAD_JS,
    SIDECAR_PRELOAD_SCRIPT_TAG,
    find_available_port,
    get_gerrit_ui_template_path,
    get_gerrit_ui_template_version,
    make_gerrit_ui_handler,
    render_static_gerrit_ui_html,
    serve_gerrit_ui,
)

__all__ = [
    "DEFAULT_GERRIT_UI_PORT",
    "MAX_PORT_PROBES",
    "SIDECAR_PRELOAD_JS",
    "SIDECAR_PRELOAD_SCRIPT_TAG",
    "find_available_port",
    "get_gerrit_ui_template_path",
    "get_gerrit_ui_template_version",
    "make_gerrit_ui_handler",
    "render_static_gerrit_ui_html",
    "serve_gerrit_ui",
]
