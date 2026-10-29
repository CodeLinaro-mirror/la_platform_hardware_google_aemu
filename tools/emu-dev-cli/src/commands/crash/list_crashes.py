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

"""Production qemu-next crash discovery subcommand for emu-dev-cli (`crash list-crashes`).

Queries `crash.prod.latest` joined with `crash_annotations.prod.latest` via F1 SQL
(`/google/bin/releases/f1-team/public/f1-sql`) and cross-checks live bug associations via
`QueryAPIService.SearchAnnotations` on `blade:crash-queryapi` so newly linked bugs are
reflected immediately without waiting for the hourly F1 annotations table refresh.
"""

import argparse
import csv
import io
import os
import subprocess
import sys
from typing import Any

from core.command import (
    Command,
    CommandResult,
    META_KEY_ALREADY_RENDERED,
    bind_command,
)
from core.context import (
    ExecutionContext,
    SERVICE_F1_SQL,
    SERVICE_PROCESS_RUNNER,
)
from core.errors import ExecutionError, UsageError
from lib.output import print_result

from commands.crash.link_bug import (
    DEFAULT_F1_SQL_BIN,
    DEFAULT_PRODUCT_NAME,
    search_crash_annotations,
)

DEFAULT_HOURS = 48
DEFAULT_VERSION_REGEX = r"0\.0\.1-|37"
DEFAULT_INCLUDE_MODULES = (
    r"qemu-system|emulator|fishtank|aquarium|android-device-hub|libgoldfish|gfxstream"
)
DEFAULT_EXCLUDE_MODULES = r"Qt6|libandroid-emu|adb|netsimd"
DEFAULT_LIMIT = 50


def extract_build_ids_from_versions(versions: list[str]) -> list[str]:
    """Extracts unique numeric Android Build IDs from version strings like '37.1.3-16373346'."""
    build_ids: list[str] = []
    for ver in versions:
        if "-" in ver:
            suffix = ver.rsplit("-", 1)[-1].strip()
            if suffix and suffix not in build_ids:
                build_ids.append(suffix)
    return build_ids


def _crash_location_sql_expr(alias: str = "c") -> str:
    """Returns SQL expression extracting the top emulator fault frame with SourceFileName."""
    return f"""COALESCE(
      (
        SELECT CONCAT(
          IFNULL(NULLIF(f.FunctionName, ''), '?'),
          ' (', f.SourceFileName, ':', CAST(IFNULL(f.SourceLine, 0) AS STRING), ')'
        )
        FROM UNNEST({alias}.CrashedStackTrace.StackFrame) AS f WITH OFFSET pos
        WHERE f.SourceFileName IS NOT NULL
          AND f.SourceFileName != ''
          AND NOT REGEXP_CONTAINS(f.SourceFileName, r'abseil-cpp|crashpad|crash_reporter\\.cc|include/c\\+\\+/v1/')
        ORDER BY pos ASC
        LIMIT 1
      ),
      (
        SELECT CONCAT(
          IFNULL(NULLIF(f.FunctionName, ''), '?'),
          CASE
            WHEN f.SourceFileName IS NOT NULL AND f.SourceFileName != ''
            THEN CONCAT(' (', f.SourceFileName, ':', CAST(IFNULL(f.SourceLine, 0) AS STRING), ')')
            ELSE ''
          END
        )
        FROM UNNEST({alias}.CrashedStackTrace.StackFrame) AS f WITH OFFSET pos
        WHERE f.FunctionName IS NOT NULL AND f.FunctionName != ''
        ORDER BY pos ASC
        LIMIT 1
      )
    )"""


