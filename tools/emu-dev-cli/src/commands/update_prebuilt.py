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

"""Dynamic dispatcher for `emu-dev-cli update-prebuilt`.

Discovers per-prebuilt Python modules in `src/prebuilts/` that implement
`update_prebuilt(build_number, targets)` and exposes each as a subcommand.
"""

import argparse
import glob
import importlib
import importlib.util
import inspect
import os
import sys
from types import ModuleType
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.command import (
    Command,
    CommandResult,
    CommandValidationError,
    STATUS_ERROR,
    STATUS_SUCCESS,
    bind_command,
)
from core.context import (
    ExecutionContext,
    SERVICE_ARTIFACT,
    SERVICE_PROCESS_RUNNER,
)
from core.errors import ExitCode
from lib.android_build_client import check_authentication_status
from lib.output import format_markdown_table
from lib.prebuilt_updater import (
    SUPPORTED_TARGETS,
    log_progress,
    normalize_target_name,
    resolve_targets_list,
)


def _get_prebuilt_entrypoint(mod: ModuleType):
    """Returns the module's `update_prebuilt` callable if defined."""
    for attr in ("update_prebuilt", "updatePrebuilt"):
        fn = getattr(mod, attr, None)
        if callable(fn):
            return fn
    return None


def get_prebuilt_modules(
    prebuilts_dir: Optional[str] = None,
) -> List[Tuple[str, ModuleType]]:
    """Dynamically discovers all prebuilt plugins in `src/prebuilts/`."""
    use_custom_dir = prebuilts_dir is not None
    if prebuilts_dir is None:
        prebuilts_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "prebuilts"
        )
    modules: List[Tuple[str, ModuleType]] = []
    if not os.path.isdir(prebuilts_dir):
        return modules

    for filepath in sorted(glob.glob(os.path.join(prebuilts_dir, "*.py"))):
        filename = os.path.basename(filepath)
        if filename.startswith("_") or filename == "__init__.py":
            continue
        stem = filename[:-3]
        mod_name = f"prebuilts.{stem}"
        try:
            if use_custom_dir:
                spec = importlib.util.spec_from_file_location(mod_name, filepath)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            else:
                mod = importlib.import_module(mod_name)
            if _get_prebuilt_entrypoint(mod) is not None:
                cli_name = getattr(mod, "PREBUILT_NAME", stem.replace("_", "-"))
                modules.append((cli_name, mod))
        except Exception as e:
            print(
                f"Warning: Failed to load prebuilt plugin '{mod_name}': {e}",
                file=sys.stderr,
            )
    return modules


def _get_module_supported_targets(mod: ModuleType) -> Tuple[str, ...]:
    """Returns the supported host targets declared by a prebuilt module."""
    targets = getattr(mod, "SUPPORTED_TARGETS", None)
    if targets:
        return tuple(targets)
    return SUPPORTED_TARGETS


def _get_module_default_branch(mod: ModuleType) -> Optional[str]:
    """Returns the default Android Build branch declared by a prebuilt module."""
    branch = getattr(mod, "DEFAULT_BRANCH", None)
    return str(branch) if branch else None


def _get_module_description(name: str, mod: ModuleType) -> str:
    """Returns a concise description string for a prebuilt module."""
    desc = getattr(mod, "DESCRIPTION", None)
    if desc:
        return str(desc)
    doc = (mod.__doc__ or "").strip()
    if doc:
        return doc.splitlines()[0].strip()
    return f"Update {name} prebuilt artifacts from Android Build (go/ab)"


def _parse_and_validate_target_arg(supported_targets: Sequence[str]):
    """Creates an argparse type function that validates `--target`."""
    allowed = {"all"} | set(supported_targets)

    def _validator(raw_value: str) -> str:
        parts = [p.strip().lower() for p in raw_value.split(",") if p.strip()]
        if not parts:
            return "all"
        normalized_parts: List[str] = []
        for p in parts:
            norm = (
                "all"
                if p == "all"
                else normalize_target_name(p, supported_targets)
            )
            if norm not in allowed:
                import argparse

                raise argparse.ArgumentTypeError(
                    f"invalid target '{p}' (choose from: all, "
                    f"{', '.join(supported_targets)})"
                )
            normalized_parts.append(norm)
        return ",".join(dict.fromkeys(normalized_parts))

    return _validator


