import os
import json
import sys
from pathlib import Path
from lib.output import print_result

CONFIG_FILE_PATH = os.path.expanduser("~/.android/emu-dev-cli.json")
DEFAULT_BRANCHES = ["emu-main-dev", "emu-main-next", "git_main", "trunk-release"]


def load_config():
    if not os.path.exists(CONFIG_FILE_PATH):
        return {"source_directories": {}}
    try:
        with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                data = {}
            if "source_directories" not in data or not isinstance(data["source_directories"], dict):
                data["source_directories"] = {}
            return data
    except Exception:
        return {"source_directories": {}}


def save_config(config_data):
    config_dir = os.path.dirname(CONFIG_FILE_PATH)
    os.makedirs(config_dir, exist_ok=True)
    with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2)


def get_source_directory(branch_name):
    config = load_config()
    source_dirs = config.get("source_directories", {})
    return source_dirs.get(branch_name)


def set_source_directory(branch_name, source_path):
    config = load_config()
    abs_path = os.path.abspath(os.path.expanduser(source_path))
    config.setdefault("source_directories", {})[branch_name] = abs_path
    save_config(config)
    return abs_path


def run_first_time_setup(interactive=True):
    config = load_config()
    source_dirs = config.get("source_directories", {})

    if source_dirs:
        return config

    print("-" * 50)
    print("Welcome to emu-dev-cli! First-time source directory configuration.")
    print("Specify local source code repository paths for branches.")
    print("-" * 50)

    is_tty = sys.stdin.isatty() and interactive
    new_dirs = {}

    for branch in DEFAULT_BRANCHES:
        prompt_val = ""
        if is_tty:
            try:
                prompt_val = input(f"Enter local source path for branch '{branch}' (leave blank to skip): ").strip()
            except (EOFError, KeyboardInterrupt):
                prompt_val = ""
        if prompt_val:
            new_dirs[branch] = os.path.abspath(os.path.expanduser(prompt_val))
        else:
            new_dirs[branch] = ""

    config["source_directories"] = new_dirs
    save_config(config)
    print(f"\nSaved configuration to {CONFIG_FILE_PATH}")
    return config


def register_parser(subparsers):
    source_parser = subparsers.add_parser(
        "source-directory",
        help="Manage and query local source code repository directories for branches"
    )
    source_parser.set_defaults(func=lambda args: source_parser.print_help() or sys.exit(0))

    source_subparsers = source_parser.add_subparsers(dest="source_cmd", help="Available actions")

    # get subcommand
    get_parser = source_subparsers.add_parser(
        "get",
        help="Get local source directory path for a branch name"
    )
    get_parser.add_argument("branch", type=str, help="Branch name (e.g. emu-main-dev, emu-main-next, git_main)")
    get_parser.set_defaults(func=run_get)

    # set subcommand
    set_parser = source_subparsers.add_parser(
        "set",
        help="Map a branch name to a local repository source path"
    )
    set_parser.add_argument("branch", type=str, help="Branch name (e.g. emu-main-dev)")
    set_parser.add_argument("path", type=str, help="Local source directory path (e.g. /work/emu-main-dev)")
    set_parser.set_defaults(func=run_set)

    # list subcommand
    list_parser = source_subparsers.add_parser(
        "list",
        help="List all configured branch-to-directory mappings"
    )
    list_parser.set_defaults(func=run_list)


def run_get(args):
    json_mode = getattr(args, "json", False)
    branch = args.branch
    source_path = get_source_directory(branch)

    if not source_path:
        print_result({
            "status": "error",
            "action": "source-directory get",
            "branch": branch,
            "error_message": f"No local source directory mapped for branch '{branch}'. Use 'emu-dev-cli source-directory set {branch} <path>' to configure it.",
            "exit_code": 1
        }, json_mode=json_mode, is_error=True)
        sys.exit(1)

    if json_mode:
        print_result({
            "status": "success",
            "action": "source-directory get",
            "branch": branch,
            "source_directory": source_path,
            "exists": os.path.exists(source_path)
        }, json_mode=True)
    else:
        print(source_path)


def run_set(args):
    json_mode = getattr(args, "json", False)
    branch = args.branch
    path_val = args.path
    saved_path = set_source_directory(branch, path_val)

    print_result({
        "status": "success",
        "action": "source-directory set",
        "summary": f"Mapped branch '{branch}' -> {saved_path}",
        "branch": branch,
        "source_directory": saved_path,
        "config_file": CONFIG_FILE_PATH,
    }, json_mode=json_mode)


def run_list(args):
    json_mode = getattr(args, "json", False)
    config = load_config()
    source_dirs = config.get("source_directories", {})

    if json_mode:
        print_result({
            "status": "success",
            "action": "source-directory list",
            "config_file": CONFIG_FILE_PATH,
            "source_directories": source_dirs,
        }, json_mode=True)
        return

    if not source_dirs:
        print(f"No source directories configured in {CONFIG_FILE_PATH}.")
        print("Run 'emu-dev-cli source-directory set <branch> <path>' or 'emu-dev-cli init' to configure.")
        return

    print(f"Configured source directories ({CONFIG_FILE_PATH}):")
    max_len = max(len(b) for b in source_dirs.keys()) if source_dirs else 10
    for branch, p in sorted(source_dirs.items()):
        status_flag = "✓" if p and os.path.exists(p) else ("⚠️ missing" if p else "not set")
        print(f"  {branch:<{max_len}} -> {p or '(empty)'} [{status_flag}]")
