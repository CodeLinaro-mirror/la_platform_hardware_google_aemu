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

"""Target and dependency resolution for Bazel tidy reports and refactoring scopes."""

import logging
from typing import List, Optional

from lib.bazel import BazelRunner

logger = logging.getLogger(__name__)


def resolve_report_targets(runner: BazelRunner, target: str) -> List[str]:
    """Derives the clang_tidy_test report target(s) from a Bazel target or wildcard pattern.

    Args:
        runner: BazelRunner instance for running cqueries.
        target: Target string (e.g. '@goldfish//emulator/libs/async:tidy', '...').

    Returns:
        List of tidy report targets (ending in '_report').
    """
    if "..." in target:
        try:
            query_expr = f'attr("name", ".*tidy$", {target})'
            query_res = runner.cquery(
                query_expr, invocation_flags=["--output=label"], check=False
            )
            if query_res.returncode == 0 and query_res.stdout.strip():
                targets = []
                for line in query_res.stdout.splitlines():
                    t = line.strip()
                    if t:
                        targets.append(t.replace(":tidy", ":tidy_report"))
                if targets:
                    return targets
        except Exception as e:
            logger.debug("Failed to query tidy targets for wildcard %s: %s", target, e)

    # Single target logic
    if not target.endswith("_report") and not target.endswith(":tidy"):
        if ":" in target:
            pkg, name = target.split(":", 1)
            return [f"{pkg}:{name}_report"]
        else:
            name = target.rstrip("/").split("/")[-1]
            return [f"{target}:{name}_report"]
    if target.endswith(":tidy"):
        return [target.replace(":tidy", ":tidy_report")]
    return [target]


def resolve_dependent_targets(
    runner: Optional[BazelRunner], target: str, level: int = 1
) -> List[str]:
    """Resolves compilation targets scoped to package closure and direct reverse dependencies.

    Avoids massive full repository builds (//... or @goldfish//...) for every symbol rename,
    targeting f"{pkg}:all" and direct 1-hop reverse dependencies (rdeps).

    Args:
        runner: Optional BazelRunner instance for querying rdeps.
        target: Target string being refactored.
        level: Refactoring blast-radius level (1..4).

    Returns:
        List of target labels to compile for verification.
    """
    clean_target = (
        target.replace(":tidy_report", "").replace(":tidy", "").replace("_report", "")
    )
    if ":" in clean_target:
        pkg, name = clean_target.split(":", 1)
        base_rule = f"{pkg}:{name}"
    else:
        pkg = clean_target
        base_rule = f"{pkg}:all"

    targets = [f"{pkg}:all" if not pkg.endswith(":all") else pkg]

    if level >= 3 and runner:
        try:
            scope = (
                "@goldfish//..."
                if target.startswith("@goldfish//")
                else ("@aemu//..." if target.startswith("@aemu//") else "//...")
            )
            query_expr = f"rdeps({scope}, {base_rule}, 1)"
            query_res = runner.cquery(
                query_expr, invocation_flags=["--output=label"], check=False
            )
            if query_res.returncode == 0 and query_res.stdout.strip():
                for line in query_res.stdout.splitlines():
                    t = line.strip()
                    if (
                        t
                        and not t.endswith(":tidy")
                        and not t.endswith("_report")
                        and t not in targets
                    ):
                        targets.append(t)
        except Exception as e:
            logger.debug("Failed to resolve rdeps for target %s: %s", target, e)

    return targets
