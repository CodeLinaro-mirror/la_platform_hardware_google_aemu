import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
from core.errors import ExecutionError
from lib.output import print_result

DEFAULT_BRANCHES = ["emu-main-dev", "emu-main-next", "git_main", "trunk-release"]
CONFIG_FILE_PATH = os.path.expanduser("~/.android/emu-dev-cli.json")
DEFAULT_CODESEARCH_URLS = {
    "emu-main-next": "https://source.corp.google.com/h/googleplex-android/platform/superproject/emu-main-next/+/emu-main-next:",
    "emu-main-dev": "https://source.corp.google.com/h/googleplex-android/platform/superproject/emu-main-dev/+/emu-main-dev:",
    "git_main": "https://source.corp.google.com/h/googleplex-android/platform/superproject/main/+/main:",
    "trunk-release": "https://source.corp.google.com/h/googleplex-android/platform/superproject/main/+/main:",
}


def _empty_config() -> dict:
    """Returns a fresh default configuration dictionary."""
    return {"source_directories": {}, "codesearch_urls": {}}


def _normalize_config_dict(data: object) -> dict:
    """Normalizes a parsed JSON object into the canonical config dictionary structure."""
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object at root, got {type(data).__name__}")
    normalized = dict(data)
    if not isinstance(normalized.get("source_directories"), dict):
        normalized["source_directories"] = {}
    if not isinstance(normalized.get("codesearch_urls"), dict):
        normalized["codesearch_urls"] = {}
    return normalized


def _read_config_file(path: str) -> dict:
    """Reads and validates a JSON configuration file from `path`."""
    with open(path, "r", encoding="utf-8") as f:
        return _normalize_config_dict(json.load(f))


def load_config(strict: bool = False) -> dict:
    """Loads `~/.android/emu-dev-cli.json`, falling back to `.bak` on corruption.

    When `strict=True`, raises `ExecutionError` if the config file is unreadable/corrupt
    and no valid `.bak` backup is available, preventing callers from overwriting user state.
    """
    backup_path = f"{CONFIG_FILE_PATH}.bak"
    if not os.path.exists(CONFIG_FILE_PATH):
        if os.path.exists(backup_path):
            try:
                return _read_config_file(backup_path)
            except (OSError, ValueError, UnicodeDecodeError):
                pass
        return _empty_config()

    try:
        return _read_config_file(CONFIG_FILE_PATH)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        logging.warning(
            "Failed to parse configuration file '%s' (%s).",
            CONFIG_FILE_PATH,
            exc,
        )
        if os.path.exists(backup_path):
            try:
                recovered = _read_config_file(backup_path)
                logging.warning(
                    "Recovered configuration from backup '%s'.",
                    backup_path,
                )
                return recovered
            except (OSError, ValueError, UnicodeDecodeError) as bak_exc:
                logging.warning(
                    "Backup configuration '%s' is also unreadable (%s).",
                    backup_path,
                    bak_exc,
                )
        if strict:
            raise ExecutionError(
                f"Failed to parse configuration file '{CONFIG_FILE_PATH}': {exc}. "
                f"Fix the JSON syntax in '{CONFIG_FILE_PATH}' or restore '{backup_path}' "
                "before modifying configuration.",
                error_code="CORRUPT_CONFIG_FILE",
            ) from exc
        return _empty_config()


def save_config(config_data: dict) -> None:
    """Atomically writes `config_data` to `CONFIG_FILE_PATH` after backing up valid state."""
    config_dir = os.path.dirname(os.path.abspath(CONFIG_FILE_PATH))
    os.makedirs(config_dir, exist_ok=True)
    backup_path = f"{CONFIG_FILE_PATH}.bak"
    if os.path.exists(CONFIG_FILE_PATH):
        try:
            _read_config_file(CONFIG_FILE_PATH)
            shutil.copy2(CONFIG_FILE_PATH, backup_path)
        except (OSError, ValueError, UnicodeDecodeError):
            pass

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=config_dir,
            prefix=".emu-dev-cli.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = tmp_file.name
            json.dump(config_data, tmp_file, indent=2, ensure_ascii=False)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, CONFIG_FILE_PATH)
    except Exception:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


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
    config = load_config(strict=True)
    config.setdefault("codesearch_urls", {})[branch_name] = url_prefix
    save_config(config)
    return url_prefix


def ensure_codesearch_urls_config():
    """
    Ensures that the standard codesearch_urls map is written/refreshed in ~/.android/emu-dev-cli.json.
    Called on init and update commands. Preserves corrupt files rather than wiping user config.
    """
    try:
        config = load_config(strict=True)
    except ExecutionError as exc:
        logging.warning(
            "Skipping codesearch_urls refresh to avoid overwriting corrupt config: %s",
            exc,
        )
        return DEFAULT_CODESEARCH_URLS.copy()

    desired_urls = DEFAULT_CODESEARCH_URLS.copy()
    if config.get("codesearch_urls") != desired_urls or not os.path.exists(CONFIG_FILE_PATH):
        config["codesearch_urls"] = desired_urls
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
    config = load_config(strict=True)
    abs_path = os.path.abspath(os.path.expanduser(source_path))
    config.setdefault("source_directories", {})[branch_name] = abs_path
    save_config(config)
    return abs_path


def run_first_time_setup(interactive=True):
    config = load_config(strict=True)
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
