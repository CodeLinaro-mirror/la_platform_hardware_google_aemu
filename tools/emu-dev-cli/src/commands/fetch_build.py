import argparse
import glob
import importlib
import os
import sys
from typing import Any

from core.command import CommandGroup, bind_command


def get_target_modules():
    """
    Dynamically discover all target plugins in the src/targets/ directory.
    Excludes __init__.py and non-python files.
    """
    targets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "targets")
    modules = []
    for filepath in sorted(glob.glob(os.path.join(targets_dir, "*.py"))):
        filename = os.path.basename(filepath)
        if filename.startswith("_") or filename == "__init__.py":
            continue
        mod_name = f"targets.{filename[:-3]}"
        try:
            mod = importlib.import_module(mod_name)
            if hasattr(mod, "register_parser"):
                modules.append((filename[:-3], mod))
        except Exception as e:
            print(f"Warning: Failed to load target plugin '{mod_name}': {e}", file=sys.stderr)
    return modules


class FetchBuildCommand(CommandGroup):
    """Parent command group for `emu-dev-cli fetch-build`."""

    name = "fetch-build"
    help_text = "Download and extract prebuilt artifacts from Android Build (go/ab)"
    subcommand_dest = "target"

    def __init__(self, default_host: str = "linux-x64") -> None:
        super().__init__()
        self._default_host = default_host

    def register(self, subparsers: Any) -> argparse.ArgumentParser:
        fetch_parser = subparsers.add_parser(
            self.name,
            help=self.help_text,
        )
        self._parser = fetch_parser
        target_subparsers = fetch_parser.add_subparsers(
            dest=self.subcommand_dest,
            help="Available build target plugins in src/targets/",
        )
        children = []
        for _name, module in get_target_modules():
            sub_p = module.register_parser(target_subparsers, self._default_host)
            cmd = sub_p.get_default("_command_instance") if sub_p is not None else None
            if cmd is not None:
                children.append(cmd)
                self._by_name[cmd.name] = cmd
        self._subcommands = tuple(children)
        self.subcommands = self._subcommands
        self.bind_parser(fetch_parser)
        return fetch_parser


def register_parser(subparsers, default_host):
    """
    Register the 'fetch-build' subcommand and dynamically populate
    sub-targets from all modules found in src/targets/.
    """
    return FetchBuildCommand(default_host=default_host).register(subparsers)
