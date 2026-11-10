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

"""Hermetic unit tests for core.poller (PollOutcome and poll_until)."""

import unittest

from core.poller import PollOutcome, poll_until


class PollerTest(unittest.TestCase):
    """Tests poll_until with deterministic clock and sleep seams."""

    def test_poll_until_succeeds_immediately_without_sleeping(self) -> None:
        sleeps: list[float] = []
        outcome = poll_until(
            lambda: {"ready": True, "label": "测试_🚀"},
            timeout_sec=10.0,
            interval_sec=2.0,
            sleep_fn=sleeps.append,
            clock_fn=lambda: 100.0,
        )
        self.assertTrue(outcome.succeeded)
        self.assertEqual(outcome.value, {"ready": True, "label": "测试_🚀"})
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(outcome.elapsed_sec, 0.0)
        self.assertEqual(sleeps, [])

    def test_poll_until_polls_with_backoff_and_clamps_to_deadline(self) -> None:
        sleeps: list[float] = []
        on_polls: list[tuple[int, float]] = []
        ticks = iter([0.0, 2.0, 6.0, 12.0])
        values = iter([None, False, "done"])

        outcome = poll_until(
            lambda: next(values),
            timeout_sec=15.0,
            interval_sec=2.0,
            backoff=2.0,
            max_interval_sec=5.0,
            sleep_fn=sleeps.append,
            clock_fn=lambda: next(ticks),
            on_poll_fn=lambda att, el: on_polls.append((att, el)),
        )
        self.assertTrue(outcome.succeeded)
        self.assertEqual(outcome.value, "done")
        self.assertEqual(outcome.attempts, 3)
        self.assertEqual(outcome.elapsed_sec, 6.0)
        self.assertEqual(sleeps, [2.0, 4.0])
        self.assertEqual(on_polls, [(1, 2.0), (2, 6.0)])

    def test_poll_until_times_out_and_clamps_final_sleep(self) -> None:
        sleeps: list[float] = []
        ticks = iter([10.0, 14.0, 18.5, 20.0])

        outcome = poll_until(
            lambda: None,
            timeout_sec=10.0,
            interval_sec=5.0,
            sleep_fn=sleeps.append,
            clock_fn=lambda: next(ticks),
        )
        self.assertFalse(outcome.succeeded)
        self.assertIsNone(outcome.value)
        self.assertEqual(outcome.attempts, 3)
        self.assertEqual(outcome.elapsed_sec, 10.0)
        self.assertEqual(sleeps, [5.0, 1.5])

    def test_poll_until_supports_custom_predicate_and_rejects_invalid_backoff(
        self,
    ) -> None:
        values = iter([1, 2, 0])
        outcome = poll_until(
            lambda: next(values),
            timeout_sec=10.0,
            interval_sec=1.0,
            predicate=lambda v: v == 0,
            sleep_fn=lambda _: None,
            clock_fn=iter([0.0, 1.0, 2.0, 3.0]).__next__,
        )
        self.assertTrue(outcome.succeeded)
        self.assertEqual(outcome.value, 0)
        self.assertEqual(outcome.attempts, 3)

        with self.assertRaises(ValueError):
            poll_until(lambda: True, timeout_sec=1.0, interval_sec=1.0, backoff=0.0)


if __name__ == "__main__":
    unittest.main()
