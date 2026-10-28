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

"""Dependency injection container, AppConfig, and ExecutionContext for emu-dev-cli."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
import json
import logging
import os
from pathlib import Path
import platform
import threading
from typing import Any, TYPE_CHECKING, TypeVar

from core.errors import ExecutionError, UsageError
from core.events import EventBus
from core.process import FakeProcessRunner, ProcessRunner, SubprocessRunner

if TYPE_CHECKING:
    from clients.agent_runtime_service import (
        AgentRuntimeService,
        FakeAgentRuntimeService,
    )
    from clients.artifact_service import (
        ArtifactService,
        FakeArtifactService,
    )
    from clients.buganizer_service import (
        BuganizerService,
        FakeBuganizerService,
    )
    from clients.f1_sql_client import (
        F1SqlClient,
        FakeF1SqlClient,
    )

LOGGER = logging.getLogger(__name__)

SERVICE_EVENT_BUS = "event_bus"
SERVICE_PROCESS_RUNNER = "process_runner"
SERVICE_ARTIFACT = "artifact_service"
SERVICE_F1_SQL = "f1_sql_client"
SERVICE_BUGANIZER = "buganizer_service"
SERVICE_AGENT_RUNTIME = "agent_runtime_service"

HOST_LINUX_X64 = "linux-x64"
HOST_MAC_ARM64 = "mac-arm64"
HOST_MAC_X64 = "mac-x64"
HOST_WINDOWS_X64 = "windows-x64"

DEFAULT_BRANCH_MAIN_NEXT = "emu-main-next"
DEFAULT_CODESEARCH_URLS: Mapping[str, str] = {
    "emu-main-next": (
        "https://source.corp.google.com/h/googleplex-android/platform/"
        "superproject/emu-main-next/+/emu-main-next:"
    ),
    "emu-main-dev": (
        "https://source.corp.google.com/h/googleplex-android/platform/"
        "superproject/emu-main-dev/+/emu-main-dev:"
    ),
    "git_main": (
        "https://source.corp.google.com/h/googleplex-android/platform/"
        "superproject/main/+/main:"
    ),
    "trunk-release": (
        "https://source.corp.google.com/h/googleplex-android/platform/"
        "superproject/main/+/main:"
    ),
}

_ServiceT = TypeVar("_ServiceT")


def detect_host_platform(
    system_name: str | None = None, machine_name: str | None = None
) -> str:
    """Detects the canonical host platform identifier (`linux-x64`, `mac-arm64`, etc.)."""
    sys_lower = (system_name if system_name is not None else platform.system()).lower()
    mach_lower = (
        machine_name if machine_name is not None else platform.machine()
    ).lower()
    if sys_lower == "darwin":
        return HOST_MAC_ARM64 if mach_lower in ("arm64", "aarch64") else HOST_MAC_X64
    if sys_lower == "windows":
        return HOST_WINDOWS_X64
    return HOST_LINUX_X64


def _load_json_config_file(config_path: Path) -> dict[str, Any]:
    """Reads `config_path` as UTF-8 JSON, returning an empty dict if missing or invalid."""
    if not config_path.is_file():
        return {}
    try:
        raw_text = config_path.read_text(encoding="utf-8")
        parsed = json.loads(raw_text)
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, ValueError) as exc:
        LOGGER.warning(
            "Could not parse configuration file '%s' (%s); using default settings.",
            config_path,
            exc,
        )
        return {}


@dataclass(frozen=True)
class AppConfig:
    """Immutable application configuration and workspace directory mappings."""

    config_file: Path
    cache_dir: Path
    release_dir: Path
    host_platform: str = field(default_factory=detect_host_platform)
    source_directories: Mapping[str, str] = field(default_factory=dict)
    codesearch_urls: Mapping[str, str] = field(
        default_factory=lambda: dict(DEFAULT_CODESEARCH_URLS)
    )
    json_mode: bool = False
    verbose: bool = False
    log_file: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "config_file", Path(os.fspath(self.config_file)))
        object.__setattr__(self, "cache_dir", Path(os.fspath(self.cache_dir)))
        object.__setattr__(self, "release_dir", Path(os.fspath(self.release_dir)))
        object.__setattr__(
            self,
            "source_directories",
            {str(k): str(v) for k, v in (self.source_directories or {}).items()},
        )
        merged_cs = dict(DEFAULT_CODESEARCH_URLS)
        for k, v in (self.codesearch_urls or {}).items():
            if v:
                merged_cs[str(k)] = str(v)
        object.__setattr__(self, "codesearch_urls", merged_cs)
        if self.log_file is not None:
            object.__setattr__(self, "log_file", Path(os.fspath(self.log_file)))

    @classmethod
    def from_environment(
        cls,
        *,
        config_file: str | os.PathLike[str] | None = None,
        cache_dir: str | os.PathLike[str] | None = None,
        release_dir: str | os.PathLike[str] | None = None,
        json_mode: bool = False,
        verbose: bool = False,
        log_file: str | os.PathLike[str] | None = None,
    ) -> AppConfig:
        """Loads AppConfig from standard user paths and `~/.android/emu-dev-cli.json`."""
        home = Path.home()
        resolved_config = (
            Path(os.fspath(config_file))
            if config_file is not None
            else home / ".android" / "emu-dev-cli.json"
        )
        resolved_cache = (
            Path(os.fspath(cache_dir))
            if cache_dir is not None
            else home / ".cache" / "emu-dev-cli"
        )
        resolved_release = (
            Path(os.fspath(release_dir))
            if release_dir is not None
            else home / ".android" / "emu-dev-cli"
        )
        raw_cfg = _load_json_config_file(resolved_config)
        raw_dirs = raw_cfg.get("source_directories")
        source_dirs = raw_dirs if isinstance(raw_dirs, Mapping) else {}
        raw_urls = raw_cfg.get("codesearch_urls")
        cs_urls = raw_urls if isinstance(raw_urls, Mapping) else {}
        return cls(
            config_file=resolved_config,
            cache_dir=resolved_cache,
            release_dir=resolved_release,
            host_platform=detect_host_platform(),
            source_directories=source_dirs,
            codesearch_urls=cs_urls,
            json_mode=json_mode,
            verbose=verbose,
            log_file=Path(os.fspath(log_file)) if log_file is not None else None,
        )

    @classmethod
    def for_testing(
        cls,
        root_dir: str | os.PathLike[str],
        *,
        source_directories: Mapping[str, str] | None = None,
        host_platform: str = HOST_LINUX_X64,
        json_mode: bool = False,
        verbose: bool = False,
    ) -> AppConfig:
        """Creates a hermetic AppConfig rooted inside `root_dir` for unit tests."""
        base = Path(os.fspath(root_dir))
        return cls(
            config_file=base / "config" / "emu-dev-cli.json",
            cache_dir=base / "cache",
            release_dir=base / "release",
            host_platform=host_platform,
            source_directories=dict(source_directories or {}),
            json_mode=json_mode,
            verbose=verbose,
        )

    def get_source_directory(self, branch_name: str) -> str | None:
        """Returns the configured local source directory for `branch_name`, if any."""
        value = self.source_directories.get(branch_name)
        return value if value else None

    def get_codesearch_url_prefix(
        self, branch_name: str = DEFAULT_BRANCH_MAIN_NEXT
    ) -> str:
        """Returns the Code Search URL prefix for `branch_name`."""
        return self.codesearch_urls.get(
            branch_name, DEFAULT_CODESEARCH_URLS[DEFAULT_BRANCH_MAIN_NEXT]
        )


ServiceBuilder = Callable[["ServiceContainer"], Any]


class ServiceContainer:
    """Thread-safe dependency injection container supporting lazy singletons."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._instances: dict[str, Any] = {}
        self._factories: dict[str, ServiceBuilder] = {}
        self._resolving: set[str] = set()

    def register_instance(self, name: str, instance: Any) -> None:
        """Registers an eager singleton instance under `name`."""
        if not name or not name.strip():
            raise UsageError(
                "Service name cannot be empty when registering an instance.",
                remediation="Provide a non-empty identifier such as 'process_runner'.",
            )
        with self._lock:
            self._instances[name] = instance
            self._factories.pop(name, None)

    def register_factory(self, name: str, factory: ServiceBuilder) -> None:
        """Registers a lazy factory callable invoked on first `resolve(name)`."""
        if not name or not name.strip():
            raise UsageError(
                "Service name cannot be empty when registering a factory.",
                remediation="Provide a non-empty identifier such as 'artifact_service'.",
            )
        if not callable(factory):
            raise UsageError(
                f"Service factory for '{name}' must be callable.",
                remediation="Pass a callable taking a single ServiceContainer argument.",
            )
        with self._lock:
            self._factories[name] = factory
            self._instances.pop(name, None)

    def has(self, name: str) -> bool:
        """Returns True if `name` is registered as an instance or factory."""
        with self._lock:
            return name in self._instances or name in self._factories

    def registered_names(self) -> tuple[str, ...]:
        """Returns a sorted tuple of all registered service identifiers."""
        with self._lock:
            return tuple(sorted(set(self._instances) | set(self._factories)))

    def resolve(self, name: str) -> Any:
        """Resolves and caches the service registered under `name`."""
        with self._lock:
            if name in self._instances:
                return self._instances[name]
            if name in self._resolving:
                raise ExecutionError(
                    f"Circular service dependency detected while resolving '{name}'.",
                    remediation="Break the dependency cycle between registered service factories.",
                )
            factory = self._factories.get(name)
            if factory is None:
                available = ", ".join(self.registered_names()) or "<none>"
                raise ExecutionError(
                    f"Service '{name}' is not registered in ServiceContainer (available: {available}).",
                    remediation=f"Register '{name}' via register_instance() or register_factory() before resolving.",
                )
            self._resolving.add(name)
            try:
                instance = factory(self)
            finally:
                self._resolving.discard(name)
            self._instances[name] = instance
            return instance


