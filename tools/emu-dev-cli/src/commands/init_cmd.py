import argparse
from pathlib import Path
from typing import Any

from commands.harness.plugin_installer import install_harness_plugin
from commands.source_directory import (
    CONFIG_FILE_PATH,
    load_config,
    run_first_time_setup,
)
from core.command import Command, CommandResult, bind_command
from core.context import ExecutionContext
from install.installer import install_skill
from lib.output import print_result


class InitCommand(Command[argparse.Namespace, dict[str, Any]]):
    """Installs global agent skills and runs first-time source directory setup."""

    name = "init"
    help = (
        "Install global agent skill (SKILL.md) and run first-time source directory "
        "setup if ~/.android/emu-dev-cli.json is missing"
    )

    def execute(
        self, args: argparse.Namespace, ctx: ExecutionContext
    ) -> CommandResult[dict[str, Any]]:
        del ctx
        return run_init(args)


def register_parser(subparsers):
    init_parser = subparsers.add_parser(
        "init",
        help=InitCommand.help,
    )
    bind_command(init_parser, InitCommand())
    return init_parser


def run_init(args) -> CommandResult[dict[str, Any]]:
    json_mode = getattr(args, "json", False)

    cfg = load_config()
    if not cfg.get("source_directories"):
        run_first_time_setup(interactive=not json_mode)

    installed_files = install_skill()

    if not installed_files:
        return print_result(
            {
                "status": "error",
                "action": "init",
                "summary": "Failed to install SKILL.md into ~/.gemini",
                "error_message": (
                    "Could not locate source skills/SKILL.md or could not write to "
                    "~/.gemini/config/skills/ and ~/.gemini/skills/. Verify directory permissions "
                    "and ensure 'emu-main-next' is configured via 'emu-dev-cli source-directory set emu-main-next <path>'."
                ),
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )

    home_dir = Path.home()
    harness_report = install_harness_plugin(
        target_dir=(
            home_dir / ".gemini" / "config" / "plugins" / "aemu-harness"
            if home_dir.is_dir()
            else None
        )
    )

    return print_result(
        {
            "status": "success",
            "action": "init",
            "summary": "Successfully initialized emu-dev-cli environment, agent skills, and aemu-harness plugin",
            "config_file": CONFIG_FILE_PATH,
            "installed_locations": installed_files,
            "harness_plugin": harness_report,
        },
        json_mode=json_mode,
    )
