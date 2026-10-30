# Copyright (C) 2026 The Android Open Source Project
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

"""Emulator target resolution and directory metadata inspection for performance benchmarks."""

import argparse
import glob
import logging
import os
import platform
import re
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from typing import Dict, Optional, Tuple

from core.context import detect_host_platform
from core.process import ProcessRunner, SubprocessRunner
from lib.emulator import resolve_emulator_executable
from lib.perf.catalog import ResolvedPerfTarget

logger = logging.getLogger(__name__)

DEFAULT_STABLE_VERSION = "37.2.12"
DEFAULT_STABLE_BUILD_ID = "16428233"
ANDROID_REPO_XML_URL = "https://dl.google.com/android/repository/repository2-3.xml"


def _resolve_emulator_executable_fn():
    perf_runner_mod = sys.modules.get("lib.perf_runner")
    if perf_runner_mod is not None:
        patched = getattr(perf_runner_mod, "resolve_emulator_executable", None)
        if patched is not None:
            return patched
    return resolve_emulator_executable


def _read_pkg_revision(prop_path: str) -> Optional[str]:
    if not os.path.isfile(prop_path):
        return None
    try:
        with open(prop_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as exc:
        logger.debug("Ignored error reading %s: %s", prop_path, exc)
        return None
    for line in lines:
        if line.startswith("Pkg.Revision="):
            return line.split("=", 1)[1].strip()
    return None


def _detect_engine_from_dir(emu_dir: str) -> Optional[str]:
    lib_qemu_dir = os.path.join(emu_dir, "lib", "qemu")
    if not os.path.isdir(lib_qemu_dir):
        lib_qemu_dir = os.path.join(emu_dir, "emulator", "lib", "qemu")
    if os.path.isdir(lib_qemu_dir):
        return "qemu-next"

    lib64_dir = os.path.join(emu_dir, "lib64")
    if not os.path.isdir(lib64_dir):
        lib64_dir = os.path.join(emu_dir, "emulator", "lib64")
    if not os.path.isdir(lib64_dir):
        return None

    try:
        files = os.listdir(lib64_dir)
        if any("goldfish" in f or "adb_vsock" in f for f in files):
            return "qemu-next"
        return "qemu-now"
    except OSError as exc:
        logger.debug("Ignored error inspecting %s: %s", lib64_dir, exc)
        return None


def inspect_emulator_dir_metadata(emu_dir: str) -> Dict[str, Optional[str]]:
    """Extracts `Pkg.Revision`, `build_id`, and engine type (`qemu-next` vs `qemu-now`) from an emulator directory."""
    meta: Dict[str, Optional[str]] = {
        "pkg_revision": None,
        "build_id": None,
        "engine": None,
    }
    if not emu_dir or not os.path.isdir(emu_dir):
        return meta

    prop_candidates = (
        os.path.join(emu_dir, "source.properties"),
        os.path.join(emu_dir, "emulator", "source.properties"),
    )
    for prop_path in prop_candidates:
        pkg_rev = _read_pkg_revision(prop_path)
        if pkg_rev is not None:
            meta["pkg_revision"] = pkg_rev
            break

    m = re.search(r"emulator-(?:[a-z0-9-]+-)?(\d{6,})", emu_dir)
    if m:
        meta["build_id"] = m.group(1)

    meta["engine"] = _detect_engine_from_dir(emu_dir)
    return meta


def _detect_default_host() -> str:
    return detect_host_platform()


def _build_emulator_dir_target(
    spec: str,
    target_dir: Optional[str],
    label_prefix: Optional[str] = None,
    build_id_override: Optional[str] = None,
    branch: Optional[str] = None,
    pkg_revision_override: Optional[str] = None,
    default_engine: Optional[str] = None,
) -> ResolvedPerfTarget:
    """Constructs a `ResolvedPerfTarget` for an emulator directory."""
    resolve_fn = _resolve_emulator_executable_fn()
    emu_bin, base_dir = resolve_fn(target_dir)
    meta = inspect_emulator_dir_metadata(base_dir)
    engine = meta.get("engine") or default_engine
    build_id = build_id_override or meta.get("build_id")

    if label_prefix:
        label = f"{label_prefix} ({engine})" if engine else label_prefix
    else:
        base_lbl = build_id or os.path.basename(
            os.path.normpath(target_dir or base_dir)
        )
        label = f"{base_lbl} ({engine})" if engine else base_lbl

    return ResolvedPerfTarget(
        spec=spec,
        kind="emulator_dir",
        label=label,
        emulator_dir=base_dir,
        emulator_bin=emu_bin,
        build_id=build_id,
        branch=branch,
        pkg_revision=meta.get("pkg_revision") or pkg_revision_override,
        engine=engine,
    )


def _try_resolve_json_target(spec: str) -> Optional[ResolvedPerfTarget]:
    if ".json" not in spec or (not os.path.isfile(spec) and ":" not in spec):
        return None
    base_path = spec.split(":", 1)[0]
    if not os.path.isfile(base_path):
        return None
    lbl = (
        spec.split(":", 1)[1]
        if ":" in spec
        else os.path.splitext(os.path.basename(base_path))[0]
    )
    return ResolvedPerfTarget(
        spec=spec,
        kind="json",
        label=lbl,
        json_path=spec,
    )


def _find_workspace_root() -> Optional[str]:
    cur = os.path.abspath(os.getcwd())
    while cur and cur != "/":
        if os.path.isfile(os.path.join(cur, "MODULE.bazel")) and os.path.isdir(
            os.path.join(cur, "hardware", "google", "aemu")
        ):
            return cur
        cur = os.path.dirname(cur)
    try:
        from commands.source_directory import get_source_directory

        return get_source_directory("emu-main-next")
    except Exception:
        return None


def _find_built_release_zip(ws_root: str, require_opt: bool = True) -> Optional[str]:
    candidates = (
        os.path.join(
            ws_root,
            "bazel-bin",
            "external",
            "goldfish+",
            "emulator",
            "release.zip",
        ),
        os.path.join(ws_root, "bazel-bin", "emulator-repo-release.zip"),
    )
    for cand in candidates:
        if not os.path.isfile(cand):
            continue
        real_c = os.path.realpath(cand)
        if not require_opt or "-opt" in real_c or "emulator-repo-release.zip" in real_c:
            return real_c
    return None


def _extract_release_zip_if_newer(
    zip_path: str, dest_dir: str, emu_subdir: str, runner: ProcessRunner
) -> None:
    if not os.path.isfile(zip_path):
        return
    if os.path.isdir(emu_subdir) and os.path.getmtime(zip_path) <= os.path.getmtime(
        emu_subdir
    ):
        return
    if os.path.isdir(dest_dir):
        shutil.rmtree(dest_dir, ignore_errors=True)
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)
    if platform.system().lower() == "darwin":
        runner.run(["xattr", "-cr", emu_subdir], check=False)