class ServiceFactory(ABC):
    """Abstract factory for populating a ServiceContainer."""

    @abstractmethod
    def create_container(self, config: AppConfig) -> ServiceContainer:
        """Creates and populates a ServiceContainer for `config`."""


def _build_prod_artifact_service(container: ServiceContainer) -> ArtifactService:
    from clients.artifact_service import ProdArtifactService

    return ProdArtifactService(
        container.resolve(SERVICE_PROCESS_RUNNER),
        event_bus=container.resolve(SERVICE_EVENT_BUS),
    )


def _build_prod_f1_sql_client(
    container: ServiceContainer, config: AppConfig
) -> F1SqlClient:
    from clients.f1_sql_client import ProdF1SqlClient

    return ProdF1SqlClient(
        container.resolve(SERVICE_PROCESS_RUNNER),
        scratch_dir=config.cache_dir / "f1_sql",
    )


def _build_prod_buganizer_service(
    container: ServiceContainer, config: AppConfig
) -> BuganizerService:
    from clients.buganizer_service import ProdBuganizerService

    return ProdBuganizerService(
        container.resolve(SERVICE_PROCESS_RUNNER),
        cache_file=config.cache_dir / "buganizer_cache.json",
    )


def _build_prod_agent_runtime_service(
    container: ServiceContainer,
) -> AgentRuntimeService:
    from clients.agent_runtime_service import ProdAgentRuntimeService

    return ProdAgentRuntimeService(container.resolve(SERVICE_PROCESS_RUNNER))