def _invoke_module_update_prebuilt(
    mod: ModuleType,
    build_number: Optional[str],
    targets: List[str],
    *,
    is_latest: bool = False,
    branch: Optional[str] = None,
    dry_run: bool = False,
    dest_dir: Optional[str] = None,
    token: Optional[str] = None,
    artifact_service: Optional[Any] = None,
    runner: Optional[Any] = None,
) -> Any:
    """Calls `mod.update_prebuilt(build_number, targets, ...)` inspecting its signature."""
    fn = _get_prebuilt_entrypoint(mod)
    if fn is None:
        raise ValueError(f"Module {mod.__name__} has no update_prebuilt function")

    effective_build_number = (
        "latest" if (is_latest and not build_number) else build_number
    )
    optional_kwargs = {
        "is_latest": is_latest,
        "branch": branch,
        "dry_run": dry_run,
        "dest_dir": dest_dir,
        "token": token,
    }
    if artifact_service is not None:
        optional_kwargs["artifact_service"] = artifact_service
    if runner is not None:
        optional_kwargs["runner"] = runner
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        return fn(effective_build_number, targets)

    has_var_kw = any(
        p.kind == inspect.Parameter.VAR_KEYWORD
        for p in sig.parameters.values()
    )
    if has_var_kw:
        return fn(effective_build_number, targets, **optional_kwargs)

    accepted_kwargs = {
        k: v for k, v in optional_kwargs.items() if k in sig.parameters
    }
    return fn(effective_build_number, targets, **accepted_kwargs)


def run_prebuilt_module(
    name: str,
    mod: ModuleType,
    args,
    *,
    artifact_service: Optional[Any] = None,
    runner: Optional[Any] = None,
) -> Dict[str, Any]:
    """Executes a discovered prebuilt plugin module and returns its result payload."""
    action_name = f"update-prebuilt {name}"
    supported_targets = _get_module_supported_targets(mod)
    raw_target = getattr(args, "target", None) or "all"
    resolved_targets = resolve_targets_list(raw_target, supported_targets)

    build_number = getattr(args, "build_number", None)
    is_latest = getattr(args, "latest", False)
    branch = getattr(args, "branch", None) or _get_module_default_branch(mod)
    dry_run = getattr(args, "dry_run", False)
    token = getattr(args, "token", None)
    dest_dir = getattr(args, "dest_dir", None)

    if not getattr(args, "skip_auth_check", False):
        auth_status = (
            artifact_service.check_auth(token=token)
            if artifact_service is not None and hasattr(artifact_service, "check_auth")
            else check_authentication_status(token=token)
        )
        if not auth_status["valid"]:
            return {
                "status": "error",
                "action": action_name,
                "prebuilt": name,
                "summary": "Android Build API authentication check failed",
                "auth_method": auth_status["auth_method"],
                "error_message": auth_status["error_message"],
                "procedure": auth_status["procedure"],
                "exit_code": 1,
            }

    bid_label = f"go/ab/{build_number}" if build_number else f"latest ({branch or 'default'})"
    mode_tag = " [dry-run]" if dry_run else ""
    log_progress(
        f"🔄 Updating '{name}' prebuilts from {bid_label} for "
        f"{len(resolved_targets)} target(s): {', '.join(resolved_targets)}{mode_tag}"
    )

    res = _invoke_module_update_prebuilt(
        mod=mod,
        build_number=build_number,
        targets=resolved_targets,
        is_latest=is_latest,
        branch=branch,
        dry_run=dry_run,
        dest_dir=dest_dir,
        token=token,
        artifact_service=artifact_service,
        runner=runner,
    )

    if isinstance(res, dict):
        return res
    return {
        "status": "success",
        "action": action_name,
        "prebuilt": name,
        "targets": resolved_targets,
    }


