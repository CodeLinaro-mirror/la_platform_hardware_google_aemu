import os
import json
import sys
import argparse
from pathlib import Path
from lib.output import print_result

DEFAULT_BRANCHES = ["emu-main-dev", "emu-main-next", "git_main", "trunk-release"]
CONFIG_FILE_PATH = os.path.expanduser("~/.android/emu-dev-cli.json")
DEFAULT_CODESEARCH_URLS = {
    "emu-main-next": "https://source.corp.google.com/h/googleplex-android/platform/superproject/emu-main-next/+/emu-main-next:",
    "emu-main-dev": "https://source.corp.google.com/h/googleplex-android/platform/superproject/emu-main-dev/+/emu-main-dev:",
    "git_main": "https://source.corp.google.com/h/googleplex-android/platform/superproject/main/+/main:",
    "trunk-release": "https://source.corp.google.com/h/googleplex-android/platform/superproject/main/+/main:",
}


def load_config():
    if not os.path.exists(CONFIG_FILE_PATH):
        return {"source_directories": {}, "codesearch_urls": {}}
    try:
        with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                data = {}
            if "source_directories" not in data or not isinstance(data["source_directories"], dict):
                data["source_directories"] = {}
            if "codesearch_urls" not in data or not isinstance(data["codesearch_urls"], dict):
                data["codesearch_urls"] = {}
            return data
    except Exception:
        return {"source_directories": {}, "codesearch_urls": {}}


def save_config(config_data):
    config_dir = os.path.dirname(CONFIG_FILE_PATH)
    os.makedirs(config_dir, exist_ok=True)
    with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2)


def get_source_directory(branch_name):
    config = load_config()
    source_dirs = config.get("source_directories", {})
    return source_dirs.get(branch_name)


def get_codesearch_url_prefix(branch_name="emu-main-next"):
    config = load_config()
    cs_urls = config.get("codesearch_urls", {})
    if branch_name in cs_urls and cs_urls[branch_name]:
        return cs_urls[branch_name]
    return DEFAULT_CODESEARCH_URLS.get(branch_name, DEFAULT_CODESEARCH_URLS["emu-main-next"])


def set_codesearch_url_prefix(branch_name, url_prefix):
    config = load_config()
    config.setdefault("codesearch_urls", {})[branch_name] = url_prefix
    save_config(config)
    return url_prefix


def ensure_codesearch_urls_config():
    """
    Ensures that the standard codesearch_urls map is written/refreshed in ~/.android/emu-dev-cli.json.
    Called on init and update commands.
    """
    config = load_config()
    config["codesearch_urls"] = DEFAULT_CODESEARCH_URLS.copy()
    save_config(config)
    return config["codesearch_urls"]


def build_codesearch_url(relative_path, branch_name="emu-main-next"):
    prefix = get_codesearch_url_prefix(branch_name)
    if not prefix.endswith(":") and not prefix.endswith("/"):
        prefix += ":"
    return f"{prefix}{relative_path}"


def get_all_source_directories():
    """
    Returns a deduplicated list of all valid local source directory paths from:
    1. Registered branch mappings in ~/.android/emu-dev-cli.json
    2. Current working directory and ancestor workspace roots
    """
    config = load_config()
    source_dirs = config.get("source_directories", {})
    dirs = []

    # 1. Registered directories in config
    for branch, path in source_dirs.items():
        if path and os.path.isdir(path) and path not in dirs:
            dirs.append(path)

    # 2. Current working directory and ancestor roots
    curr = os.path.abspath(os.getcwd())
    for _ in range(7):
        if os.path.isdir(curr) and curr not in dirs:
            if os.path.exists(os.path.join(curr, "hardware")) or os.path.exists(os.path.join(curr, "third_party")):
                dirs.append(curr)
        parent = os.path.dirname(curr)
        if parent == curr:
            break
        curr = parent

    return dirs


def find_file_in_source_directories(relative_path):
    """
    Searches across all registered source directories and workspace roots for a relative file path.
    Returns the absolute path if found, or None.
    """
    for src_dir in get_all_source_directories():
        candidate = os.path.join(src_dir, relative_path)
        if os.path.exists(candidate):
            return candidate
    return None


def set_source_directory(branch_name, source_path):
    config = load_config()
    abs_path = os.path.abspath(os.path.expanduser(source_path))
    config.setdefault("source_directories", {})[branch_name] = abs_path
    save_config(config)
    return abs_path


def run_first_time_setup(interactive=True):
    config = load_config()
    source_dirs = config.get("source_directories", {})
    ensure_codesearch_urls_config()

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
    source_parser.add_argument(
        "--codesearch-url", "--cs-url", "--url",
        dest="codesearch_url",
        action="store_true",
        help="Outputs the Code Search web URL prefix instead of local directory path"
    )
    source_parser.set_defaults(func=lambda args: source_parser.print_help() or sys.exit(0))

    source_subparsers = source_parser.add_subparsers(dest="source_cmd", help="Available actions")

    # get subcommand
    get_parser = source_subparsers.add_parser(
        "get",
        help="Get local source directory path or Code Search URL for a branch name"
    )
    get_parser.add_argument("branch", type=str, help="Branch name (e.g. emu-main-dev, emu-main-next, git_main)")
    get_parser.add_argument(
        "--codesearch-url", "--cs-url", "--url",
        dest="codesearch_url",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Outputs the Code Search web URL prefix for the branch instead of local directory path"
    )
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
        help="List all configured branch-to-directory mappings or Code Search URLs"
    )
    list_parser.add_argument(
        "--codesearch-url", "--cs-url", "--url",
        dest="codesearch_url",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Outputs Code Search web URL prefixes instead of local directory paths"
    )
    list_parser.set_defaults(func=run_list)


def run_get(args):
    json_mode = getattr(args, "json", False)
    use_cs_url = getattr(args, "codesearch_url", False)
    branch = args.branch
    source_path = get_source_directory(branch)
    cs_url = get_codesearch_url_prefix(branch)

    if use_cs_url:
        if json_mode:
            print_result({
                "status": "success",
                "action": "source-directory get",
                "branch": branch,
                "codesearch_url": cs_url,
            }, json_mode=True)
        else:
            print(cs_url)
        return

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
            "codesearch_url": cs_url,
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
    all_branches = sorted(set(list(source_dirs.keys()) + list(DEFAULT_BRANCHES) + list(DEFAULT_CODESEARCH_URLS.keys())))
    display_cs_urls = {b: get_codesearch_url_prefix(b) for b in all_branches}

    if json_mode:
        print_result({
            "status": "success",
            "action": "source-directory list",
            "config_file": CONFIG_FILE_PATH,
            "source_directories": source_dirs,
            "codesearch_urls": display_cs_urls,
        }, json_mode=True)
        return

    if not source_dirs:
        print(f"No source directories configured in {CONFIG_FILE_PATH}.")
        print("Run 'emu-dev-cli source-directory set <branch> <path>' or 'emu-dev-cli init' to configure.")
        return

    print(f"Configured source directories and Code Search URLs ({CONFIG_FILE_PATH}):\n")
    max_len = max(len(b) for b in all_branches) if all_branches else 12
    for branch in all_branches:
        p = source_dirs.get(branch, "")
        status_flag = "✓" if p and os.path.exists(p) else ("⚠️ missing" if p else "not set")
        cs_url = display_cs_urls.get(branch, "")
        print(f"  • {branch:<{max_len}} -> {p or '(empty)'} [{status_flag}]")
        if cs_url:
            print(f"    {'':<{max_len}}    CS: {cs_url}")
        print()
