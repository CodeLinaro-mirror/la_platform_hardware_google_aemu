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

"""F1 SQL (`android_devtools_infra.TestActions`) integration for Studio & Android Device Hub flakiness."""

import json
import logging
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable, Dict, List, Optional, Tuple

from lib.ath_api import FlakyTestRecord, TestHistoryEntry, sanitize_bazel_target
from lib.workspace import get_flakiness_sandbox_dir

logger = logging.getLogger(__name__)

DEFAULT_F1_SQL_BIN = "/google/bin/releases/f1-team/public/f1-sql"

STUDIO_TARGET_PLATFORMS: List[str] = [
    "studio-linux",
    "studio-win",
    "studio-mac",
    "studio-mac-arm",
]

DEFAULT_STUDIO_DIR_PREFIX = "//tools/vendor/google/cli/android-device-hub/electron"
DEFAULT_STUDIO_BRANCH = "git_studio-main"
DEFAULT_STUDIO_COMPONENT_ID = "2289192"
DEFAULT_STUDIO_LOOKBACK_DAYS = 20
DEFAULT_STUDIO_WINDOW = 100
DEFAULT_ADH_WINDOW = 250
STUDIO_FLAKES_DASHBOARD_URL = (
    "https://dashboards.corp.google.com/_b69adbf1_bc7f_4b7f_a2a7_607d6fd07a8e"
)


def _validate_sql_string(param_name: str, param_value: str) -> None:
    """Validates that a SQL string literal parameter contains no unsafe characters."""
    for ch in ("'", '"', ";", "\\", "\n", "\r"):
        if ch in param_value:
            raise ValueError(
                f"Invalid character {ch!r} in {param_name}: {param_value!r}"
            )


def is_studio_query(
    target: Optional[str] = None,
    branch: Optional[str] = None,
    directory: Optional[str] = None,
    test_target: Optional[str] = None,
) -> bool:
    """Returns True if the flakiness query targets Studio / TestActions instead of ATH."""
    if directory and directory.strip():
        return True
    if target and target.strip().lower().startswith("studio-"):
        return True
    if branch and (
        branch.strip().startswith("git_studio-")
        or branch.strip() == "studio-main"
    ):
        return True
    if test_target and test_target.strip().startswith(
        ("//tools/vendor/google/", "//tools/adt/", "//tools/base/")
    ):
        return True
    return False


def is_android_device_hub_target(target_or_dir: Optional[str]) -> bool:
    """Returns True if the target label or directory belongs to Android Device Hub."""
    if not target_or_dir:
        return False
    return "tools/vendor/google/cli/android-device-hub" in target_or_dir


def resolve_studio_window(
    window: Optional[int], directory_or_target: Optional[str] = None
) -> int:
    """Resolves the rolling run window, defaulting to 250 for Android Device Hub."""
    if window is not None:
        return window
    check_path = directory_or_target or DEFAULT_STUDIO_DIR_PREFIX
    return (
        DEFAULT_ADH_WINDOW
        if is_android_device_hub_target(check_path)
        else DEFAULT_STUDIO_WINDOW
    )


def resolve_studio_min_flake_threshold(
    min_flake_rate: Optional[float],
    default_non_adh_rate: float,
    directory_or_target: Optional[str] = None,
) -> Tuple[float, int]:
    """Resolves (min_flake_rate, min_failed_runs) for Studio / ADH queries.

    Android Device Hub targets default to reporting/filing any flake (>= 1 failed run).
    """
    if min_flake_rate is not None:
        return min_flake_rate, 0
    check_path = directory_or_target or DEFAULT_STUDIO_DIR_PREFIX
    if is_android_device_hub_target(check_path):
        return 0.0, 1
    return default_non_adh_rate, 0


def resolve_studio_targets(target: Optional[str]) -> List[str]:
    """Resolves a CLI --target value into a list of canonical studio-* build targets."""
    if not target or target in ("all", "studio-all"):
        return list(STUDIO_TARGET_PLATFORMS)
    cleaned = target.strip()
    if cleaned in STUDIO_TARGET_PLATFORMS:
        return [cleaned]
    if cleaned.startswith("studio-"):
        return [cleaned]
    return list(STUDIO_TARGET_PLATFORMS)


def resolve_studio_branches(branch: Optional[str]) -> List[str]:
    """Resolves a CLI --branch value into TestActions ab_branch filter values."""
    if not branch or branch.strip() in (
        "git_emu-main-next",
        "git_studio-main",
        "studio-main",
    ):
        return ["git_studio-main", "git_studio-main-periodic"]
    return [branch.strip()]


