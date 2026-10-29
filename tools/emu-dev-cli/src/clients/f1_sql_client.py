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

"""Unified F1 SQL client supporting CSV, JSONL, and Capacitor table queries."""

from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Mapping, Sequence
import csv
from dataclasses import dataclass
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from core.errors import ExecutionError, SubprocessError, UsageError
from core.process import ProcessRunner

LOGGER = logging.getLogger(__name__)

DEFAULT_F1_SQL_BIN = "/google/bin/releases/f1-team/public/f1-sql"
FALLBACK_F1_SQL_BIN = "/google/data/ro/projects/storage/f1/tools/f1-sql"

DEFAULT_F1_CSV_TIMEOUT_SEC: float = 120.0
DEFAULT_F1_JSONL_TIMEOUT_SEC: float = 300.0
DEFAULT_F1_SCALAR_TIMEOUT_SEC: float = 60.0

FORMAT_CSV = "csv"
FORMAT_JSONL = "jsonl"
FORMAT_SCALAR = "scalar"
CAPACITOR_FORMAT_NAME = "capacitor"
SQL_NULL_LITERAL = "NULL"

UNSAFE_SQL_CHARS: tuple[str, ...] = ("'", '"', ";", "\\", "\n", "\r", "\x00")
SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_F1_BANNER_PREFIXES: tuple[str, ...] = (
    "I0",
    "W0",
    "E0",
    "F0",
    "WARNING",
    "INFO",
    "Hint:",
    "Connecting",
    "Connected",
    "Query ",
    "Profile:",
    "http://",
    "https://",
    "--",
)

F1_REMEDIATION = (
    "Ensure you are running on a GLinux host with valid LOAS credentials ('gcert') "
    "and access to the target F1 table or accounting group."
)


def validate_sql_string(param_name: str, param_value: str) -> str:
    """Validates that `param_value` contains no SQL metacharacters or control characters."""
    text = str(param_value)
    for unsafe_char in UNSAFE_SQL_CHARS:
        if unsafe_char in text:
            raise UsageError(
                f"SQL parameter '{param_name}' contains unsafe character {unsafe_char!r} "
                f"in value {param_value!r}.",
                remediation=(
                    f"Remove quotes, semicolons, backslashes, and newlines from '{param_name}'."
                ),
                details={"parameter": param_name, "rejected_char": repr(unsafe_char)},
            )
    return text


def validate_sql_identifier(param_name: str, identifier: str) -> str:
    """Validates that `identifier` is a strict `[A-Za-z_][A-Za-z0-9_]*` SQL identifier."""
    cleaned = str(identifier or "").strip()
    if not SQL_IDENTIFIER_PATTERN.match(cleaned):
        raise UsageError(
            f"SQL identifier '{param_name}' is invalid ({identifier!r}); "
            "expected letters, digits, and underscores starting with a letter or '_'.",
            remediation=f"Provide a valid SQL identifier for '{param_name}' (e.g. 'test_runs').",
            details={"parameter": param_name, "value": identifier},
        )
    return cleaned


def quote_sql_string(param_name: str, param_value: str) -> str:
    """Validates `param_value` and returns it wrapped in single quotes (`'...'`)."""
    validated = validate_sql_string(param_name, param_value)
    return f"'{validated}'"


def format_sql_in_list(param_name: str, values: Sequence[str]) -> str:
    """Formats a non-empty sequence of strings into a validated SQL `IN` list."""
    if not values:
        raise UsageError(
            f"SQL IN-list parameter '{param_name}' cannot be empty.",
            remediation=f"Provide at least one string value for '{param_name}'.",
        )
    return ", ".join(quote_sql_string(param_name, val) for val in values)


@dataclass(frozen=True)
class CapacitorTableBinding:
    """Defines a CNS Capacitor file-backed table binding for an F1 SQL query."""

    alias: str
    cns_path: str

    def __post_init__(self) -> None:
        clean_alias = validate_sql_identifier("alias", self.alias)
        clean_path = validate_sql_string("cns_path", self.cns_path.strip())
        if not clean_path:
            raise UsageError(
                f"Capacitor table '{clean_alias}' cns_path cannot be empty.",
                remediation="Provide a valid CNS or local Capacitor file path.",
            )
        object.__setattr__(self, "alias", clean_alias)
        object.__setattr__(self, "cns_path", clean_path)

    def to_sql_statement(self) -> str:
        """Formats the `DEFINE TABLE <alias> (format = 'capacitor', path = '...');` statement."""
        return (
            f"DEFINE TABLE {self.alias} "
            f"(format = '{CAPACITOR_FORMAT_NAME}', path = '{self.cns_path}');"
        )


