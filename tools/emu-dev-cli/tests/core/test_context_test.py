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

"""Hermetic unit tests for core.context (AppConfig, ServiceContainer, ExecutionContext)."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src",
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from clients.agent_runtime_service import (
    FakeAgentRuntimeService,
    ProdAgentRuntimeService,
)
from clients.artifact_service import FakeArtifactService, ProdArtifactService
from clients.buganizer_service import FakeBuganizerService, ProdBuganizerService
from clients.chat_service import (
    FakeChatNotificationService,
    ProdChatNotificationService,
)
from clients.f1_sql_client import FakeF1SqlClient, ProdF1SqlClient
from core.context import (
    AppConfig,
    ExecutionContext,
    FakeServiceFactory,
    HOST_LINUX_X64,
    HOST_MAC_ARM64,
    HOST_MAC_X64,
    HOST_WINDOWS_X64,
    ProdServiceFactory,
    ServiceContainer,
    detect_host_platform,
)
from core.errors import ExecutionError, UsageError
from core.process import FakeProcessRunner, SubprocessRunner


class ContextTest(unittest.TestCase):
    """Tests AppConfig, ServiceContainer, ServiceFactory, and ExecutionContext."""

    def test_detect_host_platform(self) -> None:
        self.assertEqual(detect_host_platform("Linux", "x86_64"), HOST_LINUX_X64)
        self.assertEqual(detect_host_platform("Darwin", "arm64"), HOST_MAC_ARM64)
        self.assertEqual(detect_host_platform("Darwin", "aarch64"), HOST_MAC_ARM64)
        self.assertEqual(detect_host_platform("Darwin", "x86_64"), HOST_MAC_X64)
        self.assertEqual(detect_host_platform("Windows", "AMD64"), HOST_WINDOWS_X64)

    def test_app_config_loads_utf8_json_and_handles_corrupt_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emu_cfg_测试_🚀_") as tmp_dir:
            cfg_file = Path(tmp_dir) / "emu-dev-cli.json"
            cfg_file.write_text(
                json.dumps(
                    {
                        "source_directories": {
                            "emu-main-next": f"{tmp_dir}/源码_🚀_é",
                        },
                        "codesearch_urls": {
                            "custom-branch": "https://cs.corp.google.com/custom:",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            cfg = AppConfig.from_environment(
                config_file=cfg_file,
                cache_dir=Path(tmp_dir) / "cache",
                release_dir=Path(tmp_dir) / "release",
                json_mode=True,
                verbose=True,
            )
            self.assertEqual(
                cfg.get_source_directory("emu-main-next"),
                f"{tmp_dir}/源码_🚀_é",
            )
            self.assertIsNone(cfg.get_source_directory("unmapped-branch"))
            self.assertEqual(
                cfg.get_codesearch_url_prefix("custom-branch"),
                "https://cs.corp.google.com/custom:",
            )
            self.assertTrue(cfg.json_mode)
            self.assertTrue(cfg.verbose)

            # Corrupt JSON falls back cleanly without crashing
            cfg_file.write_text("{not valid json", encoding="utf-8")
            fallback_cfg = AppConfig.from_environment(config_file=cfg_file)
            self.assertEqual(fallback_cfg.source_directories, {})

    def test_service_container_lazy_resolution_and_cycle_detection(self) -> None:
        container = ServiceContainer()
        call_count = 0

        def _make_client(_c: ServiceContainer) -> dict[str, int]:
            nonlocal call_count
            call_count += 1
            return {"id": call_count}

        container.register_factory("my_client", _make_client)
        self.assertTrue(container.has("my_client"))
        first = container.resolve("my_client")
        second = container.resolve("my_client")
        self.assertIs(first, second)
        self.assertEqual(call_count, 1)

        # Missing service raises ExecutionError with available service list
        with self.assertRaises(ExecutionError) as missing_err:
            container.resolve("unknown_service")
        self.assertIn("my_client", missing_err.exception.message)

        # Circular dependency detection
        container.register_factory("svc_a", lambda c: c.resolve("svc_b"))
        container.register_factory("svc_b", lambda c: c.resolve("svc_a"))
        with self.assertRaises(ExecutionError) as cycle_err:
            container.resolve("svc_a")
        self.assertIn("Circular service dependency", cycle_err.exception.message)

        with self.assertRaises(UsageError):
            container.register_instance("  ", object())
        with self.assertRaises(UsageError):
            container.register_factory("bad", "not_callable")  # type: ignore[arg-type]

    def test_execution_context_for_testing_and_prod_factory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emu_ctx_测试_") as tmp_dir:
            fake_factory = FakeServiceFactory(
                services={"custom_service": {"fake": True}}
            )
            ctx = ExecutionContext.for_testing(
                tmp_dir,
                source_directories={"studio-main": tmp_dir},
                factory=fake_factory,
            )
            self.assertIsInstance(ctx.process_runner, FakeProcessRunner)
            self.assertIsInstance(ctx.artifact_service, FakeArtifactService)
            self.assertIsInstance(ctx.f1_sql_client, FakeF1SqlClient)
            self.assertIsInstance(ctx.buganizer_service, FakeBuganizerService)
            self.assertIsInstance(ctx.agent_runtime_service, FakeAgentRuntimeService)
            self.assertIsInstance(ctx.chat_service, FakeChatNotificationService)
            self.assertIs(ctx.fake_process_runner, fake_factory.process_runner)
            self.assertIs(ctx.fake_artifact_service, fake_factory.artifact_service)
            self.assertIs(ctx.fake_f1_sql_client, fake_factory.f1_sql_client)
            self.assertIs(ctx.fake_buganizer_service, fake_factory.buganizer_service)
            self.assertIs(
                ctx.fake_agent_runtime_service,
                fake_factory.agent_runtime_service,
            )
            self.assertIs(ctx.fake_chat_service, fake_factory.chat_service)
            self.assertEqual(
                ctx.services.resolve("custom_service"), {"fake": True}
            )

            derived = ctx.with_flags(json_mode=True, verbose=True)
            self.assertTrue(derived.config.json_mode)
            self.assertTrue(derived.config.verbose)
            self.assertIs(derived.services, ctx.services)

            prod_ctx = ExecutionContext(
                config=AppConfig.for_testing(tmp_dir),
                factory=ProdServiceFactory(record_events=True),
            )
            self.assertIsInstance(prod_ctx.process_runner, SubprocessRunner)
            self.assertIsInstance(prod_ctx.artifact_service, ProdArtifactService)
            self.assertIsInstance(prod_ctx.f1_sql_client, ProdF1SqlClient)
            self.assertIsInstance(prod_ctx.buganizer_service, ProdBuganizerService)
            self.assertIsInstance(
                prod_ctx.agent_runtime_service, ProdAgentRuntimeService
            )
            self.assertIsInstance(
                prod_ctx.chat_service, ProdChatNotificationService
            )
            with self.assertRaises(ExecutionError):
                _ = prod_ctx.fake_process_runner
            with self.assertRaises(ExecutionError):
                _ = prod_ctx.fake_artifact_service
            with self.assertRaises(ExecutionError):
                _ = prod_ctx.fake_f1_sql_client
            with self.assertRaises(ExecutionError):
                _ = prod_ctx.fake_buganizer_service
            with self.assertRaises(ExecutionError):
                _ = prod_ctx.fake_agent_runtime_service
            with self.assertRaises(ExecutionError):
                _ = prod_ctx.fake_chat_service


if __name__ == "__main__":
    unittest.main()