def _stack_preview_sql_expr(alias: str = "c", max_frames: int = 14) -> str:
    """Returns SQL expression extracting the top symbolicated stack frames from F1."""
    return f"""ARRAY_TO_STRING(
      ARRAY(
        SELECT CONCAT(
          '#', CAST(pos AS STRING), ': ',
          IFNULL(NULLIF(f.FunctionName, ''), '?'),
          CASE
            WHEN f.SourceFileName IS NOT NULL AND f.SourceFileName != ''
            THEN CONCAT(' (', f.SourceFileName, ':', CAST(IFNULL(f.SourceLine, 0) AS STRING), ')')
            WHEN f.Module.CodeFile IS NOT NULL AND f.Module.CodeFile != ''
            THEN CONCAT(' [', f.Module.CodeFile, ']')
            WHEN f.ModuleIndex >= 0 AND {alias}.Module[SAFE_OFFSET(f.ModuleIndex)].CodeFile IS NOT NULL
            THEN CONCAT(' [', {alias}.Module[SAFE_OFFSET(f.ModuleIndex)].CodeFile, ']')
            ELSE ''
          END
        )
        FROM UNNEST({alias}.CrashedStackTrace.StackFrame) AS f WITH OFFSET pos
        WHERE pos < {int(max_frames)}
        ORDER BY pos ASC
      ),
      ' || '
    )"""


