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

"""Foundational Command, Dependency Injection, ProcessRunner, and EventBus abstractions."""

from core.command import (
    COMMAND_INSTANCE_ATTR,
    Command,
    CommandResult,
    LegacyCommandAdapter,
    STATUS_ERROR,
    STATUS_SUCCESS,
)
from core.context import (
    AppConfig,
    ExecutionContext,
    FakeServiceFactory,
    ProdServiceFactory,
    SERVICE_EVENT_BUS,
    SERVICE_PROCESS_RUNNER,
    ServiceContainer,
    ServiceFactory,
    detect_host_platform,
)
from core.dispatcher import CliDispatcher, OutputRenderer
from core.errors import (
    AuthError,
    EmuCliError,
    ExecutionError,
    ExitCode,
    RetryableError,
    SubprocessError,
    UsageError,
    redact_command_args,
    redact_sensitive_text,
    sanitize_details,
)
from core.events import (
    ArtifactDownloaded,
    CliEvent,
    EventBus,
    StepCompleted,
    StepStarted,
    SubprocessInvoked,
)
from core.process import (
    DEFAULT_SUBPROCESS_TIMEOUT_SEC,
    FakeProcessCall,
    FakeProcessRunner,
    ProcessResult,
    ProcessRunner,
    SubprocessRunner,
)

__all__ = [
    "AppConfig",
    "ArtifactDownloaded",
    "AuthError",
    "COMMAND_INSTANCE_ATTR",
    "CliDispatcher",
    "CliEvent",
    "Command",
    "CommandResult",
    "DEFAULT_SUBPROCESS_TIMEOUT_SEC",
    "EmuCliError",
    "EventBus",
    "ExecutionContext",
    "ExecutionError",
    "ExitCode",
    "FakeProcessCall",
    "FakeProcessRunner",
    "FakeServiceFactory",
    "LegacyCommandAdapter",
    "OutputRenderer",
    "ProcessResult",
    "ProcessRunner",
    "ProdServiceFactory",
    "RetryableError",
    "SERVICE_EVENT_BUS",
    "SERVICE_PROCESS_RUNNER",
    "STATUS_ERROR",
    "STATUS_SUCCESS",
    "ServiceContainer",
    "ServiceFactory",
    "StepCompleted",
    "StepStarted",
    "SubprocessError",
    "SubprocessInvoked",
    "SubprocessRunner",
    "UsageError",
    "detect_host_platform",
    "redact_command_args",
    "redact_sensitive_text",
    "sanitize_details",
]