def _resolve_tree_or_local_target(
    spec: str, runner: ProcessRunner
) -> ResolvedPerfTarget:
    if spec.lower() == "local":
        try:
            resolve_fn = _resolve_emulator_executable_fn()
            emu_bin, base_dir = resolve_fn(None)
            meta = inspect_emulator_dir_metadata(base_dir)
            return ResolvedPerfTarget(
                spec=spec,
                kind="emulator_dir",
                label=f"local ({meta.get('engine') or os.path.basename(base_dir)})",
                emulator_dir=base_dir,
                emulator_bin=emu_bin,
                build_id=meta.get("build_id"),
                pkg_revision=meta.get("pkg_revision"),
                engine=meta.get("engine"),
            )
        except Exception as exc:
            logger.debug("Ignored error resolving local target: %s", exc)

    ws_root = _find_workspace_root()
    dest_dir = os.path.join(
        _get_perf_cache_dir(), "emulator-repo-release", "extracted"
    )
    emu_subdir = os.path.join(dest_dir, "emulator")
    if ws_root and os.path.isdir(ws_root):
        goldfish_dir = os.path.join(ws_root, "hardware", "generic", "goldfish")
        target_label = (
            "@goldfish//emulator:release"
            if os.path.isdir(goldfish_dir)
            else "//:emulator-repo-release"
        )
        zip_path = _find_built_release_zip(ws_root, require_opt=True)
        if not zip_path:
            logger.info(
                "Building %s (--config=release) in %s...", target_label, ws_root
            )
            runner.run(
                ["bazel", "build", "--config=release", target_label],
                cwd=ws_root,
                check=True,
                capture_output=False,
            )
            zip_path = _find_built_release_zip(ws_root, require_opt=True) or (
                _find_built_release_zip(ws_root, require_opt=False)
            )
        if zip_path:
            _extract_release_zip_if_newer(zip_path, dest_dir, emu_subdir, runner)

    return _build_emulator_dir_target(
        spec=spec,
        target_dir=emu_subdir if os.path.isdir(emu_subdir) else None,
        label_prefix="tree",
        branch="emu-main-next",
        default_engine="qemu-next",
    )


