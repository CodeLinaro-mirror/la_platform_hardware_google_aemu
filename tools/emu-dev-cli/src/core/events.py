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

"""Thread-safe in-memory EventBus and typed lifecycle events for emu-dev-cli."""

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
import threading
from typing import Any, TypeVar

from core.errors import redact_command_args, sanitize_details

LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_RECORDED_EVENTS = 1000

EVENT_TYPE_STEP_STARTED = "step_started"
EVENT_TYPE_STEP_COMPLETED = "step_completed"
EVENT_TYPE_SUBPROCESS_INVOKED = "subprocess_invoked"
EVENT_TYPE_ARTIFACT_DOWNLOADED = "artifact_downloaded"


def utc_now_iso() -> str:
    """Returns the current UTC timestamp formatted as ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CliEvent:
    """Base immutable event emitted across emu-dev-cli execution lifecycles."""

    event_type: str = field(default="cli_event", init=False)
    timestamp: str = field(default_factory=utc_now_iso, kw_only=True)

    def to_dict(self) -> dict[str, Any]:
        """Serializes the event into a JSON-compatible dictionary."""
        data = asdict(self)
        data["event_type"] = self.event_type
        return data


@dataclass(frozen=True)
class StepStarted(CliEvent):
    """Emitted when a CLI command or declarative workflow step begins."""

    step_name: str
    workflow_name: str = ""
    step_index: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)
    event_type: str = field(default=EVENT_TYPE_STEP_STARTED, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", sanitize_details(self.metadata))


@dataclass(frozen=True)
class StepCompleted(CliEvent):
    """Emitted when a CLI command or declarative workflow step finishes."""

    step_name: str
    status: str = "success"
    duration_sec: float = 0.0
    exit_code: int = 0
    workflow_name: str = ""
    step_index: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)
    event_type: str = field(default=EVENT_TYPE_STEP_COMPLETED, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", sanitize_details(self.metadata))


@dataclass(frozen=True)
class SubprocessInvoked(CliEvent):
    """Emitted after an external subprocess is executed via ProcessRunner."""

    command: tuple[str, ...]
    returncode: int
    duration_sec: float = 0.0
    cwd: str | None = None
    timed_out: bool = False
    event_type: str = field(default=EVENT_TYPE_SUBPROCESS_INVOKED, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "command", redact_command_args(self.command))


@dataclass(frozen=True)
class ArtifactDownloaded(CliEvent):
    """Emitted when an artifact is fetched from Android Build or storage."""

    build_id: str
    target: str
    artifact_name: str
    destination_path: str
    size_bytes: int = 0
    event_type: str = field(default=EVENT_TYPE_ARTIFACT_DOWNLOADED, init=False)


TEvent = TypeVar("TEvent", bound=CliEvent)
EventHandler = Callable[[Any], None]


class EventBus:
    """Thread-safe synchronous publish/subscribe bus for CLI lifecycle telemetry."""

    def __init__(
        self,
        *,
        record_history: bool = False,
        max_recorded_events: int = DEFAULT_MAX_RECORDED_EVENTS,
    ) -> None:
        self._lock = threading.RLock()
        self._subscribers: dict[type[CliEvent], list[EventHandler]] = {}
        self._wildcard_subscribers: list[EventHandler] = []
        self._record_history = bool(record_history)
        self._max_recorded_events = max(1, int(max_recorded_events))
        self._history: list[CliEvent] = []

    def subscribe(
        self, event_type: type[TEvent], handler: Callable[[TEvent], None]
    ) -> Callable[[], None]:
        """Subscribes `handler` to events matching `event_type` and returns an unsubscribe callable."""
        with self._lock:
            handlers = self._subscribers.setdefault(event_type, [])
            handlers.append(handler)

        def _unsubscribe() -> None:
            with self._lock:
                registered = self._subscribers.get(event_type, [])
                if handler in registered:
                    registered.remove(handler)

        return _unsubscribe

    def subscribe_all(
        self, handler: Callable[[CliEvent], None]
    ) -> Callable[[], None]:
        """Subscribes `handler` to all published events and returns an unsubscribe callable."""
        with self._lock:
            self._wildcard_subscribers.append(handler)

        def _unsubscribe() -> None:
            with self._lock:
                if handler in self._wildcard_subscribers:
                    self._wildcard_subscribers.remove(handler)

        return _unsubscribe

    def publish(self, event: CliEvent) -> None:
        """Publishes `event` to matching subscribers while isolating subscriber exceptions."""
        with self._lock:
            if self._record_history:
                self._history.append(event)
                if len(self._history) > self._max_recorded_events:
                    del self._history[0 : len(self._history) - self._max_recorded_events]
            typed_handlers: list[EventHandler] = []
            for subscribed_type, handlers in self._subscribers.items():
                if isinstance(event, subscribed_type):
                    typed_handlers.extend(handlers)
            all_handlers = typed_handlers + list(self._wildcard_subscribers)

        for handler in all_handlers:
            try:
                handler(event)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                LOGGER.warning(
                    "EventBus subscriber %r failed while handling %s: %s",
                    handler,
                    type(event).__name__,
                    exc,
                )

    def recorded_events(
        self, event_type: type[TEvent] | None = None
    ) -> list[Any]:
        """Returns a snapshot of recorded events, optionally filtered by `event_type`."""
        with self._lock:
            if event_type is None:
                return list(self._history)
            return [e for e in self._history if isinstance(e, event_type)]

    def clear_history(self) -> None:
        """Clears the recorded event history buffer."""
        with self._lock:
            self._history.clear()
