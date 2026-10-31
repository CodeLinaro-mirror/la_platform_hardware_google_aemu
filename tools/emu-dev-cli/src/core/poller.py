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

"""Hermetic polling and retry primitives with injectable clock and sleep seams."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time
from typing import Generic, Optional, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class PollOutcome(Generic[T]):
    """Outcome of a bounded `poll_until` loop."""

    succeeded: bool
    value: Optional[T] = None
    attempts: int = 0
    elapsed_sec: float = 0.0


def poll_until(
    condition_fn: Callable[[], Optional[T]],
    timeout_sec: float,
    interval_sec: float,
    *,
    backoff: float = 1.0,
    max_interval_sec: Optional[float] = None,
    predicate: Optional[Callable[[Optional[T]], bool]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock_fn: Callable[[], float] = time.monotonic,
    clamp_sleep_to_deadline: bool = True,
    on_poll_fn: Optional[Callable[[int, float], None]] = None,
) -> PollOutcome[T]:
    """Polls `condition_fn` until it succeeds or `timeout_sec` elapses.

    By default, `condition_fn` is considered satisfied when its return value is
    neither `None` nor `False`. Callers can supply a custom `predicate` when
    polling for values where `False` or `None` is a valid terminal state.

    Args:
        condition_fn: Zero-argument callable invoked on each poll iteration.
        timeout_sec: Maximum wall-clock duration in seconds before timing out.
        interval_sec: Initial sleep duration in seconds between poll attempts.
        backoff: Multiplicative backoff factor applied after each sleep (>= 1.0).
        max_interval_sec: Optional cap on the backed-off sleep interval.
        predicate: Optional custom predicate determining whether the returned
            value satisfies the poll condition.
        sleep_fn: Injectable sleep function for zero-sleep hermetic unit tests.
        clock_fn: Injectable monotonic clock function for deterministic timing.
        clamp_sleep_to_deadline: When True, caps the sleep duration to the
            remaining time before `deadline`.
        on_poll_fn: Optional callback `(attempts, elapsed_sec)` invoked after
            each sleep interval.

    Returns:
        A `PollOutcome[T]` describing whether the condition succeeded, the final
        sampled value, total attempts executed, and elapsed seconds.
    """
    if backoff <= 0:
        raise ValueError(f"backoff must be positive, got {backoff}")

    start = clock_fn()
    deadline = start + max(0.0, float(timeout_sec))
    last_now = start
    current_interval = max(0.0, float(interval_sec))
    attempts = 0

    while True:
        attempts += 1
        result = condition_fn()
        matched = (
            predicate(result)
            if predicate is not None
            else (result is not None and result is not False)
        )
        if matched:
            return PollOutcome(
                succeeded=True,
                value=result,
                attempts=attempts,
                elapsed_sec=max(0.0, last_now - start),
            )

        now = clock_fn()
        last_now = now
        if now >= deadline:
            return PollOutcome(
                succeeded=False,
                value=result,
                attempts=attempts,
                elapsed_sec=max(0.0, now - start),
            )

        sleep_duration = (
            min(current_interval, max(0.0, deadline - now))
            if clamp_sleep_to_deadline
            else current_interval
        )
        sleep_fn(sleep_duration)
        if on_poll_fn is not None:
            on_poll_fn(attempts, max(0.0, now - start))

        if backoff != 1.0 and current_interval > 0.0:
            current_interval *= backoff
            if max_interval_sec is not None:
                current_interval = min(
                    current_interval, max(0.0, float(max_interval_sec))
                )
