#!/usr/bin/env python3
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
import argparse
import datetime
import json
import logging
import platform
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from aemu.configure.meson_project_builder import MesonProjectBuilder
from aemu.configure.shim import create_shim
from aemu.log import configure_logging, run_meson_command
from aemu.process.bazel import Bazel
from aemu.process.runner import run
from aemu.toolchains.factory import get_toolchain_generator, get_target_alias
from aemu.util import find_aosp_root, mkdirs


class CustomFormatter(
    argparse.RawTextHelpFormatter, argparse.ArgumentDefaultsHelpFormatter
):
    pass


def _split_list(s):
    return s.split(",") if s else []


def setup_command(args):
    mkdirs(Path(args.out).absolute(), args.force)
    toolchain_generator = get_toolchain_generator(
        args.target,
        get_toolchain_dir(args.out),
        args.prefix,
        Path(args.aosp),
    )
    builder = MesonProjectBuilder(
        config_file=args.config,
        aosp=args.aosp,
        dest=get_build_dir(args.out),
        toolchain_dir=get_toolchain_dir(args.out),
        ccache=args.ccache,
        generator=toolchain_generator,
        bazel_startup_options=_split_list(args.bazel_startup_options),
        bazel_build_options=_split_list(args.bazel_build_options),
        target=get_target_alias(args.target),
    )
    builder.configure_meson(args.meson)
    return builder


def get_build_dir(base_dir):
    return Path(base_dir) / "build"


def get_toolchain_dir(base_dir):
    return Path(base_dir) / "toolchain"


def toolchain_command(args):
    mkdirs(Path(args.out).absolute(), args.force)
    toolchain_dir = get_toolchain_dir(args.out)
    toolchain = get_toolchain_generator(
        args.target,
        toolchain_dir,
        args.prefix,
        Path(args.aosp),
    )
    toolchain.bazel = Bazel(
        Path(args.aosp).absolute(),
        toolchain_dir,
        _split_list(args.bazel_startup_options),
        _split_list(args.bazel_build_options),
    )

    toolchain.gen_toolchain()

    if args.config:
        # If a config file is provided, also generate pkg-config files.
        builder = MesonProjectBuilder(
            config_file=args.config,
            aosp=Path(args.aosp),
            dest=get_build_dir(args.out),
            toolchain_dir=toolchain_dir,
            ccache=args.ccache,
            generator=toolchain,
            bazel_startup_options=_split_list(args.bazel_startup_options),
            bazel_build_options=_split_list(args.bazel_build_options),
            target=get_target_alias(args.target),
        )
        builder.generate_pkg_config_files()


def compile_command(args):
    """Compile the QEMU source by invoking Meson.

    This method compiles the QEMU source by invoking Meson's compile command.
    """
    build_dir = get_build_dir(args.out)
    toolchain_dir = get_toolchain_dir(args.out)
    cmd = [
        toolchain_dir / "meson",
        "compile",
        "-C",
        build_dir,
    ]
    run_meson_command(
        cmd,
        build_dir,
        cwd=build_dir,
        toolchain_path=toolchain_dir,
    )


def test_command(args):
    """Run the QEMU tests by invoking Meson."""
    build_dir = get_build_dir(args.out)
    toolchain_dir = get_toolchain_dir(args.out)
    cmd = [
        toolchain_dir / "meson",
        "test",
        "--print-errorlogs",
        "-C",
        build_dir,
    ]
    run_meson_command(
        cmd,
        build_dir,
        cwd=build_dir,
        toolchain_path=toolchain_dir,
    )


def release_command(args):
    """Run the QEMU tests by invoking Meson."""
    build_dir = get_build_dir(args.out)
    toolchain_dir = get_toolchain_dir(args.out)
    cmd = [
        toolchain_dir / "meson",
        "install",
        "-C",
        build_dir,
    ]
    run_meson_command(
        cmd,
        build_dir,
        cwd=build_dir,
        toolchain_path=toolchain_dir,
    )

    logging.info("Creating %s", args.release)
    with zipfile.ZipFile(
        args.release, "w", zipfile.ZIP_DEFLATED, allowZip64=True
    ) as zipf:
        search_dir = get_build_dir(args.out) / "release"
        for fname in search_dir.glob("**/*"):
            arcname = fname.relative_to(search_dir)
            logging.info("Adding %s as %s", fname, arcname)
            zipf.write(fname, arcname)


