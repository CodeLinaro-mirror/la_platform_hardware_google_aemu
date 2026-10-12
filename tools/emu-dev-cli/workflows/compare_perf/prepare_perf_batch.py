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

"""Pre-flight environment inspection for the `compare-perf` workflow."""

import argparse
import sys
from pathlib import Path
from typing import Any

_WORKFLOW_DIR = Path(__file__).resolve().parent
_EMU_DEV_CLI_DIR = _WORKFLOW_DIR.parent.parent
_SRC_DIR = _EMU_DEV_CLI_DIR / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from lib.perf_runner import (
    _detect_default_host,
    _resolve_perf_avd_template,
    resolve_suites,
)


def inspect_perf_environment(
    baseline: str,
    candidate: str,
    suite: str,
    avd: str,
) -> dict[str, Any]:
    """Checks host OS/arch, suite resolution, and system-image/AVD readiness."""
    host = _detect_default_host()
    suites, workloads = resolve_suites(suite)

    avd_ready = False
    avd_summary = ""
    try:
        tpl = _resolve_perf_avd_template(avd_name=avd)
        avd_ready = True
        avd_summary = (
            f"template='{tpl['template_avd']}' "
            f"(sysimg='{tpl['sysimg_dir']}', {tpl['cores']} cores, {tpl['ram']}MB RAM)"
        )
    except Exception as exc:  # noqa: BLE001
        avd_summary = (
            f"MISSING ({exc}) -> run `emu-dev-cli fetch-build system-image --latest` first"
        )

    return {
        "host": host,
        "baseline": baseline,
        "candidate": candidate,
        "suites": suites,
        "workloads": workloads,
        "avd_ready": avd_ready,
        "avd_summary": avd_summary,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect host and AVD readiness for compare-perf workflow."
    )
    parser.add_argument("--baseline", default="stable")
    parser.add_argument("--candidate", default="latest:emu-main-next")
    parser.add_argument("--suite", default="all")
    parser.add_argument("--avd", default="auto")
    args = parser.parse_args(argv)

    try:
        info = inspect_perf_environment(
            baseline=args.baseline,
            candidate=args.candidate,
            suite=args.suite,
            avd=args.avd,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Error inspecting perf environment: {exc}", file=sys.stderr)
        return 1

    print(
        f"Host={info['host']} | Baseline={info['baseline']} vs Candidate={info['candidate']} | "
        f"Suites={','.join(info['suites'])} ({len(info['workloads'])} workloads) | "
        f"AVD={info['avd_summary']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
