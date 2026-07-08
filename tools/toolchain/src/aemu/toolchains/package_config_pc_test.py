# -*- coding: utf-8 -*-
# Copyright 2023 - The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the',  help='License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an',  help='AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from aemu.toolchains.package_config_pc import PackageConfigPc


class PackageConfigPcTest(unittest.TestCase):

    def test_persist_archive(self):
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            workspace = tmp_path / "workspace"
            workspace.mkdir()

            # Create a dummy archive outside the workspace (to ensure it gets persisted)
            outside = tmp_path / "outside"
            outside.mkdir()
            archive = outside / "libfoo.a"
            archive.write_text("dummy archive content")

            packages_dir = tmp_path / "packages"

            cfg = PackageConfigPc(
                name="foo",
                version="1.0",
                release_dir=tmp_path / "release",
                archives=[archive],
                includes=None,
                shim={},
                target="//foo:foo",
            )

            cfg.persist(packages_dir, workspace)

            # Check if archive was persisted
            persisted_archive = packages_dir / "foo" / "lib" / "libfoo.a"
            self.assertTrue(persisted_archive.exists())
            self.assertEqual(
                persisted_archive.read_text(), "dummy archive content"
            )

            # Check if state was updated
            self.assertEqual(cfg.archives[0], persisted_archive)
            self.assertEqual(
                cfg.libdir, (packages_dir / "foo" / "lib").as_posix()
            )
            self.assertIn("-lfoo", cfg.libs)
            self.assertIn("-L${libdir}", cfg.libs)

    def test_persist_includes(self):
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            workspace = tmp_path / "workspace"
            workspace.mkdir()

            # Create dummy includes outside the workspace
            outside = tmp_path / "outside"
            outside.mkdir()
            archive = outside / "libfoo.a"
            archive.write_text("dummy")
            inc1 = outside / "include1"
            inc1.mkdir()
            (inc1 / "foo.h").write_text("foo.h content")

            inc2 = outside / "include2"
            inc2.mkdir()
            (inc2 / "bar.h").write_text("bar.h content")

            packages_dir = tmp_path / "packages"

            cfg = PackageConfigPc(
                name="foo",
                version="1.0",
                release_dir=tmp_path / "release",
                archives=[archive],
                includes={inc1, inc2},
                shim={},
                target="//foo:foo",
            )

            cfg.persist(packages_dir, workspace)

            # Check if includes were persisted
            persisted_inc_root = packages_dir / "foo" / "include"
            self.assertTrue(
                (persisted_inc_root / "0" / "foo.h").exists()
                or (persisted_inc_root / "1" / "foo.h").exists()
            )
            self.assertTrue(
                (persisted_inc_root / "0" / "bar.h").exists()
                or (persisted_inc_root / "1" / "bar.h").exists()
            )

            # Check if cflags was updated
            self.assertIn("-I${includedir}/0", cfg.cflags)
            self.assertIn("-I${includedir}/1", cfg.cflags)

    def test_persist_inside_workspace_uses_hardlink(self):
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            workspace = tmp_path / "workspace"
            workspace.mkdir()

            # Create dummy archive inside the workspace
            archive = workspace / "libfoo.a"
            archive.write_text("dummy archive content")

            packages_dir = tmp_path / "packages"

            cfg = PackageConfigPc(
                name="foo",
                version="1.0",
                release_dir=tmp_path / "release",
                archives=[archive],
                includes=None,
                shim={},
                target="//foo:foo",
            )

            cfg.persist(packages_dir, workspace)

            # Check that archive WAS persisted (as a hardlink)
            persisted_archive = packages_dir / "foo" / "lib" / "libfoo.a"
            self.assertTrue(persisted_archive.exists())
            self.assertEqual(
                persisted_archive.read_text(), "dummy archive content"
            )
            self.assertFalse(persisted_archive.is_symlink())
            self.assertEqual(cfg.archives[0], persisted_archive)

    def test_get_lib_name(self):
        from aemu.toolchains.package_config_pc import _get_lib_name

        self.assertEqual(_get_lib_name(Path("libpulse.so.0.15.3")), "pulse")
        self.assertEqual(_get_lib_name(Path("libasound.so")), "asound")
        self.assertEqual(_get_lib_name(Path("libfoo.a")), "foo")
        self.assertEqual(_get_lib_name(Path("libbar.dylib")), "bar")
        self.assertEqual(_get_lib_name(Path("libbaz.dll")), "baz")
        self.assertEqual(_get_lib_name(Path("foo.so")), "foo")
        self.assertEqual(_get_lib_name(Path("libfoo.so.1")), "foo")
        self.assertEqual(_get_lib_name(Path("libpulse.so.0")), "pulse")

    def test_persist_selective_headers_single_include(self):
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            workspace = tmp_path / "workspace"
            workspace.mkdir()

            outside = tmp_path / "outside"
            outside.mkdir()
            archive = outside / "libfoo.a"
            archive.write_text("dummy")

            inc = outside / "include"
            inc.mkdir()
            h1 = inc / "foo.h"
            h1.write_text("foo.h")
            h2 = inc / "bar.h"
            h2.write_text("bar.h")
            h3 = inc / "baz.h"
            h3.write_text("baz.h")

            packages_dir = tmp_path / "packages"

            cfg = PackageConfigPc(
                name="foo",
                version="1.0",
                release_dir=tmp_path / "release",
                archives=[archive],
                includes={inc},
                shim={},
                target="//foo:foo",
                headers=[h1, h2],
            )

            cfg.persist(packages_dir, workspace)

            persisted_inc_root = packages_dir / "foo" / "include"
            self.assertTrue((persisted_inc_root / "foo.h").exists())
            self.assertTrue((persisted_inc_root / "bar.h").exists())
            self.assertFalse((persisted_inc_root / "baz.h").exists())

            self.assertEqual(cfg.cflags, f"-I${{includedir}}")

    def test_persist_selective_headers_multiple_includes(self):
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            workspace = tmp_path / "workspace"
            workspace.mkdir()

            outside = tmp_path / "outside"
            outside.mkdir()
            archive = outside / "libfoo.a"
            archive.write_text("dummy")

            inc1 = outside / "include1"
            inc1.mkdir()
            h1 = inc1 / "foo.h"
            h1.write_text("foo.h")
            h2 = inc1 / "filtered1.h"
            h2.write_text("filtered1.h")

            inc2 = outside / "include2"
            inc2.mkdir()
            h3 = inc2 / "bar.h"
            h3.write_text("bar.h")
            h4 = inc2 / "filtered2.h"
            h4.write_text("filtered2.h")

            packages_dir = tmp_path / "packages"

            cfg = PackageConfigPc(
                name="foo",
                version="1.0",
                release_dir=tmp_path / "release",
                archives=[archive],
                includes={inc1, inc2},
                shim={},
                target="//foo:foo",
                headers=[h1, h3],
            )

            cfg.persist(packages_dir, workspace)

            persisted_inc_root = packages_dir / "foo" / "include"

            idx_foo = (
                "0" if (persisted_inc_root / "0" / "foo.h").exists() else "1"
            )
            idx_bar = "1" if idx_foo == "0" else "0"

            self.assertTrue((persisted_inc_root / idx_foo / "foo.h").exists())
            self.assertFalse(
                (persisted_inc_root / idx_foo / "filtered1.h").exists()
            )

            self.assertTrue((persisted_inc_root / idx_bar / "bar.h").exists())
            self.assertFalse(
                (persisted_inc_root / idx_bar / "filtered2.h").exists()
            )

            self.assertIn("-I${includedir}/0", cfg.cflags)
            self.assertIn("-I${includedir}/1", cfg.cflags)

    @patch("aemu.toolchains.package_config_pc.BinaryPatcher")
    def test_binplace(self, mock_patcher):
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            outside = tmp_path / "outside"
            outside.mkdir()

            lib = outside / "libfoo.so"
            lib.write_text("dummy shared library")

            release_dir = tmp_path / "release"
            release_dir.mkdir()

            cfg = PackageConfigPc(
                name="foo",
                version="1.0",
                release_dir=release_dir,
                archives=[lib],
                includes=None,
                shim={},
                target="//foo:foo",
            )

            cfg.binplace(release_dir)

            self.assertTrue((release_dir / "libfoo.so").exists())
            self.assertEqual(
                (release_dir / "libfoo.so").read_text(), "dummy shared library"
            )
            mock_patcher.patch_solib.assert_called_once_with(
                release_dir / "libfoo.so"
            )

    def test_write_pc_file(self):
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            workspace = tmp_path / "workspace"
            workspace.mkdir()

            outside = tmp_path / "outside"
            outside.mkdir()
            archive = outside / "libfoo.a"
            archive.write_text("dummy")

            packages_dir = tmp_path / "packages"
            pc_config_dir = tmp_path / "pc-config"
            pc_config_dir.mkdir()

            cfg = PackageConfigPc(
                name="foo",
                version="1.0",
                release_dir=tmp_path / "release",
                archives=[archive],
                includes=None,
                shim={"Requires": "bar >= 2.0", "link_flags": "-lflags"},
                target="//foo:foo",
            )

            cfg.persist(packages_dir, workspace)

            cfg.write(pc_config_dir)  # Pass directory, not file path

            pc_file = pc_config_dir / "foo.pc"
            self.assertTrue(pc_file.exists())
            content = pc_file.read_text()

            self.assertIn("Name: foo", content)
            self.assertIn("Version: 1.0", content)
            self.assertIn("Requires: bar >= 2.0", content)
            self.assertIn("Libs: -L${libdir} -lfoo -lflags", content)
            self.assertIn("amc_bazel_target=//foo:foo", content)


if __name__ == "__main__":
    unittest.main()