def normalize_target_prefix(directory: Optional[str]) -> str:
    """Normalizes a directory or Bazel package prefix into a '//pkg/...' prefix."""
    raw = (directory or DEFAULT_STUDIO_DIR_PREFIX).strip()
    if raw.startswith("studio-main/"):
        raw = raw[len("studio-main/") :]
    raw = raw.lstrip("/")
    if not raw.startswith("//"):
        raw = "//" + raw
    return raw.rstrip("/")


def _build_mode_filter_sql(mode: str) -> str:
    """Builds the ab_build_id filter matching postsubmit, presubmit, or all runs."""
    normalized = (mode or "postsubmit").strip().lower()
    if normalized == "postsubmit":
        return "AND NOT STARTS_WITH(ab_build_id, 'P')"
    if normalized == "presubmit":
        return "AND STARTS_WITH(ab_build_id, 'P')"
    return ""


def _build_normalize_ab_target_udf(targets: List[str]) -> str:
    """Builds the NormalizeAbTarget temp SQL function mapping -flake-reruns to base targets."""
    when_lines = []
    for t in targets:
        _validate_sql_string("target", t)
        when_lines.append(f"    WHEN '{t}' THEN '{t}'")
        when_lines.append(f"    WHEN '{t}-flake-reruns' THEN '{t}'")
    when_block = "\n".join(when_lines)
    return (
        "CREATE TEMP FUNCTION NormalizeAbTarget(ab_target STRING)\n"
        "RETURNS STRING\n"
        "AS (\n"
        "  CASE ab_target\n"
        f"{when_block}\n"
        "    ELSE ab_target\n"
        "  END\n"
        ");"
    )


def _format_target_in_list(targets: List[str]) -> str:
    """Formats base targets and their -flake-reruns counterparts as a SQL IN list."""
    expanded: List[str] = []
    for t in targets:
        _validate_sql_string("target", t)
        expanded.append(f"'{t}'")
        expanded.append(f"'{t}-flake-reruns'")
    return ", ".join(expanded)


def _format_string_in_list(param_name: str, values: List[str]) -> str:
    """Formats validated string values as a SQL IN list."""
    quoted: List[str] = []
    for v in values:
        _validate_sql_string(param_name, v)
        quoted.append(f"'{v}'")
    return ", ".join(quoted)


def build_studio_flakes_sql(
    targets: List[str],
    branches: List[str],
    directory_prefix: str = DEFAULT_STUDIO_DIR_PREFIX,
    days: int = DEFAULT_STUDIO_LOOKBACK_DAYS,
    mode: str = "postsubmit",
    test_target: Optional[str] = None,
) -> str:
    """Constructs the F1 SQL query matching the `studio-flakes 3.2` Plx dashboard logic.

    Computes rolling `last100` and `last250` failure rates per `(ab_target, target_id)`
    from `android_devtools_infra.TestActions` and uses `LAST_VALUE(... IGNORE NULLS)`
    so `latest_failed_invocation_id` reflects the chronologically newest failed run.
    """
    if not targets:
        raise ValueError("targets must not be empty")
    if not branches:
        raise ValueError("branches must not be empty")

    clean_prefix = normalize_target_prefix(directory_prefix)
    _validate_sql_string("directory_prefix", clean_prefix)

    target_filter_sql = f"AND STARTS_WITH(target_id, '{clean_prefix}')"
    if test_target:
        clean_test = sanitize_bazel_target(test_target)
        _validate_sql_string("test_target", clean_test)
        target_filter_sql = f"AND target_id = '{clean_test}'"

    udf_sql = _build_normalize_ab_target_udf(targets)
    targets_in = _format_target_in_list(targets)
    branches_in = _format_string_in_list("branch", branches)
    mode_sql = _build_mode_filter_sql(mode)

    return f"""{udf_sql}

WITH
  targets_per_invocation AS (
    SELECT
      NormalizeAbTarget(ab_target_name) AS ab_target,
      ab_build_id,
      target_id,
      run_number,
      invocation_id,
      MAX(ab_build_id)
        OVER (PARTITION BY NormalizeAbTarget(ab_target_name), target_id)
        AS latest_build_id,
      MAX(run_number)
        OVER (PARTITION BY invocation_id, target_id)
        AS max_run_number,
      LOGICAL_AND(test_status = 'PASSED') AS passed
    FROM android_devtools_infra.TestActions
    WHERE
      ab_target_name IN ({targets_in})
      AND ab_branch IN ({branches_in})
      AND invocation_status IN ('PASSED', 'FAILED')
      AND invocation_start_timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {int(days)} DAY)
      {mode_sql}
      {target_filter_sql}
    GROUP BY ab_target_name, ab_build_id, target_id, run_number, invocation_id
  ),
  target_history AS (
    SELECT DISTINCT
      ab_target,
      ab_build_id,
      latest_build_id,
      target_id,
      run_number,
      max_run_number,
      invocation_id AS latest_invocation_id,
      COUNTIF(NOT passed) OVER (last100) AS l100_failed,
      COUNT(passed) OVER (last100) AS l100_total,
      COUNTIF(NOT passed) OVER (last250) AS l250_failed,
      COUNT(passed) OVER (last250) AS l250_total,
      LAST_VALUE(IF(NOT passed, ab_build_id, NULL) IGNORE NULLS) OVER (last250) AS latest_failed_build_id,
      LAST_VALUE(IF(NOT passed, invocation_id, NULL) IGNORE NULLS) OVER (last250) AS latest_failed_invocation_id
    FROM targets_per_invocation
    WINDOW
      last100 AS (
        PARTITION BY ab_target, target_id
        ORDER BY ab_build_id, run_number, invocation_id
        ROWS 99 PRECEDING
      ),
      last250 AS (
        PARTITION BY ab_target, target_id
        ORDER BY ab_build_id, run_number, invocation_id
        ROWS 249 PRECEDING
      )
  )
SELECT
  target_id,
  ab_target,
  latest_build_id,
  latest_invocation_id,
  l100_failed,
  l100_total,
  ROUND(SAFE_DIVIDE(l100_failed, l100_total) * 100.0, 2) AS l100_flake_rate_pct,
  l250_failed,
  l250_total,
  ROUND(SAFE_DIVIDE(l250_failed, l250_total) * 100.0, 2) AS l250_flake_rate_pct,
  latest_failed_build_id,
  latest_failed_invocation_id
FROM target_history
WHERE
  ab_build_id = latest_build_id
  AND run_number = max_run_number
QUALIFY
  ROW_NUMBER() OVER (
    PARTITION BY ab_target, target_id
    ORDER BY latest_invocation_id DESC
  ) = 1
ORDER BY l100_failed DESC, l250_failed DESC, target_id, ab_target;
"""


