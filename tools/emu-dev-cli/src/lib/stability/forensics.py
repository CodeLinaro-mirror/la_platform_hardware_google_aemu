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

"""Forensic diagnostic bundle collector for DEGRADED or FAIL stability runs."""

from dataclasses import asdict, dataclass, field
import datetime
import json
import logging
import os
import re
import shutil
import tempfile
from typing import Any, Callable, Dict, List, Optional, Sequence

from lib.perf.adb import AdbDevice
from lib.stability.health import CycleHealthSample, StabilityEvaluation

logger = logging.getLogger(__name__)

DEFAULT_FORENSICS_SUBDIR: str = "stability_forensics"
MAX_HOST_LOG_TAIL_BYTES: int = 4 * 1024 * 1024


@dataclass
class ForensicBundleInfo:
    """Metadata describing an archived stability forensic bundle on disk."""

    bundle_dir: str
    artifacts: Dict[str, str] = field(default_factory=dict)
    minidump_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def default_forensics_root() -> str:
    """Returns `~/.cache/emu-dev-cli/stability_forensics` (or OS cache equivalent)."""
    cache_home = os.environ.get("XDG_CACHE_HOME")
    if not cache_home:
        if os.name == "nt":
            local_app = (
                os.environ.get("LOCALAPPDATA")
                or (
                    os.path.join(os.environ["USERPROFILE"], "AppData", "Local")
                    if os.environ.get("USERPROFILE")
                    else None
                )
                or tempfile.gettempdir()
            )
            cache_home = os.path.join(local_app, "emu-dev-cli", "cache")
        else:
            home_dir = os.environ.get("HOME") or tempfile.gettempdir()
            cache_home = os.path.join(home_dir, ".cache", "emu-dev-cli")
    return os.path.join(cache_home, DEFAULT_FORENSICS_SUBDIR)


def _sanitize_slug(label: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*()\x00-\x1f]+', "_", (label or "run").strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._")
    return cleaned[:64] or "run"


def discover_recent_minidumps(
    since_epoch_sec: float,
    extra_search_dirs: Optional[Sequence[str]] = None,
) -> List[str]:
    """Scans Crashpad and temp directories for `.dmp` minidump files modified at or after `since_epoch_sec`."""
    candidates: List[str] = [
        tempfile.gettempdir(),
        "/tmp",
    ]
    user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    if user:
        candidates.append(os.path.join(tempfile.gettempdir(), f"android-{user}"))
        candidates.append(f"/tmp/android-{user}")
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            candidates.append(os.path.join(local_app, "Temp"))
            if user:
                candidates.append(
                    os.path.join(local_app, "Temp", f"android-{user}")
                )
    if extra_search_dirs:
        candidates.extend(d for d in extra_search_dirs if d)

    found: List[str] = []
    seen_real: set[str] = set()
    threshold = max(0.0, since_epoch_sec - 2.0)

    for base_dir in candidates:
        if not base_dir or not os.path.isdir(base_dir):
            continue
        real_base = os.path.realpath(base_dir)
        if real_base in seen_real:
            continue
        seen_real.add(real_base)
        try:
            for root, dirs, files in os.walk(base_dir):
                # Limit recursion depth to avoid scanning deep unrelated temp trees
                rel_depth = os.path.relpath(root, base_dir).count(os.sep)
                if rel_depth >= 3:
                    dirs[:] = []
                for fname in files:
                    if not fname.lower().endswith(".dmp"):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        if os.path.getmtime(fpath) >= threshold:
                            found.append(fpath)
                    except OSError:
                        continue
        except OSError as exc:
            logger.debug("Ignored error scanning minidump dir %s: %s", base_dir, exc)

    return sorted(set(found))


def _write_text_artifact(
    bundle_dir: str,
    filename: str,
    content: str,
    artifacts: Dict[str, str],
    key: str,
) -> None:
    if not content:
        return
    out_path = os.path.join(bundle_dir, filename)
    with open(out_path, "w", encoding="utf-8", errors="replace") as f:
        f.write(content)
    artifacts[key] = out_path


def _copy_host_log_artifact(
    host_log_path: Optional[str],
    bundle_dir: str,
    artifacts: Dict[str, str],
) -> None:
    if not host_log_path or not os.path.isfile(host_log_path):
        return
    dest_path = os.path.join(bundle_dir, "emulator_host.log")
    try:
        size = os.path.getsize(host_log_path)
        if size <= MAX_HOST_LOG_TAIL_BYTES:
            shutil.copyfile(host_log_path, dest_path)
        else:
            with open(host_log_path, "rb") as src:
                src.seek(size - MAX_HOST_LOG_TAIL_BYTES)
                tail = src.read()
            with open(dest_path, "wb") as dst:
                dst.write(tail)
        artifacts["emulator_host_log"] = dest_path
    except OSError as exc:
        logger.debug("Failed copying host log %s: %s", host_log_path, exc)


