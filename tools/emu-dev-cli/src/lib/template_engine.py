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

"""Lightweight template rendering engine for Markdown prompts and reports."""

from pathlib import Path
from string import Template
from typing import Any, Dict
from lib.workspace import WorkspacePathResolver


class TemplateEngine:
    """Renders markdown template files using string.Template substitution."""

    def __init__(self, template_dir: Path):
        self.template_dir = template_dir

    def render(self, template_name: str, context: Dict[str, Any]) -> str:
        """Reads template_name from template_dir and substitutes context placeholders.

        Args:
            template_name: Name of the template file (e.g. 'jetski_investigation_prompt.md').
            context: Dictionary of variable values for template substitution.

        Returns:
            Rendered string output.
        """
        tpl_path = self.template_dir / template_name
        if not tpl_path.exists():
            raise FileNotFoundError(f"Template file not found at '{tpl_path}'")

        content = tpl_path.read_text(encoding="utf-8")
        tpl = Template(content)
        return tpl.safe_substitute({k: str(v) for k, v in context.items()})


def get_template_engine() -> TemplateEngine:
    """Returns a TemplateEngine instance configured to locate src/templates/ in workspace or runfiles."""
    resolver = WorkspacePathResolver("emu-main-next")
    this_dir = Path(__file__).resolve().parent.parent / "templates"
    if this_dir.exists():
        return TemplateEngine(this_dir)

    found = resolver.find_directory("hardware/google/aemu/tools/emu-dev-cli/src/templates")
    if found and found.exists():
        return TemplateEngine(found)

    return TemplateEngine(Path.cwd() / "src" / "templates")
