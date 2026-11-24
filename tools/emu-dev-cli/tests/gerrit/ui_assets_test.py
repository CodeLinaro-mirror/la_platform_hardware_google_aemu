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

"""Unit tests for modular Gerrit UI static asset assembly, HTTP serving, and packaging."""

from __future__ import annotations

from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
import urllib.request

from commands.gerrit.handlers.ui_handler import (
    make_gerrit_ui_handler,
    render_static_gerrit_ui_html,
)
from commands.gerrit.services.diff_parser import get_gerrit_ui_template_path
from commands.gerrit.services.ui_assets import (
    ASSET_ROUTE_APP_JS,
    ASSET_ROUTE_CSS,
    ASSET_ROUTE_REACT_JS,
    IMMUTABLE_ASSET_CACHE_CONTROL,
    REACT_BUNDLE_FILENAME,
    SCRIPTS_PLACEHOLDER,
    STYLESHEET_PLACEHOLDER,
    assemble_gerrit_ui_html,
    compute_gerrit_ui_assets_version,
    get_gerrit_ui_react_bundle_path,
    list_gerrit_ui_css_paths,
    list_gerrit_ui_js_paths,
    list_gerrit_ui_partial_paths,
    load_gerrit_ui_app_js,
    load_gerrit_ui_css,
    load_gerrit_ui_react_bundle,
)
from commands.gerrit.sidecar_updater import collect_installed_gerrit_blobs
from core.web.handler import CONTENT_TYPE_CSS, CONTENT_TYPE_JS
from install.installer import copy_src_to_release_lib


def _resolve_node_bin(test_case: unittest.TestCase) -> str | None:
    node_env = os.environ.get("NODE_BIN", "").strip()
    test_srcdir = os.environ.get("TEST_SRCDIR", "").strip()
    if node_env:
        candidates = [node_env]
        if test_srcdir:
            rel = node_env[len("external/") :] if node_env.startswith("external/") else node_env
            candidates.insert(0, os.path.join(test_srcdir, rel))
            candidates.append(os.path.join(test_srcdir, "_main", node_env))
        for cand in candidates:
            if os.path.isfile(cand):
                return os.path.abspath(cand)
        test_case.fail(
            f"Failed to resolve hermetic Node binary from NODE_BIN={node_env!r} "
            f"(TEST_SRCDIR={test_srcdir!r})."
        )
    return shutil.which("node") or ("/usr/bin/node" if os.path.exists("/usr/bin/node") else None)


def _check_css_brace_balance(css_text: str) -> tuple[int, int, str]:
    """Returns `(final_depth, min_depth, error_detail)` ignoring CSS comments and strings."""
    depth = 0
    min_depth = 0
    in_comment = False
    quote: str | None = None
    i = 0
    length = len(css_text)
    while i < length:
        ch = css_text[i]
        if in_comment:
            if ch == "*" and i + 1 < length and css_text[i + 1] == "/":
                in_comment = False
                i += 2
                continue
            i += 1
            continue
        if quote:
            if ch == "\\" and i + 1 < length:
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == "/" and i + 1 < length and css_text[i + 1] == "*":
            in_comment = True
            i += 2
            continue
        if ch in ('"', "'"):
            quote = ch
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < min_depth:
                min_depth = depth
        i += 1
    if in_comment:
        return depth, min_depth, "unclosed CSS comment"
    if quote is not None:
        return depth, min_depth, f"unclosed CSS string ({quote})"
    return depth, min_depth, ""