def _host_stable_archive_criteria(
    default_host: str,
) -> Tuple[str, Tuple[Optional[str], ...], str]:
    if default_host == "mac-arm64":
        return (
            "macosx",
            ("aarch64", "arm64"),
            f"emulator-darwin_aarch64-{DEFAULT_STABLE_BUILD_ID}.zip",
        )
    if default_host == "mac-x64":
        return (
            "macosx",
            (None, "x64", "x86_64"),
            f"emulator-darwin_x64-{DEFAULT_STABLE_BUILD_ID}.zip",
        )
    if default_host.startswith("win"):
        return (
            "windows",
            (None, "x64", "x86_64"),
            f"emulator-windows_x64-{DEFAULT_STABLE_BUILD_ID}.zip",
        )
    return (
        "linux",
        (None, "x64", "x86_64"),
        f"emulator-linux_x64-{DEFAULT_STABLE_BUILD_ID}.zip",
    )


def _query_stable_channel_archive(
    want_os: str,
    want_archs: Tuple[Optional[str], ...],
    default_zip_name: str,
) -> Tuple[str, str, str]:
    version_str = DEFAULT_STABLE_VERSION
    stable_bid = DEFAULT_STABLE_BUILD_ID
    zip_name = default_zip_name
    try:
        with urllib.request.urlopen(ANDROID_REPO_XML_URL, timeout=20) as resp:
            xml_bytes = resp.read()
        root = ET.fromstring(xml_bytes)
        for pkg in root.findall(".//remotePackage[@path='emulator']"):
            ch = pkg.find("channelRef")
            if ch is None or ch.attrib.get("ref") != "channel-0":
                continue
            rev = pkg.find("revision")
            if rev is not None:
                version_str = (
                    f"{rev.findtext('major', '0')}."
                    f"{rev.findtext('minor', '0')}."
                    f"{rev.findtext('micro', '0')}"
                )
            zip_name, stable_bid = _match_archive_in_package(
                pkg, want_os, want_archs, zip_name, stable_bid
            )
            break
    except Exception as exc:
        logger.warning(
            "Could not query repository2-3.xml (%s), falling back to %s",
            exc,
            zip_name,
        )
    return version_str, stable_bid, zip_name


def _match_archive_in_package(
    pkg: ET.Element,
    want_os: str,
    want_archs: Tuple[Optional[str], ...],
    default_zip: str,
    default_bid: str,
) -> Tuple[str, str]:
    for arch in pkg.findall(".//archive"):
        hos = arch.findtext("host-os")
        harch = arch.findtext("host-arch")
        url_el = arch.find(".//complete/url")
        if (
            hos == want_os
            and harch in want_archs
            and url_el is not None
            and url_el.text
        ):
            zip_name = url_el.text.strip()
            m_bid = re.search(r"-(\d+)\.zip$", zip_name)
            stable_bid = m_bid.group(1) if m_bid else default_bid
            return zip_name, stable_bid
    return default_zip, default_bid


