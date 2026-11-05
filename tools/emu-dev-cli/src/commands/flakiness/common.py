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

"""Runtime helpers shared by the `flakiness` Command classes."""

import argparse
from collections.abc import Callable, Iterator
import contextlib
from typing import Any, Optional

from core.command import CommandResult, META_KEY_ALREADY_RENDERED
from core.context import ExecutionContext
from core.errors import ExecutionError, UsageError
from lib.ath_api import SUPPORTED_TARGET_PLATFORMS, FlakyTestRecord, TestHistoryEntry
from lib.f1_test_actions import is_studio_query, query_studio_test_history

ProgressCallback = Optional[Callable[[int, int, str], None]]
AthFlakyQuery = Callable[..., list[FlakyTestRecord]]
HistoryQuery = Callable[..., tuple[dict[str, Any], list[TestHistoryEntry]]]

ALL_TARGETS = "all"
ATH_REMEDIATION = (
    "Check your network access to androidbuildinternal.googleapis.com and your "
    "credentials: export ANDROID_BUILD_TOKEN or pass --token, then retry."
)


def is_json_mode(args: argparse.Namespace, ctx: ExecutionContext) -> bool:
    """Returns True when the caller asked for JSON output."""
    return bool(ctx.config.json_mode or getattr(args, "json", False))


@contextlib.contextmanager
def query_errors_as_execution_errors(
    source: str, remediation: str = ATH_REMEDIATION
) -> Iterator[None]:
    """Turns the RuntimeError that lib query helpers raise into an ExecutionError."""
    try:
        yield
    except RuntimeError as exc:
        raise ExecutionError(
            f"{source} query failed: {exc}", remediation=remediation, cause=exc
        ) from exc


def ath_platforms(target: str) -> list[str]:
    """Expands `--target all` into every Android Test Hub platform."""
    return list(SUPPORTED_TARGET_PLATFORMS) if target == ALL_TARGETS else [target]


def query_ath_platforms(
    ath_query: AthFlakyQuery, platforms: list[str], **query_kwargs: Any
) -> list[FlakyTestRecord]:
    """Runs one Android Test Hub flaky-test query per platform and joins the records."""
    records: list[FlakyTestRecord] = []
    for platform in platforms:
        with query_errors_as_execution_errors(f"Android Test Hub ({platform})"):
            records.extend(ath_query(target=platform, **query_kwargs))
    return records


def sql_preview_result(
    sql: str, *, action: str, summary: str, json_mode: bool
) -> CommandResult[dict[str, Any]]:
    """Prints `--print-sql` output in text mode, or returns it as JSON data."""
    clean_sql = sql.strip()
    if not json_mode:
        print(clean_sql)
    return CommandResult(
        data={"sql": clean_sql},
        action=action,
        summary=summary,
        metadata={META_KEY_ALREADY_RENDERED: not json_mode},
    )


def require_positive_int(value: Any, *, flag: str, example: str) -> None:
    """Raises UsageError unless `value` (when given) is a positive integer."""
    if value is None:
        return
    text = str(value).strip()
    if not text.isdigit() or int(text) <= 0:
        raise UsageError(
            f"{flag} must be a positive integer (got '{value}').",
            remediation=f"Pass a positive integer, e.g. {flag} {example}.",
        )


def fetch_test_history(
    ctx: ExecutionContext,
    history_query: HistoryQuery,
    *,
    test_target: str,
    target: str,
    branch: str,
    days: int,
    mode: str,
    limit: int,
    token: str | None = None,
    config_name: str | None = None,
    accounting_group: str | None = None,
    progress_callback: ProgressCallback = None,
) -> tuple[dict[str, Any], list[TestHistoryEntry]]:
    """Fetches run history from F1 TestActions (Studio) or Android Test Hub."""
    if is_studio_query(target=target, branch=branch, test_target=test_target):
        return query_studio_test_history(
            test_target=test_target,
            target=target,
            branch=branch,
            days=days,
            mode=mode,
            limit=limit,
            accounting_group=accounting_group,
            progress_callback=progress_callback,
            f1_client=ctx.f1_sql_client,
        )
    with query_errors_as_execution_errors("Android Test Hub history"):
        return history_query(
            test_target=test_target,
            target=target,
            branch=branch,
            days=days,
            mode=mode,
            limit=limit,
            token=token,
            progress_callback=progress_callback,
            config_name=config_name,
        )