def build_studio_history_sql(
    test_target: str,
    targets: List[str],
    branches: List[str],
    days: int = DEFAULT_STUDIO_LOOKBACK_DAYS,
    mode: str = "all",
    limit: int = 50,
) -> str:
    """Constructs the F1 SQL query for chronological run history of a single test target."""
    if not targets:
        raise ValueError("targets must not be empty")
    if not branches:
        raise ValueError("branches must not be empty")

    clean_test = sanitize_bazel_target(test_target)
    _validate_sql_string("test_target", clean_test)

    udf_sql = _build_normalize_ab_target_udf(targets)
    targets_in = _format_target_in_list(targets)
    branches_in = _format_string_in_list("branch", branches)
    mode_sql = _build_mode_filter_sql(mode)

    return f"""{udf_sql}

SELECT
  NormalizeAbTarget(ab_target_name) AS ab_target,
  ab_target_name AS raw_ab_target,
  ab_build_id,
  target_id,
  run_number,
  invocation_id,
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S', MIN(invocation_start_timestamp), 'UTC') AS timestamp_utc,
  LOGICAL_AND(test_status = 'PASSED') AS passed,
  COUNTIF(test_status != 'PASSED') AS failed_actions,
  COUNT(*) AS total_actions
FROM android_devtools_infra.TestActions
WHERE
  ab_target_name IN ({targets_in})
  AND ab_branch IN ({branches_in})
  AND invocation_status IN ('PASSED', 'FAILED')
  AND invocation_start_timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {int(days)} DAY)
  {mode_sql}
  AND target_id = '{clean_test}'
GROUP BY ab_target_name, ab_build_id, target_id, run_number, invocation_id
ORDER BY MIN(invocation_start_timestamp) DESC, ab_build_id DESC, run_number DESC
LIMIT {int(limit)};
"""