def bazel_command(args):
    bazel_out = Path(args.out)
    bazel_out.mkdir(parents=True, exist_ok=True)

    build_dir = args.build
    temp_build = None
    if not build_dir:
        temp_build = tempfile.TemporaryDirectory(prefix="shadow")
        build_dir = Path(temp_build.__enter__()).resolve()
        toolchain_generator = get_toolchain_generator(
            args.target,
            get_toolchain_dir(build_dir),
            "",
            Path(args.aosp),
        )
        builder = MesonProjectBuilder(
            config_file=args.config,
            aosp=args.aosp,
            dest=get_build_dir(build_dir),
            toolchain_dir=get_toolchain_dir(build_dir),
            ccache=args.ccache,
            generator=toolchain_generator,
            bazel_startup_options=_split_list(args.bazel_startup_options),
            bazel_build_options=_split_list(args.bazel_build_options),
            target=get_target_alias(args.target),
        )
        builder.configure_meson([])

    with tempfile.TemporaryDirectory(prefix="bazel") as bazel_build_dir:
        # Make sure there are no accidentally symlinks that cause
        # issues when trying to find dependencies
        build_dir = Path(build_dir).resolve()
        bazel_build_dir = Path(bazel_build_dir).resolve()

        if args.shim:
            shim_path = Path(args.shim)
        else:
            shim_path = create_shim(Path(args.aosp), Path(build_dir))

        toolchain_generator = get_toolchain_generator(
            args.target,
            get_toolchain_dir(bazel_build_dir),
            "",
            Path(args.aosp),
        )
        builder = MesonProjectBuilder(
            config_file=args.config,
            aosp=args.aosp,
            dest=get_build_dir(bazel_build_dir),
            toolchain_dir=get_toolchain_dir(bazel_build_dir),
            ccache=args.ccache,
            generator=toolchain_generator,
            bazel_startup_options=_split_list(args.bazel_startup_options),
            bazel_build_options=_split_list(args.bazel_build_options),
            target=get_target_alias(args.target),
        )
        shim_file = shim_path.absolute()
        builder.configure_meson(
            [
                "--backend",
                "bazel",
                f"-Dbackend_shadow_build={get_build_dir(build_dir).as_posix()}",
                f"-Dbackend_shim={shim_file.as_posix()}",
            ]
        )
        sys_id = f"{toolchain_generator.host()}-{toolchain_generator.target_arch}"
        build_file = get_build_dir(bazel_build_dir) / "bazel" / "BUILD.bazel"
        build_file.rename(
            get_build_dir(bazel_build_dir) / "bazel" / "platform" / f"BUILD.{sys_id}"
        )
        zip_file = bazel_out / f"bazel-{sys_id}-{args.buildid}.zip"
        logging.info("Creating %s", zip_file)
        with zipfile.ZipFile(
            zip_file, "w", zipfile.ZIP_DEFLATED, allowZip64=True
        ) as zipf:
            search_dir = get_build_dir(bazel_build_dir) / "bazel"
            for fname in search_dir.glob("**/*"):
                arcname = fname.relative_to(search_dir)
                logging.info("Adding %s as %s", fname, arcname)
                zipf.write(fname, arcname)

        (bazel_out / "logs").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            get_build_dir(bazel_build_dir) / "meson-logs" / "meson-log.txt",
            bazel_out / "logs" / "meson-log.txt",
        )

    if temp_build:
        temp_build.__exit__(None, None, None)