def _chmod_executable_if_needed(
    root_d: str, fn: str, is_bin_dir: bool
) -> None:
    exec_names = ("emulator", "emulator-check", "mksdcard", "perfetto")
    if not (is_bin_dir or fn in exec_names or fn.startswith("qemu-system-")):
        return
    fp = os.path.join(root_d, fn)
    try:
        os.chmod(fp, os.stat(fp).st_mode | 0o755)
    except OSError as exc:
        logger.debug("Ignored chmod error on %s: %s", fp, exc)


def _restore_extracted_executable_permissions(emu_subdir: str) -> None:
    for root_d, _, files in os.walk(emu_subdir):
        is_bin_dir = any(p in root_d for p in ("bin64", "darwin", "MacOS"))
        for fn in files:
            _chmod_executable_if_needed(root_d, fn, is_bin_dir)


def _get_perf_cache_dir() -> str:
    """Returns the user-specific cache directory (`~/.cache/emu-dev-cli/perf`) with 0700 permissions."""
    cache_dir = os.path.expanduser("~/.cache/emu-dev-cli/perf")
    os.makedirs(cache_dir, mode=0o700, exist_ok=True)
    return cache_dir


def _resolve_stable_target(
    spec: str, default_host: str, runner: ProcessRunner
) -> ResolvedPerfTarget:
    want_os, want_archs, default_zip = _host_stable_archive_criteria(default_host)
    version_str, stable_bid, zip_name = _query_stable_channel_archive(
        want_os, want_archs, default_zip
    )
    dest_root = os.path.join(
        _get_perf_cache_dir(), f"emulator-stable-{default_host}-{stable_bid}"
    )
    extracted_dir = os.path.join(dest_root, "extracted")
    emu_subdir = os.path.join(extracted_dir, "emulator")
    if not os.path.isdir(emu_subdir):
        os.makedirs(extracted_dir, mode=0o700, exist_ok=True)
        zip_path = os.path.join(dest_root, zip_name)
        if not os.path.isfile(zip_path):
            dl_url = f"https://dl.google.com/android/repository/{zip_name}"
            logger.info(
                "Downloading official stable emulator %s (%s) from %s...",
                version_str,
                stable_bid,
                dl_url,
            )
            urllib.request.urlretrieve(dl_url, zip_path)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extracted_dir)
        _restore_extracted_executable_permissions(emu_subdir)
        if platform.system().lower() == "darwin":
            runner.run(["xattr", "-cr", emu_subdir], check=False)

    resolve_fn = _resolve_emulator_executable_fn()
    emu_bin, base_dir = resolve_fn(emu_subdir)
    meta = inspect_emulator_dir_metadata(base_dir)
    return ResolvedPerfTarget(
        spec=spec,
        kind="emulator_dir",
        label=f"stable-{version_str} ({meta.get('engine') or 'qemu-now'})",
        emulator_dir=base_dir,
        emulator_bin=emu_bin,
        build_id=stable_bid,
        branch="stable",
        pkg_revision=meta.get("pkg_revision") or version_str,
        engine=meta.get("engine"),
    )


def _try_resolve_cached_build_id(
    spec: str, default_host: str, build_id: str
) -> Optional[ResolvedPerfTarget]:
    cache_root = _get_perf_cache_dir()
    cached_candidates = (
        os.path.join(
            cache_root, f"emulator-{default_host}-{build_id}", "extracted", "emulator"
        ),
        os.path.join(
            cache_root, f"emulator-{default_host}-{build_id}", "extracted"
        ),
        os.path.join(
            cache_root, f"emulator-{build_id}", "extracted", "emulator"
        ),
        os.path.join(cache_root, f"emulator-{build_id}", "extracted"),
    )
    for cand in cached_candidates:
        if not os.path.isdir(cand):
            continue
        try:
            return _build_emulator_dir_target(
                spec=spec,
                target_dir=cand,
                label_prefix=f"build-{build_id}",
                build_id_override=build_id,
            )
        except Exception as exc:
            logger.debug("Ignored error checking cached candidate %s: %s", cand, exc)
    return None