def execute_f1_jsonl_query(
    sql: str,
    f1_sql_bin: str = DEFAULT_F1_SQL_BIN,
    accounting_group: Optional[str] = None,
    timeout: int = 300,
) -> List[Dict[str, Any]]:
    """Executes a read-only F1 SQL query via --input_file / --output_file and parses JSONL rows."""
    if not os.path.exists(f1_sql_bin):
        raise FileNotFoundError(
            f"f1-sql binary not found at '{f1_sql_bin}'. Ensure you are running on a GLinux host."
        )

    sandbox_base = get_flakiness_sandbox_dir("f1_sql")
    with tempfile.TemporaryDirectory(dir=str(sandbox_base), prefix="query_") as tmpdir:
        tmp_path = Path(tmpdir)
        sql_path = tmp_path / "query.sql"
        out_path = tmp_path / "output.jsonl"
        rio_path = tmp_path / "query_info.rio"

        normalized_sql = sql.strip()
        if not normalized_sql.endswith(";"):
            normalized_sql += ";"
        sql_path.write_text(normalized_sql + "\n", encoding="utf-8")

        cmd = [
            f1_sql_bin,
            "readonly",
            f"--input_file={sql_path}",
            f"--output_file={out_path}",
            f"--query_info_output_file={rio_path}",
            "--quiet=true",
            "--pager_output=false",
            "--f1_sql_show_query_progress=false",
            "--jsonl_output=true",
            "--print_queries=false",
            "--f1_sql_force_output_profile_link=true",
        ]
        if accounting_group:
            cmd.append(f"--f1_sql_accounting_group={accounting_group}")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            outs, errs = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            proc.communicate()
            raise RuntimeError(
                f"f1-sql query timed out after {timeout} seconds."
            ) from exc

        if proc.returncode != 0:
            err_msg = (errs or outs or f"Exit code {proc.returncode}").strip()
            raise RuntimeError(f"f1-sql query failed: {err_msg}")

        rows: List[Dict[str, Any]] = []
        if out_path.is_file():
            for line in out_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped:
                    rows.append(json.loads(stripped))
        return rows


def _module_name_from_target(target_id: str) -> str:
    """Extracts a concise module/package name from a Bazel target label."""
    clean = sanitize_bazel_target(target_id)
    pkg = clean.split(":", 1)[0].lstrip("@/")
    parts = [p for p in pkg.split("/") if p]
    return parts[-1] if parts else clean