def build_f1_crashes_query(
    hours: int = DEFAULT_HOURS,
    version_regex: str = DEFAULT_VERSION_REGEX,
    exclude_modules: str = DEFAULT_EXCLUDE_MODULES,
    include_modules: str = DEFAULT_INCLUDE_MODULES,
    unassigned_only: bool = False,
    raw: bool = False,
    limit: int = DEFAULT_LIMIT,
    product_name: str = DEFAULT_PRODUCT_NAME,
) -> str:
    """Constructs the F1 SQL query against `crash.prod.latest` and `crash_annotations.prod.latest`.

    Args:
        hours: Lookback window in hours.
        version_regex: RE2 regex matching `product.Version` (default: `0\\.0\\.1-|37`).
        exclude_modules: RE2 regex of non-qemu-next module `CodeFile` substrings to exclude.
        include_modules: RE2 regex of required emulator module `CodeFile` substrings to include.
        unassigned_only: If True, filters out signatures with existing `BugInfo` in F1.
        raw: If True, returns ungrouped individual crash reports instead of grouping by signature.
        limit: Maximum number of rows to return.
        product_name: Crash product name (default: 'AndroidEmulator').

    Returns:
        SQL query string.

    Raises:
        ValueError: If SQL string/regex parameters contain single quotes.
    """
    for param_name, param_val in (
        ("product_name", product_name),
        ("version_regex", version_regex),
        ("exclude_modules", exclude_modules),
        ("include_modules", include_modules),
    ):
        if "'" in param_val:
            raise ValueError(f"Invalid single quote in {param_name}: {param_val!r}")

    loc_expr = _crash_location_sql_expr("c")
    stack_expr = _stack_preview_sql_expr("c")
    where_clauses = [
        f"c.product_name = '{product_name}'",
        "NOT REGEXP_CONTAINS(c.product.Version, r'developer|redacted')",
    ]
    if version_regex:
        where_clauses.append(f"REGEXP_CONTAINS(c.product.Version, r'{version_regex}')")
    if include_modules or exclude_modules:
        module_preds = ["COUNT(*) > 1"]
        if include_modules:
            module_preds.append(
                f"LOGICAL_OR(REGEXP_CONTAINS(CodeFile, r'{include_modules}'))"
            )
        if exclude_modules:
            module_preds.append(
                f"LOGICAL_AND(NOT REGEXP_CONTAINS(CodeFile, r'{exclude_modules}'))"
            )
        joined_preds = "\n      AND ".join(module_preds)
        where_clauses.append(
            "(\n"
            f"    SELECT {joined_preds}\n"
            "    FROM UNNEST(c.Module)\n"
            "  )"
        )
    where_clauses.append(
        f"c.upload_time >= UNIX_MILLIS(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {int(hours)} HOUR))"
    )
    if unassigned_only:
        where_clauses.append("(a.BugInfo IS NULL OR ARRAY_LENGTH(a.BugInfo) = 0)")

    where_sql = "\n  AND ".join(where_clauses)

    if raw:
        return f"""SELECT
  c.stable_signature AS stable_signature,
  c.ReportID AS report_id,
  c.product.Version AS version,
  c.os.Name AS os_name,
  {loc_expr} AS crash_location,
  {stack_expr} AS stack_preview,
  (SELECT Value FROM UNNEST(c.ProductData) WHERE Key = 'internal-msg' LIMIT 1) AS internal_msg,
  (SELECT Value FROM UNNEST(c.ProductData) WHERE Key = 'looper_registrations' LIMIT 1) AS looper_registrations,
  (SELECT Value FROM UNNEST(c.ProductData) WHERE Key = 'list_annotations' LIMIT 1) AS list_annotations,
  ARRAY_TO_STRING(
    ARRAY(SELECT DISTINCT b.BugUrl FROM UNNEST(a.BugInfo) AS b WHERE b.BugUrl IS NOT NULL AND b.BugUrl != ''),
    '; '
  ) AS bug_urls,
  ARRAY_TO_STRING(
    ARRAY(SELECT DISTINCT CAST(b.BugID AS STRING) FROM UNNEST(a.BugInfo) AS b WHERE b.BugID IS NOT NULL AND b.BugID > 0),
    '; '
  ) AS bug_ids
FROM crash.prod.latest AS c
LEFT JOIN crash_annotations.prod.latest AS a
  ON a.Product = c.product_name
  AND a.SignatureType = 'Stable'
  AND a.StackSignature = c.stable_signature
WHERE {where_sql}
ORDER BY c.upload_time DESC
LIMIT {int(limit)};
"""

    return f"""SELECT
  c.stable_signature AS stable_signature,
  COUNT(*) AS occurrences,
  ARRAY_AGG(c.ReportID ORDER BY c.upload_time DESC LIMIT 1)[SAFE_OFFSET(0)] AS latest_report_id,
  ARRAY_TO_STRING(ARRAY_AGG(DISTINCT c.product.Version IGNORE NULLS LIMIT 10), '; ') AS versions,
  ARRAY_TO_STRING(ARRAY_AGG(DISTINCT c.os.Name IGNORE NULLS LIMIT 10), '; ') AS os_names,
  ANY_VALUE({loc_expr}) AS crash_location,
  ANY_VALUE({stack_expr}) AS stack_preview,
  ANY_VALUE((SELECT Value FROM UNNEST(c.ProductData) WHERE Key = 'internal-msg' LIMIT 1)) AS internal_msg,
  ANY_VALUE((SELECT Value FROM UNNEST(c.ProductData) WHERE Key = 'looper_registrations' LIMIT 1)) AS looper_registrations,
  ANY_VALUE((SELECT Value FROM UNNEST(c.ProductData) WHERE Key = 'list_annotations' LIMIT 1)) AS list_annotations,
  ANY_VALUE(ARRAY_TO_STRING(
    ARRAY(SELECT DISTINCT b.BugUrl FROM UNNEST(a.BugInfo) AS b WHERE b.BugUrl IS NOT NULL AND b.BugUrl != ''),
    '; '
  )) AS bug_urls,
  ANY_VALUE(ARRAY_TO_STRING(
    ARRAY(SELECT DISTINCT CAST(b.BugID AS STRING) FROM UNNEST(a.BugInfo) AS b WHERE b.BugID IS NOT NULL AND b.BugID > 0),
    '; '
  )) AS bug_ids
FROM crash.prod.latest AS c
LEFT JOIN crash_annotations.prod.latest AS a
  ON a.Product = c.product_name
  AND a.SignatureType = 'Stable'
  AND a.StackSignature = c.stable_signature
WHERE {where_sql}
GROUP BY c.stable_signature
ORDER BY occurrences DESC
LIMIT {int(limit)};
"""