def build_capacitor_preamble(
    tables: Sequence[CapacitorTableBinding] | None,
) -> str:
    """Builds the `DEFINE TABLE` preamble block for all `tables`."""
    if not tables:
        return ""
    return "\n".join(binding.to_sql_statement() for binding in tables)


def normalize_sql_query(
    sql: str,
    capacitor_tables: Sequence[CapacitorTableBinding] | None = None,
) -> str:
    """Normalizes `sql` with trailing semicolon and optional Capacitor table preamble."""
    body = (sql or "").strip()
    if not body:
        raise UsageError(
            "F1 SQL query string cannot be empty.",
            remediation="Provide a valid SQL SELECT statement.",
        )
    if not body.endswith(";"):
        body = f"{body};"
    preamble = build_capacitor_preamble(capacitor_tables)
    if not preamble:
        return body
    return f"{preamble}\n\n{body}"


def _is_banner_line(stripped_line: str) -> bool:
    """Returns True if `stripped_line` is an F1 CLI status/banner line rather than CSV data."""
    if not stripped_line:
        return True
    return stripped_line.startswith(_F1_BANNER_PREFIXES)


def _find_csv_header_index(
    lines: Sequence[str],
    expected_header_columns: Sequence[str] | None = None,
) -> int | None:
    """Finds the 0-based line index of the CSV header row in `lines`."""
    normalized_expected = (
        {col.strip().strip('"') for col in expected_header_columns if col.strip()}
        if expected_header_columns
        else set()
    )
    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if _is_banner_line(stripped):
            continue
        first_cell = stripped.split(",", 1)[0].strip().strip('"')
        if normalized_expected:
            if first_cell in normalized_expected:
                return idx
            continue
        if first_cell and SQL_IDENTIFIER_PATTERN.match(first_cell):
            return idx
    return None


def parse_f1_csv_output(
    raw_stdout: str,
    *,
    expected_header_columns: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    """Strips leading F1 CLI banner lines and parses CSV output into dictionaries."""
    if not raw_stdout or not raw_stdout.strip():
        return []
    lines = raw_stdout.splitlines()
    header_idx = _find_csv_header_index(lines, expected_header_columns)
    if header_idx is None:
        return []

    csv_text = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(csv_text))
    rows: list[dict[str, str]] = []
    for raw_row in reader:
        cleaned = {
            str(k): ("" if v in (None, SQL_NULL_LITERAL) else str(v))
            for k, v in raw_row.items()
            if k is not None
        }
        if cleaned:
            rows.append(cleaned)
    return rows