class ProdServiceFactory(ServiceFactory):
    """Production ServiceFactory wiring real SubprocessRunner, EventBus, and client services."""

    def __init__(
        self,
        *,
        extra_factories: Mapping[str, ServiceBuilder] | None = None,
        record_events: bool = False,
    ) -> None:
        self._extra_factories = dict(extra_factories or {})
        self._record_events = record_events

    def create_container(self, config: AppConfig) -> ServiceContainer:
        container = ServiceContainer()
        container.register_factory(
            SERVICE_EVENT_BUS,
            lambda _c: EventBus(record_history=self._record_events),
        )
        container.register_factory(
            SERVICE_PROCESS_RUNNER,
            lambda c: SubprocessRunner(event_bus=c.resolve(SERVICE_EVENT_BUS)),
        )
        container.register_factory(
            SERVICE_ARTIFACT,
            _build_prod_artifact_service,
        )
        container.register_factory(
            SERVICE_F1_SQL,
            lambda c: _build_prod_f1_sql_client(c, config),
        )
        container.register_factory(
            SERVICE_BUGANIZER,
            lambda c: _build_prod_buganizer_service(c, config),
        )
        container.register_factory(
            SERVICE_AGENT_RUNTIME,
            _build_prod_agent_runtime_service,
        )
        for name, factory in self._extra_factories.items():
            container.register_factory(name, factory)
        return container