def _capture_guest_artifacts(
    adb: Optional[AdbDevice],
    bundle_dir: str,
    artifacts: Dict[str, str],
    png_capture_fn: Optional[Callable[[], Optional[bytes]]] = None,
) -> None:
    if png_capture_fn is not None:
        try:
            png_bytes = png_capture_fn()
            if png_bytes:
                png_path = os.path.join(bundle_dir, "last_frame.png")
                with open(png_path, "wb") as f:
                    f.write(png_bytes)
                artifacts["last_frame_png"] = png_path
        except Exception as exc:  # noqa: BLE001
            logger.debug("gRPC PNG screenshot capture failed during forensics: %s", exc)

    if adb is None:
        return

    crash_lc = adb.run(["logcat", "-b", "crash", "-d"], timeout=15.0)
    if crash_lc.ok and crash_lc.stdout.strip():
        _write_text_artifact(
            bundle_dir,
            "logcat_crash.txt",
            crash_lc.stdout,
            artifacts,
            "logcat_crash",
        )

    tail_lc = adb.run(["logcat", "-d", "-t", "2000"], timeout=15.0)
    if tail_lc.ok and tail_lc.stdout.strip():
        _write_text_artifact(
            bundle_dir,
            "logcat_tail.txt",
            tail_lc.stdout,
            artifacts,
            "logcat_tail",
        )

    dropbox_res = adb.shell(["dumpsys", "dropbox"], timeout=15.0)
    if dropbox_res.ok and dropbox_res.stdout.strip():
        _write_text_artifact(
            bundle_dir,
            "dropbox_summary.txt",
            dropbox_res.stdout,
            artifacts,
            "dropbox_summary",
        )


def collect_forensic_bundle(
    *,
    label: str,
    samples: Sequence[CycleHealthSample],
    evaluation: StabilityEvaluation,
    session_start_epoch: float,
    forensics_root: Optional[str] = None,
    host_log_path: Optional[str] = None,
    adb: Optional[AdbDevice] = None,
    png_capture_fn: Optional[Callable[[], Optional[bytes]]] = None,
    extra_minidump_dirs: Optional[Sequence[str]] = None,
) -> ForensicBundleInfo:
    """Packages host logs, Crashpad minidumps, logcat/DropBox dumps, last frame, and telemetry JSON."""
    root_dir = os.path.abspath(
        os.path.expanduser(forensics_root or default_forensics_root())
    )
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = _sanitize_slug(label)
    bundle_dir = os.path.join(root_dir, f"stability-forensics-{slug}-{ts}")
    os.makedirs(bundle_dir, exist_ok=True)

    artifacts: Dict[str, str] = {}

    timeseries_path = os.path.join(bundle_dir, "timeseries.json")
    with open(timeseries_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "label": label,
                "evaluation": evaluation.to_dict(),
                "samples": [s.to_dict() for s in samples],
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    artifacts["timeseries_json"] = timeseries_path

    _copy_host_log_artifact(host_log_path, bundle_dir, artifacts)
    _capture_guest_artifacts(
        adb=adb,
        bundle_dir=bundle_dir,
        artifacts=artifacts,
        png_capture_fn=png_capture_fn,
    )

    dmp_sources = discover_recent_minidumps(
        since_epoch_sec=session_start_epoch,
        extra_search_dirs=extra_minidump_dirs,
    )
    copied_dmps: List[str] = []
    if dmp_sources:
        dmp_dir = os.path.join(bundle_dir, "minidumps")
        os.makedirs(dmp_dir, exist_ok=True)
        for idx, dmp_src in enumerate(dmp_sources):
            dst_name = f"{idx:02d}_{os.path.basename(dmp_src)}"
            dst_path = os.path.join(dmp_dir, dst_name)
            try:
                shutil.copyfile(dmp_src, dst_path)
                copied_dmps.append(dst_path)
            except OSError as exc:
                logger.debug("Failed copying minidump %s: %s", dmp_src, exc)
        if copied_dmps:
            artifacts["minidumps_dir"] = dmp_dir

    return ForensicBundleInfo(
        bundle_dir=bundle_dir,
        artifacts=artifacts,
        minidump_files=copied_dmps,
    )
