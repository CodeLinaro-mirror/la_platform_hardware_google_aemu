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
"""Syncs the compiled React + TypeScript bundle for AEMU Fast Gerrit UI."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

BEGIN_BUNDLE_MARKER = "/* __BEGIN_AEMU_GERRIT_REACT_BUNDLE__ */"
END_BUNDLE_MARKER = "/* __END_AEMU_GERRIT_REACT_BUNDLE__ */"
SCRIPT_CLOSE_PATTERN = re.compile(r"</script", re.IGNORECASE)
BUNDLE_FILE_HEADER = (
    "// AUTO-GENERATED from src/ui/gerrit/*.tsx via "
    "'bazel build @aemu//tools/emu-dev-cli:gerrit_ui_bundle'.\n"
    "// Do not edit manually; run src/ui/gerrit/build_bundle.py to refresh.\n"
)


def sanitize_inline_bundle(bundle_js: str) -> str:
    """Escapes any closing script sequences so the bundle is safe inside <script>."""
    return SCRIPT_CLOSE_PATTERN.sub(r"<\/script", bundle_js.strip())


def format_standalone_bundle(bundle_js: str) -> str:
    """Wraps compiled React JS with a generated-file header and trailing newline."""
    return f"{BUNDLE_FILE_HEADER}{bundle_js.strip()}\n"


def inject_bundle_into_template(template_text: str, bundle_js: str) -> str:
    """Replaces the React bundle section inside gerrit_ui_polygerrit.html."""
    safe_bundle = sanitize_inline_bundle(bundle_js)
    replacement_block = (
        f"{BEGIN_BUNDLE_MARKER}\n  {safe_bundle}\n  {END_BUNDLE_MARKER}"
    )
    if BEGIN_BUNDLE_MARKER in template_text and END_BUNDLE_MARKER in template_text:
        start_idx = template_text.index(BEGIN_BUNDLE_MARKER)
        end_idx = template_text.index(END_BUNDLE_MARKER) + len(END_BUNDLE_MARKER)
        return (
            template_text[:start_idx]
            + replacement_block
            + template_text[end_idx:]
        )

    script_tag = "<script>\n"
    if script_tag not in template_text:
        script_tag = "<script>\r\n"
    if script_tag not in template_text:
        raise ValueError(
            "Failed to inject Gerrit React bundle into template: missing <script> tag "
            f"and markers ({BEGIN_BUNDLE_MARKER}). Verify gerrit_ui_polygerrit.html."
        )
    return template_text.replace(
        script_tag,
        f"{script_tag}  {replacement_block}\n",
        1,
    )


def _sync_standalone_output(bundle_js: str, out_path: Path, *, check_only: bool) -> int:
    expected = format_standalone_bundle(bundle_js)
    if check_only:
        current = out_path.read_text(encoding="utf-8") if out_path.is_file() else ""
        if current != expected:
            sys.stderr.write(
                f"Error: '{out_path}' is out of sync with src/ui/gerrit/*.tsx. "
                "Run build_bundle.py --bundle <built> --out <path> to update.\n"
            )
            return 1
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(expected, encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle",
        type=Path,
        required=True,
        help="Path to the esbuild-compiled gerrit_ui.bundle.js file.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Path to standalone checked-in src/templates/gerrit_ui_react.bundle.js.",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=None,
        help="Legacy path to src/templates/gerrit_ui_polygerrit.html for inline injection.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify that the target bundle or template is already up to date.",
    )
    args = parser.parse_args(argv)

    if not args.bundle.is_file():
        raise FileNotFoundError(
            f"Cannot sync Gerrit React bundle: bundle file '{args.bundle}' does not "
            "exist. Run 'bazel build @aemu//tools/emu-dev-cli:gerrit_ui_bundle' first."
        )
    if args.out is None and args.template is None:
        raise ValueError("Specify either --out <bundle_path> or --template <html_path>.")

    bundle_js = args.bundle.read_text(encoding="utf-8")
    if args.out is not None:
        return _sync_standalone_output(bundle_js, args.out, check_only=args.check)

    if not args.template.is_file():
        raise FileNotFoundError(
            f"Cannot inject Gerrit React bundle: template '{args.template}' does not "
            "exist. Check the --template path."
        )
    current_html = args.template.read_text(encoding="utf-8")
    updated_html = inject_bundle_into_template(current_html, bundle_js)

    if args.check:
        if current_html != updated_html:
            sys.stderr.write(
                "Error: gerrit_ui_polygerrit.html embedded React bundle is out of sync "
                "with src/ui/gerrit/*.tsx. Run build_bundle.py to update.\n"
            )
            return 1
        return 0

    args.template.write_text(updated_html, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