class FakeServiceFactory(ServiceFactory):
    """Hermetic ServiceFactory wiring FakeProcessRunner, history EventBus, and fake clients."""

    def __init__(
        self,
        *,
        process_runner: FakeProcessRunner | None = None,
        event_bus: EventBus | None = None,
        artifact_service: FakeArtifactService | None = None,
        f1_sql_client: FakeF1SqlClient | None = None,
        buganizer_service: FakeBuganizerService | None = None,
        agent_runtime_service: FakeAgentRuntimeService | None = None,
        services: Mapping[str, Any] | None = None,
        factories: Mapping[str, ServiceBuilder] | None = None,
    ) -> None:
        from clients.agent_runtime_service import FakeAgentRuntimeService
        from clients.artifact_service import FakeArtifactService
        from clients.buganizer_service import FakeBuganizerService
        from clients.f1_sql_client import FakeF1SqlClient

        self._event_bus = event_bus or EventBus(record_history=True)
        self._process_runner = process_runner or FakeProcessRunner(
            event_bus=self._event_bus
        )
        self._artifact_service = artifact_service or FakeArtifactService(
            event_bus=self._event_bus
        )
        self._f1_sql_client = f1_sql_client or FakeF1SqlClient()
        self._buganizer_service = buganizer_service or FakeBuganizerService()
        self._agent_runtime_service = (
            agent_runtime_service or FakeAgentRuntimeService()
        )
        self._services = dict(services or {})
        self._factories = dict(factories or {})

    @property
    def event_bus(self) -> EventBus:
        """Returns the hermetic EventBus instance."""
        return self._event_bus

    @property
    def process_runner(self) -> FakeProcessRunner:
        """Returns the hermetic FakeProcessRunner instance."""
        return self._process_runner

    @property
    def artifact_service(self) -> FakeArtifactService:
        """Returns the hermetic FakeArtifactService instance."""
        return self._artifact_service

    @property
    def f1_sql_client(self) -> FakeF1SqlClient:
        """Returns the hermetic FakeF1SqlClient instance."""
        return self._f1_sql_client

    @property
    def buganizer_service(self) -> FakeBuganizerService:
        """Returns the hermetic FakeBuganizerService instance."""
        return self._buganizer_service

    @property
    def agent_runtime_service(self) -> FakeAgentRuntimeService:
        """Returns the hermetic FakeAgentRuntimeService instance."""
        return self._agent_runtime_service

    def create_container(self, config: AppConfig) -> ServiceContainer:
        del config
        container = ServiceContainer()
        container.register_instance(SERVICE_EVENT_BUS, self._event_bus)
        container.register_instance(SERVICE_PROCESS_RUNNER, self._process_runner)
        container.register_instance(SERVICE_ARTIFACT, self._artifact_service)
        container.register_instance(SERVICE_F1_SQL, self._f1_sql_client)
        container.register_instance(SERVICE_BUGANIZER, self._buganizer_service)
        container.register_instance(
            SERVICE_AGENT_RUNTIME, self._agent_runtime_service
        )
        for name, instance in self._services.items():
            container.register_instance(name, instance)
        for name, factory in self._factories.items():
            container.register_factory(name, factory)
        return container


def _require_fake_service(instance: Any, expected_type: type[_ServiceT]) -> _ServiceT:
    """Validates that `instance` is an instance of `expected_type`, or raises ExecutionError."""
    if not isinstance(instance, expected_type):
        raise ExecutionError(
            f"Expected {expected_type.__name__} in test context, "
            f"but found {type(instance).__name__}.",
            remediation=(
                "Create the ExecutionContext with ExecutionContext.for_testing() "
                "or FakeServiceFactory."
            ),
        )
    return instance