def _configure_prebuilt_module_parser(
    sub: argparse.ArgumentParser,
    name: str,
    mod: ModuleType,
) -> None:
    """Adds standard prebuilt flags to a module subparser."""
    del name
    supported_targets = _get_module_supported_targets(mod)
    default_branch = _get_module_default_branch(mod)

    sub.add_argument(
        "--target",
        type=_parse_and_validate_target_arg(supported_targets),
        default="all",
        help=(
            f"Target host platform(s) ({', '.join(supported_targets)}; "
            "defaults to 'all' to update all supported targets)"
        ),
    )
    build_group = sub.add_mutually_exclusive_group(required=False)
    build_group.add_argument(
        "--build-number",
        "--build_number",
        "--build-id",
        "--build_id",
        dest="build_number",
        type=str,
        help="Android Build number on go/ab (e.g. 16534139)",
    )
    build_group.add_argument(
        "--latest",
        action="store_true",
        help="Fetch from the latest submitted green build on the target branch",
    )
    branch_help = (
        f"Android Build branch to query for --latest (defaults to {default_branch})"
        if default_branch
        else "Android Build branch to query for --latest"
    )
    sub.add_argument(
        "--branch",
        type=str,
        default=default_branch,
        help=branch_help,
    )
    sub.add_argument(
        "--dest-dir",
        "--dest_dir",
        dest="dest_dir",
        type=str,
        default=None,
        help="Destination directory override (defaults to workspace prebuilts/...)",
    )
    sub.add_argument(
        "--dry-run",
        action="store_true",
        help="Download prebuilts without modifying workspace files",
    )
    sub.add_argument(
        "--token",
        type=str,
        default=None,
        help="Explicit OAuth2 access token for Android Build API",
    )
    sub.add_argument(
        "--skip-auth-check",
        action="store_true",
        help="Skip preflight authentication readiness check",
    )
    sub.set_defaults(prebuilt_module=mod)


