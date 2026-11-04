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

"""Tests that every consumer of goldfish library labels uses the canonical catalog."""

import contextlib
import importlib.util
import io
from pathlib import Path
import re
import shutil
import tempfile
from types import ModuleType
import unittest

from commands.harness.diagnostics import diagnose_command_failure
from commands.harness.goldfish_libs import (
    GOLDFISH_HEADER_TO_TARGET,
    REUSABLE_LIBS_CATALOG,
    target_for_include,
)
from commands.harness.mcp_server import search_reusable_libs
from commands.harness.plugin_installer import install_harness_plugin
from commands.harness.review_swarm import REVIEW_SWARM_SPECIALISTS

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "harness" / "plugin"
SIDECAR_RELATIVE_DIR = Path("sidecars") / "aemu_harness_ui"
RULES_DIR = PLUGIN_DIR / "rules"
# Package labels without a default target. Agents must never be told to depend on them.
NONEXISTENT_LABELS = frozenset({"//emulator/libs/http", "//emulator/libs/async"})
# Headers the harness may recommend: catalog headers and exact entries of the table.
KNOWN_HEADERS = frozenset(
    {entry["header"] for entry in REUSABLE_LIBS_CATALOG}
    | {prefix for prefix, _ in GOLDFISH_HEADER_TO_TARGET if prefix.endswith(".h")}
)


def _load_sidecar(sidecar_dir: Path) -> ModuleType:
    """Imports the AuxPane sidecar `server.py` from `sidecar_dir`."""
    server_py = sidecar_dir / "server.py"
    spec = importlib.util.spec_from_file_location("aemu_harness_sidecar_under_test", server_py)
    assert spec is not None and spec.loader is not None, f"cannot load {server_py}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GoldfishHeaderTableTest(unittest.TestCase):
    """Tests the canonical include-path to Bazel target table."""

    def test_most_specific_prefix_wins(self) -> None:
        self.assertEqual(
            target_for_include("goldfish/async/threaded_event_loop.h"),
            "//emulator/libs/async:threaded_event_loop",
        )
        self.assertEqual(
            target_for_include("goldfish/async/async_socket.h"), "//emulator/libs/async:async_api"
        )
        self.assertEqual(
            target_for_include("goldfish/http/web_server.h"), "//emulator/libs/http:web_server"
        )
        self.assertIsNone(target_for_include("uv.h"))

    def test_no_entry_is_shadowed_by_an_earlier_prefix(self) -> None:
        for prefix, target in GOLDFISH_HEADER_TO_TARGET:
            self.assertEqual(
                target_for_include(prefix),
                target,
                msg=f"'{prefix}' is shadowed by an earlier, broader prefix",
            )

    def test_diagnostics_use_the_shared_table(self) -> None:
        log = "x.cc:1:10: fatal error: 'goldfish/async/uv_to_absl.h' file not found"
        diagnostic = diagnose_command_failure("bazel build //x:y", log)
        self.assertIsNotNone(diagnostic)
        self.assertIn("`//emulator/libs/async:uv_to_absl`", diagnostic)


class ReusableLibsCatalogTest(unittest.TestCase):
    """Tests that the catalog and every copy of it point to targets that exist."""

    def test_catalog_targets_come_from_the_shared_table(self) -> None:
        for entry in REUSABLE_LIBS_CATALOG:
            self.assertEqual(
                entry["bazel_target"], target_for_include(entry["header"]), msg=entry["name"]
            )
            self.assertNotIn(entry["bazel_target"], NONEXISTENT_LABELS, msg=entry["name"])

    def test_search_returns_one_entry_per_async_target(self) -> None:
        targets = {lib["bazel_target"] for lib in search_reusable_libs("async")}
        self.assertTrue(
            {
                "//emulator/libs/async:async_api",
                "//emulator/libs/async:threaded_event_loop",
                "//emulator/libs/async:libuv_event_loop",
                "//emulator/libs/async:uv_to_absl",
            }.issubset(targets),
            msg=str(targets),
        )
        self.assertTrue(targets.isdisjoint(NONEXISTENT_LABELS), msg=str(targets))

    def test_installed_sidecar_serves_the_rendered_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "aemu-harness_测试"
            report = install_harness_plugin(
                target_dir=plugin_dir, source_plugin_dir=PLUGIN_DIR, cli_command="emu-dev-cli"
            )
            self.assertTrue(report["installed"], msg=str(report))
            sidecar = _load_sidecar(plugin_dir / SIDECAR_RELATIVE_DIR)
        self.assertEqual(sidecar.SIDE_CAR_LIBS_CATALOG, REUSABLE_LIBS_CATALOG)

    def test_sidecar_without_a_rendered_catalog_falls_back_to_empty(self) -> None:
        # The bundled file is only a placeholder, so no hand-maintained copy can drift.
        self.assertEqual(_load_sidecar(PLUGIN_DIR / SIDECAR_RELATIVE_DIR).SIDE_CAR_LIBS_CATALOG, [])

        with tempfile.TemporaryDirectory() as tmp:
            sidecar_dir = Path(tmp)
            shutil.copy2(PLUGIN_DIR / SIDECAR_RELATIVE_DIR / "server.py", sidecar_dir)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                sidecar = _load_sidecar(sidecar_dir)
        self.assertEqual(sidecar.SIDE_CAR_LIBS_CATALOG, [])
        self.assertIn("libs_catalog.json", stderr.getvalue())
        self.assertIn("emu-dev-cli harness install", stderr.getvalue())

    def test_rules_and_review_prompts_recommend_only_known_labels(self) -> None:
        texts = {path.name: path.read_text(encoding="utf-8") for path in RULES_DIR.glob("*.md")}
        self.assertIn("goldfish-library-reuse.md", texts)
        texts["review_swarm prompts"] = "\n".join(spec["focus"] for spec in REVIEW_SWARM_SPECIALISTS)
        for name, text in texts.items():
            for label in NONEXISTENT_LABELS:
                self.assertNotRegex(text, re.escape(label) + r"(?![:\w/])", msg=name)
            for header in re.findall(r"(?<![\w/])goldfish/[\w/]+\.h\b", text):
                self.assertIn(header, KNOWN_HEADERS, msg=f"{name}: {header}")


if __name__ == "__main__":
    unittest.main()