def main():
    aosp_root = find_aosp_root()
    parser = argparse.ArgumentParser(
        formatter_class=CustomFormatter,
        description=f"""
        Android Meson Configurator

        This script is able to create a set of wrappers that can be used to compile QEMU.
        The wrapper will create a set of toolchain files (cc, c++, nm, etc...) that will
        properly invoke the clang (or clang-cl) compiler that ships with AOSP, installing
        proper shims where needed.

        It is expected that this script will be run with access to the AOSP_ROOT from a manifest
        that is based of `emu-dev` (https://android.googlesource.com/platform/manifest/+/refs/heads/emu-dev)

        The clang version will be obtained from $AOSP_ROOT/build/bazel/toolchains/tool_versions.json

        It will also create a set of fake "pkgconfig" files that will resolve to the
        dependencies that will be built using bazel.

        Note that this does not support cross compilation.
        """,
    )

    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbose",
        default=False,
        action="store_true",
        help="Verbose logging",
    )
    parser.add_argument(
        "--bazel_startup_options",
        default=None,
        type=str,
        help="List of bazel cli startup options",
    )
    parser.add_argument(
        "--bazel_build_options",
        default=None,
        type=str,
        help="List of bazel cli build command options",
    )

    subparsers = parser.add_subparsers(title="Commands", dest="command", metavar="")

    # Subparser for 'toolchain' command
    toolchain_parser = subparsers.add_parser(
        "toolchain", help="Create toolchain wrappers for compilation"
    )
    toolchain_parser.set_defaults(func=toolchain_command)
    toolchain_parser.add_argument(
        "out", type=str, help="Directory for toolchain wrappers"
    )
    toolchain_parser.add_argument(
        "--ccache",
        dest="ccache",
        default=shutil.which("ccache") or shutil.which("sccache"),
        help="Use the given compiler cache (ccache/sccache)",
    )
    toolchain_parser.add_argument(
        "-f",
        "--force",
        dest="force",
        default=False,
        action="store_true",
        help="Ignore existing directory and overwrite existing files",
    )
    toolchain_parser.add_argument(
        "--aosp",
        type=str,
        default=aosp_root,
        help="AOSP root to use",
    )
    toolchain_parser.add_argument(
        "--prefix",
        dest="prefix",
        type=str,
        default="",
        help="Compiler prefix to use, e.g. my-pre-c++",
    )
    toolchain_parser.add_argument(
        "--target",
        type=str,
        default=platform.system().lower(),
        help="Toolchain target, the host you wish to run the executables on",
    )
    toolchain_parser.add_argument(
        "--config",
        type=str,
        help="Path to the build-config.jsonc file for the project (optional).",
    )

    # Subparser for 'setup' command
    setup_parser = subparsers.add_parser(
        "setup", help="Create wrappers for a meson project."
    )
    setup_parser.add_argument("out", type=str, help="Directory for toolchain wrappers")
    setup_parser.set_defaults(func=setup_command)
    setup_parser.add_argument(
        "--config",
        required=True,
        type=str,
        help="Path to the build-config.jsonc file for the project.",
    )
    setup_parser.add_argument(
        "--ccache",
        dest="ccache",
        default=shutil.which("ccache") or shutil.which("sccache"),
        help="Use the given compiler cache (ccache/sccache)",
    )
    setup_parser.add_argument(
        "-f",
        "--force",
        dest="force",
        default=False,
        action="store_true",
        help="Ignore existing directory and overwrite existing files",
    )
    setup_parser.add_argument(
        "--aosp",
        type=str,
        default=aosp_root,
        help="AOSP root to use",
    )
    setup_parser.add_argument(
        "--prefix",
        dest="prefix",
        type=str,
        default="",
        help="Compiler prefix to use, e.g. my-pre-c++",
    )
    setup_parser.add_argument(
        "--meson",
        nargs="*",
        help="Additional flags to pass to meson, for example --meson '--buildtype=debug'",
    )
    setup_parser.add_argument(
        "--target",
        type=str,
        default=platform.system().lower(),
        help="Toolchain target, the host you wish to run the executables on",
    )

    # Subparser for 'compile' command
    compile_parser = subparsers.add_parser(
        "compile", help="Compile the configured source"
    )
    compile_parser.set_defaults(func=compile_command)
    compile_parser.add_argument("out", type=str, help="Configured compile directory")

    # Subparser for 'test' command
    test_parser = subparsers.add_parser("test", help="Run tests")
    test_parser.set_defaults(func=test_command)
    test_parser.add_argument("out", type=str, help="Configured compile directory")

    # Subparser for 'release' command
    release_parser = subparsers.add_parser("release", help="Generate release zip")
    release_parser.set_defaults(func=release_command)
    release_parser.add_argument("out", type=str, help="Configured compile directory")
    release_parser.add_argument("release", type=str, help="Zipfile with the release")

    # Subparser for 'bazel' command
    bazel_parser = subparsers.add_parser("bazel", help="Run QEMU tests")
    bazel_parser.set_defaults(func=bazel_command)
    bazel_parser.add_argument(
        "out", type=str, help="Directory with the final bazel zip"
    )
    bazel_parser.add_argument(
        "--config",
        required=True,
        type=str,
        help="Path to the build-config.jsonc file for the project.",
    )
    bazel_parser.add_argument(
        "--aosp",
        type=str,
        default=aosp_root,
        help="AOSP root to use",
    )
    bazel_parser.add_argument(
        "--ccache",
        dest="ccache",
        default=shutil.which("ccache") or shutil.which("sccache"),
        help="Use the given compiler cache (ccache/sccache)",
    )
    bazel_parser.add_argument(
        "--target",
        type=str,
        default=platform.system().lower(),
        help="Toolchain target, the host you wish to run the executables on",
    )
    bazel_parser.add_argument(
        "--build",
        type=str,
        default=None,
        help="Shadow build to use, or None if you wish to create a temporary one",
    )
    bazel_parser.add_argument(
        "--buildid",
        type=str,
        default="",
        help="Build id to use, if any",
    )
    bazel_parser.add_argument(
        "--shim",
        default=None,
        type=str,
        help="Directory with the release",
    )

    args = parser.parse_args()

    lvl = logging.DEBUG if args.verbose else logging.INFO
    configure_logging(lvl)

    # Make sure we use absolute paths, so we do not get
    # confused.
    if hasattr(args, "out") and args.out:
        args.out = Path(args.out).absolute()

    # Call the function associated with the selected subcommand
    if hasattr(args, "func"):
        start_time = time.monotonic()
        try:
            args.func(args)
        except Exception as e:
            if args.verbose:
                raise e
            logging.fatal("Build failure: %s", e)
            sys.exit(1)
        finally:
            end_time = time.monotonic()
            logging.info(
                "Completed in: %s", datetime.timedelta(seconds=end_time - start_time)
            )

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