def parse_f1_jsonl_output(raw_jsonl: str) -> list[dict[str, Any]]:
    """Parses newline-delimited JSON objects produced by `f1-sql --jsonl_output=true`."""
    if not raw_jsonl or not raw_jsonl.strip():
        return []
    rows: list[dict[str, Any]] = []
    for line_num, raw_line in enumerate(raw_jsonl.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except ValueError as exc:
            raise ExecutionError(
                f"Failed to parse F1 JSONL output at line {line_num}: {exc}.",
                remediation="Inspect the F1 query output format and ensure --jsonl_output=true is supported.",
                details={"line_number": line_num},
                cause=exc,
            ) from exc
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _locate_f1_sql_binary(explicit_bin: str | None = None) -> str:
    """Resolves the `f1-sql` executable path."""
    if explicit_bin:
        return explicit_bin
    for candidate in (DEFAULT_F1_SQL_BIN, FALLBACK_F1_SQL_BIN):
        if os.path.exists(candidate):
            return candidate
    found = shutil.which("f1-sql")
    return found or DEFAULT_F1_SQL_BIN


class F1SqlClient(ABC):
    """Abstract interface for executing read-only F1 SQL queries."""

    @abstractmethod
    def query_csv(
        self,
        sql: str,
        *,
        accounting_group: str | None = None,
        timeout_sec: float = DEFAULT_F1_CSV_TIMEOUT_SEC,
        expected_header_columns: Sequence[str] | None = None,
        capacitor_tables: Sequence[CapacitorTableBinding] | None = None,
    ) -> list[dict[str, str]]:
        """Executes `sql` in CSV mode and returns parsed row dictionaries."""

    @abstractmethod
    def query_jsonl(
        self,
        sql: str,
        *,
        accounting_group: str | None = None,
        timeout_sec: float = DEFAULT_F1_JSONL_TIMEOUT_SEC,
        capacitor_tables: Sequence[CapacitorTableBinding] | None = None,
    ) -> list[dict[str, Any]]:
        """Executes `sql` in JSONL file mode and returns parsed JSON row dictionaries."""

    @abstractmethod
    def query_scalar(
        self,
        sql: str,
        *,
        accounting_group: str | None = None,
        timeout_sec: float = DEFAULT_F1_SCALAR_TIMEOUT_SEC,
    ) -> str | None:
        """Executes `sql` and returns the first column of the first row, or None."""


class ProdF1SqlClient(F1SqlClient):
    """Production F1SqlClient executing queries via ProcessRunner."""

    def __init__(
        self,
        process_runner: ProcessRunner,
        *,
        f1_sql_bin: str | None = None,
        default_accounting_group: str | None = None,
        scratch_dir: Path | None = None,
    ) -> None:
        self._process_runner = process_runner
        self._f1_sql_bin = _locate_f1_sql_binary(f1_sql_bin)
        self._default_accounting_group = default_accounting_group
        self._scratch_dir = scratch_dir

    @property
    def f1_sql_bin(self) -> str:
        """Returns the configured `f1-sql` executable path."""
        return self._f1_sql_bin

    def _build_csv_command(self, accounting_group: str | None) -> list[str]:
        cmd = [self._f1_sql_bin, "--csv_output=true"]
        effective_group = accounting_group or self._default_accounting_group
        if effective_group:
            cmd.append(f"--f1_sql_accounting_group={effective_group}")
        return cmd

    def _build_jsonl_command(
        self,
        sql_path: Path,
        out_path: Path,
        rio_path: Path,
        accounting_group: str | None,
    ) -> list[str]:
        cmd = [
            self._f1_sql_bin,
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
        effective_group = accounting_group or self._default_accounting_group
        if effective_group:
            cmd.append(f"--f1_sql_accounting_group={effective_group}")
        return cmd

    def query_csv(
        self,
        sql: str,
        *,
        accounting_group: str | None = None,
        timeout_sec: float = DEFAULT_F1_CSV_TIMEOUT_SEC,
        expected_header_columns: Sequence[str] | None = None,
        capacitor_tables: Sequence[CapacitorTableBinding] | None = None,
    ) -> list[dict[str, str]]:
        normalized_sql = normalize_sql_query(sql, capacitor_tables)
        cmd = self._build_csv_command(accounting_group)
        result = self._process_runner.run(
            cmd,
            input_text=normalized_sql,
            timeout_sec=timeout_sec,
            check=True,
            remediation=F1_REMEDIATION,
        )
        return parse_f1_csv_output(
            result.stdout, expected_header_columns=expected_header_columns
        )

    def query_jsonl(
        self,
        sql: str,
        *,
        accounting_group: str | None = None,
        timeout_sec: float = DEFAULT_F1_JSONL_TIMEOUT_SEC,
        capacitor_tables: Sequence[CapacitorTableBinding] | None = None,
    ) -> list[dict[str, Any]]:
        normalized_sql = normalize_sql_query(sql, capacitor_tables)
        base_dir = str(self._scratch_dir) if self._scratch_dir else None
        if base_dir:
            Path(base_dir).mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(
            dir=base_dir, prefix="emu_f1_sql_"
        ) as tmp_dir:
            tmp_path = Path(tmp_dir)
            sql_path = tmp_path / "query.sql"
            out_path = tmp_path / "output.jsonl"
            rio_path = tmp_path / "query_info.rio"
            sql_path.write_text(f"{normalized_sql}\n", encoding="utf-8")

            cmd = self._build_jsonl_command(
                sql_path, out_path, rio_path, accounting_group
            )
            result = self._process_runner.run(
                cmd,
                timeout_sec=timeout_sec,
                check=True,
                remediation=F1_REMEDIATION,
            )
            if out_path.is_file():
                raw_jsonl = out_path.read_text(encoding="utf-8")
                return parse_f1_jsonl_output(raw_jsonl)
            return parse_f1_jsonl_output(result.stdout)

    def query_scalar(
        self,
        sql: str,
        *,
        accounting_group: str | None = None,
        timeout_sec: float = DEFAULT_F1_SCALAR_TIMEOUT_SEC,
    ) -> str | None:
        try:
            rows = self.query_csv(
                sql,
                accounting_group=accounting_group,
                timeout_sec=timeout_sec,
            )
        except SubprocessError:
            raise
        if not rows:
            return None
        first_row = rows[0]
        for val in first_row.values():
            cleaned = val.strip()
            if cleaned and cleaned != SQL_NULL_LITERAL:
                return cleaned
        return None


@dataclass(frozen=True)
class FakeF1QueryCall:
    """Recorded F1 SQL invocation captured by FakeF1SqlClient."""

    sql: str
    output_format: str
    accounting_group: str | None
    timeout_sec: float
    capacitor_tables: tuple[CapacitorTableBinding, ...]


class FakeF1SqlClient(F1SqlClient):
    """Hermetic in-memory F1SqlClient for unit testing commands and workflows."""

    def __init__(self) -> None:
        self.calls: list[FakeF1QueryCall] = []
        self._queued_csv: deque[list[dict[str, str]]] = deque()
        self._queued_jsonl: deque[list[dict[str, Any]]] = deque()
        self._csv_routes: list[tuple[str, list[dict[str, str]]]] = []
        self._jsonl_routes: list[tuple[str, list[dict[str, Any]]]] = []

    def queue_csv_rows(self, rows: Sequence[Mapping[str, str]]) -> None:
        """Queues a one-shot list of CSV row dicts returned by the next `query_csv` call."""
        self._queued_csv.append(
            [{str(k): str(v) for k, v in row.items()} for row in rows]
        )

    def queue_jsonl_rows(self, rows: Sequence[Mapping[str, Any]]) -> None:
        """Queues a one-shot list of JSONL row dicts returned by the next `query_jsonl` call."""
        self._queued_jsonl.append([dict(row) for row in rows])

    def register_csv_query(
        self, sql_substring: str, rows: Sequence[Mapping[str, str]]
    ) -> None:
        """Registers canned CSV rows returned when `sql_substring` appears in `sql`."""
        self._csv_routes.append(
            (
                sql_substring,
                [{str(k): str(v) for k, v in row.items()} for row in rows],
            )
        )

    def register_jsonl_query(
        self, sql_substring: str, rows: Sequence[Mapping[str, Any]]
    ) -> None:
        """Registers canned JSONL rows returned when `sql_substring` appears in `sql`."""
        self._jsonl_routes.append(
            (sql_substring, [dict(row) for row in rows])
        )

    def query_csv(
        self,
        sql: str,
        *,
        accounting_group: str | None = None,
        timeout_sec: float = DEFAULT_F1_CSV_TIMEOUT_SEC,
        expected_header_columns: Sequence[str] | None = None,
        capacitor_tables: Sequence[CapacitorTableBinding] | None = None,
    ) -> list[dict[str, str]]:
        del expected_header_columns
        normalized_sql = normalize_sql_query(sql, capacitor_tables)
        self.calls.append(
            FakeF1QueryCall(
                sql=normalized_sql,
                output_format=FORMAT_CSV,
                accounting_group=accounting_group,
                timeout_sec=timeout_sec,
                capacitor_tables=tuple(capacitor_tables or ()),
            )
        )
        if self._queued_csv:
            return [dict(r) for r in self._queued_csv.popleft()]
        for substring, rows in self._csv_routes:
            if substring in normalized_sql:
                return [dict(r) for r in rows]
        return []

    def query_jsonl(
        self,
        sql: str,
        *,
        accounting_group: str | None = None,
        timeout_sec: float = DEFAULT_F1_JSONL_TIMEOUT_SEC,
        capacitor_tables: Sequence[CapacitorTableBinding] | None = None,
    ) -> list[dict[str, Any]]:
        normalized_sql = normalize_sql_query(sql, capacitor_tables)
        self.calls.append(
            FakeF1QueryCall(
                sql=normalized_sql,
                output_format=FORMAT_JSONL,
                accounting_group=accounting_group,
                timeout_sec=timeout_sec,
                capacitor_tables=tuple(capacitor_tables or ()),
            )
        )
        if self._queued_jsonl:
            return [dict(r) for r in self._queued_jsonl.popleft()]
        for substring, rows in self._jsonl_routes:
            if substring in normalized_sql:
                return [dict(r) for r in rows]
        return []

    def query_scalar(
        self,
        sql: str,
        *,
        accounting_group: str | None = None,
        timeout_sec: float = DEFAULT_F1_SCALAR_TIMEOUT_SEC,
    ) -> str | None:
        rows = self.query_csv(
            sql, accounting_group=accounting_group, timeout_sec=timeout_sec
        )
        if not rows:
            return None
        for val in rows[0].values():
            cleaned = val.strip()
            if cleaned and cleaned != SQL_NULL_LITERAL:
                return cleaned
        return None
