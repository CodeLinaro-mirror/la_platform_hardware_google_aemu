from pathlib import Path
import sys

from commands.harness.plugin_installer import install_harness_plugin
from commands.source_directory import (
    CONFIG_FILE_PATH,
    load_config,
    run_first_time_setup,
)
from install.installer import install_skill
from lib.output import print_result


def register_parser(subparsers):
    init_parser = subparsers.add_parser(
        "init",
        help="Install global agent skill (SKILL.md) and run first-time source directory setup if ~/.android/emu-dev-cli.json is missing",
    )
    init_parser.set_defaults(func=run_init)


def run_init(args):
    json_mode = getattr(args, "json", False)

    cfg = load_config()
    if not cfg.get("source_directories"):
        run_first_time_setup(interactive=not json_mode)

    installed_files = install_skill()

    if not installed_files:
        print_result(
            {
                "status": "error",
                "action": "init",
                "summary": "Failed to write SKILL.md into home directory ~/.gemini",
                "error_message": "Could not access ~/.gemini/config/skills/ or ~/.gemini/skills/.",
                "exit_code": 1,
            },
            json_mode=json_mode,
            is_error=True,
        )
        sys.exit(1)

    home_dir = Path.home()
    harness_report = install_harness_plugin(
        target_dir=(
            home_dir / ".gemini" / "config" / "plugins" / "aemu-harness"
            if home_dir.is_dir()
            else None
        )
    )

    print_result(
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
