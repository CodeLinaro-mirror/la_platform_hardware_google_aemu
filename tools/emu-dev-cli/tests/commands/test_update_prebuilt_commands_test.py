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

"""Hermetic zero-patch DI unit tests for `commands.update_prebuilt` Command classes."""

import argparse
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Dict, List
import unittest

_SRC_DIR = str(Path(__file__).resolve().parents[2] / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from commands.update_prebuilt import (
    UpdatePrebuiltCommand,
    UpdatePrebuiltModuleCommand,
)
from core.command import CommandValidationError
from core.context import (
    ExecutionContext,
    FakeServiceFactory,
    SERVICE_ARTIFACT,
)
from core.dispatcher import CliDispatcher
from core.process import FakeProcessRunner


class _FakePrebuiltArtifactService:
    """In-memory fake ArtifactService for update-prebuilt tests."""

    def __init__(self, *, auth_valid: bool = True) -> None:
        self.auth_valid = auth_valid
        self.auth_checks: int = 0

    def check_auth(self, token: str | None = None) -> Dict[str, Any]:
        del token
        self.auth_checks += 1
        return {
            "valid": self.auth_valid,
            "auth_method": "fake_loas",
            "error_message": "" if self.auth_valid else "Auth denied",
            "procedure": "run gcert",
        }


def _make_fake_prebuilt_module(name: str = "unicode-pkg") -> ModuleType:
    """Builds an in-memory prebuilt plugin module with rollback tracking."""
    mod = ModuleType(f"prebuilts.{name.replace('-', '_')}")
    mod.PREBUILT_NAME = name  # type: ignore[attr-defined]
    mod.DESCRIPTION = f"Update {name} prebuilt 测试_🚀_é"  # type: ignore[attr-defined]
    mod.SUPPORTED_TARGETS = ("linux-x64", "mac-arm64")  # type: ignore[attr-defined]
    mod.DEFAULT_BRANCH = "emu-main-next"  # type: ignore[attr-defined]
    mod.calls = []  # type: ignore[attr-defined]
    mod.rollbacks = []  # type: ignore[attr-defined]

    def update_prebuilt(
        build_number: str,
        targets: List[str],
        *,
        dest_dir: str | None = None,
        dry_run: bool = False,
        artifact_service: Any = None,
        runner: Any = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        del kwargs
        mod.calls.append(  # type: ignore[attr-defined]
            {
                "build_number": build_number,
                "targets": targets,
                "dest_dir": dest_dir,
                "dry_run": dry_run,
                "has_artifact_service": artifact_service is not None,
                "has_runner": runner is not None,
            }
        )
        return {
            "status": "success",
            "action": f"update-prebuilt {name}",
            "build_number": build_number,
            "targets": targets,
            "dest_dir": dest_dir,
        }

    def rollback_prebuilt(args: argparse.Namespace, error: BaseException) -> None:
        mod.rollbacks.append((getattr(args, "build_number", None), str(error)))  # type: ignore[attr-defined]

    mod.update_prebuilt = update_prebuilt  # type: ignore[attr-defined]
    mod.rollback_prebuilt = rollback_prebuilt  # type: ignore[attr-defined]
    return mod


class UpdatePrebuiltCommandsTest(unittest.TestCase):
    """Tests UpdatePrebuiltCommand and UpdatePrebuiltModuleCommand with DI fakes."""

    def test_update_prebuilt_module_validate_requires_build_number_or_latest(
        self,
    ) -> None:
        mod = _make_fake_prebuilt_module("lavapipe")
        cmd = UpdatePrebuiltModuleCommand("lavapipe", mod)
        ctx = ExecutionContext.for_testing()
        with self.assertRaises(CommandValidationError) as err_ctx:
            cmd.validate(
                argparse.Namespace(build_number=None, latest=False),
                ctx,
            )
        self.assertIn("--build-number", str(err_ctx.exception))

    def test_update_prebuilt_module_executes_with_injected_services_and_unicode(
        self,
    ) -> None:
        mod = _make_fake_prebuilt_module("unicode-pkg")
        fake_artifact = _FakePrebuiltArtifactService(auth_valid=True)
        fake_runner = FakeProcessRunner()
        factory = FakeServiceFactory(
            process_runner=fake_runner,
            services={SERVICE_ARTIFACT: fake_artifact},
        )
        ctx = ExecutionContext.for_testing(factory=factory)
        cmd = UpdatePrebuiltModuleCommand("unicode-pkg", mod)
        args = argparse.Namespace(
            target="linux-x64",
            build_number="16677889",
            latest=False,
            branch="emu-main-next",
            dest_dir="/tmp/prebuilts_测试_🚀_é",
            dry_run=True,
            token=None,
            skip_auth_check=False,
            prebuilt_module=mod,
            json=True,
        )
        validated = cmd.validate(args, ctx)
        res = cmd.execute(validated, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(fake_artifact.auth_checks, 1)
        self.assertEqual(len(mod.calls), 1)
        self.assertEqual(mod.calls[0]["dest_dir"], "/tmp/prebuilts_测试_🚀_é")
        self.assertTrue(mod.calls[0]["has_artifact_service"])
        self.assertTrue(mod.calls[0]["has_runner"])

    def test_update_prebuilt_module_rollback_invokes_plugin_hook(self) -> None:
        mod = _make_fake_prebuilt_module("lavapipe")
        cmd = UpdatePrebuiltModuleCommand("lavapipe", mod)
        ctx = ExecutionContext.for_testing()
        args = argparse.Namespace(build_number="16677889")
        cmd.rollback(args, ctx, RuntimeError("disk full 测试_🚀_é"))
        self.assertEqual(len(mod.rollbacks), 1)
        self.assertIn("测试_🚀_é", mod.rollbacks[0][1])

    def test_update_prebuilt_root_list_and_dispatcher_binding(self) -> None:
        mod = _make_fake_prebuilt_module("lavapipe")
        parser = argparse.ArgumentParser()
        root_cmd = UpdatePrebuiltCommand(discovered=[("lavapipe", mod)], parser=parser)
        root_cmd.configure_parser(parser)

        parsed_sub = parser.parse_args(
            ["lavapipe", "--build-number", "16600001", "--skip-auth-check"]
        )
        dispatcher = CliDispatcher()
        resolved = dispatcher.resolve_command(parsed_sub)
        self.assertIsInstance(resolved, UpdatePrebuiltModuleCommand)

        ctx = ExecutionContext.for_testing()
        list_args = argparse.Namespace(list=True, prebuilt_name=None, json=True)
        validated = root_cmd.validate(list_args, ctx)
        res = root_cmd.execute(validated, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