def _resolve_ab_build_target(
    spec: str, default_host: str, auto_fetch: bool
) -> Optional[ResolvedPerfTarget]:
    is_numeric_bid = spec.isdigit()
    is_latest_branch = spec.lower().startswith("latest:")
    if not (is_numeric_bid or is_latest_branch):
        return None

    build_id = spec if is_numeric_bid else None
    branch = spec.split(":", 1)[1].strip() if is_latest_branch else "emu-main-next"

    if build_id:
        cached = _try_resolve_cached_build_id(spec, default_host, build_id)
        if cached is not None:
            return cached

    if not auto_fetch:
        raise FileNotFoundError(
            f"Emulator build '{spec}' is not cached locally and auto_fetch=False."
        )

    from targets import emulator as emu_target

    cache_root = _get_perf_cache_dir()
    fetch_out_dir = (
        os.path.join(cache_root, f"emulator-{default_host}-{build_id}")
        if build_id
        else os.path.join(cache_root, f"emulator-{default_host}-latest")
    )
    fetch_args = argparse.Namespace(
        build_id=build_id,
        latest=is_latest_branch,
        branch=branch,
        host=default_host,
        output_dir=fetch_out_dir,
        token=None,
        skip_auth_check=False,
        json=True,
    )
    logger.info("Fetching emulator build '%s' from go/ab...", spec)
    emu_target.run_emulator_fetch(fetch_args)

    if build_id:
        search_dirs = [
            os.path.join(fetch_out_dir, "extracted", "emulator"),
            os.path.join(fetch_out_dir, "extracted"),
        ]
    else:
        search_dirs = sorted(
            glob.glob(
                os.path.join(cache_root, f"emulator-{default_host}-*", "extracted")
            ),
            key=os.path.getmtime,
            reverse=True,
        )

    for cand in search_dirs:
        if os.path.isdir(cand):
            resolve_fn = _resolve_emulator_executable_fn()
            emu_bin, base_dir = resolve_fn(cand)
            meta = inspect_emulator_dir_metadata(base_dir)
            resolved_bid = build_id or meta.get("build_id") or spec
            eng_suffix = f" ({meta['engine']})" if meta.get("engine") else ""
            return ResolvedPerfTarget(
                spec=spec,
                kind="emulator_dir",
                label=f"build-{resolved_bid}{eng_suffix}",
                emulator_dir=base_dir,
                emulator_bin=emu_bin,
                build_id=resolved_bid,
                branch=branch,
                pkg_revision=meta.get("pkg_revision"),
                engine=meta.get("engine"),
            )
    return None


def resolve_perf_target(
    spec: str,
    default_host: Optional[str] = None,
    auto_fetch: bool = True,
    process_runner: Optional[ProcessRunner] = None,
) -> ResolvedPerfTarget:
    """Resolves a target specification into a JSON file, local emulator binary directory,
    or automatically fetched `go/ab` build.
    """
    if not default_host:
        default_host = _detect_default_host()
    runner = process_runner or SubprocessRunner()
    s = spec.strip()

    json_target = _try_resolve_json_target(s)
    if json_target is not None:
        return json_target

    expanded = os.path.expanduser(s)
    if os.path.isdir(expanded):
        return _build_emulator_dir_target(spec=s, target_dir=expanded)

    if s.lower() in ("tree", "local"):
        return _resolve_tree_or_local_target(s, runner)

    if s.lower() == "stable":
        return _resolve_stable_target(s, default_host, runner)

    ab_target = _resolve_ab_build_target(s, default_host, auto_fetch)
    if ab_target is not None:
        return ab_target

    raise ValueError(
        f"Cannot resolve performance target '{spec}'. Pass a build ID (e.g. 15075377), "
        f"'latest:emu-main-next', 'local', an extracted emulator directory, or a .json report path."
    )
