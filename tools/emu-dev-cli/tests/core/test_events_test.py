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

"""Hermetic unit tests for core.events EventBus and typed lifecycle events."""

import os
import sys
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from core.errors import REDACTED_PLACEHOLDER
from core.events import (
    ArtifactDownloaded,
    CliEvent,
    EventBus,
    StepCompleted,
    StepStarted,
    SubprocessInvoked,
)


class EventsTest(unittest.TestCase):
    """Tests EventBus subscription, filtering, history bounds, and error isolation."""

    def test_typed_and_wildcard_subscriptions_with_unsubscribe(self) -> None:
        bus = EventBus(record_history=True)
        started_events: list[StepStarted] = []
        all_events: list[CliEvent] = []

        unsub_started = bus.subscribe(StepStarted, started_events.append)
        unsub_all = bus.subscribe_all(all_events.append)

        ev1 = StepStarted(
            step_name="compile_测试_🚀",
            workflow_name="tdd-fix",
            step_index=1,
            metadata={"api_token": "secret_123", "branch": "studio-main"},
        )
        ev2 = ArtifactDownloaded(
            build_id="16605111",
            target="emulator_linux_x64",
            artifact_name="sdk-repo-linux-emulator.zip",
            destination_path="/tmp/测试_🚀/emulator.zip",
            size_bytes=4096,
        )
        bus.publish(ev1)
        bus.publish(ev2)

        self.assertEqual(len(started_events), 1)
        self.assertEqual(started_events[0].step_name, "compile_测试_🚀")
        self.assertEqual(
            started_events[0].metadata["api_token"], REDACTED_PLACEHOLDER
        )
        self.assertEqual(len(all_events), 2)

        unsub_started()
        unsub_all()
        bus.publish(StepCompleted(step_name="compile_测试_🚀", exit_code=0))
        self.assertEqual(len(started_events), 1)
        self.assertEqual(len(all_events), 2)
        self.assertEqual(len(bus.recorded_events()), 3)
        self.assertEqual(len(bus.recorded_events(ArtifactDownloaded)), 1)

    def test_subscriber_exception_is_isolated(self) -> None:
        bus = EventBus(record_history=True)
        received: list[StepCompleted] = []

        def _failing_listener(_event: StepCompleted) -> None:
            raise RuntimeError("boom in listener")

        bus.subscribe(StepCompleted, _failing_listener)
        bus.subscribe(StepCompleted, received.append)

        event = StepCompleted(step_name="verify", status="success", exit_code=0)
        bus.publish(event)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].step_name, "verify")

    def test_subprocess_invoked_redacts_command_and_history_is_bounded(self) -> None:
        bus = EventBus(record_history=True, max_recorded_events=2)
        for idx in range(4):
            bus.publish(
                SubprocessInvoked(
                    command=(
                        "curl",
                        "--token",
                        f"secret_{idx}",
                        f"https://host.example.com/{idx}",
                    ),
                    returncode=0,
                    duration_sec=0.05,
                )
            )
        recorded = bus.recorded_events(SubprocessInvoked)
        self.assertEqual(len(recorded), 2)
        self.assertEqual(
            recorded[0].command,
            ("curl", "--token", REDACTED_PLACEHOLDER, "https://host.example.com/2"),
        )
        self.assertEqual(
            recorded[1].command,
            ("curl", "--token", REDACTED_PLACEHOLDER, "https://host.example.com/3"),
        )
        serialized = recorded[1].to_dict()
        self.assertEqual(serialized["event_type"], "subprocess_invoked")
        bus.clear_history()
        self.assertEqual(bus.recorded_events(), [])


if __name__ == "__main__":
    unittest.main()