class ExecutionContext:
    """Carries immutable AppConfig and lazy-resolved services across command execution."""

    def __init__(
        self,
        config: AppConfig,
        services: ServiceContainer | None = None,
        *,
        factory: ServiceFactory | None = None,
    ) -> None:
        self._config = config
        if services is not None:
            self._services = services
        else:
            active_factory = factory or ProdServiceFactory()
            self._services = active_factory.create_container(config)

    @classmethod
    def create_default(
        cls,
        *,
        json_mode: bool = False,
        verbose: bool = False,
        log_file: str | os.PathLike[str] | None = None,
        factory: ServiceFactory | None = None,
    ) -> ExecutionContext:
        """Constructs a production ExecutionContext from user environment settings."""
        config = AppConfig.from_environment(
            json_mode=json_mode,
            verbose=verbose,
            log_file=log_file,
        )
        return cls(config=config, factory=factory or ProdServiceFactory())

    @classmethod
    def for_testing(
        cls,
        root_dir: str | os.PathLike[str] = "/tmp/emu-dev-cli-test",
        *,
        source_directories: Mapping[str, str] | None = None,
        host_platform: str = HOST_LINUX_X64,
        json_mode: bool = False,
        verbose: bool = False,
        factory: FakeServiceFactory | None = None,
        services: Mapping[str, Any] | None = None,
    ) -> ExecutionContext:
        """Constructs a hermetic ExecutionContext backed by FakeServiceFactory."""
        config = AppConfig.for_testing(
            root_dir,
            source_directories=source_directories,
            host_platform=host_platform,
            json_mode=json_mode,
            verbose=verbose,
        )
        fake_factory = factory or FakeServiceFactory(services=services)
        return cls(config=config, factory=fake_factory)

    @property
    def config(self) -> AppConfig:
        """Returns the immutable AppConfig."""
        return self._config

    @property
    def services(self) -> ServiceContainer:
        """Returns the underlying ServiceContainer."""
        return self._services

    @property
    def event_bus(self) -> EventBus:
        """Resolves and returns the EventBus service."""
        return self._services.resolve(SERVICE_EVENT_BUS)

    @property
    def process_runner(self) -> ProcessRunner:
        """Resolves and returns the ProcessRunner service."""
        return self._services.resolve(SERVICE_PROCESS_RUNNER)

    @property
    def artifact_service(self) -> ArtifactService:
        """Resolves and returns the ArtifactService."""
        return self._services.resolve(SERVICE_ARTIFACT)

    @property
    def f1_sql_client(self) -> F1SqlClient:
        """Resolves and returns the F1SqlClient."""
        return self._services.resolve(SERVICE_F1_SQL)

    @property
    def buganizer_service(self) -> BuganizerService:
        """Resolves and returns the BuganizerService."""
        return self._services.resolve(SERVICE_BUGANIZER)

    @property
    def agent_runtime_service(self) -> AgentRuntimeService:
        """Resolves and returns the AgentRuntimeService."""
        return self._services.resolve(SERVICE_AGENT_RUNTIME)

    @property
    def fake_process_runner(self) -> FakeProcessRunner:
        """Returns the ProcessRunner typed as FakeProcessRunner for test assertions."""
        return _require_fake_service(self.process_runner, FakeProcessRunner)

    @property
    def fake_artifact_service(self) -> FakeArtifactService:
        """Returns the ArtifactService typed as FakeArtifactService for test assertions."""
        from clients.artifact_service import FakeArtifactService

        return _require_fake_service(self.artifact_service, FakeArtifactService)

    @property
    def fake_f1_sql_client(self) -> FakeF1SqlClient:
        """Returns the F1SqlClient typed as FakeF1SqlClient for test assertions."""
        from clients.f1_sql_client import FakeF1SqlClient

        return _require_fake_service(self.f1_sql_client, FakeF1SqlClient)

    @property
    def fake_buganizer_service(self) -> FakeBuganizerService:
        """Returns the BuganizerService typed as FakeBuganizerService for test assertions."""
        from clients.buganizer_service import FakeBuganizerService

        return _require_fake_service(self.buganizer_service, FakeBuganizerService)

    @property
    def fake_agent_runtime_service(self) -> FakeAgentRuntimeService:
        """Returns the AgentRuntimeService typed as FakeAgentRuntimeService for test assertions."""
        from clients.agent_runtime_service import FakeAgentRuntimeService

        return _require_fake_service(
            self.agent_runtime_service, FakeAgentRuntimeService
        )

    def with_flags(
        self,
        *,
        json_mode: bool | None = None,
        verbose: bool | None = None,
        log_file: str | os.PathLike[str] | None = None,
    ) -> ExecutionContext:
        """Returns a new ExecutionContext with updated CLI flags sharing the ServiceContainer."""
        updated_config = replace(
            self._config,
            json_mode=self._config.json_mode if json_mode is None else bool(json_mode),
            verbose=self._config.verbose if verbose is None else bool(verbose),
            log_file=(
                self._config.log_file
                if log_file is None
                else Path(os.fspath(log_file))
            ),
        )
        return ExecutionContext(config=updated_config, services=self._services)