def parse_f1_csv_output(raw_stdout: str) -> list[dict[str, str]]:
    """Strips leading F1 CLI banner/hint lines and parses CSV rows into dictionaries."""
    lines = raw_stdout.splitlines()
    header_idx: int | None = None
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("stable_signature,", '"stable_signature",')):
            header_idx = idx
            break

    if header_idx is None:
        return []

    csv_text = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(csv_text))
    rows: list[dict[str, str]] = []
    for row in reader:
        cleaned_row = {
            k: ("" if v in (None, "NULL") else v) for k, v in row.items() if k
        }
        rows.append(cleaned_row)
    return rows


def execute_f1_query(
    sql: str,
    f1_sql_bin: str = DEFAULT_F1_SQL_BIN,
    accounting_group: str | None = None,
    timeout: int = 120,
) -> list[dict[str, str]]:
    """Executes an F1 SQL query via `f1-sql --csv_output=true` and returns parsed rows."""
    if not os.path.exists(f1_sql_bin):
        raise FileNotFoundError(
            f"f1-sql binary not found at '{f1_sql_bin}'. Ensure you are running on a GLinux host."
        )

    cmd = [f1_sql_bin, "--csv_output=true"]
    if accounting_group:
        cmd.append(f"--f1_sql_accounting_group={accounting_group}")

    res = subprocess.run(
        cmd,
        input=sql,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if res.returncode != 0:
        err_msg = (res.stderr or res.stdout or f"Exit code {res.returncode}").strip()
        raise RuntimeError(f"f1-sql query failed: {err_msg}")

    return parse_f1_csv_output(res.stdout)


def merge_and_normalize_rows(
    csv_rows: list[dict[str, str]],
    live_annotations: dict[str, list[dict[str, str]]],
    raw: bool = False,
    unassigned_only: bool = False,
) -> list[dict[str, Any]]:
    """Merges F1 SQL rows with live `SearchAnnotations` bug links and formats output fields."""
    records: list[dict[str, Any]] = []
    for row in csv_rows:
        sig = row.get("stable_signature", "").strip()
        f1_urls = [
            u.strip() for u in row.get("bug_urls", "").split(";") if u and u.strip()
        ]
        f1_ids = [
            b.strip() for b in row.get("bug_ids", "").split(";") if b and b.strip()
        ]

        live_bugs = live_annotations.get(sig, [])
        merged_urls = list(f1_urls)
        merged_ids = list(f1_ids)
        for b in live_bugs:
            b_url = b.get("bug_url", "").strip()
            b_id = b.get("bug_id", "").strip()
            if b_id and b_id not in merged_ids:
                merged_ids.append(b_id)
            if (
                b_url
                and b_url not in merged_urls
                and (
                    not b_id
                    or not any(
                        existing.endswith(f"/{b_id}") for existing in merged_urls
                    )
                )
            ):
                merged_urls.append(b_url)

        if unassigned_only and (merged_urls or merged_ids):
            continue

        custom_keys: dict[str, str] = {}
        for key_col, key_name in (
            ("internal_msg", "internal-msg"),
            ("looper_registrations", "looper_registrations"),
            ("list_annotations", "list_annotations"),
        ):
            val = row.get(key_col, "").strip()
            if val:
                custom_keys[key_name] = val

        stack_frames = [
            f.strip()
            for f in row.get("stack_preview", "").split("||")
            if f and f.strip()
        ]

        if raw:
            version_str = row.get("version", "").strip()
            versions_list = [version_str] if version_str else []
            records.append(
                {
                    "stable_signature": sig,
                    "report_id": row.get("report_id", "").strip(),
                    "version": version_str,
                    "build_ids": extract_build_ids_from_versions(versions_list),
                    "os": row.get("os_name", "").strip(),
                    "crash_location": row.get("crash_location", "").strip(),
                    "stack_frames": stack_frames,
                    "custom_keys": custom_keys,
                    "bug_ids": merged_ids,
                    "bug_urls": merged_urls,
                }
            )
        else:
            versions_list = [
                v.strip() for v in row.get("versions", "").split(";") if v and v.strip()
            ]
            os_list = [
                o.strip() for o in row.get("os_names", "").split(";") if o and o.strip()
            ]
            try:
                occ = int(row.get("occurrences", "0"))
            except ValueError:
                occ = 0

            records.append(
                {
                    "stable_signature": sig,
                    "occurrences": occ,
                    "latest_report_id": row.get("latest_report_id", "").strip(),
                    "versions": versions_list,
                    "build_ids": extract_build_ids_from_versions(versions_list),
                    "os": os_list,
                    "crash_location": row.get("crash_location", "").strip(),
                    "stack_frames": stack_frames,
                    "custom_keys": custom_keys,
                    "bug_ids": merged_ids,
                    "bug_urls": merged_urls,
                }
            )

    return records





def _normalize_sql_rows_to_str(
    raw_rows: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Normalizes F1SqlClient rows into string dictionaries expected by `merge_and_normalize_rows`."""
    cleaned: list[dict[str, str]] = []
    for row in raw_rows:
        cleaned.append(
            {
                str(k): ("" if v in (None, "NULL") else str(v))
                for k, v in row.items()
                if k
            }
        )
    return cleaned


class CrashListCrashesCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Command implementation for `emu-dev-cli crash list-crashes`."""

    name = "list-crashes"
    help_text = "Query production qemu-next crashes from crash.prod.latest via F1 SQL and go/crash annotations"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.description = (
            "Queries crash.prod.latest joined with crash_annotations.prod.latest via f1-sql "
            "and cross-checks live QueryAPIService.SearchAnnotations on blade:crash-queryapi "
            "to discover and triage production qemu-next crashes."
        )
        parser.add_argument(
            "--hours",
            type=int,
            default=DEFAULT_HOURS,
            help=f"Lookback window in hours (default: {DEFAULT_HOURS})",
        )
        parser.add_argument(
            "--days",
            type=int,
            help="Lookback window in days (overrides --hours with days * 24)",
        )
        parser.add_argument(
            "--version-regex",
            default=DEFAULT_VERSION_REGEX,
            help=f"RE2 regex matching product.Version for qemu-next (default: '{DEFAULT_VERSION_REGEX}')",
        )
        parser.add_argument(
            "--exclude-modules",
            default=DEFAULT_EXCLUDE_MODULES,
            help=(
                "RE2 regex of non-qemu-next Module.CodeFile substrings to exclude "
                f"(default: '{DEFAULT_EXCLUDE_MODULES}')"
            ),
        )
        parser.add_argument(
            "--include-modules",
            default=DEFAULT_INCLUDE_MODULES,
            help=(
                "RE2 regex of required emulator Module.CodeFile substrings to include "
                f"(default: '{DEFAULT_INCLUDE_MODULES}')"
            ),
        )
        parser.add_argument(
            "--unassigned-only",
            "--untriaged",
            action="store_true",
            dest="unassigned_only",
            help="Filter out any stable_signature that already has a linked bug in go/crash",
        )
        parser.add_argument(
            "--raw",
            action="store_true",
            help="Return ungrouped individual crash reports instead of grouping by stable_signature",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=DEFAULT_LIMIT,
            help=f"Maximum number of clusters or rows to return (default: {DEFAULT_LIMIT})",
        )
        parser.add_argument(
            "--accounting-group",
            help="Optional F1 MDB accounting group (--f1_sql_accounting_group)",
        )
        parser.add_argument(
            "--print-sql",
            action="store_true",
            help="Print the generated F1 SQL query and exit without executing",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Output machine-readable JSON",
        )

    def _resolve_hours(self, args: argparse.Namespace) -> int:
        days = getattr(args, "days", None)
        return (
            int(days * 24)
            if days is not None
            else int(getattr(args, "hours", DEFAULT_HOURS))
        )

    def validate(self, args: argparse.Namespace, ctx: ExecutionContext) -> None:
        del ctx
        hours = self._resolve_hours(args)
        if hours <= 0:
            raise UsageError(
                f"Lookback window must be positive (got {hours} hours).",
                remediation="Pass a positive integer to --hours or --days.",
            )
        limit = int(getattr(args, "limit", DEFAULT_LIMIT))
        if limit <= 0:
            raise UsageError(
                f"Result limit must be positive (got {limit}).",
                remediation="Pass a positive integer to --limit (e.g. --limit 50).",
            )
        try:
            build_f1_crashes_query(
                hours=hours,
                version_regex=str(
                    getattr(args, "version_regex", DEFAULT_VERSION_REGEX)
                ),
                exclude_modules=str(
                    getattr(args, "exclude_modules", DEFAULT_EXCLUDE_MODULES)
                ),
                include_modules=str(
                    getattr(args, "include_modules", DEFAULT_INCLUDE_MODULES)
                ),
                unassigned_only=bool(getattr(args, "unassigned_only", False)),
                raw=bool(getattr(args, "raw", False)),
                limit=limit,
            )
        except ValueError as exc:
            raise UsageError(
                f"Invalid SQL filter parameter for 'crash list-crashes': {exc}",
                remediation="Remove single quotes from --version-regex, --include-modules, and --exclude-modules.",
            ) from exc
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        hours = self._resolve_hours(args)
        version_regex = str(
            getattr(args, "version_regex", DEFAULT_VERSION_REGEX)
        )
        exclude_modules = str(
            getattr(args, "exclude_modules", DEFAULT_EXCLUDE_MODULES)
        )
        include_modules = str(
            getattr(args, "include_modules", DEFAULT_INCLUDE_MODULES)
        )
        unassigned_only = bool(getattr(args, "unassigned_only", False))
        raw = bool(getattr(args, "raw", False))
        limit = int(getattr(args, "limit", DEFAULT_LIMIT))
        accounting_group = getattr(args, "accounting_group", None)

        sql = build_f1_crashes_query(
            hours=hours,
            version_regex=version_regex,
            exclude_modules=exclude_modules,
            include_modules=include_modules,
            unassigned_only=unassigned_only,
            raw=raw,
            limit=limit,
        )

        if getattr(args, "print_sql", False):
            json_mode = bool(
                ctx.config.json_mode or getattr(args, "json", False)
            )
            if not json_mode:
                print(sql.strip())
            return CommandResult(
                data={"sql": sql.strip()},
                action="crash list-crashes",
                summary="Generated F1 SQL query",
                metadata={META_KEY_ALREADY_RENDERED: not json_mode},
            )

        f1_client = ctx.services.get(SERVICE_F1_SQL)
        runner = ctx.services.get(SERVICE_PROCESS_RUNNER)
        if hasattr(f1_client, "query_csv"):
            raw_rows = f1_client.query_csv(
                sql,
                accounting_group=accounting_group,
            )
        else:
            raw_rows = f1_client.execute_sql(
                sql,
                accounting_group=accounting_group,
            )
        csv_rows = _normalize_sql_rows_to_str(raw_rows)

        unique_sigs = [
            r.get("stable_signature", "").strip()
            for r in csv_rows
            if r.get("stable_signature", "").strip()
        ]
        dedup_sigs = list(dict.fromkeys(unique_sigs))
        live_annotations = search_crash_annotations(dedup_sigs, runner=runner)
        crashes = merge_and_normalize_rows(
            csv_rows=csv_rows,
            live_annotations=live_annotations,
            raw=raw,
            unassigned_only=unassigned_only,
        )

        payload: dict[str, Any] = {
            "status": "success",
            "action": "crash list-crashes",
            "hours": hours,
            "version_regex": version_regex,
            "exclude_modules": exclude_modules,
            "include_modules": include_modules,
            "unassigned_only": unassigned_only,
            "raw": raw,
            "count": len(crashes),
            "crashes": crashes,
        }
        return CommandResult(
            data=payload,
            action="crash list-crashes",
            summary=f"Found {len(crashes)} crash cluster(s) in the last {hours} hours.",
        )


def register_list_crashes_parser(crash_subparsers) -> argparse.ArgumentParser:
    """Registers the `list-crashes` subcommand parser under `crash`."""
    return CrashListCrashesCommand().register(crash_subparsers)
