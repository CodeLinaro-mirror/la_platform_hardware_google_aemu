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

"""Modular CSS, HTML partial, and JavaScript asset assembly for AEMU Fast Gerrit UI."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
from pathlib import Path
import re

from core.web.assets import ASSET_VERSION_HEX_LENGTH

STYLESHEET_PLACEHOLDER = "<!-- __GERRIT_UI_STYLESHEET__ -->"
REACT_SCRIPT_PLACEHOLDER = "<!-- __GERRIT_UI_REACT_SCRIPT__ -->"
APP_SCRIPT_PLACEHOLDER = "<!-- __GERRIT_UI_APP_SCRIPT__ -->"
SCRIPTS_PLACEHOLDER = "<!-- __GERRIT_UI_SCRIPTS__ -->"
PARTIAL_PLACEHOLDER_RE = re.compile(r"<!--\s*__GERRIT_UI_PARTIAL:([a-zA-Z0-9_-]+)__\s*-->")
SCRIPT_CLOSE_RE = re.compile(r"</script", re.IGNORECASE)

ASSET_ROUTE_CSS = "/assets/gerrit_ui.css"
ASSET_ROUTE_REACT_JS = "/assets/gerrit_ui_react.js"
ASSET_ROUTE_APP_JS = "/assets/gerrit_ui_app.js"
REACT_BUNDLE_FILENAME = "gerrit_ui_react.bundle.js"
IMMUTABLE_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"

_BOOTSTRAP_INLINE_LINES = (
    '  window.__GERRIT_UI_VERSION__ = "__GERRIT_UI_VERSION__";',
    "  window.__GERRIT_UI_BOOTSTRAP__ = __GERRIT_UI_BOOTSTRAP_JSON__;",
)


def _resolve_src_root_from_template(template_path: Path) -> Path:
    """Returns the `src/` (or installed `lib/`) root containing `templates/` and `ui/gerrit/`."""
    return template_path.parent.parent


def resolve_gerrit_ui_asset_dirs(template_path: Path) -> tuple[Path, Path, Path, Path]:
    """Returns `(styles_dir, scripts_dir, partials_dir, react_bundle_path)` for `template_path`."""
    src_root = _resolve_src_root_from_template(template_path)
    ui_root = src_root / "ui" / "gerrit"
    return (
        ui_root / "styles",
        ui_root / "scripts",
        ui_root / "partials",
        template_path.parent / REACT_BUNDLE_FILENAME,
    )


def _list_sorted_files(directory: Path, pattern: str) -> tuple[Path, ...]:
    """Returns deterministic sorted file paths matching `pattern` inside `directory`."""
    if not directory.is_dir():
        return ()
    return tuple(sorted(p for p in directory.glob(pattern) if p.is_file()))


def list_gerrit_ui_css_paths(template_path: Path) -> tuple[Path, ...]:
    """Returns ordered modular `.css` files for the Gerrit UI."""
    styles_dir, _, _, _ = resolve_gerrit_ui_asset_dirs(template_path)
    return _list_sorted_files(styles_dir, "*.css")


def list_gerrit_ui_js_paths(template_path: Path) -> tuple[Path, ...]:
    """Returns ordered modular `.js` files for the Gerrit UI."""
    _, scripts_dir, _, _ = resolve_gerrit_ui_asset_dirs(template_path)
    return _list_sorted_files(scripts_dir, "*.js")


def list_gerrit_ui_partial_paths(template_path: Path) -> tuple[Path, ...]:
    """Returns ordered modular `.html` partial files for the Gerrit UI."""
    _, _, partials_dir, _ = resolve_gerrit_ui_asset_dirs(template_path)
    return _list_sorted_files(partials_dir, "*.html")


def get_gerrit_ui_react_bundle_path(template_path: Path) -> Path:
    """Returns the path to the compiled React bundle sibling to `template_path`."""
    _, _, _, react_bundle_path = resolve_gerrit_ui_asset_dirs(template_path)
    return react_bundle_path


def list_gerrit_ui_watched_paths(template_path: Path) -> tuple[Path, ...]:
    """Returns all UI asset files (template, CSS, partials, React bundle, JS) for watching/hashing."""
    paths: list[Path] = [template_path]
    react_bundle = get_gerrit_ui_react_bundle_path(template_path)
    if react_bundle.is_file():
        paths.append(react_bundle)
    paths.extend(list_gerrit_ui_css_paths(template_path))
    paths.extend(list_gerrit_ui_partial_paths(template_path))
    paths.extend(list_gerrit_ui_js_paths(template_path))
    return tuple(paths)


def compute_gerrit_ui_assets_version(
    template_path: Path,
    length: int = ASSET_VERSION_HEX_LENGTH,
) -> str:
    """Computes a short SHA-256 hex digest over the HTML shell and all modular UI assets."""
    if not template_path.is_file():
        return "0"
    hasher = hashlib.sha256()
    watched = list_gerrit_ui_watched_paths(template_path)
    try:
        for asset_path in watched:
            hasher.update(asset_path.name.encode("utf-8"))
            hasher.update(b"\x00")
            hasher.update(asset_path.read_bytes())
            hasher.update(b"\x00")
    except OSError:
        return "0"
    return hasher.hexdigest()[:length]


def _concat_text_files(paths: tuple[Path, ...], banner_fn: Callable[[str], str]) -> str:
    """Reads and concatenates UTF-8 text files with section banners."""
    chunks: list[str] = []
    for path in paths:
        content = path.read_text(encoding="utf-8").strip("\n")
        if not content:
            continue
        chunks.append(f"{banner_fn(path.name)}\n{content}")
    return "\n\n".join(chunks)


def load_gerrit_ui_css(template_path: Path) -> str:
    """Loads and concatenates all modular CSS files in deterministic cascade order."""
    return _concat_text_files(
        list_gerrit_ui_css_paths(template_path),
        lambda name: f"/* === styles/{name} === */",
    )


def load_gerrit_ui_react_bundle(template_path: Path) -> str:
    """Loads the compiled React + TypeScript bundle if present on disk."""
    bundle_path = get_gerrit_ui_react_bundle_path(template_path)
    if not bundle_path.is_file():
        return ""
    return bundle_path.read_text(encoding="utf-8").strip()


def load_gerrit_ui_app_js(template_path: Path) -> str:
    """Loads and concatenates all modular application JS files in deterministic load order."""
    return _concat_text_files(
        list_gerrit_ui_js_paths(template_path),
        lambda name: f"// === scripts/{name} ===",
    )


def sanitize_inline_script(script_text: str) -> str:
    """Escapes closing `</script` sequences so JS is safe inside an inline `<script>` tag."""
    return SCRIPT_CLOSE_RE.sub(r"<\/script", script_text)


def expand_html_partials(html: str, template_path: Path) -> str:
    """Replaces `<!-- __GERRIT_UI_PARTIAL:<name>__ -->` markers with `partials/<name>.html`."""
    if "__GERRIT_UI_PARTIAL:" not in html:
        return html
    _, _, partials_dir, _ = resolve_gerrit_ui_asset_dirs(template_path)

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        partial_path = partials_dir / f"{name}.html"
        if not partial_path.is_file():
            raise FileNotFoundError(
                f"Missing Gerrit UI HTML partial '{partial_path}' referenced by "
                f"'{match.group(0)}' in '{template_path}'. Verify src/ui/gerrit/partials/ "
                "and BUILD.bazel data globs."
            )
        return partial_path.read_text(encoding="utf-8").rstrip("\n")

    return PARTIAL_PLACEHOLDER_RE.sub(_replace, html)


def _render_stylesheet_block(template_path: Path, version: str, *, inline_assets: bool) -> str:
    css_paths = list_gerrit_ui_css_paths(template_path)
    if not css_paths:
        return ""
    if not inline_assets:
        return f'<link rel="stylesheet" href="{ASSET_ROUTE_CSS}?v={version}">'
    css_text = load_gerrit_ui_css(template_path)
    return f"<style>\n{css_text}\n</style>"


def _render_react_script_block(template_path: Path, version: str, *, inline_assets: bool) -> str:
    bundle_path = get_gerrit_ui_react_bundle_path(template_path)
    if not bundle_path.is_file():
        return ""
    if not inline_assets:
        return f'<script src="{ASSET_ROUTE_REACT_JS}?v={version}"></script>'
    safe_js = sanitize_inline_script(load_gerrit_ui_react_bundle(template_path))
    return f"<script>\n{safe_js}\n</script>"


def _render_combined_scripts_block(
    template_path: Path, version: str, *, inline_assets: bool
) -> str:
    has_react = get_gerrit_ui_react_bundle_path(template_path).is_file()
    has_app = bool(list_gerrit_ui_js_paths(template_path))
    bootstrap_block = "\n".join(_BOOTSTRAP_INLINE_LINES)

    if not inline_assets:
        tags = [f"<script>\n{bootstrap_block}\n</script>"]
        if has_react:
            tags.append(f'<script src="{ASSET_ROUTE_REACT_JS}?v={version}"></script>')
        if has_app:
            tags.append(f'<script src="{ASSET_ROUTE_APP_JS}?v={version}"></script>')
        return "\n".join(tags)

    parts: list[str] = [bootstrap_block]
    if has_react:
        parts.append(sanitize_inline_script(load_gerrit_ui_react_bundle(template_path)))
    if has_app:
        parts.append(sanitize_inline_script(load_gerrit_ui_app_js(template_path)))
    combined = "\n\n".join(p for p in parts if p)
    return f"<script>\n{combined}\n</script>"


def _render_app_script_block(template_path: Path, version: str, *, inline_assets: bool) -> str:
    if not list_gerrit_ui_js_paths(template_path):
        return ""
    if not inline_assets:
        return f'<script src="{ASSET_ROUTE_APP_JS}?v={version}"></script>'
    safe_js = sanitize_inline_script(load_gerrit_ui_app_js(template_path))
    return f"<script>\n{safe_js}\n</script>"


def assemble_gerrit_ui_html(
    html: str,
    template_path: Path,
    version: str,
    *,
    inline_assets: bool,
) -> str:
    """Expands partial, stylesheet, and script placeholders in `html`.

    When `inline_assets=False` (live HTTP requests), CSS and JS are linked via
    versioned `/assets/...` routes so the browser caches them cleanly. When
    `inline_assets=True` (`--export-static` snapshots and standalone test renders),
    assets are embedded directly into `<style>` and a single `<script>` block.
    """
    rendered = expand_html_partials(html, template_path)
    if STYLESHEET_PLACEHOLDER in rendered:
        rendered = rendered.replace(
            STYLESHEET_PLACEHOLDER,
            _render_stylesheet_block(template_path, version, inline_assets=inline_assets),
            1,
        )
    if SCRIPTS_PLACEHOLDER in rendered:
        rendered = rendered.replace(
            SCRIPTS_PLACEHOLDER,
            _render_combined_scripts_block(template_path, version, inline_assets=inline_assets),
            1,
        )
    if REACT_SCRIPT_PLACEHOLDER in rendered:
        rendered = rendered.replace(
            REACT_SCRIPT_PLACEHOLDER,
            _render_react_script_block(template_path, version, inline_assets=inline_assets),
            1,
        )
    if APP_SCRIPT_PLACEHOLDER in rendered:
        rendered = rendered.replace(
            APP_SCRIPT_PLACEHOLDER,
            _render_app_script_block(template_path, version, inline_assets=inline_assets),
            1,
        )
    return rendered


__all__ = [
    "APP_SCRIPT_PLACEHOLDER",
    "ASSET_ROUTE_APP_JS",
    "ASSET_ROUTE_CSS",
    "ASSET_ROUTE_REACT_JS",
    "IMMUTABLE_ASSET_CACHE_CONTROL",
    "PARTIAL_PLACEHOLDER_RE",
    "REACT_BUNDLE_FILENAME",
    "REACT_SCRIPT_PLACEHOLDER",
    "SCRIPTS_PLACEHOLDER",
    "STYLESHEET_PLACEHOLDER",
    "assemble_gerrit_ui_html",
    "compute_gerrit_ui_assets_version",
    "expand_html_partials",
    "get_gerrit_ui_react_bundle_path",
    "list_gerrit_ui_css_paths",
    "list_gerrit_ui_js_paths",
    "list_gerrit_ui_partial_paths",
    "list_gerrit_ui_watched_paths",
    "load_gerrit_ui_app_js",
    "load_gerrit_ui_css",
    "load_gerrit_ui_react_bundle",
    "resolve_gerrit_ui_asset_dirs",
    "sanitize_inline_script",
]