class UpdatePrebuiltModuleCommand(Command[argparse.Namespace, Dict[str, Any]]):
    """Command implementation for an individual `update-prebuilt <module>` plugin."""

    def __init__(
        self,
        name: str,
        mod: ModuleType,
        *,
        parser: Optional[argparse.ArgumentParser] = None,
    ) -> None:
        self.name = f"update-prebuilt {name}"
        self.description = _get_module_description(name, mod)
        self.aliases = tuple(getattr(mod, "ALIASES", ()))
        self._prebuilt_name = name
        self._mod = mod
        self._parser = parser

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        self._parser = parser
        _configure_prebuilt_module_parser(parser, self._prebuilt_name, self._mod)

    def validate(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> argparse.Namespace:
        del ctx
        if not getattr(args, "build_number", None) and not getattr(args, "latest", False):
            if self._parser is not None:
                self._parser.print_help()
            raise CommandValidationError(
                f"Missing build target for 'update-prebuilt {self._prebuilt_name}': "
                "expected '--build-number <id>' or '--latest', got neither. "
                "Pass '--build-number <id>' or '--latest' and retry."
            )
        return args

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[Dict[str, Any]]:
        artifact_svc = ctx.services.get(SERVICE_ARTIFACT) if ctx is not None else None
        runner = ctx.services.get(SERVICE_PROCESS_RUNNER) if ctx is not None else None
        mod = getattr(args, "prebuilt_module", None) or self._mod
        payload = run_prebuilt_module(
            self._prebuilt_name,
            mod,
            args,
            artifact_service=artifact_svc,
            runner=runner,
        )
        is_err = payload.get("status") == STATUS_ERROR
        return CommandResult(
            data=payload,
            action=str(payload.get("action") or self.name),
            summary=str(payload.get("summary") or self.name),
            status=STATUS_ERROR if is_err else STATUS_SUCCESS,
            exit_code=int(
                payload.get(
                    "exit_code",
                    ExitCode.FAILURE if is_err else ExitCode.SUCCESS,
                )
            ),
            markdown_table=payload.get("markdown_table"),
        )

    def rollback(
        self,
        args: argparse.Namespace,
        ctx: ExecutionContext,
        error: BaseException,
    ) -> None:
        del ctx
        rollback_fn = getattr(self._mod, "rollback_prebuilt", None)
        if callable(rollback_fn):
            rollback_fn(args, error)


def _format_discovered_prebuilts(
    discovered: Sequence[Tuple[str, ModuleType]]
) -> Dict[str, Any]:
    """Builds the structured `--list` payload for discovered prebuilt plugins."""
    rows = []
    prebuilts_dict: Dict[str, Any] = {}
    for name, mod in discovered:
        targets = _get_module_supported_targets(mod)
        branch = _get_module_default_branch(mod) or "-"
        desc = _get_module_description(name, mod)
        rows.append([name, ", ".join(targets), branch, desc])
        prebuilts_dict[name] = {
            "module": mod.__name__,
            "targets": list(targets),
            "default_branch": branch,
            "description": desc,
        }
    md_table = format_markdown_table(
        ["Prebuilt", "Supported Targets", "Default Branch", "Description"],
        rows,
    )
    return {
        "status": "success",
        "action": "update-prebuilt --list",
        "summary": f"{len(discovered)} discovered prebuilt module(s)",
        "prebuilts": prebuilts_dict,
        "markdown_table": md_table,
    }


class UpdatePrebuiltCommand(Command[argparse.Namespace, Dict[str, Any]]):
    """Composite Command implementation for `emu-dev-cli update-prebuilt`."""

    name = "update-prebuilt"
    description = "Update checked-in prebuilt packages from Android Build (go/ab)"

    def __init__(
        self,
        *,
        discovered: Optional[List[Tuple[str, ModuleType]]] = None,
        parser: Optional[argparse.ArgumentParser] = None,
    ) -> None:
        self._discovered = discovered
        self._parser = parser
        self._module_commands: Dict[str, UpdatePrebuiltModuleCommand] = {}

    def _get_discovered(self) -> List[Tuple[str, ModuleType]]:
        if self._discovered is None:
            self._discovered = get_prebuilt_modules()
        return self._discovered

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        self._parser = parser
        parser.add_argument(
            "--list",
            action="store_true",
            help="List all discovered prebuilt target modules in src/prebuilts/",
        )
        prebuilt_subparsers = parser.add_subparsers(
            dest="prebuilt_name",
            help="Available prebuilt target modules in src/prebuilts/",
        )
        for name, mod in self._get_discovered():
            desc = _get_module_description(name, mod)
            aliases = list(getattr(mod, "ALIASES", ()))
            sub = prebuilt_subparsers.add_parser(
                name,
                aliases=aliases,
                help=desc,
            )
            mod_cmd = UpdatePrebuiltModuleCommand(name, mod, parser=sub)
            self._module_commands[name] = mod_cmd
            for alias in aliases:
                self._module_commands[alias] = mod_cmd
            bind_command(sub, mod_cmd)

    def _resolve_module_command(
        self, args: argparse.Namespace
    ) -> Optional[UpdatePrebuiltModuleCommand]:
        prebuilt_name = getattr(args, "prebuilt_name", None)
        if not prebuilt_name:
            return None
        if prebuilt_name in self._module_commands:
            return self._module_commands[prebuilt_name]
        mod = getattr(args, "prebuilt_module", None)
        if mod is not None:
            return UpdatePrebuiltModuleCommand(str(prebuilt_name), mod)
        for name, discovered_mod in self._get_discovered():
            aliases = tuple(getattr(discovered_mod, "ALIASES", ()))
            if prebuilt_name == name or prebuilt_name in aliases:
                return UpdatePrebuiltModuleCommand(name, discovered_mod)
        return None

    def validate(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> argparse.Namespace:
        if getattr(args, "list", False):
            return args
        mod_cmd = self._resolve_module_command(args)
        if mod_cmd is None:
            if self._parser is not None:
                self._parser.print_help()
            raise CommandValidationError(
                "Missing prebuilt subcommand for 'update-prebuilt': expected "
                "'--list' or a discovered prebuilt module name, got none. "
                "Run 'emu-dev-cli update-prebuilt --list' to list available modules."
            )
        return mod_cmd.validate(args, ctx)

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[Dict[str, Any]]:
        if getattr(args, "list", False):
            payload = _format_discovered_prebuilts(self._get_discovered())
            return CommandResult(
                data=payload,
                action="update-prebuilt --list",
                summary=str(payload["summary"]),
                markdown_table=payload.get("markdown_table"),
            )
        mod_cmd = self._resolve_module_command(args)
        if mod_cmd is None:
            raise CommandValidationError(
                "Missing prebuilt subcommand for 'update-prebuilt': expected "
                "'--list' or a discovered prebuilt module name, got none. "
                "Run 'emu-dev-cli update-prebuilt --list' to list available modules."
            )
        return mod_cmd.execute(args, ctx)


def register_parser(subparsers, default_host: str = "linux-x64"):
    """Registers `update-prebuilt` and dynamically populates plugins from `src/prebuilts/`."""
    del default_host
    update_parser = subparsers.add_parser(
        "update-prebuilt",
        help="Update checked-in prebuilt packages from Android Build (go/ab)",
    )
    discovered = get_prebuilt_modules()
    bind_command(
        update_parser,
        UpdatePrebuiltCommand(discovered=discovered, parser=update_parser),
    )