class GerritUiAssetsTest(unittest.TestCase):
    """Verifies modular CSS/JS/partial assembly, version hashing, and HTTP static routes."""

    def _create_mock_src_tree(self, root: Path) -> Path:
        src_dir = root / "src"
        tpl_dir = src_dir / "templates"
        styles_dir = src_dir / "ui" / "gerrit" / "styles"
        scripts_dir = src_dir / "ui" / "gerrit" / "scripts"
        partials_dir = src_dir / "ui" / "gerrit" / "partials"
        for d in (tpl_dir, styles_dir, scripts_dir, partials_dir):
            d.mkdir(parents=True, exist_ok=True)

        tpl_path = tpl_dir / "gerrit_ui_polygerrit.html"
        tpl_path.write_text(
            "<!DOCTYPE html>\n<html><head>\n"
            "<script src=\"/preload.js\"></script>\n"
            f"{STYLESHEET_PLACEHOLDER}\n"
            "</head><body>\n"
            "<!-- __GERRIT_UI_PARTIAL:dialogs__ -->\n"
            f"{SCRIPTS_PLACEHOLDER}\n"
            "</body></html>\n",
            encoding="utf-8",
        )
        (tpl_dir / REACT_BUNDLE_FILENAME).write_text(
            "window.__AEMU_GERRIT_REACT__ = { label: '测试_🚀_é' };\n",
            encoding="utf-8",
        )
        (styles_dir / "00_tokens.css").write_text(":root { --pg-accent: #1a73e8; }\n", encoding="utf-8")
        (styles_dir / "01_layout.css").write_text(".pg-shell { color: var(--pg-accent); /* 测试_🚀_é */ }\n", encoding="utf-8")
        (scripts_dir / "00_state.js").write_text("const state = { tag: '测试_🚀_é' };\n", encoding="utf-8")
        (scripts_dir / "01_app.js").write_text("function bootApp() { return state.tag; }\n", encoding="utf-8")
        (partials_dir / "dialogs.html").write_text("<div id=\"pgDialogs\">测试_🚀_é</div>\n", encoding="utf-8")
        return tpl_path

    def test_assemble_inline_vs_linked_and_version_invalidation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gerrit_ui_测试_") as tmp:
            tpl_path = self._create_mock_src_tree(Path(tmp))
            v1 = compute_gerrit_ui_assets_version(tpl_path)
            self.assertEqual(len(v1), 12)
            self.assertNotEqual(v1, "0")

            raw = tpl_path.read_text(encoding="utf-8")
            linked = assemble_gerrit_ui_html(raw, tpl_path, v1, inline_assets=False)
            self.assertIn(f'<link rel="stylesheet" href="{ASSET_ROUTE_CSS}?v={v1}">', linked)
            self.assertIn(f'<script src="{ASSET_ROUTE_REACT_JS}?v={v1}"></script>', linked)
            self.assertIn(f'<script src="{ASSET_ROUTE_APP_JS}?v={v1}"></script>', linked)
            self.assertIn('<div id="pgDialogs">测试_🚀_é</div>', linked)
            self.assertNotIn("<style>", linked)

            inlined = assemble_gerrit_ui_html(raw, tpl_path, v1, inline_assets=True)
            self.assertIn("<style>", inlined)
            self.assertIn("--pg-accent: #1a73e8;", inlined)
            self.assertIn("window.__AEMU_GERRIT_REACT__", inlined)
            self.assertIn("function bootApp()", inlined)
            self.assertNotIn(ASSET_ROUTE_CSS, inlined)

            # Updating a modular CSS file must change the computed UI version hash.
            (tpl_path.parent.parent / "ui" / "gerrit" / "styles" / "01_layout.css").write_text(
                ".pg-shell { color: #fff; }\n", encoding="utf-8"
            )
            v2 = compute_gerrit_ui_assets_version(tpl_path)
            self.assertNotEqual(v1, v2)

    def test_missing_partial_raises_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tpl_path = Path(tmp) / "src" / "templates" / "gerrit_ui_polygerrit.html"
            tpl_path.parent.mkdir(parents=True, exist_ok=True)
            tpl_path.write_text("<!-- __GERRIT_UI_PARTIAL:nonexistent__ -->", encoding="utf-8")
            with self.assertRaises(FileNotFoundError) as ctx:
                assemble_gerrit_ui_html(
                    tpl_path.read_text(encoding="utf-8"), tpl_path, "abc123456789", inline_assets=False
                )
            self.assertIn("nonexistent.html", str(ctx.exception))
            self.assertIn("BUILD.bazel", str(ctx.exception))

    def test_http_static_asset_routes_and_immutable_cache_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tpl_path = self._create_mock_src_tree(Path(tmp))
            expected_v = compute_gerrit_ui_assets_version(tpl_path)
            mock_service = MagicMock()
            mock_service.default_viewer = "dev@google.com"

            with patch(
                "commands.gerrit.handlers.ui_handler.get_gerrit_ui_template_path",
                return_value=tpl_path,
            ), patch(
                "commands.gerrit.handlers.ui_handler.get_gerrit_ui_template_version",
                return_value=expected_v,
            ):
                handler_cls = make_gerrit_ui_handler(mock_service, ui_token="tok-secret")
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
                port = httpd.server_address[1]
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                try:
                    # 1. Authenticated index serves lightweight linked HTML shell.
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/?token=tok-secret", timeout=5
                    ) as resp:
                        index_html = resp.read().decode("utf-8")
                    self.assertIn(f"{ASSET_ROUTE_CSS}?v={expected_v}", index_html)
                    self.assertIn(f"{ASSET_ROUTE_REACT_JS}?v={expected_v}", index_html)
                    self.assertIn(f"{ASSET_ROUTE_APP_JS}?v={expected_v}", index_html)
                    self.assertIn('"viewer": "dev@google.com"', index_html)

                    # 2. Static asset routes are public (no token needed) and set immutable cache on version match.
                    for route, expected_ct, expected_snippet in (
                        (ASSET_ROUTE_CSS, CONTENT_TYPE_CSS, "--pg-accent"),
                        (ASSET_ROUTE_REACT_JS, CONTENT_TYPE_JS, "__AEMU_GERRIT_REACT__"),
                        (ASSET_ROUTE_APP_JS, CONTENT_TYPE_JS, "bootApp"),
                    ):
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}{route}?v={expected_v}", timeout=5
                        ) as resp:
                            body = resp.read().decode("utf-8")
                            self.assertEqual(resp.headers.get("Content-Type"), expected_ct)
                            self.assertEqual(
                                resp.headers.get("Cache-Control"), IMMUTABLE_ASSET_CACHE_CONTROL
                            )
                            self.assertIn(expected_snippet, body)
                            self.assertIn("测试_🚀_é", body)

                        # Mismatched or absent `?v=` falls back to `no-store`.
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}{route}?v=stale", timeout=5
                        ) as resp:
                            self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
                finally:
                    httpd.shutdown()
                    httpd.server_close()

    def test_installer_copies_css_js_and_partial_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tpl_path = self._create_mock_src_tree(root)
            src_dir = tpl_path.parent.parent
            release_lib = root / "release_lib"
            copy_src_to_release_lib(str(src_dir), str(release_lib))

            copied_tpl = release_lib / "templates" / "gerrit_ui_polygerrit.html"
            self.assertTrue(copied_tpl.is_file())
            self.assertIn("--pg-accent", load_gerrit_ui_css(copied_tpl))
            self.assertIn("__AEMU_GERRIT_REACT__", load_gerrit_ui_react_bundle(copied_tpl))
            self.assertIn("bootApp", load_gerrit_ui_app_js(copied_tpl))

    def test_render_static_gerrit_ui_html_default_inlines_for_standalone_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tpl_path = self._create_mock_src_tree(Path(tmp))
            with patch(
                "commands.gerrit.handlers.ui_handler.get_gerrit_ui_template_path",
                return_value=tpl_path,
            ), patch(
                "commands.gerrit.handlers.ui_handler.get_gerrit_ui_template_version",
                return_value=compute_gerrit_ui_assets_version(tpl_path),
            ):
                standalone = render_static_gerrit_ui_html({"viewer": "测试_🚀_é@google.com"})
                self.assertIn("<style>", standalone)
                self.assertIn("bootApp", standalone)
                self.assertIn("测试_🚀_é@google.com", standalone)

    def test_checked_in_react_bundle_is_multiline_and_under_readline_limit(self) -> None:
        tpl_path = get_gerrit_ui_template_path("polygerrit")
        bundle_js = load_gerrit_ui_react_bundle(tpl_path)
        self.assertIn("AemuGerritReact", bundle_js)
        lines = bundle_js.splitlines()
        self.assertGreater(len(lines), 100)
        max_line_len = max(len(line) for line in lines)
        # Keep every line well below Python asyncio StreamReader's 64 KiB readline limit.
        self.assertLess(max_line_len, 16384)

    def test_polygerrit_shell_stays_minimal_and_modular(self) -> None:
        """Guards against re-growing gerrit_ui_polygerrit.html into a monolith."""
        tpl_path = get_gerrit_ui_template_path("polygerrit")
        raw_html = tpl_path.read_text(encoding="utf-8")
        self.assertLess(len(raw_html.splitlines()), 100)
        self.assertNotIn("<style>", raw_html)
        self.assertIn(STYLESHEET_PLACEHOLDER, raw_html)
        self.assertIn(SCRIPTS_PLACEHOLDER, raw_html)

        css_files = list_gerrit_ui_css_paths(tpl_path)
        partial_files = list_gerrit_ui_partial_paths(tpl_path)
        js_files = list_gerrit_ui_js_paths(tpl_path)
        self.assertGreaterEqual(len(css_files), 16)
        self.assertGreaterEqual(len(partial_files), 5)
        self.assertGreaterEqual(len(js_files), 15)

        linked_html = render_static_gerrit_ui_html(
            {"items": [], "stacks": [], "total_open": 0},
            variant="polygerrit",
            inline_assets=False,
        )
        # Live HTTP shell expands HTML partials (~110 KB) but links CSS/React/JS externally.
        self.assertLess(len(linked_html.encode("utf-8")), 200_000)
        self.assertIn(f"{ASSET_ROUTE_CSS}?v=", linked_html)
        self.assertIn(f"{ASSET_ROUTE_REACT_JS}?v=", linked_html)
        self.assertIn(f"{ASSET_ROUTE_APP_JS}?v=", linked_html)
        self.assertNotIn("<style>", linked_html)

    def test_all_modular_css_files_are_strictly_brace_balanced(self) -> None:
        """Verifies every modular CSS stylesheet has balanced braces and closed strings/comments."""
        tpl_path = get_gerrit_ui_template_path("polygerrit")
        css_files = list_gerrit_ui_css_paths(tpl_path)
        self.assertTrue(css_files)
        for css_path in css_files:
            depth, min_depth, err = _check_css_brace_balance(css_path.read_text(encoding="utf-8"))
            self.assertEqual(
                (depth, min_depth, err),
                (0, 0, ""),
                f"CSS syntax/brace imbalance in {css_path.name}: depth={depth}, min={min_depth}, err={err!r}",
            )

    def test_all_modular_js_files_pass_node_syntax_check(self) -> None:
        """Verifies each modular JS file, the React bundle, and concatenated app JS parse cleanly."""
        node_bin = _resolve_node_bin(self)
        if not node_bin:
            self.skipTest("Node binary unavailable outside Bazel sandbox")
        tpl_path = get_gerrit_ui_template_path("polygerrit")
        js_targets = list(list_gerrit_ui_js_paths(tpl_path))
        js_targets.append(get_gerrit_ui_react_bundle_path(tpl_path))

        for js_path in js_targets:
            proc = subprocess.run(
                [node_bin, "--check", str(js_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(
                proc.returncode,
                0,
                f"Node syntax check failed for {js_path.name}: {proc.stderr or proc.stdout}",
            )

        with tempfile.TemporaryDirectory(prefix="gerrit_ui_js_测试_") as tmp:
            combined_path = Path(tmp) / "combined_app.js"
            combined_path.write_text(load_gerrit_ui_app_js(tpl_path), encoding="utf-8")
            proc = subprocess.run(
                [node_bin, "--check", str(combined_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(
                proc.returncode,
                0,
                f"Node syntax check failed for concatenated app JS: {proc.stderr or proc.stdout}",
            )

    def test_sidecar_updater_tracks_modular_ui_assets(self) -> None:
        """Verifies sidecar updater hashes modular CSS, JS, partials, and React bundle."""
        tpl_path = get_gerrit_ui_template_path("polygerrit")
        src_root = tpl_path.parent.parent
        blobs = collect_installed_gerrit_blobs(src_root)
        self.assertIn("tools/emu-dev-cli/src/templates/gerrit_ui_polygerrit.html", blobs)
        self.assertIn("tools/emu-dev-cli/src/templates/gerrit_ui_react.bundle.js", blobs)
        self.assertIn("tools/emu-dev-cli/src/ui/gerrit/styles/00_tokens.css", blobs)
        self.assertIn("tools/emu-dev-cli/src/ui/gerrit/styles/15_pg_code_viewer.css", blobs)
        self.assertIn("tools/emu-dev-cli/src/ui/gerrit/partials/workspace.html", blobs)
        self.assertIn("tools/emu-dev-cli/src/ui/gerrit/scripts/00_state_and_prefs.js", blobs)
        self.assertIn(
            "tools/emu-dev-cli/src/ui/gerrit/scripts/14_shortcuts_scroll_code_viewer_and_boot.js",
            blobs,
        )


if __name__ == "__main__":
    unittest.main()
