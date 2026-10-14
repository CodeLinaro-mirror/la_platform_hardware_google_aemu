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

import glob
import importlib
import importlib.util
import inspect
import os
import sys
from types import ModuleType
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lib.android_build_client import check_authentication_status
from lib.output import format_markdown_table, print_result
from lib.prebuilt_updater import (
    SUPPORTED_TARGETS,
    log_progress,
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
        for p in parts:
            if p not in allowed:
                import argparse

                raise argparse.ArgumentTypeError(
                    f"invalid target '{p}' (choose from: all, "
                    f"{', '.join(supported_targets)})"
                )
        return ",".join(parts)

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


def run_prebuilt_module(name: str, mod: ModuleType, args) -> None:
    """Executes a discovered prebuilt plugin module from parsed CLI arguments."""
    action_name = f"update-prebuilt {name}"
    supported_targets = _get_module_supported_targets(mod)
    raw_target = getattr(args, "target", None) or "all"
    resolved_targets = resolve_targets_list(raw_target, supported_targets)

    build_number = getattr(args, "build_number", None)
    is_latest = getattr(args, "latest", False)
    branch = getattr(args, "branch", None) or _get_module_default_branch(mod)
    dry_run = getattr(args, "dry_run", False)
    json_mode = getattr(args, "json", False)
    token = getattr(args, "token", None)
    dest_dir = getattr(args, "dest_dir", None)

    if not getattr(args, "skip_auth_check", False):
        auth_status = check_authentication_status(token=token)
        if not auth_status["valid"]:
            print_result(
                {
                    "status": "error",
                    "action": action_name,
                    "prebuilt": name,
                    "summary": "Android Build API authentication check failed",
                    "auth_method": auth_status["auth_method"],
                    "error_message": auth_status["error_message"],
                    "procedure": auth_status["procedure"],
                    "exit_code": 1,
                },
                json_mode=json_mode,
                is_error=True,
            )
            return

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
    )

    if isinstance(res, dict):
        is_err = res.get("status") == "error"
        print_result(res, json_mode=json_mode, is_error=is_err)


def register_parser(subparsers, default_host: str = "linux-x64"):
    """Registers `update-prebuilt` and dynamically populates plugins from `src/prebuilts/`."""
    del default_host
    update_parser = subparsers.add_parser(
        "update-prebuilt",
        help="Update checked-in prebuilt packages from Android Build (go/ab)",
    )
    update_parser.add_argument(
        "--list",
        action="store_true",
        help="List all discovered prebuilt target modules in src/prebuilts/",
    )
    prebuilt_subparsers = update_parser.add_subparsers(
        dest="prebuilt_name",
        help="Available prebuilt target modules in src/prebuilts/",
    )

    discovered = get_prebuilt_modules()

    for name, mod in discovered:
        supported_targets = _get_module_supported_targets(mod)
        default_branch = _get_module_default_branch(mod)
        desc = _get_module_description(name, mod)
        aliases = list(getattr(mod, "ALIASES", ()))

        sub = prebuilt_subparsers.add_parser(
            name,
            aliases=aliases,
            help=desc,
        )
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

        def _make_handler(bound_name: str, bound_mod: ModuleType, bound_parser):
            def _handle(args):
                if not args.build_number and not args.latest:
                    bound_parser.print_help()
                    sys.exit(1)
                run_prebuilt_module(bound_name, bound_mod, args)

            return _handle

        sub.set_defaults(
            func=_make_handler(name, mod, sub),
            prebuilt_module=mod,
        )

    def handle_root(args):
        if getattr(args, "list", False):
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
            print_result(
                {
                    "status": "success",
                    "action": "update-prebuilt --list",
                    "summary": f"{len(discovered)} discovered prebuilt module(s)",
                    "prebuilts": prebuilts_dict,
                    "markdown_table": md_table,
                },
                json_mode=getattr(args, "json", False),
            )
            return
        if not getattr(args, "prebuilt_name", None):
            update_parser.print_help()
            sys.exit(1)

    update_parser.set_defaults(func=handle_root)
