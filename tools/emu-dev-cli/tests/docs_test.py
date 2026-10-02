import argparse
import os
from pathlib import Path
import sys
import tempfile
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from commands import docs


class DocsWikiTest(unittest.TestCase):

    def test_doc_targets_include_playbooks_and_architecture(self):
        for expected in (
            "cts-verifier-automation",
            "emu-dev-cli-development",
            "goldfish-architecture",
            "playbook-index",
            "playbook-build-and-tidy",
            "playbook-git-and-gerrit",
            "playbook-threading-and-invariants",
        ):
            self.assertIn(expected, docs.DOC_TARGETS)

    def test_discover_wiki_documents_finds_architecture_and_playbooks(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            arch_dir = root / "hardware" / "generic" / "goldfish" / "emulator" / "plugin" / "display"
            arch_dir.mkdir(parents=True)
            (arch_dir / "BUILD.bazel").write_text("# build\n", encoding="utf-8")
            (arch_dir / "ARCHITECTURE.md").write_text(
                "# Component: Display Plugin\n\n"
                "**Role:** Core Plugin\n"
                "**Location:** `emulator/plugin/display/`\n\n"
                "Handles DisplaySource and VideoRecorder.\n",
                encoding="utf-8",
            )

            playbook_dir = (
                root / "hardware" / "google" / "aemu" / "tools" / "emu-dev-cli" / "docs" / "playbooks"
            )
            playbook_dir.mkdir(parents=True)
            (playbook_dir / "git_and_gerrit.md").write_text(
                "# Git and Gerrit Playbook\n\n"
                "**Role:** Operational Playbook\n"
                "**Location:** `hardware/google/aemu/tools/emu-dev-cli/docs/playbooks/git_and_gerrit.md`\n\n"
                "Always set push.negotiate false before repo upload.\n",
                encoding="utf-8",
            )

            entries = docs.discover_wiki_documents([str(root)])
            rel_paths = [e["relative_path"] for e in entries]
            self.assertIn(
                "hardware/generic/goldfish/emulator/plugin/display/ARCHITECTURE.md",
                rel_paths,
            )
            self.assertIn(
                "hardware/google/aemu/tools/emu-dev-cli/docs/playbooks/git_and_gerrit.md",
                rel_paths,
            )

    def test_search_wiki_documents_ranks_matches(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            arch_dir = root / "hardware" / "generic" / "goldfish" / "emulator" / "plugin" / "display"
            arch_dir.mkdir(parents=True)
            (arch_dir / "ARCHITECTURE.md").write_text(
                "# Component: Display Plugin\n\n"
                "**Role:** Core Plugin\n"
                "**Location:** `emulator/plugin/display/`\n\n"
                "Uses VideoRecorder and WebMEncoder for recording.\n",
                encoding="utf-8",
            )
            pb_dir = (
                root / "hardware" / "google" / "aemu" / "tools" / "emu-dev-cli" / "docs" / "playbooks"
            )
            pb_dir.mkdir(parents=True)
            (pb_dir / "git_and_gerrit.md").write_text(
                "# Git Playbook\n\nSet push.negotiate false.\n",
                encoding="utf-8",
            )

            results = docs.search_wiki_documents("WebMEncoder", [str(root)])
            self.assertEqual(len(results), 1)
            self.assertTrue(
                results[0]["relative_path"].endswith("emulator/plugin/display/ARCHITECTURE.md")
            )

    def test_lint_wiki_documents_detects_stale_location_and_bazel_targets(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            goldfish = root / "hardware" / "generic" / "goldfish"
            valid_pkg = goldfish / "emulator" / "libs" / "async"
            valid_pkg.mkdir(parents=True)
            (valid_pkg / "BUILD.bazel").write_text("# valid build\n", encoding="utf-8")

            # 1. Valid ARCHITECTURE.md
            (valid_pkg / "ARCHITECTURE.md").write_text(
                "# Component: Async\n\n"
                "**Role:** Core Library\n"
                "**Location:** `emulator/libs/async/`\n"
                "**Target:** `@goldfish//emulator/libs/async:async`\n",
                encoding="utf-8",
            )

            # 2. Stale ARCHITECTURE.md with wrong Location and moved Bazel package
            stale_pkg = goldfish / "emulator" / "plugin" / "adb-vsock" / "host"
            stale_pkg.mkdir(parents=True)
            (stale_pkg / "BUILD.bazel").write_text("# build\n", encoding="utf-8")
            (stale_pkg / "ARCHITECTURE.md").write_text(
                "# Component: ADB Host\n\n"
                "**Location:** `emulator/adb/host`\n"
                "**Target:** `//emulator/adb/host:adb_service`\n"
                "See [missing](nonexistent_doc.md) for details.\n",
                encoding="utf-8",
            )

            report = docs.lint_wiki_documents([str(root)])
            self.assertEqual(report["files_checked"], 2)
            self.assertGreaterEqual(len(report["issues"]), 3)
            kinds = {issue["kind"] for issue in report["issues"]}
            self.assertIn("stale_location", kinds)
            self.assertIn("missing_bazel_package", kinds)
            self.assertIn("broken_link", kinds)

    def test_parser_supports_index_search_lint_and_targets(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="subcommand")
        docs.register_parser(subparsers)

        args_index = parser.parse_args(["docs", "index"])
        self.assertEqual(args_index.docs_cmd, "index")

        args_search = parser.parse_args(["docs", "search", "VideoRecorder"])
        self.assertEqual(args_search.docs_cmd, "search")
        self.assertEqual(args_search.query, ["VideoRecorder"])

        args_lint = parser.parse_args(["docs", "lint"])
        self.assertEqual(args_lint.docs_cmd, "lint")

    def test_lint_wiki_documents_handles_workspace_prefixed_labels_canonical_locations_and_shortlinks(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            aemu_pkg = root / "hardware" / "google" / "aemu" / "tools" / "emu-dev-cli"
            aemu_pkg.mkdir(parents=True)
            (aemu_pkg / "BUILD.bazel").write_text("# build\n", encoding="utf-8")
            (aemu_pkg / "DESIGN.md").write_text(
                "# Component: Emu Dev CLI\n\n"
                "**Location:** `hardware/google/aemu/tools/emu-dev-cli/DESIGN.md`\n"
                "**Target:** `//hardware/google/aemu/tools/emu-dev-cli:emu-dev-cli`\n"
                "Links: [Bug](b/123456), [AB](go/ab), [CS](cs/foo), [G3doc](g3doc/bar.md)\n",
                encoding="utf-8",
            )

            # Test both workspace root and standalone repo root (where workspace_root == repo_root)
            report_ws = docs.lint_wiki_documents([str(root)])
            self.assertEqual(report_ws["issues"], [])

            standalone_root = root / "hardware" / "google" / "aemu"
            report_standalone = docs.lint_wiki_documents([str(standalone_root)])
            self.assertEqual(report_standalone["issues"], [])


if __name__ == "__main__":
    unittest.main()