def query_studio_flaky_tests(
    target: str = "studio-linux",
    directory: Optional[str] = None,
    branch: str = DEFAULT_STUDIO_BRANCH,
    min_flake_rate: float = 0.0,
    min_failed_runs: int = 0,
    days: int = DEFAULT_STUDIO_LOOKBACK_DAYS,
    mode: str = "postsubmit",
    window: int = 100,
    test_target: Optional[str] = None,
    accounting_group: Optional[str] = None,
    f1_sql_bin: str = DEFAULT_F1_SQL_BIN,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> List[FlakyTestRecord]:
    """Queries `android_devtools_infra.TestActions` for flaky Studio / Android Device Hub tests."""
    if window not in (100, 250):
        raise ValueError(f"Unsupported window={window}; expected 100 or 250.")

    targets = resolve_studio_targets(target)
    branches = resolve_studio_branches(branch)
    dir_prefix = normalize_target_prefix(directory)

    sql = build_studio_flakes_sql(
        targets=targets,
        branches=branches,
        directory_prefix=dir_prefix,
        days=days,
        mode=mode,
        test_target=test_target,
    )

    if progress_callback:
        progress_callback(
            0,
            1,
            f"Querying F1 TestActions for {dir_prefix} across {', '.join(targets)} ({days}d)...",
        )

    rows = execute_f1_jsonl_query(
        sql=sql,
        f1_sql_bin=f1_sql_bin,
        accounting_group=accounting_group,
    )

    if progress_callback:
        progress_callback(
            1,
            1,
            f"Fetched {len(rows)} target records from F1 TestActions.",
        )

    records: List[FlakyTestRecord] = []
    for row in rows:
        target_id = str(row.get("target_id") or "")
        ab_target = str(row.get("ab_target") or target)
        l100_failed = int(row.get("l100_failed") or 0)
        l100_total = int(row.get("l100_total") or 0)
        l100_rate = float(row.get("l100_flake_rate_pct") or 0.0)
        l250_failed = int(row.get("l250_failed") or 0)
        l250_total = int(row.get("l250_total") or 0)
        l250_rate = float(row.get("l250_flake_rate_pct") or 0.0)

        latest_build_id = str(row.get("latest_build_id") or "")
        latest_inv_id = str(row.get("latest_invocation_id") or "")
        latest_failed_build = (
            str(row["latest_failed_build_id"])
            if row.get("latest_failed_build_id")
            else None
        )
        latest_failed_inv = (
            str(row["latest_failed_invocation_id"])
            if row.get("latest_failed_invocation_id")
            else None
        )

        if window == 250:
            total_runs = l250_total
            failed_runs = l250_failed
            flake_rate_pct = l250_rate
        else:
            total_runs = l100_total
            failed_runs = l100_failed
            flake_rate_pct = l100_rate

        if failed_runs < min_failed_runs:
            continue

        if flake_rate_pct < min_flake_rate and not (
            min_flake_rate == 0.0 and total_runs > 0
        ):
            continue

        # Keep latest_build_id as the newest build, while pointing latest_invocation_id
        # to the latest failed run when the active window has failures so triage and
        # sponge inspect the failing invocation directly.
        effective_inv_id = (
            latest_failed_inv
            if failed_runs > 0 and latest_failed_inv
            else latest_inv_id
        )

        records.append(
            FlakyTestRecord(
                test_identifier=target_id,
                module_name=_module_name_from_target(target_id),
                target=ab_target,
                config_name=dir_prefix,
                total_runs=total_runs,
                failed_runs=failed_runs,
                flake_rate_pct=flake_rate_pct,
                latest_build_id=latest_build_id,
                latest_invocation_id=effective_inv_id,
                latest_work_unit_id="",
                l100_failed=l100_failed,
                l100_total=l100_total,
                l100_flake_rate_pct=l100_rate,
                l250_failed=l250_failed,
                l250_total=l250_total,
                l250_flake_rate_pct=l250_rate,
                latest_failed_build_id=latest_failed_build,
                latest_failed_invocation_id=latest_failed_inv,
            )
        )

    records.sort(
        key=lambda r: (
            r.failed_runs,
            r.flake_rate_pct,
            r.l250_failed or 0,
            r.total_runs,
        ),
        reverse=True,
    )
    return records


def query_studio_test_history(
    test_target: str,
    target: str = "studio-linux",
    branch: str = DEFAULT_STUDIO_BRANCH,
    days: int = DEFAULT_STUDIO_LOOKBACK_DAYS,
    mode: str = "all",
    limit: int = 50,
    accounting_group: Optional[str] = None,
    f1_sql_bin: str = DEFAULT_F1_SQL_BIN,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[Dict[str, Any], List[TestHistoryEntry]]:
    """Queries `android_devtools_infra.TestActions` for chronological run history of a test target."""
    clean_target = sanitize_bazel_target(test_target)
    targets = resolve_studio_targets(target)
    branches = resolve_studio_branches(branch)

    sql = build_studio_history_sql(
        test_target=clean_target,
        targets=targets,
        branches=branches,
        days=days,
        mode=mode,
        limit=limit,
    )

    if progress_callback:
        progress_callback(
            0,
            1,
            f"Querying F1 TestActions history for {clean_target} on {', '.join(targets)}...",
        )

    rows = execute_f1_jsonl_query(
        sql=sql,
        f1_sql_bin=f1_sql_bin,
        accounting_group=accounting_group,
    )

    if progress_callback:
        progress_callback(
            1,
            1,
            f"Fetched {len(rows)} historical runs from F1 TestActions.",
        )

    entries: List[TestHistoryEntry] = []
    for row in rows:
        inv_id = str(row.get("invocation_id") or "")
        build_id = str(row.get("ab_build_id") or "")
        ab_tgt = str(row.get("ab_target") or target)
        raw_ab_tgt = str(row.get("raw_ab_target") or ab_tgt)
        passed = bool(row.get("passed"))
        ts_str = str(row.get("timestamp_utc") or "N/A")
        run_type = "Presubmit" if build_id.startswith("P") else "Postsubmit"
        if raw_ab_tgt.endswith("-flake-reruns"):
            run_type = f"{run_type} (flake-rerun)"
        test_uri = (
            f"https://fusion2.corp.google.com/invocations/{inv_id}"
            if inv_id
            else ""
        )
        entries.append(
            TestHistoryEntry(
                invocation_id=inv_id,
                build_id=build_id,
                target=ab_tgt,
                timestamp=ts_str,
                status="PASSED" if passed else "FAILED",
                run_type=run_type,
                test_uri=test_uri,
                work_unit_id="",
                test_identifier=clean_target,
            )
        )

    total_runs = len(entries)
    failed_runs = sum(1 for e in entries if e.status == "FAILED")
    passed_runs = total_runs - failed_runs
    flake_rate = (failed_runs / total_runs * 100.0) if total_runs > 0 else 0.0

    summary: Dict[str, Any] = {
        "test_target": clean_target,
        "test_identifier": clean_target,
        "target": target,
        "branch": ", ".join(branches),
        "mode": mode,
        "days": days,
        "total_runs": total_runs,
        "passed_runs": passed_runs,
        "failed_runs": failed_runs,
        "flake_rate_pct": flake_rate,
        "displayed_runs": total_runs,
    }
    return summary, entries
